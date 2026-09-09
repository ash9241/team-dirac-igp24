"""Export an IGP24 ledger read-only, using an explicit allowlist of public fields.

The SQLite database, raw responses, descriptions, and authentication material
are never copied. The bulk corpus is placed outside the Git working tree.
"""
import argparse, csv, gzip, hashlib, io, json, sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ['submission_id','polynomial_index','submitted_at','coefficient_sha256',
          'coefficients','status','label','t','r','scoring_status','scoreable',
          'in_baseline','baseline_unlocked','disc_source','field_disc_abs',
          'scoring_disc_abs','poly_disc_abs','mixed_disc_abs']

def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2)+'\n')

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ledger',required=True,type=Path)
    ap.add_argument('--output',type=Path,default=Path('data'))
    ap.add_argument('--assets',required=True,type=Path)
    ap.add_argument('--rows-per-shard',type=int,default=200000)
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);args.assets.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(args.ledger.resolve().as_uri()+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row;c.execute('BEGIN')
    query='''SELECT p.submission_id,p.polynomial_index,s.created_at submitted_at,
      p.coefficient_hash coefficient_sha256,p.coefficients,
      CASE WHEN v.status IS NOT NULL THEN v.status
           WHEN f.submission_id IS NOT NULL THEN 'failed'
           ELSE 'unverified_in_local_snapshot' END status,
      v.label,v.t,v.r,v.scoring_status,v.scoreable,v.in_baseline,
      v.baseline_unlocked,v.disc_source,v.field_disc_abs,v.scoring_disc_abs,
      v.poly_disc_abs,v.mixed_disc_abs
      FROM polynomials p JOIN submissions s USING(submission_id)
      LEFT JOIN verifications v USING(submission_id,polynomial_index)
      LEFT JOIN failures f USING(submission_id,polynomial_index)
      ORDER BY p.submission_id,p.polynomial_index'''
    representatives={};pair_counts=Counter();score_counts=Counter();statuses=Counter()
    seen=0;accepted=0;scorable=0;shards=[];out=None;raw=None
    for record in c.execute(query):
        row=dict(record)
        assert hashlib.sha256(row['coefficients'].encode('ascii')).hexdigest()==row['coefficient_sha256']
        vals=list(map(int,row['coefficients'].split(',')))
        assert ','.join(map(str,vals))==row['coefficients']
        if row['status']=='accepted':
            assert len(vals)==25 and vals[-1]==1
            assert row['label']==f"24T{row['t']}" and 1<=row['t']<=25000
            assert row['r'] in range(0,25,2)
            accepted+=1;pair=(row['t'],row['r']);pair_counts[pair]+=1
            if row['scoreable']==1:scorable+=1;score_counts[pair]+=1
            key=(row['scoreable']!=1,len(row['coefficients']),row['coefficients'],row['submission_id'],row['polynomial_index'])
            if pair not in representatives or key<representatives[pair][0]:representatives[pair]=(key,row)
        statuses[row['status']]+=1
        if seen % args.rows_per_shard==0:
            if out:out.close();raw.close()
            path=args.assets/f'polynomials-{len(shards)+1:03d}.csv.gz'
            raw=path.open('wb')
            out=io.TextIOWrapper(gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=6),encoding='utf-8',newline='')
            writer=csv.DictWriter(out,fieldnames=FIELDS);writer.writeheader()
            shards.append({'file':path.name,'rows':0})
            print('Exporting',path.name,'after',seen,'rows',flush=True)
        writer.writerow(row);shards[-1]['rows']+=1;seen+=1
    if out:out.close();raw.close()
    reps=[representatives[p][1] for p in sorted(representatives)]
    path=args.output/'representatives.jsonl.gz'
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=9) as f:
            for row in reps:f.write((json.dumps(row,separators=(',',':'))+'\n').encode())
    with (args.output/'pairs.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['label','t','r','accepted_rows','scoreable_rows_in_local_snapshot','representative_sha256','submission_id','polynomial_index'])
        for p in sorted(representatives):
            row=representatives[p][1]
            w.writerow([row['label'],p[0],p[1],pair_counts[p],score_counts[p],row['coefficient_sha256'],row['submission_id'],row['polynomial_index']])
    with (args.output/'submissions.csv').open('w',newline='') as f:
        names=['submission_id','created_at','updated_at','queued_count','verified_count','failed_count','synced_at']
        w=csv.writer(f);w.writerow(names)
        w.writerows(c.execute('SELECT '+','.join(names)+' FROM submissions ORDER BY created_at,submission_id'))
    for shard in shards:
        p=args.assets/shard['file'];shard.update(bytes=p.stat().st_size,sha256=sha(p))
    summary={'exported_at_utc':datetime.now(timezone.utc).isoformat(),
        'source':'igp24_autoresearch/data/ledger.sqlite3, read-only transaction',
        'source_database_bytes':args.ledger.stat().st_size,
        'submission_span':dict(c.execute('SELECT min(created_at) first,max(created_at) last,count(*) submissions FROM submissions').fetchone()),
        'polynomial_rows':seen,'row_status_counts':dict(statuses),'accepted_rows':accepted,
        'accepted_distinct_pairs':len(representatives),'accepted_distinct_labels':len({p[0] for p in representatives}),
        'scoreable_rows_in_local_snapshot':scorable,'scoreable_distinct_pairs_in_local_snapshot':len(score_counts),
        'representative_selection':'Prefer a row marked scoreable in the local snapshot, then shortest coefficient text, lexicographic coefficient text, submission ID, row index. This is not a smallest-discriminant claim.',
        'coefficient_encoding':'25 comma-separated integers, constant coefficient first. SHA-256 over this exact ASCII string without newline. Large discriminants remain decimal strings.',
        'status_caveat':'Local verification/scoring states predate the final leaderboard; missing verification and pending scoring are preserved, never converted to failure or success.',
        'bulk_assets':shards}
    dump(args.output/'local-corpus-summary.json',summary)
    dump(args.assets/'manifest.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='bulk_assets'},indent=2),flush=True)
    c.close()

if __name__=='__main__':main()

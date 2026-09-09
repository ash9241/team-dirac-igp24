"""Offline validation of representatives, pair index, evidence, and public coverage."""
import csv, gzip, hashlib, json, re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads((ROOT/path).read_text())

def main():
    summary=read('data/local-corpus-summary.json')
    rows={}
    with gzip.open(ROOT/'data/representatives.jsonl.gz','rt') as f:
        for line in f:
            row=json.loads(line);pair=(row['label'],row['r'])
            assert pair not in rows
            coeff=list(map(int,row['coefficients'].split(',')))
            assert len(coeff)==25 and coeff[-1]==1
            assert ','.join(map(str,coeff))==row['coefficients']
            assert hashlib.sha256(row['coefficients'].encode('ascii')).hexdigest()==row['coefficient_sha256']
            assert row['label']==f"24T{row['t']}" and 1<=row['t']<=25000
            assert row['r'] in range(0,25,2) and row['status']=='accepted'
            rows[pair]=row
    assert len(rows)==summary['accepted_distinct_pairs']==30288
    assert len({p[0] for p in rows})==summary['accepted_distinct_labels']==8521
    with (ROOT/'data/pairs.csv').open() as f:
        index=list(csv.DictReader(f))
    assert len(index)==len(rows)
    assert sum(int(r['accepted_rows']) for r in index)==summary['accepted_rows']==1680129
    assert sum(int(r['scoreable_rows_in_local_snapshot']) for r in index)==summary['scoreable_rows_in_local_snapshot']
    for row in index:
        match=rows[(row['label'],int(row['r']))]
        assert row['representative_sha256']==match['coefficient_sha256']
        assert (row['submission_id'],int(row['polynomial_index']))==(match['submission_id'],match['polynomial_index'])
    metrics=read('evidence/verified_metrics.json')
    pilot=metrics['gq48']['rows']
    assert len(pilot)==48 and len({(r['label'],r['r']) for r in pilot})==48
    assert len({r['label'] for r in pilot})==14
    all_hits={(x['target']['label'],x['target']['r']) for x in metrics['f5_f6']['f5_hits']}
    all_hits|={(x['label'],x['r']) for x in metrics['f5_f6']['f6_rows']}
    assert len(all_hits)==49 and all_hits<=rows.keys()
    example=read('examples/f5/evidence/worked_example.json')
    line=example['certificate']['candidate']['coefficientLine']
    assert hashlib.sha256(line.encode()).hexdigest()==example['certificate']['candidate']['coefficientSha256']
    transcript=read('conversations/2026-07-17-team-dirac-update.json')
    assert len(transcript['messages'])==4
    assert sum(x['truncated'] for x in transcript['messages'])==1
    coverage_path=ROOT/'data/coverage.json'
    if coverage_path.exists():
        coverage=read('data/coverage.json')
        with (ROOT/'data/public-placements.csv').open() as f:placements=list(csv.DictReader(f))
        public={(r['label'],int(r['r'])) for r in placements}
        assert len(public)==len(placements)==coverage['public_placement_rows']==30421
        assert len(public&rows.keys())==coverage['public_pairs_with_local_accepted_representative']
        assert len(public-rows.keys())==coverage['public_pairs_without_local_accepted_representative']
        assert abs(sum(float(r['points']) for r in placements)-coverage['public_points_sum'])<0.0000001
        assert coverage['headline_minus_placement_rows']==5
    missing=[]
    # Check the reader-facing documents; archived notes can reference omitted caches.
    documents=[ROOT/'README.md',ROOT/'article/essay.md',*ROOT.glob('docs/*.md'),
               ROOT/'conversations/README.md',ROOT/'data/README.md',ROOT/'evidence/README.md',ROOT/'examples/f5/README.md',ROOT/'research/README.md']
    for path in documents:
        if not path.exists():continue
        for target in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if target.startswith(('http:','https:','#','mailto:')):continue
            target=target.split('#')[0]
            if target and not (path.parent/target).exists():missing.append(str(path.relative_to(ROOT))+': '+target)
    assert not missing,missing
    print(json.dumps({'status':'passed','representatives_checked':len(rows),'labels':8521,'f5_f6_pairs':49,'gq48_rows':48,'chat_truncation_explicit':True,'reader_links_checked':True,'galois_identification_rerun':False},indent=2))

if __name__=='__main__':main()

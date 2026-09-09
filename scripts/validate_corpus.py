"""Validate all downloaded CSV shards against the release manifest and schema."""
import argparse, csv, gzip, hashlib, json, re
from collections import Counter
from pathlib import Path
from download_corpus import digest

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('directory',type=Path);args=ap.parse_args()
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'data/local-corpus-summary.json').read_text())
    total=0;statuses=Counter();pairs=set()
    for item in manifest['bulk_assets']:
        p=args.directory/item['file'];assert p.stat().st_size==item['bytes'] and digest(p)==item['sha256']
        count=0
        with gzip.open(p,'rt',newline='') as f:
            for r in csv.DictReader(f):
                assert re.fullmatch(r'sub_[a-f0-9]{32}',r['submission_id'])
                assert r['polynomial_index'].isdigit()
                assert re.fullmatch(r'[0-9T:.Z+\-]+',r['submitted_at'])
                assert r['status'] in {'accepted','failed','unverified_in_local_snapshot'}
                assert re.fullmatch(r'-?\d+(,-?\d+)*',r['coefficients'])
                assert hashlib.sha256(r['coefficients'].encode('ascii')).hexdigest()==r['coefficient_sha256']
                if r['label']:assert re.fullmatch(r'24T[0-9]+',r['label'])
                for name in ['t','r','scoreable','in_baseline','baseline_unlocked','field_disc_abs','scoring_disc_abs','poly_disc_abs','mixed_disc_abs']:
                    assert not r[name] or re.fullmatch(r'\d+',r[name]),name
                assert r['scoring_status'] in {'','pending','scoreable','no_score','not_scoreable'}
                assert not r['disc_source'] or re.fullmatch(r'[A-Za-z_\-]+',r['disc_source'])
                if r['status']=='accepted':
                    vals=list(map(int,r['coefficients'].split(',')))
                    assert len(vals)==25 and vals[-1]==1
                    assert int(r['r']) in range(0,25,2)
                    assert r['label']==f"24T{int(r['t'])}" and 1<=int(r['t'])<=25000
                    pairs.add((r['label'],r['r']))
                count+=1;statuses[r['status']]+=1
        assert count==item['rows'];total+=count
        print('Validated',p.name,count,'rows',flush=True)
    assert total==manifest['polynomial_rows'] and dict(statuses)==manifest['row_status_counts']
    assert len(pairs)==manifest['accepted_distinct_pairs']
    print(json.dumps({'status':'passed','rows':total,'accepted_pairs':len(pairs),'row_status_counts':dict(statuses)},indent=2))

if __name__=='__main__':main()

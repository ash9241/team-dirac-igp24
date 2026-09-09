"""Reconcile a public Dirac placement export with the local accepted-pair index."""
import argparse, csv, json, math
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('public_export',type=Path);args=ap.parse_args()
    root=Path(__file__).resolve().parents[1];out=root/'data'
    d=json.loads(args.public_export.read_text());placements=d['placements']
    public={(r['label'],r['r']) for r in placements}
    assert len(public)==len(placements)
    with (out/'pairs.csv').open() as f:local={(r['label'],int(r['r'])) for r in csv.DictReader(f)}
    fields=['label','t','r','points','kTeams','scoringDiscAbs','minScoringDiscAbs','discSource','isSolvable','baselineUnlocked','baselineDiscAbs']
    with (out/'public-placements.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(sorted(placements,key=lambda r:(r['t'],r['r'])))
    for name,pairs in [('public-pairs-without-local-representative.csv',public-local),('local-pairs-not-in-public-placements.csv',local-public)]:
        with (out/name).open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['label','r']);w.writerows(sorted(pairs,key=lambda p:(int(p[0][3:]),p[1])))
    total=math.fsum(r['points'] for r in placements)
    assert all(r["points"]>=0 for r in placements)
    report={'source_url':d['source_url'],'retrieved_at_utc':d['retrieved_at_utc'],'completed_at_utc':d['completed_at_utc'],
      'leaderboard_generated_at_utc':'2026-09-01T13:33:10Z','team':'Dirac','team_number':'IGP24-T00135',
      'public_placement_rows':len(placements),'leaderboard_headline_pair_count':30426,'headline_minus_placement_rows':30426-len(placements),'public_points_sum':total,'published_score_rounded':405.86189,'headline_minus_rounded_placement_points':405.86189-total,
      'local_accepted_pairs':len(local),'public_pairs_with_local_accepted_representative':len(public&local),
      'public_pairs_without_local_accepted_representative':len(public-local),'local_accepted_pairs_absent_from_public_placements':len(local-public),
      'coverage_fraction':len(public&local)/len(public),
      'api_discrepancy':'Pagination reached nextCursor=null with 30,421 distinct rows, while the same published leaderboard reports 30,426. Per-row points are rounded. The five-row discrepancy is unresolved; no rows were invented to close it.',
      'qualification':'Matches pair labels and real-root counts, not necessarily the final best-discriminant polynomial. Public placements supply no polynomial coefficients. Local acceptance and final public scoring are different snapshots.',
      'authenticated_refresh':'A read-only attempt to retrieve updated submission details returned HTTP 403. No missing final receipts or coefficients were fabricated.'}
    (out/'coverage.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()

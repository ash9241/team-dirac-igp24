"""Fetch Dirac's public placements, with checkpoints and bounded request pacing.

No credentials are loaded. This sends only GET requests to the public endpoint.
The endpoint's row count may differ from the leaderboard headline; retain that
difference instead of inventing rows. See data/coverage.json for this release.
"""
import argparse, json, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORIGIN='https://server-9527.sair.foundation'
TEAM='teamv2_fb892e04a4624608ae68e61fa9b5250b'
PATH=f'/api/igp24/leaderboard/teams/{TEAM}/placements'

def get(query):
    url=ORIGIN+PATH+'?'+urllib.parse.urlencode(query)
    for attempt in range(8):
        try:
            req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'team-dirac-public-research-archive/1'})
            with urllib.request.urlopen(req,timeout=30) as f:d=json.load(f)
            assert d['ok'];return d['data']
        except urllib.error.HTTPError as exc:
            if exc.code!=429 and exc.code<500:raise
            if attempt==7:raise
            retry=exc.headers.get('Retry-After','')
            time.sleep(float(retry) if retry.isdigit() else min(60,10*(attempt+1)))
        except (urllib.error.URLError,TimeoutError):
            if attempt==7:raise
            time.sleep(min(60,10*(attempt+1)))

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    checkpoint=args.output.with_suffix('.checkpoint.json')
    prior=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    rows=prior.get('rows',[]);cursor=prior.get('cursor');pages=prior.get('pages',0)
    started=prior.get('started_at_utc',datetime.now(timezone.utc).isoformat());seen=set()
    while True:
        query={'limit':100}
        if cursor:query['cursor']=cursor
        d=get(query);assert d['teamId']==TEAM
        rows.extend(d['placements']);pages+=1;cursor=d.get('nextCursor')
        if pages%20==0:print(len(rows),'rows',flush=True)
        if not cursor:break
        assert cursor not in seen;seen.add(cursor)
        checkpoint.parent.mkdir(parents=True,exist_ok=True)
        checkpoint.write_text(json.dumps({'rows':rows,'cursor':cursor,'pages':pages,'started_at_utc':started}))
        time.sleep(1.6)
    assert len(rows)==len({(r['label'],r['r']) for r in rows})
    result={'source_url':ORIGIN+PATH,'retrieved_at_utc':started,'completed_at_utc':datetime.now(timezone.utc).isoformat(),
            'pages':pages,'response_metadata':{k:v for k,v in d.items() if k not in ['placements','nextCursor']},'placements':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Completed pagination:',len(rows),'rows')

if __name__=='__main__':main()

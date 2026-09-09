"""Print an archived representative for a degree-24 group and real-root count."""
import argparse, gzip, json, re
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('label');ap.add_argument('r',type=int)
    args=ap.parse_args()
    if not re.fullmatch(r'24T[1-9][0-9]*',args.label) or args.r not in range(0,25,2):
        ap.error('Use a label such as 24T15308 and an even real-root count from 0 to 24.')
    path=Path(__file__).resolve().parents[1]/'data/representatives.jsonl.gz'
    with gzip.open(path,'rt') as f:
        for line in f:
            row=json.loads(line)
            if (row['label'],row['r'])==(args.label,args.r):
                print(json.dumps(row,indent=2));return
    raise SystemExit('No accepted representative for that pair in the exported local snapshot.')

if __name__=='__main__':main()

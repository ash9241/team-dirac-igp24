"""Download the v1.0.0 corpus release assets and verify their SHA-256 hashes."""
import argparse, hashlib, json, urllib.request
from pathlib import Path

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=Path('downloads'))
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'data/local-corpus-summary.json').read_text())
    for item in manifest['bulk_assets']:
        name=item['file'];assert Path(name).name==name
        target=args.output/name
        if target.exists() and digest(target)==item['sha256']:
            print('Verified existing',name);continue
        url='https://github.com/ash9241/team-dirac-igp24/releases/download/v1.0.0/'+name
        temp=target.with_suffix(target.suffix+'.part')
        print('Downloading',name,flush=True)
        with urllib.request.urlopen(url,timeout=90) as response,temp.open('wb') as f:
            while chunk:=response.read(1024*1024):f.write(chunk)
        if temp.stat().st_size!=item['bytes'] or digest(temp)!=item['sha256']:
            raise RuntimeError('Size or SHA-256 mismatch for '+name)
        temp.replace(target)
    print('All corpus shards verified.')

if __name__=='__main__':main()

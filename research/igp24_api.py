#!/usr/bin/env python3
"""Minimal client for the SAIR IGP24 competition API.

Usage:
  python3 igp24_api.py me                    # my leaderboard entry
  python3 igp24_api.py subs [limit]          # my submissions (default 20)
  python3 igp24_api.py progress [t]          # label progress (optionally one 24Tt number)
  python3 igp24_api.py submit file.txt       # submit polynomials (one per line: a0,a1,...,a24)
"""
import json
import sys
import urllib.request

from igp24_config import API_BASE as BASE, api_key


def call(path, payload=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.7.1",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "me"

    if cmd == "me":
        print(json.dumps(call("/leaderboard/me"), indent=2))

    elif cmd == "subs":
        limit = sys.argv[2] if len(sys.argv) > 2 else "20"
        data = call(f"/submissions/me?limit={limit}")
        for s in data["data"]["items"]:
            polys = s.get("verifiedPolynomials") or []
            scoreable = sum(1 for p in polys if p.get("scoreable"))
            print(f"{s['submissionId']}  {s['createdAt']}  "
                  f"polys={len(polys)}  scoreable={scoreable}")

    elif cmd == "progress":
        if len(sys.argv) > 2:
            t = int(sys.argv[2])
            data = call(f"/labels/progress?limit=1&cursor=&includeEmpty=true&t={t}")
            print(json.dumps(data, indent=2))
        else:
            data = call("/labels/progress?limit=1000&includeEmpty=false")
            labels = data["data"]["labels"]
            print(f"{len(labels)} labels on first page "
                  f"(nextCursor={data['data']['nextCursor']})")
            for lab in labels[:20]:
                print(f"  {lab['label']:>9}  teams={lab['teamCount']:<3}  "
                      f"found r={lab['discoveredSignatures']}  "
                      f"open r={lab['remainingSignatures']}")

    elif cmd == "submit":
        lines = [
            ln.strip()
            for ln in open(sys.argv[2])
            if ln.strip() and not ln.strip().startswith("#")
        ]
        print(f"Submitting {len(lines)} polynomials...")
        resp = call("/submissions", {"payload": {"polynomials": lines}})
        print(json.dumps(resp, indent=2))

    else:
        print(__doc__)


if __name__ == "__main__":
    main()

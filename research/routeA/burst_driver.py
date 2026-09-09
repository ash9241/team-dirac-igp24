#!/usr/bin/env python3
"""Operation Long Night, phase 2: the burst.

Fires the vault in one pass:
  1. force-refresh progress; drop vault entries whose pair is no longer
     open/raidable (or already ours)
  2. rank pairs: unclaimed (1.0 pt) before raids (0.5), then by census grade
  3. take up to PER_PAIR candidates per pair, pack into 1,000-line payloads
  4. submit sequentially with spacing; ingest asynchronously afterwards
     (verification is queued server-side; results are collected in a sweep)
  5. online-learn + gold-ledger every ingest

RUN CHECKLIST (manual, on burst day):
  - touch routeA STOP for the daemon first (free budget + cores)
  - fresh UTC day (submission budget resets)
  - python3 burst_driver.py [--dry-run] [--max-subs N]
"""
import hashlib
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from routeA import api, HASHES  # noqa: E402
from routeA_daemon import open_pairs_now, GOLD_F, KNOW  # noqa: E402
from oracle import Oracle  # noqa: E402
import progress_sync  # noqa: E402

VAULT_F = os.path.join(HERE, "vault", "vault.jsonl")
PER_PAIR = 3
MAX_SUBS = 750
SPACING = 25          # seconds between submissions
LOG_F = os.path.join(HERE, "vault", "burst.log")


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S ") + "[burst] " + msg
    print(line, flush=True)
    with open(LOG_F, "a") as f:
        f.write(line + "\n")


def main():
    dry = "--dry-run" in sys.argv
    if not dry:
        raise SystemExit(
            "The blind burst driver is disabled. Use routeA/controller.py --execute "
            "for refreshed, reconciled, gated waves."
        )
    max_subs = MAX_SUBS
    if "--max-subs" in sys.argv:
        max_subs = int(sys.argv[sys.argv.index("--max-subs") + 1])

    progress_sync.refresh(force=True)
    open_now = open_pairs_now()
    unc = set(map(tuple, json.load(open(os.path.join(
        os.path.dirname(HERE), "daemon", "data", "unclaimed_nonbaseline.json")))))

    by_pair = defaultdict(list)
    n_total = 0
    for ln in open(VAULT_F):
        rec = json.loads(ln)
        n_total += 1
        pair = (rec["tgt"], rec["r"])
        if pair in open_now and len(by_pair[pair]) < PER_PAIR:
            by_pair[pair].append(rec)
    # rank: unclaimed first (full point), then raids
    ranked = sorted(by_pair.items(),
                    key=lambda kv: (0 if kv[0] in unc else 1, kv[0][0]))
    lines, manifest = [], []
    for pair, recs in ranked:
        for rec in recs:
            lines.append(rec["line"])
            manifest.append(rec)
    log(f"vault {n_total} entries -> {len(by_pair)} live pairs "
        f"({sum(1 for p in by_pair if p in unc)} unclaimed, rest raids) "
        f"-> {len(lines)} lines to fire")
    if dry:
        log("dry-run: stopping before submission")
        return

    batches = [(lines[i:i + 1000], manifest[i:i + 1000])
               for i in range(0, len(lines), 1000)][:max_subs]
    sids = []
    for bi, (bl, bm) in enumerate(batches):
        resp = api("/submissions", {"payload": {"polynomials": bl}})
        sid = resp["data"]["submissionId"]
        sids.append((sid, bm))
        with open(HASHES, "a") as f:
            for ln2 in bl:
                f.write(hashlib.sha1(ln2.encode()).hexdigest()[:20] + "\n")
        log(f"fired {bi + 1}/{len(batches)}: {sid} ({len(bl)} lines)")
        time.sleep(SPACING)

    # ingest sweep
    log("all fired; ingest sweep begins")
    orc = Oracle()
    done, gold_n = set(), 0
    for sweep in range(240):
        time.sleep(30)
        data = api("/submissions/me?limit=" + str(min(100, len(sids) + 5)))
        items = {s["submissionId"]: s for s in data["data"]["items"]}
        for sid, bm in sids:
            if sid in done:
                continue
            s = items.get(sid)
            if not s or not s.get("verifiedPolynomials") \
                    or (s.get("payload") or {}).get("queuedPolynomials"):
                continue
            done.add(sid)
            open_live = open_pairs_now()
            with open(KNOW, "a") as kf:
                for p in s["verifiedPolynomials"]:
                    idx = p.get("polynomialIndex", -1)
                    if p.get("status") != "accepted" or idx < 0 or idx >= len(bm):
                        continue
                    rec = {"sid": sid, "t": p["t"], "r": p["r"],
                           "dial": bm[idx]}
                    pair = (p["t"], p["r"])
                    if pair in open_live:
                        gold_n += 1
                        rec["gold"] = True
                        with open(GOLD_F, "a") as gf:
                            gf.write(json.dumps({"pair": list(pair),
                                                 "dial": bm[idx], "sid": sid,
                                                 "at": time.strftime("%Y-%m-%d %H:%M")}) + "\n")
                    kf.write(json.dumps(rec) + "\n")
            log(f"ingested {sid} ({len(done)}/{len(sids)}), gold so far: {gold_n}")
        if len(done) == len(sids):
            break
    log(f"BURST COMPLETE: {len(done)}/{len(sids)} ingested, {gold_n} gold captures")


if __name__ == "__main__":
    main()

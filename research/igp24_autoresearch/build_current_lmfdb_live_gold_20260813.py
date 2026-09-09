#!/usr/bin/env python3
"""Acquire exact live-gold degree-24 fields from the current LMFDB API.

The competition baseline is frozen locally.  This script asks LMFDB only for
exact 24T labels that still have nonbaseline, team_count=0 target signatures,
then excludes every coefficient hash already in our ledger or outbox.  LMFDB's
``galois_label`` and ``r2`` fields provide the exact target label and real-root
signature; no heuristic group identification is used.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
API = "https://www.lmfdb.org/api/nf_fields/"


def sha256_line(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def load_profiles() -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    for path in (Path("/tmp/tc0_light_vm3.out"), Path("/tmp/tc0_light_vm4.out")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            parts = raw.split("|")
            if len(parts) != 5 or parts[0] != "GROUP":
                continue
            profiles[parts[1]] = {
                "blockSizes": parts[4],
                "order": int(parts[2]),
                "solvable": parts[3] == "true",
            }
    return profiles


def fetch_label(label: str, timeout: int) -> dict:
    offset = 0
    seen_records: set[str] = set()
    records: list[dict] = []
    pages = 0
    while True:
        query = urllib.parse.urlencode(
            {
                "_format": "json",
                "_offset": offset,
                "degree": 24,
                "galois_label": label,
            }
        )
        request = urllib.request.Request(
            f"{API}?{query}",
            headers={"User-Agent": "Dirac-IGP24 exact-field acquisition/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        page = payload.get("data", [])
        pages += 1
        added = 0
        for row in page:
            record_label = str(row.get("label", ""))
            if record_label and record_label not in seen_records:
                seen_records.add(record_label)
                records.append(row)
                added += 1
        if not page or len(page) < 100 or added == 0:
            break
        offset += len(page)
    return {"label": label, "pages": pages, "records": records}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-order", type=int, default=12288)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--tag", default="wave1")
    args = parser.parse_args()

    profiles = load_profiles()
    connection = sqlite3.connect(ROOT / "data" / "ledger.sqlite3")
    try:
        live_rows = connection.execute(
            """
            SELECT t.label,t.r
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned ON owned.label=t.label AND owned.r=t.r
            WHERE t.team_count=0 AND b.label IS NULL AND owned.label IS NULL
            ORDER BY CAST(SUBSTR(t.label,4) AS INTEGER),t.r
            """
        ).fetchall()
        ledger_hashes = {
            str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")
        }
    finally:
        connection.close()

    live = {(str(label), int(r)) for label, r in live_rows}
    labels = sorted(
        {
            label
            for label, _r in live
            if label in profiles
            and profiles[label]["solvable"]
            and profiles[label]["order"] <= args.max_order
        },
        key=lambda value: int(value[3:]),
    )

    outbox_hashes: set[str] = set()
    for path in (ROOT / "outbox").glob("*.txt"):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line:
                outbox_hashes.add(sha256_line(line))

    started = time.monotonic()
    results: list[dict] = []
    failures: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_label = {
            pool.submit(fetch_label, label, args.timeout): label for label in labels
        }
        for index, future in enumerate(concurrent.futures.as_completed(future_to_label), 1):
            label = future_to_label[future]
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append({"error": f"{type(exc).__name__}: {exc}", "label": label})
            if index % 50 == 0:
                print(json.dumps({"completedLabels": index, "failures": len(failures)}), flush=True)

    candidates: list[dict] = []
    skipped = Counter()
    seen_hashes: set[str] = set()
    for result in results:
        requested_label = result["label"]
        for row in result["records"]:
            if str(row.get("galois_label")) != requested_label or int(row.get("degree", 0)) != 24:
                skipped["api_label_or_degree_mismatch"] += 1
                continue
            r = 24 - 2 * int(row["r2"])
            pair = (requested_label, r)
            if pair not in live:
                skipped["signature_not_live_gold"] += 1
                continue
            coefficients = [int(value) for value in row["coeffs"]]
            if len(coefficients) != 25 or coefficients[-1] != 1:
                skipped["not_monic_degree24"] += 1
                continue
            line = ",".join(str(value) for value in coefficients)
            digest = sha256_line(line)
            if digest in ledger_hashes:
                skipped["already_in_ledger"] += 1
                continue
            if digest in outbox_hashes:
                skipped["already_in_outbox"] += 1
                continue
            if digest in seen_hashes:
                skipped["duplicate_acquisition_hash"] += 1
                continue
            seen_hashes.add(digest)
            candidates.append(
                {
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "fieldDiscriminantAbs": str(row["disc_abs"]),
                    "lmfdbLabel": str(row["label"]),
                    "targetLabel": requested_label,
                    "targetR": r,
                }
            )

    candidates.sort(key=lambda row: (int(row["targetLabel"][3:]), row["targetR"], int(row["fieldDiscriminantAbs"])))
    stem = f"current_lmfdb_live_gold_{args.tag}_20260813"
    manifest = ROOT / "outbox" / f"{stem}.txt"
    certificate = ROOT / "data" / f"{stem}_certificate.json"
    manifest.write_text("".join(row["coefficientLine"] + "\n" for row in candidates), encoding="utf-8")
    payload = {
        "api": API,
        "campaign": "current-lmfdb-live-gold-acquisition",
        "candidateCount": len(candidates),
        "candidates": candidates,
        "distinctTargetLabels": len({row["targetLabel"] for row in candidates}),
        "distinctTargetPairs": len({(row["targetLabel"], row["targetR"]) for row in candidates}),
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactSourceClaims": ["degree", "galois_label", "r2", "disc_abs", "coeffs"],
        "failures": failures,
        "labelsQueried": len(labels),
        "liveGoldPairsInScope": sum(
            1
            for pair in live
            if pair[0] in profiles and profiles[pair[0]]["order"] <= args.max_order
        ),
        "maxGroupOrder": args.max_order,
        "networkCalls": sum(result["pages"] for result in results) + len(failures),
        "output": str(manifest.resolve()),
        "outputSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "recordsFetched": sum(len(result["records"]) for result in results),
        "skipped": dict(sorted(skipped.items())),
        "submissionCalls": 0,
    }
    certificate.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "candidates"}, sort_keys=True))
    return 0 if candidates else 2


if __name__ == "__main__":
    raise SystemExit(main())

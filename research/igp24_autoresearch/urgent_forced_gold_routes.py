#!/usr/bin/env python3
"""Fast read-only census of deterministic one-step routes to current gold pairs."""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def jsonl(path):
    with path.open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def main():
    conn = sqlite3.connect(f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    gold = {
        (str(row["label"]), int(row["r"]))
        for row in conn.execute(
            """
            SELECT t.label,t.r FROM targets t
            WHERE t.team_count=0
              AND NOT EXISTS (
                SELECT 1 FROM baseline_pairs b
                WHERE b.label=t.label AND b.r=t.r
              )
              AND NOT EXISTS (
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }
    orbits = {}
    for row in jsonl(DATA / "pair_orbit_map.jsonl"):
        targets = list(row.get("targets") or [])
        if len(targets) == 1 and str(targets[0]["targetLabel"]) != str(row["sourceLabel"]):
            orbits[str(row["sourceLabel"])] = row
    profiles = {}
    for name in (
        "pair_signature_map.jsonl",
        "rank12_route_signature_profiles.jsonl",
        "agent_pair_sibling_shard2_profiles.jsonl",
        "agent_rank10_pair_stage2_profiles.jsonl",
    ):
        for row in jsonl(DATA / name):
            if row.get("status") in (None, "certified"):
                profiles[str(row["sourceLabel"])] = row
    forced = {}
    for source_label, orbit in orbits.items():
        target_label = str(orbit["targets"][0]["targetLabel"])
        owned_rs = [
            int(row[0])
            for row in conn.execute(
                "SELECT DISTINCT r FROM verifications WHERE label=? AND scoreable=1",
                (source_label,),
            )
        ]
        for source_r in owned_rs:
            if source_r == 24:
                pair = (target_label, 24)
            else:
                profile = profiles.get(source_label)
                if profile is None:
                    continue
                compatible = [
                    row for row in profile.get("profiles", [])
                    if int(row["sourceR"]) == source_r
                ]
                realized = set()
                valid = bool(compatible)
                for class_row in compatible:
                    signatures = list(class_row.get("orbitSignatures") or [])
                    if len(signatures) != 1:
                        valid = False
                        break
                    sig = signatures[0]
                    if str(sig["targetLabel"]) != target_label:
                        valid = False
                        break
                    realized.add((target_label, int(sig["targetR"])))
                if not valid or len(realized) != 1:
                    continue
                pair = next(iter(realized))
            if pair in gold:
                forced[(source_label, source_r)] = pair
    routes = []
    for (source_label, source_r), target in forced.items():
        for row in conn.execute(
            """
            SELECT v.submission_id,v.polynomial_index,p.coefficient_hash,
                   length(p.original_line) AS coefficient_bytes
            FROM verifications v
            JOIN polynomials p USING(submission_id,polynomial_index)
            WHERE v.label=? AND v.r=? AND v.scoreable=1
            ORDER BY length(p.original_line),v.submission_id,v.polynomial_index
            LIMIT 4
            """,
            (source_label, source_r),
        ):
            routes.append(
                {
                    "sourceSubmissionId": str(row["submission_id"]),
                    "sourcePolynomialIndex": int(row["polynomial_index"]),
                    "sourceLabel": source_label,
                    "sourceR": source_r,
                    "sourceCoefficientSha256": str(row["coefficient_hash"]),
                    "sourceCoefficientBytes": int(row["coefficient_bytes"]),
                    "targetLabel": target[0],
                    "targetR": target[1],
                    "orbitMap": str((DATA / "pair_orbit_map.jsonl").resolve()),
                }
            )
    routes.sort(key=lambda x: (x["sourceCoefficientBytes"], x["targetLabel"], x["targetR"]))
    distinct = {(r["targetLabel"], r["targetR"]) for r in routes}
    print(json.dumps({"goldPairs": len(gold), "forcedTargets": len(distinct), "routes": routes[:100]}, indent=2))


if __name__ == "__main__":
    main()

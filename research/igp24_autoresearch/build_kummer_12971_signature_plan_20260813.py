#!/usr/bin/env python3
"""Build exhaustive 24T18495 -> 24T12971 pair-product signature plans."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/ledger.sqlite3")
    parser.add_argument(
        "--reference",
        type=Path,
        default=ROOT / "data/current_lower_kummer_expansion_value_gold_resample1_20260813_20260812.json",
    )
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=2)
    args = parser.parse_args()

    reference = json.loads(args.reference.read_text())
    templates = {}
    for group in reference["groups"]:
        if int(group["subsetSize"]) == 2 and int(group["source"]["r"]) in (8, 16):
            templates.setdefault(int(group["source"]["r"]), group)
    if set(templates) != {8, 16}:
        raise RuntimeError("reference plan lacks singleton k=2 templates for r=8 and r=16")

    con = sqlite3.connect(args.ledger)
    rows = con.execute(
        """
        SELECT v.r, p.coefficients, v.submission_id, v.polynomial_index,
               v.field_disc_abs
        FROM verifications AS v
        JOIN polynomials AS p USING (submission_id, polynomial_index)
        WHERE v.status = 'accepted' AND v.scoreable = 1
          AND v.label = '24T18495' AND v.r IN (8, 16)
        ORDER BY v.r DESC, length(p.coefficients), p.coefficients
        """
    ).fetchall()

    seen = set()
    groups = []
    for r, coefficient_line, submission_id, polynomial_index, disc in rows:
        coefficients = [int(value) for value in coefficient_line.split(",")]
        if len(coefficients) != 25 or coefficients[-1] != 1:
            continue
        if any(coefficients[index] for index in range(1, 24, 2)):
            continue
        quotient = coefficients[::2]
        quotient_line = ",".join(map(str, quotient))
        quotient_sha = hashlib.sha256(quotient_line.encode("ascii")).hexdigest()
        if quotient_sha in seen:
            continue
        seen.add(quotient_sha)
        group = copy.deepcopy(templates[int(r)])
        group["groupOrdinal"] = len(groups) + 1
        group["quotientLine"] = quotient_line
        group["quotientSha256"] = quotient_sha
        group["source"] = {
            "coefficientBytes": len(coefficient_line),
            "coefficientSha256": hashlib.sha256(coefficient_line.encode("ascii")).hexdigest(),
            "fieldDiscAbs": disc,
            "label": "24T18495",
            "polynomialIndex": int(polynomial_index),
            "r": int(r),
            "submissionId": submission_id,
            "t": 18495,
        }
        groups.append(group)

    shard_sizes = []
    for shard in range(args.shards):
        shard_groups = groups[shard::args.shards]
        payload = {
            "schemaVersion": "kummer-12971-signature-exhaustive-v1",
            "sourceRows": len(rows),
            "distinctQuotients": len(groups),
            "shardIndex": shard,
            "shardCount": args.shards,
            "groupCount": len(shard_groups),
            "groups": shard_groups,
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        output = Path(f"{args.output_prefix}_shard{shard}.json")
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        shard_sizes.append(len(shard_groups))
    print(json.dumps({"sourceRows": len(rows), "distinctQuotients": len(groups), "shardSizes": shard_sizes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Stage exact archived presentations that improve Dirac's owned pairs."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as outbox_audit
import run_low_contention_sequential as lane
import stage_alex_exact_intersection as exact_stage
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
def ratio_score(minimum: int, discriminant: int) -> float:
    if discriminant <= minimum:
        return 1.0
    return min(1.0, math.log(minimum) / math.log(discriminant))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="wave1")
    args = parser.parse_args()
    if not args.tag.replace("-", "").replace("_", "").isalnum():
        raise ValueError("tag must contain only letters, digits, underscores, or hyphens")
    manifest_path = ROOT / f"outbox/current_discriminant_upgrades_{args.tag}_20260812.txt"
    index_path = DATA / f"current_discriminant_upgrades_{args.tag}_20260812.jsonl"
    certificate_path = (
        DATA / f"current_discriminant_upgrades_{args.tag}_20260812_certificate.json"
    )
    for path in (manifest_path, index_path, certificate_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        pool, pair_index, corpus_meta = exact_stage.reconstruct_exact_corpus(connection)
        known_hashes = exact_single.query_known_hashes(connection, set(pool))
        receipt_hashes, _receipt_pairs, receipt_meta = exact_single.receipt_exclusions(
            lane.RECEIPTS, DATA, connection, pair_index
        )
        outbox_hashes, _outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
            pair_index, connection
        )
        excluded_hashes = known_hashes | receipt_hashes | outbox_hashes

        accepted_by_pair: dict[tuple[str, int], list[int]] = defaultdict(list)
        for row in connection.execute(
            "SELECT label,r,field_disc_abs FROM verifications "
            "WHERE status='accepted' AND scoreable=1 AND label IS NOT NULL "
            "AND r IS NOT NULL AND field_disc_abs IS NOT NULL"
        ):
            accepted_by_pair[(str(row[0]), int(row[1]))].append(int(row[2]))
        current_best = {pair: min(values) for pair, values in accepted_by_pair.items()}
        targets = {
            (str(row["label"]), int(row["r"])): dict(row)
            for row in connection.execute("SELECT * FROM targets")
        }
        baseline = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }

        candidates = []
        for digest, row in pool.items():
            pair = tuple(row["pair"])
            field_disc = row.get("fieldDiscriminantAbs")
            if (
                digest in excluded_hashes
                or pair in baseline
                or pair not in current_best
                or pair not in targets
                or field_disc is None
                or int(field_disc) >= current_best[pair]
            ):
                continue
            target = targets[pair]
            minimum = target.get("minimum_disc_abs")
            if minimum is None:
                continue
            team_count = max(1, int(target["team_count"]))
            base = 2.0 ** (-(team_count - 1))
            old_ratio = ratio_score(int(minimum), current_best[pair])
            new_ratio = ratio_score(int(minimum), int(field_disc))
            candidates.append(
                {
                    "coefficientLine": row["coefficientLine"],
                    "coefficientSha256": digest,
                    "pair": f"{pair[0]}/r{pair[1]}",
                    "currentBestFieldDiscriminantAbs": str(current_best[pair]),
                    "candidateFieldDiscriminantAbs": str(field_disc),
                    "globalMinimumFieldDiscriminantAbs": str(minimum),
                    "improvementFactor": current_best[pair] / int(field_disc),
                    "teamCount": team_count,
                    "projectedPointGain": base * max(0.0, new_ratio - old_ratio),
                    "families": sorted(row["families"]),
                    "proofArtifacts": [
                        value for value in row["proofs"] if value.get("artifact")
                    ],
                }
            )

        candidates.sort(
            key=lambda row: (
                -float(row["projectedPointGain"]),
                -float(row["improvementFactor"]),
                row["pair"],
                row["coefficientSha256"],
            )
        )
        best = {}
        for row in candidates:
            best.setdefault(row["pair"], row)
        selected = list(best.values())
    finally:
        connection.close()

    manifest = "".join(row["coefficientLine"] + "\n" for row in selected).encode()
    public_rows = [
        {key: value for key, value in row.items() if key != "coefficientLine"}
        for row in selected
    ]
    index = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in public_rows
    ).encode()
    certificate = {
        "schemaVersion": "current-owned-discriminant-upgrade-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "exact_archived_owned_pair_upgrades_staged_not_submitted",
        "candidateRows": len(selected),
        "projectedPointGain": sum(row["projectedPointGain"] for row in selected),
        "corpus": corpus_meta,
        "exclusions": {
            "knownHashes": len(known_hashes),
            "receiptHashes": len(receipt_hashes),
            "outboxHashes": len(outbox_hashes),
            "receiptAudit": receipt_meta,
            "outboxDistinctHashes": outbox_meta["distinctCoefficientHashes"],
        },
        "artifacts": {
            "manifest": str(manifest_path.relative_to(ROOT)),
            "index": str(index_path.relative_to(ROOT)),
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    exact_stage.write_bundle(
        [
            (manifest_path, manifest),
            (index_path, index),
            (certificate_path, exact_stage.json_bytes(certificate)),
        ]
    )
    print(json.dumps(certificate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

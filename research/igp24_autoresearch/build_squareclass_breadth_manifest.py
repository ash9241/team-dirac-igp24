#!/usr/bin/env python3
"""Select a fail-closed, submission-free squareclass breadth workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
ACTIONS = ROOT / "data/agent_gold_b_even_twist_action_map.jsonl"
OUTPUT = ROOT / "data/current_squareclass_breadth_manifest_20260806.json"
RANK10 = ROOT / "data/current_rank10_t00171_unique_20260806.jsonl"
RANK12 = ROOT / "data/current_rank12_t00087_unique_20260806.jsonl"
RANK14 = ROOT / "data/current_rank14_t00134_unique_20260806.jsonl"
LANES = {260: 8, 81: 5, 185: 5, 195: 5, 222: 5, 141: 5, 187: 5}
PROVEN = {260, 81, 185, 195}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--actions", type=Path, default=ACTIONS)
    parser.add_argument("--rank10", type=Path, default=RANK10)
    parser.add_argument("--rank12", type=Path, default=RANK12)
    parser.add_argument("--rank14", type=Path, default=RANK14)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    label_q: dict[str, set[int]] = defaultdict(set)
    for row in jsonl(args.actions):
        label = str(row["sourceLabel"])
        for system in row.get("systems") or []:
            label_q[label].add(int(system["blockActionT12"]))
    q_labels: dict[int, set[str]] = defaultdict(set)
    for label, qs in label_q.items():
        for q in qs:
            q_labels[q].add(label)
    raid_segments = [
        ("rank10", "IGP24-T00171", jsonl(args.rank10)),
        ("rank12", "IGP24-T00087", jsonl(args.rank12)),
        ("rank14", "IGP24-T00134", jsonl(args.rank14)),
    ]

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        owned_pairs = {
            (str(label), int(r)) for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE status='accepted' AND scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
            )
        }
        owned_labels = {label for label, _r in owned_pairs}
        lanes = []
        for q, limit in LANES.items():
            labels = sorted(q_labels[q])
            placeholders = ",".join("?" for _ in labels)
            query = (
                "SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.submission_id," 
                "v.polynomial_index,v.field_disc_abs,s.created_at "
                "FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index) "
                "JOIN submissions s USING(submission_id) "
                f"WHERE v.status='accepted' AND v.scoreable=1 AND v.label IN ({placeholders}) "
                "ORDER BY s.created_at DESC,LENGTH(p.coefficients),p.coefficient_hash"
            )
            pool = []
            seen_quotients = set()
            seen_source_labels = set()
            for row in connection.execute(query, labels):
                values = [int(value) for value in str(row[0]).split(",")]
                if len(values) != 25 or any(values[index] != 0 for index in range(1, 24, 2)):
                    continue
                quotient = ",".join(str(values[index]) for index in range(0, 25, 2))
                quotient_sha = hashlib.sha256(quotient.encode("ascii")).hexdigest()
                if quotient_sha in seen_quotients:
                    continue
                source_label = str(row[2])
                # Diversify the small pilot across verified parent labels first.
                diversity_penalty = source_label in seen_source_labels
                pool.append((diversity_penalty, {
                    "sourceSubmissionId": str(row[4]),
                    "sourcePolynomialIndex": int(row[5]),
                    "sourceLabel": source_label,
                    "sourceR": int(row[3]),
                    "sourceCoefficientSha256": str(row[1]),
                    "sourceFieldDiscriminantAbs": str(row[6]) if row[6] is not None else None,
                    "sourceCreatedAt": str(row[7]),
                    "quotientPolynomial": quotient,
                    "quotientPresentationSha256": quotient_sha,
                    "canonicalFieldSha256": None,
                    "canonicalFreshnessChecked": False,
                }))
                seen_quotients.add(quotient_sha)
                seen_source_labels.add(source_label)
                if len(pool) >= max(limit * 6, 30):
                    break
            pool.sort(key=lambda item: (item[0], -int(item[1]["sourceCreatedAt"][:4] or 0), len(item[1]["quotientPolynomial"])))
            selected = [item[1] for item in pool[:limit]]
            gold_targets = []
            for label in labels:
                if label not in owned_labels:
                    continue
                for r_value, team_count, discovered, generated_at in connection.execute(
                    "SELECT r,team_count,discovered,generated_at FROM targets WHERE label=? ORDER BY r",
                    (label,),
                ):
                    pair = (label, int(r_value))
                    if int(team_count) == 0 and not bool(discovered) and pair not in owned_pairs:
                        gold_targets.append({
                            "label": label,
                            "r": int(r_value),
                            "teamCount": 0,
                            "generatedAt": str(generated_at),
                        })
            opponent_raid_targets = []
            for opponent_rank, opponent_number, opponent_rows in raid_segments:
                for row in opponent_rows:
                    label, r_value = str(row["label"]), int(row["r"])
                    if (
                        label not in labels
                        or label not in owned_labels
                        or (label, r_value) in owned_pairs
                    ):
                        continue
                    same_group_parents = []
                    seen_parent_hashes = set()
                    parent_query = (
                        "SELECT p.coefficients,p.coefficient_hash,v.r,v.submission_id,"
                        "v.polynomial_index,v.field_disc_abs FROM verifications v "
                        "JOIN polynomials p USING(submission_id,polynomial_index) "
                        "WHERE v.status='accepted' AND v.label=? "
                        "ORDER BY LENGTH(p.coefficients),p.coefficient_hash"
                    )
                    for parent in connection.execute(parent_query, (label,)):
                        values = [int(value) for value in str(parent[0]).split(",")]
                        if len(values) != 25 or any(
                            values[index] != 0 for index in range(1, 24, 2)
                        ):
                            continue
                        if str(parent[1]) in seen_parent_hashes:
                            continue
                        quotient = ",".join(
                            str(values[index]) for index in range(0, 25, 2)
                        )
                        same_group_parents.append({
                            "sourceSubmissionId": str(parent[3]),
                            "sourcePolynomialIndex": int(parent[4]),
                            "sourceLabel": label,
                            "sourceR": int(parent[2]),
                            "sourceCoefficientSha256": str(parent[1]),
                            "sourceFieldDiscriminantAbs": (
                                str(parent[5]) if parent[5] is not None else None
                            ),
                            "quotientPolynomial": quotient,
                            "quotientPresentationSha256": hashlib.sha256(
                                quotient.encode("ascii")
                            ).hexdigest(),
                        })
                        seen_parent_hashes.add(str(parent[1]))
                        if len(same_group_parents) == 5:
                            break
                    opponent_raid_targets.append({
                        "label": label,
                        "r": r_value,
                        "teamCount": 1,
                        "opponentRank": opponent_rank,
                        "opponent": opponent_number,
                        "preferredSameGroupParentPresentations": same_group_parents,
                    })
            lanes.append({
                "quotientT12": q,
                "mode": "proven_exact_breadth" if q in PROVEN else "five_field_calibration",
                "selectedSourcePresentations": selected,
                "currentGoldTargets": gold_targets,
                "currentGoldTargetCount": len(gold_targets),
                "currentOpponentRaidTargets": opponent_raid_targets,
                "currentOpponentRaidTargetCount": len(opponent_raid_targets),
                "submissionReady": False,
                "mandatoryPreflight": [
                    "PARI polredabs canonicalize and reject previously explored field hashes",
                    "recover the accepted parent's unique 12x2 quotient or prove exact 12Tq independently",
                    "reduce compact K(S,2) generators modulo squares before expansion",
                    "require exact sign-state image membership and singleton core alignment",
                    "use independent containment plus separating-resolvent classification before any 24T claim",
                    "refresh tc0, ownership, baseline, receipt, outbox, and coefficient-hash gates",
                ],
                "promotionGate": (
                    "scale breadth only after one independently exact target-pair hit"
                    if q in PROVEN else
                    "at least 4/5 exact-label agreement and at least one current target-pair hit"
                ),
            })
    finally:
        connection.close()

    payload = {
        "schemaVersion": "current-squareclass-breadth-manifest-v1",
        "submissionAuthorized": False,
        "cloudLaunchAuthorized": False,
        "lanes": lanes,
        "totalSelectedSourcePresentations": sum(len(row["selectedSourcePresentations"]) for row in lanes),
        "note": "quotient presentation hashes are not field-isomorphism hashes; canonical freshness is a mandatory worker-side gate",
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "selected": payload["totalSelectedSourcePresentations"],
        "lanes": [{"q": row["quotientT12"], "sources": len(row["selectedSourcePresentations"]), "goldTargets": row["currentGoldTargetCount"], "soloRaids": row["currentOpponentRaidTargetCount"]} for row in lanes],
    }, indent=2))


if __name__ == "__main__":
    main()

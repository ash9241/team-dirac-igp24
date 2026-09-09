#!/usr/bin/env sage
"""Rank every remaining full-rank 12x2 character cluster by live breadth.

For each quotient action 12Tq occurring with kernel 2^11, this combines the
frozen strict-tc0 target list with totally-real degree-12 quotient fields
recovered from accepted scoreable even polynomials.  It is an inventory only:
no Selmer search, coefficient search, network call, staging, or submission.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
OUTPUT = DATA / "character_fullrank_tr_priority_inventory_20260730.json"
AUDITED_OR_ACTIVE = {
    12,
    28,
    36,
    38,
    66,
    77,
    78,
    80,
    81,
    108,
    109,
    138,
    156,
    169,
    210,
    214,
    243,
    266,
    267,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def structural_maps():
    label_to_quotients = defaultdict(set)
    quotient_to_labels = defaultdict(set)
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            label = str(row["label"])
            for system in row.get("blockSystems", []):
                action = str(system.get("quotientActionLabel", ""))
                if (
                    system.get("shape") == "12x2"
                    and int(system.get("blockKernelOrder", 0)) == 2**11
                    and action.startswith("12T")
                ):
                    quotient_t = int(action[3:])
                    label_to_quotients[label].add(quotient_t)
                    quotient_to_labels[quotient_t].add(label)
    return label_to_quotients, quotient_to_labels


def main() -> int:
    require(not OUTPUT.exists(), f"refusing to overwrite {OUTPUT}")
    label_to_quotients, quotient_to_labels = structural_maps()
    all_labels = sorted(label_to_quotients)
    require(all_labels, "no full-rank structural labels")
    placeholders = ",".join("?" for _label in all_labels)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        live_rows = connection.execute(
            f"""
            SELECT t.label,t.r,t.team_count,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label IN ({placeholders})
              AND t.team_count=0
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.label,t.r
            """,
            tuple(all_labels),
        ).fetchall()
        source_rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash
            """,
            tuple(all_labels),
        ).fetchall()
    finally:
        connection.close()

    live_by_quotient = defaultdict(list)
    generated_at = set()
    for label, r, team_count, timestamp in live_rows:
        require(int(team_count) == 0, "nonzero team count in strict query")
        generated_at.add(str(timestamp))
        for quotient_t in label_to_quotients[str(label)]:
            live_by_quotient[quotient_t].append(
                {
                    "label": str(label),
                    "r": int(r),
                }
            )

    ring = PolynomialRing(QQ, "y")
    fields = {}
    accepted_even_irreducible = 0
    totally_real_source_rows = 0
    for (
        label,
        source_r,
        submission_id,
        polynomial_index,
        coefficient_hash,
        coefficient_text,
    ) in source_rows:
        coefficients = [ZZ(value) for value in str(coefficient_text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        accepted_even_irreducible += 1
        if int(quotient.number_of_real_roots()) != 12:
            continue
        totally_real_source_rows += 1
        reduced = ring(pari(quotient).polredabs())
        canonical_line = ",".join(str(ZZ(value)) for value in reduced.list())
        field_hash = hashlib.sha256(canonical_line.encode("ascii")).hexdigest()
        for quotient_t in label_to_quotients[str(label)]:
            if quotient_t not in live_by_quotient:
                continue
            item = fields.setdefault(
                (quotient_t, field_hash),
                {
                    "canonicalPolynomial": canonical_line,
                    "fieldCanonicalSha256": field_hash,
                    "quotientT12": quotient_t,
                    "sourceRows": [],
                },
            )
            item["sourceRows"].append(
                {
                    "coefficientSha256": str(coefficient_hash),
                    "label": str(label),
                    "polynomialIndex": int(polynomial_index),
                    "r": int(source_r),
                    "submissionId": str(submission_id),
                }
            )

    fields_by_quotient = defaultdict(list)
    for (quotient_t, _field_hash), field in sorted(fields.items()):
        field["sourceLabels"] = sorted(
            {row["label"] for row in field["sourceRows"]}
        )
        field["sourceSignatures"] = sorted(
            {row["r"] for row in field["sourceRows"]}
        )
        fields_by_quotient[quotient_t].append(field)

    clusters = []
    for quotient_t, pairs in live_by_quotient.items():
        distinct_pairs = sorted(
            {(row["label"], row["r"]) for row in pairs},
            key=lambda pair: (int(pair[0][3:]), pair[1]),
        )
        canonical_fields = fields_by_quotient.get(quotient_t, [])
        labels = sorted(
            {label for label, _r in distinct_pairs},
            key=lambda label: int(label[3:]),
        )
        clusters.append(
            {
                "activeOrAudited": quotient_t in AUDITED_OR_ACTIVE,
                "canonicalTotallyRealFieldCount": len(canonical_fields),
                "canonicalTotallyRealFields": canonical_fields,
                "livePairCount": len(distinct_pairs),
                "livePairs": [
                    {"label": label, "r": r} for label, r in distinct_pairs
                ],
                "quotientT12": quotient_t,
                "sourceStructuralLabels": sorted(
                    quotient_to_labels[quotient_t],
                    key=lambda label: int(label[3:]),
                ),
                "targetLabels": labels,
            }
        )
    clusters.sort(
        key=lambda row: (
            row["activeOrAudited"],
            -row["livePairCount"],
            -row["canonicalTotallyRealFieldCount"],
            row["quotientT12"],
        )
    )

    payload = {
        "audit": {
            "acceptedEvenIrreducibleRows": accepted_even_irreducible,
            "coefficientSearches": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(generated_at),
            "totallyRealSourceRows": totally_real_source_rows,
        },
        "clusters": clusters,
        "summary": {
            "fieldReadyUnauditedClusters": sum(
                not row["activeOrAudited"]
                and row["canonicalTotallyRealFieldCount"] > 0
                for row in clusters
            ),
            "fullRankClustersWithLivePairs": len(clusters),
            "livePairsAcrossClusters": sum(
                row["livePairCount"] for row in clusters
            ),
            "topUnauditedFieldReady": [
                {
                    "canonicalTotallyRealFieldCount":
                        row["canonicalTotallyRealFieldCount"],
                    "livePairCount": row["livePairCount"],
                    "quotientT12": row["quotientT12"],
                    "targetLabels": row["targetLabels"],
                }
                for row in clusters
                if not row["activeOrAudited"]
                and row["canonicalTotallyRealFieldCount"] > 0
            ][:12],
        },
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(OUTPUT, rendered)
    print(json.dumps(payload["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

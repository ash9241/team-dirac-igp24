#!/usr/bin/env sage -python
"""Audit fresh totally-real q12T138 fields from every local 12x2 parent.

This is a source census only.  It broadens the earlier full-rank inventory
from block kernel 2^11 to every degree-24 group having an exact 12x2 quotient
12T138.  Accepted scoreable ledger rows and the local LMFDB baseline CSV are
screened for monic even q(x^2) presentations.  Degree-12 quotients are checked
for irreducibility and total reality, canonicalized with PARI ``polredabs``,
and deduplicated against the complete 15-field q138 local-gate artifact.

There are no coefficient searches, network calls, staging operations, manifest
writes, or submissions.
"""

from __future__ import annotations

import csv
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
BASELINE = Path("/path/to/private-file")
PRIOR = DATA / "character_field_gate_q138_local_20260730.json"
OUTPUT = (
    DATA
    / "character_fresh_field_inventory_q138_root_q12q81_direct_20260730.json"
)
QUOTIENT_T = 138
EXPECTED_PRIOR_FIELDS = 15


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def canonical_polynomial(polynomial, ring):
    reduced = ring(pari(polynomial).polredabs())
    line = ",".join(str(ZZ(value)) for value in reduced.list())
    return line, hashlib.sha256(line.encode("ascii")).hexdigest()


def structural_sources() -> dict[str, list[dict]]:
    output = defaultdict(list)
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for system in row.get("blockSystems", []):
                if (
                    system.get("shape") == "12x2"
                    and system.get("quotientActionLabel")
                    == f"12T{QUOTIENT_T}"
                ):
                    output[str(row["label"])].append(
                        {
                            "blockKernelOrder": int(
                                system["blockKernelOrder"]
                            ),
                            "quotientActionOrder": int(
                                system["quotientActionOrder"]
                            ),
                            "systemSha256": str(system["systemSha256"]),
                        }
                    )
    require(output, f"no exact q{QUOTIENT_T} 12x2 structural parents")
    return {
        label: sorted(
            systems,
            key=lambda row: (
                row["blockKernelOrder"],
                row["systemSha256"],
            ),
        )
        for label, systems in sorted(
            output.items(), key=lambda item: int(item[0][3:])
        )
    }


def prior_hashes() -> set[str]:
    payload = json.loads(PRIOR.read_text(encoding="utf-8"))
    hashes = {
        str(field["fieldCanonicalSha256"])
        for field in payload.get("fields", [])
        if int(field.get("quotientT12", QUOTIENT_T)) == QUOTIENT_T
    }
    require(
        len(hashes) == EXPECTED_PRIOR_FIELDS,
        f"expected {EXPECTED_PRIOR_FIELDS} prior q{QUOTIENT_T} fields, "
        f"found {len(hashes)}",
    )
    return hashes


def accepted_rows(labels: list[str]) -> list[dict]:
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   v.field_disc_abs,p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash,
                     v.submission_id,v.polynomial_index
            """,
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "coefficientSha256": str(coefficient_hash),
            "coefficients": str(coefficients),
            "fieldDiscriminantAbs": str(field_disc_abs),
            "label": str(label),
            "polynomialIndex": int(polynomial_index),
            "r": int(source_r),
            "sourceCorpus": "accepted_scoreable_ledger",
            "submissionId": str(submission_id),
        }
        for (
            label,
            source_r,
            submission_id,
            polynomial_index,
            field_disc_abs,
            coefficient_hash,
            coefficients,
        ) in rows
    ]


def baseline_rows(labels: set[str]) -> list[dict]:
    rows = []
    with BASELINE.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=2):
            if str(row["label"]) not in labels:
                continue
            coefficient_text = str(row["coeffs"])
            coefficient_hash = hashlib.sha256(
                coefficient_text.encode("ascii")
            ).hexdigest()
            rows.append(
                {
                    "coefficientSha256": coefficient_hash,
                    "coefficients": coefficient_text,
                    "fieldDiscriminantAbs": str(row["nfdisc_abs"]),
                    "label": str(row["label"]),
                    "polynomialIndex": index,
                    "r": int(row["r"]),
                    "sourceCorpus": "local_lmfdb_baseline_csv",
                    "submissionId": None,
                }
            )
    return rows


def main() -> int:
    require(not OUTPUT.exists(), f"refusing to overwrite {OUTPUT}")
    structures = structural_sources()
    labels = list(structures)
    old_hashes = prior_hashes()
    ledger = accepted_rows(labels)
    baseline = baseline_rows(set(labels))
    ring = PolynomialRing(QQ, "y")

    counts = defaultdict(int)
    counts["acceptedRows"] = len(ledger)
    counts["baselineRows"] = len(baseline)
    fields = {}
    label_counts = defaultdict(lambda: defaultdict(int))
    for row in ledger + baseline:
        corpus = row["sourceCorpus"]
        counts[f"{corpus}Rows"] += 1
        label_counts[row["label"]]["rows"] += 1
        coefficients = [
            ZZ(value) for value in row["coefficients"].split(",")
        ]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        counts[f"{corpus}EvenRows"] += 1
        label_counts[row["label"]]["evenRows"] += 1
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        counts[f"{corpus}EvenIrreducibleRows"] += 1
        label_counts[row["label"]]["evenIrreducibleRows"] += 1
        real_roots = int(quotient.number_of_real_roots())
        label_counts[row["label"]][f"realRoots{real_roots}"] += 1
        counts[f"{corpus}RealRoots{real_roots}"] += 1
        if real_roots != 12:
            continue
        counts[f"{corpus}TotallyRealRows"] += 1
        label_counts[row["label"]]["totallyRealRows"] += 1
        canonical, field_hash = canonical_polynomial(quotient, ring)
        item = fields.setdefault(
            field_hash,
            {
                "alreadyAudited": field_hash in old_hashes,
                "canonicalPolynomial": canonical,
                "fieldCanonicalSha256": field_hash,
                "provenance": (
                    "monic accepted/baseline degree-24 q(x^2) with exact "
                    f"12x2 quotient 12T{QUOTIENT_T}; q irreducible and "
                    "totally real; "
                    "PARI polredabs canonicalization"
                ),
                "quotientT12": QUOTIENT_T,
                "sourceRows": [],
            },
        )
        item["sourceRows"].append(
            {
                key: value
                for key, value in row.items()
                if key != "coefficients"
            }
        )

    canonical_fields = [fields[key] for key in sorted(fields)]
    for field in canonical_fields:
        field["sourceRows"].sort(
            key=lambda row: (
                row["sourceCorpus"],
                int(row["label"][3:]),
                row["r"],
                str(row["submissionId"]),
                row["polynomialIndex"],
            )
        )
    fresh = [
        field
        for field in canonical_fields
        if not field["alreadyAudited"]
    ]
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "inputSha256": {
                "priorGate": hashlib.sha256(PRIOR.read_bytes()).hexdigest(),
                "structures": hashlib.sha256(
                    STRUCTURES.read_bytes()
                ).hexdigest(),
            },
            "networkCalls": 0,
            "priorCanonicalFieldCount": len(old_hashes),
            "priorCanonicalFieldHashes": sorted(old_hashes),
            "sourceCounts": dict(sorted(counts.items())),
            "submissionCalls": 0,
        },
        "canonicalTotallyRealFields": canonical_fields,
        "freshCanonicalTotallyRealFields": fresh,
        "labelCensus": {
            label: {
                **dict(sorted(label_counts[label].items())),
                "structuralSystems": structures[label],
            }
            for label in labels
        },
        "summary": {
            "acceptedScoreableRows": len(ledger),
            "allCanonicalTotallyRealFields": len(canonical_fields),
            "baselineRows": len(baseline),
            "freshCanonicalTotallyRealFields": len(fresh),
            "freshFieldHashes": [
                field["fieldCanonicalSha256"] for field in fresh
            ],
            "structuralSourceLabels": len(labels),
        },
    }
    rendered = rendered_json(payload)
    write_atomic(OUTPUT, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(OUTPUT.resolve()),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

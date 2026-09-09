#!/usr/bin/env sage -python
"""Exhaustive local source census for fresh totally-real 12T210 fields.

This is an offline, read-only source audit.  It discovers every degree-24
transitive label with an exact 12x2 quotient 12T210 from the frozen structural
catalog, scans all accepted scoreable ledger rows with those labels, and
extracts the unique degree-12 fixed field.  Trace-zero presentations q(x^2)
are handled directly; dense presentations use ``NumberField.subfields(12)``.
The resulting fields are deduplicated by PARI ``polredabs``.

The local LMFDB baseline CSV is also searched by the same exact parent-label
set.  There are no coefficient searches, network calls, staging operations,
or submissions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_BASELINE = Path("/path/to/private-file")
DEFAULT_OUTPUT = (
    DATA / "character_q210_freshfields_agent_source_census_20260730.json"
)
QUOTIENT_T = 210
EXPECTED_SYSTEM_SHA256 = (
    "c955155f4d7e005577da7ccafedbfc62bd697d3b7ea3bf598e1542babe7f9bac"
)
EXPECTED_SOURCE_LABELS = {
    "24T5203",
    "24T5204",
    "24T7595",
    "24T7599",
    "24T9978",
    "24T12486",
    "24T19578",
    "24T20564",
    "24T20566",
    "24T20568",
    "24T20569",
    "24T20570",
    "24T20571",
    "24T20572",
    "24T20573",
    "24T20574",
    "24T20576",
    "24T20577",
    "24T20578",
    "24T20579",
    "24T20581",
    "24T20582",
    "24T21610",
    "24T21615",
    "24T21616",
    "24T21618",
    "24T22543",
    "24T22544",
    "24T22545",
    "24T22546",
    "24T22547",
    "24T22548",
    "24T23274",
}
PRIOR_AUDITED_FIELD_HASHES = {
    # The first three are the already-audited totally-real q210 fields.
    "34eaa879946800fa03b996b115ad50639ce8e4a4ca751e79bfcf0175277ea58f",
    "d2eef78bf4effdd7f4bf25aeb7553f40b21f2ea8a5a6ed70a8d4715979e1ff0f",
    "ee1adf66ce8bd877acd946c6b920562634876d47d75f2efe09229a82d5b744ac",
    # This non-totally-real field was also explicitly audited.
    "f870d6b9dcdff160f8dcdd2894603bfb3de92155b1de49672c18f6445944082c",
}


def coefficient_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def canonical_polynomial(polynomial, ring):
    reduced = ring(pari(polynomial).polredabs())
    line = coefficient_line(reduced)
    return reduced, line, hashlib.sha256(line.encode("ascii")).hexdigest()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def structural_sources(path: Path) -> tuple[list[str], dict[str, dict]]:
    rows = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            systems = [
                system
                for system in row.get("blockSystems", [])
                if system.get("shape") == "12x2"
                and system.get("quotientActionLabel") == f"12T{QUOTIENT_T}"
            ]
            if not systems:
                continue
            if len(systems) != 1:
                raise ValueError(
                    f"{row['label']} has {len(systems)} q210 two-block systems"
                )
            all_two_block = [
                system
                for system in row.get("blockSystems", [])
                if system.get("shape") == "12x2"
            ]
            if len(all_two_block) != 1:
                raise ValueError(
                    f"{row['label']} has {len(all_two_block)} total 12x2 systems"
                )
            system = systems[0]
            if system.get("systemSha256") != EXPECTED_SYSTEM_SHA256:
                raise ValueError(f"unexpected q210 system hash for {row['label']}")
            rows[str(row["label"])] = {
                "blockKernelOrder": int(system["blockKernelOrder"]),
                "groupOrder": int(row["groupOrder"]),
                "quotientActionLabel": str(system["quotientActionLabel"]),
                "quotientActionOrder": int(system["quotientActionOrder"]),
                "systemSha256": str(system["systemSha256"]),
            }
    labels = sorted(rows, key=lambda label: int(label[3:]))
    if set(labels) != EXPECTED_SOURCE_LABELS:
        raise ValueError(
            f"q210 structural labels changed: {labels}, "
            f"expected {sorted(EXPECTED_SOURCE_LABELS)}"
        )
    return labels, rows


def ledger_rows(db: Path, labels: list[str]) -> list[tuple]:
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        return connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   v.field_disc_abs,p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            ORDER BY CAST(substr(v.label,4) AS INTEGER),v.r,
                     p.coefficient_hash,v.submission_id,v.polynomial_index
            """,
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()


def baseline_rows(path: Path, labels: set[str]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if str(row.get("label")) in labels
        ]


def add_field(
    fields: dict,
    polynomial,
    ring,
    source: dict,
    extraction: str,
    subfield_count: int,
    subfield_index: int,
) -> tuple[str, int]:
    real_roots = int(polynomial.number_of_real_roots())
    reduced, line, field_hash = canonical_polynomial(polynomial, ring)
    canonical_real_roots = int(reduced.number_of_real_roots())
    if canonical_real_roots != real_roots:
        raise ValueError(f"signature changed under polredabs for {field_hash}")
    item = fields.setdefault(
        field_hash,
        {
            "canonicalPolynomial": line,
            "fieldCanonicalSha256": field_hash,
            "priorAudited": field_hash in PRIOR_AUDITED_FIELD_HASHES,
            "quotientT12": QUOTIENT_T,
            "realRootCount": canonical_real_roots,
            "sourceRows": [],
            "totallyReal": canonical_real_roots == 12,
        },
    )
    if item["canonicalPolynomial"] != line:
        raise ValueError(f"canonical hash collision: {field_hash}")
    item["sourceRows"].append(
        {
            **source,
            "extraction": extraction,
            "subfieldCount": subfield_count,
            "subfieldIndex": subfield_index,
        }
    )
    return field_hash, canonical_real_roots


def process_polynomial(
    fields: dict,
    ring,
    coefficients: list,
    source: dict,
    counters: Counter,
) -> list[tuple[str, int]]:
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError(
            f"non-monic degree-24 source {source.get('coefficientSha256')}"
        )
    polynomial = ring(coefficients)
    even = not any(coefficients[index] for index in range(1, 25, 2))
    outputs = []
    if even:
        counters["evenRows"] += 1
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            raise ValueError(
                f"invalid even quotient {source.get('coefficientSha256')}"
            )
        counters["evenIrreducibleRows"] += 1
        outputs.append(
            add_field(
                fields,
                quotient,
                ring,
                source,
                "exact_trace_zero_q_of_x2",
                1,
                0,
            )
        )
        return outputs

    counters["denseRows"] += 1
    field = NumberField(polynomial, "a")
    subfields = field.subfields(12)
    counters[f"denseSubfieldCount{len(subfields)}"] += 1
    if len(subfields) != 1:
        raise ValueError(
            f"dense source {source.get('coefficientSha256')} has "
            f"{len(subfields)} degree-12 subfields, expected unique q210 fixed field"
        )
    for index, (subfield, _embedding, _reverse) in enumerate(subfields):
        outputs.append(
            add_field(
                fields,
                subfield.polynomial(),
                ring,
                source,
                "NumberField.subfields(12)_unique_fixed_field",
                len(subfields),
                index,
            )
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite q210 fresh-field census")

    proof.all(True)
    ring = PolynomialRing(QQ, "y")
    labels, structural = structural_sources(args.structures)
    rows = ledger_rows(args.db, labels)
    baseline = baseline_rows(args.baseline, set(labels))
    counters = Counter()
    fields = {}
    label_counts = Counter()
    for (
        label,
        source_r,
        submission_id,
        polynomial_index,
        field_disc_abs,
        coefficient_hash,
        text,
    ) in rows:
        counters["ledgerAcceptedScoreableRows"] += 1
        label_counts[str(label)] += 1
        coefficients = [ZZ(value) for value in str(text).split(",")]
        source = {
            "coefficientSha256": str(coefficient_hash),
            "corpus": "data/ledger.sqlite3",
            "fieldDiscAbs": str(field_disc_abs) if field_disc_abs else None,
            "label": str(label),
            "polynomialIndex": int(polynomial_index),
            "r": int(source_r),
            "submissionId": str(submission_id),
        }
        process_polynomial(fields, ring, coefficients, source, counters)
        print(
            json.dumps(
                {
                    "coefficientSha256": str(coefficient_hash),
                    "denseRowsCompleted": counters["denseRows"],
                    "event": "ledger_source_processed",
                    "label": str(label),
                    "row": counters["ledgerAcceptedScoreableRows"],
                    "totalRows": len(rows),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    counters["baselineMatchingRows"] = len(baseline)
    for index, row in enumerate(baseline):
        coefficients = [ZZ(value) for value in str(row["coeffs"]).split(",")]
        source_line = ",".join(str(value) for value in coefficients)
        source = {
            "coefficientSha256": hashlib.sha256(
                source_line.encode("ascii")
            ).hexdigest(),
            "corpus": str(args.baseline.resolve()),
            "fieldDiscAbs": str(row.get("nfdisc_abs") or "") or None,
            "label": str(row["label"]),
            "polynomialIndex": index,
            "r": int(row["r"]),
            "submissionId": None,
        }
        process_polynomial(fields, ring, coefficients, source, counters)

    field_rows = [fields[key] for key in sorted(fields)]
    for item in field_rows:
        item["sourceRows"].sort(
            key=lambda row: (
                row["corpus"],
                row["label"],
                row["r"],
                row["coefficientSha256"],
                row["polynomialIndex"],
            )
        )
        item["sourceLabels"] = sorted(
            {row["label"] for row in item["sourceRows"]},
            key=lambda label: int(label[3:]),
        )
        item["sourceRowCount"] = len(item["sourceRows"])

    fresh_totally_real = [
        row
        for row in field_rows
        if row["totallyReal"] and not row["priorAudited"]
    ]
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "construction": (
                "unique degree-12 fixed field of the exact 12x2 quotient "
                "12T210, using q(x^2) or NumberField.subfields(12)"
            ),
            "networkCalls": 0,
            "priorAuditedFieldHashes": sorted(PRIOR_AUDITED_FIELD_HASHES),
            "submissionCalls": 0,
        },
        "fields": field_rows,
        "freshTotallyRealFields": fresh_totally_real,
        "sourceCorpus": {
            "baseline": {
                "matchingRows": len(baseline),
                "path": str(args.baseline.resolve()),
            },
            "ledger": {
                "acceptedScoreableRows": len(rows),
                "distinctCoefficientHashes": len(
                    {str(row[5]) for row in rows}
                ),
                "distinctFieldDiscriminants": len(
                    {str(row[4]) for row in rows if row[4]}
                ),
                "labelRowCounts": {
                    label: int(label_counts[label]) for label in labels
                },
                "path": str(args.db.resolve()),
            },
            "structuralCatalog": {
                "labelCount": len(labels),
                "labels": labels,
                "path": str(args.structures.resolve()),
                "systems": structural,
            },
        },
        "summary": {
            "allCanonicalFixedFields": len(field_rows),
            "baselineMatchingRows": counters["baselineMatchingRows"],
            "denseRows": counters["denseRows"],
            "denseRowsWithUniqueDegree12Subfield": counters[
                "denseSubfieldCount1"
            ],
            "evenIrreducibleRows": counters["evenIrreducibleRows"],
            "evenRows": counters["evenRows"],
            "freshTotallyRealCanonicalFields": len(fresh_totally_real),
            "ledgerAcceptedScoreableRows": counters[
                "ledgerAcceptedScoreableRows"
            ],
            "priorAuditedCanonicalFieldsSeen": sum(
                row["priorAudited"] for row in field_rows
            ),
            "totallyRealCanonicalFields": sum(
                row["totallyReal"] for row in field_rows
            ),
            "totallyRealSourceRows": sum(
                row["sourceRowCount"]
                for row in field_rows
                if row["totallyReal"]
            ),
        },
    }
    rendered = rendered_json(payload)
    write_atomic(args.output.resolve(), rendered)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

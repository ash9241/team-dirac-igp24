#!/usr/bin/env sage -python
"""Finite field-first character gate for the full-rank q12T138 cluster.

The local phase:

* freezes the July-29 strict tc0 signatures for 24T20767 and 24T20768;
* inventories accepted scoreable even rows only from the six degree-24
  full-rank (block kernel 2^11) q12T138 actions;
* requires an irreducible, totally-real degree-12 quotient and deduplicates
  fields by PARI polredabs;
* performs a full per-field character alignment and the cheap rational norm
  valuation/sign parity gate.

The exact phase accepts exactly one canonical field hash.  It recomputes that
field's local gate, runs exact K(S,2) only on the locally surviving cores, and
reconstructs/certifies P_alpha for every exact passing target route.

There are no coefficient boxes, random choices, network calls, submissions,
candidate staging actions, or manifest writes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_LOCAL_OUTPUT = DATA / "character_field_gate_q138_local_20260730.json"

QUOTIENT_T = 138
FAMILIES = {
    QUOTIENT_T: {
        "ambiguousTargetLabels": ["24T20768"],
        "targetLabels": ["24T20767", "24T20768"],
    }
}
EXPECTED_LIVE = {
    "24T20767": [4, 8, 12, 16, 20, 24],
    "24T20768": [8, 12, 16, 20, 24],
}
EXPECTED_SOURCE_LABELS = {
    "24T20764",
    "24T20765",
    "24T20766",
    "24T20767",
    "24T20768",
    "24T20769",
}
EXPECTED_CANONICAL_FIELDS = 15


def load_base():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("q138_gate_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character gate base from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAMILIES = FAMILIES
    return module


BASE = load_base()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def canonical_polynomial(polynomial, ring):
    reduced = ring(pari(polynomial).polredabs())
    line = ",".join(str(ZZ(value)) for value in reduced.list())
    return reduced, line, hashlib.sha256(line.encode()).hexdigest()


def full_rank_source_labels(structures: Path) -> set[str]:
    labels = set()
    with structures.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for system in row.get("blockSystems", []):
                if (
                    system.get("shape") == "12x2"
                    and int(system.get("blockKernelOrder", 0)) == 2**11
                    and system.get("quotientActionLabel")
                    == f"12T{QUOTIENT_T}"
                ):
                    labels.add(str(row["label"]))
                    break
    if labels != EXPECTED_SOURCE_LABELS:
        raise ValueError(
            f"full-rank q{QUOTIENT_T} structural label mismatch: "
            f"{sorted(labels)}, expected {sorted(EXPECTED_SOURCE_LABELS)}"
        )
    return labels


def strict_live_pairs(db: Path) -> dict[str, list[dict]]:
    labels = sorted(EXPECTED_LIVE)
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
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
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()
    actual = defaultdict(list)
    output = defaultdict(list)
    for label, r, team_count, generated_at in rows:
        actual[str(label)].append(int(r))
        output[str(label)].append(
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": str(generated_at) if generated_at else None,
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
        )
    if dict(actual) != EXPECTED_LIVE:
        raise ValueError(
            f"strict q138 live-pair mismatch: {dict(actual)}, "
            f"expected {EXPECTED_LIVE}"
        )
    return dict(output)


def canonical_fields(db: Path, structures: Path, ring) -> list[dict]:
    labels = sorted(full_rank_source_labels(structures))
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash
            """,
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()

    counts = defaultdict(int)
    fields = {}
    for label, source_r, submission_id, polynomial_index, source_hash, text in rows:
        counts["acceptedRows"] += 1
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        counts["acceptedEvenRows"] += 1
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        counts["acceptedEvenIrreducibleRows"] += 1
        if int(quotient.number_of_real_roots()) != 12:
            continue
        counts["totallyRealSourceRows"] += 1
        reduced, line, field_hash = canonical_polynomial(quotient, ring)
        item = fields.setdefault(
            field_hash,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": field_hash,
                "family": f"12T{QUOTIENT_T}",
                "provenance": (
                    "accepted scoreable even q(x^2) from an exact full-rank "
                    f"12x2/block-kernel-2^11 q12T{QUOTIENT_T} action; "
                    "q has 12 real "
                    "roots; PARI polredabs field deduplication"
                ),
                "quotientT12": QUOTIENT_T,
                "sourceRows": [],
            },
        )
        item["sourceRows"].append(
            {
                "coefficientSha256": str(source_hash),
                "label": str(label),
                "polynomialIndex": int(polynomial_index),
                "r": int(source_r),
                "submissionId": str(submission_id),
            }
        )
    output = [fields[key] for key in sorted(fields)]
    if len(output) != EXPECTED_CANONICAL_FIELDS:
        raise ValueError(
            f"q138 canonical field mismatch: {len(output)}, "
            f"expected {EXPECTED_CANONICAL_FIELDS}"
        )
    for row in output:
        row["sourceRows"].sort(
            key=lambda source: (
                source["label"],
                source["r"],
                source["submissionId"],
                source["polynomialIndex"],
            )
        )
    print(
        json.dumps(
            {
                "acceptedEvenIrreducibleRows": counts[
                    "acceptedEvenIrreducibleRows"
                ],
                "acceptedEvenRows": counts["acceptedEvenRows"],
                "acceptedRows": counts["acceptedRows"],
                "canonicalFields": len(output),
                "event": "field_census",
                "sourceLabels": labels,
                "totallyRealSourceRows": counts["totallyRealSourceRows"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return output


def exact_passing_routes(rows: list[dict]) -> list[dict]:
    output = []
    for field in rows:
        for pair in field["exactSelmerSignGate"].get("pairs", []):
            if pair["status"] != "exact_selmer_sign_pass":
                continue
            reconstruction = (
                pair.get("certifiedExactSelmerReconstruction")
                or pair.get("firstExactSelmerReconstruction")
            )
            output.append(
                {
                    "candidateSha256": (
                        reconstruction.get("candidateSha256")
                        if reconstruction
                        else None
                    ),
                    "candidateStatus": (
                        reconstruction.get("candidateStatus")
                        if reconstruction
                        else None
                    ),
                    "core": pair["core"],
                    "fieldCanonicalSha256": field["fieldCanonicalSha256"],
                    "label": pair["label"],
                    "r": pair["r"],
                    "solvableSignMasks": pair[
                        "exactSelmerSolvableSignMasks"
                    ],
                }
            )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--phase", choices=("local", "exact"), default="local")
    parser.add_argument("--field-hash")
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--max-reconstructions", type=int, default=1)
    parser.add_argument(
        "--max-coset-reconstructions-per-sign",
        type=int,
        default=1,
    )
    parser.add_argument("--coset-mask-start", type=int, default=0)
    parser.add_argument("--coset-mask-stride", type=int, default=1)
    parser.add_argument("--pari-stack-bytes", type=int, default=0)
    parser.add_argument("--pari-stack-max-bytes", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        if args.phase == "local":
            args.output = DEFAULT_LOCAL_OUTPUT
        else:
            raise ValueError("--output is required for the exact phase")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("refusing to overwrite the q138 field-gate artifact")

    ring = PolynomialRing(QQ, "y")
    live = strict_live_pairs(args.db)
    fields = canonical_fields(args.db, args.structures, ring)
    if args.field_hash:
        fields = [
            row
            for row in fields
            if row["fieldCanonicalSha256"].startswith(args.field_hash)
        ]
        if len(fields) != 1:
            raise ValueError(
                f"field prefix selected {len(fields)} canonical fields"
            )
    if args.phase == "exact" and len(fields) != 1:
        raise ValueError("the exact phase processes exactly one field")
    if args.phase == "local" and args.field_hash:
        raise ValueError("the preserved local phase must audit all 15 fields")

    if args.phase == "exact":
        # Prevent Sage's internal class-group helper from silently restoring
        # proof=True and entering an unbounded bnfcertify call.  The preserved
        # output marks the exact K(S,2) computation as GRH-conditional.
        proof.number_field(False)
        if args.pari_stack_bytes:
            pari.allocatemem(
                int(args.pari_stack_bytes),
                int(args.pari_stack_max_bytes),
            )

    audited = []
    for index, field_row in enumerate(fields, start=1):
        print(
            json.dumps(
                {
                    "event": f"field_{args.phase}_start",
                    "fieldCanonicalSha256": field_row[
                        "fieldCanonicalSha256"
                    ],
                    "fieldIndex": index,
                    "fieldTotal": len(fields),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        result = BASE.audit_field(
            field_row,
            ring,
            live,
            args.phase == "local",
            args.phase == "exact",
            args.witness_primes,
            args.max_reconstructions,
            args.max_coset_reconstructions_per_sign,
            args.coset_mask_start,
            args.coset_mask_stride,
        )
        if args.phase == "exact" and result[
            "exactSelmerSignGate"
        ].get("status") != "skipped_no_local_passers":
            result["exactSelmerSignGate"]["conditionalOnGRH"] = True
        audited.append(result)
        exact_pairs = result["exactSelmerSignGate"].get("pairs", [])
        print(
            json.dumps(
                {
                    "event": f"field_{args.phase}_done",
                    "exactPassers": sum(
                        pair["status"] == "exact_selmer_sign_pass"
                        for pair in exact_pairs
                    ),
                    "fieldCanonicalSha256": result[
                        "fieldCanonicalSha256"
                    ],
                    "fieldIndex": index,
                    "fieldTotal": len(fields),
                    "localPassers": sum(
                        pair["localPass"]
                        for pair in result["localPairGates"]
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    exact_routes = exact_passing_routes(audited)
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "conditionalOnGRH": args.phase == "exact",
            "networkCalls": 0,
            "phase": args.phase,
            "sourceCanonicalFields": EXPECTED_CANONICAL_FIELDS,
            "sourceFullRankLabels": sorted(EXPECTED_SOURCE_LABELS),
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    pair["generatedAt"]
                    for pairs in live.values()
                    for pair in pairs
                    if pair["generatedAt"]
                }
            ),
            "witnessPrimeCount": int(args.witness_primes),
        },
        "family": FAMILIES[QUOTIENT_T],
        "fields": audited,
        "livePairs": live,
        "summary": {
            "auditedFields": len(audited),
            "certifiedCandidates": sum(
                str(route["candidateStatus"]).startswith("certified_")
                for route in exact_routes
            ),
            "exactPassingPairFieldRoutes": len(exact_routes),
            "exactPassingRoutes": exact_routes,
            "localPassingPairFieldRoutes": sum(
                pair["localPass"]
                for field in audited
                for pair in field["localPairGates"]
            ),
        },
    }
    rendered = rendered_json(payload)
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(output),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

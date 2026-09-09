#!/usr/bin/env sage -python
"""Targeted fresh-field character gate for the live 24T19320 signatures.

The field census is broader than the earlier full-rank-source audit: it takes
every locally accepted even degree-24 polynomial whose exact transitive-group
structure has a 12x2 block quotient 12T77.  It then requires an irreducible,
totally-real degree-12 quotient, deduplicates by PARI ``polredabs``, and drops
the four canonical fields already audited on July 30.

The local phase is only a character/norm screen.  The exact phase selects one
fresh canonical field, independently computes its degree-12 Galois group with
Sage/GAP, requires exact transitive number 77, and only then performs the exact
Selmer/sign reconstruction and maximal-subgroup Frobenius certificate.

There are no network calls, submissions, or staging actions.
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
DEFAULT_OUTPUT = DATA / "fresh_q77_19320_field_gate_local_20260730.json"
QUOTIENT_T = 77
TARGET_LABEL = "24T19320"
EXPECTED_LIVE = {TARGET_LABEL: [4, 8, 12, 16]}
AUDITED_HASHES = {
    "4eb67f18c3c4bba075b862ad9dd276112e9ad552ff122fb011b3dd16722c2a37",
    "9ed95f15c53e45227bf4d70872d7ec026c80f56c8ad276c2478418947d62e586",
    "b80fbfa2f0e735cd3f697339fee464886f48915d4ba3ba7211d1df36f80e829c",
    "f9d44381e494ffc398e3e5043204f1a6b635c9ecb4b18a754cb925cad4cad250",
}
QUEUED_LMFDB_SUBMISSION = "sub_3b36cde6bccc4a019a422d853a6c4353"


def load_base():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("fresh_q77_gate_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character gate base from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAMILIES = {
        QUOTIENT_T: {
            "ambiguousTargetLabels": [TARGET_LABEL],
            "targetLabels": [TARGET_LABEL],
        }
    }
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


def q77_structural_labels(structures: Path) -> set[str]:
    labels = set()
    with structures.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if any(
                system.get("shape") == "12x2"
                and system.get("quotientActionLabel") == "12T77"
                for system in row.get("blockSystems", [])
            ):
                labels.add(str(row["label"]))
    if len(labels) != 74:
        raise ValueError(
            f"q77 structural label census changed: {len(labels)}, expected 74"
        )
    return labels


def strict_live_pairs(db: Path) -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
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
            WHERE t.label=?
              AND t.team_count=0
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.r
            """,
            (TARGET_LABEL,),
        ).fetchall()
    finally:
        connection.close()
    actual = {TARGET_LABEL: [int(row[1]) for row in rows]}
    if actual != EXPECTED_LIVE:
        raise ValueError(
            f"strict live-pair mismatch: {actual}, expected {EXPECTED_LIVE}"
        )
    return {
        TARGET_LABEL: [
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": str(generated_at) if generated_at else None,
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
            for _label, r, team_count, generated_at in rows
        ]
    }


def queued_lmfdb_census(db: Path, ring) -> dict:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT p.polynomial_index,p.coefficients
            FROM polynomials AS p
            LEFT JOIN verifications AS v
              USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND v.submission_id IS NULL
            ORDER BY p.polynomial_index
            """,
            (QUEUED_LMFDB_SUBMISSION,),
        ).fetchall()
    finally:
        connection.close()
    counts = defaultdict(int)
    for _index, text in rows:
        counts["unverifiedRows"] += 1
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        counts["evenRows"] += 1
        quotient = ring(coefficients[::2])
        if not quotient.is_irreducible():
            continue
        counts["irreducibleQuotients"] += 1
        if int(quotient.number_of_real_roots()) == 12:
            counts["totallyRealQuotients"] += 1
    return {
        "description": (
            "queued, unverified PARI-certified degree-24 quadratic-lift "
            "probes from local LMFDB seeds"
        ),
        "evenRows": counts["evenRows"],
        "irreducibleQuotients": counts["irreducibleQuotients"],
        "submissionId": QUEUED_LMFDB_SUBMISSION,
        "totallyRealQuotients": counts["totallyRealQuotients"],
        "unverifiedRows": counts["unverifiedRows"],
    }


def fresh_fields(db: Path, structures: Path, ring) -> tuple[list[dict], dict]:
    labels = sorted(q77_structural_labels(structures))
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   v.scoreable,p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash
            """,
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()

    counts = defaultdict(int)
    fields = {}
    for (
        label,
        source_r,
        submission_id,
        polynomial_index,
        scoreable,
        source_hash,
        text,
    ) in rows:
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
        _reduced, line, field_hash = canonical_polynomial(quotient, ring)
        item = fields.setdefault(
            field_hash,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": field_hash,
                "family": "12T77",
                "provenance": (
                    "accepted even q(x^2) from a server-certified degree-24 "
                    "label whose exact transitive-group structure has a "
                    "12x2/12T77 block quotient; q has 12 real roots; PARI "
                    "polredabs field deduplication"
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
                "scoreable": bool(scoreable),
                "submissionId": str(submission_id),
            }
        )

    all_field_count = len(fields)
    fresh = [fields[key] for key in sorted(fields) if key not in AUDITED_HASHES]
    for row in fresh:
        row["sourceRows"].sort(
            key=lambda source: (
                source["label"],
                source["r"],
                source["submissionId"],
                source["polynomialIndex"],
            )
        )
    if all_field_count != 31 or len(fresh) != 27:
        raise ValueError(
            f"q77 fresh-field census changed: all={all_field_count}, "
            f"fresh={len(fresh)}, expected 31/27"
        )
    census = {
        "acceptedEvenIrreducibleRows": counts[
            "acceptedEvenIrreducibleRows"
        ],
        "acceptedEvenRows": counts["acceptedEvenRows"],
        "acceptedRows": counts["acceptedRows"],
        "allCanonicalTotallyRealFields": all_field_count,
        "excludedPreviouslyAuditedFields": len(AUDITED_HASHES),
        "freshCanonicalTotallyRealFields": len(fresh),
        "sourceStructuralLabelCount": len(labels),
        "totallyRealSourceRows": counts["totallyRealSourceRows"],
    }
    return fresh, census


def exact_routes(rows: list[dict]) -> list[dict]:
    output = []
    for field in rows:
        for pair in field["exactSelmerSignGate"].get("pairs", []):
            reconstruction = pair.get("certifiedExactSelmerReconstruction")
            if (
                pair.get("status") == "exact_selmer_sign_pass"
                and reconstruction
                and str(reconstruction.get("candidateStatus", "")).startswith(
                    "certified_"
                )
            ):
                output.append(
                    {
                        "candidateSha256": reconstruction["candidateSha256"],
                        "candidateStatus": reconstruction["candidateStatus"],
                        "core": pair["core"],
                        "fieldCanonicalSha256": field[
                            "fieldCanonicalSha256"
                        ],
                        "label": pair["label"],
                        "r": pair["r"],
                    }
                )
    return output


def exact_galois_certificate(field_row: dict, ring) -> dict:
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    group = quotient.galois_group(algorithm="gap")
    transitive_number = int(group.transitive_number())
    order = int(group.order())
    if transitive_number != QUOTIENT_T:
        raise ValueError(
            f"independent quotient group is 12T{transitive_number}, not 12T77"
        )
    return {
        "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
        "degree": int(group.degree()),
        "order": order,
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{transitive_number}",
        "transitiveNumber": transitive_number,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--phase", choices=("local", "exact"), default="local")
    parser.add_argument("--field-hash")
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--max-reconstructions", type=int, default=1)
    parser.add_argument(
        "--max-coset-reconstructions-per-sign", type=int, default=16
    )
    parser.add_argument("--coset-mask-start", type=int, default=0)
    parser.add_argument("--coset-mask-stride", type=int, default=1)
    parser.add_argument("--pari-stack-bytes", type=int, default=0)
    parser.add_argument("--pari-stack-max-bytes", type=int, default=0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    if args.phase == "exact" and not args.field_hash:
        raise ValueError("--field-hash is required for exact phase")
    if args.phase == "local" and args.field_hash:
        raise ValueError("local phase audits the complete fresh census")

    ring = PolynomialRing(QQ, "y")
    live = strict_live_pairs(args.db)
    fields, source_census = fresh_fields(args.db, args.structures, ring)
    source_census["queuedLmfdbProbeCensus"] = queued_lmfdb_census(
        args.db, ring
    )
    if args.field_hash:
        fields = [
            row
            for row in fields
            if row["fieldCanonicalSha256"].startswith(args.field_hash)
        ]
        if len(fields) != 1:
            raise ValueError(
                f"field prefix selected {len(fields)} fresh canonical fields"
            )

    if args.phase == "exact":
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
        group_certificate = (
            exact_galois_certificate(field_row, ring)
            if args.phase == "exact"
            else {
                "status": "deferred_until_exact_survivor",
                "structuralSourceCertificate": (
                    "accepted degree-24 label plus exact 12x2/12T77 "
                    "transitive-group block structure"
                ),
            }
        )
        try:
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
        except ValueError as exc:
            if args.phase != "local":
                raise
            result = {
                **field_row,
                "alignment": {
                    "error": f"{type(exc).__name__}: {exc}",
                    "method": "exact-gf2-ramified-squareclass-solve",
                    "status": "rejected_inconsistent_12T77_frobenius_characters",
                },
                "exactSelmerSignGate": {
                    "pairs": [],
                    "status": "skipped_quotient_identity_rejection",
                },
                "localCoreGates": {},
                "localPairGates": [],
            }
        result["quotientGaloisCertificate"] = group_certificate
        audited.append(result)
        print(
            json.dumps(
                {
                    "event": f"field_{args.phase}_done",
                    "fieldCanonicalSha256": result[
                        "fieldCanonicalSha256"
                    ],
                    "localPassers": sum(
                        row["localPass"] for row in result["localPairGates"]
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    routes = exact_routes(audited)
    payload = {
        "audit": {
            "conditionalOnGRH": args.phase == "exact",
            "networkCalls": 0,
            "phase": args.phase,
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
        "family": {
            "ambiguousTargetLabels": [TARGET_LABEL],
            "targetLabels": [TARGET_LABEL],
        },
        "fields": audited,
        "livePairs": live,
        "sourceCensus": source_census,
        "summary": {
            "auditedFields": len(audited),
            "certifiedExactRoutes": routes,
            "certifiedExactRouteCount": len(routes),
            "localPassingPairFieldRoutes": sum(
                row["localPass"]
                for field in audited
                for row in field["localPairGates"]
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

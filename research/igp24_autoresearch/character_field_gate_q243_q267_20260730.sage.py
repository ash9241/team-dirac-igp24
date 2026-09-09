#!/usr/bin/env sage -python
"""Read-only exact character/local/Selmer audit for quotient clusters 243 and 267."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, prod, proof


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
ACTION_MAP = ROOT / "data" / "agent_gold_b_even_twist_action_map.jsonl"
TARGETS = {
    243: {"24T23287": [12, 16, 20, 24]},
    267: {
        "24T23820": [24],
        "24T23821": [24],
        "24T23822": [16, 20, 24],
    },
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_module(
    "character_gate_shared",
    ROOT / "character_field_gate_q156_q210_20260730.sage.py",
)
HELPER = GATE.HELPER


def quotient_labels() -> dict[int, set[str]]:
    output = {243: set(), 267: set()}
    with ACTION_MAP.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            for system in row.get("systems", []):
                q_t = int(system.get("blockActionT12", -1))
                if q_t in output:
                    output[q_t].add(str(row["sourceLabel"]))
    return output


def all_canonical_fields(ring) -> list[dict]:
    labels_by_q = quotient_labels()
    q_by_label = {
        label: q_t for q_t, labels in labels_by_q.items() for label in labels
    }
    labels = sorted(q_by_label)
    placeholders = ",".join("?" for _ in labels)
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
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

    fields: dict[tuple[int, str], dict] = {}
    inspected_even = defaultdict(int)
    inspected_totally_real = defaultdict(int)
    for label, source_r, submission_id, polynomial_index, source_hash, text in rows:
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        q_t = q_by_label[str(label)]
        inspected_even[q_t] += 1
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        if int(quotient.number_of_real_roots()) != 12:
            continue
        inspected_totally_real[q_t] += 1
        reduced, line, field_hash = GATE.canonical_polynomial(quotient, ring)
        field = NumberField(reduced.change_ring(QQ), "a")
        key = (q_t, field_hash)
        item = fields.setdefault(
            key,
            {
                "canonicalPolynomial": line,
                "coefficientBytes": len(line.encode()),
                "fieldCanonicalSha256": field_hash,
                "fieldDiscriminantAbs": str(abs(ZZ(field.discriminant()))),
                "quotientT12": q_t,
                "sourceRows": [],
            },
        )
        item["sourceRows"].append(
            {
                "coefficientSha256": str(source_hash),
                "label": str(label),
                "polynomialIndex": int(polynomial_index),
                "quotientConstantSquarefreeCore": int(
                    ZZ(quotient[0]).squarefree_part()
                ),
                "r": int(source_r),
                "submissionId": str(submission_id),
            }
        )
    output = sorted(
        fields.values(),
        key=lambda row: (
            row["quotientT12"],
            int(row["fieldDiscriminantAbs"]),
            row["coefficientBytes"],
            row["fieldCanonicalSha256"],
        ),
    )
    print(
        json.dumps(
            {
                "event": "field_census",
                "acceptedEvenPresentations": dict(inspected_even),
                "acceptedTotallyRealPresentations": dict(inspected_totally_real),
                "canonicalFields": {
                    str(q_t): sum(row["quotientT12"] == q_t for row in output)
                    for q_t in TARGETS
                },
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return output


def local_audit(field_row: dict, ring) -> dict:
    q_t = int(field_row["quotientT12"])
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    field = NumberField(quotient.change_ring(QQ), "a")
    if tuple(int(value) for value in field.signature()) != (12, 0):
        raise ValueError("non-totally-real field escaped the field census")
    alignment = HELPER.character_alignment(quotient, q_t)
    target_rows = []
    passing_by_core = defaultdict(list)
    core_gates = {}
    for label, signatures in TARGETS[q_t].items():
        exact_cores = alignment[
            "labelToUnambiguousSquarefreeNormCores"
        ].get(label, [])
        for core in exact_cores:
            core = int(core)
            if core not in core_gates:
                core_gates[core] = GATE.local_core_gate(field, core)
            for r in signatures:
                negative_count = 12 - r // 2
                signed_norm_parity_pass = (
                    0 <= negative_count <= 12
                    and ((negative_count & 1) == int(core < 0))
                )
                local_pass = (
                    core_gates[core]["rationalNormParityLocallyPossible"]
                    and signed_norm_parity_pass
                )
                row = {
                    "core": core,
                    "label": label,
                    "localPass": bool(local_pass),
                    "negativeRealEmbeddingsRequired": negative_count,
                    "r": r,
                    "signedNormParityPass": bool(signed_norm_parity_pass),
                }
                target_rows.append(row)
                if local_pass:
                    passing_by_core[core].append(row)

    source_alignment_checks = []
    for source in field_row["sourceRows"]:
        core = int(source["quotientConstantSquarefreeCore"])
        possible = alignment["coreToPossibleLabels"].get(str(core), [])
        source_alignment_checks.append(
            {
                **source,
                "alignmentRecoveredSourceLabel": source["label"] in possible,
                "possibleLabelsForSourceCore": possible,
            }
        )
    return {
        **field_row,
        "alignment": {
            "labelToSquarefreeNormCores": {
                label: alignment["labelToSquarefreeNormCores"].get(label, [])
                for label in TARGETS[q_t]
            },
            "labelToUnambiguousSquarefreeNormCores": {
                label: alignment[
                    "labelToUnambiguousSquarefreeNormCores"
                ].get(label, [])
                for label in TARGETS[q_t]
            },
            "ramifiedPrimes": alignment.get("ramifiedPrimes"),
            "quotientOrder": alignment["quotientOrder"],
        },
        "localCoreGates": {
            str(core): row for core, row in sorted(core_gates.items())
        },
        "localPairGates": target_rows,
        "passingByCore": {
            str(core): rows for core, rows in sorted(passing_by_core.items())
        },
        "sourceAlignmentChecks": source_alignment_checks,
    }


def selmer_audit(field_row: dict, local: dict, ring) -> dict:
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    field = NumberField(quotient.change_ring(QQ), "a")
    work = {
        int(core): rows for core, rows in local["passingByCore"].items()
    }
    if not work:
        return {"pairs": [], "status": "skipped_no_local_passers"}
    rational_primes = sorted(
        {
            int(prime)
            for core in work
            for prime in abs(ZZ(core)).prime_divisors()
        }
    )
    prime_ideals = [
        ideal
        for prime in rational_primes
        for ideal in field.primes_above(prime)
    ]
    print(
        json.dumps(
            {
                "event": "selmer_start",
                "fieldCanonicalSha256": field_row["fieldCanonicalSha256"],
                "rationalPrimes": rational_primes,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    # Sage's internal principal-ideal helper otherwise silently restores the
    # global proof=True flag even when selmer_space(proof=False) is requested.
    # Disable it process-wide so this finite audit consistently uses PARI's
    # GRH-conditional class-group result and does not spend unbounded time in
    # bnfcertify.  The output records that proof status explicitly.
    proof.number_field(False)
    selmer, _generators, from_selmer, _to_selmer = field.selmer_space(
        prime_ideals, ZZ(2), proof=False
    )
    representatives = [
        from_selmer(value) for value in selmer.basis()
    ]
    embeddings = field.embeddings(GATE.RealIntervalField(160))
    matrix = GATE.state_matrix(
        field, representatives, embeddings, rational_primes
    )
    image = matrix.column_space()
    print(
        json.dumps(
            {
                "event": "selmer_ready",
                "fieldCanonicalSha256": field_row["fieldCanonicalSha256"],
                "selmerDimension": int(selmer.dimension()),
                "stateImageDimension": int(matrix.rank()),
                "stateImageLeftKernel": [
                    [int(value) for value in relation]
                    for relation in matrix.left_kernel().basis()
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )

    rows = []
    for core, pairs in sorted(work.items()):
        for pair in sorted(pairs, key=lambda row: (row["label"], row["r"])):
            negative_count = len(embeddings) - int(pair["r"]) // 2
            solvable_masks = 0
            sample_masks = []
            actionable = None
            for indexes in itertools.combinations(
                range(len(embeddings)), negative_count
            ):
                target = GATE.target_vector(
                    len(embeddings), rational_primes, core, indexes
                )
                if target not in image:
                    continue
                solvable_masks += 1
                if len(sample_masks) < 8:
                    sample_masks.append(
                        sum(1 << index for index in indexes)
                    )
                if actionable is not None:
                    continue
                solution = matrix.solve_right(target)
                selected = [
                    index
                    for index, value in enumerate(solution)
                    if int(value)
                ]
                element = prod(
                    (representatives[index] for index in selected),
                    field.one(),
                )
                norm = QQ(element.norm())
                norm_core = ZZ(
                    norm.numerator() * norm.denominator()
                ).squarefree_part()
                if int(norm_core) != int(core):
                    raise ArithmeticError(
                        f"representative norm core {norm_core} != target {core}"
                    )
                minpoly = element.minpoly()
                actionable = {
                    "basisIndexes": selected,
                    "element": str(element),
                    "elementMinpoly": str(minpoly),
                    "elementMinpolyDegree": int(minpoly.degree()),
                    "negativeEmbeddingIndexes": list(indexes),
                    "norm": str(norm),
                    "normSquarefreeCore": int(norm_core),
                }
            rows.append(
                {
                    **pair,
                    "actionableRepresentative": actionable,
                    "exactSelmerSolvableSignMasks": solvable_masks,
                    "sampleExactSelmerSignMasks": sample_masks,
                    "status": (
                        "exact_selmer_sign_pass"
                        if solvable_masks
                        else "exact_selmer_sign_obstruction"
                    ),
                }
            )
    return {
        "conditionalOnGRH": True,
        "matrix": {
            "realEmbeddings": len(embeddings),
            "rationalPrimes": rational_primes,
            "rows": int(matrix.nrows()),
            "selmerColumns": int(matrix.ncols()),
            "selmerStateImageDimension": int(matrix.rank()),
            "stateImageLeftKernel": [
                [int(value) for value in relation]
                for relation in matrix.left_kernel().basis()
            ],
        },
        "pairs": rows,
        "selmer": {
            "dimension": int(selmer.dimension()),
            "primeIdealSupportCount": len(prime_ideals),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=("local", "selmer"), default="local"
    )
    parser.add_argument("--only-field-sha")
    args = parser.parse_args()
    ring = PolynomialRing(QQ, "y")
    fields = all_canonical_fields(ring)
    if args.only_field_sha:
        fields = [
            row
            for row in fields
            if row["fieldCanonicalSha256"].startswith(args.only_field_sha)
        ]
        if len(fields) != 1:
            raise ValueError(
                f"field prefix selected {len(fields)} canonical fields"
            )
    for field_row in fields:
        local = local_audit(field_row, ring)
        payload = {
            "alignment": local["alignment"],
            "canonicalPolynomial": local["canonicalPolynomial"],
            "coefficientBytes": local["coefficientBytes"],
            "event": "field_local_audit",
            "fieldCanonicalSha256": local["fieldCanonicalSha256"],
            "fieldDiscriminantAbs": local["fieldDiscriminantAbs"],
            "localCoreGates": local["localCoreGates"],
            "localPairGates": local["localPairGates"],
            "quotientT12": local["quotientT12"],
            "sourceAlignmentChecks": local["sourceAlignmentChecks"],
        }
        if args.phase == "selmer":
            payload["exactSelmerSignGate"] = selmer_audit(
                field_row, local, ring
            )
        print(json.dumps(payload, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

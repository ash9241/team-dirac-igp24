#!/usr/bin/env sage -python
"""Batch exact sign/norm feasibility for one character-kernel base.

The expensive S-unit group is built once for the union of all currently live
aligned norm cores and the requested auxiliary primes.  Every reported state
is then tested by exact GF(2) image membership.  This worker performs no
candidate staging, network calls, or submissions.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, NumberField, QQ, RealField, ZZ, prod, vector


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_feasibility_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()


def live_pairs(db: Path, labels: set[str]):
    connection = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.r,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.team_count=0 AND b.label IS NULL AND owned.label IS NULL
            ORDER BY t.t,t.r
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        {"generatedAt": row[2], "label": str(row[0]), "r": int(row[1])}
        for row in rows
        if str(row[0]) in labels and int(row[1]) % 4 == 0
    ]


def state_bits(sign_mask: int, norm, rational_primes: list[int]):
    bits = [(sign_mask >> index) & 1 for index in range(12)]
    norm = QQ(norm)
    bits.extend(int(norm.valuation(prime)) & 1 for prime in rational_primes)
    return bits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--aux-primes", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-masks", type=int, default=8)
    args = parser.parse_args()

    requested_auxiliary = sorted(
        {int(value) for value in args.aux_primes.split(",") if value.strip()}
    )
    source = HELPER.load_source(args.db, args.submission_id, args.polynomial_index)
    source_structure = HELPER.target_structure(HELPER.parse_label(source["label"]))
    alignment = HELPER.character_alignment(
        source["quotient"], source_structure["quotientT"]
    )
    cores_by_label = alignment["labelToUnambiguousSquarefreeNormCores"]
    pairs = live_pairs(args.db, set(cores_by_label))

    aligned_rows = []
    all_core_primes = set()
    for pair in pairs:
        for core_value in cores_by_label.get(pair["label"], []):
            core = ZZ(core_value)
            if core <= 0:
                continue
            all_core_primes.update(int(prime) for prime in core.prime_divisors())
            aligned_rows.append({**pair, "normCore": int(core)})
    rational_primes = sorted(all_core_primes | set(requested_auxiliary))

    field = NumberField(source["quotient"].change_ring(QQ), "a")
    s_units = field.S_unit_group(
        proof=False, S=prod(rational_primes) if rational_primes else 1
    )
    generators = list(s_units.gens_values())
    embeddings = field.embeddings(RealField(160))
    columns = []
    for unit in generators:
        sign_mask = sum(
            1 << index
            for index, embedding in enumerate(embeddings)
            if embedding(unit) < 0
        )
        columns.append(state_bits(sign_mask, unit.norm(), rational_primes))
    matrix = Matrix(
        GF(2),
        12 + len(rational_primes),
        len(generators),
        lambda row, column: columns[column][row],
    )
    image = matrix.column_space()

    results = []
    for row in aligned_rows:
        negative_embeddings = 12 - row["r"] // 2
        core = ZZ(row["normCore"])
        samples = []
        count = 0
        for indexes in itertools.combinations(range(12), negative_embeddings):
            sign_mask = sum(1 << index for index in indexes)
            target = vector(
                GF(2),
                [(sign_mask >> index) & 1 for index in range(12)]
                + [int(core.valuation(prime)) & 1 for prime in rational_primes],
            )
            if target not in image:
                continue
            count += 1
            if len(samples) < args.sample_masks:
                samples.append(sign_mask)
        auxiliary_for_task = [
            prime for prime in rational_primes if core.valuation(prime) == 0
        ]
        results.append(
            {
                **row,
                "auxiliaryPrimesForTask": auxiliary_for_task,
                "sampleSolvableSignMasks": samples,
                "solvableSignMasks": count,
                "status": "solvable" if count else "no_solvable_sign_mask",
            }
        )

    result = {
        "alignment": {
            "quotientT": int(alignment["quotientT"]),
            "ramifiedPrimes": [int(value) for value in alignment["ramifiedPrimes"]],
        },
        "audit": {
            "networkCalls": 0,
            "requestedAuxiliaryPrimes": requested_auxiliary,
            "submissionCalls": 0,
            "unionCorePrimes": sorted(all_core_primes),
        },
        "matrix": {
            "columns": int(matrix.ncols()),
            "imageDimension": int(matrix.rank()),
            "kernelDimension": int(matrix.right_kernel().dimension()),
            "rationalPrimes": rational_primes,
            "rows": int(matrix.nrows()),
            "sUnitPrimeIdealCount": len(s_units.primes()),
        },
        "results": results,
        "source": {key: value for key, value in source.items() if key != "quotient"},
        "summary": {
            "feasiblePairs": sum(row["solvableSignMasks"] > 0 for row in results),
            "liveAlignedPairs": len(results),
            "totalSolvableSignMasks": sum(row["solvableSignMasks"] for row in results),
        },
    }
    args.output.resolve().write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output.resolve()), **result["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

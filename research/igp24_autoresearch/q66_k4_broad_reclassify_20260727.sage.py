#!/usr/bin/env sage -python
"""Reclassify every Q66/K4 radical against the full compatible 24T catalog.

The first search only compared rank-nine groups.  The constructed Kummer
module has rank *at most* nine, so lower-rank Q66 extensions must also be
retained.  This fail-closed pass uses every 24T group with a 12x2/12T66 block
quotient and kernel at most 2^9, archimedean type, and Frobenius profiles.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, libgap, prime_range


ROOT = Path.cwd()
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
INPUT = ROOT / "data" / "q66_k4_radical_search_20260727.jsonl"
OUTPUT = ROOT / "data" / "q66_k4_broad_reclassification_20260727.json"
DB = ROOT / "data" / "ledger.sqlite3"


def gap_cycle_type(permutation):
    return tuple(
        sorted(
            int(value)
            for value in libgap.CycleLengths(
                permutation, libgap.eval("[1..24]")
            )
        )
    )


def polynomial_cycle_type(polynomial, prime):
    reduced = polynomial.change_ring(GF(prime))
    if not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def live_gate(connection, label, real_roots, digest):
    target = connection.execute(
        "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
        (label, real_roots),
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (label, real_roots),
    ).fetchone()
    owned = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1",
        (label, real_roots),
    ).fetchone()
    known = connection.execute(
        "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
    ).fetchone()
    return {
        "teamCount": None if target is None else int(target[0]),
        "discovered": None if target is None else int(target[1]),
        "generatedAt": None if target is None else target[2],
        "baseline": baseline is not None,
        "owned": owned is not None,
        "knownHash": known is not None,
        "currentTc0": bool(
            target
            and int(target[0]) == 0
            and int(target[1]) == 0
            and baseline is None
            and owned is None
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if output_path.exists():
        raise ValueError(f"refusing to overwrite {output_path}")
    compatible = {}
    for line in STRUCTURES.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        for system in row.get("blockSystems", []):
            if (
                system.get("shape") == "12x2"
                and system.get("quotientActionLabel") == "12T66"
                and int(system.get("blockKernelOrder", 0)) <= 2**9
            ):
                compatible[int(row["t"])] = {
                    "label": str(row["label"]),
                    "kernelOrder": int(system["blockKernelOrder"]),
                    "order": int(row["groupOrder"]),
                }
                break

    profiles = {}
    for target_t in sorted(compatible):
        group = libgap.TransitiveGroup(24, target_t)
        profiles[target_t] = {
            gap_cycle_type(libgap.Representative(conjugacy_class))
            for conjugacy_class in libgap.ConjugacyClasses(group)
        }

    ring = PolynomialRing(ZZ, "x")
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    results = []
    for source in [
        json.loads(line)
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]:
        if (
            not source.get("candidateCoefficientLine")
            or source.get("realRoots") is None
        ):
            continue
        polynomial = ring(
            [ZZ(value) for value in source["candidateCoefficientLine"].split(",")]
        )
        real_roots = int(source["realRoots"])
        archimedean = tuple(
            sorted([1] * real_roots + [2] * ((24 - real_roots) // 2))
        )
        possible = {
            target_t
            for target_t in compatible
            if archimedean in profiles[target_t]
        }
        observations = []
        eliminations = []
        for prime in prime_range(2, 5001):
            cycle_type = polynomial_cycle_type(polynomial, int(prime))
            if cycle_type is None:
                continue
            before = set(possible)
            possible = {
                target_t
                for target_t in possible
                if cycle_type in profiles[target_t]
            }
            observations.append(
                {"prime": int(prime), "cycleType": list(cycle_type)}
            )
            removed = sorted(before - possible)
            if removed:
                eliminations.append(
                    {
                        "prime": int(prime),
                        "cycleType": list(cycle_type),
                        "removed": [f"24T{value}" for value in removed],
                    }
                )
            if len(possible) <= 1:
                break

        digest = str(source["candidateSha256"])
        gates = {
            f"24T{target_t}": live_gate(
                connection, f"24T{target_t}", real_roots, digest
            )
            for target_t in sorted(possible)
        }
        results.append(
            {
                "candidateSha256": digest,
                "coefficientLine": source["candidateCoefficientLine"],
                "sourceParameter": source.get(
                    "hilbert90Parameter", source.get("shift")
                ),
                "realRoots": real_roots,
                "observations": observations,
                "eliminations": eliminations,
                "survivors": [
                    {
                        **compatible[target_t],
                        "gate": gates[f"24T{target_t}"],
                    }
                    for target_t in sorted(possible)
                ],
                "status": (
                    "unique_exact_catalog_survivor"
                    if len(possible) == 1
                    else "no_compatible_survivor"
                    if not possible
                    else "ambiguous_compatible_survivors"
                ),
            }
        )
    connection.close()

    payload = {
        "schemaVersion": "q66-k4-broad-reclassification-v1",
        "compatibleCatalogCount": len(compatible),
        "compatibleCatalog": [compatible[t] for t in sorted(compatible)],
        "input": str(input_path.relative_to(ROOT)),
        "inputSha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "results": results,
        "uniqueCurrentTc0": [
            {
                "candidateSha256": row["candidateSha256"],
                "coefficientLine": row["coefficientLine"],
                "sourceParameter": row["sourceParameter"],
                "realRoots": row["realRoots"],
                "survivor": row["survivors"][0],
            }
            for row in results
            if row["status"] == "unique_exact_catalog_survivor"
            and row["survivors"][0]["gate"]["currentTc0"]
        ],
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output_path)
    print(
        json.dumps(
            {
                "catalog": len(compatible),
                "statuses": {
                    status: sum(row["status"] == status for row in results)
                    for status in sorted({row["status"] for row in results})
                },
                "uniqueCurrentTc0": [
                    {
                        "parameter": row["sourceParameter"],
                        "hash": row["candidateSha256"],
                        "label": row["survivors"][0]["label"],
                        "r": row["realRoots"],
                    }
                    for row in results
                    if row["status"] == "unique_exact_catalog_survivor"
                    and row["survivors"][0]["gate"]["currentTc0"]
                ],
                "output": str(output_path.relative_to(ROOT)),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

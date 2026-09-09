#!/usr/bin/env sage -python
"""Rigorously disambiguate multi-orbit factors with Frobenius cycle types.

Every candidate factor comes from an exact GAP-certified list of pair actions.
For primes where the polynomial is squarefree, its modular factor degrees are
an exact Frobenius cycle partition.  A target permutation group that lacks any
observed partition is impossible.  We combine those exclusions with exact
complex-conjugation profiles and orbit multiplicities, then retain only factor
labels that are constant over every surviving assignment.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, prime_range


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
MULTI_RESULTS = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
SIGNATURE_MAP = ROOT / "data" / "pair_signature_map.jsonl"
CYCLE_MAP = ROOT / "data" / "group_cycle_types.jsonl"
OUTPUT = ROOT / "data" / "frobenius_assignments.jsonl"
SUMMARY = ROOT / "data" / "frobenius_summary.json"
MANIFEST = ROOT / "outbox" / "frobenius_gold.txt"


def load_jsonl(path: Path, key: str) -> dict[str, dict]:
    return {
        str(row[key]): row
        for row in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        )
    }


def compatible_profiles(result: dict, signature_row: dict) -> list[dict]:
    actual_r = sorted(int(row["targetR"]) for row in result["candidates"])
    return [
        profile
        for profile in signature_row["profiles"]
        if int(profile["sourceR"]) == int(result["sourceR"])
        and sorted(int(row["targetR"]) for row in profile["orbitSignatures"])
        == actual_r
    ]


def modular_patterns(coefficient_line: str, maximum: int) -> list[list[int]]:
    coefficient_values = [int(value) for value in coefficient_line.split(",")]
    patterns = []
    seen = set()
    for prime in prime_range(29, 5000):
        ring = PolynomialRing(GF(prime), "x")
        polynomial = ring(coefficient_values)
        if polynomial.degree() != 24 or not polynomial.is_squarefree():
            continue
        factors = list(polynomial.factor())
        if any(int(exponent) != 1 for _factor, exponent in factors):
            continue
        degrees = tuple(
            sorted(
                int(factor.degree()) for factor, _exponent in factors
            )
        )
        if sum(degrees) != 24 or degrees in seen:
            continue
        seen.add(degrees)
        patterns.append(list(degrees))
        if len(patterns) >= maximum:
            break
    return patterns


def unique_multiset_permutations(values: list[str]):
    return sorted(set(itertools.permutations(values)))


def surviving_assignments(
    result: dict,
    profiles: list[dict],
    factor_possible_labels: list[set[str]],
) -> set[tuple[str, ...]]:
    candidates = result["candidates"]
    all_assignments: set[tuple[str, ...]] = set()
    for profile in profiles:
        partial: list[tuple[dict[int, str], int]] = [({}, 0)]
        for r in sorted({int(row["targetR"]) for row in candidates}):
            factor_indexes = [
                index
                for index, candidate in enumerate(candidates)
                if int(candidate["targetR"]) == r
            ]
            labels = [
                str(row["targetLabel"])
                for row in profile["orbitSignatures"]
                if int(row["targetR"]) == r
            ]
            if len(factor_indexes) != len(labels):
                partial = []
                break
            next_partial = []
            for permutation in unique_multiset_permutations(labels):
                if any(
                    label not in factor_possible_labels[index]
                    for index, label in zip(factor_indexes, permutation)
                ):
                    continue
                for assignment, marker in partial:
                    extended = dict(assignment)
                    extended.update(dict(zip(factor_indexes, permutation)))
                    next_partial.append((extended, marker + 1))
            partial = next_partial
        for assignment, _marker in partial:
            if len(assignment) == len(candidates):
                all_assignments.add(
                    tuple(assignment[index] for index in range(len(candidates)))
                )
    return all_assignments


def refine_result(
    result: dict,
    signature_row: dict,
    cycle_catalogs: dict[str, dict],
    maximum_patterns: int,
) -> dict:
    profiles = compatible_profiles(result, signature_row)
    orbit_labels = sorted(
        {str(row["targetLabel"]) for row in result["orbitTargets"]}
    )
    catalog_sets = {
        label: {
            tuple(int(value) for value in partition)
            for partition in cycle_catalogs[label]["cycleTypes"]
        }
        for label in orbit_labels
    }
    factor_possible_labels = []
    factors = []
    for candidate in result["candidates"]:
        patterns = modular_patterns(candidate["coefficientLine"], maximum_patterns)
        possible = {
            label
            for label in orbit_labels
            if all(tuple(pattern) in catalog_sets[label] for pattern in patterns)
        }
        factor_possible_labels.append(possible)
        factors.append(
            {
                **candidate,
                "frobeniusPatterns": patterns,
                "cycleCompatibleLabels": sorted(possible),
                "cycleEliminatedLabels": sorted(set(orbit_labels) - possible),
            }
        )

    assignments = surviving_assignments(result, profiles, factor_possible_labels)
    for index, factor in enumerate(factors):
        labels = {assignment[index] for assignment in assignments}
        factor["survivingLabels"] = sorted(labels)
        factor["exactTargetLabel"] = next(iter(labels)) if len(labels) == 1 else None
    return {
        "sourceSubmissionId": result["sourceSubmissionId"],
        "sourcePolynomialIndex": result["sourcePolynomialIndex"],
        "sourceLabel": result["sourceLabel"],
        "sourceR": result["sourceR"],
        "compatibleClassIndexes": [int(row["classIndex"]) for row in profiles],
        "survivingAssignmentCount": len(assignments),
        "survivingAssignments": [list(row) for row in sorted(assignments)],
        "factors": factors,
    }


def select_gold(refined_rows: list[dict]) -> list[dict]:
    exact_factors = []
    with sqlite3.connect(DB_PATH) as conn:
        for row in refined_rows:
            for factor in row["factors"]:
                label = factor.get("exactTargetLabel")
                if label is None:
                    continue
                r = int(factor["targetR"])
                target = conn.execute(
                    "SELECT t,team_count FROM targets WHERE label=? AND r=?",
                    (label, r),
                ).fetchone()
                baseline = conn.execute(
                    "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
                    (label, r),
                ).fetchone()
                owned = conn.execute(
                    "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
                    (label, r),
                ).fetchone()
                factor.update(
                    {
                        "targetT": int(target[0]) if target else None,
                        "teamCount": int(target[1]) if target else None,
                        "baseline": baseline is not None,
                        "locallyOwned": owned is not None,
                        "sourceLabel": row["sourceLabel"],
                        "sourceR": row["sourceR"],
                        "sourceSubmissionId": row["sourceSubmissionId"],
                        "sourcePolynomialIndex": row["sourcePolynomialIndex"],
                    }
                )
                if target and int(target[1]) == 0 and baseline is None and owned is None:
                    exact_factors.append(factor)
    best: dict[tuple[str, int], dict] = {}
    for factor in exact_factors:
        key = (str(factor["exactTargetLabel"]), int(factor["targetR"]))
        incumbent = best.get(key)
        if incumbent is None or int(factor["polynomialDiscriminantAbs"]) < int(
            incumbent["polynomialDiscriminantAbs"]
        ):
            best[key] = factor
    return sorted(best.values(), key=lambda row: (row["targetT"], row["targetR"]))


def write_outputs(refined: list[dict], selected: list[dict]) -> None:
    temporary = OUTPUT.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in refined:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(OUTPUT)
    manifest_temporary = MANIFEST.with_suffix(".txt.tmp")
    manifest_temporary.write_text(
        "".join(f"{row['coefficientLine']}\n" for row in selected),
        encoding="utf-8",
    )
    manifest_temporary.replace(MANIFEST)
    summary = {
        "sources": len(refined),
        "factors": sum(len(row["factors"]) for row in refined),
        "exactlyAssignedFactors": sum(
            factor["exactTargetLabel"] is not None
            for row in refined
            for factor in row["factors"]
        ),
        "gold": len(selected),
        "manifest": str(MANIFEST),
        "selectedPairs": [
            {
                "label": row["exactTargetLabel"],
                "r": row["targetR"],
                "sourceLabel": row["sourceLabel"],
                "sourceR": row["sourceR"],
                "coefficientSha256": row["coefficientSha256"],
            }
            for row in selected
        ],
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patterns", type=int, default=25)
    parser.add_argument(
        "--source-pair",
        action="append",
        default=[],
        metavar="LABEL/R",
        help="refine only an explicit source label/signature pair; repeatable",
    )
    args = parser.parse_args()
    selected_source_pairs = set()
    for value in args.source_pair:
        try:
            label, r_text = value.rsplit("/", 1)
            selected_source_pairs.add((label, int(r_text)))
        except ValueError as exc:
            parser.error(f"invalid --source-pair {value!r}; expected LABEL/R")
    multi_rows = [
        json.loads(line)
        for line in MULTI_RESULTS.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if selected_source_pairs:
        multi_rows = [
            row
            for row in multi_rows
            if (str(row.get("sourceLabel")), int(row.get("sourceR", -1)))
            in selected_source_pairs
        ]
        found = {
            (str(row["sourceLabel"]), int(row["sourceR"])) for row in multi_rows
        }
        missing = sorted(selected_source_pairs - found)
        if missing:
            parser.error(f"source pairs absent from multi results: {missing}")
    signatures = load_jsonl(SIGNATURE_MAP, "sourceLabel")
    cycles = load_jsonl(CYCLE_MAP, "label")
    refined = []
    for index, result in enumerate(multi_rows, start=1):
        if result.get("status") != "certified_multi":
            continue
        row = refine_result(
            result,
            signatures[result["sourceLabel"]],
            cycles,
            args.patterns,
        )
        refined.append(row)
        print(
            f"refined {index}/{len(multi_rows)}: {result['sourceLabel']} r={result['sourceR']}",
            flush=True,
        )
    selected = select_gold(refined)
    write_outputs(refined, selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

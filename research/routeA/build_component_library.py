#!/usr/bin/env python3
"""Build classified cubic or octic component libraries with PARI/GP."""

from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

from routeA.constructions.compositum_8x3 import classify_component


def cubic_seeds(bound: int = 8) -> Iterable[tuple[int, ...]]:
    # Shanks cyclic cubics plus generic S3 cubics.
    for t in range(-bound, bound + 1):
        if t != -1:
            yield (-1, -(t + 3), -t, 1)
    for a in range(-bound, bound + 1):
        for b in range(-bound, bound + 1):
            if b:
                yield (b, a, 0, 1)


def octic_seeds(bound: int = 4) -> Iterable[tuple[int, ...]]:
    seen = set()
    for exponent in (1, 2, 3, 4, 5, 6, 7):
        for a in range(-bound, bound + 1):
            for b in range(-bound, bound + 1):
                if not a or not b:
                    continue
                coeffs = [0] * 9
                coeffs[0], coeffs[exponent], coeffs[8] = b, a, 1
                key = tuple(coeffs)
                if key not in seen:
                    seen.add(key)
                    yield key
    # Add quartic-shaped perturbations to escape the generic S8 stratum.
    for a, b, c in itertools.product(range(-bound, bound + 1), repeat=3):
        if not a or not c:
            continue
        coeffs = (c, b, 0, 0, a, 0, 0, 0, 1)
        if coeffs not in seen:
            seen.add(coeffs)
            yield coeffs


def build_library(
    seeds: Iterable[Sequence[int]],
    *,
    max_records: int,
    max_per_stratum: int,
) -> tuple[list[dict], dict[str, int]]:
    records = []
    strata: Counter[tuple[int, int]] = Counter()
    stats: Counter[str] = Counter()
    seen_components = set()
    for coefficients in seeds:
        stats["attempted"] += 1
        try:
            component = classify_component(coefficients)
        except Exception:
            stats["rejected"] += 1
            continue
        if component.component_id in seen_components:
            stats["duplicate"] += 1
            continue
        stratum = (component.transitive_id, component.root_count)
        if strata[stratum] >= max_per_stratum:
            stats["stratum_full"] += 1
            continue
        seen_components.add(component.component_id)
        strata[stratum] += 1
        records.append(component.to_json())
        stats["accepted"] += 1
        if len(records) >= max_records:
            break
    stats["strata"] = len(strata)
    return records, dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("degree", type=int, choices=(3, 8))
    parser.add_argument("output")
    parser.add_argument("--input", help="optional JSONL records with a coefficients field")
    parser.add_argument("--bound", type=int, default=4)
    parser.add_argument("--max-records", type=int, default=200)
    parser.add_argument("--max-per-stratum", type=int, default=25)
    args = parser.parse_args()
    if args.input:
        seeds = (
            json.loads(line)["coefficients"]
            for line in Path(args.input).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    elif args.degree == 3:
        seeds = cubic_seeds(max(args.bound, 4))
    else:
        seeds = octic_seeds(args.bound)
    records, stats = build_library(
        seeds,
        max_records=args.max_records,
        max_per_stratum=args.max_per_stratum,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

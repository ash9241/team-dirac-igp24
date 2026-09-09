#!/usr/bin/env python3
"""Derive conservative 24T compatibility sets from tower intermediate fields."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.constructions.compositum_8x3 import classify_component
from routeA.ledger import canonical_coefficients
from routeA.oracle import GP, unramified_cycle_patterns
from routeA.oracle_v2 import normalize_cycle_pattern


def support_compatible_labels(
    patterns: Iterable[str],
    cycle_indices: Mapping[int, Mapping[str, float]],
) -> tuple[int, ...]:
    """Keep every group supporting every observed unramified cycle type."""

    observed = {normalize_cycle_pattern(pattern) for pattern in patterns}
    return tuple(sorted(
        int(label)
        for label, index in cycle_indices.items()
        if all(float(index.get(pattern, 0.0)) > 0.0 for pattern in observed)
    ))


def cycle_index_order(index: Mapping[str, float], degree: int) -> int:
    identity = ".".join(["1"] * int(degree))
    probability = float(index[identity])
    return round(1.0 / probability)


def cycle_index_sign(index: Mapping[str, float], degree: int) -> int:
    for pattern, probability in index.items():
        cycles = len(normalize_cycle_pattern(pattern).split("."))
        if probability > 0 and (int(degree) - cycles) % 2:
            return -1
    return 1


def pari_degree6_invariants(
    lines: Sequence[str], *, timeout: float = 1800
) -> list[tuple[int, int]]:
    script = ["default(new_galois_format,1);"]
    for index, line in enumerate(lines):
        terms = "+".join(
            f"({coefficient})*x^{power}"
            for power, coefficient in enumerate(line.split(","))
            if coefficient != "0"
        )
        script.append(
            f"g=polgalois({terms});print(\"PG6|{index}|\",g[1],\"|\",g[2]);"
        )
    result = subprocess.run(
        [GP, "-q", "-f", "-s", "400000000"],
        input="\n".join(script) + "\nquit;\n",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    invariants: list[tuple[int, int] | None] = [None] * len(lines)
    for raw in result.stdout.splitlines():
        if raw.startswith("PG6|"):
            _, raw_index, order, sign = raw.split("|", 3)
            invariants[int(raw_index)] = (int(order), int(sign))
    if any(value is None for value in invariants):
        raise RuntimeError("PARI did not return every degree-6 Galois invariant")
    return [value for value in invariants if value is not None]


def compatible_degree24_labels(
    census: Sequence[Mapping[str, Any]],
    *,
    quotient3: int,
    quotient6: Iterable[int],
    quotient12: Iterable[int],
    block_shape: Iterable[int] = (2, 4, 8),
) -> tuple[int, ...]:
    """Conservatively match a tower's quotient evidence to the exact census."""

    q6 = set(map(int, quotient6))
    q12 = set(map(int, quotient12))
    wanted_shape = tuple(sorted(set(map(int, block_shape))))
    labels = []
    for group in census:
        if tuple(map(int, group.get("block_sizes", []))) != wanted_shape:
            continue
        quotients = {
            (
                int(row["block_size"]),
                int(row["quotient_degree"]),
                int(row["quotient_t"]),
            )
            for row in group.get("block_quotients", [])
        }
        if (8, 3, int(quotient3)) not in quotients:
            continue
        if not any((4, 6, target) in quotients for target in q6):
            continue
        if not any((2, 12, target) in quotients for target in q12):
            continue
        labels.append(int(group["t"]))
    return tuple(sorted(labels))


def annotate_towers(
    candidates: Sequence[Mapping[str, Any]],
    patterns6: Sequence[set[str]],
    patterns12: Sequence[set[str]],
    cycle6: Mapping[int, Mapping[str, float]],
    cycle12: Mapping[int, Mapping[str, float]],
    census: Sequence[Mapping[str, Any]],
    invariants6: Sequence[tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    output = []
    if invariants6 is None:
        invariants6 = [(0, 0)] * len(candidates)
    for candidate, observed6, observed12, invariant6 in zip(
        candidates, patterns6, patterns12, invariants6
    ):
        quotient6 = support_compatible_labels(observed6, cycle6)
        if invariant6 != (0, 0):
            order6, sign6 = invariant6
            quotient6 = tuple(
                label for label in quotient6
                if cycle_index_order(cycle6[label], 6) == order6
                and cycle_index_sign(cycle6[label], 6) == sign6
            )
        quotient12 = support_compatible_labels(observed12, cycle12)
        cubic = candidate.get("parameters", {}).get("cubic")
        if not cubic or not quotient6 or not quotient12:
            continue
        quotient3 = int(classify_component(cubic).group_label.split("T", 1)[1])
        compatible = compatible_degree24_labels(
            census,
            quotient3=quotient3,
            quotient6=quotient6,
            quotient12=quotient12,
        )
        if not compatible:
            continue
        record = dict(candidate)
        record.pop("target_t", None)
        record.pop("target_r", None)
        record.pop("label_probability", None)
        record["coefficients"] = canonical_coefficients(record["coefficients"])
        record["compatible_labels"] = list(compatible)
        record["quotient_evidence"] = {
            "kind": "unramified-cycle-support-and-exact-block-census",
            "quotient3": quotient3,
            "possible_quotient6": list(quotient6),
            "possible_quotient12": list(quotient12),
            "observed_patterns6": len(observed6),
            "observed_patterns12": len(observed12),
            "compatible_degree24_labels": len(compatible),
            "quotient6_order": invariant6[0] or None,
            "quotient6_sign": invariant6[1] or None,
        }
        output.append(record)
    return output


def _line(values: Sequence[int]) -> str:
    return ",".join(str(int(value)) for value in values)


def _indices(path: str | Path) -> dict[int, dict[str, float]]:
    return {
        int(label): {
            normalize_cycle_pattern(pattern): float(probability)
            for pattern, probability in index.items()
        }
        for label, index in json.loads(Path(path).read_text(encoding="utf-8")).items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates")
    parser.add_argument("cycle6")
    parser.add_argument("cycle12")
    parser.add_argument("census")
    parser.add_argument("output")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--primes", type=int, default=1000)
    args = parser.parse_args()
    candidates = [
        json.loads(line)
        for line in Path(args.candidates).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lines6 = [_line(row["intermediate_degree6_coefficients"]) for row in candidates]
    lines12 = [_line(row["intermediate_degree12_coefficients"]) for row in candidates]
    patterns6 = unramified_cycle_patterns(
        lines6, workers=args.workers, prime_count=args.primes
    )
    patterns12 = unramified_cycle_patterns(
        lines12, workers=args.workers, prime_count=args.primes
    )
    invariants6 = pari_degree6_invariants(lines6)
    census = [
        json.loads(line)
        for line in Path(args.census).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    output = annotate_towers(
        candidates,
        patterns6,
        patterns12,
        _indices(args.cycle6),
        _indices(args.cycle12),
        census,
        invariants6,
    )
    Path(args.output).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in output)
        + ("\n" if output else ""),
        encoding="utf-8",
    )
    sizes = sorted(len(row["compatible_labels"]) for row in output)
    print(json.dumps({
        "candidates": len(candidates),
        "annotated": len(output),
        "median_compatible_labels": sizes[len(sizes) // 2] if sizes else None,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

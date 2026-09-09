#!/usr/bin/env python3
"""Exact unordered-pair-product resolvents for degree-12 polynomials."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Sequence

from routeA.oracle import GP


def canonical_monic(coefficients: Sequence[int]) -> str:
    values = [int(value) for value in coefficients]
    while len(values) > 1 and values[0] == 0:
        values.pop(0)
    if not values or values[-1] != 1:
        raise ValueError("resolvent must be monic")
    return ",".join(map(str, values))


def _square_root_monic(descending: Sequence[int]) -> list[int]:
    if len(descending) != 133 or descending[0] != 1:
        raise ValueError("expected a monic degree-132 square")
    root = [1]
    for k in range(1, 67):
        remainder = descending[k] - sum(
            root[i] * root[k - i]
            for i in range(1, k)
            if i < len(root) and k - i < len(root)
        )
        if remainder % 2:
            raise ValueError("degree-132 resolvent is not a polynomial square")
        root.append(remainder // 2)
    square = [0] * 133
    for i, left in enumerate(root):
        for j, right in enumerate(root):
            square[i + j] += left * right
    if square != list(descending):
        raise ValueError("degree-132 square root verification failed")
    return list(reversed(root))


def pair_product_resolvent_lines(
    lines: Sequence[str], *, timeout: float = 3600
) -> list[str]:
    script = []
    for index, line in enumerate(lines):
        terms = "+".join(
            f"({coefficient})*x^{power}"
            for power, coefficient in enumerate(line.split(","))
            if coefficient != "0"
        )
        script.append(
            f"f={terms}; rr=polresultant(f,x^12*subst(f,x,y/x),x); "
            f"dd=polresultant(f,y-x^2,x); vv=Vec(rr/dd); "
            f"for(k=1,#vv,print(\"QPR|{index}|\",k,\"|\",vv[k]));"
        )
    result = subprocess.run(
        [GP, "-q", "-f", "-s", "400000000"],
        input="\n".join(script) + "\nquit;\n",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    values: list[list[int]] = [[] for _ in lines]
    for raw in result.stdout.splitlines():
        if not raw.startswith("QPR|"):
            continue
        _, raw_index, _, raw_value = raw.split("|", 3)
        values[int(raw_index)].append(int(raw_value))
    if any(len(value) != 133 for value in values):
        raise RuntimeError("PARI did not return every 132-degree resolvent")
    return [
        canonical_monic(_square_root_monic(value))
        for value in values
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    lines = [
        ",".join(map(str, row["intermediate_degree12_coefficients"]))
        for row in rows
    ]
    resolvents = pair_product_resolvent_lines(lines)
    Path(args.output).write_text(
        "\n".join(json.dumps({"coefficients": line}) for line in resolvents) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"candidates": len(rows), "resolvents": len(resolvents)}))


if __name__ == "__main__":
    main()

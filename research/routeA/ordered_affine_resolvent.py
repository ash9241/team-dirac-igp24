#!/usr/bin/env python3
"""Ordered affine resolvents for degree-12 intermediates."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Sequence

from routeA.oracle import GP
from routeA.ledger import candidate_hash


def canonical_monic(coefficients: Sequence[int]) -> str:
    values = [int(value) for value in coefficients]
    while len(values) > 1 and values[0] == 0:
        values.pop(0)
    if not values or values[-1] != 1:
        raise ValueError("resolvent must be monic")
    return ",".join(map(str, values))


def ordered_affine_resolvent_lines(
    lines: Sequence[str], *, scale: int = 2, timeout: float = 3600
) -> list[str]:
    script = []
    for index, line in enumerate(lines):
        terms = "+".join(
            f"({coefficient})*x^{power}"
            for power, coefficient in enumerate(line.split(","))
            if coefficient != "0"
        )
        script.append(
            f"f={terms}; rr=polresultant(f,{scale}^12*subst(f,x,(y-x)/{scale}),x); "
            f"dd={(1 + scale)}^12*subst(f,x,y/{1 + scale}); "
            f"vv=Vec(rr/dd); for(k=1,#vv,print(\"OAR|{index}|\",k,\"|\",vv[k]));"
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
        if raw.startswith("OAR|"):
            _, raw_index, _, raw_value = raw.split("|", 3)
            values[int(raw_index)].append(int(raw_value))
    if any(len(value) != 133 for value in values):
        raise RuntimeError("PARI did not return every degree-132 affine resolvent")
    return [canonical_monic(list(reversed(value))) for value in values]


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
    output = ordered_affine_resolvent_lines(lines)
    Path(args.output).write_text(
        "\n".join(
            json.dumps(
                {
                    "candidate_hash": row.get("candidate_hash")
                    or candidate_hash(row["coefficients"]),
                    "coefficients": line,
                },
                sort_keys=True,
            )
            for row, line in zip(rows, output)
        )
        + "\n"
    )
    print(json.dumps({"candidates": len(rows), "resolvents": len(output)}))


if __name__ == "__main__":
    main()

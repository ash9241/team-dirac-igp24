#!/usr/bin/env python3
"""Map every full quadratic wreath product over the degree-12 groups.

For a separable degree-12 polynomial with Galois group ``H`` and any
quadratic relative extension ``x^2-h(alpha)``, the absolute degree-24
Galois group is contained in ``C2 wr H`` in its natural imprimitive action.
This exact overgroup does not require knowing the rational norm character,
so it is a safe starting point for proper-subgroup descent of arbitrary
Kummer seeds.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


DEFAULT_GAP = Path("/tmp/igp24-gap/gap-4.16.0/gap")
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "full_wreath_map.jsonl"


def build_gap_script() -> str:
    return r'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);
LiftPerm := function(p)
  local l,i,j;
  l := [];
  for i in [1..12] do
    j := i^p;
    l[2*i-1] := 2*j-1;
    l[2*i] := 2*j;
  od;
  return PermList(l);
end;;
flips := List([1..12], i -> (2*i-1,2*i));;
for t in [1..301] do
  H := TransitiveGroup(12,t);;
  W := Group(Concatenation(flips,List(GeneratorsOfGroup(H),LiftPerm)));;
  Print("WREATH|",t,"|",TransitiveIdentification(W),"|",Size(W),"\n");
od;
QUIT;
'''


def parse_gap_output(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("WREATH|"):
            continue
        _, base_t, target_t, order = line.split("|", 3)
        rows.append({
            "base_t": int(base_t),
            "target_t": int(target_t),
            "order": int(order),
            "construction": "C2_wreath_12T",
        })
    rows.sort(key=lambda row: int(row["base_t"]))
    if [int(row["base_t"]) for row in rows] != list(range(1, 302)):
        raise RuntimeError("incomplete full-wreath map")
    return rows


def run_gap(
    *,
    gap: str | Path = DEFAULT_GAP,
    timeout: float = 900,
) -> list[dict[str, Any]]:
    executable = Path(gap)
    if not executable.exists():
        raise FileNotFoundError(f"GAP executable not found: {executable}")
    result = subprocess.run(
        [str(executable), "-q", "-A"],
        input=build_gap_script(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"GAP full-wreath mapping failed: {result.stderr or result.stdout}")
    return parse_gap_output(result.stdout.splitlines())


def write_jsonl(rows: Iterable[dict[str, Any]], path: str | Path) -> int:
    destination = Path(path)
    materialized = list(rows)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in materialized),
        encoding="utf-8",
    )
    return len(materialized)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", default=str(DEFAULT_GAP))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    rows = run_gap(gap=args.gap, timeout=args.timeout)
    count = write_jsonl(rows, args.output)
    print(json.dumps({"rows": count, "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()

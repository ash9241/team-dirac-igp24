#!/usr/bin/env python3
"""Map the transitive degree-24 direct product ``12Tn x C2``."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


DEFAULT_GAP = Path("/tmp/igp24-gap/gap-4.16.0/gap")
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent / "data" / "degree12_quadratic_product_map.jsonl"
)


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
globalFlip := Product([1..12], i -> (2*i-1,2*i));;
for t in [1..301] do
  H := TransitiveGroup(12,t);;
  P := Group(Concatenation(List(GeneratorsOfGroup(H),LiftPerm),[globalFlip]));;
  Print("PRODUCT|",t,"|",TransitiveIdentification(P),"|",Size(P),"\n");
od;
QUIT;
'''


def parse_gap_output(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("PRODUCT|"):
            continue
        _, base_t, target_t, order = line.split("|", 3)
        rows.append({
            "base_t": int(base_t),
            "target_t": int(target_t),
            "order": int(order),
            "construction": "12T_direct_product_C2",
        })
    rows.sort(key=lambda row: int(row["base_t"]))
    if [int(row["base_t"]) for row in rows] != list(range(1, 302)):
        raise RuntimeError("incomplete degree-12 quadratic-product map")
    return rows


def run_gap(*, gap: str | Path = DEFAULT_GAP, timeout: float = 900) -> list[dict[str, Any]]:
    result = subprocess.run(
        [str(gap), "-q", "-A"],
        input=build_gap_script(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return parse_gap_output(result.stdout.splitlines())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", default=str(DEFAULT_GAP))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = run_gap(gap=args.gap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"rows": len(rows), "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()

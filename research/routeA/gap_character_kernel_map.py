#!/usr/bin/env python3
"""Map every parity-character kernel above the 301 degree-12 groups.

For ``H <= S_12`` and a quadratic character ``chi: H -> C2``, the group

    {(v, h) in C2^12 semidirect H : parity(v) = chi(h)}

is a transitive degree-24 group.  These index-two character kernels are the
high-value generalization of the discriminant-character lifts used by the
T00035 campaign.  This module obtains their exact 24T labels from GAP and
stores the character values on GAP's canonical generators of ``12Tn``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_GAP = Path("/tmp/igp24-gap/gap-4.16.0/gap")
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "character_kernel_map.jsonl"


def build_gap_script() -> str:
    """Return the exact GAP enumeration program."""

    return r'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);
C := Group((1,2));;
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
evenFlips := List([1..11], i -> flips[i]*flips[12]);;
BoolInt := function(value)
  if value then return 1; fi;
  return 0;
end;;
for t in [1..301] do
  H := TransitiveGroup(12,t);;
  gens := GeneratorsOfGroup(H);;
  signBits := List(gens, g -> SignPerm(g) = -1);;
  homs := AllHomomorphisms(H,C);;
  for hom in homs do
    bits := List(gens, g -> Image(hom,g) <> One(C));;
    kernelGens := ShallowCopy(evenFlips);
    for i in [1..Length(gens)] do
      if bits[i] then
        Add(kernelGens, flips[1]*LiftPerm(gens[i]));
      else
        Add(kernelGens, LiftPerm(gens[i]));
      fi;
    od;
    K := Group(kernelGens);;
    Print("CHAR|",t,"|",TransitiveIdentification(K),"|",
          JoinStringsWithSeparator(List(bits,BoolInt),","),"|",
          bits = List(bits, x -> false),"|",bits = signBits,"\n");
  od;
od;
QUIT;
'''


def parse_gap_output(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Collapse isomorphic character kernels while retaining all bit vectors."""

    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for raw in lines:
        line = raw.strip()
        if not line.startswith("CHAR|"):
            continue
        _, base, target, raw_bits, raw_trivial, raw_sign = line.split("|", 5)
        key = int(base), int(target)
        row = grouped.setdefault(key, {
            "base_t": key[0],
            "target_t": key[1],
            "character_bits": [],
            "includes_trivial_character": False,
            "includes_permutation_sign": False,
        })
        bits = [bool(int(value)) for value in raw_bits.split(",") if value != ""]
        if bits not in row["character_bits"]:
            row["character_bits"].append(bits)
        row["includes_trivial_character"] |= raw_trivial == "true"
        row["includes_permutation_sign"] |= raw_sign == "true"
    rows = sorted(grouped.values(), key=lambda row: (row["base_t"], row["target_t"]))
    bases = {int(row["base_t"]) for row in rows}
    if bases != set(range(1, 302)):
        missing = sorted(set(range(1, 302)) - bases)
        raise RuntimeError(f"incomplete degree-12 character map; missing {missing}")
    return rows


def run_gap(*, gap: str | Path = DEFAULT_GAP, timeout: float = 900) -> list[dict[str, Any]]:
    executable = Path(gap)
    if not executable.exists():
        raise FileNotFoundError(f"GAP executable not found: {executable}")
    result = subprocess.run(
        [str(executable), "-q"],
        input=build_gap_script(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GAP character mapping failed: {result.stderr or result.stdout}")
    return parse_gap_output(result.stdout.splitlines())


def write_jsonl(rows: Iterable[dict[str, Any]], path: str | Path) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    materialized = list(rows)
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
    per_base: dict[int, int] = defaultdict(int)
    for row in rows:
        per_base[int(row["base_t"])] += 1
    print(json.dumps({
        "base_groups": len(per_base),
        "character_targets": count,
        "output": str(args.output),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

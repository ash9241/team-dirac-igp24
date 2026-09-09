#!/usr/bin/env python3
"""Identify the four index-two reciprocal lifts above ``12T299``.

The base group is ``S6 wr C2`` in its imprimitive action on twelve roots.
For a reciprocal polynomial ``x^12 f(x + 1/x)``, the full flip kernel is
``C2^12``.  Prescribing the square class of ``f(2)f(-2)`` cuts this to an
index-two subgroup in which flip parity equals one of the four quadratic
characters of the base group.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


CHARACTERS: dict[str, tuple[int, int, int, int, int]] = {
    # Generator order: transposition/cycle in the left S6, the same two in
    # the right S6, and the element interchanging the two six-point blocks.
    "even": (0, 0, 0, 0, 0),
    "top": (0, 0, 0, 0, 1),
    "within": (1, 1, 1, 1, 0),
    "product": (1, 1, 1, 1, 1),
}


def build_gap_script() -> str:
    """Return a self-contained GAP program for the exact T-number map."""

    character_rows = ",".join(
        f'["{name}",[{",".join(map(str, bits))}]]'
        for name, bits in CHARACTERS.items()
    )
    return f'''\
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);
aL := (1,2);;
bL := (1,2,3,4,5,6);;
aR := (7,8);;
bR := (7,8,9,10,11,12);;
t := (1,7)(2,8)(3,9)(4,10)(5,11)(6,12);;
bgens := [aL,bL,aR,bR,t];;
B := Group(bgens);;
Print("BASE|",TransitiveIdentification(B),"|",Size(B),"\\n");
LiftPerm := function(p)
  local l,i,j;
  l := [];
  for i in [1..12] do
    j := i^p;
    l[2*i-1] := 2*j-1;
    l[2*i] := 2*j;
  od;
  return PermList(l);
end;
flips := List([1..12], i -> (2*i-1,2*i));;
evenGens := List([1..11], i -> flips[i]*flips[12]);;
MakeTwist := function(bits)
  local gens,j;
  gens := ShallowCopy(evenGens);
  for j in [1..5] do
    if bits[j] = 1 then
      Add(gens, flips[1]*LiftPerm(bgens[j]));
    else
      Add(gens, LiftPerm(bgens[j]));
    fi;
  od;
  return Group(gens);
end;
rows := [{character_rows}];;
for row in rows do
  H := MakeTwist(row[2]);
  Print("TWIST|",row[1],"|",TransitiveIdentification(H),"|",Size(H),"|",
        IsSubgroup(AlternatingGroup(24),H),"\\n");
od;
QUIT;
'''


def parse_gap_output(output: str) -> dict[str, object]:
    """Parse only stable tagged lines, ignoring GAP package notices."""

    base_id: int | None = None
    base_order: int | None = None
    twists: list[dict[str, object]] = []
    for line in output.splitlines():
        if line.startswith("BASE|"):
            _, raw_id, raw_order = line.split("|", 2)
            base_id, base_order = int(raw_id), int(raw_order)
        elif line.startswith("TWIST|"):
            _, character, raw_id, raw_order, raw_even = line.split("|", 4)
            twists.append(
                {
                    "character": character,
                    "target_t": int(raw_id),
                    "group_order": int(raw_order),
                    "subgroup_of_a24": raw_even == "true",
                }
            )
    if base_id is None or base_order is None or len(twists) != len(CHARACTERS):
        raise RuntimeError("incomplete GAP output for twisted reciprocal map")
    return {"base_t": base_id, "base_order": base_order, "twists": twists}


def run_gap(*, gap: str = "gap", timeout: float = 3600) -> dict[str, object]:
    """Run the exact map in GAP and return structured results."""

    if not shutil.which(gap):
        raise FileNotFoundError(f"GAP executable not found: {gap}")
    with tempfile.NamedTemporaryFile("w", suffix=".g", encoding="utf-8") as handle:
        handle.write(build_gap_script())
        handle.flush()
        result = subprocess.run(
            [gap, "-q", handle.name],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"GAP failed: {result.stderr or result.stdout}")
    parsed = parse_gap_output(result.stdout)
    if parsed["base_t"] != 299:
        raise RuntimeError(f"unexpected base group 12T{parsed['base_t']}")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", default="gap")
    parser.add_argument("--emit-script")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.emit_script:
        Path(args.emit_script).write_text(build_gap_script(), encoding="utf-8")
        return
    payload = run_gap(gap=args.gap)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

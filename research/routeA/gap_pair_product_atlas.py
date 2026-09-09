#!/usr/bin/env python3
"""Exact GAP cycle atlas for the unordered-pair action of degree-12 groups."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


def build_gap_script() -> str:
    lines = [
        'if LoadPackage("transgrp") = fail then Error("transgrp package unavailable"); fi;',
        "SizeScreen([4096,]);",
        "CycleKey := function(p)",
        "  local lengths; lengths := SortedList(CycleLengths(p,[1..66]));",
        '  return JoinStringsWithSeparator(List(lengths,String),".");',
        "end;",
        "pairs := Combinations([1..12],2);;",
    ]
    for label in range(1, 302):
        lines += [
            f"g := TransitiveGroup(12,{label});; order := Size(g);;",
            "hom := ActionHomomorphism(g,pairs,OnSets);;",
            "classes := ConjugacyClasses(g);;",
            "for cl in classes do",
            "  image := Image(hom,Representative(cl));;",
            f'  Print("PAIRINDEX|{label}|",CycleKey(image),"|",Size(cl),"|",order,"\\n");',
            "od;",
        ]
    lines.append("QUIT;")
    return "\n".join(lines) + "\n"


def parse_gap_output(output: str) -> dict[int, dict[str, float]]:
    counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    orders: dict[int, int] = {}
    for line in output.splitlines():
        if not line.startswith("PAIRINDEX|"):
            continue
        _, label, pattern, count, order = line.split("|", 4)
        label = int(label)
        counts[label][pattern] += int(count)
        orders[label] = int(order)
    result = {}
    for label, patterns in counts.items():
        total = sum(patterns.values())
        if total != orders[label]:
            raise ValueError(f"pair atlas {label} sums to {total}, expected {orders[label]}")
        result[label] = {pattern: count / total for pattern, count in patterns.items()}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--gap", default="gap")
    args = parser.parse_args()
    if not shutil.which(args.gap):
        raise FileNotFoundError(args.gap)
    with tempfile.NamedTemporaryFile("w", suffix=".g") as handle:
        handle.write(build_gap_script())
        handle.flush()
        result = subprocess.run(
            [args.gap, "-q", handle.name], capture_output=True, text=True, timeout=7200
        )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    payload = parse_gap_output(result.stdout)
    if len(payload) != 301:
        raise RuntimeError(f"GAP returned {len(payload)} pair atlases, expected 301")
    Path(args.output).write_text(json.dumps(payload, sort_keys=True) + "\n")
    print(json.dumps({"labels": len(payload)}))


if __name__ == "__main__":
    main()

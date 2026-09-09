#!/usr/bin/env python3
"""Enumerate exact degree-24 Cartesian product actions in GAP."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


TRANSITIVE_GROUP_COUNTS = {2: 1, 3: 2, 4: 5, 6: 16, 8: 50, 12: 301}


def build_gap_script(
    degree_a: int,
    degree_b: int,
    group_pairs: Iterable[tuple[int, int]],
) -> str:
    if degree_a * degree_b != 24:
        raise ValueError("component degrees must multiply to 24")
    pairs = sorted(set((int(a), int(b)) for a, b in group_pairs))
    lines = [
        'if LoadPackage("transgrp") = fail then Error("transgrp package unavailable"); fi;',
        "SizeScreen([4096,]);",
        "LiftA := function(p)",
        "  return PermList(List([1..24], function(k)",
        f"    local i, j; i := QuoInt(k-1,{degree_b})+1; j := ((k-1) mod {degree_b})+1;",
        f"    return {degree_b}*((i^p)-1)+j; end));",
        "end;",
        "LiftB := function(p)",
        "  return PermList(List([1..24], function(k)",
        f"    local i, j; i := QuoInt(k-1,{degree_b})+1; j := ((k-1) mod {degree_b})+1;",
        f"    return {degree_b}*(i-1)+(j^p); end));",
        "end;",
    ]
    for group_a, group_b in pairs:
        lines += [
            f"ga := TransitiveGroup({degree_a},{group_a});; gb := TransitiveGroup({degree_b},{group_b});;",
            "gens := Concatenation(List(GeneratorsOfGroup(ga),LiftA),",
            "                      List(GeneratorsOfGroup(gb),LiftB));;",
            "g24 := Group(gens);;",
            (
                f'Print("PRODUCT|{degree_a}|{group_a}|{degree_b}|{group_b}|",'
                'TransitiveIdentification(g24),"|",Size(g24),"\\n");'
            ),
        ]
    lines.append("QUIT;")
    return "\n".join(lines) + "\n"


def parse_gap_output(output: str) -> list[dict[str, int]]:
    records = []
    for line in output.splitlines():
        if not line.startswith("PRODUCT|"):
            continue
        _, degree_a, group_a, degree_b, group_b, target_t, order = line.split("|", 6)
        records.append({
            "degree_a": int(degree_a),
            "group_a": int(group_a),
            "degree_b": int(degree_b),
            "group_b": int(group_b),
            "target_t": int(target_t),
            "group_order": int(order),
        })
    return records


def run_map(
    degree_a: int,
    degree_b: int,
    *,
    gap: str = "gap",
    timeout: float = 3600,
) -> list[dict[str, int]]:
    if not shutil.which(gap):
        raise FileNotFoundError(f"GAP executable not found: {gap}")
    try:
        count_a = TRANSITIVE_GROUP_COUNTS[degree_a]
        count_b = TRANSITIVE_GROUP_COUNTS[degree_b]
    except KeyError as exc:
        raise ValueError(f"unsupported component degree: {exc.args[0]}") from exc
    pairs = (
        (group_a, group_b)
        for group_a in range(1, count_a + 1)
        for group_b in range(1, count_b + 1)
    )
    script = build_gap_script(degree_a, degree_b, pairs)
    with tempfile.NamedTemporaryFile("w", suffix=".g", encoding="utf-8") as handle:
        handle.write(script)
        handle.flush()
        result = subprocess.run(
            [gap, "-q", handle.name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    if result.returncode != 0:
        raise RuntimeError(f"GAP failed: {result.stderr or result.stdout}")
    records = parse_gap_output(result.stdout)
    expected = count_a * count_b
    if len(records) != expected:
        raise RuntimeError(f"GAP returned {len(records)} routes; expected {expected}")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("degree_a", type=int)
    parser.add_argument("degree_b", type=int)
    parser.add_argument("output")
    parser.add_argument("--gap", default="gap")
    parser.add_argument("--emit-script")
    args = parser.parse_args()
    try:
        count_a = TRANSITIVE_GROUP_COUNTS[args.degree_a]
        count_b = TRANSITIVE_GROUP_COUNTS[args.degree_b]
    except KeyError as exc:
        parser.error(f"unsupported component degree: {exc.args[0]}")
    pairs = (
        (group_a, group_b)
        for group_a in range(1, count_a + 1)
        for group_b in range(1, count_b + 1)
    )
    if args.emit_script:
        Path(args.emit_script).write_text(
            build_gap_script(args.degree_a, args.degree_b, pairs),
            encoding="utf-8",
        )
        return
    records = run_map(args.degree_a, args.degree_b, gap=args.gap)
    Path(args.output).write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

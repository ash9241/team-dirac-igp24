#!/usr/bin/env python3
"""Enumerate exact degree-24 product actions for 8T x 3T components in GAP."""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from routeA.constructions.compositum_8x3 import ComponentPolynomial


def build_gap_script(group_pairs: Iterable[tuple[int, int]]) -> str:
    pairs = sorted(set((int(g8), int(g3)) for g8, g3 in group_pairs))
    lines = [
        'if LoadPackage("transgrp") = fail then Error("transgrp package unavailable"); fi;',
        "SizeScreen([4096,]);",
        "Lift8 := function(p)",
        "  return PermList(List([1..24], function(k)",
        "    local i, j; i := QuoInt(k-1,3)+1; j := ((k-1) mod 3)+1;",
        "    return 3*((i^p)-1)+j; end));",
        "end;",
        "Lift3 := function(p)",
        "  return PermList(List([1..24], function(k)",
        "    local i, j; i := QuoInt(k-1,3)+1; j := ((k-1) mod 3)+1;",
        "    return 3*(i-1)+(j^p); end));",
        "end;",
    ]
    for g8, g3 in pairs:
        lines += [
            f"g8 := TransitiveGroup(8,{g8});; g3 := TransitiveGroup(3,{g3});;",
            "gens := Concatenation(List(GeneratorsOfGroup(g8),Lift8),",
            "                      List(GeneratorsOfGroup(g3),Lift3));;",
            "g24 := Group(gens);;",
            f'Print("PRODUCT|{g8}|{g3}|",TransitiveIdentification(g24),"|",Size(g24),"\\n");',
        ]
    lines.append("QUIT;")
    return "\n".join(lines) + "\n"


def build_c2_fiber_gap_script(group_pairs: Iterable[tuple[int, int]]) -> str:
    """Build exact product-action groups with matching sign quotients."""

    pairs = sorted(set((int(g8), int(g3)) for g8, g3 in group_pairs))
    prefix = build_gap_script([]).rsplit("QUIT;", 1)[0]
    lines = [prefix]
    for g8, g3 in pairs:
        lines += [
            f"g8 := TransitiveGroup(8,{g8});; g3 := TransitiveGroup(3,{g3});;",
            "if ForAny(Elements(g8),p->SignPerm(p)=-1) and",
            "   ForAny(Elements(g3),p->SignPerm(p)=-1) then",
            "  sign8 := GroupHomomorphismByFunction(g8,Group((1,2)),function(p)",
            "    if SignPerm(p)=1 then return (); else return (1,2); fi; end);;",
            "  sign3 := GroupHomomorphismByFunction(g3,Group((1,2)),function(p)",
            "    if SignPerm(p)=1 then return (); else return (1,2); fi; end);;",
            "  k8 := Kernel(sign8);; k3 := Kernel(sign3);;",
            "  o8 := First(Elements(g8),p->SignPerm(p)=-1);;",
            "  o3 := First(Elements(g3),p->SignPerm(p)=-1);;",
            "  gens := Concatenation(List(GeneratorsOfGroup(k8),Lift8),",
            "                        List(GeneratorsOfGroup(k3),Lift3),",
            "                        [Lift8(o8)*Lift3(o3)]);;",
            "  g24 := Group(gens);;",
            f'  Print("FIBER|{g8}|{g3}|",TransitiveIdentification(g24),"|",Size(g24),"\\n");',
            "else",
            f'  Print("FIBER_UNAVAILABLE|{g8}|{g3}|no-sign-c2\\n");',
            "fi;",
        ]
    lines.append("QUIT;")
    return "\n".join(lines) + "\n"


def run_gap_map(
    group_pairs: Iterable[tuple[int, int]],
    *,
    gap: str = "gap",
    timeout: float = 3600,
    mode: str = "direct",
) -> dict[tuple[int, int], int]:
    if not shutil.which(gap):
        raise FileNotFoundError(f"GAP executable not found: {gap}")
    if mode not in {"direct", "fiber-c2"}:
        raise ValueError(f"unknown GAP map mode {mode}")
    script = build_gap_script(group_pairs) if mode == "direct" else build_c2_fiber_gap_script(group_pairs)
    with tempfile.NamedTemporaryFile("w", suffix=".g", encoding="utf-8") as handle:
        handle.write(script)
        handle.flush()
        result = subprocess.run(
            [gap, "-q", handle.name], capture_output=True, text=True, timeout=timeout
        )
    if result.returncode != 0:
        raise RuntimeError(f"GAP failed: {result.stderr or result.stdout}")
    return parse_gap_output(result.stdout, mode=mode)


def parse_gap_output(output: str, *, mode: str = "direct") -> dict[tuple[int, int], int]:
    mapping = {}
    tag = "PRODUCT|" if mode == "direct" else "FIBER|"
    for line in output.splitlines():
        if not line.startswith(tag):
            continue
        _, g8, g3, t24, _order = line.split("|", 4)
        mapping[(int(g8), int(g3))] = int(t24)
    return mapping


def load_components(path: str | Path) -> list[ComponentPolynomial]:
    with Path(path).open(encoding="utf-8") as handle:
        return [ComponentPolynomial.from_json(json.loads(line)) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("octics", nargs="?")
    parser.add_argument("cubics", nargs="?")
    parser.add_argument("--gap", default="gap")
    parser.add_argument("--emit-script")
    parser.add_argument("--parse-output")
    parser.add_argument("--output")
    parser.add_argument("--mode", choices=("direct", "fiber-c2"), default="direct")
    parser.add_argument(
        "--all-degree8",
        action="store_true",
        help="enumerate every 8T1..8T50 against 3T1 and 3T2",
    )
    args = parser.parse_args()
    if args.all_degree8:
        pairs = set(itertools.product(range(1, 51), range(1, 3)))
    else:
        if not args.octics or not args.cubics:
            parser.error("octics and cubics are required unless --all-degree8 is used")
        octics = load_components(args.octics)
        cubics = load_components(args.cubics)
        pairs = {(o.transitive_id, c.transitive_id) for o in octics for c in cubics}
    if args.emit_script:
        builder = build_gap_script if args.mode == "direct" else build_c2_fiber_gap_script
        Path(args.emit_script).write_text(builder(pairs), encoding="utf-8")
        return
    if args.parse_output:
        mapping = parse_gap_output(
            Path(args.parse_output).read_text(encoding="utf-8"), mode=args.mode
        )
    else:
        mapping = run_gap_map(pairs, gap=args.gap, mode=args.mode)
    records = [
        {"g8": g8, "g3": g3, "target_t": target_t}
        for (g8, g3), target_t in sorted(mapping.items())
    ]
    payload = "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()

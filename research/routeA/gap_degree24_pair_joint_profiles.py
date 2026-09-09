#!/usr/bin/env python3
"""Build exact joint Frobenius profiles for degree-24 pair orbits.

For a source group ``24Tt``, a good prime acts through one conjugacy class of
the source Galois group.  Factoring both the source polynomial and every
degree-24 pair-resolvent factor modulo that same prime therefore gives a
*coupled* tuple of cycle types.  This module exhausts the conjugacy classes in
GAP and records every tuple that is possible.  The resulting table is an exact
certificate: a concrete factor can only be assigned to an abstract pair orbit
when all of its observed tuples occur in this table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from routeA.gap_degree24_pair_orbit_map import DEFAULT_GAP


ACTION_KINDS = {
    "unordered-pair": ("Combinations([1..24],2)", "OnSets"),
    "ordered-pair": ("Arrangements([1..24],2)", "OnTuples"),
    "triple-subset": ("Combinations([1..24],3)", "OnSets"),
    "four-subset": ("Combinations([1..24],4)", "OnSets"),
}


def build_gap_script(
    source_ts: Sequence[int], *, action_kind: str = "unordered-pair"
) -> str:
    selected = tuple(sorted({int(value) for value in source_ts}))
    if not selected:
        raise ValueError("at least one source T-number is required")
    if selected[0] < 1 or selected[-1] > 25_000:
        raise ValueError("source T-numbers must lie in [1, 25000]")
    if action_kind not in ACTION_KINDS:
        raise ValueError(f"unsupported degree-24 action kind: {action_kind}")
    group_list = "[" + ",".join(str(value) for value in selected) + "]"
    domain, action = ACTION_KINDS[action_kind]
    return rf'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([1000000,]);
CycleKey := function(p)
  local lengths;
  lengths := SortedList(CycleLengths(p,[1..24]));
  return JoinStringsWithSeparator(List(lengths,String),".");
end;;
domain := {domain};;
for t in {group_list} do
  G := TransitiveGroup(24,t);;
  orbits := Filtered(OrbitsDomain(G,domain,{action}),o->Length(o)=24);;
  homs := List(orbits,o->ActionHomomorphism(G,o,{action}));;
  targets := List(orbits,o->TransitiveIdentification(Action(G,o,{action})));;
  Print("JGROUP|",t,"|",Size(G),"|",
        JoinStringsWithSeparator(List(targets,String),","),"\n");
  for class in ConjugacyClasses(G) do
    g := Representative(class);;
    Print("JCLASS|",t,"|",Size(class),"|",CycleKey(g),"|",
          JoinStringsWithSeparator(
            List(homs,h->CycleKey(Image(h,g))),";"),"\n");
  od;
od;
QUIT;
'''


def _cycle(raw: str) -> tuple[int, ...]:
    values = tuple(sorted(int(value) for value in raw.split(".") if value))
    if not values or sum(values) != 24:
        raise ValueError(f"invalid degree-24 cycle type: {raw!r}")
    return values


def parse_joint_profiles(lines: Iterable[str]) -> list[dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    profiles: dict[int, list[tuple[int, tuple[int, ...], tuple[tuple[int, ...], ...]]]] = (
        defaultdict(list)
    )
    for raw in lines:
        line = raw.strip()
        if line.startswith("JGROUP|"):
            parts = line.split("|", 3)
            if len(parts) != 4:
                raise ValueError(f"malformed GAP group row: {line[:200]}")
            _, raw_t, raw_order, raw_targets = parts
            source_t = int(raw_t)
            if source_t in groups:
                raise ValueError(f"duplicate GAP group row for 24T{source_t}")
            targets = tuple(int(value) for value in raw_targets.split(",") if value)
            if not targets:
                raise ValueError(f"24T{source_t} has no degree-24 pair orbit")
            groups[source_t] = {
                "group_order": int(raw_order),
                "orbit_targets": targets,
            }
        elif line.startswith("JCLASS|"):
            parts = line.split("|", 4)
            if len(parts) != 5:
                raise ValueError(f"malformed GAP class row: {line[:200]}")
            _, raw_t, raw_size, raw_source_cycle, raw_orbit_cycles = parts
            source_t = int(raw_t)
            orbit_cycles = tuple(
                _cycle(value) for value in raw_orbit_cycles.split(";") if value
            )
            profiles[source_t].append(
                (int(raw_size), _cycle(raw_source_cycle), orbit_cycles)
            )

    rows: list[dict[str, Any]] = []
    for source_t, metadata in groups.items():
        targets = metadata["orbit_targets"]
        raw_profiles = profiles.get(source_t, [])
        if not raw_profiles:
            raise ValueError(f"GAP returned no conjugacy classes for 24T{source_t}")
        if any(len(item[2]) != len(targets) for item in raw_profiles):
            raise ValueError(f"pair-orbit count changed within 24T{source_t} profiles")

        # Several conjugacy classes can have the same observable tuple.  Their
        # class sizes are additive; aggregating keeps the certificate compact
        # without losing the exact Chebotarev mass.
        combined: dict[tuple[Any, ...], int] = defaultdict(int)
        for size, source_cycle, orbit_cycles in raw_profiles:
            combined[(source_cycle, orbit_cycles)] += size
        total = sum(combined.values())
        if total != int(metadata["group_order"]):
            raise ValueError(
                f"class sizes for 24T{source_t} sum to {total}, "
                f"expected {metadata['group_order']}"
            )
        packed = [
            {
                "class_size": size,
                "source_cycle": list(key[0]),
                "orbit_cycles": [list(cycle) for cycle in key[1]],
            }
            for key, size in sorted(
                combined.items(),
                key=lambda item: (item[0][0], item[0][1]),
            )
        ]
        rows.append({
            "source_t": source_t,
            "group_order": int(metadata["group_order"]),
            "orbit_count": len(targets),
            "orbit_targets": list(targets),
            "profile_count": len(packed),
            "profiles": packed,
            "evidence": "gap-exact-pair-joint-cycle-profile",
        })
    missing_headers = sorted(set(profiles) - set(groups))
    if missing_headers:
        raise ValueError(f"GAP class rows precede missing group rows: {missing_headers}")
    return sorted(rows, key=lambda row: int(row["source_t"]))


def generate_joint_profiles(
    output_path: str | Path,
    *,
    source_ts: Sequence[int],
    action_kind: str = "unordered-pair",
    gap: str | Path = DEFAULT_GAP,
    timeout: float = 7_200,
) -> dict[str, Any]:
    script = build_gap_script(source_ts, action_kind=action_kind)
    result = subprocess.run(
        [str(gap), "-q", "-A"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    rows = parse_joint_profiles(result.stdout.splitlines())
    for row in rows:
        row["action_kind"] = action_kind
        row["evidence"] = f"gap-exact-{action_kind}-joint-cycle-profile"
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(output, payload)
    summary = {
        "output": str(output),
        "source_groups": len(rows),
        "orbit_count": sum(int(row["orbit_count"]) for row in rows),
        "profile_count": sum(int(row["profile_count"]) for row in rows),
        "action_kind": action_kind,
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    _atomic_text(
        output.with_suffix(output.suffix + ".report.json"),
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    return summary


def _atomic_text(path: Path, payload: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gap", type=Path, default=DEFAULT_GAP)
    parser.add_argument("--source-t", action="append", type=int, default=[])
    parser.add_argument(
        "--action-kind", choices=tuple(ACTION_KINDS), default="unordered-pair"
    )
    parser.add_argument(
        "--orbit-map",
        type=Path,
        help="JSONL pair-orbit map; ambiguous source rows are selected",
    )
    parser.add_argument("--timeout", type=float, default=7_200)
    args = parser.parse_args()
    source_ts = list(args.source_t)
    if args.orbit_map:
        source_ts.extend(
            int(row["source_t"])
            for row in (
                json.loads(line)
                for line in args.orbit_map.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if row.get("unambiguous_target_t") is None
        )
    summary = generate_joint_profiles(
        args.output,
        source_ts=source_ts,
        action_kind=args.action_kind,
        gap=args.gap,
        timeout=args.timeout,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

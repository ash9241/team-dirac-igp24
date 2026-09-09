#!/usr/bin/env python3
"""Map exact degree-24 actions on fixed-size subsets with GAP."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from routeA.gap_degree24_pair_orbit_map import DEFAULT_GAP


def build_gap_script(
    subset_size: int,
    start_t: int = 1,
    end_t: int = 25_000,
    *,
    source_ts: Sequence[int] = (),
) -> str:
    subset_size = int(subset_size)
    if not 2 <= subset_size <= 12:
        raise ValueError("subset size must lie in [2, 12]")
    if not 1 <= int(start_t) <= int(end_t) <= 25_000:
        raise ValueError("T-number range must lie in [1, 25000]")
    selected = tuple(sorted({int(value) for value in source_ts}))
    if selected and (selected[0] < 1 or selected[-1] > 25_000):
        raise ValueError("source T-numbers must lie in [1, 25000]")
    group_list = (
        "[" + ",".join(str(value) for value in selected) + "]"
        if selected
        else f"[{int(start_t)}..{int(end_t)}]"
    )
    return rf'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
subsets := Combinations([1..24],{subset_size});;
for t in {group_list} do
  G := TransitiveGroup(24,t);;
  orbits := Filtered(
    OrbitsDomain(G,subsets,OnSets),
    orbit -> Length(orbit)=24
  );;
  for orbit in orbits do
    # Emit one bounded-width record per orbit.  GAP wraps long Print output at
    # the configured screen width, so a single target list is not parse-safe.
    Print("SUBSET24|",t,"|",
          TransitiveIdentification(Action(G,orbit,OnSets)),"\n");
  od;
od;
QUIT;
'''


def parse_subset_orbits(
    lines: Iterable[str],
    *,
    subset_size: int,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[int]] = {}
    for raw in lines:
        line = raw.strip()
        if not line.startswith("SUBSET24|"):
            continue
        _, raw_source, raw_targets = line.split("|", 2)
        source_t = int(raw_source)
        targets = [int(value) for value in raw_targets.split(",") if value]
        if not targets:
            raise ValueError(f"empty GAP subset row for 24T{source_t}")
        grouped.setdefault(source_t, []).extend(targets)
    rows: list[dict[str, Any]] = []
    for source_t, targets in grouped.items():
        rows.append({
            "source_t": source_t,
            "subset_size": int(subset_size),
            "orbit_targets": targets,
            "orbit_count": len(targets),
            "unique_targets": sorted(set(targets)),
            "unambiguous_target_t": (
                targets[0] if len(set(targets)) == 1 else None
            ),
            "evidence": f"gap-exact-{int(subset_size)}-subset-action",
        })
    return sorted(rows, key=lambda row: int(row["source_t"]))


def generate_subset_orbit_map(
    output_path: str | Path,
    *,
    subset_size: int,
    gap: str | Path = DEFAULT_GAP,
    start_t: int = 1,
    end_t: int = 25_000,
    source_ts: Sequence[int] = (),
    timeout: float = 7_200,
) -> dict[str, Any]:
    selected = tuple(sorted({int(value) for value in source_ts}))
    result = subprocess.run(
        [str(gap), "-q", "-A"],
        input=build_gap_script(
            subset_size,
            start_t,
            end_t,
            source_ts=selected,
        ),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    rows = parse_subset_orbits(result.stdout.splitlines(), subset_size=subset_size)
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(output)
    nontrivial_relations = {
        (int(row["source_t"]), int(target_t))
        for row in rows
        for target_t in row["unique_targets"]
        if int(target_t) != int(row["source_t"])
    }
    summary = {
        "output": str(output),
        "subset_size": int(subset_size),
        "source_groups": len(rows),
        "orbit_count": sum(int(row["orbit_count"]) for row in rows),
        "unambiguous_groups": sum(
            row["unambiguous_target_t"] is not None for row in rows
        ),
        "nontrivial_unambiguous_groups": sum(
            row["unambiguous_target_t"] is not None
            and int(row["unambiguous_target_t"]) != int(row["source_t"])
            for row in rows
        ),
        "nontrivial_source_target_relations": len(nontrivial_relations),
        "nontrivial_target_groups": len({target for _, target in nontrivial_relations}),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "start_t": int(start_t),
        "end_t": int(end_t),
        "requested_groups": (
            len(selected) if selected else int(end_t) - int(start_t) + 1
        ),
        "explicit_source_selection": bool(selected),
    }
    report = output.with_suffix(output.suffix + ".report.json")
    report.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--subset-size", type=int, required=True)
    parser.add_argument("--gap", type=Path, default=DEFAULT_GAP)
    parser.add_argument("--start-t", type=int, default=1)
    parser.add_argument("--end-t", type=int, default=25_000)
    parser.add_argument("--source-t", action="append", type=int, default=[])
    parser.add_argument("--source-catalog", action="append", type=Path, default=[])
    parser.add_argument("--timeout", type=float, default=7_200)
    args = parser.parse_args()
    source_ts = list(args.source_t)
    for catalog in args.source_catalog:
        source_ts.extend(
            int(json.loads(line)["source_t"])
            for line in catalog.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    summary = generate_subset_orbit_map(
        args.output,
        subset_size=args.subset_size,
        gap=args.gap,
        start_t=args.start_t,
        end_t=args.end_t,
        source_ts=source_ts,
        timeout=args.timeout,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

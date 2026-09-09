#!/usr/bin/env python3
"""Map exact degree-24 actions on ordered pairs of distinct roots with GAP."""

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
    start_t: int = 1,
    end_t: int = 25_000,
    *,
    source_ts: Sequence[int] = (),
) -> str:
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
ordered := Arrangements([1..24],2);;
for t in {group_list} do
  G := TransitiveGroup(24,t);;
  orbits := Filtered(
    OrbitsDomain(G,ordered,OnTuples),
    orbit -> Length(orbit)=24
  );;
  if Length(orbits)>0 then
    Print("ORDERED24|",t,"|");
    for orbit in orbits do
      Print(TransitiveIdentification(Action(G,orbit,OnTuples)),",");
    od;
    Print("\n");
  fi;
od;
QUIT;
'''


def parse_ordered_pair_orbits(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in lines:
        line = raw.strip()
        if not line.startswith("ORDERED24|"):
            continue
        _, raw_source, raw_targets = line.split("|", 2)
        source_t = int(raw_source)
        if source_t in seen:
            raise ValueError(f"duplicate GAP ordered-pair row for 24T{source_t}")
        targets = [int(value) for value in raw_targets.split(",") if value]
        if not targets:
            raise ValueError(f"empty GAP ordered-pair row for 24T{source_t}")
        seen.add(source_t)
        rows.append({
            "source_t": source_t,
            "orbit_targets": targets,
            "orbit_count": len(targets),
            "unique_targets": sorted(set(targets)),
            "unambiguous_target_t": (
                targets[0] if len(set(targets)) == 1 else None
            ),
            "evidence": "gap-exact-ordered-pair-action",
        })
    return sorted(rows, key=lambda row: int(row["source_t"]))


def generate_ordered_pair_orbit_map(
    output_path: str | Path,
    *,
    gap: str | Path = DEFAULT_GAP,
    start_t: int = 1,
    end_t: int = 25_000,
    source_ts: Sequence[int] = (),
    timeout: float = 7_200,
) -> dict[str, Any]:
    selected = tuple(sorted({int(value) for value in source_ts}))
    result = subprocess.run(
        [str(gap), "-q", "-A"],
        input=build_gap_script(start_t, end_t, source_ts=selected),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    rows = parse_ordered_pair_orbits(result.stdout.splitlines())
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
    summary = {
        "output": str(output),
        "source_groups": len(rows),
        "unambiguous_groups": sum(
            row["unambiguous_target_t"] is not None for row in rows
        ),
        "nontrivial_unambiguous_groups": sum(
            row["unambiguous_target_t"] is not None
            and int(row["unambiguous_target_t"]) != int(row["source_t"])
            for row in rows
        ),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "start_t": int(start_t),
        "end_t": int(end_t),
        "requested_groups": len(selected) if selected else int(end_t) - int(start_t) + 1,
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
    summary = generate_ordered_pair_orbit_map(
        args.output,
        gap=args.gap,
        start_t=args.start_t,
        end_t=args.end_t,
        source_ts=source_ts,
        timeout=args.timeout,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

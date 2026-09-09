#!/usr/bin/env python3
"""Build exact block-system and quotient-action records for live IGP24 targets."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable


def live_target_ids(progress_path: str | Path, max_team_count: int = 1) -> list[int]:
    labels = json.loads(Path(progress_path).read_text(encoding="utf-8"))
    targets = []
    for label in labels:
        signatures = label.get("signatures", [])
        if any(
            not bool(signature.get("baseline"))
            and int(signature.get("teamCount", 0)) <= max_team_count
            for signature in signatures
        ):
            targets.append(int(label.get("t") or str(label["label"]).replace("24T", "")))
    return sorted(set(targets))


def all_target_ids(progress_path: str | Path) -> list[int]:
    labels = json.loads(Path(progress_path).read_text(encoding="utf-8"))
    return sorted({
        int(label.get("t") or str(label["label"]).replace("24T", ""))
        for label in labels
    })


def build_gap_script(target_ids: Iterable[int]) -> str:
    targets = sorted(set(int(target) for target in target_ids))
    lines = [
        'if LoadPackage("transgrp") = fail then Error("transgrp package unavailable"); fi;',
        "SizeScreen([4096,]);",
    ]
    for target in targets:
        lines += [
            f"g := TransitiveGroup(24,{target});;",
            (
                f'Print("GROUP|{target}|",Size(g),"|",IsPrimitive(g),"|",'
                'IsSolvable(g),"\\n");'
            ),
            "for b in AllBlocks(g) do",
            "  if Length(b)>1 and Length(b)<24 then",
            "    orb := Orbit(g,b,OnSets);; act := Action(g,orb,OnSets);;",
            (
                f'    Print("BLOCK|{target}|",Length(b),"|",Length(orb),"|",'
                'TransitiveIdentification(act),"\\n");'
            ),
            "  fi;",
            "od;",
        ]
    lines.append("QUIT;")
    return "\n".join(lines) + "\n"


def parse_gap_output(output: str) -> list[dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    seen_quotients: dict[int, set[tuple[int, int, int]]] = {}
    for line in output.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "GROUP" and len(parts) == 5:
            target = int(parts[1])
            records[target] = {
                "t": target,
                "order": parts[2],
                "primitive": parts[3] == "true",
                "solvable": parts[4] == "true",
                "block_sizes": [],
                "block_quotients": [],
                "evidence": "gap-exact",
            }
            seen_quotients[target] = set()
        elif parts[0] == "BLOCK" and len(parts) == 5:
            target = int(parts[1])
            if target not in records:
                raise ValueError(f"BLOCK appeared before GROUP for 24T{target}")
            quotient = (int(parts[2]), int(parts[3]), int(parts[4]))
            if quotient in seen_quotients[target]:
                continue
            seen_quotients[target].add(quotient)
            records[target]["block_quotients"].append({
                "block_size": quotient[0],
                "quotient_degree": quotient[1],
                "quotient_t": quotient[2],
            })
    for record in records.values():
        record["block_quotients"].sort(
            key=lambda value: (
                value["block_size"],
                value["quotient_degree"],
                value["quotient_t"],
            )
        )
        record["block_sizes"] = sorted({
            value["block_size"] for value in record["block_quotients"]
        })
    return [records[target] for target in sorted(records)]


def run_census(
    target_ids: Iterable[int],
    *,
    gap: str = "gap",
    timeout: float = 7200,
    workers: int = 1,
) -> list[dict[str, Any]]:
    targets = sorted(set(int(target) for target in target_ids))
    if not shutil.which(gap):
        raise FileNotFoundError(f"GAP executable not found: {gap}")
    workers = max(1, min(int(workers), len(targets) or 1))
    chunks = [targets[index::workers] for index in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        outputs = list(executor.map(
            lambda chunk: _run_gap_chunk(chunk, gap=gap, timeout=timeout),
            chunks,
        ))
    records = parse_gap_output("\n".join(outputs))
    if len(records) != len(targets):
        raise RuntimeError(f"GAP returned {len(records)} groups; expected {len(targets)}")
    return records


def _run_gap_chunk(targets: list[int], *, gap: str, timeout: float) -> str:
    script = build_gap_script(targets)
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
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("progress")
    parser.add_argument("output")
    parser.add_argument("--gap", default="gap")
    parser.add_argument("--max-team-count", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--all-labels", action="store_true")
    parser.add_argument("--emit-script")
    args = parser.parse_args()
    targets = (
        all_target_ids(args.progress)
        if args.all_labels
        else live_target_ids(args.progress, args.max_team_count)
    )
    if args.emit_script:
        Path(args.emit_script).write_text(build_gap_script(targets), encoding="utf-8")
        print(json.dumps({"targets": len(targets), "script": args.emit_script}))
        return
    records = run_census(targets, gap=args.gap, workers=args.workers)
    Path(args.output).write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"targets": len(targets), "records": len(records)}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate and parse GAP cycle-index evidence for compatible 24T labels."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable


TRANSITIVE_GROUP_COUNTS = {3: 2, 6: 16, 12: 301, 24: 25000}


def build_cycle_index_script(labels: Iterable[int], degree: int = 24) -> str:
    labels = sorted(set(int(label) for label in labels))
    lines = [
        'if LoadPackage("transgrp") = fail then Error("transgrp package unavailable"); fi;',
        "SizeScreen([4096,]);",
        "CycleKey := function(p)",
        f"  local lengths; lengths := SortedList(CycleLengths(p,[1..{int(degree)}]));",
        '  return JoinStringsWithSeparator(List(lengths,String),".");',
        "end;",
    ]
    for label in labels:
        lines += [
            f"g := TransitiveGroup({int(degree)},{label});; order := Size(g);;",
            "classes := ConjugacyClasses(g);;",
            "for cl in classes do",
            f'  Print("INDEX|{label}|",CycleKey(Representative(cl)),"|",Size(cl),"|",order,"\\n");',
            "od;",
        ]
    lines.append("QUIT;")
    return "\n".join(lines) + "\n"


def parse_cycle_index(output: str) -> dict[int, dict[str, float]]:
    counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    orders: dict[int, int] = {}
    for line in output.splitlines():
        if not line.startswith("INDEX|"):
            continue
        _, label, pattern, count, order = line.split("|", 4)
        label_i = int(label)
        counts[label_i][pattern] += int(count)
        orders[label_i] = int(order)
    result = {}
    for label, patterns in counts.items():
        order = orders[label]
        total = sum(patterns.values())
        if total != order:
            raise ValueError(f"cycle-index counts for 24T{label} sum to {total}, expected {order}")
        result[label] = {pattern: count / order for pattern, count in patterns.items()}
    return result


def run_cycle_index(
    labels: Iterable[int],
    gap: str = "gap",
    timeout: float = 7200,
    workers: int = 1,
    degree: int = 24,
) -> dict[int, dict[str, float]]:
    if not shutil.which(gap):
        raise FileNotFoundError(f"GAP executable not found: {gap}")
    labels = sorted(set(int(label) for label in labels))
    workers = max(1, min(int(workers), len(labels) or 1))
    chunks = [labels[index::workers] for index in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        outputs = list(executor.map(
            lambda chunk: _run_cycle_index_chunk(
                chunk, gap=gap, timeout=timeout, degree=degree
            ),
            chunks,
        ))
    parsed = parse_cycle_index("\n".join(outputs))
    if len(parsed) != len(labels):
        raise RuntimeError(f"GAP returned {len(parsed)} cycle indices; expected {len(labels)}")
    return parsed


def _run_cycle_index_chunk(
    labels: list[int], *, gap: str, timeout: float, degree: int
) -> str:
    script = build_cycle_index_script(labels, degree=degree)
    with tempfile.NamedTemporaryFile("w", suffix=".g", encoding="utf-8") as handle:
        handle.write(script)
        handle.flush()
        result = subprocess.run([gap, "-q", handle.name], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


def labels_from_census(path: str | Path, block_shape: Iterable[int]) -> list[int]:
    wanted = tuple(sorted(set(int(value) for value in block_shape)))
    labels = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if tuple(int(value) for value in row.get("block_sizes", [])) == wanted:
                labels.append(int(row["t"]))
    return sorted(set(labels))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="?", help="comma-separated 24T numbers")
    parser.add_argument("--gap", default="gap")
    parser.add_argument("--emit-script")
    parser.add_argument("--parse-output")
    parser.add_argument("--output")
    parser.add_argument("--census", help="JSONL GAP census used to select a block shape")
    parser.add_argument("--block-shape", default="2,4,8")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--degree", type=int, default=24)
    parser.add_argument("--all-degree", action="store_true")
    args = parser.parse_args()
    if args.all_degree:
        try:
            labels = list(range(1, TRANSITIVE_GROUP_COUNTS[args.degree] + 1))
        except KeyError:
            parser.error(f"unsupported --all-degree value: {args.degree}")
    elif args.census:
        labels = labels_from_census(
            args.census,
            [int(value) for value in args.block_shape.split(",") if value],
        )
    elif args.labels:
        labels = [int(value) for value in args.labels.split(",") if value]
    else:
        parser.error("labels, --census, or --all-degree is required")
    if args.emit_script:
        Path(args.emit_script).write_text(
            build_cycle_index_script(labels, degree=args.degree), encoding="utf-8"
        )
        return
    if args.parse_output:
        indices = parse_cycle_index(Path(args.parse_output).read_text(encoding="utf-8"))
    else:
        indices = run_cycle_index(
            labels, gap=args.gap, workers=args.workers, degree=args.degree
        )
    payload = json.dumps({str(label): value for label, value in indices.items()}, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()

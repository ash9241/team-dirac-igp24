#!/usr/bin/env python3
"""Select a structurally diverse, high-value signature portfolio."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_QUOTAS = {
    24: 250,
    16: 200,
    12: 160,
    20: 140,
    8: 100,
    4: 70,
    6: 30,
    10: 20,
    2: 15,
    0: 15,
}


def _stratum(row: Mapping[str, Any]) -> tuple[str, tuple[int, ...]]:
    parameters = row.get("parameters") or {}
    form = str(parameters.get("form") or row.get("recipe_family") or "unknown")
    base = tuple(int(value) for value in parameters.get("base_coefficients") or ())
    return form, base


def _round_robin(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, tuple[int, ...]], list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        groups[_stratum(row)].append(row)
    queues = []
    for key in sorted(groups):
        ordered = sorted(
            groups[key],
            key=lambda row: (
                int(row.get("field_disc_abs") or row.get("estimated_nfdisc_abs") or 0),
                str(row.get("candidate_hash") or ""),
            ),
        )
        queues.append(deque(ordered))
    selected = []
    while queues:
        next_queues = []
        for queue in queues:
            selected.append(queue.popleft())
            if queue:
                next_queues.append(queue)
        queues = next_queues
    return selected


def select_signature_portfolio(
    rows: Iterable[Mapping[str, Any]],
    *,
    quotas: Mapping[int, int] = DEFAULT_QUOTAS,
    limit: int = 1000,
    exclude_hashes: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    excluded = {str(value) for value in exclude_hashes if str(value)}
    unique: dict[str, dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        key = str(row.get("candidate_hash") or "")
        if not key:
            raise ValueError("candidate row has no candidate_hash")
        if key not in excluded:
            unique.setdefault(key, row)
    by_root: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in unique.values():
        root_count = int(row.get("target_r", row.get("local_root_count")))
        by_root[root_count].append(row)

    selected: list[dict[str, Any]] = []
    selected_hashes: set[str] = set()
    requested = Counter()
    for root_count, quota in quotas.items():
        requested[int(root_count)] = int(quota)
        for row in _round_robin(by_root.get(int(root_count), ()))[: int(quota)]:
            selected.append(row)
            selected_hashes.add(str(row["candidate_hash"]))
            if len(selected) == int(limit):
                break
        if len(selected) == int(limit):
            break

    if len(selected) < int(limit):
        remainder = [
            row for row in unique.values()
            if str(row["candidate_hash"]) not in selected_hashes
        ]
        for row in _round_robin(remainder)[: int(limit) - len(selected)]:
            selected.append(row)
            selected_hashes.add(str(row["candidate_hash"]))

    selected_counts = Counter(
        int(row.get("target_r", row.get("local_root_count"))) for row in selected
    )
    return selected, {
        "input_rows": sum(len(values) for values in by_root.values()),
        "unique_candidates": len(unique),
        "excluded_candidates": len(excluded),
        "selected": len(selected),
        "requested_quotas": {str(k): v for k, v in requested.items()},
        "selected_signatures": {
            str(k): v for k, v in sorted(selected_counts.items())
        },
        "selected_strata": len({_stratum(row) for row in selected}),
    }


def _parse_quotas(raw: str) -> dict[int, int]:
    quotas: dict[int, int] = {}
    for part in raw.split(","):
        root_count, count = part.split(":", 1)
        quotas[int(root_count)] = int(count)
    return quotas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=Path,
        help="JSONL portfolios whose candidate hashes must not be selected",
    )
    parser.add_argument(
        "--quotas",
        default=",".join(f"{root}:{count}" for root, count in DEFAULT_QUOTAS.items()),
    )
    args = parser.parse_args()
    rows = []
    for source in args.sources:
        rows.extend(
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    exclude_hashes = set()
    for source in args.exclude:
        exclude_hashes.update(
            str(json.loads(line).get("candidate_hash") or "")
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    selected, report = select_signature_portfolio(
        rows,
        quotas=_parse_quotas(args.quotas),
        limit=args.limit,
        exclude_hashes=exclude_hashes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in selected),
        encoding="utf-8",
    )
    report["output"] = str(args.output)
    report_path = args.output.with_suffix(args.output.suffix + ".report.json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

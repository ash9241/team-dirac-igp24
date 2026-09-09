#!/usr/bin/env python3
"""Export unseen source fields whose observed real profile hits an open pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import load_owned_pairs


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    payload = "".join(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_sources(
    catalog_path: Path,
    orbit_map_path: Path,
    candidate_paths: list[Path],
    output_path: Path,
    *,
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    catalog = _load_jsonl(catalog_path)
    by_hash = {str(row["candidate_hash"]): row for row in catalog}
    target_by_source = {
        int(row["source_t"]): int(row["unambiguous_target_t"])
        for row in _load_jsonl(orbit_map_path)
        if row.get("unambiguous_target_t") is not None
        and int(row["unambiguous_target_t"]) != int(row["source_t"])
    }

    # A real-root profile is learned only from exact, completed outputs.  For
    # an unambiguous pair action, (source group, source roots) determines the
    # possible target root signatures.  Alternate verified source fields with
    # that same profile can then be aimed at pairs that remain open.
    profiles: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    completed_combos: set[tuple[str, int]] = set()
    observation_rows = 0
    for path in candidate_paths:
        for row in _load_jsonl(path):
            if not bool(row.get("exact_compatibility_proven")):
                continue
            parameters = row.get("parameters") or {}
            source_hash = str(parameters.get("source_candidate_hash") or "")
            source = by_hash.get(source_hash)
            if source is None:
                continue
            source_t = int(source["source_t"])
            target_t = int(row["target_t"])
            if target_by_source.get(source_t) != target_t:
                continue
            source_r = int(source["source_r"])
            profiles[(source_t, source_r, target_t)].add(int(row["target_r"]))
            completed_combos.add((source_hash, target_t))
            observation_rows += 1

    with Ledger(db_path) as ledger:
        live = ledger.latest_targets()
        owned = load_owned_pairs() | ledger.owned_pairs()

    selected: list[tuple[float, dict[str, Any], list[int]]] = []
    for source in catalog:
        source_hash = str(source["candidate_hash"])
        source_t = int(source["source_t"])
        target_t = target_by_source.get(source_t)
        if target_t is None or (source_hash, target_t) in completed_combos:
            continue
        predicted = sorted(profiles.get((source_t, int(source["source_r"]), target_t), ()))
        opportunity = 0.0
        open_roots = []
        for target_r in predicted:
            pair = target_t, target_r
            state = live.get(pair)
            if state is None or pair in owned or bool(state["baseline"]):
                continue
            open_roots.append(target_r)
            opportunity += 2.0 ** (-int(state["team_count"]))
        if opportunity <= 0.0:
            continue
        row = dict(source)
        row["root_conditioned_target_t"] = target_t
        row["root_conditioned_target_rs"] = open_roots
        row["root_conditioned_opportunity"] = opportunity
        selected.append((opportunity, row, open_roots))

    selected.sort(key=lambda item: (
        -item[0],
        int(item[1]["source_t"]),
        int(item[1]["source_r"]),
        int(item[1].get("disc_abs") or 0),
        str(item[1]["candidate_hash"]),
    ))
    rows = [row for _, row, _ in selected]
    sha256 = _atomic_jsonl(output_path, rows)
    report = {
        "catalog_rows": len(catalog),
        "candidate_sources": [str(path) for path in candidate_paths],
        "completed_source_target_combos": len(completed_combos),
        "empirical_profiles": len(profiles),
        "observation_rows": observation_rows,
        "output": str(output_path),
        "output_rows": len(rows),
        "output_source_groups": len({int(row["source_t"]) for row in rows}),
        "predicted_open_pairs": len({
            (int(row["root_conditioned_target_t"]), int(target_r))
            for row in rows
            for target_r in row["root_conditioned_target_rs"]
        }),
        "predicted_opportunity_ceiling": sum({
            (int(row["root_conditioned_target_t"]), int(target_r)): float(row["root_conditioned_opportunity"])
            for row in rows
            for target_r in row["root_conditioned_target_rs"]
        }.values()),
        "sha256": sha256,
    }
    report_path = output_path.with_suffix(output_path.suffix + ".report.json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("orbit_map", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    report = export_sources(
        args.catalog,
        args.orbit_map,
        args.candidates,
        args.output,
        db_path=args.db,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

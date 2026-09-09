#!/usr/bin/env python3
"""Select unit twists aimed at live roots of server-calibrated groups.

The generator's ``target_t`` is only a wreath overgroup.  After a validity
pilot, ``source_calibrated_target_t`` records the group actually reached by a
source Kummer class.  This selector uses that server label as a retention
hypothesis and spends probes only on currently unowned gold/raid signatures
of the calibrated group.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.submit_exploration_batch import validate_validity_candidate


PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_PROGRESS = PROJECT / "daemon" / "data" / "all_progress.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_live_targets(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    labels = json.loads(path.read_text(encoding="utf-8"))
    live: dict[tuple[int, int], dict[str, Any]] = {}
    for label in labels:
        target_t = int(label.get("t") or str(label["label"]).replace("24T", ""))
        for signature in label.get("signatures", []):
            team_count = int(signature.get("teamCount", 0))
            if bool(signature.get("baseline")) or team_count > 1:
                continue
            target_r = int(signature["r"])
            live[(target_t, target_r)] = {
                "team_count": team_count,
                "immediate_value": 2.0 ** (-team_count),
                "minimum_disc_abs": signature.get("minimumDiscAbs"),
            }
    return live


def select_calibrated_closure(
    sources: Sequence[Path],
    output: Path,
    *,
    progress_path: Path = DEFAULT_PROGRESS,
    db_path: str | Path = DEFAULT_DB,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be in [1, 1000]")
    live = load_live_targets(progress_path)
    counts: Counter[str] = Counter()
    eligible: dict[str, dict[str, Any]] = {}
    with Ledger(db_path) as ledger:
        owned = ledger.owned_pairs()
        for source in sources:
            counts["source_files"] += 1
            for row in _load_jsonl(source):
                counts["input_rows"] += 1
                record = validate_validity_candidate(row)
                parameters = row.get("parameters") or {}
                calibrated_t = int(parameters.get("source_calibrated_target_t") or 0)
                if calibrated_t <= 0:
                    counts["rejected_uncalibrated_source"] += 1
                    continue
                pair = calibrated_t, int(record.local_root_count)
                target = live.get(pair)
                if target is None:
                    counts["rejected_not_live_gold_or_raid"] += 1
                    continue
                if pair in owned:
                    counts["rejected_owned_pair"] += 1
                    continue
                if ledger.candidate_committed(record.candidate_hash):
                    counts["rejected_committed_candidate"] += 1
                    continue
                enriched = dict(row)
                enriched["calibrated_retention_target_t"] = calibrated_t
                enriched["calibrated_retention_target_r"] = pair[1]
                enriched["calibrated_retention_team_count"] = int(target["team_count"])
                enriched["calibrated_retention_value"] = float(target["immediate_value"])
                previous = eligible.get(record.candidate_hash)
                if previous is None or int(row["field_disc_abs"]) < int(previous["field_disc_abs"]):
                    eligible[record.candidate_hash] = enriched

    by_pair: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible.values():
        pair = (
            int(row["calibrated_retention_target_t"]),
            int(row["calibrated_retention_target_r"]),
        )
        by_pair[pair].append(row)
    for rows in by_pair.values():
        rows.sort(key=lambda row: (
            int(row["field_disc_abs"]),
            int((row.get("parameters") or {}).get("unit_mask", 0)),
            -int((row.get("parameters") or {}).get("unit_sign", 1)),
            str(row["candidate_hash"]),
        ))
    pairs = sorted(
        by_pair,
        key=lambda pair: (
            -float(live[pair]["immediate_value"]),
            pair[0],
            pair[1],
        ),
    )
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < int(limit):
        added = False
        for pair in pairs:
            rows = by_pair[pair]
            if depth < len(rows):
                selected.append(rows[depth])
                added = True
                if len(selected) == int(limit):
                    break
        if not added:
            break
        depth += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        for row in selected:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(output)
    return {
        "sources": [str(path) for path in sources],
        "output": str(output),
        "counts": dict(sorted(counts.items())),
        "eligible_candidates": len(eligible),
        "eligible_pairs": len(by_pair),
        "selected_candidates": len(selected),
        "selected_pairs": len({
            (
                int(row["calibrated_retention_target_t"]),
                int(row["calibrated_retention_target_r"]),
            )
            for row in selected
        }),
        "retention_hypothesis_ceiling": sum(
            float(row["calibrated_retention_value"]) for row in selected
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(select_calibrated_closure(
        args.sources,
        args.output,
        progress_path=args.progress,
        db_path=args.db,
        limit=args.limit,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Harvest live unit-sign siblings from server-calibrated pilots.

Rows are grouped by source field, subfield structure, and unit mask while the
unit sign is ignored.  A server-verified member calibrates the group; an
unsubmitted opposite-sign member is retained only when its calibrated
``(24Tt, r)`` pair is live, non-baseline, and not already owned locally.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

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


def _pair_key(row: dict[str, Any]) -> tuple[Any, ...]:
    parameters = row.get("parameters") or {}
    return (
        str(row.get("source_field_label") or ""),
        int(row["base_t"]),
        int(parameters.get("subfield_degree", 0)),
        int(parameters.get("subfield_index", 0)),
        int(parameters.get("subfield_shift", 0)),
        int(parameters.get("unit_mask", 0)),
    )


def _load_live(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
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
                "value": 2.0 ** (-team_count),
            }
    return live


def select_exact_unit_sign_siblings(
    sources: Sequence[Path],
    output: Path,
    *,
    progress_path: Path = DEFAULT_PROGRESS,
    db_path: str | Path = DEFAULT_DB,
    alternatives_per_pair: int = 2,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be in [1, 1000]")
    if int(alternatives_per_pair) <= 0:
        raise ValueError("alternatives-per-pair must be positive")

    live = _load_live(progress_path)
    counts: Counter[str] = Counter()
    rows_by_hash: dict[str, dict[str, Any]] = {}
    for source in sources:
        counts["source_files"] += 1
        for row in _load_jsonl(source):
            counts["input_rows"] += 1
            record = validate_validity_candidate(row)
            counts["valid_rows"] += 1
            previous = rows_by_hash.get(record.candidate_hash)
            if previous is None or int(row["field_disc_abs"]) < int(
                previous["field_disc_abs"]
            ):
                rows_by_hash[record.candidate_hash] = row

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows_by_hash.values():
        groups[_pair_key(row)].append(row)

    by_pair: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    with Ledger(db_path) as ledger:
        owned = ledger.owned_pairs()
        verified = {
            str(row["candidate_hash"]): int(row["verified_t"])
            for row in ledger.connection.execute(
                """SELECT candidate_hash, verified_t FROM verification
                   WHERE accepted=1 AND verified_t IS NOT NULL"""
            )
        }
        for rows in groups.values():
            calibrated = {
                verified[str(row["candidate_hash"])]
                for row in rows
                if str(row["candidate_hash"]) in verified
            }
            if not calibrated:
                counts["rejected_uncalibrated_group"] += len(rows)
                continue
            if len(calibrated) != 1:
                counts["rejected_conflicting_group_labels"] += len(rows)
                continue
            target_t = next(iter(calibrated))
            verified_signs = {
                int((row.get("parameters") or {}).get("unit_sign", 1))
                for row in rows
                if str(row["candidate_hash"]) in verified
            }
            for row in rows:
                record = validate_validity_candidate(row)
                if record.candidate_hash in verified:
                    continue
                if ledger.candidate_committed(record.candidate_hash):
                    counts["rejected_committed_candidate"] += 1
                    continue
                sign = int((row.get("parameters") or {}).get("unit_sign", 1))
                if sign in verified_signs:
                    counts["rejected_not_opposite_sign"] += 1
                    continue
                pair = (target_t, int(record.local_root_count))
                target = live.get(pair)
                if target is None:
                    counts["rejected_not_live_gold_or_raid"] += 1
                    continue
                if pair in owned:
                    counts["rejected_owned_pair"] += 1
                    continue
                enriched = dict(row)
                enriched["sign_sibling_target_t"] = target_t
                enriched["sign_sibling_target_r"] = pair[1]
                enriched["sign_sibling_team_count"] = int(target["team_count"])
                enriched["sign_sibling_value"] = float(target["value"])
                enriched["sign_sibling_empirical_precision"] = 0.9636
                by_pair[pair].append(enriched)

    selected: list[dict[str, Any]] = []
    for pair in sorted(
        by_pair,
        key=lambda value: (-float(live[value]["value"]), value[0], value[1]),
    ):
        rows = sorted(
            by_pair[pair],
            key=lambda row: (
                int(row["field_disc_abs"]),
                str(row["candidate_hash"]),
            ),
        )
        selected.extend(rows[: int(alternatives_per_pair)])
        if len(selected) >= int(limit):
            selected = selected[: int(limit)]
            break

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        for row in selected:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(output)

    selected_pairs = {
        (int(row["sign_sibling_target_t"]), int(row["sign_sibling_target_r"]))
        for row in selected
    }
    return {
        "sources": [str(path) for path in sources],
        "output": str(output),
        "counts": dict(sorted(counts.items())),
        "eligible_pairs": len(by_pair),
        "eligible_candidates": sum(len(rows) for rows in by_pair.values()),
        "selected_pairs": len(selected_pairs),
        "selected_candidates": len(selected),
        "nominal_pair_opportunity": sum(float(live[pair]["value"]) for pair in selected_pairs),
        "precision_adjusted_opportunity": sum(
            float(live[pair]["value"]) * 0.9636 for pair in selected_pairs
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--alternatives-per-pair", type=int, default=2)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(select_exact_unit_sign_siblings(
        args.sources,
        args.output,
        progress_path=args.progress,
        db_path=args.db,
        alternatives_per_pair=args.alternatives_per_pair,
        limit=args.limit,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

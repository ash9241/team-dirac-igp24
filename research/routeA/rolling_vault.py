#!/usr/bin/env python3
"""Build a short-horizon, diversity-aware vault from candidate shards."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import PortfolioScheduler, load_candidates, load_owned_pairs, targets_from_ledger


def build_vault(
    shard_paths: list[Path],
    ledger: Ledger,
    *,
    limit: int,
    horizon_hours: float = 4.0,
) -> list[dict]:
    candidates = []
    for path in shard_paths:
        candidates.extend(load_candidates(path))
    targets = targets_from_ledger(ledger.latest_targets())
    if not targets:
        raise RuntimeError("no target snapshot available")
    owned = load_owned_pairs() | ledger.owned_pairs()
    available = [candidate for candidate in candidates if not ledger.candidate_committed(candidate.candidate_hash)]
    selected = PortfolioScheduler(max_per_target=2).select(available, targets, limit, owned)
    selected_at = datetime.now(timezone.utc)
    expires_at = selected_at + timedelta(hours=horizon_hours)
    rows = []
    for ranked in selected:
        row = dict(ranked.candidate.metadata)
        row.update(
            {
                "coefficients": ranked.candidate.coefficients,
                "candidate_hash": ranked.candidate.candidate_hash,
                "target_t": ranked.candidate.target_t,
                "target_r": ranked.candidate.target_r,
                "label_probability": ranked.candidate.label_probability,
                "recipe_family": ranked.candidate.recipe_family,
                "recipe_lineage": ranked.candidate.recipe_lineage,
                "local_root_count": ranked.candidate.local_root_count,
                "expected_value": ranked.expected_value,
                "target_snapshot_id": ranked.target.snapshot_id,
                "selected_at": selected_at.isoformat(timespec="seconds"),
                "expires_at": expires_at.isoformat(timespec="seconds"),
            }
        )
        rows.append(row)
    return rows


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shards", nargs="+")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "data" / "rolling_vault.jsonl"))
    parser.add_argument("--limit", type=int, default=250000)
    parser.add_argument("--horizon-hours", type=float, default=4.0)
    args = parser.parse_args()
    if not 0 < args.horizon_hours <= 4:
        raise SystemExit("vault horizon must be in (0, 4] hours")
    with Ledger(args.db) as ledger:
        rows = build_vault(
            [Path(path) for path in args.shards],
            ledger,
            limit=args.limit,
            horizon_hours=args.horizon_hours,
        )
    atomic_jsonl(Path(args.output), rows)
    print(f"vaulted {len(rows)} candidates for at most {args.horizon_hours:g} hours")


if __name__ == "__main__":
    main()

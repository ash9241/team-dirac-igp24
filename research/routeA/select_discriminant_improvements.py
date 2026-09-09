#!/usr/bin/env python3
"""Select exact lower-discriminant replacements for already-owned IGP24 pairs.

Submitting a second field for an owned ``(24Tt, r)`` pair can improve the
official logarithmic discriminant ratio without increasing the holder count.
This selector is local-only: it streams exact candidate JSONL files, retains
the best uncommitted replacement per pair, and writes a score-ranked harvest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, candidate_hash, canonical_coefficients
from routeA.scheduler import discriminant_score_ratio
from routeA.select_exact_harvest import LiveTarget, _candidate_disc_abs, _positive_int


def load_discriminant_state(
    db_path: str | Path,
) -> tuple[dict[tuple[int, int], LiveTarget], dict[tuple[int, int], int], set[str], dict[str, Any]]:
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """SELECT snapshot_id, captured_at FROM target_snapshot
               ORDER BY captured_at DESC, snapshot_id DESC LIMIT 1"""
        ).fetchone()
        if latest is None:
            raise ValueError("control database has no target snapshot")
        targets = {
            (int(row["t"]), int(row["r"])): LiveTarget(
                t=int(row["t"]),
                r=int(row["r"]),
                team_count=int(row["team_count"]),
                baseline=bool(row["baseline"]),
                minimum_disc_abs=_positive_int(row["minimum_disc_abs"]),
                snapshot_id=str(row["snapshot_id"]),
                captured_at=str(row["captured_at"]),
            )
            for row in connection.execute(
                "SELECT * FROM target_snapshot WHERE snapshot_id=?",
                (latest["snapshot_id"],),
            )
        }
        best: dict[tuple[int, int], int] = {}

        def retain(t: Any, r: Any, value: Any) -> None:
            disc = _positive_int(value)
            if t is None or r is None or disc is None:
                return
            pair = int(t), int(r)
            previous = best.get(pair)
            if previous is None or disc < previous:
                best[pair] = disc

        for row in connection.execute(
            """SELECT verified_t, verified_r, COALESCE(nfdisc, discriminant) AS disc
               FROM verification WHERE accepted=1"""
        ):
            retain(row["verified_t"], row["verified_r"], row["disc"])
        for row in connection.execute(
            """SELECT verified_t, verified_r, field_disc_abs AS disc
               FROM server_verification WHERE accepted=1"""
        ):
            retain(row["verified_t"], row["verified_r"], row["disc"])
        committed = {
            str(row["candidate_hash"])
            for row in connection.execute(
                """SELECT DISTINCT si.candidate_hash
                   FROM submission_item si JOIN submission s USING(batch_uuid)
                   WHERE s.dry_run=0"""
            )
        }
    finally:
        connection.close()
    metadata = {
        "snapshot_id": str(latest["snapshot_id"]),
        "snapshot_captured_at": str(latest["captured_at"]),
        "snapshot_pairs": len(targets),
        "owned_pairs_with_discriminants": len(best),
        "committed_candidate_hashes": len(committed),
    }
    return targets, best, committed, metadata


def select_improvement_rows(
    rows: Iterable[Mapping[str, Any]],
    targets: Mapping[tuple[int, int], LiveTarget],
    current_discs: Mapping[tuple[int, int], int],
    committed_hashes: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    committed_hashes = committed_hashes or set()
    counts: Counter[str] = Counter()
    best_by_pair: dict[tuple[int, int], tuple[int, str, dict[str, Any]]] = {}
    seen_hashes: dict[str, tuple[tuple[int, int], int]] = {}
    for raw in rows:
        counts["input_rows"] += 1
        if raw.get("exact_compatibility_proven") is not True:
            counts["rejected_not_exact"] += 1
            continue
        if raw.get("submission_ready") is not True:
            counts["rejected_not_submission_ready"] += 1
            continue
        try:
            pair = int(raw["target_t"]), int(raw["target_r"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("exact-ready candidate has no valid target pair") from exc
        target = targets.get(pair)
        current = current_discs.get(pair)
        if target is None or target.baseline or current is None:
            counts["rejected_not_scoreable_owned"] += 1
            continue
        if raw.get("local_irreducible") is not True:
            counts["rejected_not_locally_irreducible"] += 1
            continue
        try:
            local_roots = int(raw["local_root_count"])
        except (KeyError, TypeError, ValueError):
            counts["rejected_missing_local_root_count"] += 1
            continue
        if local_roots != pair[1]:
            counts["rejected_root_mismatch"] += 1
            continue
        candidate_disc = _candidate_disc_abs(raw)
        if candidate_disc is None:
            counts["rejected_missing_discriminant"] += 1
            continue
        if candidate_disc >= current:
            counts["rejected_not_lower_discriminant"] += 1
            continue
        coefficients = canonical_coefficients(raw.get("coefficients"))
        key = candidate_hash(coefficients)
        supplied = str(raw.get("candidate_hash") or "")
        if supplied and supplied != key:
            raise ValueError(f"candidate hash mismatch for exact-ready row {supplied}")
        if key in committed_hashes:
            counts["rejected_committed_candidate"] += 1
            continue
        previous_hash = seen_hashes.get(key)
        if previous_hash is not None:
            if previous_hash != (pair, candidate_disc):
                raise ValueError(f"conflicting exact candidate row for {key}")
            counts["duplicate_candidate_rows"] += 1
            continue
        seen_hashes[key] = pair, candidate_disc
        normalized = dict(raw)
        normalized["coefficients"] = coefficients
        normalized["candidate_hash"] = key
        rank = candidate_disc, key
        previous = best_by_pair.get(pair)
        if previous is None or rank < (previous[0], previous[1]):
            if previous is not None:
                counts["superseded_by_lower_discriminant"] += 1
            best_by_pair[pair] = candidate_disc, key, normalized
        else:
            counts["superseded_by_lower_discriminant"] += 1

    selected: list[dict[str, Any]] = []
    for pair, (candidate_disc, _, raw) in best_by_pair.items():
        target = targets[pair]
        if target.minimum_disc_abs is None:
            counts["rejected_missing_reference_discriminant"] += 1
            continue
        current_disc = int(current_discs[pair])
        ceiling = 2.0 ** (1 - target.team_count)
        current_ratio = discriminant_score_ratio(target.minimum_disc_abs, current_disc)
        candidate_ratio = discriminant_score_ratio(target.minimum_disc_abs, candidate_disc)
        gain = ceiling * (candidate_ratio - current_ratio)
        if gain <= 0:
            counts["rejected_no_forecast_gain"] += 1
            continue
        row = dict(raw)
        row["discriminant_improvement"] = {
            "snapshot_id": target.snapshot_id,
            "snapshot_captured_at": target.captured_at,
            "team_count": target.team_count,
            "minimum_disc_abs": target.minimum_disc_abs,
            "current_disc_abs": current_disc,
            "candidate_disc_abs": candidate_disc,
            "score_ceiling": ceiling,
            "current_ratio": current_ratio,
            "candidate_ratio": candidate_ratio,
            "forecast_score_gain": gain,
        }
        selected.append(row)
    selected.sort(key=lambda row: (
        -float(row["discriminant_improvement"]["forecast_score_gain"]),
        int(row["target_t"]),
        int(row["target_r"]),
        str(row["candidate_hash"]),
    ))
    counts["selected_pairs"] = len(selected)
    return selected, dict(sorted(counts.items()))


def select_discriminant_improvements(
    candidate_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
) -> dict[str, Any]:
    sources = sorted({Path(path).resolve() for path in candidate_paths})
    if not sources:
        raise ValueError("at least one candidate JSONL is required")
    output = Path(output_path).resolve()
    if output in sources:
        raise ValueError("output must not overwrite an input shard")
    targets, current, committed, state = load_discriminant_state(db_path)
    load_counts: Counter[str] = Counter(source_files=len(sources))

    def stream() -> Iterable[dict[str, Any]]:
        for path in sources:
            if not path.is_file():
                raise FileNotFoundError(path)
            with path.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    load_counts["source_rows"] += 1
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"invalid JSON in {path}:{number}") from exc
                    if not isinstance(value, dict):
                        raise ValueError(f"candidate row is not an object in {path}:{number}")
                    row = dict(value)
                    row.setdefault("harvest_source_path", str(path))
                    row.setdefault("harvest_source_line", number)
                    yield row

    selected, selection_counts = select_improvement_rows(
        stream(), targets, current, committed
    )
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in selected
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(output, payload)
    summary = {
        "output": str(output),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        **state,
        "counts": {**dict(sorted(load_counts.items())), **selection_counts},
        "forecast": {
            "candidate_count": len(selected),
            "pair_count": len(selected),
            "score_gain": sum(
                float(row["discriminant_improvement"]["forecast_score_gain"])
                for row in selected
            ),
        },
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
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    summary = select_discriminant_improvements(
        args.sources, args.output, db_path=args.db
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

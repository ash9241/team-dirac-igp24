#!/usr/bin/env python3
"""Select one ownership-safe exact candidate per live IGP24 target pair.

This is a local harvest step only.  It never records or submits a batch.  The
current target snapshot and accepted server history in ``control.sqlite3`` are
treated as authoritative, and candidate eligibility is deliberately
fail-closed: both exact compatibility and submission readiness must be
explicitly true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, candidate_hash, canonical_coefficients
from routeA.scheduler import discriminant_score_ratio


@dataclass(frozen=True)
class LiveTarget:
    t: int
    r: int
    team_count: int
    baseline: bool
    minimum_disc_abs: int | None
    snapshot_id: str
    captured_at: str

    @property
    def immediate_value(self) -> float:
        return 2.0 ** (-self.team_count)


def load_authoritative_state(
    db_path: str | Path,
) -> tuple[
    dict[tuple[int, int], LiveTarget],
    set[tuple[int, int]],
    set[str],
    dict[str, Any],
]:
    """Read the newest complete snapshot and accepted ownership in read-only mode."""

    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        latest = connection.execute(
            """SELECT snapshot_id, captured_at
               FROM target_snapshot
               ORDER BY captured_at DESC, snapshot_id DESC LIMIT 1"""
        ).fetchone()
        if latest is None:
            raise ValueError("control database has no target snapshot")
        target_rows = list(connection.execute(
            "SELECT * FROM target_snapshot WHERE snapshot_id=?",
            (latest["snapshot_id"],),
        ))
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
            for row in target_rows
        }
        owned = {
            (int(row["verified_t"]), int(row["verified_r"]))
            for row in connection.execute(
                """SELECT DISTINCT verified_t, verified_r FROM verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL
                   UNION
                   SELECT DISTINCT verified_t, verified_r FROM server_verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL"""
                )
        }
        committed_hashes = {
            str(row["candidate_hash"])
            for row in connection.execute(
                """SELECT DISTINCT si.candidate_hash
                   FROM submission_item si
                   JOIN submission s USING(batch_uuid)
                   WHERE s.dry_run=0"""
            )
        }
    finally:
        connection.close()
    metadata = {
        "snapshot_id": str(latest["snapshot_id"]),
        "snapshot_captured_at": str(latest["captured_at"]),
        "snapshot_pairs": len(targets),
        "authoritative_owned_pairs": len(owned),
        "committed_candidate_hashes": len(committed_hashes),
    }
    return targets, owned, committed_hashes, metadata


def load_candidate_rows(paths: Sequence[str | Path]) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        counts["source_files"] += 1
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                counts["input_rows"] += 1
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path}:{number}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"candidate row is not an object in {path}:{number}")
                enriched = dict(value)
                enriched.setdefault("harvest_source_path", str(path))
                enriched.setdefault("harvest_source_line", number)
                rows.append(enriched)
    return rows, counts


def select_exact_rows(
    rows: Iterable[Mapping[str, Any]],
    targets: Mapping[tuple[int, int], LiveTarget],
    owned_pairs: set[tuple[int, int]],
    committed_hashes: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter exact rows and retain the lowest discriminant for each pair."""

    counts: Counter[str] = Counter()
    committed_hashes = committed_hashes or set()
    eligible_by_hash: dict[str, dict[str, Any]] = {}
    pair_by_hash: dict[str, tuple[int, int]] = {}
    disc_by_hash: dict[str, int] = {}

    for raw in rows:
        counts["input_rows"] += 1
        if raw.get("exact_compatibility_proven") is not True:
            counts["rejected_not_exact"] += 1
            continue
        if raw.get("submission_ready") is not True:
            counts["rejected_not_submission_ready"] += 1
            continue
        try:
            target_t = int(raw["target_t"])
            target_r = int(raw["target_r"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("exact-ready candidate has no valid target pair") from exc
        pair = target_t, target_r
        target = targets.get(pair)
        if target is None:
            counts["rejected_non_live"] += 1
            continue
        if target.baseline:
            counts["rejected_baseline"] += 1
            continue
        if pair in owned_pairs:
            counts["rejected_owned"] += 1
            continue
        if raw.get("local_irreducible") is not True:
            counts["rejected_not_locally_irreducible"] += 1
            continue
        try:
            local_roots = int(raw["local_root_count"])
        except (KeyError, TypeError, ValueError):
            counts["rejected_missing_local_root_count"] += 1
            continue
        if local_roots != target_r:
            counts["rejected_root_mismatch"] += 1
            continue
        coefficients = canonical_coefficients(raw.get("coefficients"))
        key = candidate_hash(coefficients)
        supplied_hash = str(raw.get("candidate_hash") or "")
        if supplied_hash and supplied_hash != key:
            raise ValueError(f"candidate hash mismatch for exact-ready row {supplied_hash}")
        if key in committed_hashes:
            counts["rejected_committed_candidate"] += 1
            continue
        disc = _candidate_disc_abs(raw)
        if disc is None:
            counts["rejected_missing_discriminant"] += 1
            continue

        previous_pair = pair_by_hash.get(key)
        if previous_pair is not None and previous_pair != pair:
            raise ValueError(
                f"exact candidate {key} is assigned to conflicting pairs "
                f"{previous_pair} and {pair}"
            )
        previous_disc = disc_by_hash.get(key)
        if previous_disc is not None:
            counts["duplicate_candidate_rows"] += 1
            if previous_disc != disc:
                raise ValueError(
                    f"exact candidate {key} has conflicting discriminants "
                    f"{previous_disc} and {disc}"
                )
            continue
        normalized = dict(raw)
        normalized["coefficients"] = coefficients
        normalized["candidate_hash"] = key
        eligible_by_hash[key] = normalized
        pair_by_hash[key] = pair
        disc_by_hash[key] = disc
        counts["eligible_unique_candidates"] += 1

    best_by_pair: dict[tuple[int, int], tuple[int, str, dict[str, Any]]] = {}
    for key, row in eligible_by_hash.items():
        pair = pair_by_hash[key]
        rank = (disc_by_hash[key], key)
        previous = best_by_pair.get(pair)
        if previous is None or rank < (previous[0], previous[1]):
            if previous is not None:
                counts["superseded_by_lower_discriminant"] += 1
            best_by_pair[pair] = (rank[0], rank[1], row)
        else:
            counts["superseded_by_lower_discriminant"] += 1

    selected = []
    for pair, (disc, _, row) in sorted(best_by_pair.items()):
        target = targets[pair]
        ratio = (
            1.0
            if target.team_count == 0
            else (
                discriminant_score_ratio(target.minimum_disc_abs, disc)
                if target.minimum_disc_abs is not None
                else 0.0
            )
        )
        enriched = dict(row)
        enriched["harvest_selection"] = {
            "snapshot_id": target.snapshot_id,
            "snapshot_captured_at": target.captured_at,
            "team_count": target.team_count,
            "minimum_disc_abs": target.minimum_disc_abs,
            "candidate_disc_abs": disc,
            "immediate_value": target.immediate_value,
            "discriminant_ratio": ratio,
            "forecast_score": target.immediate_value * ratio,
        }
        selected.append(enriched)
    counts["selected_pairs"] = len(selected)
    return selected, dict(sorted(counts.items()))


def build_forecast(
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    team_counts: Counter[int] = Counter()
    immediate = 0.0
    adjusted = 0.0
    known_discriminant = 0
    target_ts: set[int] = set()
    for row in selected:
        selection = row["harvest_selection"]
        team_counts[int(selection["team_count"])] += 1
        immediate += float(selection["immediate_value"])
        adjusted += float(selection["forecast_score"])
        if selection.get("minimum_disc_abs") is not None or int(selection["team_count"]) == 0:
            known_discriminant += 1
        target_ts.add(int(row["target_t"]))
    return {
        "candidate_count": len(selected),
        "pair_count": len(selected),
        "target_group_count": len(target_ts),
        "team_count_histogram": {
            str(key): value for key, value in sorted(team_counts.items())
        },
        "solo_pairs": team_counts.get(0, 0),
        "raid_pairs": team_counts.get(1, 0),
        "immediate_score_ceiling": immediate,
        "discriminant_adjusted_score": adjusted,
        "pairs_with_scoreable_discriminant_forecast": known_discriminant,
    }


def select_exact_harvest(
    candidate_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
) -> dict[str, Any]:
    output = Path(output_path).resolve()
    sources = [Path(path).resolve() for path in candidate_paths]
    if not sources:
        raise ValueError("at least one candidate JSONL is required")
    if output in sources:
        raise ValueError("harvest output must not overwrite an input shard")
    rows, load_counts = load_candidate_rows(sources)
    targets, owned, committed, state = load_authoritative_state(db_path)
    selected, selection_counts = select_exact_rows(
        rows, targets, owned, committed
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
        "source_paths_sha256": hashlib.sha256(
            ("\n".join(str(path) for path in sources) + "\n").encode("utf-8")
        ).hexdigest(),
        **state,
        "counts": {
            **dict(sorted(load_counts.items())),
            **{
                key: value
                for key, value in selection_counts.items()
                if key != "input_rows"
            },
        },
        "forecast": build_forecast(selected),
    }
    report = output.with_suffix(output.suffix + ".report.json")
    _atomic_text(report, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def _candidate_disc_abs(row: Mapping[str, Any]) -> int | None:
    for key in (
        "field_disc_abs",
        "nfdisc_abs",
        "estimated_nfdisc_abs",
        "estimated_discriminant_abs",
        "predicted_nfdisc_abs",
        "predicted_discriminant_abs",
    ):
        value = _positive_int(row.get(key))
        if value is not None:
            return value
    features = row.get("discriminant_features")
    if isinstance(features, Mapping):
        for key in ("field_disc_abs", "nfdisc_abs", "estimated_nfdisc_abs"):
            value = _positive_int(features.get(key))
            if value is not None:
                return value
    return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = abs(int(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 1 else None


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
    summary = select_exact_harvest(
        args.sources,
        args.output,
        db_path=args.db,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

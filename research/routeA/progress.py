#!/usr/bin/env python3
"""Complete, versioned competition-progress snapshots."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from routeA.api_client import APIClient
from routeA.ledger import DEFAULT_DB, Ledger, utc_now


PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = PROJECT / "daemon" / "data"
DEFAULT_ARCHIVE = Path(__file__).resolve().parent / "data" / "progress_snapshots"


@dataclass(frozen=True)
class ProgressResult:
    snapshot_id: str
    captured_at: str
    label_count: int
    pair_count: int
    gold_count: int
    raid_count: int
    baseline_count: int


def load_baseline_pairs(path: str | os.PathLike[str] | None = None) -> set[tuple[int, int]]:
    source = Path(path) if path else DEFAULT_DATA / "baseline_best.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    pairs = set()
    for key in raw:
        t, r = key.split(",", 1)
        pairs.add((int(t), int(r)))
    return pairs


def normalize_progress(
    labels: Iterable[Mapping[str, Any]],
    baseline: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_labels: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for source in labels:
        lab = dict(source)
        t = int(lab.get("t") or str(lab["label"]).replace("24T", ""))
        signatures = []
        for raw_sig in lab.get("signatures", []):
            sig = dict(raw_sig)
            r = int(sig["r"])
            k = int(sig.get("teamCount", 0))
            discovered = bool(sig.get("discovered", k > 0))
            is_baseline = (t, r) in baseline
            minimum_disc_abs = _positive_decimal(sig.get("minimumDiscAbs"))
            sig["r"] = r
            sig["teamCount"] = k
            sig["discovered"] = discovered
            sig["baseline"] = is_baseline
            signatures.append(sig)
            pairs.append(
                {
                    "t": t,
                    "r": r,
                    "team_count": k,
                    "discovered": discovered,
                    "baseline": is_baseline,
                    "immediate_value": 2.0 ** (-k),
                    "minimum_disc_abs": minimum_disc_abs,
                    "holders": sig.get("holders"),
                    "raw": sig,
                }
            )
        lab["t"] = t
        lab["signatures"] = signatures
        normalized_labels.append(lab)
    return normalized_labels, pairs


def refresh_progress(
    client: APIClient,
    ledger: Ledger,
    *,
    data_dir: str | os.PathLike[str] = DEFAULT_DATA,
    archive_dir: str | os.PathLike[str] = DEFAULT_ARCHIVE,
    baseline_path: str | os.PathLike[str] | None = None,
    archive_keep: int = 1008,
) -> ProgressResult:
    raw_labels = client.fetch_all_progress()
    baseline = load_baseline_pairs(baseline_path)
    labels, pairs = normalize_progress(raw_labels, baseline)
    snapshot_id = str(uuid.uuid4())
    captured_at = utc_now()
    ledger.record_target_snapshot(pairs, snapshot_id=snapshot_id, captured_at=captured_at)

    data_dir = Path(data_dir)
    archive_dir = Path(archive_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    gold = [[p["t"], p["r"]] for p in pairs if not p["baseline"] and p["team_count"] == 0]
    raids = [[p["t"], p["r"]] for p in pairs if not p["baseline"] and p["team_count"] == 1]
    payload = {"snapshotId": snapshot_id, "capturedAt": captured_at, "labels": labels}
    # Preserve the legacy list shape for existing generators and analysis tools.
    _atomic_json(data_dir / "all_progress.json", labels)
    _atomic_json(
        data_dir / "progress_meta.json",
        {"snapshotId": snapshot_id, "capturedAt": captured_at},
    )
    _atomic_json(data_dir / "unclaimed_nonbaseline.json", gold)
    _atomic_json(data_dir / "raid_pairs.json", raids)
    _atomic_text(data_dir / ".progress_stamp", captured_at + "\n")

    archive_name = captured_at.replace(":", "").replace("+", "_") + f"_{snapshot_id}.json.gz"
    archive_path = archive_dir / archive_name
    with tempfile.NamedTemporaryFile("wb", dir=archive_dir, delete=False) as handle:
        temp_name = handle.name
        with gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=6) as zipped:
            zipped.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    os.replace(temp_name, archive_path)
    _prune_archives(archive_dir, archive_keep)

    return ProgressResult(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        label_count=len(labels),
        pair_count=len(pairs),
        gold_count=len(gold),
        raid_count=len(raids),
        baseline_count=sum(1 for p in pairs if p["baseline"]),
    )


def import_progress_file(
    path: str | os.PathLike[str],
    ledger: Ledger,
    *,
    baseline_path: str | os.PathLike[str] | None = None,
) -> ProgressResult:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    labels = raw.get("labels", []) if isinstance(raw, dict) else raw
    if not isinstance(labels, list):
        raise ValueError("progress file must be a label list or an object containing labels")
    baseline = load_baseline_pairs(baseline_path)
    normalized, pairs = normalize_progress(labels, baseline)
    snapshot_id = f"import:{uuid.uuid4()}"
    captured_at = datetime.fromtimestamp(
        source.stat().st_mtime, tz=timezone.utc
    ).isoformat(timespec="seconds")
    ledger.record_target_snapshot(pairs, snapshot_id=snapshot_id, captured_at=captured_at)
    return ProgressResult(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        label_count=len(normalized),
        pair_count=len(pairs),
        gold_count=sum(1 for p in pairs if not p["baseline"] and p["team_count"] == 0),
        raid_count=sum(1 for p in pairs if not p["baseline"] and p["team_count"] == 1),
        baseline_count=sum(1 for p in pairs if p["baseline"]),
    )


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, separators=(",", ":")))


def _positive_decimal(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = abs(int(value))
    except (TypeError, ValueError):
        return None
    return str(number) if number > 1 else None


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temp_name = handle.name
    os.replace(temp_name, path)


def _prune_archives(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    archives = sorted(directory.glob("*.json.gz"), key=lambda path: path.stat().st_mtime)
    for path in archives[:-keep]:
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA))
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--from-file", help="import a historical snapshot without API access")
    args = parser.parse_args()
    with Ledger(args.db) as ledger:
        if args.from_file:
            result = import_progress_file(args.from_file, ledger)
        else:
            result = refresh_progress(
                APIClient(), ledger, data_dir=args.data_dir, archive_dir=args.archive_dir
            )
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()

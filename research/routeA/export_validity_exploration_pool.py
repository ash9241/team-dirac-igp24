#!/usr/bin/env python3
"""Export every uncommitted validity-certified candidate in diverse order.

The normal submission command intentionally caps selection at 1,000 rows.  A
large, already-generated exploration reserve would otherwise be reparsed for
every batch.  This exporter performs the expensive validation and ledger
filter once, then round-robins across source files so consecutive 1,000-row
shards retain architectural diversity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.select_character_calibration_pilot import load_candidate_rows
from routeA.submit_exploration_batch import ValidityRecord, validate_validity_candidate


def export_validity_pool(
    sources: Sequence[str | Path],
    output: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Validate, de-duplicate, filter, and diversely order candidate rows."""

    paths = [Path(path) for path in sources]
    destination = Path(output)
    if destination in paths:
        raise ValueError("output must not overwrite an input source")

    rows, load_counts = load_candidate_rows(paths)
    counts: Counter[str] = Counter(load_counts)
    unique: dict[str, ValidityRecord] = {}
    source_by_hash: dict[str, str] = {}
    for row in rows:
        record = validate_validity_candidate(row)
        previous = unique.get(record.candidate_hash)
        if previous is not None:
            counts["duplicate_candidate_rows"] += 1
            if previous.identity != record.identity:
                raise ValueError(
                    f"candidate {record.candidate_hash} has conflicting metadata"
                )
            continue
        unique[record.candidate_hash] = record
        source_by_hash[record.candidate_hash] = str(row["_pilot_source_path"])

    eligible: list[ValidityRecord] = []
    with Ledger(db_path) as ledger:
        for record in unique.values():
            if not record.selectable:
                counts[f"rejected_{record.rejection_reason}"] += 1
                continue
            if ledger.candidate_committed(record.candidate_hash):
                counts["rejected_committed_candidate"] += 1
                continue
            eligible.append(record)

    by_source: dict[str, list[ValidityRecord]] = defaultdict(list)
    for record in eligible:
        by_source[source_by_hash[record.candidate_hash]].append(record)
    queues: dict[str, deque[ValidityRecord]] = {}
    for source, records in by_source.items():
        records.sort(key=lambda record: (
            record.target_r,
            record.target_t,
            record.disc_abs,
            record.candidate_hash,
        ))
        queues[source] = deque(records)

    ordered: list[ValidityRecord] = []
    active = deque(sorted(queues))
    while active:
        source = active.popleft()
        queue = queues[source]
        ordered.append(queue.popleft())
        if queue:
            active.append(source)

    payload_parts: list[str] = []
    for record in ordered:
        row = {
            key: value
            for key, value in dict(record.row).items()
            if not key.startswith("_pilot_")
        }
        payload_parts.append(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        )
    payload = "".join(payload_parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(destination, payload)
    counts["unique_candidates"] = len(unique)
    counts["eligible_candidates"] = len(ordered)
    counts["eligible_source_files"] = len(by_source)
    summary = {
        "output": str(destination),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "counts": dict(sorted(counts.items())),
    }
    _atomic_text(
        destination.with_suffix(destination.suffix + ".report.json"),
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
    summary = export_validity_pool(args.sources, args.output, db_path=args.db)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

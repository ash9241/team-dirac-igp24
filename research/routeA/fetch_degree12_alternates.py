#!/usr/bin/env python3
"""Fetch additional totally real degree-12 fields for subgroup harvesting.

The primary catalog keeps only the lowest-discriminant field for each 12T
group.  Proper Kummer submodules depend on the arithmetic field and its unit
squareclasses, not only on the abstract quotient group, so additional LMFDB
fields provide genuinely new exact lifts.  This fetcher excludes the primary
labels, checkpoints after every group, and is safe to resume.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

from routeA.fetch_degree12_catalog import (
    API,
    DEFAULT_CATALOG,
    DEFAULT_MAP,
    load_jsonl,
    prioritized_bases,
)
from routeA.ledger import DEFAULT_DB


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_ALTERNATES = DATA / "degree12_lmfdb_alternates.jsonl"
DEFAULT_CHECKED = DATA / "degree12_lmfdb_alternates_checked.json"
DEFAULT_STATUS = DATA / "degree12_lmfdb_alternates_status.json"


def fetch_many(
    base_t: int,
    *,
    limit: int = 20,
    timeout: float = 60,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` lowest-discriminant fields for one 12T group."""

    query = urllib.parse.urlencode({
        "degree": "i12",
        "r2": "i0",
        "galt": f"i{int(base_t)}",
        "_sort": "disc_abs",
        "_format": "json",
        "_fields": "label,coeffs,disc_abs,galt,subfields,unit_signature_rank",
        "_limit": max(1, int(limit)),
    })
    result = subprocess.run(
        [
            "curl", "-fsS", "-b", "human=1",
            "--max-time", str(max(1, int(timeout))), API + "?" + query,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"curl exited {result.returncode}")
    if not result.stdout.lstrip().startswith("{"):
        raise RuntimeError("LMFDB challenge page; cooldown required")
    payload = json.loads(result.stdout)
    rows: list[dict[str, Any]] = []
    for source_rank, field in enumerate(payload.get("data") or [], 1):
        actual_group = int(field.get("galt", base_t))
        if actual_group != int(base_t):
            raise RuntimeError(
                f"LMFDB returned 12T{actual_group} for requested 12T{base_t}"
            )
        subfields = list(field.get("subfields") or [])
        rows.append({
            "base_t": int(base_t),
            "label": str(field["label"]),
            "disc_abs": int(field["disc_abs"]),
            "coefficients": [int(value) for value in field["coeffs"]],
            "subfields": subfields,
            "subfield_degrees": [len(value.split(".")) - 1 for value in subfields],
            "unit_signature_rank": field.get("unit_signature_rank"),
            "source_rank": source_rank,
            "source": "LMFDB nf_fields API",
        })
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows.values(),
        key=lambda row: (int(row["base_t"]), int(row["disc_abs"]), str(row["label"])),
    )
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in ordered),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_ALTERNATES)
    parser.add_argument("--checked", type=Path, default=DEFAULT_CHECKED)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--limit-bases", type=int)
    parser.add_argument("--fields-per-base", type=int, default=20)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--challenge-wait", type=float, default=120.0)
    args = parser.parse_args()

    primary_labels = {str(row["label"]) for row in load_jsonl(args.primary)}
    alternates = {str(row["label"]): row for row in load_jsonl(args.catalog)}
    checked = (
        {int(value) for value in json.loads(args.checked.read_text(encoding="utf-8"))}
        if args.checked.exists()
        else set()
    )
    priority = prioritized_bases(args.map, args.db)
    if args.base:
        wanted = set(args.base)
        priority = [item for item in priority if item[0] in wanted]
    queue = [(base, score) for base, score in priority if base not in checked]
    if args.limit_bases is not None:
        queue = queue[: max(0, args.limit_bases)]

    failures: dict[int, str] = {}
    for index, (base_t, score) in enumerate(queue, 1):
        fields: list[dict[str, Any]] | None = None
        error: Exception | None = None
        for attempt in range(max(1, args.retries)):
            try:
                fields = fetch_many(base_t, limit=args.fields_per_base)
                error = None
                break
            except Exception as exc:
                error = exc
                wait = (
                    args.challenge_wait
                    if "challenge page" in str(exc)
                    else min(30.0, 2.0 ** attempt)
                )
                time.sleep(max(1.0, wait))
        added = 0
        if error is not None:
            failures[base_t] = str(error)
        else:
            failures.pop(base_t, None)
            checked.add(base_t)
            for field in fields or []:
                if field["label"] in primary_labels:
                    continue
                field["live_character_ceiling_at_fetch"] = float(score)
                if field["label"] not in alternates:
                    added += 1
                alternates[field["label"]] = field
            _write_jsonl(args.catalog, alternates)
            _write_json(args.checked, sorted(checked))
        _write_json(args.status, {
            "checked": len(checked),
            "alternate_fields": len(alternates),
            "remaining": len(queue) - index,
            "current_base_t": base_t,
            "current_live_ceiling": score,
            "last_added": added,
            "failures": failures,
            "complete": index == len(queue),
        })
        print(json.dumps({
            "event": "alternate_catalog_group",
            "index": index,
            "queued": len(queue),
            "base_t": base_t,
            "added": added,
            "total_alternates": len(alternates),
            "error": None if error is None else str(error),
        }, sort_keys=True), flush=True)
        if args.delay > 0:
            time.sleep(args.delay)

    print(json.dumps({
        "checked": len(checked),
        "alternate_fields": len(alternates),
        "failures": len(failures),
        "catalog": str(args.catalog),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch the lowest-discriminant totally real field for each degree-12 group.

The LMFDB query is intentionally resumable and checkpointed after every base
group.  Base groups are visited in current character-kernel score order so an
interrupted overnight run still leaves the highest-value arithmetic seeds.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from routeA.ledger import DEFAULT_DB, Ledger  # noqa: E402
from routeA.scheduler import load_owned_pairs  # noqa: E402


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_MAP = DATA / "character_kernel_map.jsonl"
DEFAULT_CATALOG = DATA / "degree12_lmfdb_catalog.jsonl"
DEFAULT_CHECKED = DATA / "degree12_lmfdb_checked.json"
DEFAULT_STATUS = DATA / "degree12_lmfdb_status.json"
API = "https://www.lmfdb.org/api/nf_fields/"


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    return [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]


def prioritized_bases(map_path: str | Path, db_path: str | Path) -> list[tuple[int, float]]:
    rows = load_jsonl(map_path)
    with Ledger(db_path) as ledger:
        targets = ledger.latest_targets()
        owned = ledger.owned_pairs() | load_owned_pairs(PROJECT)
    scores: dict[int, float] = {base: 0.0 for base in range(1, 302)}
    for row in rows:
        base_t, target_t = int(row["base_t"]), int(row["target_t"])
        for roots in range(0, 25, 4):
            pair = target_t, roots
            target = targets.get(pair)
            if target is None or pair in owned or bool(target["baseline"]):
                continue
            scores[base_t] += 2.0 ** (-int(target["team_count"]))
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def fetch_one(base_t: int, *, timeout: float = 60) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({
        "degree": "i12",
        "r2": "i0",
        "galt": f"i{base_t}",
        "_sort": "disc_abs",
        "_format": "json",
        "_fields": "label,coeffs,disc_abs,galt,subfields,unit_signature_rank",
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
    data = payload.get("data") or []
    if not data:
        return None
    field = data[0]
    if int(field.get("galt", base_t)) != base_t:
        raise RuntimeError(f"LMFDB returned 12T{field.get('galt')} for requested 12T{base_t}")
    subfields = list(field.get("subfields") or [])
    return {
        "base_t": base_t,
        "label": field["label"],
        "disc_abs": int(field["disc_abs"]),
        "coefficients": [int(value) for value in field["coeffs"]],
        "subfields": subfields,
        "subfield_degrees": [len(value.split(".")) - 1 for value in subfields],
        "unit_signature_rank": field.get("unit_signature_rank"),
        "source": "LMFDB nf_fields API",
    }


def _write_jsonl(rows: dict[int, dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(rows[key], sort_keys=True) + "\n" for key in sorted(rows)),
        encoding="utf-8",
    )


def _write_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--checked", type=Path, default=DEFAULT_CHECKED)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--challenge-wait", type=float, default=120.0)
    parser.add_argument("--hold-after-complete", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    catalog = {int(row["base_t"]): row for row in load_jsonl(args.catalog)}
    checked = set()
    if args.checked.exists():
        checked = {int(value) for value in json.loads(args.checked.read_text(encoding="utf-8"))}
    queue = [(base, score) for base, score in prioritized_bases(args.map, args.db) if base not in checked]
    if args.limit is not None:
        queue = queue[: max(0, args.limit)]

    failures: dict[int, str] = {}
    for index, (base_t, score) in enumerate(queue, 1):
        error: Exception | None = None
        field = None
        for attempt in range(max(1, args.retries)):
            try:
                field = fetch_one(base_t)
                error = None
                break
            except Exception as exc:  # transient HTTP and JSON errors are retried
                error = exc
                if "challenge page" in str(exc):
                    time.sleep(max(1.0, args.challenge_wait))
                else:
                    time.sleep(min(30.0, 1.0 * (2 ** attempt)))
        if error is not None:
            failures[base_t] = str(error)
        else:
            checked.add(base_t)
            if field is not None:
                field["live_character_ceiling_at_fetch"] = score
                catalog[base_t] = field
            _write_jsonl(catalog, args.catalog)
            _write_json(sorted(checked), args.checked)
        _write_json({
            "checked": len(checked),
            "found": len(catalog),
            "remaining": len(queue) - index,
            "current_base_t": base_t,
            "current_live_ceiling": score,
            "failures": failures,
            "complete": index == len(queue),
        }, args.status)
        print(json.dumps({
            "event": "catalog_field",
            "index": index,
            "queued": len(queue),
            "base_t": base_t,
            "live_ceiling": score,
            "found": field is not None,
            "error": None if error is None else str(error),
        }, sort_keys=True), flush=True)
        if args.delay > 0:
            time.sleep(args.delay)

    print(json.dumps({
        "checked": len(checked),
        "found": len(catalog),
        "failures": len(failures),
        "catalog": str(args.catalog),
    }, sort_keys=True))
    if args.hold_after_complete:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()

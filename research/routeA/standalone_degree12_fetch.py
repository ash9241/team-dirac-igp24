#!/usr/bin/env python3
"""Standalone, launchd-safe LMFDB seed fetcher using only supplied /tmp paths."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any


API = "https://www.lmfdb.org/api/nf_fields/"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fetch_one(base_t: int, timeout: float = 60) -> dict[str, Any] | None:
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
            "/usr/bin/curl", "-fsS", "-b", "human=1",
            "--max-time", str(int(timeout)), API + "?" + query,
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
    data = json.loads(result.stdout).get("data") or []
    if not data:
        return None
    field = data[0]
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


def write_json(value: Any, path: Path) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(rows: dict[int, dict[str, Any]], path: Path) -> None:
    path.write_text(
        "".join(json.dumps(rows[key], sort_keys=True) + "\n" for key in sorted(rows)),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--priority", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--checked", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--delay", type=float, default=0.75)
    parser.add_argument("--challenge-wait", type=float, default=120.0)
    parser.add_argument("--hold-after-complete", action="store_true")
    args = parser.parse_args()

    priority = [(int(base), float(score)) for base, score in json.loads(args.priority.read_text())]
    catalog = {int(row["base_t"]): row for row in load_jsonl(args.catalog)}
    checked = set(json.loads(args.checked.read_text())) if args.checked.exists() else set()
    queue = [(base, score) for base, score in priority if base not in checked]
    failures: dict[int, str] = {}
    index = 0
    while index < len(queue):
        base_t, score = queue[index]
        try:
            field = fetch_one(base_t)
        except Exception as exc:
            failures[base_t] = str(exc)
            write_json({
                "checked": len(checked),
                "found": len(catalog),
                "remaining": len(queue) - index,
                "current_base_t": base_t,
                "current_live_ceiling": score,
                "last_error": str(exc),
                "complete": False,
            }, args.status)
            wait = args.challenge_wait if "challenge page" in str(exc) else 30.0
            time.sleep(max(1.0, wait))
            continue
        failures.pop(base_t, None)
        checked.add(base_t)
        if field is not None:
            field["live_character_ceiling_at_fetch"] = score
            catalog[base_t] = field
        write_jsonl(catalog, args.catalog)
        write_json(sorted(checked), args.checked)
        index += 1
        write_json({
            "checked": len(checked),
            "found": len(catalog),
            "remaining": len(queue) - index,
            "current_base_t": base_t,
            "current_live_ceiling": score,
            "last_error": None,
            "complete": index == len(queue),
        }, args.status)
        time.sleep(max(0.0, args.delay))

    if args.hold_after_complete:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()

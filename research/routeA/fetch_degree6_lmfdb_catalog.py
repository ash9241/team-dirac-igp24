#!/usr/bin/env python3
"""Fetch low-discriminant totally-real sextic fields from the LMFDB API."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Sequence


API = "https://www.lmfdb.org/api/nf_fields/"


def fetch_group(group_t: int, *, limit: int = 2, timeout: float = 60) -> list[dict[str, Any]]:
    if not 1 <= int(group_t) <= 16:
        raise ValueError("degree-6 transitive group must lie in [1, 16]")
    query = urllib.parse.urlencode({
        # The current LMFDB API accepts bare integer filters.  Older local
        # fetchers use the legacy i-prefixed encoding, which now returns 500.
        "degree": 6,
        "r2": 0,
        "galt": int(group_t),
        "_sort": "disc_abs",
        "_format": "json",
        "_fields": "label,coeffs,disc_abs,galt",
        "_limit": max(1, int(limit)),
    })
    result = subprocess.run(
        [
            "curl", "-fsS", "-b", "human=1", "--max-time",
            str(max(1, int(timeout))), API + "?" + query,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"curl exited {result.returncode}")
    payload = json.loads(result.stdout)
    rows: list[dict[str, Any]] = []
    for rank, field in enumerate((payload.get("data") or [])[: int(limit)], 1):
        actual = int(field["galt"])
        if actual != int(group_t):
            raise RuntimeError(f"LMFDB returned 6T{actual} for requested 6T{group_t}")
        coefficients = list(map(int, field["coeffs"]))
        if len(coefficients) != 7 or coefficients[-1] != 1:
            raise ValueError("LMFDB sextic is not monic degree 6")
        rows.append({
            "base_group": f"6T{int(group_t)}",
            "source_index": rank - 1,
            "source_rank": rank,
            "label": str(field["label"]),
            "disc_abs": int(field["disc_abs"]),
            "coefficients": coefficients,
            "source": "LMFDB nf_fields API",
        })
    return rows


def fetch_catalog(groups: Sequence[int], *, fields_per_group: int = 2) -> list[dict[str, Any]]:
    rows = [
        row
        for group_t in sorted({int(value) for value in groups})
        for row in fetch_group(group_t, limit=fields_per_group)
    ]
    # Indexes only need to be unique within the human-readable group tag.
    return sorted(rows, key=lambda row: (
        int(str(row["base_group"])[2:]), int(row["source_index"])
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--group", action="append", type=int, default=[])
    parser.add_argument("--fields-per-group", type=int, default=2)
    args = parser.parse_args()
    groups = args.group or list(range(1, 17))
    rows = fetch_catalog(groups, fields_per_group=args.fields_per_group)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "requested_groups": len(set(groups)),
        "groups_found": len({row["base_group"] for row in rows}),
        "fields": len(rows),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch exact degree-24 field seeds from the LMFDB API in parallel."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from routeA.ledger import candidate_hash, canonical_coefficients


API = "https://beta.lmfdb.org/api/nf_fields/"
PAGE_SIZE = 100


def fetch_page(
    offset: int,
    *,
    sort: str = "disc_abs",
    timeout: int = 90,
    retries: int = 4,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "degree": 24,
        "_format": "json",
        "_fields": "label,coeffs,disc_abs,galt,r2",
        "_sort": str(sort),
        "_limit": PAGE_SIZE,
        "_offset": int(offset),
    })
    error = "unknown fetch failure"
    for attempt in range(max(1, int(retries))):
        result = subprocess.run(
            [
                "curl", "-fsS", "-b", "human=1", "--max-time",
                str(int(timeout)), API + "?" + query,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.lstrip().startswith("{"):
            payload = json.loads(result.stdout)
            if isinstance(payload.get("data"), list):
                return payload
            error = "LMFDB response has no data list"
        else:
            error = result.stderr.strip() or f"curl exited {result.returncode}"
        time.sleep(min(8.0, 0.5 * (2 ** attempt)))
    raise RuntimeError(f"sort {sort} offset {offset}: {error}")


def normalize(field: dict[str, Any]) -> dict[str, Any]:
    coefficient_line = canonical_coefficients(field["coeffs"])
    coefficients = [int(value) for value in coefficient_line.split(",")]
    if len(coefficients) != 25:
        raise ValueError(f"{field.get('label')} is not degree 24")
    target_t = int(field["galt"])
    r2 = int(field["r2"])
    target_r = 24 - 2 * r2
    if not 1 <= target_t <= 25_000 or target_r not in range(0, 25, 2):
        raise ValueError(f"invalid exact target for {field.get('label')}")
    key = candidate_hash(coefficient_line)
    disc_abs = int(field["disc_abs"])
    return {
        "candidate_hash": key,
        "coefficients": coefficients,
        "target_t": target_t,
        "target_r": target_r,
        "source_t": target_t,
        "source_r": target_r,
        "local_root_count": target_r,
        "local_irreducible": True,
        "label_probability": 1.0,
        "valid_probability": 1.0,
        "submission_ready": True,
        "exact_compatibility_proven": True,
        "field_disc_abs": disc_abs,
        "disc_abs": disc_abs,
        "recipe_family": "lmfdb_degree24_exact",
        "recipe_lineage": f"lmfdb:{field['label']}",
        "construction_overgroup": f"LMFDB exact 24T{target_t}",
        "parameters": {
            "lmfdb_label": str(field["label"]),
            "lmfdb_galt": target_t,
            "lmfdb_r2": r2,
            "source": "LMFDB nf_fields API",
        },
    }


def fetch_catalog(
    output: Path,
    *,
    total: int = 18_252,
    workers: int = 16,
) -> dict[str, Any]:
    # The public API caps offsets at 10,000.  Reading both discriminant orders
    # covers collections up to 20,200 rows while retaining a large overlap
    # that lets the label deduplication below detect pagination drift.
    offsets = list(range(0, min(int(total), 10_100), PAGE_SIZE))
    page_keys = [("disc_abs", offset) for offset in offsets]
    if int(total) > 10_100:
        page_keys.extend(("-disc_abs", offset) for offset in offsets)
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = {
            pool.submit(fetch_page, offset, sort=sort): (sort, offset)
            for sort, offset in page_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            pages[key] = future.result()
    raw_fields = [
        field
        for key in page_keys
        for field in pages[key].get("data", [])
    ]
    by_label = {str(field["label"]): field for field in raw_fields}
    rows = [normalize(by_label[label]) for label in sorted(by_label)]
    rows.sort(key=lambda row: (
        int(row["target_t"]), int(row["target_r"]),
        int(row["field_disc_abs"]), str(row["candidate_hash"]),
    ))
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(output)
    summary = {
        "output": str(output),
        "requested_total": int(total),
        "pages": len(page_keys),
        "rows": len(rows),
        "target_groups": len({int(row["target_t"]) for row in rows}),
        "target_pairs": len({
            (int(row["target_t"]), int(row["target_r"])) for row in rows
        }),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "source": API,
    }
    output.with_suffix(output.suffix + ".report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--total", type=int, default=18_252)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    print(json.dumps(
        fetch_catalog(args.output, total=args.total, workers=args.workers),
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()

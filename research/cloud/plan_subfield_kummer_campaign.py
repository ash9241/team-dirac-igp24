#!/usr/bin/env python3
"""Plan exact proper-subfield Kummer searches for degree-12 catalog fields.

Each task receives one degree-12 field and one requested proper-subfield
degree.  Keeping all requested shifts in the same task lets GAP reuse its
expensive transitive-subgroup data while fields and subfield degrees still
run independently across Cloud Batch workers.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from cloud.plan_character_campaign import (
    PROJECT,
    _atomic_json,
    _atomic_jsonl,
    _now,
    _relative,
    _require_within,
    _safe_id,
    _sha256,
    load_jsonl,
)


DEFAULT_CATALOG = PROJECT / "routeA" / "data" / "degree12_lmfdb_catalog.jsonl"
DEFAULT_MAP = PROJECT / "routeA" / "data" / "full_wreath_map.jsonl"


def plan_subfield_kummer_campaign(
    *,
    campaign_id: str,
    output_dir: str | Path,
    catalog_path: str | Path = DEFAULT_CATALOG,
    wreath_map_path: str | Path = DEFAULT_MAP,
    bases: Sequence[int] = (),
    degrees: Sequence[int] = (2, 3, 4, 6),
    shift_min: int = -2,
    shift_max: int = 2,
    prime_limit: int = 10000,
    timeout: float = 3600,
    classification_mode: str = "exact",
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(output_dir, project, "campaign output")
    catalog_path = Path(catalog_path).resolve()
    wreath_map_path = Path(wreath_map_path).resolve()
    _require_within(catalog_path, project, "degree-12 catalog")
    _require_within(wreath_map_path, project, "full-wreath map")
    if int(shift_min) > int(shift_max):
        raise ValueError("shift_min must not exceed shift_max")
    requested_degrees = tuple(sorted({int(value) for value in degrees}))
    if not requested_degrees or requested_degrees[0] < 2 or requested_degrees[-1] >= 12:
        raise ValueError("proper subfield degrees must lie in [2, 11]")
    if int(prime_limit) < 2:
        raise ValueError("prime_limit must be at least 2")
    if float(timeout) <= 0:
        raise ValueError("timeout must be positive")
    if classification_mode not in {"exact", "validity"}:
        raise ValueError("classification_mode must be exact or validity")

    campaign = _safe_id(campaign_id)
    mapped = {int(row["base_t"]) for row in load_jsonl(wreath_map_path)}
    rows_by_base: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in load_jsonl(catalog_path):
        base_t = int(row["base_t"])
        if base_t in mapped:
            rows_by_base[base_t].append(row)
    selected_bases = sorted(rows_by_base)
    if bases:
        requested_bases = {int(value) for value in bases}
        missing = sorted(requested_bases - set(selected_bases))
        if missing:
            raise ValueError(f"unavailable degree-12 bases: {missing}")
        selected_bases = [value for value in selected_bases if value in requested_bases]
    if not selected_bases:
        raise ValueError("no mapped degree-12 catalog fields were selected")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir = output_dir / "manifests"
    catalog_shards_dir = output_dir / "catalog_shards"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    catalog_shards_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    input_paths: set[Path] = {wreath_map_path}
    tasks: list[dict[str, Any]] = []
    field_count = 0

    for base_t in selected_bases:
        for field_index, field in enumerate(rows_by_base[base_t]):
            field_count += 1
            field_shard = catalog_shards_dir / f"b{base_t:03d}-f{field_index:04d}.jsonl"
            _atomic_jsonl(field_shard, [field])
            input_paths.add(field_shard)
            for degree in requested_degrees:
                job_id = _safe_id(
                    f"{campaign}-b{base_t:03d}-f{field_index:04d}-d{degree:02d}"
                )
                stem = output_prefix / job_id
                candidates = stem.with_suffix(".candidates.jsonl")
                payload = stem.with_suffix(".txt")
                report = stem.with_suffix(".report.json")
                manifest_inputs = (field_shard, wreath_map_path)
                manifest = {
                    "job_id": job_id,
                    "module": "routeA.build_subfield_kummer_campaign",
                    "args": [
                        "--catalog", _relative(field_shard, project),
                        "--wreath-map", _relative(wreath_map_path, project),
                        "--manifest", str(candidates),
                        "--payload", str(payload),
                        "--degree", str(degree),
                        "--shift-min", str(int(shift_min)),
                        "--shift-max", str(int(shift_max)),
                        "--coefficient-limit", str(10**120),
                        "--prime-limit", str(int(prime_limit)),
                        "--timeout", str(float(timeout)),
                        "--classification-mode", classification_mode,
                    ],
                    "inputs": [
                        {"path": _relative(path, project), "sha256": _sha256(path)}
                        for path in manifest_inputs
                    ],
                    "artifacts": [
                        {"path": str(candidates), "required": False},
                        {"path": str(payload), "required": False},
                    ],
                    "report": str(report),
                    "campaign": {
                        "campaign_id": campaign,
                        "base_t": base_t,
                        "field_index": field_index,
                        "subfield_degree": degree,
                        "lane": f"subfield-kummer-{classification_mode}",
                    },
                }
                manifest_path = manifests_dir / f"{job_id}.json"
                _atomic_json(manifest_path, manifest)
                tasks.append({
                    "index": len(tasks),
                    "job_id": job_id,
                    "manifest": _relative(manifest_path, project),
                    "manifest_sha256": _sha256(manifest_path),
                    "base_t": base_t,
                    "field_index": field_index,
                    "subfield_degree": degree,
                    "lane": f"subfield-kummer-{classification_mode}",
                    "variant": f"subfield-degree-{degree}",
                })

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "task_count": len(tasks),
        "field_count": field_count,
        "base_count": len(selected_bases),
        "degrees": list(requested_degrees),
        "shift_min": int(shift_min),
        "shift_max": int(shift_max),
        "prime_limit": int(prime_limit),
        "timeout": float(timeout),
        "classification_mode": classification_mode,
        "inputs": [
            {
                "path": _relative(path, project),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(input_paths)
        ],
        "tasks": tasks,
    }
    matrix_path = output_dir / "matrix.json"
    _atomic_json(matrix_path, matrix)
    summary = {
        "campaign_id": campaign,
        "matrix": _relative(matrix_path, project),
        "task_count": len(tasks),
        "field_count": field_count,
        "base_count": len(selected_bases),
        "degrees": list(requested_degrees),
        "shifts_per_task": int(shift_max) - int(shift_min) + 1,
        "maximum_seeds": len(tasks) * (int(shift_max) - int(shift_min) + 1),
        "prime_limit": int(prime_limit),
        "classification_mode": classification_mode,
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--wreath-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--degree", action="append", type=int, default=[])
    parser.add_argument("--shift-min", type=int, default=-2)
    parser.add_argument("--shift-max", type=int, default=2)
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument(
        "--classification-mode",
        choices=("exact", "validity"),
        default="exact",
    )
    args = parser.parse_args()
    summary = plan_subfield_kummer_campaign(
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        catalog_path=args.catalog,
        wreath_map_path=args.wreath_map,
        bases=args.base,
        degrees=args.degree or (2, 3, 4, 6),
        shift_min=args.shift_min,
        shift_max=args.shift_max,
        prime_limit=args.prime_limit,
        timeout=args.timeout,
        classification_mode=args.classification_mode,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

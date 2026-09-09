#!/usr/bin/env python3
"""Plan one credential-free full-wreath proper-subgroup task per base group."""

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
from routeA.ledger import DEFAULT_DB, Ledger


DEFAULT_CATALOG = PROJECT / "routeA" / "data" / "degree12_lmfdb_catalog.jsonl"
DEFAULT_MAP = PROJECT / "routeA" / "data" / "full_wreath_map.jsonl"


def plan_wreath_subgroup_campaign(
    *,
    campaign_id: str,
    output_dir: str | Path,
    db_path: str | Path = DEFAULT_DB,
    catalog_path: str | Path = DEFAULT_CATALOG,
    wreath_map_path: str | Path = DEFAULT_MAP,
    bases: Sequence[int] = (),
    shift_min: int = -3,
    shift_max: int = 3,
    options_per_signature: int = 4,
    minimum_value: float = 0.125,
    prime_limit: int = 10000,
    catalog_rows_per_task: int | None = None,
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(output_dir, project, "campaign output")
    if int(shift_min) > int(shift_max):
        raise ValueError("shift_min must not exceed shift_max")
    if int(options_per_signature) < 1:
        raise ValueError("options_per_signature must be positive")
    if float(minimum_value) < 0:
        raise ValueError("minimum_value must be nonnegative")
    if catalog_rows_per_task is not None and int(catalog_rows_per_task) < 1:
        raise ValueError("catalog_rows_per_task must be positive when provided")
    campaign = _safe_id(campaign_id)
    catalog_path = Path(catalog_path).resolve()
    wreath_map_path = Path(wreath_map_path).resolve()
    _require_within(catalog_path, project, "degree-12 catalog")
    _require_within(wreath_map_path, project, "full-wreath map")

    catalog_rows = load_jsonl(catalog_path)
    rows_by_base: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in catalog_rows:
        rows_by_base[int(row["base_t"])].append(row)
    available = {int(row["base_t"]) for row in catalog_rows}
    mapped = {int(row["base_t"]) for row in load_jsonl(wreath_map_path)}
    selected_bases = sorted(available & mapped)
    if bases:
        requested = {int(base) for base in bases}
        missing = sorted(requested - set(selected_bases))
        if missing:
            raise ValueError(f"unavailable degree-12 bases: {missing}")
        selected_bases = [base for base in selected_bases if base in requested]

    with Ledger(db_path) as ledger:
        live = ledger.latest_targets()
        owned = ledger.owned_pairs()
    if not live:
        raise RuntimeError("live target snapshot is required")
    snapshot_id = str(next(iter(live.values()))["snapshot_id"])
    target_rows = [
        {
            "t": int(row["t"]),
            "r": int(row["r"]),
            "team_count": int(row["team_count"]),
            "discovered": bool(row["discovered"]),
            "baseline": bool(row["baseline"]),
            "immediate_value": float(row["immediate_value"]),
            "minimum_disc_abs": row["minimum_disc_abs"],
        }
        for row in live.values()
    ]
    target_rows.sort(key=lambda row: (row["t"], row["r"]))
    owned_rows = [
        {"t": target_t, "r": target_r}
        for target_t, target_r in sorted(owned)
    ]
    targets_path = output_dir / "target-snapshot.jsonl"
    owned_path = output_dir / "owned-pairs.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_jsonl(targets_path, target_rows)
    _atomic_jsonl(owned_path, owned_rows)

    manifests_dir = output_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    shared_inputs = (wreath_map_path, targets_path, owned_path)
    input_paths = set(shared_inputs)
    catalog_shards_dir = output_dir / "catalog_shards"
    tasks: list[dict[str, Any]] = []
    for base_t in selected_bases:
        units: list[tuple[Path, int, int]] = [
            (catalog_path, 0, len(rows_by_base[base_t]))
        ]
        if catalog_rows_per_task is not None:
            units = []
            chunk_size = int(catalog_rows_per_task)
            for unit_index, start in enumerate(
                range(0, len(rows_by_base[base_t]), chunk_size)
            ):
                chunk = rows_by_base[base_t][start:start + chunk_size]
                shard = catalog_shards_dir / f"b{base_t:03d}-f{unit_index:04d}.jsonl"
                _atomic_jsonl(shard, chunk)
                units.append((shard, unit_index, len(chunk)))
        for task_catalog, unit_index, unit_rows in units:
            input_paths.add(task_catalog)
            field_tag = (
                "" if catalog_rows_per_task is None else f"-f{unit_index:04d}"
            )
            job_id = _safe_id(f"{campaign}-b{base_t:03d}{field_tag}-wreath")
            stem = output_prefix / job_id
            candidates = stem.with_suffix(".candidates.jsonl")
            payload = stem.with_suffix(".txt")
            worker_db = stem.with_suffix(".sqlite3")
            report = stem.with_suffix(".report.json")
            manifest_inputs = (task_catalog, *shared_inputs)
            manifest = {
            "job_id": job_id,
            "module": "routeA.build_wreath_seed_campaign",
            "args": [
                "--catalog", _relative(task_catalog, project),
                "--wreath-map", _relative(wreath_map_path, project),
                "--manifest", str(candidates),
                "--payload", str(payload),
                "--db", str(worker_db),
                "--target-snapshot", _relative(targets_path, project),
                "--owned-pairs", _relative(owned_path, project),
                "--base", str(base_t),
                "--shift-min", str(int(shift_min)),
                "--shift-max", str(int(shift_max)),
                "--options-per-signature", str(int(options_per_signature)),
                "--minimum-value", str(float(minimum_value)),
                "--coefficient-limit", str(10**120),
                "--prime-limit", str(int(prime_limit)),
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
                "catalog_unit": unit_index,
                "catalog_rows": unit_rows,
                "lane": "wreath-subgroup",
                "target_snapshot_id": snapshot_id,
                "minimum_value": float(minimum_value),
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
                "catalog_unit": unit_index,
                "catalog_rows": unit_rows,
                "lane": "wreath-subgroup",
                "variant": "wreath-subgroup",
            })

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "target_snapshot_id": snapshot_id,
        "task_count": len(tasks),
        "shift_min": int(shift_min),
        "shift_max": int(shift_max),
        "options_per_signature": int(options_per_signature),
        "minimum_value": float(minimum_value),
        "catalog_rows_per_task": catalog_rows_per_task,
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
        "base_count": len(selected_bases),
        "target_snapshot_id": snapshot_id,
        "owned_pairs": len(owned_rows),
        "minimum_value": float(minimum_value),
        "seed_families": len(selected_bases) * (int(shift_max) - int(shift_min) + 1),
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--wreath-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--shift-min", type=int, default=-3)
    parser.add_argument("--shift-max", type=int, default=3)
    parser.add_argument("--options-per-signature", type=int, default=4)
    parser.add_argument("--minimum-value", type=float, default=0.125)
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--catalog-rows-per-task", type=int)
    args = parser.parse_args()
    summary = plan_wreath_subgroup_campaign(
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        db_path=args.db,
        catalog_path=args.catalog,
        wreath_map_path=args.wreath_map,
        bases=args.base,
        shift_min=args.shift_min,
        shift_max=args.shift_max,
        options_per_signature=args.options_per_signature,
        minimum_value=args.minimum_value,
        prime_limit=args.prime_limit,
        catalog_rows_per_task=args.catalog_rows_per_task,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

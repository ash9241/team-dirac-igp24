#!/usr/bin/env python3
"""Plan one credential-free unit-twist task per degree-12 source field."""

from __future__ import annotations

import argparse
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


DEFAULT_MAP = PROJECT / "routeA" / "data" / "full_wreath_map.jsonl"


def plan_subfield_unit_twist_campaign(
    *,
    campaign_id: str,
    output_dir: str | Path,
    source_paths: Sequence[str | Path],
    wreath_map_path: str | Path = DEFAULT_MAP,
    bases: Sequence[int] = (),
    exclude_bases: Sequence[int] = (),
    options_per_signature: int = 1,
    max_source_seeds_per_field: int = 1,
    max_lifts_per_field: int = 30,
    timeout: float = 3600,
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(output_dir, project, "campaign output")
    source_paths = tuple(Path(path).resolve() for path in source_paths)
    if not source_paths:
        raise ValueError("at least one source candidate manifest is required")
    for path in source_paths:
        _require_within(path, project, "source candidate manifest")
    wreath_map_path = Path(wreath_map_path).resolve()
    _require_within(wreath_map_path, project, "full-wreath map")
    if min(
        int(options_per_signature),
        int(max_source_seeds_per_field),
        int(max_lifts_per_field),
    ) < 1:
        raise ValueError("unit-twist limits must be positive")
    if float(timeout) <= 0:
        raise ValueError("timeout must be positive")

    mapped = {int(row["base_t"]) for row in load_jsonl(wreath_map_path)}
    wanted = {int(value) for value in bases}
    excluded = {int(value) for value in exclude_bases}
    grouped: dict[
        tuple[int, tuple[int, ...], str], list[dict[str, Any]]
    ] = defaultdict(list)
    source_row_count = 0
    for source_path in source_paths:
        for row in load_jsonl(source_path):
            source_row_count += 1
            base_t = int(row["base_t"])
            if (
                base_t not in mapped
                or base_t in excluded
                or (wanted and base_t not in wanted)
            ):
                continue
            key = (
                base_t,
                tuple(int(value) for value in row["base_coefficients"]),
                str(row.get("source_field_label") or ""),
            )
            grouped[key].append(row)

    if not grouped:
        raise ValueError("no eligible source fields remain")

    campaign = _safe_id(campaign_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir = output_dir / "manifests"
    source_shards_dir = output_dir / "source_shards"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    source_shards_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    input_paths: set[Path] = {wreath_map_path}
    tasks: list[dict[str, Any]] = []
    index_by_base: dict[int, int] = defaultdict(int)

    for (base_t, _, source_label), rows in sorted(grouped.items()):
        field_index = index_by_base[base_t]
        index_by_base[base_t] += 1
        source_shard = source_shards_dir / f"b{base_t:03d}-f{field_index:04d}.jsonl"
        _atomic_jsonl(source_shard, rows)
        input_paths.add(source_shard)
        job_id = _safe_id(f"{campaign}-b{base_t:03d}-f{field_index:04d}")
        stem = output_prefix / job_id
        candidates = stem.with_suffix(".candidates.jsonl")
        payload = stem.with_suffix(".txt")
        report = stem.with_suffix(".report.json")
        args = [
            "--source", _relative(source_shard, project),
            "--wreath-map", _relative(wreath_map_path, project),
            "--manifest", str(candidates),
            "--payload", str(payload),
            "--options-per-signature", str(int(options_per_signature)),
            "--max-source-seeds-per-field", str(int(max_source_seeds_per_field)),
            "--max-lifts-per-field", str(int(max_lifts_per_field)),
            "--coefficient-limit", str(10**120),
            "--timeout", str(float(timeout)),
            "--base", str(base_t),
        ]
        manifest_inputs = (source_shard, wreath_map_path)
        manifest = {
            "job_id": job_id,
            "module": "routeA.build_subfield_unit_twist_campaign",
            "args": args,
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
                "source_field_label": source_label,
                "lane": "subfield-unit-twist-validity",
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
            "source_field_label": source_label,
            "source_rows": len(rows),
            "lane": "subfield-unit-twist-validity",
            "variant": "unit-twist-field",
        })

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "task_count": len(tasks),
        "field_count": len(tasks),
        "base_count": len({task["base_t"] for task in tasks}),
        "options_per_signature": int(options_per_signature),
        "max_source_seeds_per_field": int(max_source_seeds_per_field),
        "max_lifts_per_field": int(max_lifts_per_field),
        "timeout": float(timeout),
        "source_row_count": source_row_count,
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
        "field_count": len(tasks),
        "base_count": matrix["base_count"],
        "maximum_candidates": len(tasks) * int(max_lifts_per_field),
        "source_row_count": source_row_count,
        "excluded_bases": sorted(excluded),
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--wreath-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--exclude-base", action="append", type=int, default=[])
    parser.add_argument("--options-per-signature", type=int, default=1)
    parser.add_argument("--max-source-seeds-per-field", type=int, default=1)
    parser.add_argument("--max-lifts-per-field", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    summary = plan_subfield_unit_twist_campaign(
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        source_paths=args.source,
        wreath_map_path=args.wreath_map,
        bases=args.base,
        exclude_bases=args.exclude_base,
        options_per_signature=args.options_per_signature,
        max_source_seeds_per_field=args.max_source_seeds_per_field,
        max_lifts_per_field=args.max_lifts_per_field,
        timeout=args.timeout,
    )
    import json
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

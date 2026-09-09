#!/usr/bin/env python3
"""Plan a deeper norm wave for mapped targets absent from a discovery merge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from cloud.plan_character_campaign import (
    DEFAULT_CATALOGS,
    DEFAULT_MAP,
    PROJECT,
    _atomic_json,
    _relative,
    _require_within,
    _sha256,
    base_opportunity,
    load_jsonl,
    nonstandard_targets_by_base,
    plan_campaign,
)
from routeA.ledger import DEFAULT_DB


DEFAULT_DEEP_SCALES = (
    67, 71, 73, 79, 83, 89, 97,
    101, 103, 107, 109, 113, 127, 131,
    137, 139, 149, 151, 157, 163, 167,
    173, 179, 181, 191, 193, 197, 199,
)


def plan_deep_campaign(
    discoveries_path: str | Path,
    *,
    campaign_id: str,
    output_dir: str | Path,
    catalogs: Sequence[str | Path] = DEFAULT_CATALOGS,
    map_path: str | Path = DEFAULT_MAP,
    db_path: str | Path | None = DEFAULT_DB,
    minimum_missing_opportunity: float = 0.0,
    base_limit: int | None = None,
    catalog_rows_per_task: int = 2,
    norm_scale_shards: int = 8,
    norm_scales: Sequence[int] = DEFAULT_DEEP_SCALES,
    norm_solutions_per_scale: int = 2,
    norm_options_per_squareclass: int = 8,
    norm_equation_timeout: int = 10,
    norm_initialization_timeout: int = 180,
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    discoveries_path = Path(discoveries_path).resolve()
    map_path = Path(map_path).resolve()
    _require_within(discoveries_path, project, "discoveries")
    _require_within(map_path, project, "character map")
    discoveries = load_jsonl(discoveries_path)
    found_targets = {
        int(row.get("starting_target_t", row.get("target_t")))
        for row in discoveries
    }
    target_map = nonstandard_targets_by_base(load_jsonl(map_path))
    missing_map = {
        base: targets - found_targets
        for base, targets in target_map.items()
        if targets - found_targets
    }
    opportunities, snapshot_id = base_opportunity(missing_map, db_path=db_path)
    ranked = sorted(
        (
            (float(opportunities.get(base, 0.0)), int(base), len(targets))
            for base, targets in missing_map.items()
            if float(opportunities.get(base, 0.0)) > float(minimum_missing_opportunity)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if base_limit is not None:
        ranked = ranked[: max(0, int(base_limit))]
    selected_bases = [base for _, base, _ in ranked]
    if not selected_bases:
        raise ValueError("no bases have missing live character opportunity")

    summary = plan_campaign(
        campaign_id=campaign_id,
        output_dir=output_dir,
        catalogs=catalogs,
        map_path=map_path,
        db_path=db_path,
        lanes=("norm",),
        bases=selected_bases,
        catalog_rows_per_task=catalog_rows_per_task,
        norm_scale_shards=norm_scale_shards,
        norm_scales=norm_scales,
        norm_solutions_per_scale=norm_solutions_per_scale,
        norm_options_per_squareclass=norm_options_per_squareclass,
        norm_equation_timeout=norm_equation_timeout,
        norm_initialization_timeout=norm_initialization_timeout,
        project=project,
    )
    output_dir = Path(output_dir).resolve()
    matrix_path = output_dir / "matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    selection = {
        "source_discoveries": _relative(discoveries_path, project),
        "source_discoveries_sha256": _sha256(discoveries_path),
        "source_discovery_rows": len(discoveries),
        "source_discovered_targets": len(found_targets),
        "missing_live_targets": len(set().union(*(missing_map[base] for base in selected_bases))),
        "missing_live_opportunity": sum(score for score, _, _ in ranked),
        "minimum_missing_opportunity": float(minimum_missing_opportunity),
        "selected_base_count": len(selected_bases),
        "selected_bases": selected_bases,
        "target_snapshot_id": snapshot_id,
    }
    matrix["deep_selection"] = selection
    _atomic_json(matrix_path, matrix)
    summary.update(selection)
    summary["matrix"] = _relative(matrix_path, project)
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discoveries", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--catalog", action="append", type=Path, default=[])
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--minimum-missing-opportunity", type=float, default=0.0)
    parser.add_argument("--base-limit", type=int)
    parser.add_argument("--catalog-rows-per-task", type=int, default=2)
    parser.add_argument("--norm-scale-shards", type=int, default=8)
    parser.add_argument("--norm-scale", action="append", type=int, default=[])
    parser.add_argument("--norm-solutions-per-scale", type=int, default=2)
    parser.add_argument("--norm-options-per-squareclass", type=int, default=8)
    parser.add_argument("--norm-equation-timeout", type=int, default=10)
    parser.add_argument("--norm-initialization-timeout", type=int, default=180)
    args = parser.parse_args()
    summary = plan_deep_campaign(
        args.discoveries,
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        catalogs=args.catalog or DEFAULT_CATALOGS,
        map_path=args.map,
        db_path=args.db,
        minimum_missing_opportunity=args.minimum_missing_opportunity,
        base_limit=args.base_limit,
        catalog_rows_per_task=args.catalog_rows_per_task,
        norm_scale_shards=args.norm_scale_shards,
        norm_scales=args.norm_scale or DEFAULT_DEEP_SCALES,
        norm_solutions_per_scale=args.norm_solutions_per_scale,
        norm_options_per_squareclass=args.norm_options_per_squareclass,
        norm_equation_timeout=args.norm_equation_timeout,
        norm_initialization_timeout=args.norm_initialization_timeout,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Plan deterministic, credential-free character-discovery array tasks.

Each task owns one catalog/base/lane parameter shard and writes to unique
paths.  The resulting matrix is transport-agnostic: it can run locally with
``cloud.run_array_task`` or as a Google Cloud Batch task array.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import load_owned_pairs


PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / "routeA" / "data"
DEFAULT_CATALOGS = (
    DATA / "degree12_lmfdb_catalog.jsonl",
    DATA / "degree12_lmfdb_alternates.jsonl",
)
DEFAULT_MAP = DATA / "character_kernel_map.jsonl"
DEFAULT_LANES = ("linear", "product", "kernel", "norm")
DEFAULT_NORM_SCALES = (
    1, 2, 3, 5, 7, 11, 13, 17, 19, 23,
    29, 31, 37, 41, 43, 47, 53, 59, 61,
)
LANE_MODULES = {
    "linear": "routeA.discover_linear_characters",
    "product": "routeA.discover_product_characters",
    "kernel": "routeA.discover_kernel_product_characters",
    "norm": "routeA.discover_norm_equation_characters",
}


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def nonstandard_targets_by_base(
    map_rows: Iterable[Mapping[str, Any]],
) -> dict[int, set[int]]:
    output: dict[int, set[int]] = defaultdict(set)
    for row in map_rows:
        if row.get("includes_permutation_sign") or row.get("includes_trivial_character"):
            continue
        output[int(row["base_t"])].add(int(row["target_t"]))
    return dict(output)


def base_opportunity(
    targets_by_base: Mapping[int, set[int]],
    *,
    db_path: str | Path | None = DEFAULT_DB,
) -> tuple[dict[int, float], str | None]:
    """Score each base by the better live r mod 4 parity for each character."""

    fallback = {
        int(base): float(len(targets))
        for base, targets in targets_by_base.items()
    }
    if db_path is None or not Path(db_path).exists():
        return fallback, None
    with Ledger(db_path) as ledger:
        live = ledger.latest_targets()
        owned = load_owned_pairs(PROJECT) | ledger.owned_pairs()
    if not live:
        return fallback, None
    any_row = next(iter(live.values()))
    snapshot_id = str(any_row["snapshot_id"])
    scores: dict[int, float] = {}
    for base, target_ts in targets_by_base.items():
        score = 0.0
        for target_t in target_ts:
            parity_values = {0: 0.0, 2: 0.0}
            for root in range(0, 25, 2):
                pair = int(target_t), root
                row = live.get(pair)
                if row is None or pair in owned or bool(row["baseline"]):
                    continue
                parity_values[root % 4] += 2.0 ** (-int(row["team_count"]))
            score += max(parity_values.values())
        scores[int(base)] = score
    return scores, snapshot_id


def plan_campaign(
    *,
    campaign_id: str,
    output_dir: str | Path,
    catalogs: Sequence[str | Path] = DEFAULT_CATALOGS,
    map_path: str | Path = DEFAULT_MAP,
    db_path: str | Path | None = DEFAULT_DB,
    lanes: Sequence[str] = DEFAULT_LANES,
    bases: Sequence[int] = (),
    base_limit: int | None = None,
    max_seeds_per_task: int | None = None,
    catalog_rows_per_task: int | None = None,
    norm_scale_shards: int = 4,
    norm_scales: Sequence[int] = DEFAULT_NORM_SCALES,
    norm_solutions_per_scale: int = 1,
    norm_options_per_squareclass: int = 4,
    norm_maximum_primes: int = 9,
    norm_equation_timeout: int = 10,
    norm_initialization_timeout: int = 180,
    calibration_options: int = 4,
    max_lifts: int = 12,
    shift_windows: Sequence[tuple[int, int]] = (),
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(output_dir, project, "campaign output")
    campaign = _safe_id(campaign_id)
    requested_lanes = tuple(dict.fromkeys(str(lane) for lane in lanes))
    unknown = sorted(set(requested_lanes) - set(LANE_MODULES))
    if unknown:
        raise ValueError(f"unknown discovery lanes: {unknown}")
    if norm_scale_shards < 1:
        raise ValueError("norm_scale_shards must be positive")
    if catalog_rows_per_task is not None and int(catalog_rows_per_task) < 1:
        raise ValueError("catalog_rows_per_task must be positive when provided")
    normalized_scales = tuple(dict.fromkeys(abs(int(value)) for value in norm_scales if int(value)))
    if not normalized_scales:
        raise ValueError("at least one nonzero norm scale is required")
    if min(
        int(norm_solutions_per_scale),
        int(norm_options_per_squareclass),
        int(norm_maximum_primes),
        int(norm_equation_timeout),
        int(norm_initialization_timeout),
        int(calibration_options),
        int(max_lifts),
    ) < 1:
        raise ValueError("discovery limits must be positive")
    normalized_windows = tuple(
        (int(lower), int(upper)) for lower, upper in shift_windows
    )
    if any(lower > upper for lower, upper in normalized_windows):
        raise ValueError("shift-window lower bound must not exceed upper bound")

    map_path = Path(map_path).resolve()
    _require_within(map_path, project, "character map")
    map_rows = load_jsonl(map_path)
    target_map = nonstandard_targets_by_base(map_rows)
    opportunities, snapshot_id = base_opportunity(target_map, db_path=db_path)
    ranked_bases = sorted(
        target_map,
        key=lambda base: (-opportunities.get(base, 0.0), base),
    )
    requested_bases = {int(value) for value in bases}
    if requested_bases:
        unknown_bases = sorted(requested_bases - set(target_map))
        if unknown_bases:
            raise ValueError(f"bases have no nonstandard character targets: {unknown_bases}")
        ranked_bases = [base for base in ranked_bases if base in requested_bases]
    if base_limit is not None:
        ranked_bases = ranked_bases[: max(0, int(base_limit))]
    allowed_bases = set(ranked_bases)

    manifests_dir = output_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    tasks: list[dict[str, Any]] = []
    seen_job_ids: set[str] = set()
    input_paths = {map_path}
    source_catalog_paths: set[Path] = set()
    catalog_shards_dir = output_dir / "catalog_shards"

    for catalog_index, raw_catalog in enumerate(catalogs):
        catalog = Path(raw_catalog).resolve()
        _require_within(catalog, project, "catalog")
        if not catalog.exists():
            continue
        input_paths.add(catalog)
        source_catalog_paths.add(catalog)
        catalog_rows = load_jsonl(catalog)
        rows_by_base: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in catalog_rows:
            rows_by_base[int(row["base_t"])].append(row)
        catalog_bases = set(rows_by_base)
        catalog_tag = _safe_id(catalog.stem)[:24]
        for base in ranked_bases:
            if base not in catalog_bases or base not in allowed_bases:
                continue
            catalog_units: list[tuple[Path, int, int]] = [
                (catalog, 0, len(rows_by_base[base]))
            ]
            if catalog_rows_per_task is not None:
                catalog_units = []
                chunk_size = int(catalog_rows_per_task)
                for unit_index, start in enumerate(
                    range(0, len(rows_by_base[base]), chunk_size)
                ):
                    chunk = rows_by_base[base][start:start + chunk_size]
                    shard = (
                        catalog_shards_dir
                        / f"c{catalog_index:02d}-{catalog_tag}-b{base:03d}-f{unit_index:04d}.jsonl"
                    )
                    _atomic_jsonl(shard, chunk)
                    input_paths.add(shard)
                    catalog_units.append((shard, unit_index, len(chunk)))
            for task_catalog, unit_index, unit_rows in catalog_units:
                field_tag = (
                    ""
                    if catalog_rows_per_task is None
                    else f"-f{unit_index:04d}"
                )
                for lane in requested_lanes:
                    variants: list[tuple[str, list[str]]]
                    if lane == "norm":
                        scale_groups = _balanced_groups(
                            normalized_scales,
                            norm_scale_shards,
                        )
                        variants = [
                            (
                                f"n{index:02d}",
                                [
                                    value
                                    for scale in scales
                                    for value in ("--scale", str(scale))
                                ],
                            )
                            for index, scales in enumerate(scale_groups)
                            if scales
                        ]
                    elif lane in {"product", "kernel"} and normalized_windows:
                        variants = [
                            (
                                f"w{index:02d}",
                                ["--shift-min", str(lower), "--shift-max", str(upper)],
                            )
                            for index, (lower, upper) in enumerate(normalized_windows)
                        ]
                    else:
                        variants = [("v00", [])]
                    for variant, variant_args in variants:
                        job_id = _unique_job_id(
                            f"{campaign}-{catalog_index:02d}-{catalog_tag}-b{base:03d}"
                            f"{field_tag}-{lane}-{variant}",
                            seen_job_ids,
                        )
                        stem = output_prefix / job_id
                        discoveries = stem.with_suffix(".discoveries.jsonl")
                        checked = stem.with_suffix(".checked.json")
                        report = stem.with_suffix(".report.json")
                        args = [
                            "--catalog", _relative(task_catalog, project),
                            "--map", _relative(map_path, project),
                            "--base", str(base),
                            *_lane_args(
                                lane,
                                norm_solutions_per_scale=norm_solutions_per_scale,
                                norm_options_per_squareclass=norm_options_per_squareclass,
                                norm_maximum_primes=norm_maximum_primes,
                                norm_equation_timeout=norm_equation_timeout,
                                norm_initialization_timeout=norm_initialization_timeout,
                                calibration_options=calibration_options,
                                max_lifts=max_lifts,
                            ),
                            *variant_args,
                            *(
                                ["--limit", str(max(0, int(max_seeds_per_task)))]
                                if max_seeds_per_task is not None
                                else []
                            ),
                            "--output", str(discoveries),
                            "--checked", str(checked),
                        ]
                        manifest = {
                            "job_id": job_id,
                            "module": LANE_MODULES[lane],
                            "args": args,
                            "inputs": [
                                {
                                    "path": _relative(path, project),
                                    "sha256": _sha256(path),
                                }
                                for path in (task_catalog, map_path)
                            ],
                            "artifacts": [
                                {"path": str(discoveries), "required": False},
                                {"path": str(checked), "required": False},
                            ],
                            "report": str(report),
                            "campaign": {
                                "campaign_id": campaign,
                                "catalog": _relative(task_catalog, project),
                                "source_catalog": _relative(catalog, project),
                                "catalog_unit": unit_index,
                                "catalog_rows": unit_rows,
                                "base_t": base,
                                "lane": lane,
                                "variant": variant,
                                "live_opportunity": opportunities.get(base, 0.0),
                                "target_snapshot_id": snapshot_id,
                            },
                        }
                        manifest_path = manifests_dir / f"{job_id}.json"
                        _atomic_json(manifest_path, manifest)
                        tasks.append({
                            "index": len(tasks),
                            "job_id": job_id,
                            "manifest": _relative(manifest_path, project),
                            "manifest_sha256": _sha256(manifest_path),
                            "catalog": _relative(task_catalog, project),
                            "source_catalog": _relative(catalog, project),
                            "catalog_unit": unit_index,
                            "catalog_rows": unit_rows,
                            "base_t": base,
                            "lane": lane,
                            "variant": variant,
                            "live_opportunity": opportunities.get(base, 0.0),
                        })

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "target_snapshot_id": snapshot_id,
        "project_root_name": project.name,
        "task_count": len(tasks),
        "lanes": list(requested_lanes),
        "norm_scale_shards": norm_scale_shards,
        "norm_scales": list(normalized_scales),
        "norm_solutions_per_scale": int(norm_solutions_per_scale),
        "norm_options_per_squareclass": int(norm_options_per_squareclass),
        "norm_maximum_primes": int(norm_maximum_primes),
        "norm_equation_timeout": int(norm_equation_timeout),
        "norm_initialization_timeout": int(norm_initialization_timeout),
        "calibration_options": int(calibration_options),
        "max_lifts": int(max_lifts),
        "shift_windows": [list(window) for window in normalized_windows],
        "max_seeds_per_task": max_seeds_per_task,
        "catalog_rows_per_task": catalog_rows_per_task,
        "ranked_bases": ranked_bases,
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
        "base_count": len({task["base_t"] for task in tasks}),
        "catalog_count": len(source_catalog_paths),
        "catalog_shard_count": len({task["catalog"] for task in tasks}),
        "lane_counts": {
            lane: sum(task["lane"] == lane for task in tasks)
            for lane in requested_lanes
        },
        "target_snapshot_id": snapshot_id,
        "live_opportunity": sum(
            opportunities.get(base, 0.0)
            for base in {task["base_t"] for task in tasks}
        ),
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _lane_args(
    lane: str,
    *,
    norm_solutions_per_scale: int = 1,
    norm_options_per_squareclass: int = 4,
    norm_maximum_primes: int = 9,
    norm_equation_timeout: int = 10,
    norm_initialization_timeout: int = 180,
    calibration_options: int = 4,
    max_lifts: int = 12,
) -> list[str]:
    common = [
        "--coefficient-limit", str(10**120),
        "--prime-limit", "10000",
    ]
    if lane == "linear":
        return [
            "--shift-min", "-100", "--shift-max", "100",
            "--options", str(int(calibration_options)), *common,
        ]
    if lane == "product":
        return [
            "--shift-min", "-24", "--shift-max", "24",
            "--degree", "2", "--degree", "3",
            "--options", str(int(calibration_options)),
            "--max-lifts", str(int(max_lifts)), *common,
        ]
    if lane == "kernel":
        return [
            "--shift-min", "-18", "--shift-max", "18",
            "--minimum-degree", "4",
            "--options-per-squareclass", "4",
            "--maximum-relations", str(1 << 20),
            "--options", str(int(calibration_options)),
            "--max-lifts", str(int(max_lifts)), *common,
        ]
    if lane == "norm":
        return [
            "--solutions-per-scale", str(int(norm_solutions_per_scale)),
            "--options-per-squareclass", str(int(norm_options_per_squareclass)),
            "--maximum-primes", str(int(norm_maximum_primes)),
            "--equation-timeout", str(int(norm_equation_timeout)),
            "--initialization-timeout", str(int(norm_initialization_timeout)),
            "--options", str(int(calibration_options)),
            "--max-lifts", str(int(max_lifts)), *common,
        ]
    raise ValueError(f"unknown lane {lane!r}")


def _balanced_groups(values: Sequence[int], count: int) -> list[list[int]]:
    groups = [[] for _ in range(count)]
    for index, value in enumerate(values):
        groups[index % count].append(int(value))
    return groups


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-_")
    if not safe:
        raise ValueError("campaign id contains no usable characters")
    return safe[:80]


def _unique_job_id(value: str, seen: set[str]) -> str:
    base = _safe_id(value)[:180]
    candidate = base
    counter = 1
    while candidate in seen:
        counter += 1
        candidate = f"{base[:170]}-{counter}"
    seen.add(candidate)
    return candidate


def _relative(path: Path, project: Path) -> str:
    return str(path.resolve().relative_to(project))


def _require_within(path: Path, project: Path, kind: str) -> None:
    if path != project and project not in path.parents:
        raise ValueError(f"{kind} escapes project root: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    Path(temporary).replace(path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        temporary = handle.name
    Path(temporary).replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--catalog", action="append", type=Path, default=[])
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--lane", action="append", choices=sorted(LANE_MODULES), default=[])
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--base-limit", type=int)
    parser.add_argument("--max-seeds-per-task", type=int)
    parser.add_argument("--catalog-rows-per-task", type=int)
    parser.add_argument("--norm-scale-shards", type=int, default=4)
    parser.add_argument("--norm-scale", action="append", type=int, default=[])
    parser.add_argument("--norm-solutions-per-scale", type=int, default=1)
    parser.add_argument("--norm-options-per-squareclass", type=int, default=4)
    parser.add_argument("--norm-maximum-primes", type=int, default=9)
    parser.add_argument("--norm-equation-timeout", type=int, default=10)
    parser.add_argument("--norm-initialization-timeout", type=int, default=180)
    parser.add_argument("--calibration-options", type=int, default=4)
    parser.add_argument("--max-lifts", type=int, default=12)
    parser.add_argument(
        "--shift-window",
        action="append",
        nargs=2,
        type=int,
        metavar=("MIN", "MAX"),
        default=[],
        help="disjoint product/kernel shift interval; repeat for more windows",
    )
    args = parser.parse_args()
    summary = plan_campaign(
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        catalogs=args.catalog or DEFAULT_CATALOGS,
        map_path=args.map,
        db_path=args.db,
        lanes=args.lane or DEFAULT_LANES,
        bases=args.base,
        base_limit=args.base_limit,
        max_seeds_per_task=args.max_seeds_per_task,
        catalog_rows_per_task=args.catalog_rows_per_task,
        norm_scale_shards=args.norm_scale_shards,
        norm_scales=args.norm_scale or DEFAULT_NORM_SCALES,
        norm_solutions_per_scale=args.norm_solutions_per_scale,
        norm_options_per_squareclass=args.norm_options_per_squareclass,
        norm_maximum_primes=args.norm_maximum_primes,
        norm_equation_timeout=args.norm_equation_timeout,
        norm_initialization_timeout=args.norm_initialization_timeout,
        calibration_options=args.calibration_options,
        max_lifts=args.max_lifts,
        shift_windows=args.shift_window,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Plan target-sharded direct certification from merged character discoveries."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from cloud.plan_character_campaign import (
    PROJECT,
    _atomic_json,
    _now,
    _relative,
    _require_within,
    _safe_id,
    _sha256,
    load_jsonl,
)
from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import load_owned_pairs


def plan_certification(
    discoveries_path: str | Path,
    *,
    campaign_id: str,
    output_dir: str | Path,
    db_path: str | Path = DEFAULT_DB,
    pilot_only: bool = True,
    pilot_by_key: bool = False,
    target_limit: int | None = None,
    alternatives_per_target: int = 3,
    pilot_root_rank: int = 0,
    prime_limit: int = 10000,
    minimum_opportunity: float = 0.0,
    exclude_discoveries_paths: Sequence[str | Path] = (),
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    discoveries_path = Path(discoveries_path).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(discoveries_path, project, "discoveries")
    _require_within(output_dir, project, "campaign output")
    if alternatives_per_target < 1:
        raise ValueError("alternatives_per_target must be positive")
    if pilot_root_rank < 0:
        raise ValueError("pilot_root_rank must be nonnegative")
    if prime_limit < 2:
        raise ValueError("prime_limit must be at least two")
    if pilot_by_key and not pilot_only:
        raise ValueError("pilot_by_key is only valid for pilot certification")
    campaign = _safe_id(campaign_id)
    input_rows = load_jsonl(discoveries_path)
    excluded_seed_keys = _excluded_seed_keys(exclude_discoveries_paths)
    rows = [row for row in input_rows if _seed_key(row) not in excluded_seed_keys]
    grouped: dict[int | CharacterGroup, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        target_t = int(row["starting_target_t"])
        group: int | CharacterGroup = target_t
        if pilot_by_key:
            group = (
                target_t,
                int(row["base_t"]),
                int(row["norm_squareclass"]),
            )
        grouped[group].append(dict(row))

    with Ledger(db_path) as ledger:
        live = ledger.latest_targets()
        owned = load_owned_pairs(project) | ledger.owned_pairs()
    snapshot_id = (
        str(next(iter(live.values()))["snapshot_id"])
        if live
        else None
    )

    ranked: list[
        tuple[float, int, CharacterGroup | None, list[dict[str, Any]]]
    ] = []
    reserved_pilot_roots: dict[int, set[int]] = defaultdict(set)
    grouped_items = list(grouped.items())
    grouped_items.sort(key=lambda item: (
        item[0] if isinstance(item[0], tuple) else (item[0], -1, -1)
    ))
    for group, candidates in grouped_items:
        target_t = group[0] if isinstance(group, tuple) else group
        character_group = group if isinstance(group, tuple) else None
        assigned_pilot_root: int | None = None
        if pilot_by_key:
            roots = tuple(range(
                2 if int(candidates[0]["norm_squareclass"]) < 0 else 0,
                25,
                4,
            ))
            root_values = {
                root: _pair_value(target_t, root, live, owned)
                for root in roots
            }
            available = tuple(
                root for root in roots
                if root not in reserved_pilot_roots[target_t]
            ) or roots
            ranked_roots = sorted(
                available,
                key=lambda root: (
                    root_values[root],
                    -abs(root - 12),
                    -root,
                ),
                reverse=True,
            )
            assigned_pilot_root = ranked_roots[
                min(int(pilot_root_rank), len(ranked_roots) - 1)
            ]
            reserved_pilot_roots[target_t].add(assigned_pilot_root)
        scored = []
        for row in candidates:
            roots = tuple(range(
                2 if int(row["norm_squareclass"]) < 0 else 0,
                25,
                4,
            ))
            root_values = {
                root: _pair_value(target_t, root, live, owned)
                for root in roots
            }
            pilot_root = assigned_pilot_root
            if pilot_root is None:
                pilot_root = max(
                    roots,
                    key=lambda root: (
                        root_values[root],
                        -abs(root - 12),
                        -root,
                    ),
                )
            active = dict(row)
            active["pilot_target_r"] = pilot_root
            active["pilot_forecast_points"] = root_values[pilot_root]
            opportunity = sum(root_values.values())
            scored.append((
                opportunity,
                root_values[pilot_root],
                -_field_disc(active),
                -_seed_height(active),
                active,
            ))
        scored.sort(key=lambda item: (
            -item[0],
            -item[1],
            -item[2],
            -item[3],
            _seed_key(item[4]),
        ))
        selected = [item[4] for item in scored[:alternatives_per_target]]
        if pilot_only and not pilot_by_key:
            selected = selected[:1]
        opportunity = max((item[0] for item in scored), default=0.0)
        if opportunity > float(minimum_opportunity):
            ranked.append((opportunity, target_t, character_group, selected))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2] or (item[1], -1, -1)))
    if target_limit is not None:
        ranked = ranked[: max(0, int(target_limit))]

    sources_dir = output_dir / "sources"
    manifests_dir = output_dir / "manifests"
    sources_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    tasks = []
    for opportunity, target_t, character_group, selected in ranked:
        mode = "pilot" if pilot_only else "full"
        key_tag = ""
        if character_group is not None:
            _, base_t, norm_squareclass = character_group
            squareclass_tag = (
                f"m{abs(norm_squareclass)}"
                if norm_squareclass < 0
                else f"p{norm_squareclass}"
            )
            key_tag = f"-b{base_t:03d}-q{squareclass_tag}"
        job_id = _safe_id(f"{campaign}-t{target_t:05d}{key_tag}-{mode}")
        source = sources_dir / f"24T{target_t}{key_tag}.jsonl"
        _atomic_text(
            source,
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in selected),
        )
        stem = output_prefix / job_id
        manifest_output = stem.with_suffix(".candidates.jsonl")
        payload = stem.with_suffix(".txt")
        worker_db = stem.with_suffix(".sqlite3")
        report = stem.with_suffix(".report.json")
        args = [
            "--discoveries", _relative(source, project),
            "--manifest", str(manifest_output),
            "--payload", str(payload),
            "--db", str(worker_db),
            "--certify",
            "--options-per-signature", "32",
            "--coefficient-limit", str(10**120),
            "--prime-limit", str(int(prime_limit)),
        ]
        if pilot_only:
            args.append("--pilot-only")
        manifest = {
            "job_id": job_id,
            "module": "routeA.build_discovered_character_campaign",
            "args": args,
            "inputs": [{
                "path": _relative(source, project),
                "sha256": _sha256(source),
            }],
            "artifacts": [
                {"path": str(manifest_output), "required": False},
                {"path": str(payload), "required": False},
            ],
            "report": str(report),
            "campaign": {
                "campaign_id": campaign,
                "target_t": target_t,
                "mode": mode,
                "pilot_by_key": bool(pilot_by_key),
                "pilot_root_rank": int(pilot_root_rank),
                "character_key": (
                    {
                        "base_t": character_group[1],
                        "norm_squareclass": character_group[2],
                    }
                    if character_group is not None
                    else None
                ),
                "live_opportunity": opportunity,
                "target_snapshot_id": snapshot_id,
            },
        }
        manifest_path = manifests_dir / f"{job_id}.json"
        _atomic_json(manifest_path, manifest)
        task = {
            "index": len(tasks),
            "job_id": job_id,
            "manifest": _relative(manifest_path, project),
            "manifest_sha256": _sha256(manifest_path),
            "target_t": target_t,
            "lane": mode,
            "variant": mode,
            "live_opportunity": opportunity,
        }
        if character_group is not None:
            task["base_t"] = character_group[1]
            task["norm_squareclass"] = character_group[2]
            task["source_alternatives"] = len(selected)
        tasks.append(task)

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "target_snapshot_id": snapshot_id,
        "task_count": len(tasks),
        "mode": "pilot" if pilot_only else "full",
        "pilot_by_key": bool(pilot_by_key),
        "pilot_root_rank": int(pilot_root_rank),
        "prime_limit": int(prime_limit),
        "source_discoveries": _relative(discoveries_path, project),
        "inputs": [{
            "path": _relative(discoveries_path, project),
            "size": discoveries_path.stat().st_size,
            "sha256": _sha256(discoveries_path),
        }],
        "tasks": tasks,
    }
    matrix_path = output_dir / "matrix.json"
    _atomic_json(matrix_path, matrix)
    summary = {
        "campaign_id": campaign,
        "mode": matrix["mode"],
        "pilot_by_key": bool(pilot_by_key),
        "pilot_root_rank": int(pilot_root_rank),
        "prime_limit": int(prime_limit),
        "matrix": _relative(matrix_path, project),
        "source_discoveries": len(rows),
        "input_discoveries": len(input_rows),
        "excluded_discoveries": len(input_rows) - len(rows),
        "source_targets": len({int(row["starting_target_t"]) for row in rows}),
        "source_groups": len(grouped),
        "source_character_keys": len({
            (
                int(row["starting_target_t"]),
                int(row["base_t"]),
                int(row["norm_squareclass"]),
            )
            for row in rows
        }),
        "task_count": len(tasks),
        "live_opportunity": sum(task["live_opportunity"] for task in tasks),
        "target_snapshot_id": snapshot_id,
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _pair_value(
    target_t: int,
    root: int,
    live: Mapping[tuple[int, int], Mapping[str, Any]],
    owned: set[tuple[int, int]],
) -> float:
    pair = int(target_t), int(root)
    row = live.get(pair)
    if row is None or pair in owned or bool(row["baseline"]):
        return 0.0
    return 2.0 ** (-int(row["team_count"]))


CharacterGroup = tuple[int, int, int]


def _field_disc(row: Mapping[str, Any]) -> int:
    value = row.get("disc_abs")
    if value is None:
        return 10**1000
    try:
        disc = abs(int(value))
    except (TypeError, ValueError):
        return 10**1000
    return disc if disc > 1 else 10**1000


def _seed_height(row: Mapping[str, Any]) -> int:
    values = row.get("seed_coefficients")
    if values is None:
        return abs(int(row.get("shift", 0)))
    height = 0
    for value in values:
        text = str(value)
        for part in text.split("/", 1):
            try:
                height = max(height, abs(int(part)))
            except ValueError:
                height = max(height, len(part))
    return height


def _seed_key(row: Mapping[str, Any]) -> str:
    return json.dumps({
        "base_t": int(row["base_t"]),
        "label": str(row["label"]),
        "norm_squareclass": int(row["norm_squareclass"]),
        "seed_coefficients": row.get("seed_coefficients"),
        "shift": row.get("shift"),
    }, sort_keys=True, separators=(",", ":"))


def _excluded_seed_keys(paths: Sequence[str | Path]) -> set[str]:
    keys: set[str] = set()
    for raw in paths:
        path = Path(raw)
        sources = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        if not sources or any(not source.is_file() for source in sources):
            raise ValueError(f"excluded discovery source does not exist: {path}")
        for source in sources:
            keys.update(_seed_key(row) for row in load_jsonl(source))
    return keys


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temporary = handle.name
    Path(temporary).replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discoveries", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--full", action="store_true")
    parser.add_argument(
        "--pilot-by-key",
        action="store_true",
        help=(
            "plan one pilot task per (target, base, norm-squareclass) key and "
            "retain several independent seed alternatives"
        ),
    )
    parser.add_argument("--target-limit", type=int)
    parser.add_argument("--alternatives-per-target", type=int, default=3)
    parser.add_argument(
        "--pilot-root-rank",
        type=int,
        default=0,
        help="try the Nth-best still-unowned root signature for each pilot key",
    )
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--minimum-opportunity", type=float, default=0.0)
    parser.add_argument(
        "--exclude-discoveries",
        action="append",
        type=Path,
        default=[],
        help="prior discovery JSONL or sources directory to exclude (repeatable)",
    )
    args = parser.parse_args()
    summary = plan_certification(
        args.discoveries,
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        db_path=args.db,
        pilot_only=not args.full,
        pilot_by_key=args.pilot_by_key,
        target_limit=args.target_limit,
        alternatives_per_target=args.alternatives_per_target,
        pilot_root_rank=args.pilot_root_rank,
        prime_limit=args.prime_limit,
        minimum_opportunity=args.minimum_opportunity,
        exclude_discoveries_paths=args.exclude_discoveries,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

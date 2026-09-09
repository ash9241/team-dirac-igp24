#!/usr/bin/env python3
"""Prioritize exact action tasks by the real signatures they can still score.

The older action planners rank a task by every open signature of its target
group.  A concrete source field has a fixed complex-conjugation class, so most
of that group-wide opportunity is often unreachable.  GAP joint action
profiles describe the exact target cycles for every possible source class.
This planner conditions those profiles on the source field's real-root count
and ranks only tasks that can still reach an unowned, non-baseline pair.

This command only emits a reindexed matrix.  It does not launch compute or
submit benchmark polynomials.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from cloud.generator_worker import PROJECT
from cloud.package_character_campaign import _sha256
from routeA.classify_degree24_pair_factors import involution_cycle
from routeA.ledger import DEFAULT_DB
from routeA.select_exact_harvest import LiveTarget, load_authoritative_state


@dataclass(frozen=True)
class SignatureForecast:
    source_r: int
    matching_class_mass: int
    expected_score: float
    maximum_score: float
    minimum_score: float
    possible_root_sets: tuple[tuple[int, ...], ...]


def forecast_task_signatures(
    *,
    source_r: int,
    target_t: int,
    profile: Mapping[str, Any],
    targets: Mapping[tuple[int, int], LiveTarget],
    owned_pairs: set[tuple[int, int]],
) -> SignatureForecast:
    """Forecast one unambiguous exact-action task from its GAP profile."""

    orbit_targets = tuple(int(value) for value in profile["orbit_targets"])
    if not orbit_targets or any(value != int(target_t) for value in orbit_targets):
        raise ValueError(
            f"24T{profile.get('source_t')} profile is not unambiguous for "
            f"target 24T{target_t}"
        )
    wanted_cycle = tuple(involution_cycle(int(source_r)))
    outcomes: list[tuple[int, tuple[int, ...], float]] = []
    for row in profile.get("profiles") or []:
        source_cycle = tuple(int(value) for value in row["source_cycle"])
        if source_cycle != wanted_cycle:
            continue
        mass = int(row.get("class_size") or 0)
        if mass < 1:
            raise ValueError("joint profile class_size must be positive")
        roots = tuple(sorted({
            sum(int(length) == 1 for length in cycle)
            for cycle in row["orbit_cycles"]
        }))
        score = sum(
            target.immediate_value
            for root in roots
            if (target := targets.get((int(target_t), int(root)))) is not None
            and not target.baseline
            and (int(target_t), int(root)) not in owned_pairs
        )
        outcomes.append((mass, roots, score))
    if not outcomes:
        raise ValueError(
            f"24T{profile.get('source_t')} has no involution profile for r={source_r}"
        )
    total_mass = sum(row[0] for row in outcomes)
    scores = [row[2] for row in outcomes]
    return SignatureForecast(
        source_r=int(source_r),
        matching_class_mass=total_mass,
        expected_score=sum(mass * score for mass, _, score in outcomes) / total_mass,
        maximum_score=max(scores),
        minimum_score=min(scores),
        possible_root_sets=tuple(sorted({roots for _, roots, _ in outcomes})),
    )


def prioritize_tasks(
    tasks: Iterable[Mapping[str, Any]],
    profiles: Mapping[int, Mapping[str, Any]],
    targets: Mapping[tuple[int, int], LiveTarget],
    owned_pairs: set[tuple[int, int]],
    source_row: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    excluded_job_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    excluded_job_ids = excluded_job_ids or set()
    selected: list[dict[str, Any]] = []
    counts = {
        "input_tasks": 0,
        "excluded_completed_tasks": 0,
        "missing_profile_tasks": 0,
        "zero_signature_opportunity_tasks": 0,
        "eligible_tasks": 0,
    }
    for raw in tasks:
        counts["input_tasks"] += 1
        job_id = str(raw.get("job_id") or "")
        if not job_id:
            raise ValueError("action task has no job_id")
        if job_id in excluded_job_ids:
            counts["excluded_completed_tasks"] += 1
            continue
        source_t = int(raw["source_t"])
        target_t = int(raw["target_t"])
        profile = profiles.get(source_t)
        if profile is None:
            counts["missing_profile_tasks"] += 1
            continue
        source = source_row(raw)
        if int(source["source_t"]) != source_t:
            raise ValueError(f"source row mismatch for {job_id}")
        forecast = forecast_task_signatures(
            source_r=int(source["source_r"]),
            target_t=target_t,
            profile=profile,
            targets=targets,
            owned_pairs=owned_pairs,
        )
        if forecast.maximum_score <= 0:
            counts["zero_signature_opportunity_tasks"] += 1
            continue
        task = dict(raw)
        task["source_r"] = forecast.source_r
        task["signature_expected_score"] = forecast.expected_score
        task["signature_maximum_score"] = forecast.maximum_score
        task["signature_minimum_score"] = forecast.minimum_score
        task["signature_matching_class_mass"] = forecast.matching_class_mass
        task["signature_possible_root_sets"] = [
            list(values) for values in forecast.possible_root_sets
        ]
        selected.append(task)
        counts["eligible_tasks"] += 1
    selected.sort(key=lambda row: (
        -float(row["signature_expected_score"]),
        -float(row["signature_maximum_score"]),
        int(row["target_t"]),
        int(row["source_t"]),
        str(row["job_id"]),
    ))
    return selected, counts


def build_signature_priority_matrix(
    matrix_path: str | Path,
    profile_path: str | Path,
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    limit: int | None = None,
    exclude_matrices: Sequence[str | Path] = (),
    exclude_candidates: Sequence[str | Path] = (),
) -> dict[str, Any]:
    matrix_path = Path(matrix_path).resolve()
    profile_path = Path(profile_path).resolve()
    output_path = Path(output_path).resolve()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    tasks = matrix.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("source matrix has no tasks")
    profiles: dict[int, dict[str, Any]] = {}
    for row in _load_jsonl(profile_path):
        source_t = int(row["source_t"])
        if source_t in profiles:
            raise ValueError(f"duplicate joint profile for 24T{source_t}")
        profiles[source_t] = row
    targets, owned, _, state = load_authoritative_state(db_path)
    excluded = _excluded_job_ids(exclude_matrices, exclude_candidates)
    source_cache: dict[Path, dict[str, Any]] = {}

    def load_source(task: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest_path = _project_path(task["manifest"])
        if _sha256(manifest_path) != str(task.get("manifest_sha256") or ""):
            raise ValueError(f"manifest checksum changed: {task['job_id']}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for input_row in manifest.get("inputs") or []:
            path = _project_path(input_row["path"])
            if path.suffix != ".jsonl":
                continue
            if _sha256(path) != str(input_row.get("sha256") or ""):
                raise ValueError(f"source checksum changed: {path}")
            if path not in source_cache:
                rows = _load_jsonl(path)
                if len(rows) == 1 and "source_t" in rows[0] and "source_r" in rows[0]:
                    source_cache[path] = rows[0]
            if path in source_cache:
                return source_cache[path]
        raise ValueError(f"no source field row in manifest for {task['job_id']}")

    ranked, counts = prioritize_tasks(
        tasks,
        profiles,
        targets,
        owned,
        load_source,
        excluded_job_ids=excluded,
    )
    if limit is not None:
        ranked = ranked[: max(0, int(limit))]
    if not ranked:
        raise ValueError("no signature-positive action tasks remain")
    output = dict(matrix)
    output["parent_matrix"] = str(matrix_path)
    output["signature_profile"] = str(profile_path)
    output["priority_kind"] = "exact-involution-signature-expected-score"
    output["target_snapshot_id"] = state["snapshot_id"]
    output["tasks"] = [dict(task, index=index) for index, task in enumerate(ranked)]
    output["task_count"] = len(ranked)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(output_path, output)
    summary = {
        "output": str(output_path),
        "source_matrix": str(matrix_path),
        "joint_profile": str(profile_path),
        "snapshot_id": state["snapshot_id"],
        "excluded_job_ids": len(excluded),
        "selected_tasks": len(ranked),
        "selected_target_groups": len({int(row["target_t"]) for row in ranked}),
        "summed_expected_score": sum(
            float(row["signature_expected_score"]) for row in ranked
        ),
        "summed_maximum_score": sum(
            float(row["signature_maximum_score"]) for row in ranked
        ),
        "counts": counts,
    }
    _atomic_json(output_path.with_suffix(output_path.suffix + ".report.json"), summary)
    return summary


def _excluded_job_ids(
    matrices: Sequence[str | Path], candidates: Sequence[str | Path]
) -> set[str]:
    excluded: set[str] = set()
    for path in matrices:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        excluded.update(str(row["job_id"]) for row in value.get("tasks") or [])
    for path in candidates:
        for row in _load_jsonl(path):
            job_id = row.get("cloud_job_id")
            if job_id:
                excluded.add(str(job_id))
    return excluded


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _project_path(raw: str | Path) -> Path:
    path = (PROJECT / str(raw)).resolve()
    if path != PROJECT and PROJECT not in path.parents:
        raise ValueError(f"path escapes project root: {raw}")
    return path


def _atomic_json(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("profiles", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--exclude-matrix", action="append", type=Path, default=[])
    parser.add_argument("--exclude-candidates", action="append", type=Path, default=[])
    args = parser.parse_args()
    summary = build_signature_priority_matrix(
        args.matrix,
        args.profiles,
        args.output,
        db_path=args.db,
        limit=args.limit,
        exclude_matrices=args.exclude_matrix,
        exclude_candidates=args.exclude_candidates,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Plan an iterative exact degree-24 unordered-pair closure frontier.

The input rows are exact candidates emitted by prior generators, rather than
server-exported source rows.  This planner validates those rows fail-closed,
normalizes them to the source catalog schema expected by the pair-resolvent
generator, and schedules every unprocessed source for which GAP provides an
unambiguous unordered-pair action.  Ownership is used only to report current
score opportunity; it never removes a mathematically useful closure seed.

No credentials are copied into a manifest and this module never launches a
cloud job or submits a polynomial.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cloud.plan_character_campaign import (
    PROJECT,
    _atomic_json,
    _atomic_jsonl,
    _now,
    _relative,
    _require_within,
    _safe_id,
    _sha256,
)
from routeA.ledger import DEFAULT_DB, Ledger, candidate_hash, canonical_coefficients
from routeA.scheduler import load_owned_pairs


_HASH_RE = re.compile(r"[0-9a-f]{64}")
_EXACT_DISC_KEYS = ("field_disc_abs", "nfdisc_abs", "estimated_nfdisc_abs")


def load_candidate_rows(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    """Load candidate JSONLs while retaining deterministic source diagnostics."""

    rows: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path}:{line_number}") from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        f"candidate row is not an object in {path}:{line_number}"
                    )
                row = dict(value)
                row["_closure_input_path"] = str(path)
                row["_closure_input_line"] = line_number
                rows.append(row)
    return rows


def load_processed_source_hashes(
    matrix_paths: Sequence[str | Path],
) -> set[str]:
    """Load source hashes from caller-certified completed campaign matrices.

    A matrix records what was scheduled, not whether it completed.  Callers
    should therefore pass only matrices whose successful task set they have
    already audited.  Successful generated pair rows also infer their parent
    source hash automatically in :func:`normalize_closure_sources`.
    """

    processed: set[str] = set()
    for raw_path in matrix_paths:
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            matrix = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid processed matrix JSON: {path}") from exc
        tasks = matrix.get("tasks") if isinstance(matrix, Mapping) else None
        if not isinstance(tasks, list):
            raise ValueError(f"processed matrix has no task list: {path}")
        for index, task in enumerate(tasks):
            if not isinstance(task, Mapping):
                raise ValueError(f"processed matrix task {index} is not an object: {path}")
            value = str(task.get("source_candidate_hash") or "")
            if _HASH_RE.fullmatch(value) is None:
                raise ValueError(
                    f"processed matrix task {index} has no canonical source hash: {path}"
                )
            processed.add(value)
    return processed


def normalize_closure_sources(
    rows: Iterable[Mapping[str, Any]],
    *,
    processed_hashes: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], set[str]]:
    """Validate exact generated rows and return an unprocessed source frontier.

    Boolean proof gates are deliberately explicit.  A row is ineligible unless
    it is exact, submission-ready, and locally irreducible.  Once a row claims
    those gates, malformed coefficients, hashes, labels, roots, or exact field
    discriminants are treated as integrity failures and abort planning.

    Returns ``(normalized_sources, counts, effective_processed_hashes)``.
    Parent hashes from valid pair-resolvent outputs are included in the
    effective processed set, which makes accumulated generation JSONLs usable
    directly as an iterative closure frontier.
    """

    materialized = [dict(row) for row in rows]
    counts: Counter[str] = Counter()
    explicit_processed = set(processed_hashes or ())
    for value in explicit_processed:
        if _HASH_RE.fullmatch(str(value)) is None:
            raise ValueError(f"invalid processed candidate hash: {value!r}")

    validated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    inferred_processed: set[str] = set()
    for raw in materialized:
        counts["input_rows"] += 1
        if raw.get("exact_compatibility_proven") is not True:
            counts["rejected_not_exact"] += 1
            continue
        if raw.get("submission_ready") is not True:
            counts["rejected_not_submission_ready"] += 1
            continue
        if raw.get("local_irreducible") is not True:
            counts["rejected_not_locally_irreducible"] += 1
            continue

        source = _normalize_claimed_exact_row(raw)
        validated.append((raw, source))
        counts["validated_exact_rows"] += 1
        parent_hash = _pair_parent_hash(raw)
        if parent_hash is not None:
            inferred_processed.add(parent_hash)

    effective_processed = explicit_processed | inferred_processed
    counts["explicit_processed_hashes"] = len(explicit_processed)
    counts["inferred_processed_hashes"] = len(inferred_processed)
    counts["effective_processed_hashes"] = len(effective_processed)

    unique: dict[str, dict[str, Any]] = {}
    for _, source in validated:
        key = str(source["candidate_hash"])
        previous = unique.get(key)
        if previous is not None:
            counts["duplicate_candidate_rows"] += 1
            if _source_identity(previous) != _source_identity(source):
                raise ValueError(f"exact candidate {key} has conflicting source labels")
            continue
        unique[key] = source

    frontier: list[dict[str, Any]] = []
    for key, source in unique.items():
        if key in effective_processed:
            counts["rejected_already_processed"] += 1
            continue
        frontier.append(source)
    frontier.sort(
        key=lambda row: (
            int(row["source_t"]),
            int(row["disc_abs"]),
            int(row["source_r"]),
            str(row["candidate_hash"]),
        )
    )
    counts["unique_validated_candidates"] = len(unique)
    counts["normalized_frontier_sources"] = len(frontier)
    return frontier, dict(sorted(counts.items())), effective_processed


def _normalize_claimed_exact_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    location = _row_location(raw)
    try:
        coefficients_line = canonical_coefficients(raw["coefficients"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid exact degree-24 coefficients at {location}") from exc
    computed_hash = candidate_hash(coefficients_line)
    supplied_hash = str(raw.get("candidate_hash") or "")
    if _HASH_RE.fullmatch(supplied_hash) is None:
        raise ValueError(f"exact candidate has no canonical hash at {location}")
    if supplied_hash != computed_hash:
        raise ValueError(f"exact candidate hash mismatch at {location}: {supplied_hash}")

    try:
        source_t = int(raw["target_t"])
        source_r = int(raw["target_r"])
        local_roots = int(raw["local_root_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"exact candidate has no valid target/root label at {location}") from exc
    if source_t < 1:
        raise ValueError(f"exact candidate has invalid transitive label at {location}")
    if source_r < 0 or source_r > 24 or source_r % 2:
        raise ValueError(f"exact candidate has invalid degree-24 root count at {location}")
    if local_roots != source_r:
        raise ValueError(f"exact candidate local/target root mismatch at {location}")

    disc_abs = _exact_disc_abs(raw, location=location)
    return {
        "source_t": source_t,
        "source_r": source_r,
        "coefficients": [int(value) for value in coefficients_line.split(",")],
        "disc_abs": disc_abs,
        "candidate_hash": computed_hash,
        "evidence": "generated-exact-label-pair-closure-seed",
        "exact_compatibility_proven": True,
        "closure_seed_recipe_family": str(raw.get("recipe_family") or "unknown"),
        "closure_seed_recipe_lineage": str(raw.get("recipe_lineage") or ""),
    }


def _exact_disc_abs(raw: Mapping[str, Any], *, location: str) -> int:
    values: list[tuple[str, int]] = []
    for key in _EXACT_DISC_KEYS:
        raw_value = raw.get(key)
        if raw_value is None:
            continue
        try:
            value = abs(int(raw_value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid exact discriminant {key} at {location}") from exc
        if value <= 1:
            raise ValueError(f"invalid exact discriminant {key} at {location}")
        values.append((key, value))
    if not values:
        raise ValueError(f"exact candidate has no certified field discriminant at {location}")
    unique = {value for _, value in values}
    if len(unique) != 1:
        raise ValueError(f"exact candidate has conflicting field discriminants at {location}")
    return values[0][1]


def _pair_parent_hash(raw: Mapping[str, Any]) -> str | None:
    """Infer a successfully processed parent only from exact pair output metadata."""

    family = str(raw.get("recipe_family") or "")
    if family not in {
        "degree24_pair_sum_resolvent",
        "degree24_pair_sum_product_resolvent",
    }:
        return None
    parameters = raw.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    value = str(parameters.get("source_candidate_hash") or "")
    if not value:
        return None
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"pair-resolvent row has invalid parent source hash: {value!r}")
    lineage = str(raw.get("recipe_lineage") or "")
    if lineage and not lineage.endswith(f":{value}"):
        raise ValueError("pair-resolvent row lineage conflicts with parent source hash")
    return value


def _source_identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(row["source_t"]),
        int(row["source_r"]),
        tuple(int(value) for value in row["coefficients"]),
        int(row["disc_abs"]),
    )


def _row_location(raw: Mapping[str, Any]) -> str:
    path = raw.get("_closure_input_path", "<memory>")
    line = raw.get("_closure_input_line", "?")
    return f"{path}:{line}"


def load_unambiguous_unordered_pair_actions(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, int]]:
    """Validate and retain only GAP-certified unambiguous unordered actions."""

    actions: dict[int, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for raw in rows:
        counts["input_orbit_rows"] += 1
        if raw.get("evidence") != "gap-exact-pair-action":
            counts["rejected_non_unordered_pair_action"] += 1
            continue
        target = raw.get("unambiguous_target_t")
        if target is None:
            counts["rejected_ambiguous_pair_action"] += 1
            continue
        try:
            source_t = int(raw["source_t"])
            target_t = int(target)
            orbit_count = int(raw["orbit_count"])
            orbit_targets = [int(value) for value in raw["orbit_targets"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("malformed exact unordered-pair orbit row") from exc
        if source_t < 1 or target_t < 1 or orbit_count < 1:
            raise ValueError(f"invalid exact pair action for 24T{source_t}")
        if len(orbit_targets) != orbit_count or set(orbit_targets) != {target_t}:
            raise ValueError(f"unambiguous pair action is internally inconsistent for 24T{source_t}")
        action = {
            key: value
            for key, value in raw.items()
            if not str(key).startswith("_closure_input_")
        }
        previous = actions.get(source_t)
        if previous is not None and previous != action:
            raise ValueError(f"conflicting unordered-pair actions for 24T{source_t}")
        if previous is not None:
            counts["duplicate_orbit_rows"] += 1
            continue
        actions[source_t] = action
    counts["eligible_unambiguous_actions"] = len(actions)
    counts["eligible_self_actions"] = sum(
        int(source_t == int(row["unambiguous_target_t"]))
        for source_t, row in actions.items()
    )
    return actions, dict(sorted(counts.items()))


def plan_pair_closure_campaign(
    candidate_paths: Sequence[str | Path],
    orbit_map_path: str | Path,
    *,
    campaign_id: str,
    output_dir: str | Path,
    db_path: str | Path | None = DEFAULT_DB,
    processed_matrix_paths: Sequence[str | Path] = (),
    generation: int = 1,
    task_limit: int | None = None,
    factor_timeout: int = 7_200,
    gp_path: str | Path = "/usr/bin/gp",
    resolvent_kind: str = "pair-sum",
    product_weight: int = 1,
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    """Build a credential-free closure matrix without launching it."""

    if not candidate_paths:
        raise ValueError("at least one exact candidate JSONL is required")
    if generation < 1:
        raise ValueError("closure generation must be positive")
    if task_limit is not None and int(task_limit) < 1:
        raise ValueError("task_limit must be positive when provided")
    if factor_timeout < 1:
        raise ValueError("factor_timeout must be positive")
    if resolvent_kind not in {"pair-sum", "pair-sum-product"}:
        raise ValueError("resolvent_kind must be pair-sum or pair-sum-product")
    if int(product_weight) == 0:
        raise ValueError("product_weight must be nonzero")

    project = Path(project).resolve()
    output_dir = Path(output_dir).resolve()
    orbit_map_path = Path(orbit_map_path).resolve()
    candidate_files = [Path(path).resolve() for path in candidate_paths]
    processed_files = [Path(path).resolve() for path in processed_matrix_paths]
    _require_within(output_dir, project, "closure campaign output")
    _require_within(orbit_map_path, project, "unordered-pair orbit map")
    for path in candidate_files:
        _require_within(path, project, "exact candidate input")
    for path in processed_files:
        _require_within(path, project, "processed campaign matrix")

    campaign = _safe_id(campaign_id)
    candidate_rows = load_candidate_rows(candidate_files)
    explicit_processed = load_processed_source_hashes(processed_files)
    sources, normalization_counts, effective_processed = normalize_closure_sources(
        candidate_rows,
        processed_hashes=explicit_processed,
    )
    orbit_rows = load_candidate_rows([orbit_map_path])
    actions, action_counts = load_unambiguous_unordered_pair_actions(orbit_rows)

    live, owned, snapshot_id = _authoritative_state(db_path, project=project)
    eligible: list[dict[str, Any]] = []
    missing_action = 0
    for source in sources:
        source_t = int(source["source_t"])
        action = actions.get(source_t)
        if action is None:
            missing_action += 1
            continue
        target_t = int(action["unambiguous_target_t"])
        opportunity = _target_opportunity(target_t, live, owned)
        source_pair = source_t, int(source["source_r"])
        eligible.append(
            {
                "source": source,
                "action": action,
                "target_t": target_t,
                "live_opportunity": opportunity,
                "source_pair_owned": source_pair in owned,
            }
        )
    eligible.sort(
        key=lambda item: (
            -float(item["live_opportunity"]),
            int(item["source"]["disc_abs"]),
            int(item["source"]["source_t"]),
            str(item["source"]["candidate_hash"]),
        )
    )
    eligible_before_limit = len(eligible)
    if task_limit is not None:
        eligible = eligible[: int(task_limit)]

    normalized_path = output_dir / "normalized_frontier_sources.jsonl"
    _atomic_jsonl(normalized_path, sources)
    sources_dir = output_dir / "sources"
    maps_dir = output_dir / "orbit_maps"
    manifests_dir = output_dir / "manifests"
    for directory in (sources_dir, maps_dir, manifests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    seen_job_ids: set[str] = set()
    for item in eligible:
        source = item["source"]
        action = item["action"]
        source_t = int(source["source_t"])
        target_t = int(item["target_t"])
        source_hash = str(source["candidate_hash"])
        job_id = _safe_id(
            f"{campaign[:32]}-g{int(generation):02d}-s{source_t:05d}-"
            f"{source_hash[:16]}-t{target_t:05d}"
        )
        if job_id in seen_job_ids:
            raise ValueError(f"closure job id collision: {job_id}")
        seen_job_ids.add(job_id)
        source_path = sources_dir / f"24T{source_t}-{source_hash[:16]}.jsonl"
        map_path = maps_dir / f"24T{source_t}.jsonl"
        _atomic_jsonl(source_path, [source])
        _atomic_jsonl(map_path, [action])

        stem = output_prefix / job_id
        candidates = stem.with_suffix(".candidates.jsonl")
        candidate_report = candidates.with_suffix(candidates.suffix + ".report.json")
        worker_report = stem.with_suffix(".worker.report.json")
        args = [
            _relative(source_path, project),
            _relative(map_path, project),
            str(candidates),
            "--source-t",
            str(source_t),
            "--gp",
            str(gp_path),
            "--factor-timeout",
            str(int(factor_timeout)),
        ]
        if resolvent_kind == "pair-sum-product":
            args.extend(
                [
                    "--resolvent-kind",
                    "pair-sum-product",
                    "--product-weight",
                    str(int(product_weight)),
                ]
            )
        if source_t == target_t:
            args.append("--allow-self-map")
        manifest = {
            "job_id": job_id,
            "module": "routeA.generate_degree24_pair_resolvents",
            "args": args,
            "inputs": [
                {"path": _relative(path, project), "sha256": _sha256(path)}
                for path in (source_path, map_path)
            ],
            "artifacts": [
                {"path": str(candidates), "required": True},
                {"path": str(candidate_report), "required": True},
            ],
            "report": str(worker_report),
            "campaign": {
                "campaign_id": campaign,
                "closure_generation": int(generation),
                "source_t": source_t,
                "source_r": int(source["source_r"]),
                "source_candidate_hash": source_hash,
                "source_pair_owned": bool(item["source_pair_owned"]),
                "target_t": target_t,
                "self_action": source_t == target_t,
                "gap_orbit_count": int(action["orbit_count"]),
                "live_opportunity": float(item["live_opportunity"]),
                "target_snapshot_id": snapshot_id,
                "preserved_as_closure_seed_regardless_of_ownership": True,
                "certificate": (
                    "generated-exact-source-plus-gap-exact-generic-unordered-"
                    "pair-action-closure"
                    if resolvent_kind == "pair-sum-product"
                    else "generated-exact-source-plus-gap-exact-unordered-pair-"
                    "action-closure"
                ),
            },
        }
        manifest_path = manifests_dir / f"{job_id}.json"
        _atomic_json(manifest_path, manifest)
        tasks.append(
            {
                "index": len(tasks),
                "job_id": job_id,
                "manifest": _relative(manifest_path, project),
                "manifest_sha256": _sha256(manifest_path),
                "closure_generation": int(generation),
                "source_t": source_t,
                "source_r": int(source["source_r"]),
                "source_candidate_hash": source_hash,
                "source_pair_owned": bool(item["source_pair_owned"]),
                "target_t": target_t,
                "self_action": source_t == target_t,
                "lane": "pair-action-closure",
                "variant": (
                    "exact-unambiguous-generic-unordered-pair"
                    if resolvent_kind == "pair-sum-product"
                    else "exact-unambiguous-unordered-pair"
                ),
                "live_opportunity": float(item["live_opportunity"]),
            }
        )

    input_paths = [*candidate_files, orbit_map_path, *processed_files]
    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "target_snapshot_id": snapshot_id,
        "task_count": len(tasks),
        "mode": "iterative-exact-degree24-unordered-pair-closure",
        "closure_generation": int(generation),
        "include_self_maps": True,
        "action_kind": (
            "generic-unordered-pair"
            if resolvent_kind == "pair-sum-product"
            else "unordered-pair"
        ),
        "preserve_owned_closure_seeds": True,
        "factor_timeout": int(factor_timeout),
        "gp_path": str(gp_path),
        "resolvent_kind": resolvent_kind,
        "product_weight": int(product_weight),
        "normalized_frontier": _relative(normalized_path, project),
        "normalized_frontier_sha256": _sha256(normalized_path),
        "effective_processed_source_hash_count": len(effective_processed),
        "inputs": [
            {
                "path": _relative(path, project),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in input_paths
        ],
        "tasks": tasks,
    }
    matrix_path = output_dir / "matrix.json"
    _atomic_json(matrix_path, matrix)

    selected_target_ts = {int(task["target_t"]) for task in tasks}
    summary = {
        "campaign_id": campaign,
        "matrix": _relative(matrix_path, project),
        "closure_generation": int(generation),
        "task_count": len(tasks),
        "task_limit": int(task_limit) if task_limit is not None else None,
        "eligible_source_count_before_limit": eligible_before_limit,
        "deferred_by_task_limit": eligible_before_limit - len(tasks),
        "normalized_frontier_source_count": len(sources),
        "missing_unambiguous_action_sources": missing_action,
        "owned_source_seeds_preserved": sum(bool(task["source_pair_owned"]) for task in tasks),
        "self_action_tasks": sum(bool(task["self_action"]) for task in tasks),
        "zero_immediate_opportunity_tasks_preserved": sum(
            float(task["live_opportunity"]) <= 0.0 for task in tasks
        ),
        "selected_target_group_count": len(selected_target_ts),
        "unique_target_live_opportunity": sum(
            _target_opportunity(target_t, live, owned)
            for target_t in selected_target_ts
        ),
        "task_weighted_live_opportunity": sum(
            float(task["live_opportunity"]) for task in tasks
        ),
        "target_snapshot_id": snapshot_id,
        "preserve_owned_closure_seeds": True,
        "normalization_counts": normalization_counts,
        "action_counts": action_counts,
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _authoritative_state(
    db_path: str | Path | None,
    *,
    project: Path,
) -> tuple[dict[tuple[int, int], Mapping[str, Any]], set[tuple[int, int]], str | None]:
    live: dict[tuple[int, int], Mapping[str, Any]] = {}
    owned = load_owned_pairs(project)
    if db_path is not None and Path(db_path).exists():
        with Ledger(db_path) as ledger:
            live = dict(ledger.latest_targets())
            owned |= ledger.owned_pairs()
    snapshot_id = str(next(iter(live.values()))["snapshot_id"]) if live else None
    return live, owned, snapshot_id


def _target_opportunity(
    target_t: int,
    live: Mapping[tuple[int, int], Mapping[str, Any]],
    owned: set[tuple[int, int]],
) -> float:
    if not live:
        return 0.0
    opportunity = 0.0
    for root in range(0, 25, 2):
        pair = int(target_t), root
        row = live.get(pair)
        if row is None or pair in owned or bool(row["baseline"]):
            continue
        opportunity += 2.0 ** (-int(row["team_count"]))
    return opportunity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orbit_map", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--processed-matrix", action="append", default=[], type=Path)
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--task-limit", type=int)
    parser.add_argument("--factor-timeout", type=int, default=7_200)
    parser.add_argument("--gp", type=Path, default=Path("/usr/bin/gp"))
    parser.add_argument(
        "--resolvent-kind",
        choices=("pair-sum", "pair-sum-product"),
        default="pair-sum",
    )
    parser.add_argument("--product-weight", type=int, default=1)
    args = parser.parse_args()
    summary = plan_pair_closure_campaign(
        args.candidates,
        args.orbit_map,
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        db_path=args.db,
        processed_matrix_paths=args.processed_matrix,
        generation=args.generation,
        task_limit=args.task_limit,
        factor_timeout=args.factor_timeout,
        gp_path=args.gp,
        resolvent_kind=args.resolvent_kind,
        product_weight=args.product_weight,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

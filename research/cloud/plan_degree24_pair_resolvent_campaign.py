#!/usr/bin/env python3
"""Plan exact degree-24 sibling fields from pair-sum resolvents.

The source catalog must contain server-verified degree-24 fields.  The GAP
orbit map certifies the transitive group of every degree-24 pair orbit.  Only
nontrivial maps whose degree-24 orbits all have one target label are eligible.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

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


def plan_pair_resolvent_campaign(
    source_catalog_path: str | Path,
    orbit_map_path: str | Path,
    *,
    campaign_id: str,
    output_dir: str | Path,
    db_path: str | Path | None = DEFAULT_DB,
    target_limit: int | None = None,
    sources_per_target: int = 1,
    fields_per_source: int = 1,
    include_self_maps: bool = False,
    action_kind: str = "unordered-pair",
    joint_profile_path: str | Path | None = None,
    product_weight: int = 1,
    shift_weight: int = 1,
    classification_prime_count: int = 256,
    minimum_good_primes: int = 8,
    minimum_classification_confidence: float = 0.99,
    classification_timeout: int = 1_800,
    minimum_opportunity: float = 0.0,
    factor_timeout: int = 7_200,
    gp: str = "/usr/bin/gp",
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    """Create one deterministic GCP task per selected verified source field."""

    project = Path(project).resolve()
    source_catalog_path = Path(source_catalog_path).resolve()
    orbit_map_path = Path(orbit_map_path).resolve()
    output_dir = Path(output_dir).resolve()
    _require_within(source_catalog_path, project, "verified source catalog")
    _require_within(orbit_map_path, project, "pair-orbit map")
    _require_within(output_dir, project, "campaign output")
    if sources_per_target < 1:
        raise ValueError("sources_per_target must be positive")
    if fields_per_source < 1:
        raise ValueError("fields_per_source must be positive")
    if factor_timeout < 1:
        raise ValueError("factor_timeout must be positive")
    if action_kind not in {
        "unordered-pair",
        "generic-unordered-pair",
        "ordered-pair",
        "triple-subset",
        "ambiguous-unordered-pair",
    }:
        raise ValueError(
            "action_kind must be unordered-pair, generic-unordered-pair, "
            "ordered-pair, triple-subset, or ambiguous-unordered-pair"
        )
    if int(product_weight) == 0:
        raise ValueError("product_weight must be nonzero")
    if int(shift_weight) == 0:
        raise ValueError("shift_weight must be nonzero")
    if int(minimum_good_primes) < 1:
        raise ValueError("minimum_good_primes must be positive")
    if int(classification_prime_count) < int(minimum_good_primes):
        raise ValueError(
            "classification_prime_count cannot be smaller than minimum_good_primes"
        )
    if not 0.0 <= float(minimum_classification_confidence) <= 1.0:
        raise ValueError("minimum_classification_confidence must lie in [0, 1]")

    campaign = _safe_id(campaign_id)
    source_rows = load_jsonl(source_catalog_path)
    orbit_rows = load_jsonl(orbit_map_path)
    sources_by_t: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        source_t = int(row["source_t"])
        coefficients = row.get("coefficients")
        if not isinstance(coefficients, list) or len(coefficients) != 25:
            raise ValueError(f"verified source 24T{source_t} is not degree 24")
        sources_by_t[source_t].append(dict(row))
    for rows in sources_by_t.values():
        rows.sort(key=lambda row: (
            _positive_disc(row.get("disc_abs")),
            int(row.get("source_r", 0)),
            str(row.get("candidate_hash", "")),
        ))

    live: Mapping[tuple[int, int], Mapping[str, Any]] = {}
    owned = load_owned_pairs(project)
    if db_path is not None and Path(db_path).exists():
        with Ledger(db_path) as ledger:
            live = ledger.latest_targets()
            owned |= ledger.owned_pairs()
    snapshot_id = str(next(iter(live.values()))["snapshot_id"]) if live else None

    if action_kind == "ambiguous-unordered-pair":
        if joint_profile_path is None:
            raise ValueError("ambiguous pair campaigns require a joint-profile file")
        joint_profile_path = Path(joint_profile_path).resolve()
        _require_within(joint_profile_path, project, "pair joint-profile map")
        return _plan_ambiguous_pair_resolvent_campaign(
            campaign=campaign,
            source_catalog_path=source_catalog_path,
            orbit_map_path=orbit_map_path,
            joint_profile_path=joint_profile_path,
            output_dir=output_dir,
            project=project,
            source_rows=source_rows,
            orbit_rows=orbit_rows,
            live=live,
            owned=owned,
            snapshot_id=snapshot_id,
            target_limit=target_limit,
            fields_per_source=int(fields_per_source),
            minimum_opportunity=float(minimum_opportunity),
            factor_timeout=int(factor_timeout),
            product_weight=int(product_weight),
            classification_prime_count=int(classification_prime_count),
            minimum_good_primes=int(minimum_good_primes),
            minimum_classification_confidence=float(
                minimum_classification_confidence
            ),
            classification_timeout=int(classification_timeout),
            gp=str(gp),
        )

    grouped: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    rejected_ambiguous = 0
    rejected_self = 0
    rejected_unverified = 0
    for orbit in orbit_rows:
        source_t = int(orbit["source_t"])
        target_t = orbit.get("unambiguous_target_t")
        if target_t is None:
            rejected_ambiguous += 1
            continue
        target_t = int(target_t)
        if target_t == source_t and not include_self_maps:
            rejected_self += 1
            continue
        sources = sources_by_t.get(source_t)
        if not sources:
            rejected_unverified += 1
            continue
        # Distinct verified fields with the same transitive source group can
        # land in different real signatures of the exact sibling target.
        for source in sources[: int(fields_per_source)]:
            grouped[target_t].append((source, dict(orbit)))

    ranked: list[tuple[float, int, list[tuple[dict[str, Any], dict[str, Any]]]]] = []
    for target_t, pairs in grouped.items():
        opportunity = _target_opportunity(target_t, live, owned)
        if opportunity <= float(minimum_opportunity):
            continue
        pairs.sort(key=lambda pair: (
            _positive_disc(pair[0].get("disc_abs")),
            int(pair[0]["source_t"]),
        ))
        ranked.append((opportunity, target_t, pairs[: int(sources_per_target)]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if target_limit is not None:
        ranked = ranked[: max(0, int(target_limit))]

    sources_dir = output_dir / "sources"
    maps_dir = output_dir / "orbit_maps"
    manifests_dir = output_dir / "manifests"
    sources_dir.mkdir(parents=True, exist_ok=True)
    maps_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    tasks: list[dict[str, Any]] = []
    for opportunity, target_t, pairs in ranked:
        for source, orbit in pairs:
            source_t = int(source["source_t"])
            source_hash = str(source.get("candidate_hash") or "")
            source_tag = source_hash[:10] or f"r{int(source.get('source_r', 0)):02d}"
            job_id = _safe_id(
                f"{campaign}-s{source_t:05d}-{source_tag}-t{target_t:05d}"
            )
            source_path = sources_dir / f"24T{source_t}-{source_tag}.jsonl"
            map_path = maps_dir / f"24T{source_t}.jsonl"
            _atomic_jsonl(source_path, [source])
            _atomic_jsonl(map_path, [orbit])
            stem = output_prefix / job_id
            candidates = stem.with_suffix(".candidates.jsonl")
            candidate_report = candidates.with_suffix(candidates.suffix + ".report.json")
            worker_report = stem.with_suffix(".worker.report.json")
            manifest = {
                "job_id": job_id,
                "module": "routeA.generate_degree24_pair_resolvents",
                "args": [
                    _relative(source_path, project),
                    _relative(map_path, project),
                    str(candidates),
                    "--source-t", str(source_t),
                    "--gp", str(gp),
                    "--factor-timeout", str(int(factor_timeout)),
                ]
                + (["--allow-self-map"] if target_t == source_t else [])
                + (
                    ["--resolvent-kind", "ordered-affine"]
                    if action_kind == "ordered-pair"
                    else (
                        [
                            "--resolvent-kind", "pair-sum-product",
                            "--product-weight", str(int(product_weight)),
                        ]
                        if action_kind == "generic-unordered-pair"
                        else (
                            [
                                "--resolvent-kind", "triple-shift-product",
                                "--shift-weight", str(int(shift_weight)),
                            ]
                            if action_kind == "triple-subset"
                            else []
                        )
                    )
                ),
                "inputs": [
                    {"path": _relative(source_path, project), "sha256": _sha256(source_path)},
                    {"path": _relative(map_path, project), "sha256": _sha256(map_path)},
                ],
                "artifacts": [
                    {"path": str(candidates), "required": True},
                    {"path": str(candidate_report), "required": True},
                ],
                "report": str(worker_report),
                "campaign": {
                    "campaign_id": campaign,
                    "source_t": source_t,
                    "target_t": target_t,
                    "gap_orbit_count": int(orbit["orbit_count"]),
                    "live_opportunity": opportunity,
                    "target_snapshot_id": snapshot_id,
                    "certificate": (
                        "server-source-plus-gap-exact-ordered-pair-action"
                        if action_kind == "ordered-pair"
                        else (
                            "server-source-plus-gap-exact-3-subset-action"
                            if action_kind == "triple-subset"
                            else "server-source-plus-gap-exact-pair-action"
                        )
                    ),
                },
            }
            manifest_path = manifests_dir / f"{job_id}.json"
            _atomic_json(manifest_path, manifest)
            tasks.append({
                "index": len(tasks),
                "job_id": job_id,
                "manifest": _relative(manifest_path, project),
                "manifest_sha256": _sha256(manifest_path),
                "source_t": source_t,
                "source_candidate_hash": source_hash,
                "target_t": target_t,
                "lane": (
                    "ordered-pair-resolvent"
                    if action_kind == "ordered-pair"
                    else (
                        "generic-pair-resolvent"
                        if action_kind == "generic-unordered-pair"
                        else (
                            "triple-subset-resolvent"
                            if action_kind == "triple-subset"
                            else "pair-resolvent"
                        )
                    )
                ),
                "variant": "exact",
                "live_opportunity": opportunity,
            })

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "target_snapshot_id": snapshot_id,
        "task_count": len(tasks),
        "mode": (
            "exact-degree24-ordered-pair-resolvent"
            if action_kind == "ordered-pair"
            else (
                "exact-degree24-generic-pair-resolvent"
                if action_kind == "generic-unordered-pair"
                else (
                    "exact-degree24-triple-subset-resolvent"
                    if action_kind == "triple-subset"
                    else "exact-degree24-pair-resolvent"
                )
            )
        ),
        "factor_timeout": int(factor_timeout),
        "fields_per_source": int(fields_per_source),
        "include_self_maps": bool(include_self_maps),
        "action_kind": action_kind,
        "product_weight": (
            int(product_weight)
            if action_kind == "generic-unordered-pair"
            else None
        ),
        "shift_weight": (
            int(shift_weight) if action_kind == "triple-subset" else None
        ),
        "inputs": [
            {
                "path": _relative(path, project),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in (source_catalog_path, orbit_map_path)
        ],
        "tasks": tasks,
    }
    matrix_path = output_dir / "matrix.json"
    _atomic_json(matrix_path, matrix)
    summary = {
        "campaign_id": campaign,
        "matrix": _relative(matrix_path, project),
        "task_count": len(tasks),
        "source_count": len({task["source_t"] for task in tasks}),
        "source_field_count": len(tasks),
        "target_count": len({task["target_t"] for task in tasks}),
        "include_self_maps": bool(include_self_maps),
        "action_kind": action_kind,
        "live_opportunity": sum(opportunity for opportunity, _, _ in ranked),
        "target_snapshot_id": snapshot_id,
        "eligible_target_count": len(grouped),
        "rejected_ambiguous_maps": rejected_ambiguous,
        "rejected_self_maps": rejected_self,
        "rejected_unverified_sources": rejected_unverified,
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _target_opportunity(
    target_t: int,
    live: Mapping[tuple[int, int], Mapping[str, Any]],
    owned: set[tuple[int, int]],
) -> float:
    if not live:
        return 1.0
    score = 0.0
    for root in range(0, 25, 2):
        pair = int(target_t), root
        row = live.get(pair)
        if row is None or pair in owned or bool(row["baseline"]):
            continue
        score += 2.0 ** (-int(row["team_count"]))
    return score


def _plan_ambiguous_pair_resolvent_campaign(
    *,
    campaign: str,
    source_catalog_path: Path,
    orbit_map_path: Path,
    joint_profile_path: Path,
    output_dir: Path,
    project: Path,
    source_rows: list[dict[str, Any]],
    orbit_rows: list[dict[str, Any]],
    live: Mapping[tuple[int, int], Mapping[str, Any]],
    owned: set[tuple[int, int]],
    snapshot_id: str | None,
    target_limit: int | None,
    fields_per_source: int,
    minimum_opportunity: float,
    factor_timeout: int,
    product_weight: int,
    classification_prime_count: int,
    minimum_good_primes: int,
    minimum_classification_confidence: float,
    classification_timeout: int,
    gp: str,
) -> dict[str, Any]:
    """Plan generic unordered-pair resolvents plus exact joint classification."""

    sources_by_t: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        source_t = int(row["source_t"])
        coefficients = row.get("coefficients")
        if not isinstance(coefficients, list) or len(coefficients) != 25:
            raise ValueError(f"verified source 24T{source_t} is not degree 24")
        sources_by_t[source_t].append(dict(row))
    for rows in sources_by_t.values():
        rows.sort(key=lambda row: (
            _positive_disc(row.get("disc_abs")),
            int(row.get("source_r", 0)),
            str(row.get("candidate_hash", "")),
        ))

    profiles: dict[int, dict[str, Any]] = {}
    for row in load_jsonl(joint_profile_path):
        source_t = int(row["source_t"])
        if source_t in profiles:
            raise ValueError(f"duplicate joint profile for 24T{source_t}")
        profiles[source_t] = dict(row)

    rejected_unambiguous = 0
    rejected_unverified = 0
    rejected_missing_profile = 0
    eligible: list[
        tuple[float, int, dict[str, Any], dict[str, Any], list[dict[str, Any]]]
    ] = []
    for raw_orbit in orbit_rows:
        orbit = dict(raw_orbit)
        if orbit.get("unambiguous_target_t") is not None:
            rejected_unambiguous += 1
            continue
        source_t = int(orbit["source_t"])
        sources = sources_by_t.get(source_t, [])[:fields_per_source]
        if not sources:
            rejected_unverified += 1
            continue
        profile = profiles.get(source_t)
        if profile is None:
            rejected_missing_profile += 1
            continue
        orbit_targets = tuple(int(value) for value in orbit["orbit_targets"])
        profile_targets = tuple(int(value) for value in profile["orbit_targets"])
        if orbit_targets != profile_targets:
            raise ValueError(
                f"joint profile targets for 24T{source_t} do not match orbit map"
            )
        opportunity = sum(
            _target_opportunity(target_t, live, owned)
            for target_t in sorted(set(orbit_targets))
        )
        if opportunity <= minimum_opportunity:
            continue
        eligible.append((opportunity, source_t, orbit, profile, sources))
    eligible.sort(key=lambda item: (-item[0], item[1]))
    if target_limit is not None:
        eligible = eligible[: max(0, int(target_limit))]

    sources_dir = output_dir / "sources"
    maps_dir = output_dir / "orbit_maps"
    profiles_dir = output_dir / "joint_profiles"
    manifests_dir = output_dir / "manifests"
    for directory in (sources_dir, maps_dir, profiles_dir, manifests_dir):
        directory.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    tasks: list[dict[str, Any]] = []
    selected_targets: set[int] = set()
    for opportunity, source_t, orbit, profile, sources in eligible:
        orbit_targets = [int(value) for value in orbit["orbit_targets"]]
        selected_targets.update(orbit_targets)
        map_path = maps_dir / f"24T{source_t}.jsonl"
        profile_path = profiles_dir / f"24T{source_t}.jsonl"
        _atomic_jsonl(map_path, [orbit])
        _atomic_jsonl(profile_path, [profile])
        for source in sources:
            source_hash = str(source.get("candidate_hash") or "")
            source_tag = source_hash[:10] or f"r{int(source.get('source_r', 0)):02d}"
            job_id = _safe_id(
                f"{campaign}-s{source_t:05d}-{source_tag}-ambiguous"
            )
            source_path = sources_dir / f"24T{source_t}-{source_tag}.jsonl"
            _atomic_jsonl(source_path, [source])
            stem = output_prefix / job_id
            candidates = stem.with_suffix(".candidates.jsonl")
            candidate_report = candidates.with_suffix(candidates.suffix + ".report.json")
            worker_report = stem.with_suffix(".worker.report.json")
            manifest = {
                "job_id": job_id,
                "module": "routeA.generate_degree24_pair_resolvents",
                "args": [
                    _relative(source_path, project),
                    _relative(map_path, project),
                    str(candidates),
                    "--source-t", str(source_t),
                    "--gp", str(gp),
                    "--factor-timeout", str(factor_timeout),
                    "--resolvent-kind", "pair-sum-product",
                    "--product-weight", str(product_weight),
                    "--joint-profile", _relative(profile_path, project),
                    "--classification-prime-count", str(classification_prime_count),
                    "--minimum-good-primes", str(minimum_good_primes),
                    "--minimum-classification-confidence",
                    str(minimum_classification_confidence),
                    "--classification-timeout", str(classification_timeout),
                ],
                "inputs": [
                    {"path": _relative(path, project), "sha256": _sha256(path)}
                    for path in (source_path, map_path, profile_path)
                ],
                "artifacts": [
                    {"path": str(candidates), "required": True},
                    {"path": str(candidate_report), "required": True},
                ],
                "report": str(worker_report),
                "campaign": {
                    "campaign_id": campaign,
                    "source_t": source_t,
                    "target_ts": sorted(set(orbit_targets)),
                    "gap_orbit_count": int(orbit["orbit_count"]),
                    "live_opportunity": opportunity,
                    "target_snapshot_id": snapshot_id,
                    "certificate": (
                        "server-source-plus-gap-exact-pair-action-plus-"
                        "coupled-frobenius-classification"
                    ),
                },
            }
            manifest_path = manifests_dir / f"{job_id}.json"
            _atomic_json(manifest_path, manifest)
            tasks.append({
                "index": len(tasks),
                "job_id": job_id,
                "manifest": _relative(manifest_path, project),
                "manifest_sha256": _sha256(manifest_path),
                "source_t": source_t,
                "source_candidate_hash": source_hash,
                "target_ts": sorted(set(orbit_targets)),
                "lane": "ambiguous-pair-resolvent",
                "variant": "exact-joint-classification",
                "live_opportunity": opportunity,
            })

    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": _now(),
        "target_snapshot_id": snapshot_id,
        "task_count": len(tasks),
        "mode": "exact-degree24-ambiguous-pair-resolvent",
        "factor_timeout": factor_timeout,
        "fields_per_source": fields_per_source,
        "action_kind": "ambiguous-unordered-pair",
        "product_weight": product_weight,
        "classification_prime_count": classification_prime_count,
        "minimum_good_primes": minimum_good_primes,
        "minimum_classification_confidence": minimum_classification_confidence,
        "classification_timeout": classification_timeout,
        "inputs": [
            {
                "path": _relative(path, project),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in (
                source_catalog_path,
                orbit_map_path,
                joint_profile_path,
            )
        ],
        "tasks": tasks,
    }
    matrix_path = output_dir / "matrix.json"
    _atomic_json(matrix_path, matrix)
    summary = {
        "campaign_id": campaign,
        "matrix": _relative(matrix_path, project),
        "task_count": len(tasks),
        "source_count": len({task["source_t"] for task in tasks}),
        "source_field_count": len(tasks),
        "target_count": len(selected_targets),
        "action_kind": "ambiguous-unordered-pair",
        "live_opportunity": sum(
            _target_opportunity(target_t, live, owned)
            for target_t in selected_targets
        ),
        "target_snapshot_id": snapshot_id,
        "eligible_action_count": len(eligible),
        "eligible_target_count": len(selected_targets),
        "rejected_unambiguous_maps": rejected_unambiguous,
        "rejected_unverified_sources": rejected_unverified,
        "rejected_missing_profiles": rejected_missing_profile,
        "product_weight": product_weight,
        "classification_prime_count": classification_prime_count,
        "minimum_good_primes": minimum_good_primes,
        "minimum_classification_confidence": minimum_classification_confidence,
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _positive_disc(value: Any) -> int:
    try:
        result = abs(int(value))
    except (TypeError, ValueError):
        return 10**1000
    return result if result > 1 else 10**1000


def _atomic_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_catalog", type=Path)
    parser.add_argument("orbit_map", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--target-limit", type=int)
    parser.add_argument("--sources-per-target", type=int, default=1)
    parser.add_argument("--fields-per-source", type=int, default=1)
    parser.add_argument("--include-self-maps", action="store_true")
    parser.add_argument(
        "--action-kind",
        choices=(
            "unordered-pair",
            "generic-unordered-pair",
            "ordered-pair",
            "triple-subset",
            "ambiguous-unordered-pair",
        ),
        default="unordered-pair",
    )
    parser.add_argument("--joint-profile", type=Path)
    parser.add_argument("--product-weight", type=int, default=1)
    parser.add_argument("--shift-weight", type=int, default=1)
    parser.add_argument("--classification-prime-count", type=int, default=256)
    parser.add_argument("--minimum-good-primes", type=int, default=8)
    parser.add_argument(
        "--minimum-classification-confidence", type=float, default=0.99
    )
    parser.add_argument("--classification-timeout", type=int, default=1_800)
    parser.add_argument("--minimum-opportunity", type=float, default=0.0)
    parser.add_argument("--factor-timeout", type=int, default=7_200)
    parser.add_argument("--gp", default="/usr/bin/gp")
    args = parser.parse_args()
    summary = plan_pair_resolvent_campaign(
        args.source_catalog,
        args.orbit_map,
        campaign_id=args.campaign_id,
        output_dir=args.output_dir,
        db_path=args.db,
        target_limit=args.target_limit,
        sources_per_target=args.sources_per_target,
        fields_per_source=args.fields_per_source,
        include_self_maps=args.include_self_maps,
        action_kind=args.action_kind,
        joint_profile_path=args.joint_profile,
        product_weight=args.product_weight,
        shift_weight=args.shift_weight,
        classification_prime_count=args.classification_prime_count,
        minimum_good_primes=args.minimum_good_primes,
        minimum_classification_confidence=args.minimum_classification_confidence,
        classification_timeout=args.classification_timeout,
        minimum_opportunity=args.minimum_opportunity,
        factor_timeout=args.factor_timeout,
        gp=args.gp,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

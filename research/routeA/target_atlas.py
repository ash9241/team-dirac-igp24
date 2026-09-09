#!/usr/bin/env python3
"""Build an owned-pair-aware constructibility atlas for live IGP24 targets.

The atlas distinguishes four materially different states:

* ``candidate_ready``: a concrete, unsubmitted candidate targets the pair;
* ``seed_ready``: an exact GAP label route and compatible lower-degree seed
  profile exist, but a fresh degree-24 candidate still needs to be built;
* ``family_calibration_needed``: a construction can reach the label family
  and signature, but cannot yet distinguish the exact 24T sibling;
* ``exact_label_seed_gap`` / ``architecture_gap``: the missing work is an
  arithmetic seed or a new extension architecture, not more submissions.

This prevents a structural census entry from being mistaken for a
submission-ready construction.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger, candidate_hash
from routeA.scheduler import candidate_submission_ready, load_owned_pairs


PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_PROGRESS = PROJECT / "daemon" / "data" / "all_progress.json"
DEFAULT_CENSUS = PROJECT / "cloud" / "output" / "full_block_census.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "target_atlas.jsonl"

MAP_SPECS = (
    (
        "direct_8x3",
        PROJECT / "cloud" / "output" / "all_8x3_product_map.jsonl",
        "g8",
        "g3",
        8,
        3,
        "disjoint",
    ),
    (
        "fiber_c2_8x3",
        PROJECT / "cloud" / "output" / "all_8x3_fiber_map.jsonl",
        "g8",
        "g3",
        8,
        3,
        "shared_squareclass",
    ),
    (
        "direct_12x2",
        PROJECT / "cloud" / "output" / "all_12x2_product_map.jsonl",
        "group_a",
        "group_b",
        12,
        2,
        "unknown",
    ),
    (
        "direct_6x4",
        PROJECT / "cloud" / "output" / "all_6x4_product_map.jsonl",
        "group_a",
        "group_b",
        6,
        4,
        "unknown",
    ),
)

DEFAULT_COMPONENTS = (
    PROJECT / "routeA" / "data" / "components" / "octics.jsonl",
    PROJECT / "routeA" / "data" / "components" / "cubics.jsonl",
    PROJECT / "routeA" / "data" / "components" / "fiber_17920_octics.jsonl",
    PROJECT / "routeA" / "data" / "components" / "fiber_17920_cubics.jsonl",
)


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    with source.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def index_components(
    component_rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    indexed: dict[tuple[int, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for source in component_rows:
        row = dict(source)
        try:
            key = (int(row["degree"]), int(row["transitive_id"]))
            component_id = str(row["component_id"])
            int(row["root_count"])
        except (KeyError, TypeError, ValueError):
            continue
        indexed[key][component_id] = row
    return {key: list(rows.values()) for key, rows in indexed.items()}


def exact_product_routes(
    map_rows: Iterable[Mapping[str, Any]],
    components: Mapping[tuple[int, int], Sequence[Mapping[str, Any]]],
    *,
    family: str,
    group_a_key: str,
    group_b_key: str,
    degree_a: int,
    degree_b: int,
    relation: str,
) -> dict[int, list[dict[str, Any]]]:
    routes: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in map_rows:
        try:
            group_a = int(row[group_a_key])
            group_b = int(row[group_b_key])
            target = int(row["target_t"])
        except (KeyError, TypeError, ValueError):
            continue
        seeds_a = components.get((degree_a, group_a), ())
        seeds_b = components.get((degree_b, group_b), ())
        signature_counts: Counter[int] = Counter()
        for left in seeds_a:
            for right in seeds_b:
                if not _component_relation(left, right, relation):
                    continue
                signature_counts[int(left["root_count"]) * int(right["root_count"])] += 1
        routes[target].append({
            "family": family,
            "evidence": "gap-exact-label",
            "lower_groups": {
                str(degree_a): group_a,
                str(degree_b): group_b,
            },
            "component_relation": relation,
            "seed_count_a": len(seeds_a),
            "seed_count_b": len(seeds_b),
            "available_signatures": sorted(signature_counts),
            "available_seed_pairs": {
                str(root_count): count
                for root_count, count in sorted(signature_counts.items())
            },
        })
    return routes


def _component_relation(
    left: Mapping[str, Any], right: Mapping[str, Any], relation: str
) -> bool:
    if relation == "disjoint":
        left_support = set(map(int, left.get("discriminant_support", [])))
        right_support = set(map(int, right.get("discriminant_support", [])))
        return left_support.isdisjoint(right_support)
    if relation == "shared_squareclass":
        left_class = _optional_int(left.get("discriminant_squareclass"))
        right_class = _optional_int(right.get("discriminant_squareclass"))
        return left_class not in (None, 1) and left_class == right_class
    return False


def merge_route_indices(
    indices: Iterable[Mapping[int, Sequence[Mapping[str, Any]]]],
) -> dict[int, list[dict[str, Any]]]:
    merged: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index in indices:
        for target, routes in index.items():
            merged[int(target)].extend(dict(route) for route in routes)
    return merged


def build_target_atlas(
    progress_labels: Iterable[Mapping[str, Any]],
    census_rows: Iterable[Mapping[str, Any]],
    exact_routes: Mapping[int, Sequence[Mapping[str, Any]]],
    candidate_rows: Iterable[Mapping[str, Any]],
    *,
    owned_pairs: set[tuple[int, int]],
    committed_hashes: set[str] | None = None,
    max_team_count: int = 1,
) -> list[dict[str, Any]]:
    census = {int(row["t"]): dict(row) for row in census_rows}
    committed_hashes = committed_hashes or set()
    ready: Counter[tuple[int, int]] = Counter()
    calibration_blocked: Counter[tuple[int, int]] = Counter()
    compatible: Counter[tuple[int, int]] = Counter()
    for candidate in candidate_rows:
        roots = _optional_int(candidate.get("target_r", candidate.get("local_root_count")))
        if roots is None:
            continue
        coefficients = candidate.get("coefficients") or candidate.get("line")
        supplied_hash = candidate.get("candidate_hash")
        try:
            key = str(supplied_hash or candidate_hash(str(coefficients)))
        except (TypeError, ValueError):
            continue
        if key in committed_hashes:
            continue
        target = _optional_int(candidate.get("target_t", candidate.get("tgt")))
        if target is not None:
            if candidate_submission_ready(candidate):
                ready[(target, roots)] += 1
            else:
                calibration_blocked[(target, roots)] += 1
        for label in candidate.get("compatible_labels", []) or []:
            try:
                compatible[(int(label), roots)] += 1
            except (TypeError, ValueError):
                continue

    records = []
    for label in progress_labels:
        target = int(label.get("t") or str(label["label"]).replace("24T", ""))
        group = census.get(target, {})
        for signature in label.get("signatures", []):
            roots = int(signature["r"])
            team_count = int(signature.get("teamCount", 0))
            pair = (target, roots)
            if (
                team_count > max_team_count
                or bool(signature.get("baseline"))
                or pair in owned_pairs
            ):
                continue
            routes = []
            pair_seed_routes = 0
            for source in exact_routes.get(target, ()):
                route = dict(source)
                route["pair_seed_ready"] = roots in set(route.get("available_signatures", []))
                pair_seed_routes += int(route["pair_seed_ready"])
                routes.append(route)
            exact_candidates = ready[pair]
            compatible_candidates = max(0, compatible[pair] - exact_candidates)
            if exact_candidates:
                status = "candidate_ready"
            elif pair_seed_routes:
                status = "seed_ready"
            elif compatible_candidates:
                status = "family_calibration_needed"
            elif routes:
                status = "exact_label_seed_gap"
            else:
                status = "architecture_gap"
            order = _optional_int(group.get("order"))
            record = {
                "t": target,
                "r": roots,
                "team_count": team_count,
                "score_ceiling": 2.0 ** (-team_count),
                "minimum_disc_abs": signature.get("minimumDiscAbs"),
                "status": status,
                "ready_candidate_count": exact_candidates,
                "calibration_blocked_candidate_count": calibration_blocked[pair],
                "compatible_unresolved_candidate_count": compatible_candidates,
                "pair_seed_route_count": pair_seed_routes,
                "exact_label_route_count": len(routes),
                "routes": routes,
                "group_order": str(group.get("order")) if group.get("order") is not None else None,
                "solvable": bool(group.get("solvable")) if group else None,
                "primitive": bool(group.get("primitive")) if group else None,
                "block_sizes": list(group.get("block_sizes", [])),
                "block_quotients": list(group.get("block_quotients", [])),
                "priority": _priority(status, team_count, bool(group.get("solvable")), order),
            }
            records.append(record)
    return sorted(
        records,
        key=lambda row: (
            -float(row["priority"]),
            row["team_count"],
            int(row["group_order"]) if row["group_order"] else 10**100,
            row["t"],
            -row["r"],
        ),
    )


def atlas_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row["status"]) for row in records)
    architectures = Counter(
        tuple(int(value) for value in row.get("block_sizes", []))
        for row in records
        if row.get("block_sizes")
    )
    return {
        "open_unowned_pairs": len(records),
        "gold_pairs": sum(int(row["team_count"]) == 0 for row in records),
        "raid_pairs": sum(int(row["team_count"]) == 1 for row in records),
        "solo_equivalent_ceiling": sum(float(row["score_ceiling"]) for row in records),
        "status_counts": dict(sorted(statuses.items())),
        "top_architecture_gaps": [
            {"block_sizes": list(shape), "pairs": count}
            for shape, count in architectures.most_common(10)
        ],
    }


def _priority(
    status: str, team_count: int, solvable: bool, order: int | None
) -> float:
    status_bonus = {
        "candidate_ready": 5.0,
        "seed_ready": 4.0,
        "family_calibration_needed": 3.0,
        "exact_label_seed_gap": 2.0,
        "architecture_gap": 1.0,
    }[status]
    order_bonus = 0.0 if not order else 1.0 / max(1.0, order.bit_length())
    return status_bonus + 2.0 ** (-team_count) + 0.1 * int(solvable) + order_bonus


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def atomic_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        temporary = handle.name
    os.replace(temporary, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progress", default=str(DEFAULT_PROGRESS))
    parser.add_argument("--census", default=str(DEFAULT_CENSUS))
    parser.add_argument("--component", action="append", default=[])
    parser.add_argument("--candidate-shard", action="append", default=[])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-team-count", type=int, default=1)
    args = parser.parse_args()

    component_paths = [Path(path) for path in args.component] or list(DEFAULT_COMPONENTS)
    component_rows = [row for path in component_paths for row in load_jsonl(path)]
    components = index_components(component_rows)
    route_indices = []
    for family, path, key_a, key_b, degree_a, degree_b, relation in MAP_SPECS:
        route_indices.append(exact_product_routes(
            load_jsonl(path),
            components,
            family=family,
            group_a_key=key_a,
            group_b_key=key_b,
            degree_a=degree_a,
            degree_b=degree_b,
            relation=relation,
        ))
    candidates = [
        row
        for path in args.candidate_shard
        for row in load_jsonl(path)
    ]
    progress = json.loads(Path(args.progress).read_text(encoding="utf-8"))
    census = load_jsonl(args.census)
    with Ledger(args.db) as ledger:
        owned = load_owned_pairs(PROJECT) | ledger.owned_pairs()
        committed = {
            str(row["candidate_hash"])
            for row in ledger.connection.execute(
                """SELECT DISTINCT si.candidate_hash
                   FROM submission_item si JOIN submission s USING(batch_uuid)
                   WHERE s.dry_run=0"""
            )
        }
    records = build_target_atlas(
        progress,
        census,
        merge_route_indices(route_indices),
        candidates,
        owned_pairs=owned,
        committed_hashes=committed,
        max_team_count=args.max_team_count,
    )
    atomic_jsonl(args.output, records)
    print(json.dumps(atlas_summary(records), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

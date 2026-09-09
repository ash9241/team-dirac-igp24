#!/usr/bin/env sage -python
"""Exact scalar-twist closure census for all saved F5/F6 induced actions.

Every saved induced action is a signed action on twelve root pairs.  Its
aligned scalar twist adjoins the central global pair flip.  This script first
enumerates every fixed-point-free centralizer involution of the corresponding
standard transitive group.  The quotient action on the twelve flip-orbits
usually determines the aligned closure uniquely.  In the exceptional
ambiguous cases it reconstructs the saved induced action in its original
coordinates and computes the closure directly.

The current tc0 set is read from the live ledger, not from a frozen JSONL
snapshot.  Arithmetic is not authorized unless an aligned flip is absent and
the exact closure label/signature intersects that set.
"""

from __future__ import annotations

import glob
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
F5_GLOB = str(DATA / "agent_f5_full_ledger_pair_product_actions_shard[0-3]of4.jsonl")
F6 = DATA / "index24_f6a_kummer_action_census.json"
CACHED_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
TARGET_MAP = DATA / "scalar_twist_saved_f5_f6_target_system_map_20260727.jsonl"
ACTIONS = DATA / "scalar_twist_saved_f5_f6_action_closures_20260727.jsonl"
SUMMARY = DATA / "scalar_twist_saved_f5_f6_induced_action_census_20260727.json"


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
    temporary.replace(path)


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def fixed_points(permutation) -> int:
    return 24 - int(libgap.NrMovedPoints(permutation))


def block_action_data(
    blocks: list[tuple[int, int]], permutation
) -> tuple[list[int], list[int]]:
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def induced_permutation(orbit, block_permutation, sign_vector):
    position = {tuple(subset): index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        image_subset = tuple(sorted(block_permutation[index] for index in subset))
        target = position[image_subset]
        sign = sum(sign_vector[index] for index in subset) % 2
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def orbit_permutation(orbit, block_permutation):
    position = {tuple(subset): index for index, subset in enumerate(orbit)}
    return libgap.PermList(
        [
            position[tuple(sorted(block_permutation[index] for index in subset))] + 1
            for subset in orbit
        ]
    )


def global_flip():
    return libgap.PermList(
        [value for index in range(12) for value in (2 * index + 2, 2 * index + 1)]
    )


def blocks_for_flip(flip) -> tuple[tuple[int, int], ...]:
    return tuple(
        (point, int(libgap.OnPoints(point, flip)))
        for point in range(1, 25)
        if point < int(libgap.OnPoints(point, flip))
    )


def target_system_row(target_t: int) -> dict:
    group = libgap.TransitiveGroup(24, target_t)
    generators = list(libgap.GeneratorsOfGroup(group))
    centralizer = libgap.Centralizer(libgap.SymmetricGroup(24), group)
    systems = {}
    for flip in libgap.Elements(centralizer):
        if int(libgap.Order(flip)) != 2 or int(libgap.NrMovedPoints(flip)) != 24:
            continue
        blocks = blocks_for_flip(flip)
        block_sets = [libgap.Set(list(pair)) for pair in blocks]
        quotient = libgap.Action(group, block_sets, libgap.OnSets)
        quotient_t = int(libgap.TransitiveIdentification(quotient))
        flip_in_group = bool(flip in group)
        if flip_in_group:
            closure_t = target_t
            closure_order = int(libgap.Size(group))
        else:
            closure = libgap.Group(generators + [flip])
            closure_t = int(libgap.TransitiveIdentification(closure))
            closure_order = int(libgap.Size(closure))
        systems[blocks] = {
            "blocks": [list(pair) for pair in blocks],
            "closureLabel": f"24T{closure_t}",
            "closureOrder": closure_order,
            "closureT": closure_t,
            "flipInAction": flip_in_group,
            "quotientT12": quotient_t,
        }
    if not systems:
        raise ArithmeticError(f"24T{target_t} has no fixed-point-free centralizer flip")
    return {
        "source": "computed exact standard transitive action",
        "systemCount": len(systems),
        "systems": [systems[key] for key in sorted(systems)],
        "targetLabel": f"24T{target_t}",
        "targetOrder": int(libgap.Size(group)),
        "targetT": target_t,
    }


def normalize_cached_map(row: dict) -> dict:
    return {
        "source": "data/agent_gold_b_even_twist_action_map.jsonl",
        "systemCount": int(row["systemCount"]),
        "systems": [
            {
                "blocks": system["blocks"],
                "closureLabel": system["targetLabel"],
                "closureOrder": int(system["targetOrder"]),
                "closureT": int(system["targetT"]),
                "flipInAction": bool(system["flipInSource"]),
                "quotientT12": int(system["blockActionT12"]),
            }
            for system in row["systems"]
        ],
        "targetLabel": row["sourceLabel"],
        "targetOrder": int(row["sourceOrder"]),
        "targetT": int(row["sourceT"]),
    }


def exact_natural_action(row: dict, family: str, f6_system_cache: dict):
    source_t = int(row["sourceT"])
    source_group = libgap.TransitiveGroup(24, source_t)
    source_generators = list(libgap.GeneratorsOfGroup(source_group))
    if family == "F5":
        blocks = [tuple(map(int, pair)) for pair in row["sourceBlockSystem"]]
        orbit = [tuple(map(int, pair)) for pair in row["pairOrbit"]]
    else:
        cache_key = (source_t, str(row["blockSystemSha256"]))
        blocks = f6_system_cache.get(cache_key)
        if blocks is None:
            centralizer = libgap.Centralizer(
                libgap.SymmetricGroup(24), source_group
            )
            for candidate in libgap.Elements(centralizer):
                if (
                    int(libgap.Order(candidate)) != 2
                    or int(libgap.NrMovedPoints(candidate)) != 24
                ):
                    continue
                candidate_blocks = blocks_for_flip(candidate)
                candidate_sha = hashlib.sha256(
                    canonical_json([list(pair) for pair in candidate_blocks]).encode()
                ).hexdigest()
                f6_system_cache[(source_t, candidate_sha)] = list(candidate_blocks)
            blocks = f6_system_cache.get(cache_key)
            if blocks is None:
                raise ArithmeticError(f"missing exact F6 system {cache_key}")
        orbit = [tuple(map(int, subset)) for subset in row["subsetOrbit"]]
    generator_data = [
        block_action_data(blocks, generator) for generator in source_generators
    ]
    induced_generators = [
        induced_permutation(orbit, block_permutation, sign_vector)
        for block_permutation, sign_vector in generator_data
    ]
    action = libgap.Group(induced_generators)
    action_t = int(libgap.TransitiveIdentification(action))
    if action_t != int(row["targetT"]):
        raise ArithmeticError(
            f"saved target mismatch: expected {row['targetT']}, got {action_t}"
        )
    quotient = libgap.Group(
        [
            orbit_permutation(orbit, block_permutation)
            for block_permutation, _sign_vector in generator_data
        ]
    )
    quotient_t = int(libgap.TransitiveIdentification(quotient))
    flip = global_flip()
    flip_in_action = bool(flip in action)
    if flip_in_action:
        closure = action
    else:
        closure = libgap.Group(induced_generators + [flip])
    closure_t = int(libgap.TransitiveIdentification(closure))
    return {
        "action": action,
        "closureLabel": f"24T{closure_t}",
        "closureOrder": int(libgap.Size(closure)),
        "closureT": closure_t,
        "flip": flip,
        "flipInAction": flip_in_action,
        "quotientT12": quotient_t,
    }


def signature_twist_map(action, flip) -> dict[str, list[int]]:
    mapping = defaultdict(set)
    for conjugacy_class in libgap.ConjugacyClasses(action):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) not in (1, 2):
            continue
        mapping[fixed_points(representative)].add(
            fixed_points(flip * representative)
        )
    return {str(key): sorted(values) for key, values in sorted(mapping.items())}


def main() -> int:
    started = time.monotonic()
    if ACTIONS.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite completed census artifacts")

    f5_paths = sorted(Path(path) for path in glob.glob(F5_GLOB))
    f5_rows = []
    for path in f5_paths:
        f5_rows.extend(read_jsonl(path))
    f6_document = json.loads(F6.read_text())
    f6_rows = [
        row
        for row in f6_document["actionRows"]
        if row.get("transitive") and row.get("targetT") is not None
    ]
    saved = [("F5", row) for row in f5_rows] + [
        ("F6", row) for row in f6_rows
    ]
    target_ts = sorted({int(row["targetT"]) for _family, row in saved})

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        current_tc0 = {
            (str(label), int(r))
            for label, r in connection.execute(
                """
                SELECT t.label,t.r FROM targets AS t
                WHERE t.team_count=0 AND t.discovered=0
                  AND NOT EXISTS(
                    SELECT 1 FROM baseline_pairs AS b
                    WHERE b.label=t.label AND b.r=t.r
                  )
                  AND NOT EXISTS(
                    SELECT 1 FROM verifications AS v
                    WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
                  )
                """
            )
        }
    finally:
        connection.close()
    live_by_label = defaultdict(set)
    for label, r in current_tc0:
        live_by_label[label].add(r)

    cached = {
        int(row["sourceT"]): normalize_cached_map(row)
        for row in read_jsonl(CACHED_MAP)
        if int(row["sourceT"]) in set(target_ts)
    }
    computed = {}
    if TARGET_MAP.exists():
        computed = {
            int(row["targetT"]): row for row in read_jsonl(TARGET_MAP)
        }
    missing = [target_t for target_t in target_ts if target_t not in cached and target_t not in computed]
    for index, target_t in enumerate(missing, start=1):
        computed[target_t] = target_system_row(target_t)
        if index % 25 == 0:
            write_jsonl_atomic(
                TARGET_MAP, [computed[key] for key in sorted(computed)]
            )
            print(
                canonical_json(
                    {
                        "phase": "target-system-map",
                        "completedMissing": index,
                        "missingTotal": len(missing),
                    }
                ),
                flush=True,
            )
    write_jsonl_atomic(TARGET_MAP, [computed[key] for key in sorted(computed)])
    system_map = {**cached, **computed}

    possible_live_systems = []
    for target_t in target_ts:
        for system in system_map[target_t]["systems"]:
            if system["flipInAction"]:
                continue
            live_rs = sorted(live_by_label.get(system["closureLabel"], set()))
            if live_rs:
                possible_live_systems.append(
                    {
                        "actionLabel": f"24T{target_t}",
                        "closureLabel": system["closureLabel"],
                        "closureLiveRs": live_rs,
                        "quotientT12": system["quotientT12"],
                    }
                )

    outcomes_by_target = {}
    for target_t in target_ts:
        by_q = defaultdict(set)
        all_outcomes = set()
        for system in system_map[target_t]["systems"]:
            outcome = (
                bool(system["flipInAction"]),
                int(system["closureT"]),
                int(system["closureOrder"]),
            )
            all_outcomes.add(outcome)
            by_q[int(system["quotientT12"])].add(outcome)
        outcomes_by_target[target_t] = (all_outcomes, by_q)

    action_rows = []
    f6_system_cache = {}
    exact_reconstructions = 0
    q_reconstructions = 0
    q_context_cache = {}
    natural_cache = {}
    for index, (family, row) in enumerate(saved, start=1):
        target_t = int(row["targetT"])
        all_outcomes, by_q = outcomes_by_target[target_t]
        quotient_t = (
            int(row["inducedQuotientT12"])
            if family == "F6"
            else None
        )
        resolution = "unique outcome over every fixed-point-free centralizer flip"
        candidate_outcomes = all_outcomes
        if len(candidate_outcomes) > 1 and quotient_t is None:
            source_t = int(row["sourceT"])
            context = q_context_cache.get(source_t)
            if context is None:
                source_group = libgap.TransitiveGroup(24, source_t)
                source_generators = list(libgap.GeneratorsOfGroup(source_group))
                blocks = [
                    tuple(map(int, pair)) for pair in row["sourceBlockSystem"]
                ]
                generator_data = [
                    block_action_data(blocks, generator)
                    for generator in source_generators
                ]
                context = generator_data
                q_context_cache[source_t] = context
            orbit = [tuple(map(int, pair)) for pair in row["pairOrbit"]]
            quotient = libgap.Group(
                [
                    orbit_permutation(orbit, block_permutation)
                    for block_permutation, _sign_vector in context
                ]
            )
            quotient_t = int(libgap.TransitiveIdentification(quotient))
            q_reconstructions += 1
        if quotient_t is not None and len(candidate_outcomes) > 1:
            candidate_outcomes = by_q.get(quotient_t, set())
            resolution = "exact quotient action identifies centralizer-flip outcome"
        if len(candidate_outcomes) != 1:
            natural_key = (
                family,
                int(row["sourceT"]),
                str(row.get("blockSystemSha256", row.get("sourceBlockSystem"))),
                str(row.get("subsetOrbit", row.get("pairOrbit"))),
            )
            natural = natural_cache.get(natural_key)
            if natural is None:
                natural = exact_natural_action(row, family, f6_system_cache)
                natural_cache[natural_key] = natural
            exact_reconstructions += 1
            quotient_t = int(natural["quotientT12"])
            outcome = (
                bool(natural["flipInAction"]),
                int(natural["closureT"]),
                int(natural["closureOrder"]),
            )
            resolution = "direct reconstruction in saved induced-action coordinates"
        else:
            outcome = next(iter(candidate_outcomes))
            natural = None
        flip_in_action, closure_t, closure_order = outcome
        closure_label = f"24T{closure_t}"
        live_rs = sorted(live_by_label.get(closure_label, set()))
        signature_map = None
        signature_intersection = []
        if not flip_in_action and live_rs:
            if natural is None:
                natural_key = (
                    family,
                    int(row["sourceT"]),
                    str(row.get("blockSystemSha256", row.get("sourceBlockSystem"))),
                    str(row.get("subsetOrbit", row.get("pairOrbit"))),
                )
                natural = natural_cache.get(natural_key)
                if natural is None:
                    natural = exact_natural_action(row, family, f6_system_cache)
                    natural_cache[natural_key] = natural
                    exact_reconstructions += 1
            signature_map = signature_twist_map(
                natural["action"], natural["flip"]
            )
            possible_twisted_rs = {
                value for values in signature_map.values() for value in values
            }
            signature_intersection = sorted(
                possible_twisted_rs.intersection(live_rs)
            )
        action_id = hashlib.sha256(
            canonical_json(
                {
                    "family": family,
                    "pairOrbit": row.get("pairOrbit"),
                    "sourceBlockSystem": row.get("sourceBlockSystem"),
                    "sourceT": row["sourceT"],
                    "subsetOrbit": row.get("subsetOrbit"),
                    "blockSystemSha256": row.get("blockSystemSha256"),
                    "targetT": row["targetT"],
                }
            ).encode()
        ).hexdigest()
        action_rows.append(
            {
                "actionIdSha256": action_id,
                "closureLabel": closure_label,
                "closureLiveRs": live_rs,
                "closureOrder": closure_order,
                "closureT": closure_t,
                "family": family,
                "flipInAction": flip_in_action,
                "quotientT12": quotient_t,
                "resolution": resolution,
                "signatureIntersectionCurrentTc0": signature_intersection,
                "sourceLabel": row["sourceLabel"],
                "sourceT": int(row["sourceT"]),
                "targetLabel": row["targetLabel"],
                "targetT": target_t,
                "twistSignatureMap": signature_map,
            }
        )
        if index % 250 == 0:
            print(
                canonical_json(
                    {
                        "phase": "action-resolution",
                        "completed": index,
                        "total": len(saved),
                    }
                ),
                flush=True,
            )

    ranked_routes = [
        row
        for row in action_rows
        if (
            not row["flipInAction"]
            and row["signatureIntersectionCurrentTc0"]
        )
    ]
    ranked_routes.sort(
        key=lambda row: (
            -len(row["signatureIntersectionCurrentTc0"]),
            row["closureT"],
            row["sourceT"],
            row["actionIdSha256"],
        )
    )
    write_jsonl_atomic(ACTIONS, action_rows)
    summary = {
        "actionCount": len(action_rows),
        "actionOutput": str(ACTIONS.relative_to(ROOT)),
        "arithmeticConstructionAuthorized": bool(ranked_routes),
        "cachedTargetActionMapsUsed": len(cached),
        "computedTargetActionMaps": len(computed),
        "currentTc0Definition": (
            "targets.team_count=0 AND discovered=0 AND nonbaseline AND "
            "locally unowned by any scoreable verification"
        ),
        "currentTc0PairCount": len(current_tc0),
        "exactNaturalActionReconstructions": exact_reconstructions,
        "families": dict(Counter(row["family"] for row in action_rows)),
        "flipAbsentActionCount": sum(
            not row["flipInAction"] for row in action_rows
        ),
        "flipPresentActionCount": sum(
            row["flipInAction"] for row in action_rows
        ),
        "inputSha256": {
            str(path.relative_to(ROOT)): sha256_path(path)
            for path in f5_paths + [F6, CACHED_MAP, DB]
        },
        "networkCalls": 0,
        "possibleLiveSystemsBeforeExactAlignment": possible_live_systems,
        "quotientActionReconstructions": q_reconstructions,
        "rankedRoutes": ranked_routes,
        "rankedRouteCount": len(ranked_routes),
        "runtimeSeconds": time.monotonic() - started,
        "status": (
            "eligible_exact_scalar_twist_routes"
            if ranked_routes
            else "hard_blocked_zero_exact_scalar_twist_routes"
        ),
        "submissionCalls": 0,
        "targetActionLabelCount": len(target_ts),
        "targetSystemMapCheckpoint": str(TARGET_MAP.relative_to(ROOT)),
        "veto": (
            "exclude aligned flips already in G; do not twist an upstream "
            "quotient because even pair/subset scalar products cancel"
        ),
    }
    write_json_atomic(SUMMARY, summary)
    print(canonical_json(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

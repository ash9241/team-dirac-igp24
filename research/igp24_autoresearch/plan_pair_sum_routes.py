#!/usr/bin/env python3
"""Rank exact unordered-pair sibling routes without changing project state.

The expensive pair-sum worker is useful only when the source field's complex
conjugation class can land on a live target *pair*.  The production drivers
currently prefilter on target labels, which admits source signatures that can
only land on already discovered real-root signatures of the same group.

This planner combines three exact, independent pieces of local evidence:

* the GAP pair-orbit action census (target transitive-group labels),
* GAP complex-conjugation profiles (target real-root signatures), and
* the latest SQLite target/ownership snapshot.

It is deliberately read-only: the ledger is opened with ``mode=ro``; no cache,
manifest, pipeline output, or submission is written.  Missing profiles may be
computed in memory with ``--compute-missing-profiles`` and are printed only as
part of the resulting plan.  Redirect stdout to a temporary file if a durable
planning snapshot is wanted.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import sqlite3


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "ledger.sqlite3"
ORBIT_MAP_PATH = DATA / "pair_orbit_map.jsonl"
SIGNATURE_PATHS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
    DATA / "agent_gold_a_missing_profiles.jsonl",
    DATA / "agent_pair_sibling_shard2_profiles.jsonl",
    DATA / "agent_gold_b_backfill_profiles.jsonl",
    DATA / "agent_rank10_pair_stage2_profiles.jsonl",
    DATA / "agent_rank11_pair_stage2_profiles.jsonl",
)
SIGNATURE_WORKER = ROOT / "pair_signature_one.sage.py"
HISTORICAL_RESULTS = DATA / "pair_sum_candidates.jsonl"
MULTI_RESULTS = DATA / "pair_sum_multi_candidates.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_only_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def target_pairs(connection: sqlite3.Connection) -> set[tuple[str, int]]:
    """Return live nonbaseline gold pairs not present in the local ledger."""
    return {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT t.label,t.r
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE status='accepted'
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.team_count=0
              AND b.label IS NULL
              AND owned.label IS NULL
            """
        )
    }


def current_driver_labels(connection: sqlite3.Connection) -> set[str]:
    """Mirror the broad label predicate used by build_pair_sum_pilot.py."""
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT t.label
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            WHERE t.team_count <= 1 AND b.label IS NULL
            """
        )
    }


def source_tasks(connection: sqlite3.Connection, cap: int) -> list[dict]:
    rows = connection.execute(
        """
        WITH ranked_sources AS (
            SELECT
                v.submission_id,
                v.polynomial_index,
                v.label,
                v.t,
                v.r,
                p.coefficient_hash,
                length(p.original_line) AS coefficient_bytes,
                ROW_NUMBER() OVER (
                    PARTITION BY v.label,v.r
                    ORDER BY v.submission_id,v.polynomial_index
                ) AS source_rank
            FROM verifications AS v
            JOIN polynomials AS p
              USING(submission_id,polynomial_index)
            WHERE v.status='accepted'
              AND v.label IS NOT NULL
              AND v.r IS NOT NULL
        )
        SELECT *
        FROM ranked_sources
        WHERE source_rank <= ?
        ORDER BY label,r,source_rank
        """,
        (cap,),
    )
    return [
        {
            "submissionId": str(row["submission_id"]),
            "polynomialIndex": int(row["polynomial_index"]),
            "sourceLabel": str(row["label"]),
            "sourceT": int(row["t"]),
            "sourceR": int(row["r"]),
            "sourceRank": int(row["source_rank"]),
            "sourceCoefficientBytes": int(row["coefficient_bytes"]),
            "sourceCoefficientSha256": str(row["coefficient_hash"]),
        }
        for row in rows
    ]


def load_profiles() -> tuple[dict[str, dict], dict[str, str]]:
    profiles: dict[str, dict] = {}
    provenance: dict[str, str] = {}
    for path in SIGNATURE_PATHS:
        for row in read_jsonl(path):
            if row.get("status") not in (None, "certified"):
                continue
            label = str(row["sourceLabel"])
            profiles[label] = row
            provenance[label] = path.name
    return profiles, provenance


def compute_profile(source: tuple[str, int], timeout: int) -> dict:
    label, t = source
    try:
        completed = subprocess.run(
            [str(Path("/usr/bin/sage")) if Path("/usr/bin/sage").exists() else "sage", "-python", str(SIGNATURE_WORKER), label, str(t)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"sourceLabel": label, "sourceT": t, "status": "timeout"}
    if completed.returncode != 0:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "error",
            "error": completed.stderr[-1000:],
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "invalid_output",
            "error": str(exc),
        }
    row["status"] = "certified"
    return row


def cross_check_profile(profile: dict, orbit: dict) -> None:
    if str(profile["sourceLabel"]) != str(orbit["sourceLabel"]):
        raise ValueError("source-label mismatch")
    if int(profile["sourceT"]) != int(orbit["sourceT"]):
        raise ValueError("source-T mismatch")
    if int(profile["length24OrbitCount"]) != int(orbit["length24OrbitCount"]):
        raise ValueError("length-24 orbit-count mismatch")
    expected = {
        (int(row["orbitIndex"]), str(row["targetLabel"]))
        for row in orbit["targets"]
    }
    for class_profile in profile["profiles"]:
        actual = {
            (int(row["orbitIndex"]), str(row["targetLabel"]))
            for row in class_profile["orbitSignatures"]
        }
        if actual != expected:
            raise ValueError(
                f"class {class_profile['classIndex']} action-map mismatch"
            )


def identity_profile(orbit: dict) -> dict:
    """Return the universal profile forced by a totally real source field.

    Source r=24 means complex conjugation is the identity element, so its
    image fixes all 24 points in every length-24 action.  This needs no GAP
    conjugacy-class census and is exact for every source group.
    """
    return {
        "sourceLabel": str(orbit["sourceLabel"]),
        "sourceT": int(orbit["sourceT"]),
        "length24OrbitCount": int(orbit["length24OrbitCount"]),
        "profiles": [
            {
                "classIndex": -1,
                "classSize": 1,
                "order": 1,
                "sourceR": 24,
                "orbitSignatures": [
                    {
                        "orbitIndex": int(row["orbitIndex"]),
                        "targetLabel": str(row["targetLabel"]),
                        "targetR": 24,
                    }
                    for row in orbit["targets"]
                ],
            }
        ],
    }


def class_pair_set(
    class_profile: dict, gold: set[tuple[str, int]]
) -> set[tuple[str, int]]:
    return {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in class_profile["orbitSignatures"]
        if (str(row["targetLabel"]), int(row["targetR"])) in gold
    }


def route_for_task(
    task: dict,
    orbit: dict,
    profile: dict,
    profile_source: str,
    gold: set[tuple[str, int]],
) -> dict | None:
    compatible = [
        row
        for row in profile["profiles"]
        if int(row["sourceR"]) == int(task["sourceR"])
    ]
    if not compatible:
        return None
    class_sets = [class_pair_set(row, gold) for row in compatible]
    possible = set().union(*class_sets)
    forced = set.intersection(*class_sets) if class_sets else set()
    total_mass = sum(int(row["classSize"]) for row in compatible)
    gold_mass = sum(
        int(row["classSize"])
        for row, pairs in zip(compatible, class_sets)
        if pairs
    )
    expected_gold = sum(
        int(row["classSize"]) * len(pairs)
        for row, pairs in zip(compatible, class_sets)
    ) / total_mass
    pair_mass = Counter()
    for row, pairs in zip(compatible, class_sets):
        for pair in pairs:
            pair_mass[pair] += int(row["classSize"])

    result = {
        **task,
        "lane": "single" if int(orbit["length24OrbitCount"]) == 1 else "multi",
        "length24OrbitCount": int(orbit["length24OrbitCount"]),
        "profileSource": profile_source,
        "compatibleClassIndexes": [int(row["classIndex"]) for row in compatible],
        "compatibleClassCount": len(compatible),
        "compatibleClassMass": total_mass,
        "goldClassMass": gold_mass,
        "goldClassCoverage": gold_mass / total_mass,
        "guaranteedAnyGold": all(bool(pairs) for pairs in class_sets),
        "expectedGoldPairs": expected_gold,
        "forcedGoldPairs": [
            {"label": label, "r": r} for label, r in sorted(forced)
        ],
        "possibleGoldPairs": [
            {
                "label": label,
                "r": r,
                "conditionalClassMass": pair_mass[(label, r)] / total_mass,
            }
            for label, r in sorted(possible)
        ],
    }
    return result


def route_key(route: dict) -> tuple:
    return (
        not bool(route["guaranteedAnyGold"]),
        -len(route["forcedGoldPairs"]),
        -float(route["expectedGoldPairs"]),
        -float(route["goldClassCoverage"]),
        0 if route["lane"] == "multi" else 1,
        int(route["sourceCoefficientBytes"]),
        int(route["sourceT"]),
        int(route["sourceR"]),
    )


def historical_cost() -> dict:
    rows = read_jsonl(HISTORICAL_RESULTS)
    values = [
        float(row["workerWallSeconds"])
        for row in rows
        if row.get("workerWallSeconds") is not None
    ]
    if not values:
        return {}
    return {
        "observations": len(values),
        "medianWorkerWallSeconds": statistics.median(values),
        "p90WorkerWallSeconds": statistics.quantiles(values, n=10)[8],
    }


def set_certified_gold_blocks(
    gold: set[tuple[str, int]], profiles: dict[str, dict]
) -> list[dict]:
    """Find unresolved factor blocks whose exact label multiset is all gold.

    Individual factor labels need not be known when every label in an
    actual-real-signature block is valuable.  Submitting the entire block then
    realizes the proven label multiset in some order.  This is stronger than
    the production stable-assignment rule, which currently accepts a block
    only when every label in it is identical.
    """
    blocks = []
    for result in read_jsonl(MULTI_RESULTS):
        if result.get("status") != "certified_multi":
            continue
        profile = profiles.get(str(result["sourceLabel"]))
        if profile is None:
            continue
        actual_r = sorted(int(row["targetR"]) for row in result["candidates"])
        compatible = [
            row
            for row in profile["profiles"]
            if int(row["sourceR"]) == int(result["sourceR"])
            and sorted(
                int(target["targetR"])
                for target in row["orbitSignatures"]
            )
            == actual_r
        ]
        if not compatible:
            continue
        for r in sorted(set(actual_r)):
            label_multisets = [
                tuple(
                    sorted(
                        str(target["targetLabel"])
                        for target in row["orbitSignatures"]
                        if int(target["targetR"]) == r
                    )
                )
                for row in compatible
            ]
            if any(labels != label_multisets[0] for labels in label_multisets[1:]):
                continue
            labels = label_multisets[0]
            candidates = [
                row
                for row in result["candidates"]
                if int(row["targetR"]) == r
            ]
            if len(labels) != len(candidates) or not labels:
                continue
            pairs = {(label, r) for label in labels}
            if not all(pair in gold for pair in pairs):
                continue
            blocks.append(
                {
                    "sourceLabel": str(result["sourceLabel"]),
                    "sourceR": int(result["sourceR"]),
                    "sourceSubmissionId": str(result["sourceSubmissionId"]),
                    "sourcePolynomialIndex": int(result["sourcePolynomialIndex"]),
                    "targetR": r,
                    "exactTargetLabelMultiset": list(labels),
                    "distinctGoldPairs": [
                        {"label": label, "r": target_r}
                        for label, target_r in sorted(pairs)
                    ],
                    "candidateFactors": [
                        {
                            "factorIndex": int(row["factorIndex"]),
                            "coefficientSha256": str(row["coefficientSha256"]),
                        }
                        for row in candidates
                    ],
                    "proof": (
                        "actual factor-r multiset plus invariant target-label "
                        "multiset across every compatible conjugacy class"
                    ),
                }
            )
    return sorted(
        blocks,
        key=lambda row: (
            -len(row["distinctGoldPairs"]),
            int(row["sourceLabel"][3:]),
            int(row["sourceR"]),
            int(row["targetR"]),
        ),
    )


def frobenius_candidate_rows(
    gold: set[tuple[str, int]], profiles: dict[str, dict]
) -> list[dict]:
    """Rank realized multi packets where only label assignment remains.

    The pair resolvent has already fixed the multiset of factor real-root
    counts.  We first restrict to the compatible complex-conjugation profiles,
    then retain only mixed-value blocks: homogeneous-label blocks are handled
    by stable assignment and all-gold blocks need no individual assignment.
    """
    rows = []
    for result in read_jsonl(MULTI_RESULTS):
        if result.get("status") != "certified_multi":
            continue
        profile = profiles.get(str(result["sourceLabel"]))
        if profile is None:
            continue
        actual_r = sorted(int(row["targetR"]) for row in result["candidates"])
        compatible = [
            row
            for row in profile["profiles"]
            if int(row["sourceR"]) == int(result["sourceR"])
            and sorted(
                int(target["targetR"])
                for target in row["orbitSignatures"]
            )
            == actual_r
        ]
        if not compatible:
            continue
        mixed_blocks = []
        for r in sorted(set(actual_r)):
            label_multisets = [
                tuple(
                    sorted(
                        str(target["targetLabel"])
                        for target in row["orbitSignatures"]
                        if int(target["targetR"]) == r
                    )
                )
                for row in compatible
            ]
            possible_labels = sorted(set().union(*(set(x) for x in label_multisets)))
            possible_gold = {
                (label, r) for label in possible_labels if (label, r) in gold
            }
            if not possible_gold:
                continue
            invariant = all(
                labels == label_multisets[0] for labels in label_multisets[1:]
            )
            # Existing stable assignment handles invariant one-label blocks;
            # the set-certified rule handles invariant all-gold blocks.
            if invariant and len(set(label_multisets[0])) == 1:
                continue
            if invariant and all((label, r) in gold for label in label_multisets[0]):
                continue
            gold_sets = [
                {(label, r) for label in labels if (label, r) in gold}
                for labels in label_multisets
            ]
            forced_gold = set.intersection(*gold_sets) if gold_sets else set()
            mixed_blocks.append(
                {
                    "targetR": r,
                    "possibleLabels": possible_labels,
                    "possibleGoldPairs": [
                        {"label": label, "r": target_r}
                        for label, target_r in sorted(possible_gold)
                    ],
                    "forcedGoldPairs": [
                        {"label": label, "r": target_r}
                        for label, target_r in sorted(forced_gold)
                    ],
                    "candidateFactorIndexes": [
                        int(row["factorIndex"])
                        for row in result["candidates"]
                        if int(row["targetR"]) == r
                    ],
                }
            )
        if not mixed_blocks:
            continue
        possible = {
            (str(pair["label"]), int(pair["r"]))
            for block in mixed_blocks
            for pair in block["possibleGoldPairs"]
        }
        forced = {
            (str(pair["label"]), int(pair["r"]))
            for block in mixed_blocks
            for pair in block["forcedGoldPairs"]
        }
        rows.append(
            {
                "sourceLabel": str(result["sourceLabel"]),
                "sourceR": int(result["sourceR"]),
                "sourceSubmissionId": str(result["sourceSubmissionId"]),
                "sourcePolynomialIndex": int(result["sourcePolynomialIndex"]),
                "compatibleClassIndexes": [
                    int(row["classIndex"]) for row in compatible
                ],
                "forcedGoldPairCount": len(forced),
                "possibleGoldPairCount": len(possible),
                "blocks": mixed_blocks,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -int(row["forcedGoldPairCount"]),
            -int(row["possibleGoldPairCount"]),
            int(row["sourceLabel"][3:]),
            int(row["sourceR"]),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-source-polynomials-per-pair", type=int, default=1)
    parser.add_argument("--compute-missing-profiles", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()
    if args.max_source_polynomials_per_pair < 1:
        parser.error("--max-source-polynomials-per-pair must be positive")

    orbit_rows = read_jsonl(ORBIT_MAP_PATH)
    orbits = {str(row["sourceLabel"]): row for row in orbit_rows}
    if len(orbits) != len(orbit_rows):
        raise ValueError("duplicate source label in orbit map")

    connection = read_only_connection()
    try:
        gold = target_pairs(connection)
        broad_labels = current_driver_labels(connection)
        sources = source_tasks(connection, args.max_source_polynomials_per_pair)
    finally:
        connection.close()
    gold_labels = {label for label, _r in gold}

    mapped = [task for task in sources if task["sourceLabel"] in orbits]
    single_current = [
        task
        for task in mapped
        if int(orbits[task["sourceLabel"]]["length24OrbitCount"]) == 1
        and str(orbits[task["sourceLabel"]]["targets"][0]["targetLabel"])
        != task["sourceLabel"]
        and str(orbits[task["sourceLabel"]]["targets"][0]["targetLabel"])
        in broad_labels
    ]
    # Do not discard a faithful size-24 action merely because GAP gives its
    # image the same 24T label as the source action.  Such an action can be an
    # outer-automorphism twist: its image of complex conjugation can have a
    # different fixed-point count.  Since ``gold`` excludes locally owned
    # pairs, a retained same-label route necessarily changes r and is not the
    # already owned source pair.
    label_prefiltered = [
        task
        for task in mapped
        if int(orbits[task["sourceLabel"]]["length24OrbitCount"]) >= 1
        and any(
            str(row["targetLabel"]) in gold_labels
            for row in orbits[task["sourceLabel"]]["targets"]
        )
    ]
    single_label_prefiltered = [
        task
        for task in label_prefiltered
        if int(orbits[task["sourceLabel"]]["length24OrbitCount"]) == 1
    ]
    multi_label_prefiltered = [
        task
        for task in label_prefiltered
        if int(orbits[task["sourceLabel"]]["length24OrbitCount"]) > 1
    ]

    profiles, profile_sources = load_profiles()
    relevant_labels = {task["sourceLabel"] for task in label_prefiltered}
    profile_required_labels = {
        task["sourceLabel"]
        for task in label_prefiltered
        if int(task["sourceR"]) != 24
    }
    pending_labels = sorted(
        profile_required_labels - profiles.keys(),
        key=lambda label: int(orbits[label]["sourceT"]),
    )
    failures = []
    if pending_labels and args.compute_missing_profiles:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.workers)
        ) as executor:
            futures = {
                executor.submit(
                    compute_profile,
                    (label, int(orbits[label]["sourceT"])),
                    args.timeout,
                ): label
                for label in pending_labels
            }
            for index, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                row = future.result()
                label = str(row["sourceLabel"])
                if row.get("status") == "certified":
                    profiles[label] = row
                    profile_sources[label] = "computed_in_memory"
                else:
                    failures.append(row)
                if index % 25 == 0 or index == len(pending_labels):
                    print(
                        f"profiled {index}/{len(pending_labels)}: "
                        f"{label} status={row.get('status')}",
                        file=sys.stderr,
                        flush=True,
                    )

    routes = []
    cross_check_failures = []
    checked_labels = set()
    analyzed_task_ids = set()
    for task in label_prefiltered:
        label = task["sourceLabel"]
        profile = profiles.get(label)
        if profile is None:
            if int(task["sourceR"]) != 24:
                continue
            profile = identity_profile(orbits[label])
            profile_source = "universal_identity_class"
        else:
            profile_source = profile_sources[label]
        if profile_source != "universal_identity_class" and label not in checked_labels:
            try:
                cross_check_profile(profile, orbits[label])
            except ValueError as exc:
                cross_check_failures.append(
                    {"sourceLabel": label, "error": str(exc)}
                )
                continue
            checked_labels.add(label)
        analyzed_task_ids.add(
            (
                task["submissionId"],
                int(task["polynomialIndex"]),
            )
        )
        route = route_for_task(
            task,
            orbits[label],
            profile,
            profile_source,
            gold,
        )
        if route is not None and route["possibleGoldPairs"]:
            routes.append(route)
    routes.sort(key=route_key)

    forced_representatives: dict[tuple[str, int], dict] = {}
    for route in routes:
        for pair in route["forcedGoldPairs"]:
            key = (str(pair["label"]), int(pair["r"]))
            forced_representatives.setdefault(key, route)

    profiled_tasks = len(analyzed_task_ids)
    missing_tasks = len(label_prefiltered) - profiled_tasks
    cost = historical_cost()
    certified_blocks = set_certified_gold_blocks(gold, profiles)
    frobenius_rows = frobenius_candidate_rows(gold, profiles)
    certified_block_pairs = {
        (str(pair["label"]), int(pair["r"]))
        for block in certified_blocks
        for pair in block["distinctGoldPairs"]
    }
    median = cost.get("medianWorkerWallSeconds")
    projected_seconds = (
        len(routes) * float(median) / max(1, args.workers)
        if median is not None and missing_tasks == 0
        else None
    )
    summary = {
        "snapshot": {
            "mappedSourceLabels": len(orbits),
            "verifiedSourcePairsAtCap": len(sources),
            "liveUnownedNonbaselineGoldPairs": len(gold),
            "liveGoldLabels": len(gold_labels),
        },
        "taskReduction": {
            "currentSingleDriverTasks": len(single_current),
            "singleTasksAfterGoldLabelFilter": len(single_label_prefiltered),
            "sameLabelSingleTasksAfterGoldLabelFilter": sum(
                str(orbits[task["sourceLabel"]]["targets"][0]["targetLabel"])
                == task["sourceLabel"]
                for task in single_label_prefiltered
            ),
            "multiTasksAfterGoldLabelFilter": len(multi_label_prefiltered),
            "allTasksAfterGoldLabelFilter": len(label_prefiltered),
            "profiledTasks": profiled_tasks,
            "missingProfileTasks": missing_tasks,
            "missingProfileLabels": len(profile_required_labels - checked_labels),
            "exactPairFeasibleTasksAmongProfiled": len(routes),
            "exactRejectedTasksAmongProfiled": profiled_tasks - len(routes),
            "guaranteedAnyGoldTasks": sum(
                bool(route["guaranteedAnyGold"]) for route in routes
            ),
            "distinctForcedGoldPairs": len(forced_representatives),
        },
        "historicalCost": {
            **cost,
            "workersForProjection": max(1, args.workers),
            "projectedFilteredBatchWallSecondsIfFullyProfiled": projected_seconds,
        },
        "profileFailures": failures,
        "crossCheckFailures": cross_check_failures,
        "setCertifiedAllGold": {
            "blocks": len(certified_blocks),
            "candidateFactors": sum(
                len(block["candidateFactors"]) for block in certified_blocks
            ),
            "distinctGoldPairs": len(certified_block_pairs),
            "rows": certified_blocks,
        },
        "frobeniusPriority": {
            "sourceRows": len(frobenius_rows),
            "forcedGoldPairsAcrossRows": sum(
                int(row["forcedGoldPairCount"]) for row in frobenius_rows
            ),
            "possibleGoldPairsAcrossRows": len(
                {
                    (str(pair["label"]), int(pair["r"]))
                    for row in frobenius_rows
                    for block in row["blocks"]
                    for pair in block["possibleGoldPairs"]
                }
            ),
            "rows": frobenius_rows,
        },
        "topRoutes": routes[: max(0, args.top)],
        "forcedPairRepresentatives": [
            {
                "targetLabel": label,
                "targetR": r,
                "sourceLabel": route["sourceLabel"],
                "sourceR": route["sourceR"],
                "submissionId": route["submissionId"],
                "polynomialIndex": route["polynomialIndex"],
            }
            for (label, r), route in sorted(forced_representatives.items())
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failures and not cross_check_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

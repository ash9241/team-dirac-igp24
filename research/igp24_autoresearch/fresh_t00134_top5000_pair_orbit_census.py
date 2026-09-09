#!/usr/bin/env python3
"""Exact pair-action frontier for T00134's verified top-5000 prefix.

This is the k=1..6 companion to ``fresh_t00134_pair_orbit_census.py``.  It
keeps the exact GAP action/profile proofs, expands the target boundary to the
verified 5,000-row prefix, excludes locally owned/baseline/known/receipted/
outboxed target pairs, and ranks untested accepted source polynomials by a
class-weighted generalized relative-swing upper bound.

No candidate coefficients are generated here.  Consequently the ranking uses
the explicitly named "current-min-preserving" upper bound

    2**(-kTeams) + opponent_current_points / 2

for each factor that hits a prefix pair.  Once a candidate field discriminant
is known, its exact swing must replace this structural upper bound.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import analyze_rank12_routes as shared
import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import fresh_t00134_pair_orbit_census as sole
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ORBIT_MAP = DATA / "pair_orbit_map.jsonl"
PREFIX = DATA / "rank10_t00134_top5000_placements_20260730.jsonl"
CHECKPOINT = DATA / "rank10_t00134_top5000_checkpoint_20260730.json"
SEALED_EXACT_AUDIT = (
    DATA / "rank10_t00134_top5000_exact_intersection_certificate.json"
)
PROFILE_CACHE = DATA / "fresh_t00134_top5000_pair_orbit_profiles.jsonl"
FRONTIER = DATA / "fresh_t00134_top5000_pair_orbit_frontier.jsonl"
TESTED_ROUTES = DATA / "fresh_t00134_top5000_pair_orbit_tested_routes.jsonl"
SUMMARY = DATA / "fresh_t00134_top5000_pair_orbit_summary.json"
PREFIX_SIZE = 5000
PAGE_LIMIT = 50
ARTIFACT_TAG = "top5000"
MAX_K_TEAMS = 6

TEAM_ID = sole.TEAM_ID
TEAM_NUMBER = sole.TEAM_NUMBER


def validated_prefix() -> dict[tuple[str, int], dict]:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    if (
        checkpoint.get("schemaVersion") != "rank10-placement-prefix-checkpoint-v1"
        or checkpoint.get("complete") is not True
        or checkpoint.get("teamId") != TEAM_ID
        or checkpoint.get("teamNumber") != TEAM_NUMBER
        or int(checkpoint.get("pageLimit", 0)) != PAGE_LIMIT
        or int(checkpoint.get("pages", 0)) != PAGE_LIMIT
        or checkpoint.get("cursor") is not None
    ):
        raise ValueError("top-5000 checkpoint identity/completeness check failed")
    checkpoint_rows = checkpoint.get("rows")
    rows = shared.read_jsonl(PREFIX)
    checkpoint_fields = (
        "discSource",
        "isSolvable",
        "kTeams",
        "label",
        "minScoringDiscAbs",
        "points",
        "r",
        "scoringDiscAbs",
        "t",
    )
    projected_rows = [
        {key: row.get(key) for key in checkpoint_fields} for row in rows
    ]
    if (
        not isinstance(checkpoint_rows, list)
        or len(checkpoint_rows) != PREFIX_SIZE
        or len(rows) != PREFIX_SIZE
        or checkpoint_rows != projected_rows
    ):
        raise ValueError("top-5000 checkpoint and JSONL rows differ")
    for row in rows:
        if (
            str(row.get("teamId")) != TEAM_ID
            or str(row.get("teamNumber")) != TEAM_NUMBER
            or not 1 <= int(row.get("kTeams", 0)) <= MAX_K_TEAMS
            or float(row.get("points", 0)) <= 0
        ):
            raise ValueError("prefix row identity/kTeams/points invariant failed")
    points = [float(row["points"]) for row in rows]
    if points != sorted(points, reverse=True):
        raise ValueError("top-5000 prefix is not ordered by descending points")
    pairs = {(str(row["label"]), int(row["r"])): row for row in rows}
    if len(pairs) != len(rows):
        raise ValueError("duplicate pair in top-5000 prefix")
    return pairs


def relevant_accepted_presentations(
    connection: sqlite3.Connection, relevant_labels: set[str]
) -> dict[tuple[str, int], list[dict]]:
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    labels = sorted(relevant_labels)
    if not labels:
        return grouped
    placeholders = ",".join("?" for _ in labels)
    query = f"""
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               v.field_disc_abs,v.disc_source,p.coefficient_hash,p.original_line
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
          AND v.label IN ({placeholders})
          AND v.r IS NOT NULL
        ORDER BY v.label,v.r,length(p.original_line),
                 v.submission_id,v.polynomial_index
    """
    for row in connection.execute(query, labels):
        grouped[(str(row[0]), int(row[2]))].append(
            {
                "sourceLabel": str(row[0]),
                "sourceT": int(row[1]),
                "sourceR": int(row[2]),
                "submissionId": str(row[3]),
                "polynomialIndex": int(row[4]),
                "sourceFieldDiscAbs": str(row[5]) if row[5] else None,
                "sourceDiscProof": str(row[6]) if row[6] else None,
                "sourceCoefficientSha256": str(row[7]),
                "sourceCoefficientBytes": len(str(row[8]).encode("utf-8")),
            }
        )
    return grouped


def candidate_profile_paths() -> list[Path]:
    paths = set()
    for pattern in ("*profile*.jsonl", "*signature*.jsonl"):
        paths.update(DATA.rglob(pattern))
    paths.update(sole.PROFILE_INPUTS)
    paths.add(sole.PROFILE_CACHE)
    paths.add(PROFILE_CACHE)
    return sorted(path for path in paths if path.exists())


def load_profiles(
    relevant: dict[str, dict], compute_missing: bool, timeout: int
) -> tuple[dict[str, dict], dict[str, str], list[dict]]:
    candidates: dict[str, tuple[dict, Path]] = {}
    for path in candidate_profile_paths():
        for row in shared.read_jsonl(path):
            label = str(row.get("sourceLabel"))
            if (
                label in relevant
                and row.get("status") in (None, "certified")
                and isinstance(row.get("profiles"), list)
                and row.get("length24OrbitCount") is not None
            ):
                candidates[label] = (row, path)

    profiles: dict[str, dict] = {}
    sources: dict[str, str] = {}
    failures: list[dict] = []
    for label, (profile, path) in candidates.items():
        try:
            profiles[label] = shared.verified_profile(profile, relevant[label])
            sources[label] = str(path.relative_to(ROOT))
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(
                {
                    "sourceLabel": label,
                    "status": "cached_profile_cross_check_error",
                    "path": str(path.relative_to(ROOT)),
                    "error": str(exc),
                }
            )

    missing = [
        (label, int(orbit["sourceT"]))
        for label, orbit in sorted(relevant.items())
        if label not in profiles
    ]
    if compute_missing:
        # Repository-wide invariant: one Sage/GAP worker at a time.
        for source in missing:
            row = shared.compute_profile(source, timeout)
            if row.get("status") != "certified":
                failures.append(row)
                continue
            label = str(row["sourceLabel"])
            try:
                profiles[label] = shared.verified_profile(row, relevant[label])
                sources[label] = "computed_fresh_t00134_top5000"
            except (KeyError, TypeError, ValueError) as exc:
                failures.append(
                    {
                        "sourceLabel": label,
                        "status": "computed_profile_cross_check_error",
                        "error": str(exc),
                    }
                )

    shared.write_jsonl_atomic(
        PROFILE_CACHE,
        profiles.values(),
        sort_key=lambda row: int(row["sourceT"]),
    )
    return profiles, sources, failures


def exclusion_boundary(
    connection: sqlite3.Connection,
    prefix_pairs: set[tuple[str, int]],
) -> tuple[set[tuple[str, int]], dict]:
    _exact_pool, _single_rows, _exact_meta, pair_index = raid.exact_corpus(
        connection
    )
    _receipt_hashes, receipt_pairs, receipt_meta = exact_single.receipt_exclusions(
        lane.RECEIPTS, DATA, connection, pair_index
    )
    _outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
        pair_index, connection
    )
    snapshot = pair_routes.load_ledger_snapshot(connection)
    excluded = (
        snapshot["baseline"]
        | snapshot["owned"]
        | snapshot["knownPairs"]
        | receipt_pairs
        | outbox_pairs
    )
    eligible = prefix_pairs - excluded

    if SEALED_EXACT_AUDIT is not None:
        sealed = json.loads(SEALED_EXACT_AUDIT.read_text(encoding="utf-8"))
        expected = int(
            sealed["boundary"]["eligibleOpponentPairsAfterAllExclusions"]
        )
        if len(eligible) != expected:
            raise ValueError(
                f"fresh eligibility set has {len(eligible)} pairs; "
                f"sealed exact audit recorded {expected}"
            )
    return eligible, {
        "prefixPairs": len(prefix_pairs),
        "eligiblePairs": len(eligible),
        "baselinePairs": len(snapshot["baseline"] & prefix_pairs),
        "ownedPairs": len(snapshot["owned"] & prefix_pairs),
        "knownVerificationPairs": len(snapshot["knownPairs"] & prefix_pairs),
        "receiptPairs": len(receipt_pairs & prefix_pairs),
        "outboxPairs": len(outbox_pairs & prefix_pairs),
        "receiptAudit": receipt_meta,
        "outboxAudit": {
            key: outbox_meta.get(key)
            for key in (
                "outboxFiles",
                "nonemptyOutboxFiles",
                "canonicalPolynomialRows",
                "distinctCoefficientHashes",
                "distinctPairsExcluded",
            )
        },
    }


def pair_swing_upper_bound(row: dict) -> float:
    """Current-min-preserving relative swing if our candidate ratio is one."""

    k = int(row["kTeams"])
    points = float(row["points"])
    # Joining a pair halves each incumbent's base score; our maximum marginal
    # score is 2**(-k), before any discriminant penalty.
    return 2.0 ** (-k) + points / 2.0


def annotate_generalized_swing(
    route: dict,
    orbit: dict,
    profile: dict,
    targets: dict[tuple[str, int], dict],
) -> None:
    source_r = int(route["sourceR"])
    if source_r == 24:
        profile = sole.identity_profile(orbit)
    classes = [
        row
        for row in profile["profiles"]
        if int(row["sourceR"]) == source_r
    ]
    class_weight = sum(int(row["classSize"]) for row in classes)
    weighted_swing = 0.0
    maximum = 0.0
    for class_row in classes:
        pairs = {
            (str(signature["targetLabel"]), int(signature["targetR"]))
            for signature in class_row.get("orbitSignatures", [])
            if (str(signature["targetLabel"]), int(signature["targetR"])) in targets
        }
        class_swing = sum(pair_swing_upper_bound(targets[pair]) for pair in pairs)
        weighted_swing += int(class_row["classSize"]) * class_swing
        maximum = max(maximum, class_swing)
    route["expectedGeneralizedRelativeSwingUpperBound"] = (
        weighted_swing / class_weight
    )
    route["maximumClassGeneralizedRelativeSwingUpperBound"] = maximum
    route["generalizedSwingBoundKind"] = (
        "current_min_preserving_candidate_ratio_one"
    )
    route["reachablePairCompetition"] = [
        {
            "label": pair["label"],
            "r": int(pair["r"]),
            "kTeams": int(targets[(str(pair["label"]), int(pair["r"]))]["kTeams"]),
            "opponentCurrentPoints": float(
                targets[(str(pair["label"]), int(pair["r"]))]["points"]
            ),
            "generalizedRelativeSwingUpperBound": pair_swing_upper_bound(
                targets[(str(pair["label"]), int(pair["r"]))]
            ),
            "holderScoringDiscAbs": str(
                targets[(str(pair["label"]), int(pair["r"]))].get(
                    "scoringDiscAbs"
                )
            ),
            "minimumScoringDiscAbs": str(
                targets[(str(pair["label"]), int(pair["r"]))].get(
                    "minScoringDiscAbs"
                )
            ),
        }
        for pair in route["reachableTargetPairs"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compute-missing-profiles", action="store_true")
    parser.add_argument("--profile-timeout", type=int, default=180)
    args = parser.parse_args()
    if args.profile_timeout < 1:
        parser.error("--profile-timeout must be positive")

    prefix = validated_prefix()
    orbit_rows = shared.read_jsonl(ORBIT_MAP)
    orbit_by_label = {str(row["sourceLabel"]): row for row in orbit_rows}
    if len(orbit_by_label) != len(orbit_rows):
        raise ValueError("duplicate source label in pair action map")

    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        eligible, exclusion_meta = exclusion_boundary(connection, set(prefix))
        eligible_targets = {pair: prefix[pair] for pair in eligible}
        target_labels = {label for label, _r in eligible}
        accepted_labels = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT label FROM verifications
                WHERE status='accepted' AND scoreable=1 AND label IS NOT NULL
                """
            )
        }
        relevant = {
            label: row
            for label, row in orbit_by_label.items()
            if label in accepted_labels
            and any(
                str(target["targetLabel"]) in target_labels
                for target in row.get("targets", [])
            )
        }
        accepted = relevant_accepted_presentations(connection, set(relevant))

    profiles, profile_sources, failures = load_profiles(
        relevant, args.compute_missing_profiles, args.profile_timeout
    )
    presentation_hash = {
        (str(row["submissionId"]), int(row["polynomialIndex"])): str(
            row["sourceCoefficientSha256"]
        )
        for rows in accepted.values()
        for row in rows
    }
    tested_hashes, tested_evidence = sole.tested_source_hashes(presentation_hash)
    routes, tested_routes = sole.build_routes(
        accepted,
        relevant,
        profiles,
        eligible_targets,
        tested_hashes,
        tested_evidence,
    )
    for route in (*routes, *tested_routes):
        source_label = str(route["sourceLabel"])
        profile = profiles.get(source_label)
        if int(route["sourceR"]) == 24:
            profile = sole.identity_profile(relevant[source_label])
        if profile is None:
            raise AssertionError("route emitted without exact signature profile")
        annotate_generalized_swing(
            route,
            relevant[source_label],
            profile,
            eligible_targets,
        )

    routes.sort(
        key=lambda row: (
            -float(row["expectedGeneralizedRelativeSwingUpperBound"]),
            -float(row["maximumClassGeneralizedRelativeSwingUpperBound"]),
            -int(row["guaranteedTargetPairCount"]),
            int(row["sourceCoefficientBytes"]),
            int(row["sourceT"]),
            int(row["sourceR"]),
            str(row["sourceCoefficientSha256"]),
        )
    )
    for rank, route in enumerate(routes, start=1):
        route["priorityRank"] = rank
    shared.write_jsonl_atomic(
        FRONTIER, routes, sort_key=lambda row: int(row["priorityRank"])
    )
    shared.write_jsonl_atomic(
        TESTED_ROUTES,
        tested_routes,
        sort_key=lambda row: (
            int(row["sourceT"]),
            int(row["sourceR"]),
            str(row["sourceCoefficientSha256"]),
        ),
    )

    missing = sorted(set(relevant) - set(profiles))
    summary = {
        "schemaVersion": f"fresh-t00134-{ARTIFACT_TAG}-pair-orbit-census-v2",
        "method": {
            "action": "exact GAP unordered-pair orbit map",
            "signature": "exact complex-conjugation class action profiles",
            "multiOrbitPolicy": "all exact degree-24 pair orbits retained",
            "generalizedRelativeSwingUpperBound": (
                "2^(-kTeams) + opponentCurrentPoints/2, "
                "then class-size weighted over exact reachable factors"
            ),
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "provenance": {
            "prefix": {"path": str(PREFIX), "sha256": shared.sha256_file(PREFIX)},
            "checkpoint": {
                "path": str(CHECKPOINT),
                "sha256": shared.sha256_file(CHECKPOINT),
            },
            "ledger": str(DB),
            "orbitMap": {
                "path": str(ORBIT_MAP),
                "sha256": shared.sha256_file(ORBIT_MAP),
            },
            "profileCache": {
                "path": str(PROFILE_CACHE),
                "sha256": shared.sha256_file(PROFILE_CACHE),
            },
        },
        "exclusions": exclusion_meta,
        "profileSources": profile_sources,
        "profileFailures": failures,
        "profileMissing": missing,
        "summary": {
            "prefixPairs": len(prefix),
            "eligiblePairsAfterAllExclusions": len(eligible_targets),
            "eligibleTargetLabels": len(target_labels),
            "mappedAcceptedSourceLabels": len(relevant),
            "mappedMultiOrbitSourceLabels": sum(
                int(row["length24OrbitCount"]) > 1 for row in relevant.values()
            ),
            "profilesCertified": len(profiles),
            "profilesMissing": len(missing),
            "untestedDistinctSourceRoutes": len(routes),
            "testedDistinctSourceRoutes": len(tested_routes),
            "multiOrbitUntestedRoutes": sum(
                int(row["length24OrbitCount"]) > 1 for row in routes
            ),
            "guaranteedUntestedRoutes": sum(
                int(row["guaranteedTargetPairCount"]) > 0 for row in routes
            ),
            "distinctReachableEligiblePairs": len(
                {
                    (str(pair["label"]), int(pair["r"]))
                    for route in routes
                    for pair in route["reachableTargetPairs"]
                }
            ),
            "sumExpectedGeneralizedRelativeSwingUpperBound": sum(
                float(row["expectedGeneralizedRelativeSwingUpperBound"])
                for row in routes
            ),
        },
        "topRoutes": [
            {
                key: route[key]
                for key in (
                    "priorityRank",
                    "sourceLabel",
                    "sourceR",
                    "submissionId",
                    "polynomialIndex",
                    "sourceCoefficientSha256",
                    "length24OrbitCount",
                    "reachableTargetPairs",
                    "guaranteedTargetPairs",
                    "estimatedAnyT00134HitProbability",
                    "expectedGeneralizedRelativeSwingUpperBound",
                    "maximumClassGeneralizedRelativeSwingUpperBound",
                )
            }
            for route in routes[:50]
        ],
        "outputs": {
            "frontier": str(FRONTIER),
            "testedRoutes": str(TESTED_ROUTES),
        },
    }
    if SEALED_EXACT_AUDIT is not None:
        summary["provenance"]["sealedExactAudit"] = {
            "path": str(SEALED_EXACT_AUDIT),
            "sha256": shared.sha256_file(SEALED_EXACT_AUDIT),
        }
    shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary["summary"], indent=2, sort_keys=True))
    if missing and not args.compute_missing_profiles:
        print(
            f"partial exact frontier: {len(missing)} mapped source profiles "
            "remain; rerun with --compute-missing-profiles when the single "
            "heavy-worker slot is available"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

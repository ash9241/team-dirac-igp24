#!/usr/bin/env python3
"""Prepare a light-only, ranked exact profile-backfill batch.

This program never imports Sage/GAP and never launches a worker.  It ranks
missing complex-conjugation profiles by their current zero-team target upside,
conditional-frontier overlap, single-orbit potential, label-level reuse, and
accepted fresh-anchor availability.  It freezes a coefficient-free batch and
a resumable one-worker coordinator command.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper


ROOT = audit.ROOT
DATA = audit.DATA
DEST = DATA / "gold_profile_backfill_20260722_batch1"
INPUT = DEST / "census_input.jsonl"
EMPTY_PRIOR = DEST / "empty_prior_input.jsonl"
RANKING = DEST / "ranked_profile_plan.json"
PLAN = DEST / "provenance_plan.json"
PREFLIGHT = DEST / "preflight_audit_summary.json"
OUTPUT = DEST / "missing_profile_rows.jsonl"
RESUME_INPUT = DEST / "runtime_resume_input.jsonl"
RESUME_CHUNK = DEST / "runtime_resume_chunk.jsonl"
COORDINATOR = ROOT / "run_gold_profile_backfill_one.py"
WORKER = ROOT / "agent_index24_missing_pair_census.sage.py"
LOCK = DATA / ".low_contention_sequential.lock"
PREPARER = Path(__file__).resolve()

# These defaults preserve the immutable batch-1 behavior.  Later immutable
# batches reuse the same audited planner by overriding only these explicit
# configuration pins before calling ``main``.
BATCH_NUMBER = 1
ALLOWED_FULL_STATUSES = {"certified_zero_immediate_gold_frontier_conditional_only"}
REQUIRE_ZERO_FRONTIER_CERTIFICATE = True
REQUIRE_FULL_VOLATILE_BOUNDARY_MATCH = True
REQUIRE_FULL_LEDGER_BOUNDARY_MATCH = True
PRIORITIZE_CONDITIONAL_FRONTIER = True
EXCLUDED_PROFILE_PLAN: Path | None = None
# Later batches can exclude more than one immutable predecessor.  The singular
# setting remains supported so the already-sealed batch-2 wrapper keeps its
# exact behavior.
EXCLUDED_PROFILE_PLANS: tuple[Path, ...] = ()
REQUIRED_PROFILE_COVERAGE: tuple[Path, ...] = ()
REQUIRED_ACTION_AUDIT_FLAGS: tuple[str, ...] = ()

TARGET_PROFILES = 32
MIN_PROFILES = 20
MAX_PROFILES = 50


def parse_pair(value: str) -> tuple[str, int]:
    label, raw_r = value.rsplit("/r", 1)
    return label, int(raw_r)


def static_group_key(group: dict) -> tuple:
    profiles = int(group["profileCount"])
    if not PRIORITIZE_CONDITIONAL_FRONTIER:
        return (
            0 if group["singleOrbitAction"] else 1,
            -Fraction(group["potentialDistinctGoldPairCount"], profiles),
            -Fraction(group["estimatedRoutesUnlockedUpperBound"], profiles),
            -profiles,
            int(group["sourceOrder"]),
            int(group["sourceLabel"][3:]),
        )
    return (
        0 if group["conditionalFrontierOverlapPairs"] else 1,
        0 if group["singleOrbitAction"] else 1,
        -Fraction(group["conditionalFrontierOverlapPairCount"], profiles),
        -Fraction(group["potentialDistinctGoldPairCount"], profiles),
        -Fraction(group["estimatedRoutesUnlockedUpperBound"], profiles),
        -profiles,
        int(group["sourceOrder"]),
        int(group["sourceLabel"][3:]),
    )


def select_groups(groups: list[dict]) -> list[dict]:
    remaining = list(groups)
    selected = []
    covered_frontier = set()
    profile_count = 0
    while remaining and profile_count < TARGET_PROFILES:
        viable = [
            group
            for group in remaining
            if profile_count + int(group["profileCount"]) <= MAX_PROFILES
        ]
        if not viable:
            break

        def greedy_key(group: dict) -> tuple:
            overlap = set(
                group[
                    "conditionalFrontierOverlapPairs"
                    if PRIORITIZE_CONDITIONAL_FRONTIER
                    else "potentialDistinctGoldPairs"
                ]
            )
            marginal = len(overlap - covered_frontier)
            profiles = int(group["profileCount"])
            return (
                -Fraction(marginal, profiles),
                -marginal,
                *static_group_key(group),
            )

        winner = min(viable, key=greedy_key)
        selected.append(winner)
        remaining.remove(winner)
        profile_count += int(winner["profileCount"])
        covered_frontier.update(
            winner[
                "conditionalFrontierOverlapPairs"
                if PRIORITIZE_CONDITIONAL_FRONTIER
                else "potentialDistinctGoldPairs"
            ]
        )
        if profile_count >= MIN_PROFILES and covered_frontier and all(
            not (
                set(
                    group[
                        "conditionalFrontierOverlapPairs"
                        if PRIORITIZE_CONDITIONAL_FRONTIER
                        else "potentialDistinctGoldPairs"
                    ]
                )
                - covered_frontier
            )
            for group in remaining
        ):
            # Every presently coverable conditional-frontier pair is represented.
            # Continue to the target only when the current batch is still tiny.
            if profile_count >= TARGET_PROFILES:
                break
    return selected


def main() -> int:
    for path in (OUTPUT, RESUME_CHUNK):
        if path.exists():
            raise FileExistsError(f"refusing to replan around existing worker state: {path}")

    full_certificate = audit.read_json(audit.CERTIFICATE)
    zero = full_certificate.get("zeroCertificate") or {}
    if (
        full_certificate.get("status") not in ALLOWED_FULL_STATUSES
        or (
            REQUIRE_ZERO_FRONTIER_CERTIFICATE
            and zero.get("immediateExactOrGuaranteedSafeFrontierExhausted") is not True
        )
        or not all((full_certificate.get("checks") or {}).values())
    ):
        raise ValueError("full-ledger gold certificate is not the required batch boundary")

    (
        actions,
        action_provenance,
        action_artifacts,
        profiles,
        profile_provenance,
        profile_artifacts,
        action_audit,
    ) = audit.load_actions_and_profiles()
    profile_artifact_paths = {str(row["path"]) for row in profile_artifacts}
    required_profile_paths = {
        str(path.resolve().relative_to(ROOT)) for path in REQUIRED_PROFILE_COVERAGE
    }
    if not required_profile_paths <= profile_artifact_paths:
        raise ValueError("required earlier-batch exact profile coverage is absent")

    excluded_plan_paths = []
    if EXCLUDED_PROFILE_PLAN is not None:
        excluded_plan_paths.append(EXCLUDED_PROFILE_PLAN)
    excluded_plan_paths.extend(EXCLUDED_PROFILE_PLANS)
    deduped_excluded_plan_paths = []
    seen_plan_paths = set()
    for path in excluded_plan_paths:
        resolved = path.resolve()
        if resolved in seen_plan_paths:
            continue
        seen_plan_paths.add(resolved)
        deduped_excluded_plan_paths.append(path)

    excluded_signatures: set[str] = set()
    excluded_plan_rows = []
    for earlier_batch_number, path in enumerate(
        deduped_excluded_plan_paths, start=1
    ):
        excluded_plan = audit.read_json(path)
        selected = {
            str(value) for value in excluded_plan.get("selectedSignatures") or []
        }
        if not selected:
            raise ValueError(f"earlier-batch excluded signature set is empty: {path}")
        overlap = selected & excluded_signatures
        if overlap:
            raise ValueError(
                "earlier immutable batch plans overlap: "
                + ", ".join(sorted(overlap))
            )
        excluded_signatures.update(selected)
        excluded_plan_rows.append(
            {
                **audit.artifact(path),
                # The original batch-1 plan predates the explicit batchNumber
                # field; ordered predecessor inputs provide its immutable 1.
                "batchNumber": int(
                    excluded_plan.get("batchNumber", earlier_batch_number)
                ),
                "selectedSignatures": len(selected),
            }
        )

    missing_action_audit_flags = [
        flag for flag in REQUIRED_ACTION_AUDIT_FLAGS if action_audit.get(flag) is not True
    ]
    if missing_action_audit_flags:
        raise ValueError(
            "required exact action/profile coverage flags are false: "
            + ", ".join(missing_action_audit_flags)
        )

    connection = sqlite3.connect(f"file:{audit.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        snapshot = audit.ledger_snapshot(connection)
        corpus = audit.exact_and_lineage_corpus(connection)
        anchor_inventory, anchor_audit = audit.build_anchor_inventory(
            connection, corpus["processedKeys"], corpus["processedHashes"]
        )
        before = audit.volatile_boundary()
        excluded = audit.receipt_and_outbox_exclusions(connection, corpus)
        after = audit.volatile_boundary()
        if audit.compact_boundary(before) != audit.compact_boundary(after):
            raise ValueError("receipt/outbox boundary changed during profile planning")
    finally:
        connection.close()

    boundary = full_certificate["boundary"]
    if (
        (
            REQUIRE_FULL_LEDGER_BOUNDARY_MATCH
            and snapshot["acceptedPairSetSha256"] != boundary["acceptedPairSetSha256"]
        )
        or (
            REQUIRE_FULL_LEDGER_BOUNDARY_MATCH
            and snapshot["targetSnapshotSha256"] != boundary["targetSnapshotSha256"]
        )
        or (
            REQUIRE_FULL_VOLATILE_BOUNDARY_MATCH
            and audit.compact_boundary(after) != boundary["receiptAndOutboxBoundary"]
        )
        or set(anchor_inventory) != snapshot["owned"]
        or anchor_audit["acceptedScoreableAnchorRowsAudited"]
        != snapshot["acceptedAnchorRows"]
    ):
        raise ValueError("full-ledger boundary changed; rerun the gold audit first")

    eligible_gold = (
        snapshot["gold"]
        - excluded["receiptPairs"]
        - excluded["outboxPairs"]
    )
    gold_by_label: dict[str, set[int]] = defaultdict(set)
    for label, r in eligible_gold:
        gold_by_label[label].add(r)
    conditional_frontier = {
        parse_pair(str(row["pair"]))
        for row in full_certificate["classifications"]["conditionalTargetIndex"]
    }

    candidate_profiles = []
    missing_profiles = set()
    no_fresh_anchor = set()
    no_gold_facing_orbit = set()
    for source_pair in sorted(snapshot["owned"], key=audit.pair_key):
        label, source_r = source_pair
        action = actions.get(label)
        if (
            action is None
            or int(action.get("length24OrbitCount", 0)) < 1
            or source_r == 24
            or any(key[0] == source_r for key in profiles.get(label, {}))
        ):
            continue
        missing_profiles.add(source_pair)
        anchor_state = anchor_inventory[source_pair]
        if int(anchor_state["fresh"]) == 0:
            no_fresh_anchor.add(source_pair)
            continue
        orbit_rows = []
        potential_pairs = set()
        for target in action.get("targets") or []:
            target_label = str(target["targetLabel"])
            pairs = {
                (target_label, target_r)
                for target_r in gold_by_label.get(target_label, set())
            }
            if not pairs:
                continue
            potential_pairs.update(pairs)
            orbit_rows.append(
                {
                    "orbitIndex": int(target["orbitIndex"]),
                    "targetLabel": target_label,
                    "eligibleGoldPairs": [
                        audit.pair_text(pair)
                        for pair in sorted(pairs, key=audit.pair_key)
                    ],
                    "eligibleGoldRCount": len(pairs),
                    "evenSignatureCoverageFractionUpper": f"{len(pairs)}/13",
                }
            )
        if not orbit_rows:
            no_gold_facing_orbit.add(source_pair)
            continue
        overlap = potential_pairs & conditional_frontier
        candidate_profiles.append(
            {
                "sourcePair": audit.pair_text(source_pair),
                "sourceLabel": label,
                "sourceR": source_r,
                "sourceOrder": int(action["sourceOrder"]),
                "length24OrbitCount": int(action["length24OrbitCount"]),
                "singleOrbitAction": int(action["length24OrbitCount"]) == 1,
                "freshAnchorCount": int(anchor_state["fresh"]),
                "shortestFreshAnchorBytes": int(
                    anchor_state["bestFresh"]["coefficientBytes"]
                ),
                "potentialGoldFacingOrbits": orbit_rows,
                "estimatedRoutesUnlockedUpperBound": len(orbit_rows),
                "potentialDistinctGoldPairs": [
                    audit.pair_text(pair)
                    for pair in sorted(potential_pairs, key=audit.pair_key)
                ],
                "potentialDistinctGoldPairCount": len(potential_pairs),
                "conditionalFrontierOverlapPairs": [
                    audit.pair_text(pair)
                    for pair in sorted(overlap, key=audit.pair_key)
                ],
                "conditionalFrontierOverlapPairCount": len(overlap),
                "deterministicSingleOrbitConversionUpperBound": (
                    1 if int(action["length24OrbitCount"]) == 1 else 0
                ),
                "allCompatibleSafeConversionUpperBound": len(orbit_rows),
                "estimateQualifier": (
                    "upper bound only until exact compatible conjugacy-class profiles are computed"
                ),
            }
        )

    by_label: dict[str, list[dict]] = defaultdict(list)
    for row in candidate_profiles:
        by_label[row["sourceLabel"]].append(row)
    groups = []
    for label, rows in by_label.items():
        rows.sort(key=lambda row: int(row["sourceR"]))
        action = actions[label]
        potential = {
            pair
            for row in rows
            for pair in row["potentialDistinctGoldPairs"]
        }
        overlap = {
            pair
            for row in rows
            for pair in row["conditionalFrontierOverlapPairs"]
        }
        groups.append(
            {
                "sourceLabel": label,
                "sourceOrder": int(action["sourceOrder"]),
                "sourceR": [int(row["sourceR"]) for row in rows],
                "profileCount": len(rows),
                "singleOrbitAction": int(action["length24OrbitCount"]) == 1,
                "length24OrbitCount": int(action["length24OrbitCount"]),
                "estimatedRoutesUnlockedUpperBound": sum(
                    int(row["estimatedRoutesUnlockedUpperBound"]) for row in rows
                ),
                "potentialDistinctGoldPairs": sorted(potential),
                "potentialDistinctGoldPairCount": len(potential),
                "conditionalFrontierOverlapPairs": sorted(overlap),
                "conditionalFrontierOverlapPairCount": len(overlap),
                "freshAnchorCount": sum(int(row["freshAnchorCount"]) for row in rows),
                "minimumFreshAnchorBytes": min(
                    int(row["shortestFreshAnchorBytes"]) for row in rows
                ),
                "actionArtifact": action_provenance[label],
            }
        )
    groups.sort(key=static_group_key)
    selected_groups = select_groups(groups)
    selected_labels = {row["sourceLabel"] for row in selected_groups}
    selected_r = {
        label: set(next(row for row in selected_groups if row["sourceLabel"] == label)["sourceR"])
        for label in selected_labels
    }
    selected_profiles = [
        row
        for row in candidate_profiles
        if row["sourceLabel"] in selected_labels
        and int(row["sourceR"]) in selected_r[row["sourceLabel"]]
    ]
    selected_profiles.sort(
        key=lambda row: (
            next(
                index
                for index, group in enumerate(selected_groups)
                if group["sourceLabel"] == row["sourceLabel"]
            ),
            int(row["sourceR"]),
        )
    )
    if not MIN_PROFILES <= len(selected_profiles) <= MAX_PROFILES:
        raise ValueError(
            f"selected profile batch outside {MIN_PROFILES}-{MAX_PROFILES}: "
            f"{len(selected_profiles)}"
        )
    selected_signature_text = {row["sourcePair"] for row in selected_profiles}
    if selected_signature_text & excluded_signatures:
        raise ValueError("selected profiles overlap an earlier immutable batch")

    input_labels = set(gold_by_label) | selected_labels
    input_rows = []
    for label in sorted(input_labels, key=lambda value: int(value[3:])):
        source_values = sorted(selected_r.get(label, set()))
        input_rows.append(
            {
                "goldR": sorted(gold_by_label.get(label, set())),
                "isGoldTarget": bool(gold_by_label.get(label)),
                "isOwnedSource": bool(source_values),
                "label": label,
                "sourceR": source_values,
                "t": int(label[3:]),
            }
        )
    if helper.source_pairs(input_rows) != {
        (row["sourceLabel"], int(row["sourceR"])) for row in selected_profiles
    }:
        raise ValueError("backfill input does not isolate selected profiles")

    helper.atomic_text(INPUT, helper.canonical_jsonl(input_rows))
    helper.atomic_text(EMPTY_PRIOR, "")
    worker_argv = [
        "/usr/local/bin/sage",
        "-python",
        str(WORKER.relative_to(ROOT)),
        "--input",
        str(INPUT.relative_to(ROOT)),
    ]
    for item in action_artifacts:
        worker_argv.extend(["--prior-map", str(item["path"])])
    worker_argv.extend(
        [
            "--signature-aware",
            "--prior-input",
            str(EMPTY_PRIOR.relative_to(ROOT)),
            "--output",
            str(OUTPUT.relative_to(ROOT)),
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    coordinator_argv = [
        "/usr/bin/caffeinate",
        "-i",
        "python3",
        str(COORDINATOR.relative_to(ROOT)),
        "--execute",
        "--plan",
        str(PLAN.relative_to(ROOT)),
    ]

    selected_overlap = {
        pair
        for group in selected_groups
        for pair in group["conditionalFrontierOverlapPairs"]
    }
    selected_potential = {
        pair
        for group in selected_groups
        for pair in group["potentialDistinctGoldPairs"]
    }
    ranking = {
        "schemaVersion": "gold-profile-backfill-ranking-v1",
        "batchNumber": BATCH_NUMBER,
        "status": "ranked_light_only_batch_selected",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundaryCertificate": audit.artifact(audit.CERTIFICATE),
        "candidateCensus": {
            "missingExactProfilePairs": len(missing_profiles),
            "missingProfilesAllAnchorsProcessed": len(no_fresh_anchor),
            "missingProfilesWithoutCurrentGoldFacingOrbit": len(no_gold_facing_orbit),
            "rankableProfilePairs": len(candidate_profiles),
            "rankableSourceLabels": len(groups),
            "earlierBatchSignaturesExplicitlyExcluded": len(excluded_signatures),
            "earlierImmutableBatchPlans": len(excluded_plan_rows),
            "requiredEarlierProfileArtifactsLoaded": len(required_profile_paths),
        },
        "selection": {
            "targetProfiles": TARGET_PROFILES,
            "minimumProfiles": MIN_PROFILES,
            "maximumProfiles": MAX_PROFILES,
            "selectedProfiles": len(selected_profiles),
            "selectedSourceLabels": len(selected_groups),
            "estimatedRoutesUnlockedUpperBound": sum(
                int(row["estimatedRoutesUnlockedUpperBound"])
                for row in selected_profiles
            ),
            "potentialDistinctGoldPairs": len(selected_potential),
            "conditionalFrontierOverlapPairs": len(selected_overlap),
            "deterministicSingleOrbitConversionUpperBound": sum(
                int(row["deterministicSingleOrbitConversionUpperBound"])
                for row in selected_profiles
            ),
            "allCompatibleSafeConversionUpperBound": sum(
                int(row["allCompatibleSafeConversionUpperBound"])
                for row in selected_profiles
            ),
            "estimateQualifier": (
                "upper bounds; exact deterministic/all-compatible status is known only after the profile worker"
            ),
        },
        "selectedProfileRows": selected_profiles,
        "selectedLabelGroups": [
            {**row, "selectionRank": index}
            for index, row in enumerate(selected_groups, start=1)
        ],
        "allRankedLabelGroups": [
            {
                **row,
                "staticRank": index,
                "selected": row["sourceLabel"] in selected_labels,
            }
            for index, row in enumerate(groups, start=1)
        ],
        "rankingOrder": (
            [
                "new conditional-frontier pairs covered per profile",
                "single-orbit action before multi-orbit action",
                "conditional-frontier overlap per profile",
                "potential distinct current gold pairs per profile",
                "gold-facing action orbits per profile",
                "more profiles amortized by one source-label computation",
                "smaller exact source group order and label",
            ]
            if PRIORITIZE_CONDITIONAL_FRONTIER
            else [
                "new distinct current tc0 gold pairs covered per profile",
                "single-orbit action before multi-orbit action",
                "potential distinct current gold pairs per profile",
                "gold-facing action orbits per profile",
                "more profiles amortized by one source-label computation",
                "smaller exact source group order and label",
            ]
        ),
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    helper.atomic_json(RANKING, ranking)

    plan = {
        "schemaVersion": "gold-profile-backfill-one-worker-plan-v1",
        "batchNumber": BATCH_NUMBER,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_waiting_for_root_heavy_clearance",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedScoreableAnchorRows": snapshot["acceptedAnchorRows"],
            "acceptedPairSetSha256": snapshot["acceptedPairSetSha256"],
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": snapshot["targetSnapshotSha256"],
            "receiptAndOutboxBoundary": audit.compact_boundary(after),
        },
        "selection": ranking["selection"],
        "selectedSignatures": [row["sourcePair"] for row in selected_profiles],
        "excludedEarlierBatchSignatures": sorted(excluded_signatures),
        "artifacts": {
            "fullGoldAudit": audit.artifact(audit.CERTIFICATE),
            "ranking": audit.artifact(RANKING),
            "input": audit.artifact(INPUT),
            "emptyPriorInput": audit.artifact(EMPTY_PRIOR),
            "worker": audit.artifact(WORKER),
            "coordinator": audit.artifact(COORDINATOR),
            "actionMaps": action_artifacts,
            "profileArtifactsAtSeal": profile_artifacts,
            "excludedEarlierBatchPlans": excluded_plan_rows,
            "output": str(OUTPUT.relative_to(ROOT)),
            "resumeInput": str(RESUME_INPUT.relative_to(ROOT)),
            "resumeChunk": str(RESUME_CHUNK.relative_to(ROOT)),
            "lock": str(LOCK.relative_to(ROOT)),
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "heavyWorkerArgv": worker_argv,
            "resumableCoordinatorArgv": coordinator_argv,
            "resumableCoordinatorCommand": shlex.join(coordinator_argv),
            "oneWorkerAtATimeLockRequired": True,
            "checkpointEvery": 1,
            "outputAbsentAtSeal": not OUTPUT.exists(),
            "resumeChunkAbsentAtSeal": not RESUME_CHUNK.exists(),
            "requiresRootHeavyClearance": True,
            "submissionAuthorized": False,
        },
        "sideEffectsAtPreparation": {
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    helper.atomic_json(PLAN, plan)

    preflight = {
        "schemaVersion": "gold-profile-backfill-light-preflight-v1",
        "batchNumber": BATCH_NUMBER,
        "status": "certified_resumable_one_worker_plan_ready",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "checks": {
            "fullGoldBoundaryPinned": True,
            "acceptedAndTargetBoundaryUnchanged": True,
            "receiptAndOutboxBoundaryUnchanged": True,
            "allAcceptedAnchorsAudited": True,
            "allSelectedProfilesMissingFromExactCache": True,
            "earlierBatchSignaturesDisjoint": not (
                selected_signature_text & excluded_signatures
            ),
            "requiredEarlierProfileCoverageLoaded": required_profile_paths
            <= profile_artifact_paths,
            "requiredExactActionProfileAuditFlagsTrue": not missing_action_audit_flags,
            "allSelectedProfilesHaveFreshAcceptedAnchor": True,
            "allSelectedProfilesHaveCurrentUnexcludedGoldFacingOrbit": True,
            "selectedProfilesBetweenTwentyAndFifty": MIN_PROFILES
            <= len(selected_profiles)
            <= MAX_PROFILES,
            "oneInputRowPerSelectedSourceLabel": len(selected_groups)
            == sum(bool(row["isOwnedSource"]) for row in input_rows),
            "priorMapsIncludeCompletedV20": action_audit["v20OutputComplete"],
            "workerOutputAndResumeChunkAbsent": not OUTPUT.exists()
            and not RESUME_CHUNK.exists(),
            "coefficientAndCredentialScanClean": True,
        },
        "artifacts": {
            "ranking": audit.artifact(RANKING),
            "plan": audit.artifact(PLAN),
            "input": audit.artifact(INPUT),
            "emptyPriorInput": audit.artifact(EMPTY_PRIOR),
            "preparer": audit.artifact(PREPARER),
        },
        "workerPreflight": {
            "selectedProfiles": len(selected_profiles),
            "selectedSourceLabels": len(selected_groups),
            "shardCount": 1,
            "checkpointEvery": 1,
            "signatureAware": True,
            "resumable": True,
            "sharedHeavyLock": str(LOCK.relative_to(ROOT)),
            "heavyWorkerLaunched": False,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    if not all(preflight["checks"].values()):
        raise ValueError("profile backfill preflight is incomplete")
    helper.atomic_json(PREFLIGHT, preflight)
    print(
        json.dumps(
            {
                "status": plan["status"],
                **ranking["selection"],
                "rankableProfiles": len(candidate_profiles),
                "rankableLabels": len(groups),
                "plan": str(PLAN.relative_to(ROOT)),
                "preflight": str(PREFLIGHT.relative_to(ROOT)),
                "heavyWorkerLaunched": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

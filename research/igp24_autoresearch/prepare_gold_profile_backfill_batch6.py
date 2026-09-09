#!/usr/bin/env python3
"""Prepare max-throughput immutable batch 6 on exact batch1-5/v21 coverage."""

from pathlib import Path

import prepare_gold_profile_backfill as planner


ROOT = planner.ROOT
DEST = planner.DATA / "gold_profile_backfill_20260722_batch6"

EXPECTED_FULL_AUDIT_SHA256 = (
    "b9b9005cfe1489e00cedfaa30a3019417eeedf2dd33485dc037474a5f97bfff7"
)
EXPECTED_BOUNDARY = {
    "acceptedScoreablePairs": 22_062,
    "acceptedScoreableAnchorRows": 927_813,
    "acceptedPairSetSha256": (
        "3cbfb63b7b1e18be2873be0b9a3be937d4b6bf36727705261dd1a8e487624dbf"
    ),
    "targetRows": 165_836,
    "targetSnapshotSha256": (
        "7ee2fed7ee72aebfe0f0f17c8ea3d2a3833f48b244597e353d55eba0ea1717f6"
    ),
    "receiptAndOutboxBoundary": {
        "receipts": {
            "files": 136,
            "indexSha256": (
                "ea3981cb3f2cd6204eced146beb9ec20278cce43aca9bb545d1c3f289747440e"
            ),
        },
        "outboxes": {
            "files": 402,
            "indexSha256": (
                "4117ff79d7d3551e88cf47c573f72fe364be7cd02c62ba40fae102f38ade1697"
            ),
        },
    },
}


def validate_frozen_boundary() -> None:
    certificate = planner.audit.read_json(planner.audit.CERTIFICATE)
    boundary = certificate.get("boundary") or {}
    if planner.helper.sha256_path(planner.audit.CERTIFICATE) != EXPECTED_FULL_AUDIT_SHA256:
        raise ValueError("batch6 full-ledger audit certificate changed")
    if any(boundary.get(key) != value for key, value in EXPECTED_BOUNDARY.items()):
        raise ValueError("batch6 exact accepted/target/receipt/outbox boundary changed")


def configure() -> None:
    planner.DEST = DEST
    planner.INPUT = DEST / "census_input.jsonl"
    planner.EMPTY_PRIOR = DEST / "empty_prior_input.jsonl"
    planner.RANKING = DEST / "ranked_profile_plan.json"
    planner.PLAN = DEST / "provenance_plan.json"
    planner.PREFLIGHT = DEST / "preflight_audit_summary.json"
    planner.OUTPUT = DEST / "missing_profile_rows.jsonl"
    planner.RESUME_INPUT = DEST / "runtime_resume_input.jsonl"
    planner.RESUME_CHUNK = DEST / "runtime_resume_chunk.jsonl"
    planner.PREPARER = Path(__file__).resolve()
    planner.BATCH_NUMBER = 6
    planner.TARGET_PROFILES = 50
    planner.ALLOWED_FULL_STATUSES = {
        "certified_zero_immediate_gold_frontier_conditional_only"
    }
    planner.REQUIRE_ZERO_FRONTIER_CERTIFICATE = True
    planner.REQUIRE_FULL_VOLATILE_BOUNDARY_MATCH = True
    planner.REQUIRE_FULL_LEDGER_BOUNDARY_MATCH = True
    planner.PRIORITIZE_CONDITIONAL_FRONTIER = False
    planner.EXCLUDED_PROFILE_PLAN = None
    planner.EXCLUDED_PROFILE_PLANS = tuple(
        planner.DATA / f"gold_profile_backfill_20260722_batch{number}/provenance_plan.json"
        for number in (1, 2, 3, 4, 5)
    )
    planner.REQUIRED_PROFILE_COVERAGE = tuple(
        planner.DATA
        / f"gold_profile_backfill_20260722_batch{number}/missing_profile_rows.jsonl"
        for number in (1, 2, 3, 4, 5)
    ) + (planner.DATA / "autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl",)
    planner.REQUIRED_ACTION_AUDIT_FLAGS = tuple(
        f"profileBackfillBatch{number}OutputComplete"
        for number in (1, 2, 3, 4, 5)
    ) + ("v21OutputComplete",)


def main() -> int:
    configure()
    validate_frozen_boundary()
    return planner.main()


if __name__ == "__main__":
    raise SystemExit(main())

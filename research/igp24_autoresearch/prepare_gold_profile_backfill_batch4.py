#!/usr/bin/env python3
"""Prepare immutable profile-backfill batch 4 on exact batch1/2/3/v21 coverage."""

from pathlib import Path

import prepare_gold_profile_backfill as planner


ROOT = planner.ROOT
DEST = planner.DATA / "gold_profile_backfill_20260722_batch4"

EXPECTED_FULL_AUDIT_SHA256 = (
    "d9687f740d1035cd5d7d66c13f72ae3b0e4bc39ea056edd499617aca74bf4663"
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
        raise ValueError("batch4 full-ledger audit certificate changed")
    if any(boundary.get(key) != value for key, value in EXPECTED_BOUNDARY.items()):
        raise ValueError("batch4 exact accepted/target/receipt/outbox boundary changed")


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
    planner.BATCH_NUMBER = 4
    planner.ALLOWED_FULL_STATUSES = {
        "certified_zero_immediate_gold_frontier_conditional_only"
    }
    planner.REQUIRE_ZERO_FRONTIER_CERTIFICATE = True
    planner.REQUIRE_FULL_VOLATILE_BOUNDARY_MATCH = True
    planner.REQUIRE_FULL_LEDGER_BOUNDARY_MATCH = True
    planner.PRIORITIZE_CONDITIONAL_FRONTIER = False
    planner.EXCLUDED_PROFILE_PLAN = None
    planner.EXCLUDED_PROFILE_PLANS = (
        planner.DATA / "gold_profile_backfill_20260722_batch1/provenance_plan.json",
        planner.DATA / "gold_profile_backfill_20260722_batch2/provenance_plan.json",
        planner.DATA / "gold_profile_backfill_20260722_batch3/provenance_plan.json",
    )
    planner.REQUIRED_PROFILE_COVERAGE = (
        planner.DATA
        / "gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl",
        planner.DATA
        / "gold_profile_backfill_20260722_batch2/missing_profile_rows.jsonl",
        planner.DATA
        / "gold_profile_backfill_20260722_batch3/missing_profile_rows.jsonl",
        planner.DATA / "autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl",
    )
    planner.REQUIRED_ACTION_AUDIT_FLAGS = (
        "profileBackfillBatch1OutputComplete",
        "profileBackfillBatch2OutputComplete",
        "profileBackfillBatch3OutputComplete",
        "v21OutputComplete",
    )


def main() -> int:
    configure()
    validate_frozen_boundary()
    return planner.main()


if __name__ == "__main__":
    raise SystemExit(main())

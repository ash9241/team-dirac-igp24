#!/usr/bin/env python3
"""Prepare immutable profile-backfill batch 3 on exact batch1/2/v21 coverage."""

from pathlib import Path

import prepare_gold_profile_backfill as planner


ROOT = planner.ROOT
DEST = planner.DATA / "gold_profile_backfill_20260722_batch3"


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
    planner.BATCH_NUMBER = 3
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
    )
    planner.REQUIRED_PROFILE_COVERAGE = (
        planner.DATA
        / "gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl",
        planner.DATA
        / "gold_profile_backfill_20260722_batch2/missing_profile_rows.jsonl",
        planner.DATA / "autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl",
    )
    planner.REQUIRED_ACTION_AUDIT_FLAGS = (
        "profileBackfillBatch1OutputComplete",
        "profileBackfillBatch2OutputComplete",
        "v21OutputComplete",
    )


def main() -> int:
    configure()
    return planner.main()


if __name__ == "__main__":
    raise SystemExit(main())

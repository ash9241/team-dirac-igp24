#!/usr/bin/env python3
"""Audit deterministic pair routes for one exact current team-count layer."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_higher_tc_routes as higher
import audit_low_contention_pair_routes as base
import run_low_contention_sequential as lane


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-count", type=int, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.team_count < 3:
        raise ValueError("team count must be at least three")
    certificate_path = lane.DATA / f"{args.tag}.json"
    summary_path = lane.DATA / f"{args.tag}_summary.json"
    if certificate_path.exists() or summary_path.exists():
        raise FileExistsError("refusing to overwrite exact-team-count audit")
    higher.CERTIFICATE = certificate_path

    actions, action_provenance, action_artifacts = base.load_sealed_actions()
    profiles, profile_provenance, profile_artifacts = base.load_profiles(
        actions, action_artifacts
    )
    connection = sqlite3.connect(f"file:{lane.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = base.load_ledger_snapshot(connection)
        candidate_snapshot = base.exact_candidate_and_receipt_snapshot(connection)
        census = higher.census_for_count(
            args.team_count, snapshot, actions, action_provenance, profiles,
            profile_provenance, candidate_snapshot, connection,
        )
    finally:
        connection.close()
    runbooks = higher.make_runbooks(census, snapshot)
    marginal = Fraction(len(runbooks), 2 ** args.team_count)
    payload = {
        "artifacts": {
            "sealedActionMaps": action_artifacts,
            "profileArtifacts": profile_artifacts,
        },
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "networkCalls": 0,
        "runbooks": runbooks,
        "schemaVersion": "current-exact-team-count-route-audit-v1",
        "selectedTeamCount": args.team_count,
        "status": "certified_runbooks_ready" if runbooks else "certified_no_routes",
        "submissionCalls": 0,
        "summary": {
            "deterministicRoutes": len(census["deterministicRoutes"]),
            "executableRoutes": len(census["executableRoutes"]),
            "marginalScoreExact": base.fraction_text(marginal),
            "runbooks": len(runbooks),
            "selectedRoutes": len(census["selectedRoutes"]),
            "teamCount": args.team_count,
            "testedSourceHashes": len(census["lineage"]["testedSourceHashes"]),
            "testedSourceKeys": len(census["lineage"]["testedSourceKeys"]),
        },
    }
    certificate_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(json.dumps(payload["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"certificate": str(certificate_path), **payload["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

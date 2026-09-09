#!/usr/bin/env python3
"""Coefficient-free postflight for one freshly re-audited opponent raid route."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as exclusions
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--team-id", default=raid.TEAM_ID)
    parser.add_argument("--team-number", default=raid.TEAM_NUMBER)
    parser.add_argument("--team-name", default=raid.TEAM_NAME)
    args = parser.parse_args()
    args.opponent = raid.validate_opponent(
        args.team_id, args.team_number, args.team_name
    )
    return args


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected one object: {path}")
    return value


def one_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("worker result must contain exactly one JSON object")
    return rows[0]


def parse_pair(value: str) -> tuple[str, int]:
    match = exact_single.PAIR_TEXT_RE.fullmatch(str(value))
    if match is None:
        raise ValueError(f"invalid pair: {value!r}")
    return match.group(1), int(match.group(2))


def main() -> int:
    args = arguments()
    opponent = args.opponent
    expected_opponent = opponent.public()
    certificate_path = args.certificate.expanduser().resolve()
    result_path = args.result.expanduser().resolve()
    if (
        not certificate_path.is_relative_to(DATA)
        or not result_path.is_relative_to(DATA)
    ):
        raise ValueError("certificate/result escaped the project data directory")
    certificate = read_json(certificate_path)
    freshness = certificate.get("cacheFreshness") or {}
    if (
        certificate.get("schemaVersion")
        != "rank11-low-hanging-fruit-raid-audit-v1"
        or certificate.get("coefficientMaterialIncluded") is not False
        or certificate.get("status")
        != "fresh_audit_ready_for_explicit_worker_selection"
        or freshness.get("freshAtAudit") is not True
        or freshness.get("refreshRequiredBeforeExecution") is not False
        or certificate.get("opponent") != expected_opponent
        or freshness.get("teamId") != opponent.team_id
        or freshness.get("teamNumber") != opponent.team_number
        or freshness.get("teamName") != opponent.team_name
    ):
        raise ValueError(
            "route certificate is stale, unsealed, or for a different opponent"
        )

    snapshot_artifact = freshness.get("placementSnapshot") or {}
    snapshot_path = (ROOT / str(snapshot_artifact.get("path") or "")).resolve()
    if (
        not snapshot_path.is_relative_to(DATA)
        or not snapshot_path.is_file()
        or lane.sha256_path(snapshot_path) != str(snapshot_artifact.get("sha256"))
    ):
        raise ValueError("certificate does not pin an intact opponent placement snapshot")

    plan_artifact = ((certificate.get("artifacts") or {}).get("plan") or {})
    plan_path = (ROOT / str(plan_artifact.get("path"))).resolve()
    if (
        not plan_path.is_relative_to(DATA)
        or not plan_path.is_file()
        or lane.sha256_path(plan_path) != str(plan_artifact.get("sha256"))
    ):
        raise ValueError("certificate does not pin an intact raid plan")
    plan = read_json(plan_path)
    if (
        plan.get("status") != "fresh_audit_ready_for_explicit_worker_selection"
        or plan.get("publicPlacementRefreshRequiredBeforeExecution") is not False
        or plan.get("coefficientMaterialIncluded") is not False
        or plan.get("opponent") != expected_opponent
        or plan.get("publicPlacementSnapshot") != snapshot_artifact
    ):
        raise ValueError(
            "raid plan is not fresh, coefficient-free, and opponent-pinned"
        )
    matches = [
        row for row in plan.get("guardedOneWorkerRunbooks") or []
        if str(row.get("routeId")) == args.route_id
    ]
    if len(matches) != 1:
        raise ValueError("route id is absent or nonunique")
    runbook = matches[0]
    if (ROOT / str(runbook["output"])).resolve() != result_path:
        raise ValueError("result path differs from the sealed plan")
    target = runbook.get("target") or {}
    if (
        target.get("soleHolderTeamIdAtCrawl") != opponent.team_id
        or target.get("soleHolderTeamNumberAtCrawl") != opponent.team_number
        or target.get("soleHolderTeamAtCrawl") != opponent.team_name
    ):
        raise ValueError("sealed route target has a different opponent identity")

    source = runbook["source"]
    target_pair = parse_pair(target["pair"])
    row = one_jsonl(result_path)
    if (
        row.get("status") != "certified"
        or int(row.get("workerExitCode", -1)) != 0
        or str(row.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(row.get("sourcePolynomialIndex", -1))
        != int(source["polynomialIndex"])
        or str(row.get("sourceCoefficientSha256"))
        != str(source["coefficientSha256"])
        or str(row.get("sourceLabel")) != str(source["label"])
        or int(row.get("sourceR", -1)) != int(source["r"])
        or str(row.get("targetLabel")) != target_pair[0]
        or int(row.get("targetR", -1)) != target_pair[1]
    ):
        raise ValueError("worker result differs from the sealed exact route")
    line = exact_single.canonical_polynomial_line(row.get("coefficientLine"))
    if line is None:
        raise ValueError("worker output is not canonical primitive monic degree 24")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(row.get("coefficientSha256")):
        raise ValueError("worker output hash mismatch")
    orbit = row.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected = [int(value) for value in orbit.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit.get("exponents") or []]
    if (
        not actual
        or actual != expected
        or actual.count(24) != 1
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
    ):
        raise ValueError("worker orbit certificate is not exact and squarefree")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        latest_crawl, placements = raid.latest_public_placements(
            connection, datetime.now(timezone.utc), opponent
        )
        latest_snapshot = raid.validate_public_placement_snapshot(
            latest_crawl, placements, opponent
        )
        if not latest_crawl["freshAtAudit"]:
            raise ValueError("latest public placement crawl is stale")
        if int(latest_crawl["crawl_id"]) != int(freshness["crawlId"]):
            raise ValueError("public placement crawl changed; rerun the raid audit")
        if latest_snapshot != snapshot_artifact:
            raise ValueError("opponent placement snapshot changed; rerun the raid audit")
        placement = placements.get(target_pair)
        if placement is None or int(placement["k_teams"]) != 1:
            raise ValueError("target is no longer solely held by the opponent")

        source_row = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
            "FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? AND p.polynomial_index=?",
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        if (
            source_row is None
            or str(source_row["coefficient_hash"])
            != str(source["coefficientSha256"])
            or str(source_row["status"]) != "accepted"
            or int(source_row["scoreable"] or 0) != 1
            or str(source_row["label"]) != str(source["label"])
            or int(source_row["r"]) != int(source["r"])
        ):
            raise ValueError("source ledger anchor changed")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (digest,),
        ).fetchone() is not None:
            raise ValueError("worker output hash is already in the ledger")
        if connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", target_pair
        ).fetchone() is not None:
            raise ValueError("target pair is baseline")
        if connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1",
            target_pair,
        ).fetchone() is not None:
            raise ValueError("target pair is already known locally")

        exact_pool, _single, _meta, pair_index = raid.exact_corpus(connection)
        receipt_hashes, receipt_pairs, receipt_audit = (
            exact_single.receipt_exclusions(
                lane.RECEIPTS, DATA, connection, pair_index
            )
        )
        outbox_hashes, outbox_pairs, outbox_audit = exclusions.outbox_exclusions(
            pair_index, connection
        )
        if digest in receipt_hashes or digest in outbox_hashes:
            raise ValueError("worker output hash is already receipted or outboxed")
        if target_pair in receipt_pairs or target_pair in outbox_pairs:
            raise ValueError("target pair is already receipted or outboxed")
    finally:
        connection.close()

    postflight_path = result_path.with_name(result_path.stem + "_postflight.json")
    postflight = {
        "schemaVersion": "rank11-raid-pair-route-postflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_current_opponent_pair_not_staged",
        "routeCertificate": lane.artifact(certificate_path),
        "raidPlan": lane.artifact(plan_path),
        "routeId": args.route_id,
        "result": lane.artifact(result_path),
        "source": {
            "submissionId": source["submissionId"],
            "polynomialIndex": source["polynomialIndex"],
            "label": source["label"],
            "r": source["r"],
            "coefficientSha256": source["coefficientSha256"],
        },
        "target": {
            "pair": raid.public_pair(target_pair),
            "soleHolderTeam": opponent.team_name,
            "soleHolderTeamId": opponent.team_id,
            "soleHolderTeamNumber": opponent.team_number,
            "publicCrawlId": int(latest_crawl["crawl_id"]),
            "leaderboardGeneratedAt": latest_crawl["leaderboard_generated_at"],
            "scoringDiscAbs": placement.get("scoring_disc_abs"),
        },
        "candidate": {
            "coefficientSha256": digest,
            "primitiveMonicDegree24": True,
            "irreducible": True,
            "exactSquarefreeOrbitCertificate": True,
            "knownLedgerHash": False,
            "receiptHash": False,
            "outboxHash": False,
        },
        "exclusionAudit": {
            "exactCorpusHashes": len(exact_pool),
            "receiptCount": receipt_audit["receiptCount"],
            "receiptHashes": len(receipt_hashes),
            "outboxFiles": outbox_audit["outboxFiles"],
            "outboxHashes": len(outbox_hashes),
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0
        },
    }
    lane.exclusive_json(postflight_path, postflight)
    print(json.dumps({
        "status": postflight["status"],
        "routeId": args.route_id,
        "targetPair": raid.public_pair(target_pair),
        "candidateSha256": digest,
        "postflight": str(postflight_path.relative_to(ROOT)),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileExistsError, ValueError, OSError, sqlite3.Error,
        json.JSONDecodeError, lane.GuardFailure,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

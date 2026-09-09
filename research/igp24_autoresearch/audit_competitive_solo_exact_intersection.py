#!/usr/bin/env python3
"""Intersect one fresh sole-holder snapshot with the sealed exact corpus.

This audit is offline after the placement crawl.  It validates the latest
complete unique-placement snapshot for a pinned opponent, rejoins the complete
local exact corpus to accepted source rows, and applies ledger, baseline,
receipt, and text-outbox exclusions.  It writes coefficient-free artifacts
only and has no submission path.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
MAX_CACHE_AGE_SECONDS = 6 * 60 * 60

DEFAULT_TEAM_ID = "teamv2_32d618912a0e471fb418e886de946622"
DEFAULT_TEAM_NUMBER = "IGP24-T00134"
DEFAULT_TEAM_NAME = ""
DEFAULT_CERTIFICATE = (
    DATA / "rank10_t00134_20260730_exact_intersection_certificate.json"
)
DEFAULT_PLAN = DATA / "rank10_t00134_20260730_exact_intersection_plan.json"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", default=DEFAULT_TEAM_ID)
    parser.add_argument("--team-number", default=DEFAULT_TEAM_NUMBER)
    parser.add_argument("--team-name", default=DEFAULT_TEAM_NAME)
    parser.add_argument("--certificate", type=Path, default=DEFAULT_CERTIFICATE)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    return parser.parse_args()


def timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def latest_snapshot(
    connection: sqlite3.Connection,
    *,
    team_id: str,
    team_number: str,
    team_name: str,
    now: datetime,
) -> tuple[dict, dict[tuple[str, int], dict]]:
    row = connection.execute(
        """
        SELECT * FROM public_team_placement_crawls
        WHERE complete=1 AND scope='unique' AND team_id=?
          AND team_number=? AND team_name=?
        ORDER BY crawl_id DESC LIMIT 1
        """,
        (team_id, team_number, team_name),
    ).fetchone()
    if row is None:
        raise ValueError("no complete unique-placement crawl for pinned opponent")
    crawl = dict(row)
    placements = {
        (str(value["label"]), int(value["r"])): dict(value)
        for value in connection.execute(
            "SELECT * FROM public_team_placements "
            "WHERE crawl_id=? AND k_teams=1",
            (int(crawl["crawl_id"]),),
        )
    }
    if (
        int(crawl["complete"]) != 1
        or int(crawl["placement_rows"]) != int(crawl["unique_rows"])
        or int(crawl["unique_rows"]) != len(placements)
        or any(
            int(value["k_teams"]) != 1
            or abs(float(value["points"]) - 1.0) > 1e-12
            for value in placements.values()
        )
    ):
        raise ValueError("unique-placement crawl is incomplete or inconsistent")
    leaderboard_generated_at = crawl.get("leaderboard_generated_at")
    freshness_value = (
        leaderboard_generated_at
        if leaderboard_generated_at not in (None, "", "unavailable")
        else crawl.get("completed_at")
    )
    generated = timestamp(freshness_value)
    age = (now - generated).total_seconds()
    if age < -300:
        raise ValueError("placement snapshot is unexpectedly in the future")
    crawl["freshnessTimestamp"] = str(freshness_value)
    crawl["ageSecondsAtAudit"] = max(0, int(age))
    crawl["freshAtAudit"] = 0 <= age <= MAX_CACHE_AGE_SECONDS
    return crawl, placements


def candidate_rank(row: dict) -> tuple:
    projection = row["projection"]["projectedNetRelativeSwing"]
    field = row["candidateFieldDiscriminantAbs"]
    return (
        -(float(projection) if projection is not None else -1.0),
        field is None,
        int(field) if field is not None else 0,
        int(row["coefficientBytes"]),
        str(row["coefficientSha256"]),
    )


def main() -> int:
    args = arguments()
    certificate_path = args.certificate.expanduser().resolve()
    plan_path = args.plan.expanduser().resolve()
    if certificate_path == plan_path:
        raise ValueError("certificate and plan outputs must be distinct")
    if any(path.exists() for path in (certificate_path, plan_path)):
        raise FileExistsError("refusing to overwrite exact-intersection artifact")

    now = datetime.now(timezone.utc)
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        crawl, placements = latest_snapshot(
            connection,
            team_id=str(args.team_id),
            team_number=str(args.team_number),
            team_name=str(args.team_name),
            now=now,
        )
        exact_pool, _single_rows, exact_meta, pair_index = raid.exact_corpus(
            connection
        )
        receipt_hashes, receipt_pairs, receipt_meta = (
            exact_single.receipt_exclusions(
                lane.RECEIPTS, DATA, connection, pair_index
            )
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(pair_index, connection)
        )
        known_hashes = exact_single.query_known_hashes(
            connection, set(exact_pool)
        )
        snapshot = pair_routes.load_ledger_snapshot(connection)

        eligible = set(placements) - (
            snapshot["baseline"]
            | snapshot["owned"]
            | snapshot["knownPairs"]
            | receipt_pairs
            | outbox_pairs
        )
        excluded_hashes = known_hashes | receipt_hashes | outbox_hashes
        hits = []
        for digest, candidate in exact_pool.items():
            pair = tuple(candidate["pair"])
            if pair not in eligible or digest in excluded_hashes:
                continue
            holder_disc = raid.holder_discriminant(placements[pair])
            candidate_disc = candidate["fieldDiscriminantAbs"]
            hits.append(
                {
                    "coefficientSha256": str(digest),
                    "coefficientBytes": int(candidate["coefficientBytes"]),
                    "targetPair": raid.public_pair(pair),
                    "candidateFieldDiscriminantAbs": (
                        str(candidate_disc)
                        if candidate_disc is not None else None
                    ),
                    "holderScoringDiscAbsAtCrawl": (
                        str(holder_disc) if holder_disc is not None else None
                    ),
                    "families": sorted(candidate["families"]),
                    "proofArtifacts": raid.proof_artifacts(
                        candidate["proofs"]
                    ),
                    "sourceKeys": [
                        {
                            "submissionId": key[0],
                            "polynomialIndex": key[1],
                            "label": key[2],
                            "r": key[3],
                        }
                        for key in sorted(candidate["sourceKeys"])
                    ],
                    "projection": raid.head_to_head_projection(
                        candidate_disc, holder_disc
                    ),
                    "status": "exact_unowned_unreceipted_unoutboxed",
                }
            )
        hits.sort(key=candidate_rank)
        best_by_pair = {}
        for hit in hits:
            best_by_pair.setdefault(str(hit["targetPair"]), hit)
        selected = sorted(best_by_pair.values(), key=candidate_rank)
    finally:
        connection.close()

    known_swing = sum(
        float(value["projection"]["projectedNetRelativeSwing"])
        for value in selected
        if value["projection"]["projectedNetRelativeSwing"] is not None
    )
    unknown_swing_rows = sum(
        value["projection"]["projectedNetRelativeSwing"] is None
        for value in selected
    )
    plan = {
        "schemaVersion": "competitive-solo-exact-intersection-plan-v1",
        "createdAt": now.isoformat(),
        "status": (
            "fresh_exact_intersection_ready"
            if crawl["freshAtAudit"]
            else "stale_snapshot_refresh_required"
        ),
        "team": {
            "teamId": str(args.team_id),
            "teamNumber": str(args.team_number),
            "teamName": str(args.team_name),
        },
        "publicCrawlId": int(crawl["crawl_id"]),
        "selectedBestExactCandidatePerPair": selected,
        "allSurvivingExactCandidates": hits,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
    }
    lane.exclusive_json(plan_path, plan)

    placement_rows = [
        [
            pair[0],
            pair[1],
            float(value["points"]),
            int(value["k_teams"]),
            value.get("scoring_disc_abs"),
            value.get("minimum_disc_abs"),
        ]
        for pair, value in sorted(
            placements.items(),
            key=lambda item: pair_routes.pair_sort_key(item[0]),
        )
    ]
    certificate = {
        "schemaVersion": "competitive-solo-exact-intersection-audit-v1",
        "createdAt": now.isoformat(),
        "status": plan["status"],
        "team": plan["team"],
        "cache": {
            "crawlId": int(crawl["crawl_id"]),
            "leaderboardGeneratedAt": crawl.get("leaderboard_generated_at"),
            "completedAt": crawl.get("completed_at"),
            "freshnessTimestamp": crawl["freshnessTimestamp"],
            "ageSecondsAtAudit": int(crawl["ageSecondsAtAudit"]),
            "freshAtAudit": bool(crawl["freshAtAudit"]),
            "pages": int(crawl["pages"]),
            "uniqueRows": len(placements),
            "pairSetSha256": pair_routes.canonical_digest(placement_rows),
        },
        "boundary": {
            "opponentSolePairs": len(placements),
            "eligibleOpponentPairsAfterAllExclusions": len(eligible),
            "locallyOwnedScoreablePairs": len(snapshot["owned"]),
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "baselinePairs": len(snapshot["baseline"]),
            "knownExactHashesExcluded": len(known_hashes),
            "receiptHashesExcluded": len(receipt_hashes),
            "receiptPairsExcluded": len(receipt_pairs),
            "outboxHashesExcluded": len(outbox_hashes),
            "outboxPairsExcluded": len(outbox_pairs),
        },
        "exactCorpus": {
            **exact_meta,
            "survivingCandidateHashes": len(hits),
            "distinctSurvivingPairs": len(selected),
            "knownProjectedNetRelativeSwing": known_swing,
            "unknownProjectionPairs": unknown_swing_rows,
            "maximumNetRelativeSwing": float(len(selected)),
        },
        "plan": lane.artifact(plan_path),
        "checks": {
            "latestCompletePinnedUniqueCrawlUsed": True,
            "allRowsSoleHeldAtCrawl": True,
            "allExactSourcePinsRejoinedToAcceptedLedger": True,
            "baselineOwnedKnownReceiptAndOutboxExclusionsPassed": True,
            "oneBestCandidateSelectedPerTargetPair": True,
            "coefficientPayloadOmitted": True,
        },
        "receiptAudit": receipt_meta,
        "outboxAudit": {
            key: outbox_meta[key]
            for key in (
                "outboxFiles",
                "nonemptyOutboxFiles",
                "canonicalPolynomialRows",
                "distinctCoefficientHashes",
                "distinctPairsExcluded",
            )
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "coefficientManifestWrites": 0,
            "auditArtifactWrites": 2,
        },
    }
    lane.exclusive_json(certificate_path, certificate)
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "teamNumber": str(args.team_number),
                "publicCrawlId": int(crawl["crawl_id"]),
                "opponentSolePairs": len(placements),
                "eligibleOpponentPairs": len(eligible),
                "exactCandidateHashes": len(hits),
                "exactTargetPairs": len(selected),
                "knownProjectedNetRelativeSwing": known_swing,
                "unknownProjectionPairs": unknown_swing_rows,
                "maximumNetRelativeSwing": float(len(selected)),
                "certificate": lane.artifact(certificate_path),
                "plan": lane.artifact(plan_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileExistsError,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        lane.GuardFailure,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

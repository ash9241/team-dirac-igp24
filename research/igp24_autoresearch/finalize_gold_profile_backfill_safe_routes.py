#!/usr/bin/env python3
"""Seal newly guaranteed gold routes after exact profile-backfill batch 7."""

from __future__ import annotations

import json
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper


ROOT = audit.ROOT
DATA = audit.DATA
BACKFILL = DATA / "gold_profile_backfill_20260722_batch7/missing_profile_rows.jsonl"
CERTIFICATE = DATA / "gold_profile_backfill_batch7_safe_routes_certificate.json"
SUMMARY = DATA / "gold_profile_backfill_batch7_safe_routes_summary.json"
VALIDATOR = ROOT / "validate_gold_all_compatible_pair_route.py"


def main() -> int:
    full = audit.read_json(audit.CERTIFICATE)
    corpus = full.get("sealedPairCorpus") or {}
    backfill_artifact = audit.artifact(BACKFILL)
    batch7_plan = audit.read_json(
        DATA / "gold_profile_backfill_20260722_batch7/provenance_plan.json"
    )
    plan_boundary = batch7_plan.get("boundary") or {}
    batch7_selected = {
        str(value) for value in batch7_plan.get("selectedSignatures") or []
    }
    if (
        full.get("status")
        not in {
            "certified_fresh_gold_candidates_found",
            "certified_zero_immediate_gold_frontier_conditional_only",
            "certified_zero_full_frontier",
        }
        or corpus.get("profileBackfillBatch1OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch1Signatures", -1)) != 32
        or corpus.get("profileBackfillBatch2OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch2Signatures", -1)) != 35
        or corpus.get("profileBackfillBatch3OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch3Signatures", -1)) != 36
        or corpus.get("profileBackfillBatch4OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch4Signatures", -1)) != 36
        or corpus.get("profileBackfillBatch5OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch5Signatures", -1)) != 32
        or corpus.get("profileBackfillBatch6OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch6Signatures", -1)) != 50
        or corpus.get("profileBackfillBatch7OutputComplete") is not True
        or int(corpus.get("profileBackfillBatch7Signatures", -1)) != 50
        or int(corpus.get("profileBackfillCombinedSignatures", -1)) != 271
        or backfill_artifact not in list(corpus.get("profileArtifacts") or [])
        or backfill_artifact not in list(corpus.get("actionMaps") or [])
        or batch7_plan.get("schemaVersion")
        != "gold-profile-backfill-one-worker-plan-v1"
        or int(batch7_plan.get("batchNumber", -1)) != 7
        or len(batch7_selected) != 50
        or (batch7_plan.get("execution") or {}).get("heavyWorkerLaunched") is not False
        or (batch7_plan.get("execution") or {}).get("submissionAuthorized") is not False
        or int(full.get("boundary", {}).get("acceptedScoreablePairs", -1)) != 22_062
        or int(full.get("boundary", {}).get("acceptedScoreableAnchorRows", -1))
        != 927_813
        or full.get("boundary", {}).get("acceptedPairSetSha256")
        != plan_boundary.get("acceptedPairSetSha256")
        or full.get("boundary", {}).get("targetSnapshotSha256")
        != plan_boundary.get("targetSnapshotSha256")
        or (full.get("conditionalExecutionPolicy") or {}).get(
            "automaticHeavyExecutionAuthorized"
        )
        is not False
        or not all((full.get("checks") or {}).values())
    ):
        raise ValueError("post-backfill full-ledger certificate is not intact")
    classifications = full["classifications"]
    promoted = []
    for class_name in (
        "deterministicSingleOrbitRunbooks",
        "allCompatibleSafeRoutes",
    ):
        for row in classifications[class_name]:
            if (
                str(BACKFILL.relative_to(ROOT))
                not in set(map(str, row["profileArtifacts"]))
                or str(row["sourcePair"]) not in batch7_selected
            ):
                continue
            if int(row["length24OrbitCount"]) != 1:
                continue
            promoted.append(row)
    promoted.sort(
        key=lambda row: (
            0 if row["certaintyClass"] == "deterministic_single_orbit" else 1,
            -int(row["compatibleSuccessMass"].split("/")[0])
            / int(row["compatibleSuccessMass"].split("/")[1]),
            int(row["sourceAnchor"]["coefficientBytes"]),
            row["sourcePair"],
        )
    )

    seen_outcome_sets = set()
    runbooks = []
    connection = sqlite3.connect(f"file:{audit.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = audit.ledger_snapshot(connection)
        before = audit.volatile_boundary()
        exact_corpus = audit.exact_and_lineage_corpus(connection)
        after = audit.volatile_boundary()
        if (
            snapshot["acceptedPairSetSha256"]
            != full["boundary"]["acceptedPairSetSha256"]
            or snapshot["targetSnapshotSha256"]
            != full["boundary"]["targetSnapshotSha256"]
            or audit.compact_boundary(before)
            != full["boundary"]["receiptAndOutboxBoundary"]
            or audit.compact_boundary(after)
            != full["boundary"]["receiptAndOutboxBoundary"]
        ):
            raise ValueError("post-backfill full-ledger boundary changed")
        for row in promoted:
            outcomes = tuple(sorted(set(row["possibleTargetPairs"])))
            if outcomes in seen_outcome_sets:
                continue
            seen_outcome_sets.add(outcomes)
            labels = {value.rsplit("/r", 1)[0] for value in outcomes}
            if len(labels) != 1 or set(row["currentGoldTargetPairs"]) != set(outcomes):
                raise ValueError("promoted isolated route is not all-compatible current gold")
            target_label = next(iter(labels))
            source_label, source_r_raw = row["sourcePair"].rsplit("/r", 1)
            source = row["sourceAnchor"]
            source_key = (str(source["submissionId"]), int(source["polynomialIndex"]))
            ledger = connection.execute(
                "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
                "FROM polynomials p JOIN verifications v "
                "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
                "AND p.polynomial_index=?",
                (source["submissionId"], source["polynomialIndex"]),
            ).fetchone()
            if (
                ledger is None
                or str(ledger["coefficient_hash"]) != source["coefficientSha256"]
                or str(ledger["status"]) != "accepted"
                or int(ledger["scoreable"] or 0) != 1
                or str(ledger["label"]) != source_label
                or int(ledger["r"]) != int(source_r_raw)
                or source_key in exact_corpus["processedKeys"]
                or str(source["coefficientSha256"]) in exact_corpus["processedHashes"]
            ):
                raise ValueError("promoted source anchor changed")
            for value in outcomes:
                pair = (target_label, int(value.rsplit("/r", 1)[1]))
                state = snapshot["targets"].get(pair)
                if (
                    state is None
                    or int(state["teamCount"]) != 0
                    or pair in snapshot["baseline"]
                    or pair in snapshot["owned"]
                    or pair in snapshot["knownPairs"]
                ):
                    raise ValueError("promoted possible outcome is no longer fresh gold")
            route_id = f"gpbf7_safe_{len(runbooks)+1:03d}"
            output = DATA / f"{route_id}_result.jsonl"
            temporary = output.with_suffix(output.suffix + ".tmp")
            if output.exists() or temporary.exists():
                raise FileExistsError(f"isolated factor output already exists: {output}")
            command = [
                "/usr/local/bin/sage",
                "-python",
                "pair_sum_one.sage.py",
                str(source["submissionId"]),
                str(source["polynomialIndex"]),
                "--orbit-map",
                str(BACKFILL.relative_to(ROOT)),
                "--expected-source-hash",
                str(source["coefficientSha256"]),
                "--expected-target",
                target_label,
                "--output-jsonl",
                str(output.relative_to(ROOT)),
            ]
            shell = (
                f"test ! -e {shlex.quote(str(output.relative_to(ROOT)))} && "
                f"test ! -e {shlex.quote(str(temporary.relative_to(ROOT)))} && "
                f"exec /usr/bin/caffeinate -i {shlex.join(command)}"
            )
            runbooks.append(
                {
                    "routeId": route_id,
                    "status": "ready_waiting_for_root_heavy_clearance",
                    "certaintyClass": row["certaintyClass"],
                    "source": {
                        **source,
                        "label": source_label,
                        "r": int(source_r_raw),
                        "ledgerStatus": "accepted_scoreable",
                        "freshAgainstProcessedExactLineage": True,
                    },
                    "possibleCurrentGoldTargets": [
                        {
                            "pair": value,
                            "teamCountAtSeal": 0,
                            "projectedMarginalScoreUpper": "1",
                        }
                        for value in outcomes
                    ],
                    "exactAction": {
                        "targetLabel": target_label,
                        "orbitIndex": int(row["orbitIndex"]),
                        "length24OrbitCount": 1,
                        "compatibleClassCount": int(row["compatibleClassCount"]),
                        "compatibleSuccessMass": row["compatibleSuccessMass"],
                        "allCompatibleClassesCurrentGold": True,
                        "deterministicTargetPair": len(outcomes) == 1,
                        "profileArtifact": backfill_artifact,
                    },
                    "guards": {
                        "outputAbsentAtSeal": True,
                        "temporaryOutputAbsentAtSeal": True,
                        "sourceHashAcceptedScoreableAtSeal": True,
                        "allPossibleTargetPairsCurrentTc0NonbaselineUnownedUnknown": True,
                        "allReceiptAndOutboxPairsExcludedAtSeal": True,
                        "fullBoundaryMustRemainUnchangedAtExecution": True,
                        "submissionAuthorized": False,
                    },
                    "output": str(output.relative_to(ROOT)),
                    "heavyCommand": command,
                    "guardedHeavyCommand": shell,
                    "postflightCommand": [
                        "python3",
                        str(VALIDATOR.relative_to(ROOT)),
                        "--certificate",
                        str(CERTIFICATE.relative_to(ROOT)),
                        "--route-id",
                        route_id,
                        "--result",
                        str(output.relative_to(ROOT)),
                    ],
                }
            )
    finally:
        connection.close()

    certificate = {
        "schemaVersion": "gold-profile-backfill-safe-route-frontier-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_isolated_factor_runbooks_ready"
            if runbooks
            else "certified_no_new_safe_routes"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundary": full["boundary"],
        "fullReintersection": audit.artifact(audit.CERTIFICATE),
        "profileBackfill": backfill_artifact,
        "newRouteCensus": {
            "newDeterministicSingleOrbitRoutes": sum(
                row["certaintyClass"] == "deterministic_single_orbit"
                for row in promoted
            ),
            "newAllCompatibleSafeRoutes": sum(
                row["certaintyClass"] == "all_compatible_safe"
                for row in promoted
            ),
            "isolatedFactorRunbooks": len(runbooks),
            "distinctPossibleGoldPairs": len(
                {
                    target["pair"]
                    for row in runbooks
                    for target in row["possibleCurrentGoldTargets"]
                }
            ),
        },
        "ranking": [
            "exact deterministic target before all-compatible option set",
            "guaranteed compatible-class success mass",
            "shorter accepted source encoding",
            "source pair order",
        ],
        "runbooks": runbooks,
        "checks": {
            "profileBackfillBatch1_29Rows32SignaturesExact": True,
            "profileBackfillBatch2_25Rows35SignaturesExact": True,
            "profileBackfillBatch3_10Rows36SignaturesExact": True,
            "profileBackfillBatch4_7Rows36SignaturesExact": True,
            "profileBackfillBatch5_8Rows32SignaturesExact": True,
            "profileBackfillBatch6_10Rows50SignaturesExact": True,
            "profileBackfillBatch7_10Rows50SignaturesExact": True,
            "conditionalAutomaticExecutionRemainsPaused": True,
            "fullLedgerReceiptOutboxReintersectionCurrent": True,
            "allRunbooksSingleLength24Orbit": all(
                row["exactAction"]["length24OrbitCount"] == 1 for row in runbooks
            ),
            "allCompatibleOutcomesCurrentGold": True,
            "allSourcesAcceptedScoreableFresh": True,
            "allOutputsAbsent": True,
            "runbooksPairSetDeduplicated": len(seen_outcome_sets) == len(runbooks),
            "coefficientAndCredentialPayloadOmitted": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "heavyWorkersLaunched": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered safe-route certificate")
    if not all(certificate["checks"].values()):
        raise ValueError("safe-route certificate checks failed")
    audit.atomic_replace(CERTIFICATE, rendered)
    summary = {
        "schemaVersion": "gold-profile-backfill-safe-route-summary-v1",
        "status": certificate["status"],
        "certificate": audit.artifact(CERTIFICATE),
        **certificate["newRouteCensus"],
        "bestNextHeavyCommand": (
            runbooks[0]["guardedHeavyCommand"] if runbooks else None
        ),
        "coefficientMaterialIncluded": False,
        "heavyWorkerLaunched": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    audit.atomic_replace(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

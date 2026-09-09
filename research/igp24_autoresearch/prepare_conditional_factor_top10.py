#!/usr/bin/env python3
"""Seal the best pending source-deduplicated conditional factor batch.

This is a light-only planner.  It ranks the frozen conditional factor tasks by
exact compatible-class gold mass, factor-task route reuse, current target
freshness, and source cost.  It emits serial factor/Frobenius/stage commands,
but launches no worker and performs no submission.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper


ROOT = audit.ROOT
DATA = audit.DATA
RUNBOOKS = DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_runbooks.json"
PLAN = DATA / "gold_profile_backfill_20260722_batch1/conditional_top10_plan.json"
PREFLIGHT = DATA / "gold_profile_backfill_20260722_batch1/conditional_top10_preflight.json"
STAGER = ROOT / "stage_conditional_factor_gold.py"
FACTOR_WORKER = ROOT / "pair_sum_one.sage.py"
FROBENIUS_WORKER = ROOT / "frobenius_discriminate.sage.py"
SAFE_RESULT = DATA / "gold_safe_factor_0001_result.jsonl"
SAFE_STAGE = DATA / "gold_profile_backfill_20260722_batch1/safe_factor_stage_certificate.json"

BATCH_SIZE = 10
SAFE_PAIR = ("24T15253", 8)
SAFE_FACTOR_HASH = "511102a73e86ab3ebb4b5ff7665c2df0861316ff9f276f53280facb69569d423"
REQUIRED_PLAN_CHECKS = (
    "parentArtifactsPinned",
    "completedFactorOutputsExcluded",
    "safeOutcomePairAndHashExcluded",
    "allCurrentGoldTargetsFresh",
    "allSourcesAcceptedScoreableAndPinned",
    "batchSourceDeduplicated",
    "taskPathsUniqueAndAbsent",
    "completeOrbitTargetsPinned",
    "serialFailClosedCommandsPresent",
    "coefficientAndCredentialPayloadOmitted",
    "noWorkerNetworkSubmissionOrLedgerWrite",
)


def parse_pair(value: str) -> tuple[str, int]:
    label, raw_r = str(value).rsplit("/r", 1)
    pair = (label, int(raw_r))
    if not label.startswith("24T") or pair[1] not in range(0, 25, 2):
        raise ValueError(f"invalid pair: {value}")
    return pair


def fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def record_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def artifact(path: Path) -> dict:
    return audit.artifact(path)


def action_row(path: Path, source_label: str) -> dict:
    matches = [
        row
        for row in helper.read_jsonl(path)
        if str(row.get("sourceLabel")) == source_label
    ]
    if len(matches) != 1:
        raise ValueError(f"action row for {source_label} is absent or nonunique: {path}")
    return matches[0]


def task_mass(runbook: dict) -> Fraction:
    return sum(
        (Fraction(str(route["conditionalSuccessFractionExact"])) for route in runbook["routes"]),
        Fraction(),
    )


def rank_key(row: dict) -> tuple:
    # Exact expected score per factor worker is the primary objective.  Reuse
    # and target freshness break ties before source encoding cost.
    return (
        -row["mass"],
        -row["routeReuse"],
        -row["freshGoldTargets"],
        row["receiptOutcomeCollisionCount"] + row["outboxOutcomeCollisionCount"],
        row["coefficientBytes"],
        row["factorTaskId"],
    )


def main() -> int:
    full = audit.read_json(audit.CERTIFICATE)
    source = audit.read_json(RUNBOOKS)
    if (
        full.get("status") != "certified_fresh_gold_candidates_found"
        or source.get("factorTaskCount") != len(source.get("runbooks") or [])
        or int(source.get("factorTaskCount", -1)) != 147
        or not all((full.get("checks") or {}).values())
    ):
        raise ValueError("conditional factor parents are not intact")
    if source["parentGoldCertificate"] != artifact(audit.CERTIFICATE):
        raise ValueError("conditional runbooks do not pin the current full audit")

    safe_rows = helper.read_jsonl(SAFE_RESULT)
    if len(safe_rows) != 1:
        raise ValueError("safe factor result is absent or nonunique")
    safe_row = safe_rows[0]
    if (
        (str(safe_row.get("targetLabel")), int(safe_row.get("targetR", -1))) != SAFE_PAIR
        or str(safe_row.get("coefficientSha256")) != SAFE_FACTOR_HASH
        or safe_row.get("status") != "certified"
    ):
        raise ValueError("safe outcome exclusion evidence changed")

    connection = sqlite3.connect(f"file:{audit.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        snapshot = audit.ledger_snapshot(connection)
        before = audit.volatile_boundary()
        corpus = audit.exact_and_lineage_corpus(connection)
        excluded = audit.receipt_and_outbox_exclusions(connection, corpus)
        after = audit.volatile_boundary()
        if audit.compact_boundary(before) != audit.compact_boundary(after):
            raise ValueError("receipt/outbox boundary changed while ranking")

        candidates = []
        completed = []
        stale = []
        for runbook in source["runbooks"]:
            output = ROOT / str(runbook["output"])
            if output.exists() or output.with_suffix(output.suffix + ".tmp").exists():
                completed.append(
                    {
                        "factorTaskId": runbook["factorTaskId"],
                        "sourcePair": runbook["sourcePair"],
                        "sourceCoefficientSha256": runbook["sourceAnchor"]["coefficientSha256"],
                        "output": str(output.relative_to(ROOT)),
                    }
                )
                continue
            source_anchor = runbook["sourceAnchor"]
            ledger = connection.execute(
                "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
                "FROM polynomials p JOIN verifications v "
                "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
                "AND p.polynomial_index=?",
                (source_anchor["submissionId"], source_anchor["polynomialIndex"]),
            ).fetchone()
            source_pair = parse_pair(runbook["sourcePair"])
            if (
                ledger is None
                or str(ledger["coefficient_hash"]) != source_anchor["coefficientSha256"]
                or str(ledger["status"]) != "accepted"
                or int(ledger["scoreable"] or 0) != 1
                or (str(ledger["label"]), int(ledger["r"])) != source_pair
            ):
                stale.append({"factorTaskId": runbook["factorTaskId"], "reason": "source"})
                continue

            possible = {
                parse_pair(value)
                for route in runbook["routes"]
                for value in route["possibleTargetPairs"]
            }
            gold = {
                parse_pair(value)
                for route in runbook["routes"]
                for value in route["currentGoldTargetPairs"]
            }
            if not gold or not gold <= possible:
                raise ValueError("conditional route has invalid gold/possible sets")
            if SAFE_PAIR in possible or source_anchor["coefficientSha256"] == SAFE_FACTOR_HASH:
                stale.append({"factorTaskId": runbook["factorTaskId"], "reason": "safe_exclusion"})
                continue
            fresh = True
            for pair in gold:
                state = snapshot["targets"].get(pair)
                if (
                    state is None
                    or int(state["teamCount"]) != 0
                    or pair in snapshot["baseline"]
                    or pair in snapshot["knownPairs"]
                    or pair in excluded["receiptPairs"]
                    or pair in excluded["outboxPairs"]
                ):
                    fresh = False
                    break
            if not fresh:
                stale.append({"factorTaskId": runbook["factorTaskId"], "reason": "target"})
                continue
            candidates.append(
                {
                    "factorTaskId": str(runbook["factorTaskId"]),
                    "runbook": runbook,
                    "mass": task_mass(runbook),
                    "routeReuse": len(runbook["routes"]),
                    "freshGoldTargets": len(gold),
                    "possible": possible,
                    "gold": gold,
                    "receiptCollisions": possible & excluded["receiptPairs"],
                    "outboxCollisions": possible & excluded["outboxPairs"],
                    "receiptOutcomeCollisionCount": len(possible & excluded["receiptPairs"]),
                    "outboxOutcomeCollisionCount": len(possible & excluded["outboxPairs"]),
                    "coefficientBytes": int(source_anchor["coefficientBytes"]),
                }
            )
    finally:
        connection.close()

    candidates.sort(key=rank_key)
    selected = candidates[:BATCH_SIZE]
    if len(selected) != BATCH_SIZE:
        raise ValueError(f"wanted {BATCH_SIZE} pending tasks, found {len(selected)}")

    tasks = []
    seen_sources = set()
    seen_paths = set()
    for rank, item in enumerate(selected, start=1):
        runbook = item["runbook"]
        task_id = item["factorTaskId"]
        source_anchor = runbook["sourceAnchor"]
        source_key = (
            str(source_anchor["submissionId"]),
            int(source_anchor["polynomialIndex"]),
            str(source_anchor["coefficientSha256"]),
        )
        if source_key in seen_sources:
            raise ValueError("selected factor sources are not deduplicated")
        seen_sources.add(source_key)
        action_path = ROOT / str(runbook["actionArtifact"]["path"])
        if artifact(action_path) != runbook["actionArtifact"]:
            raise ValueError("conditional action artifact changed")
        source_label, source_r = parse_pair(runbook["sourcePair"])
        action = action_row(action_path, source_label)
        expected_orbits = sorted(
            [
                {
                    "orbitIndex": int(row["orbitIndex"]),
                    "orbitSize": int(row["orbitSize"]),
                    "targetLabel": str(row["targetLabel"]),
                    "targetT": int(row["targetT"]),
                }
                for row in action.get("targets") or []
                if int(row.get("orbitSize", -1)) == 24
            ],
            key=lambda row: row["orbitIndex"],
        )
        orbit_count = int(runbook["length24OrbitCount"])
        if len(expected_orbits) != orbit_count:
            raise ValueError("complete action orbit target list is inconsistent")
        route_labels = {pair[0] for pair in item["possible"]}
        if not route_labels <= {row["targetLabel"] for row in expected_orbits}:
            raise ValueError("selected route label is absent from action targets")

        output = str(runbook["output"])
        frobenius_path = (
            f"data/{task_id}_frobenius_certificate.json" if orbit_count > 1 else None
        )
        stage_manifest = f"outbox/gold_conditional_top10_{task_id}.txt"
        stage_certificate = f"data/gold_conditional_top10_{task_id}_stage.json"
        planned_paths = [output, output + ".tmp", stage_manifest, stage_certificate]
        if frobenius_path:
            planned_paths.extend([frobenius_path, frobenius_path + ".tmp"])
        if any((ROOT / path).exists() for path in planned_paths):
            raise FileExistsError(f"planned task path already exists: {task_id}")
        if seen_paths & set(planned_paths):
            raise ValueError("selected tasks reuse an output/stage path")
        seen_paths.update(planned_paths)

        factor_argv = list(map(str, runbook["heavyCommand"]))
        if orbit_count > 1 and "--all-degree-24" not in factor_argv:
            factor_argv.insert(factor_argv.index("--output-jsonl"), "--all-degree-24")
        preflight_argv = [
            "python3",
            str(STAGER.relative_to(ROOT)),
            "--plan",
            str(PLAN.relative_to(ROOT)),
            "--task-id",
            task_id,
            "--preflight",
        ]
        guarded_factor = (
            f"{shlex.join(preflight_argv)} >/dev/null && "
            f"test ! -e {shlex.quote(output)} && test ! -e {shlex.quote(output + '.tmp')} && "
            f"exec /usr/bin/caffeinate -i {shlex.join(factor_argv)}"
        )
        frobenius_argv = None
        guarded_frobenius = None
        if frobenius_path:
            frobenius_argv = [
                "/usr/local/bin/sage",
                "-python",
                str(FROBENIUS_WORKER.relative_to(ROOT)),
                "--input",
                output,
                "--source-pair",
                source_label,
                str(source_r),
                "--prime-bound",
                "5000",
                "--require-resolved",
                "--output",
                frobenius_path,
            ]
            guarded_frobenius = (
                f"test -s {shlex.quote(output)} && "
                f"test ! -e {shlex.quote(frobenius_path)} && "
                f"test ! -e {shlex.quote(frobenius_path + '.tmp')} && "
                f"exec /usr/bin/caffeinate -i {shlex.join(frobenius_argv)}"
            )
        stage_argv = [
            "python3",
            str(STAGER.relative_to(ROOT)),
            "--plan",
            str(PLAN.relative_to(ROOT)),
            "--task-id",
            task_id,
            "--result",
            output,
        ]
        if frobenius_path:
            stage_argv.extend(["--frobenius-certificate", frobenius_path])
        routes = []
        for route in runbook["routes"]:
            routes.append(
                {
                    **route,
                    "fullAuditRouteRecordSha256": record_sha256(
                        next(
                            row
                            for row in full["classifications"]["conditionalRoutes"]
                            if row["routeId"] == route["routeId"]
                        )
                    ),
                }
            )
        tasks.append(
            {
                "rank": rank,
                "taskId": task_id,
                "originalFactorTaskRecordSha256": record_sha256(runbook),
                "status": "ready_serial_fail_closed_no_worker_launched_by_planner",
                "source": {
                    "submissionId": source_key[0],
                    "polynomialIndex": source_key[1],
                    "coefficientSha256": source_key[2],
                    "coefficientBytes": int(source_anchor["coefficientBytes"]),
                },
                "sourcePair": runbook["sourcePair"],
                "actionArtifact": runbook["actionArtifact"],
                "length24OrbitCount": orbit_count,
                "expectedOrbitTargets": expected_orbits,
                "routeTargetLabels": sorted(route_labels, key=lambda value: int(value[3:])),
                "selectedRoutes": routes,
                "routeReuseCount": item["routeReuse"],
                "possibleTargetPairs": [
                    audit.pair_text(pair) for pair in sorted(item["possible"], key=audit.pair_key)
                ],
                "currentGoldTargetPairs": [
                    audit.pair_text(pair) for pair in sorted(item["gold"], key=audit.pair_key)
                ],
                "targetFreshness": {
                    "allCurrentGoldTargetsTc0NonbaselineUnknownUnowned": True,
                    "receiptOutcomeCollisions": [
                        audit.pair_text(pair)
                        for pair in sorted(item["receiptCollisions"], key=audit.pair_key)
                    ],
                    "outboxOutcomeCollisions": [
                        audit.pair_text(pair)
                        for pair in sorted(item["outboxCollisions"], key=audit.pair_key)
                    ],
                    "currentGoldReceiptOrOutboxCollisions": [],
                },
                "exactCompatibleClassGoldMass": fraction_text(item["mass"]),
                "expectedGoldScorePerFactorWorker": fraction_text(item["mass"]),
                "expectedGoldScorePerHeavyInvocation": fraction_text(
                    item["mass"] / (2 if orbit_count > 1 else 1)
                ),
                "factorOutput": output,
                "frobeniusCertificate": frobenius_path,
                "stageManifest": stage_manifest,
                "stageCertificate": stage_certificate,
                "factorCommand": factor_argv,
                "guardedFactorCommand": guarded_factor,
                "frobeniusCommand": frobenius_argv,
                "guardedFrobeniusCommand": guarded_frobenius,
                "stageCommand": stage_argv,
                "submissionAuthorized": False,
            }
        )

    total_mass = sum((Fraction(row["exactCompatibleClassGoldMass"]) for row in tasks), Fraction())
    frobenius_workers = sum(row["frobeniusCertificate"] is not None for row in tasks)
    reuse_leaders = sorted(
        candidates,
        key=lambda row: (-row["routeReuse"], -row["mass"], row["factorTaskId"]),
    )[:5]
    checks = {name: True for name in REQUIRED_PLAN_CHECKS}
    plan = {
        "schemaVersion": "conditional-gold-factor-top-batch-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_serial_fail_closed_no_worker_launched",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "artifacts": {
            "fullGoldAudit": artifact(audit.CERTIFICATE),
            "conditionalFactorRunbooks": artifact(RUNBOOKS),
            "factorWorker": artifact(FACTOR_WORKER),
            "frobeniusWorker": artifact(FROBENIUS_WORKER),
            "stager": artifact(STAGER),
            "safeOutcomeResult": artifact(SAFE_RESULT),
            "safeOutcomeStage": artifact(SAFE_STAGE),
        },
        "safeOutcomeExclusion": {
            "pair": audit.pair_text(SAFE_PAIR),
            "coefficientSha256": SAFE_FACTOR_HASH,
            "excludedFromEverySelectedTask": True,
        },
        "boundary": {
            "acceptedPairSetSha256": snapshot["acceptedPairSetSha256"],
            "targetSnapshotSha256": snapshot["targetSnapshotSha256"],
            "receiptAndOutboxBoundaryAtSeal": audit.compact_boundary(after),
            "executionPolicy": "unrelated growth allowed; each task reruns target-specific preflight",
        },
        "ranking": {
            "order": [
                "exact compatible-class expected gold mass per factor worker descending",
                "factor-task reuse across conditional gold routes descending",
                "fresh distinct current-gold targets descending",
                "receipt/outbox possible-outcome collision count ascending",
                "source coefficient bytes ascending",
            ],
            "sharpCutoffObserved": False,
            "selectionRule": "best 10 pending tasks because rank 10 and 11 have no sharp mass cutoff",
            "globalReuseLeaders": [
                {
                    "factorTaskId": row["factorTaskId"],
                    "routeReuseCount": row["routeReuse"],
                    "exactCompatibleClassGoldMass": fraction_text(row["mass"]),
                }
                for row in reuse_leaders
            ],
        },
        "completedOrInFlightOutputsExcluded": completed,
        "staleTasksExcluded": stale,
        "batch": {
            "tasks": len(tasks),
            "factorWorkers": len(tasks),
            "frobeniusWorkers": frobenius_workers,
            "heavyInvocations": len(tasks) + frobenius_workers,
            "expectedGoldMassExact": fraction_text(total_mass),
            "expectedGoldMassDecimal": f"{float(total_mass):.12f}",
            "expectedGoldMassPerFactorWorkerExact": fraction_text(total_mass / len(tasks)),
            "expectedGoldMassPerHeavyInvocationExact": fraction_text(
                total_mass / (len(tasks) + frobenius_workers)
            ),
            "bestNextTaskId": tasks[0]["taskId"],
            "bestNextExpectedGoldMassExact": tasks[0]["exactCompatibleClassGoldMass"],
            "bestNextGuardedFactorCommand": tasks[0]["guardedFactorCommand"],
        },
        "tasks": tasks,
        "checks": checks,
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    if len(tasks) != len({row["taskId"] for row in tasks}) or len(seen_sources) != len(tasks):
        raise ValueError("task/source deduplication failed")
    rendered = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered conditional plan")
    audit.atomic_replace(PLAN, rendered)
    preflight = {
        "schemaVersion": "conditional-gold-factor-top-batch-preflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_pending_top10_serial_batch_ready",
        "plan": artifact(PLAN),
        "bestNextTaskId": tasks[0]["taskId"],
        "bestNextGuardedFactorCommand": tasks[0]["guardedFactorCommand"],
        "expectedGoldMassExact": fraction_text(total_mass),
        "factorWorkers": len(tasks),
        "frobeniusWorkers": frobenius_workers,
        "checks": checks,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": plan["sideEffects"],
    }
    audit.atomic_replace(PREFLIGHT, json.dumps(preflight, indent=2, sort_keys=True) + "\n")
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

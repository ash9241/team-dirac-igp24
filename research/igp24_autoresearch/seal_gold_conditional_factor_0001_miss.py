#!/usr/bin/env python3
"""Seal the exact covered-outcome miss for gold conditional factor 0001."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_current_tc7_frontier as receipt_helper
import prepare_v11_pair_delta as helper
import run_deterministic_frontier_coordinator as coordinator
import stage_single_exact_census as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNBOOKS = DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_runbooks.json"
PROFILE = DATA / "gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl"
RESULT = DATA / "gold_conditional_factor_0001_result.jsonl"
CERTIFICATE = DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_0001_exact_miss_certificate.json"
FORBIDDEN_MANIFEST = ROOT / "outbox/gold_conditional_factor_0001_20260722.txt"
RUNBOOK_SHA256 = "6b51abe62fa604727d2207e335d0c579d7fa2d663111f184bf0980dd3167943d"
SAFE_RECEIPT = "sub_aed2e90f4b804427b2d0f1e497e042ae"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def main() -> int:
    if CERTIFICATE.exists():
        raise FileExistsError(f"refusing to overwrite {CERTIFICATE}")
    if FORBIDDEN_MANIFEST.exists():
        raise ValueError("conditional miss unexpectedly has a manifest")
    if helper.sha256_path(RUNBOOKS) != RUNBOOK_SHA256:
        raise ValueError("conditional runbook artifact changed")
    document = json.loads(RUNBOOKS.read_text(encoding="utf-8"))
    matches = [
        row
        for row in document.get("runbooks") or []
        if row.get("factorTaskId") == "conditional_factor_0001"
    ]
    if len(matches) != 1:
        raise ValueError("conditional task is absent or nonunique")
    task = matches[0]
    route = task["routes"][0]
    possible = {
        (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
        for value in route["possibleTargetPairs"]
    }
    gold = {
        (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
        for value in route["currentGoldTargetPairs"]
    }
    if (
        task.get("sourcePair") != "24T13798/r0"
        or int(task.get("length24OrbitCount", -1)) != 1
        or route.get("compatibleSuccessMass") != "158/207"
        or possible != {
            ("24T13074", 0),
            ("24T13074", 4),
            ("24T13074", 8),
            ("24T13074", 12),
        }
        or gold != {
            ("24T13074", 4),
            ("24T13074", 8),
            ("24T13074", 12),
        }
    ):
        raise ValueError("conditional route envelope changed")

    profile_matches = [
        row for row in read_jsonl(PROFILE) if row.get("sourceLabel") == "24T13798"
    ]
    if (
        len(profile_matches) != 1
        or profile_matches[0].get("status") != "certified"
        or not helper.certificate_is_exact(profile_matches[0])
        or profile_matches[0].get("sourceR") != [0]
    ):
        raise ValueError("exact conditional profile changed")
    mass = Counter()
    class_indexes = set()
    for profile in profile_matches[0].get("profiles") or []:
        if int(profile.get("sourceR", -1)) != 0:
            continue
        class_index = int(profile["classIndex"])
        if class_index in class_indexes:
            raise ValueError("duplicate compatible class")
        class_indexes.add(class_index)
        targets = [
            target
            for target in audit.pair_audit.profile_targets(profile)
            if int(target["orbitIndex"]) == int(route["orbitIndex"])
        ]
        if len(targets) != 1 or str(targets[0]["targetLabel"]) != "24T13074":
            raise ValueError("profile target/action mismatch")
        mass[int(targets[0]["targetR"])] += int(profile["classSize"])
    if mass != Counter({0: 49, 4: 102, 8: 24, 12: 32}):
        raise ValueError("exact compatible-class mass changed")

    result_rows = read_jsonl(RESULT)
    if len(result_rows) != 1:
        raise ValueError("conditional result is not isolated")
    result = result_rows[0]
    source = task["sourceAnchor"]
    realized = (str(result.get("targetLabel")), int(result.get("targetR", -1)))
    if (
        result.get("status") != "certified"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(result.get("sourcePolynomialIndex", -1))
        != int(source["polynomialIndex"])
        or str(result.get("sourceCoefficientSha256"))
        != str(source["coefficientSha256"])
        or str(result.get("sourceLabel")) != "24T13798"
        or int(result.get("sourceR", -1)) != 0
        or realized != ("24T13074", 0)
        or realized not in possible
        or realized in gold
    ):
        raise ValueError("result is not the exact covered conditional branch")
    line = exact.canonical_polynomial_line(result.get("coefficientLine"))
    if line is None:
        raise ValueError("conditional factor is not canonical degree 24")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result.get("coefficientSha256")):
        raise ValueError("conditional factor hash mismatch")
    orbit = result.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected = [int(value) for value in orbit.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit.get("exponents") or []]
    orbit_targets = result.get("orbitTargets") or []
    if (
        not actual
        or actual != expected
        or actual.count(24) != 1
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
        or len(orbit_targets) != 1
        or int(orbit_targets[0].get("orbitIndex", -1)) != 1
        or int(orbit_targets[0].get("orbitSize", -1)) != 24
        or str(orbit_targets[0].get("targetLabel")) != "24T13074"
    ):
        raise ValueError("conditional factor/orbit certificate is not exact")

    config = coordinator.Config(
        root=ROOT,
        data=DATA,
        outbox=ROOT / "outbox",
        receipts=ROOT / "receipts",
        database=DATA / "ledger.sqlite3",
        certificate=RUNBOOKS,
        batch_name="gold_conditional_factor_0001",
        reservations=(),
    )
    boundary_before = audit.compact_boundary(audit.volatile_boundary())
    exclusions = coordinator.exclusion_snapshot(config)
    _outbox_hashes, _outbox_pairs, _outbox_audit, pairs_by_hash = (
        coordinator.outbox_snapshot(config)
    )
    safe_receipt_audit = receipt_helper.receipt_manifest_audit(
        config, SAFE_RECEIPT, 1, exclusions, pairs_by_hash
    )
    boundary_after = audit.compact_boundary(audit.volatile_boundary())
    if boundary_before != boundary_after:
        raise ValueError("receipt/outbox boundary changed during miss audit")

    connection = sqlite3.connect(
        f"file:{config.database.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        source_row = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
            "FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
            "AND p.polynomial_index=?",
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        if (
            source_row is None
            or str(source_row["coefficient_hash"]) != source["coefficientSha256"]
            or str(source_row["status"]) != "accepted"
            or int(source_row["scoreable"] or 0) != 1
            or str(source_row["label"]) != "24T13798"
            or int(source_row["r"]) != 0
        ):
            raise ValueError("conditional source pin changed")
        realized_state = connection.execute(
            "SELECT team_count,discovered,minimum_disc_abs,generated_at "
            "FROM targets WHERE label=? AND r=?", realized
        ).fetchone()
        realized_owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? "
            "AND status='accepted' AND scoreable=1 LIMIT 1", realized
        ).fetchone() is not None
        realized_known = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", realized
        ).fetchone() is not None
        if (
            realized_state is None
            or int(realized_state["team_count"]) != 1
            or not realized_owned
            or not realized_known
            or realized not in exclusions["receiptPairs"]
        ):
            raise ValueError("realized nongold branch is not covered fail-closed")
        gold_states = {}
        for outcome in gold:
            state = connection.execute(
                "SELECT team_count,discovered,minimum_disc_abs,generated_at "
                "FROM targets WHERE label=? AND r=?", outcome
            ).fetchone()
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", outcome
            ).fetchone() is not None
            known = connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", outcome
            ).fetchone() is not None
            if (
                state is None
                or int(state["team_count"]) != 0
                or baseline
                or known
                or outcome in exclusions["receiptPairs"]
                or outcome in exclusions["outboxPairs"]
            ):
                raise ValueError("a gold branch changed during miss postflight")
            gold_states[pair_text(outcome)] = {
                "teamCount": int(state["team_count"]),
                "discovered": bool(state["discovered"]),
            }
        candidate_known = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
        ).fetchone() is not None
    finally:
        connection.close()

    if FORBIDDEN_MANIFEST.exists():
        raise ValueError("miss manifest appeared before certificate seal")
    certificate = {
        "schemaVersion": "gold-conditional-factor-exact-miss-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_covered_nongold_miss_fail_closed_not_staged",
        "factorTaskId": "conditional_factor_0001",
        "routeId": route["routeId"],
        "runbook": helper.relative_artifact(RUNBOOKS),
        "profile": helper.relative_artifact(PROFILE),
        "result": helper.relative_artifact(RESULT),
        "sourcePair": "24T13798/r0",
        "possibleTargetPairs": [
            pair_text(value) for value in sorted(possible, key=audit.pair_key)
        ],
        "goldTargetPairs": [
            pair_text(value) for value in sorted(gold, key=audit.pair_key)
        ],
        "realizedTarget": {
            "pair": pair_text(realized),
            "compatibleClassMass": "49/207",
            "teamCount": int(realized_state["team_count"]),
            "locallyOwned": realized_owned,
            "knownVerificationPair": realized_known,
            "receiptCoveredPair": realized in exclusions["receiptPairs"],
        },
        "goldMassExact": "158/207",
        "candidateSha256": digest,
        "candidateKnownLedgerHash": candidate_known,
        "factorCertificate": {
            "squarefreeFactorDegreesMatchExactOrbitSizes": True,
            "degree24FactorCount": 1,
            "allFactorExponentsOne": True,
            "targetLabelFixedByUniqueLength24Orbit": True,
            "targetRComputedExactly": 0,
        },
        "stageDecision": {
            "decision": "fail_closed_no_manifest",
            "reason": "exact factor realized covered nongold option",
            "manifestCreated": False,
            "projectedMarginalScoreExact": "0",
        },
        "currentGoldBranchAudit": gold_states,
        "currentExclusionAudit": exclusions["audit"],
        "namedSafeGoldReceiptAudit": safe_receipt_audit,
        "receiptOutboxBoundary": boundary_after,
        "checks": {
            "sourceAcceptedScoreableAndHashPinned": True,
            "exactActionAndCompatibleClassProfilePinned": True,
            "compatibleMassPartitionIs158Gold49Covered": True,
            "allDegree24FactorsCertified": True,
            "realizedPairAssignedExactly": True,
            "realizedPairIsCoveredNongold": True,
            "allThreeGoldBranchesRemainFreshTc0": True,
            "safeGoldReceiptAed2IncludedAndExcluded": True,
            "allCurrentReceiptsAndOutboxesScanned": True,
            "receiptOutboxBoundaryStableDuringAudit": True,
            "noManifestCreatedForMiss": True,
            "coefficientAndCredentialPayloadOmitted": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "heavyWorkersLaunchedForThisTask": 1,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered miss certificate")
    if not all(certificate["checks"].values()):
        raise ValueError("miss certificate checks failed")
    audit.atomic_replace(CERTIFICATE, rendered)
    print(json.dumps({
        "status": certificate["status"],
        "realizedTarget": pair_text(realized),
        "goldMassExact": "158/207",
        "manifestCreated": False,
        "certificate": helper.relative_artifact(CERTIFICATE),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal the exact covered-outcome miss for gold conditional factor 0002."""

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
PROFILE = DATA / "autopilot_pair_delta_20260722_v5/missing_pair_all.jsonl"
RESULT = DATA / "gold_conditional_factor_0002_result.jsonl"
CERTIFICATE = DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_0002_exact_miss_certificate.json"
FORBIDDEN_MANIFEST = ROOT / "outbox/gold_conditional_factor_0002_20260722.txt"
RUNBOOK_SHA256 = "6b51abe62fa604727d2207e335d0c579d7fa2d663111f184bf0980dd3167943d"
PROFILE_SHA256 = "365c820c97d818593d30c3d0195a7508de5f68f31cc0d5e459e0c6f3b8ed5849"
SAFE_RECEIPT = "sub_aed2e90f4b804427b2d0f1e497e042ae"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def ptext(value: tuple[str, int]) -> str:
    return f"{value[0]}/r{value[1]}"


def main() -> int:
    if CERTIFICATE.exists() or FORBIDDEN_MANIFEST.exists():
        raise FileExistsError("0002 miss certificate or forbidden manifest already exists")
    if helper.sha256_path(RUNBOOKS) != RUNBOOK_SHA256:
        raise ValueError("conditional runbook artifact changed")
    if helper.sha256_path(PROFILE) != PROFILE_SHA256:
        raise ValueError("v5 exact action/profile artifact changed")
    document = json.loads(RUNBOOKS.read_text())
    matches = [r for r in document["runbooks"] if r["factorTaskId"] == "conditional_factor_0002"]
    if len(matches) != 1:
        raise ValueError("0002 task is absent or nonunique")
    task = matches[0]
    route = task["routes"][0]
    source = task["sourceAnchor"]
    possible = {("24T15251", 16), ("24T15251", 20)}
    gold = ("24T15251", 16)
    covered = ("24T15251", 20)
    if (
        task["sourcePair"] != "24T15927/r16"
        or int(task["length24OrbitCount"]) != 1
        or route["possibleTargetPairs"] != ["24T15251/r16", "24T15251/r20"]
        or route["currentGoldTargetPairs"] != ["24T15251/r16"]
        or route["conditionalSuccessFractionExact"] != "5/7"
        or route["compatibleSuccessMass"] != "15/21"
    ):
        raise ValueError("0002 route envelope changed")

    profile_matches = [r for r in rows(PROFILE) if r.get("sourceLabel") == "24T15927"]
    if (
        len(profile_matches) != 1
        or profile_matches[0].get("status") != "certified"
        or not helper.certificate_is_exact(profile_matches[0])
        or profile_matches[0].get("sourceR") != [16]
    ):
        raise ValueError("0002 exact action/profile changed")
    mass = Counter()
    classes = set()
    for profile in profile_matches[0].get("profiles") or []:
        if int(profile.get("sourceR", -1)) != 16:
            continue
        ci = int(profile["classIndex"])
        if ci in classes:
            raise ValueError("duplicate compatible class")
        classes.add(ci)
        targets = [
            target for target in audit.pair_audit.profile_targets(profile)
            if int(target["orbitIndex"]) == 1
        ]
        if len(targets) != 1 or str(targets[0]["targetLabel"]) != "24T15251":
            raise ValueError("0002 action/profile target mismatch")
        mass[int(targets[0]["targetR"])] += int(profile["classSize"])
    if mass != Counter({16: 15, 20: 6}):
        raise ValueError("0002 compatible-class mass changed")

    result_rows = rows(RESULT)
    if len(result_rows) != 1:
        raise ValueError("0002 result is not isolated")
    result = result_rows[0]
    realized = (str(result.get("targetLabel")), int(result.get("targetR", -1)))
    if (
        result.get("status") != "certified"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != source["submissionId"]
        or int(result.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
        or str(result.get("sourceCoefficientSha256")) != source["coefficientSha256"]
        or str(result.get("sourceLabel")) != "24T15927"
        or int(result.get("sourceR", -1)) != 16
        or realized != covered
        or realized not in possible
    ):
        raise ValueError("0002 result is not the exact covered branch")
    line = exact.canonical_polynomial_line(result.get("coefficientLine"))
    if line is None:
        raise ValueError("0002 factor is not canonical degree 24")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result.get("coefficientSha256")):
        raise ValueError("0002 candidate hash mismatch")
    orbit = result.get("orbitCertificate") or {}
    actual = list(map(int, orbit.get("actualDegrees") or []))
    expected = list(map(int, orbit.get("expectedDegrees") or []))
    exponents = list(map(int, orbit.get("exponents") or []))
    targets = result.get("orbitTargets") or []
    if (
        not actual or actual != expected or actual.count(24) != 1
        or len(exponents) != len(actual) or any(x != 1 for x in exponents)
        or len(targets) != 1 or int(targets[0].get("orbitIndex", -1)) != 1
        or int(targets[0].get("orbitSize", -1)) != 24
        or str(targets[0].get("targetLabel")) != "24T15251"
    ):
        raise ValueError("0002 exact factor/action certificate failed")

    config = coordinator.Config(
        root=ROOT, data=DATA, outbox=ROOT / "outbox", receipts=ROOT / "receipts",
        database=DATA / "ledger.sqlite3", certificate=RUNBOOKS,
        batch_name="gold_conditional_factor_0002", reservations=(),
    )
    before = audit.compact_boundary(audit.volatile_boundary())
    exclusions = coordinator.exclusion_snapshot(config)
    _hashes, _pairs, _outbox_audit, pairs_by_hash = coordinator.outbox_snapshot(config)
    safe_receipt = receipt_helper.receipt_manifest_audit(
        config, SAFE_RECEIPT, 1, exclusions, pairs_by_hash
    )
    after = audit.compact_boundary(audit.volatile_boundary())
    if before != after:
        raise ValueError("receipt/outbox boundary changed during 0002 audit")

    connection = sqlite3.connect(f"file:{config.database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        source_row = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r FROM polynomials p "
            "JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? AND p.polynomial_index=?",
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        if (
            source_row is None or str(source_row["coefficient_hash"]) != source["coefficientSha256"]
            or str(source_row["status"]) != "accepted" or int(source_row["scoreable"] or 0) != 1
            or str(source_row["label"]) != "24T15927" or int(source_row["r"]) != 16
        ):
            raise ValueError("0002 source pin changed")
        gold_state = connection.execute(
            "SELECT team_count,discovered FROM targets WHERE label=? AND r=?", gold
        ).fetchone()
        gold_known = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", gold
        ).fetchone() is not None
        gold_baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", gold
        ).fetchone() is not None
        covered_state = connection.execute(
            "SELECT team_count,discovered FROM targets WHERE label=? AND r=?", covered
        ).fetchone()
        covered_owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND status='accepted' "
            "AND scoreable=1 LIMIT 1", covered
        ).fetchone() is not None
        covered_known = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", covered
        ).fetchone() is not None
        candidate_known = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
        ).fetchone() is not None
    finally:
        connection.close()
    if (
        gold_state is None or int(gold_state["team_count"]) != 0 or gold_known or gold_baseline
        or gold in exclusions["receiptPairs"] or gold in exclusions["outboxPairs"]
    ):
        raise ValueError("0002 gold branch is no longer fresh")
    if (
        covered_state is None or int(covered_state["team_count"]) != 1
        or not covered_owned or not covered_known
        or covered not in exclusions["receiptPairs"] or covered not in exclusions["outboxPairs"]
    ):
        raise ValueError("0002 realized branch is not fully covered")
    if FORBIDDEN_MANIFEST.exists():
        raise ValueError("0002 miss manifest appeared")

    certificate = {
        "schemaVersion": "gold-conditional-factor-exact-miss-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_covered_nongold_miss_fail_closed_not_staged",
        "factorTaskId": "conditional_factor_0002",
        "routeId": route["routeId"],
        "runbook": helper.relative_artifact(RUNBOOKS),
        "profile": helper.relative_artifact(PROFILE),
        "result": helper.relative_artifact(RESULT),
        "sourcePair": "24T15927/r16",
        "possibleTargetPairs": ["24T15251/r16", "24T15251/r20"],
        "goldTargetPairs": ["24T15251/r16"],
        "goldMassExact": "5/7",
        "realizedTarget": {
            "pair": "24T15251/r20",
            "compatibleClassMass": "2/7",
            "teamCount": int(covered_state["team_count"]),
            "locallyOwned": covered_owned,
            "knownVerificationPair": covered_known,
            "receiptCoveredPair": covered in exclusions["receiptPairs"],
            "outboxCoveredPair": covered in exclusions["outboxPairs"],
        },
        "candidateSha256": digest,
        "candidateKnownLedgerHash": candidate_known,
        "factorCertificate": {
            "squarefreeFactorDegreesMatchExactOrbitSizes": True,
            "degree24FactorCount": 1,
            "allFactorExponentsOne": True,
            "targetLabelFixedByUniqueLength24Orbit": True,
            "targetRComputedExactly": 20,
        },
        "stageDecision": {
            "decision": "fail_closed_no_manifest",
            "reason": "exact factor realized receipt/outbox-covered nongold option",
            "manifestCreated": False,
            "projectedMarginalScoreExact": "0",
        },
        "freshGoldBranchAudit": {"pair": ptext(gold), "teamCount": 0},
        "currentExclusionAudit": exclusions["audit"],
        "namedSafeGoldReceiptAudit": safe_receipt,
        "receiptOutboxBoundary": after,
        "checks": {
            "sourceAcceptedScoreableAndHashPinned": True,
            "exactActionAndCompatibleClassProfilePinned": True,
            "compatibleMassPartitionIsFiveSeventhsGold": True,
            "allDegree24FactorsCertified": True,
            "realizedPairAssignedExactly": True,
            "realizedPairIsReceiptOutboxCoveredNongold": True,
            "goldBranchRemainsFreshTc0": True,
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
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered) or not all(certificate["checks"].values()):
        raise ValueError("0002 miss metadata failed final checks")
    audit.atomic_replace(CERTIFICATE, rendered)
    print(json.dumps({
        "status": certificate["status"],
        "realizedTarget": "24T15251/r20",
        "goldMassExact": "5/7",
        "manifestCreated": False,
        "certificate": helper.relative_artifact(CERTIFICATE),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

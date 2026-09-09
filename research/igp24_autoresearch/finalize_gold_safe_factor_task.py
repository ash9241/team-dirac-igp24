#!/usr/bin/env python3
"""Validate and stage the one authorized all-compatible gold pair factor."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper
import stage_single_exact_census as exact


ROOT = audit.ROOT
DATA = audit.DATA
RUNBOOKS = DATA / "gold_profile_backfill_20260722_batch1/safe_factor_runbooks.json"
PROFILE = DATA / "gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl"
RESULT = DATA / "gold_safe_factor_0001_result.jsonl"
POSTFLIGHT = DATA / "gold_safe_factor_0001_result_postflight.json"
MANIFEST = ROOT / "outbox/gold_profile_backfill_safe_factor_20260722.txt"
CERTIFICATE = DATA / "gold_profile_backfill_20260722_batch1/safe_factor_stage_certificate.json"
MAPPING = DATA / "gold_profile_backfill_20260722_batch1/safe_factor_receipt_mapping_ready.json"
EXPECTED_RUNBOOK_SHA256 = "d40648b81e1cacbf715a211809086c94e86caecb2a804fb36b8e59ecd1f443e9"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def one_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("safe factor result must contain exactly one object")
    return rows[0]


def pair(value: str) -> tuple[str, int]:
    label, raw_r = str(value).rsplit("/r", 1)
    return label, int(raw_r)


def coefficient_free_write(path: Path, value: dict) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError(f"coefficient payload entered metadata: {path}")
    audit.atomic_replace(path, rendered)


def main() -> int:
    for destination in (POSTFLIGHT, MANIFEST, CERTIFICATE, MAPPING):
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        if destination.exists() or temporary.exists():
            raise FileExistsError(f"refusing to overwrite staged artifact: {destination}")
    if helper.sha256_path(RUNBOOKS) != EXPECTED_RUNBOOK_SHA256:
        raise ValueError("safe runbook artifact changed")
    runbook_doc = read_json(RUNBOOKS)
    runbooks = runbook_doc.get("runbooks") or []
    if (
        len(runbooks) != 1
        or runbook_doc.get("coefficientMaterialIncluded") is not False
        or runbook_doc.get("credentialMaterialIncluded") is not False
        or runbook_doc.get("submissionAuthorized") is not False
    ):
        raise ValueError("safe runbook handoff is not intact")
    runbook = runbooks[0]
    if (
        runbook.get("factorTaskId") != "safe_factor_0001"
        or (ROOT / str(runbook["output"])).resolve() != RESULT.resolve()
        or int(runbook.get("length24OrbitCount", -1)) != 1
        or len(runbook.get("routes") or []) != 1
    ):
        raise ValueError("safe runbook identity or cardinality changed")
    route = runbook["routes"][0]
    possible = {pair(value) for value in route["possibleTargetPairs"]}
    current_gold = {pair(value) for value in route["currentGoldTargetPairs"]}
    if (
        route.get("certaintyClass") != "all_compatible_safe"
        or route.get("conditionalSuccessFractionExact") != "1"
        or possible != current_gold
        or possible != {("24T15253", 8), ("24T15253", 16)}
    ):
        raise ValueError("safe outcome envelope changed")

    profile_rows = [
        json.loads(line)
        for line in PROFILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected_profiles = [
        row for row in profile_rows if str(row.get("sourceLabel")) == "24T15218"
    ]
    if (
        len(selected_profiles) != 1
        or selected_profiles[0].get("status") != "certified"
        or not helper.certificate_is_exact(selected_profiles[0])
        or 16 not in {int(value) for value in selected_profiles[0].get("sourceR") or []}
    ):
        raise ValueError("exact safe-route profile row changed")
    profile_row = selected_profiles[0]
    compatible = [
        row
        for row in profile_row.get("profiles") or []
        if int(row.get("sourceR", -1)) == 16
    ]
    profile_outcomes = set()
    class_indexes = set()
    compatible_mass = 0
    for row in compatible:
        class_index = int(row["classIndex"])
        if class_index in class_indexes:
            raise ValueError("duplicate exact compatible class")
        class_indexes.add(class_index)
        compatible_mass += int(row["classSize"])
        targets = [
            target
            for target in audit.pair_audit.profile_targets(row)
            if int(target["orbitIndex"]) == int(route["orbitIndex"])
        ]
        if len(targets) != 1:
            raise ValueError("compatible class lacks unique selected orbit image")
        profile_outcomes.add(
            (str(targets[0]["targetLabel"]), int(targets[0]["targetR"]))
        )
    if (
        len(compatible) != 3
        or compatible_mass != 63
        or route.get("compatibleSuccessMass") != "63/63"
        or profile_outcomes != possible
    ):
        raise ValueError("exact compatible-class outcome set changed")

    result = one_jsonl(RESULT)
    source = runbook["sourceAnchor"]
    realized = (str(result.get("targetLabel")), int(result.get("targetR", -1)))
    if (
        result.get("status") != "certified"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(result.get("sourcePolynomialIndex", -1))
        != int(source["polynomialIndex"])
        or str(result.get("sourceCoefficientSha256"))
        != str(source["coefficientSha256"])
        or str(result.get("sourceLabel")) != "24T15218"
        or int(result.get("sourceR", -1)) != 16
        or realized not in possible
    ):
        raise ValueError("factor result escaped sealed source/outcome envelope")
    line = exact.canonical_polynomial_line(result.get("coefficientLine"))
    if line is None:
        raise ValueError("factor result is not a canonical degree-24 polynomial")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result.get("coefficientSha256")):
        raise ValueError("factor result coefficient hash mismatch")
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
        or int(orbit_targets[0].get("orbitIndex", -1)) != int(route["orbitIndex"])
        or int(orbit_targets[0].get("orbitSize", -1)) != 24
        or str(orbit_targets[0].get("targetLabel")) != realized[0]
    ):
        raise ValueError("factor/orbit certificate is not exact and complete")

    full = read_json(audit.CERTIFICATE)
    connection = sqlite3.connect(f"file:{audit.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        snapshot = audit.ledger_snapshot(connection)
        if (
            snapshot["acceptedPairSetSha256"]
            != full["boundary"]["acceptedPairSetSha256"]
            or snapshot["targetSnapshotSha256"]
            != full["boundary"]["targetSnapshotSha256"]
        ):
            raise ValueError("accepted or target boundary changed")
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
            or str(source_row["label"]) != "24T15218"
            or int(source_row["r"]) != 16
        ):
            raise ValueError("source ledger pin changed")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
        ).fetchone() is not None:
            raise ValueError("factor hash is already known in ledger")
        for outcome in possible:
            state = snapshot["targets"].get(outcome)
            if (
                state is None
                or int(state["teamCount"]) != 0
                or outcome in snapshot["baseline"]
                or outcome in snapshot["owned"]
                or outcome in snapshot["knownPairs"]
            ):
                raise ValueError("possible outcome is no longer fresh current gold")
        before = audit.volatile_boundary()
        corpus = audit.exact_and_lineage_corpus(connection)
        exclusions = audit.receipt_and_outbox_exclusions(connection, corpus)
        after = audit.volatile_boundary()
        if audit.compact_boundary(before) != audit.compact_boundary(after):
            raise ValueError("receipt/outbox boundary changed during postflight")
        if (
            digest in exclusions["receiptHashes"]
            or digest in exclusions["outboxHashes"]
            or realized in exclusions["receiptPairs"]
            or realized in exclusions["outboxPairs"]
            or any(
                outcome in exclusions["receiptPairs"]
                or outcome in exclusions["outboxPairs"]
                for outcome in possible
            )
        ):
            raise ValueError("factor or safe outcome is already receipt/outbox covered")
        target_state = snapshot["targets"][realized]
    finally:
        connection.close()

    now = datetime.now(timezone.utc).isoformat()
    postflight = {
        "schemaVersion": "gold-safe-factor-postflight-v1",
        "createdAt": now,
        "status": "certified_exact_current_gold_ready_for_authorized_local_staging",
        "runbook": helper.relative_artifact(RUNBOOKS),
        "result": helper.relative_artifact(RESULT),
        "routeId": route["routeId"],
        "sourcePair": "24T15218/r16",
        "realizedTarget": audit.pair_text(realized),
        "possibleSafeTargets": [
            audit.pair_text(value) for value in sorted(possible, key=audit.pair_key)
        ],
        "candidateSha256": digest,
        "factorCertificate": {
            "squarefreeFactorDegreesMatchExactOrbitSizes": True,
            "degree24FactorCount": 1,
            "allFactorExponentsOne": True,
            "frobeniusAssignmentRequired": False,
            "targetLabelFixedByUniqueLength24Orbit": True,
            "targetRComputedExactly": int(realized[1]),
        },
        "novelty": {
            "knownLedgerHash": False,
            "knownVerificationPair": False,
            "baselinePair": False,
            "receiptHashOrPair": False,
            "outboxHashOrPair": False,
        },
        "boundary": audit.compact_boundary(after),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "heavyWorkersLaunchedEarlierForThisTask": 1,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    coefficient_free_write(POSTFLIGHT, postflight)

    audit.atomic_replace(MANIFEST, line + "\n")
    manifest_artifact = helper.relative_artifact(MANIFEST)
    certificate = {
        "schemaVersion": "gold-safe-factor-authorized-stage-v1",
        "createdAt": now,
        "status": "certified_exact_gold_staged_offline_not_submitted",
        "localStagingAuthorized": True,
        "submissionAuthorized": False,
        "runbook": helper.relative_artifact(RUNBOOKS),
        "result": helper.relative_artifact(RESULT),
        "postflight": helper.relative_artifact(POSTFLIGHT),
        "manifest": {**manifest_artifact, "rows": 1},
        "sourcePair": "24T15218/r16",
        "targetPair": audit.pair_text(realized),
        "candidateSha256": digest,
        "teamCountAtStage": int(target_state["teamCount"]),
        "projectedMarginalScoreExact": "1",
        "allCompatibleOutcomeSetWasGoldAtStage": True,
        "factorAndAssignment": postflight["factorCertificate"],
        "checks": {
            "sourceAcceptedScoreableAndHashPinned": True,
            "exactProfileOutcomeEnvelopePinned": True,
            "allCompatibleOutcomesCurrentGold": True,
            "allDegree24FactorsCertified": True,
            "uniqueDegree24OrbitNeedsNoFrobeniusDisambiguation": True,
            "realizedTargetInSafeOutcomeEnvelope": True,
            "candidateCanonicalPrimitiveMonicDegree24": True,
            "candidateHashAndPairNovel": True,
            "receiptOutboxBoundaryStableDuringPostflight": True,
            "manifestContainsOneExactNovelRow": True,
            "coefficientAndCredentialPayloadOmittedFromMetadata": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "heavyWorkersLaunchedForThisTask": 1,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    coefficient_free_write(CERTIFICATE, certificate)
    mapping = {
        "schemaVersion": "gold-safe-factor-receipt-mapping-ready-v1",
        "createdAt": now,
        "status": "ready_for_receipt_mapping_after_submission",
        "stageCertificate": helper.relative_artifact(CERTIFICATE),
        "manifest": {**manifest_artifact, "rows": 1},
        "routeCount": 1,
        "distinctCandidateHashes": 1,
        "distinctTargetPairs": 1,
        "mappings": [
            {
                "manifestPosition": 0,
                "candidateSha256": digest,
                "targetPair": audit.pair_text(realized),
                "teamCount": int(target_state["teamCount"]),
                "result": helper.relative_artifact(RESULT),
                "postflight": helper.relative_artifact(POSTFLIGHT),
                "receiptSubmissionId": None,
            }
        ],
        "receiptSubmissionId": None,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    coefficient_free_write(MAPPING, mapping)
    print(json.dumps({
        "status": certificate["status"],
        "targetPair": certificate["targetPair"],
        "projectedMarginalScoreExact": "1",
        "manifest": helper.relative_artifact(MANIFEST),
        "certificate": helper.relative_artifact(CERTIFICATE),
        "mapping": helper.relative_artifact(MAPPING),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

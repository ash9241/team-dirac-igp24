#!/usr/bin/env python3
"""Receipt/outbox-aware finalizer for the guarded tc3 negative twists."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import run_low_contention_remaining_batch as exact
import run_low_contention_sequential as lane
import sair_api
import stage_single_exact_census as single


ROOT = lane.ROOT
DATA = lane.DATA
OUTBOX = lane.OUTBOX
RUNBOOK = DATA / "low_contention_tc3_even_twist_guarded_runbook.json"
ACTION_MAP = DATA / "low_contention_tc3_even_twist_action_subset.jsonl"
RAW_RESULTS = DATA / "low_contention_tc3_even_twist_results.jsonl"
RAW_SUMMARY = DATA / "low_contention_tc3_even_twist_summary.json"
RAW_EMPTY_MANIFEST = OUTBOX / "low_contention_tc3_even_twist_safe.txt"
CENSUS_MANIFEST = OUTBOX / "low_contention_tc3_even_twist_single_census_raw.txt"
CENSUS_CERTIFICATE = DATA / "low_contention_tc3_even_twist_single_census_certificate.json"
CENSUS_SUMMARY = DATA / "low_contention_tc3_even_twist_single_census_summary.json"
FINAL_MANIFEST = OUTBOX / "low_contention_tc3_even_twist_final_duplicate_free_20260722.txt"
FINAL_MAPPING = DATA / "low_contention_tc3_even_twist_final_receipt_mapping_ready.json"
FINAL_CERTIFICATE = DATA / "low_contention_tc3_even_twist_final_certificate.json"
NEWEST_RECEIPTS = (
    "sub_a1115b5e820c47c59e3be632d1b673b5",
    "sub_b4f7f2bd853446aca3382d22345a73ec",
    "sub_6eeaa15d8d994fd19646243de90b4b09",
    "sub_bc7a3dd9294543bd813bed8ba413972b",
)
OWN_PROVISIONAL_MANIFESTS = {RAW_EMPTY_MANIFEST.resolve(), CENSUS_MANIFEST.resolve()}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate_newest_receipts() -> tuple[list[dict], set[str]]:
    facts, union = [], set()
    for submission_id in NEWEST_RECEIPTS:
        path = lane.RECEIPTS / f"{submission_id}.json"
        receipt = lane.read_json(path)
        response = receipt.get("response") or {}
        manifest = Path(str(receipt.get("manifest"))).resolve()
        lines, hashes = sair_api.validated_manifest(manifest)
        if (
            receipt.get("commit") is not True
            or str(response.get("submissionId")) != submission_id
            or int(response.get("rejectedCount", 0)) != 0
            or list(response.get("failedPolynomials") or [])
            or len(lines) != int(receipt.get("polynomials", -1))
            or lane.sha256_path(manifest) != str(receipt.get("manifestHash"))
        ):
            raise lane.GuardFailure(f"newest receipt is not intact: {submission_id}")
        union.update(hashes)
        facts.append({
            "submissionId": submission_id,
            "receipt": lane.artifact(path),
            "manifest": {
                **lane.artifact(manifest),
                "polynomials": len(lines),
                "bytes": manifest.stat().st_size,
            },
            "submissionStatus": str(response.get("submissionStatus")),
            "rejectedCount": 0,
            "failedCount": 0,
        })
    return facts, union


def outbox_snapshot(
    pair_index: dict[str, set[tuple[str, int]]]
) -> tuple[set[str], set[tuple[str, int]], dict]:
    hashes: set[str] = set()
    pairs: set[tuple[str, int]] = set()
    files = []
    for path in sorted(OUTBOX.glob("*.txt")):
        if path.resolve() in OWN_PROVISIONAL_MANIFESTS or path.stat().st_size == 0:
            continue
        lines, manifest_hashes = sair_api.validated_manifest(path)
        hashes.update(manifest_hashes)
        for digest in manifest_hashes:
            pairs.update(pair_index.get(digest, set()))
        files.append({
            **lane.artifact(path),
            "polynomials": len(lines),
            "bytes": path.stat().st_size,
        })
    index_text = "".join(
        f"{row['path']}\0{row['sha256']}\n" for row in files
    )
    return hashes, pairs, {
        "manifestFiles": len(files),
        "manifestHashes": len(hashes),
        "mappedTargetPairs": len(pairs),
        "manifestFileIndexSha256": hashlib.sha256(index_text.encode()).hexdigest(),
    }


def main() -> int:
    if FINAL_MANIFEST.exists() or FINAL_MAPPING.exists() or FINAL_CERTIFICATE.exists():
        raise lane.GuardFailure("final tc3 twist artifact already exists")
    runbook = lane.read_json(RUNBOOK)
    ranked = runbook.get("rankedSources") or []
    if (
        runbook.get("schemaVersion")
        != "low-contention-tc3-even-twist-guarded-runbook-v1"
        or len(ranked) != 8
        or [int(row["priority"]) for row in ranked] != list(range(1, 9))
        or runbook.get("coefficientMaterialIncluded") is not False
    ):
        raise lane.GuardFailure("tc3 twist runbook changed")
    for key in ("actionBuilder", "twistWorker"):
        artifact = runbook["inputs"][key]
        path = (ROOT / artifact["path"]).resolve()
        if lane.sha256_path(path) != artifact["sha256"]:
            raise lane.GuardFailure(f"pinned twist program changed: {key}")
    receipt_facts, newest_receipt_hashes = validate_newest_receipts()

    action_rows = jsonl(ACTION_MAP)
    actions = {str(row["sourceLabel"]): row for row in action_rows}
    if len(actions) != 7 or any(
        int(row["systemCount"]) != 1
        or set(map(str, row["targetLabels"])) != {str(row["sourceLabel"])}
        for row in action_rows
    ):
        raise lane.GuardFailure("twist action map is not exact single-target")
    raw_rows = jsonl(RAW_RESULTS)
    raw_summary = lane.read_json(RAW_SUMMARY)
    negative = [row for row in raw_rows if row.get("twistSign") == "negative"]
    positive = [row for row in raw_rows if row.get("twistSign") == "positive"]
    if (
        len(raw_rows) != 16
        or len(negative) != 8
        or len(positive) != 8
        or int(raw_summary.get("certifiedGeneratedTwists", -1)) != 16
        or int(raw_summary.get("deltaUniqueEvenHashes", -1)) != 8
        or int(raw_summary.get("generatedIrreducibleChecksPassed", -1)) != 16
        or int(raw_summary.get("freshPrimeSquarefreeCertificates", -1)) != 16
        or int(raw_summary.get("networkCalls", -1)) != 0
        or int(raw_summary.get("submissionCalls", -1)) != 0
    ):
        raise lane.GuardFailure("raw eight-anchor twist execution is incomplete")

    candidates, corpus = single.scan_candidates(DATA)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for candidate in candidates:
        pair_index[str(candidate["coefficientSha256"])].add(
            (str(candidate["targetLabel"]), int(candidate["targetR"]))
        )
    outbox_hashes, outbox_pairs, outbox_audit = outbox_snapshot(pair_index)
    with lane.connect_ro() as connection:
        receipt_hashes, receipt_pairs, receipt_audit = lane.receipt_snapshot(connection)
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        targets = {
            (str(row["label"]), int(row["r"])): dict(row)
            for row in connection.execute("SELECT * FROM targets")
        }
        by_source = {str(row["coefficientSha256"]): row for row in ranked}
        selected, reports, selected_pairs, selected_hashes = [], [], set(), set()
        for row in negative:
            source_hash = str(row.get("sourceCoefficientSha256"))
            source = by_source.get(source_hash)
            pair = (str(row.get("targetLabel")), int(row.get("targetR", -1)))
            line = single.canonical_polynomial_line(row.get("coefficientLine"))
            digest = hashlib.sha256(line.encode("ascii")).hexdigest() if line else ""
            reasons = []
            ledger = connection.execute(
                "SELECT p.coefficients,p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
                "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
                "WHERE p.submission_id=? AND p.polynomial_index=?",
                (row.get("sourceSubmissionId"), row.get("sourcePolynomialIndex")),
            ).fetchone()
            action = actions.get(str(row.get("sourceLabel")))
            target = targets.get(pair)
            if source is None:
                reasons.append("source_not_in_guarded_runbook")
            elif (
                str(row.get("sourceSubmissionId")) != str(source["submissionId"])
                or int(row.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
                or str(row.get("sourceLabel")) != str(source["label"])
                or int(row.get("sourceR", -1)) != int(source["r"])
                or pair[1] != int(source["exactNegativeR"])
            ):
                reasons.append("source_or_negative_signature_pin_changed")
            if ledger is None or (
                str(ledger["coefficient_hash"]) != source_hash
                or str(ledger["status"]) != "accepted"
                or int(ledger["scoreable"] or 0) != 1
                or str(ledger["label"]) != str(row.get("sourceLabel"))
                or int(ledger["r"]) != int(row.get("sourceR", -1))
                or hashlib.sha256(str(ledger["coefficients"]).encode("ascii")).hexdigest()
                != source_hash
            ):
                reasons.append("source_reconstruction_failed")
            if (
                action is None
                or pair[0] != str(row.get("sourceLabel"))
                or set(map(str, row.get("allBlockSystemsTargetLabels") or [])) != {pair[0]}
                or row.get("actionResolutionMethod") != "all-block-systems-same-target"
                or int(row.get("actionSystemCount", -1)) != 1
            ):
                reasons.append("exact_action_assignment_failed")
            if (
                line is None
                or digest != str(row.get("coefficientSha256"))
                or row.get("twistIrreducible") is not True
                or row.get("sourceSquarefreeModRamificationPrime") is not True
                or int(row.get("twistDirectRealRootCount", -1)) != pair[1]
                or not str(row.get("genericActionProof", "")).strip()
            ):
                reasons.append("arithmetic_certificate_failed")
            if target is None or int(target["discovered"] or 0) != 1:
                reasons.append("target_not_live_discovered")
            if pair in baseline or pair in owned:
                reasons.append("target_baseline_or_owned")
            if digest in receipt_hashes or digest in newest_receipt_hashes:
                reasons.append("candidate_receipt_hash")
            if pair in receipt_pairs:
                reasons.append("target_receipt_pair")
            if digest in outbox_hashes or pair in outbox_pairs:
                reasons.append("candidate_or_pair_reserved_in_other_outbox")
            if connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
            ).fetchone() is not None:
                reasons.append("candidate_hash_known_in_ledger")
            if digest in selected_hashes or pair in selected_pairs:
                reasons.append("intra_batch_duplicate_hash_or_pair")
            report = {
                "priority": int(source["priority"]) if source else None,
                "sourceLabel": str(row.get("sourceLabel")),
                "sourceR": int(row.get("sourceR", -1)),
                "sourceCoefficientSha256": source_hash,
                "targetPair": f"{pair[0]}/r{pair[1]}",
                "targetTeamCount": int(target["team_count"]) if target else None,
                "candidateSha256": digest,
                "status": "certified_exact_safe_selected" if not reasons else "excluded_fail_closed",
                "exclusionReasons": reasons,
            }
            reports.append(report)
            if not reasons:
                selected.append((source, row, line, digest, pair, target))
                selected_hashes.add(digest)
                selected_pairs.add(pair)

    census = lane.read_json(CENSUS_CERTIFICATE)
    census_hashes = {str(row["coefficientSha256"]) for row in census.get("selected") or []}
    if (
        int(census.get("selectedCount", -1)) != 8
        or census_hashes != {str(row["coefficientSha256"]) for row in negative}
        or not all((census.get("checks") or {}).values())
    ):
        raise lane.GuardFailure("receipt-aware exact SINGLE census did not certify all twists")
    selected.sort(key=lambda item: int(item[0]["priority"]))
    lines = [item[2] for item in selected]
    hashes = [item[3] for item in selected]
    lane.exclusive_text(FINAL_MANIFEST, "\n".join(lines) + "\n")
    offline = exact.offline_manifest_check(FINAL_MANIFEST, hashes, receipt_hashes)
    postflights = []
    mappings = []
    for position, (source, row, _line, digest, pair, target) in enumerate(selected, start=1):
        postflight_path = DATA / f"low_contention_tc3_even_twist_candidate_{position:03d}_postflight.json"
        postflight = {
            "schemaVersion": "low-contention-tc3-even-twist-postflight-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": "certified_exact_negative_twist_novel_safe_not_submitted",
            "priority": int(source["priority"]),
            "source": {
                "submissionId": source["submissionId"],
                "polynomialIndex": source["polynomialIndex"],
                "label": source["label"],
                "r": source["r"],
                "coefficientSha256": source["coefficientSha256"],
                "exactReconstructionPinned": True,
            },
            "target": {
                "pair": f"{pair[0]}/r{pair[1]}",
                "teamCount": int(target["team_count"]),
                "discovered": True,
                "baseline": False,
                "locallyOwned": False,
                "receiptCovered": False,
                "otherOutboxCovered": False,
            },
            "candidate": {
                "coefficientSha256": digest,
                "primitiveMonicDegree24": True,
                "irreducible": True,
                "negativeSignatureExact": True,
                "genericActionExactSingleTarget": True,
                "freshRamificationPrimeSquarefreeSource": True,
                "knownLedgerHash": False,
                "receiptHash": False,
                "otherOutboxHash": False,
            },
            "rawResult": {"path": lane.relative(RAW_RESULTS), "record": raw_rows.index(row) + 1},
            "coefficientMaterialIncluded": False,
            "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
        }
        lane.exclusive_json(postflight_path, postflight)
        postflights.append(lane.artifact(postflight_path))
        mappings.append({
            "manifestPosition": position,
            "priority": int(source["priority"]),
            "targetPair": f"{pair[0]}/r{pair[1]}",
            "targetTeamCount": int(target["team_count"]),
            "candidateSha256": digest,
            "postflight": lane.artifact(postflight_path),
            "receiptSubmissionId": None,
        })
    projection = sum(
        (Fraction(1, 2 ** int(item[5]["team_count"])) for item in selected),
        Fraction(0),
    )
    mapping = {
        "schemaVersion": "low-contention-tc3-even-twist-receipt-mapping-ready-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_receipt_mapping_after_submission",
        "manifest": {**lane.artifact(FINAL_MANIFEST), "polynomials": len(selected), "bytes": FINAL_MANIFEST.stat().st_size},
        "routeCount": len(selected),
        "distinctCandidateHashes": len(hashes),
        "distinctTargetPairs": len(selected_pairs),
        "mappings": mappings,
        "receiptSubmissionId": None,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
    }
    lane.exclusive_json(FINAL_MAPPING, mapping)
    certificate = {
        "schemaVersion": "low-contention-tc3-even-twist-final-batch-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_negative_twists_duplicate_free_not_submitted",
        "runbook": lane.artifact(RUNBOOK),
        "actionMap": {**lane.artifact(ACTION_MAP), "rows": len(action_rows)},
        "rawExecution": {
            "results": {**lane.artifact(RAW_RESULTS), "generatedTwists": len(raw_rows)},
            "summary": lane.artifact(RAW_SUMMARY),
            "anchorsProcessed": 8,
            "positiveTwistsCertified": 8,
            "negativeTwistsCertified": 8,
            "maximumConcurrentSageGapWorkers": 1,
        },
        "singleExactCensus": {
            "certificate": lane.artifact(CENSUS_CERTIFICATE),
            "summary": lane.artifact(CENSUS_SUMMARY),
            "selectedNegativeTwists": 8,
        },
        "newestReceiptCrossCheck": receipt_facts,
        "receiptExclusion": receipt_audit,
        "outboxExclusion": outbox_audit,
        "candidateCorpus": corpus,
        "routes": sorted(reports, key=lambda row: row["priority"] or 999),
        "exactNegativeArithmeticHits": len(negative),
        "selectedSafeRows": len(selected),
        "failClosedExclusions": len(negative) - len(selected),
        "projectedMarginalScoreExact": str(projection),
        "combinedManifest": {**lane.artifact(FINAL_MANIFEST), "polynomials": len(selected), "bytes": FINAL_MANIFEST.stat().st_size},
        "combinedOfflineDryRun": offline,
        "receiptMappingReady": lane.artifact(FINAL_MAPPING),
        "postflights": postflights,
        "checks": {
            "allEightGuardedSourcesReconstructedExactly": len(negative) == 8,
            "allNegativeSignaturesCertifiedExactly": len(negative) == 8,
            "allActionsExactSingleTarget": len(actions) == 7,
            "allSelectedTargetsLiveNonbaselineUnowned": len(selected) == len(negative),
            "allSelectedHashesCoefficientNovel": len(hashes) == len(set(hashes)),
            "allSelectedPairsDistinct": len(selected) == len(selected_pairs),
            "allReceiptsIncludingNewestFourExcluded": newest_receipt_hashes <= receipt_hashes,
            "allOtherOutboxHashesPairsExcluded": True,
            "combinedManifestOfflineValidated": offline.get("commit") is False,
            "certificateContainsNoCoefficientPayload": True,
        },
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"sageWorkerProcesses": 2, "maximumConcurrentSageGapWorkers": 1, "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    if not all(certificate["checks"].values()):
        raise lane.GuardFailure("final tc3 twist certificate checks did not all pass")
    lane.exclusive_json(FINAL_CERTIFICATE, certificate)
    print(json.dumps({
        "status": certificate["status"],
        "exactNegativeArithmeticHits": len(negative),
        "selectedSafeRows": len(selected),
        "failClosedExclusions": len(negative) - len(selected),
        "projectedMarginalScoreExact": str(projection),
        "manifest": lane.relative(FINAL_MANIFEST),
        "manifestSha256": lane.sha256_path(FINAL_MANIFEST),
        "certificate": lane.relative(FINAL_CERTIFICATE),
        "mapping": lane.relative(FINAL_MAPPING),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (lane.GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

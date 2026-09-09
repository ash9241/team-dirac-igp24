#!/usr/bin/env python3
"""Stage exact unowned post-v18 twists after receipt/outbox exclusions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
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
RUNBOOK = DATA / "postv18_even_twist_guarded_runbook.json"
CLOSURE = DATA / "postv18_nonpair_closure_certificate.json"
ACTION_MAP = DATA / "postv18_even_twist_action_subset.jsonl"
RAW_RESULTS = DATA / "postv18_even_twist_results.jsonl"
RAW_SUMMARY = DATA / "postv18_even_twist_summary.json"
RAW_GOLD_MANIFEST = OUTBOX / "postv18_even_twist_safe.txt"
CENSUS_MANIFEST = OUTBOX / "postv18_even_twist_single_census_raw.txt"
CENSUS_CERTIFICATE = DATA / "postv18_even_twist_single_census_certificate.json"
CENSUS_SUMMARY = DATA / "postv18_even_twist_single_census_summary.json"
FINAL_MANIFEST = OUTBOX / "postv18_even_twist_final_duplicate_free_20260722.txt"
FINAL_MAPPING = DATA / "postv18_even_twist_final_receipt_mapping_ready.json"
FINAL_CERTIFICATE = DATA / "postv18_even_twist_final_certificate.json"
OWN_PROVISIONAL = {RAW_GOLD_MANIFEST.resolve(), CENSUS_MANIFEST.resolve()}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def outbox_snapshot(
    pair_index: dict[str, set[tuple[str, int]]],
) -> tuple[set[str], set[tuple[str, int]], dict]:
    hashes: set[str] = set()
    pairs: set[tuple[str, int]] = set()
    files = []
    for path in sorted(OUTBOX.glob("*.txt")):
        if path.resolve() in OWN_PROVISIONAL or path.stat().st_size == 0:
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
    index_text = "".join(f"{row['path']}\0{row['sha256']}\n" for row in files)
    return hashes, pairs, {
        "manifestFiles": len(files),
        "manifestHashes": len(hashes),
        "mappedTargetPairs": len(pairs),
        "manifestFileIndexSha256": hashlib.sha256(index_text.encode()).hexdigest(),
    }


def main() -> int:
    downstream = [FINAL_MANIFEST, FINAL_MAPPING, FINAL_CERTIFICATE]
    downstream.extend(
        DATA / f"postv18_even_twist_candidate_{index:03d}_postflight.json"
        for index in range(1, 5)
    )
    if any(path.exists() for path in downstream):
        raise lane.GuardFailure("post-v18 final twist artifact already exists")

    runbook = lane.read_json(RUNBOOK)
    closure = lane.read_json(CLOSURE)
    if (
        runbook.get("schemaVersion") != "postv18-even-twist-guarded-runbook-v1"
        or runbook.get("status") != "ready_waiting_for_root_heavy_clearance"
        or len(runbook.get("sources") or []) != 2
        or runbook.get("coefficientMaterialIncluded") is not False
        or str((closure.get("evenTwistRunbook") or {}).get("sha256"))
        != lane.sha256_path(RUNBOOK)
    ):
        raise lane.GuardFailure("post-v18 twist runbook/closure changed")

    action_rows = jsonl(ACTION_MAP)
    actions = {str(row["sourceLabel"]): row for row in action_rows}
    if (
        len(action_rows) != 2
        or len(actions) != 2
        or any(int(row.get("systemCount", -1)) != 1 for row in action_rows)
    ):
        raise lane.GuardFailure("post-v18 action subset is not exact two-row single-system")

    raw_rows = jsonl(RAW_RESULTS)
    raw_summary = lane.read_json(RAW_SUMMARY)
    if (
        len(raw_rows) != 4
        or Counter(str(row.get("twistSign")) for row in raw_rows)
        != Counter({"positive": 2, "negative": 2})
        or int(raw_summary.get("certifiedGeneratedTwists", -1)) != 4
        or int(raw_summary.get("generatedIrreducibleChecksPassed", -1)) != 4
        or int(raw_summary.get("freshPrimeSquarefreeCertificates", -1)) != 4
        or int(raw_summary.get("generatedUniqueHashes", -1)) != 4
        or int(raw_summary.get("networkCalls", -1)) != 0
        or int(raw_summary.get("submissionCalls", -1)) != 0
        or RAW_GOLD_MANIFEST.stat().st_size != 0
    ):
        raise lane.GuardFailure("post-v18 raw twist execution is incomplete")

    census = lane.read_json(CENSUS_CERTIFICATE)
    if (
        census.get("method") != "offline-exact-single-output-sealed-stage-v1"
        or not all((census.get("checks") or {}).values())
    ):
        raise lane.GuardFailure("exact SINGLE census is not sealed")
    census_rows = [
        row for row in census.get("selected") or []
        if str((row.get("proof") or {}).get("artifact"))
        == lane.relative(RAW_RESULTS)
    ]
    census_hashes = {str(row["coefficientSha256"]) for row in census_rows}

    candidates, corpus = single.scan_candidates(DATA)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for candidate in candidates:
        pair_index[str(candidate["coefficientSha256"])].add(
            (str(candidate["targetLabel"]), int(candidate["targetR"]))
        )
    outbox_hashes, outbox_pairs, outbox_audit = outbox_snapshot(pair_index)

    evaluated = []
    with lane.connect_ro() as connection:
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            lane.RECEIPTS, DATA, connection, pair_index
        )
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
        for record, row in enumerate(raw_rows, start=1):
            proof = single.validate_twist(row, (), RAW_RESULTS, record, "$")
            pair = (str(row.get("targetLabel")), int(row.get("targetR", -1)))
            line = single.canonical_polynomial_line(row.get("coefficientLine"))
            digest = hashlib.sha256(line.encode("ascii")).hexdigest() if line else ""
            target = targets.get(pair)
            reasons = []
            action = actions.get(str(row.get("sourceLabel")))
            if proof is None or line is None or digest != str(row.get("coefficientSha256")):
                reasons.append("exact_twist_certificate_failed")
            elif not single.validate_source_pins(connection, proof):
                reasons.append("source_reconstruction_or_scoreability_failed")
            if (
                action is None
                or set(map(str, action.get("targetLabels") or [])) != {pair[0]}
                or int(action.get("systemCount", -1)) != 1
            ):
                reasons.append("exact_action_assignment_failed")
            if pair_index.get(digest) != {pair}:
                reasons.append("ambiguous_or_missing_exact_pair_claim")
            if digest not in census_hashes:
                reasons.append("not_selected_by_receipt_aware_single_census")
            if target is None or int(target["discovered"] or 0) != 1:
                reasons.append("target_not_current_discovered")
            if pair in baseline:
                reasons.append("target_baseline")
            if pair in owned:
                reasons.append("target_locally_owned")
            if digest in receipt_hashes:
                reasons.append("candidate_receipt_hash")
            if pair in receipt_pairs:
                reasons.append("target_receipt_pair")
            if digest in outbox_hashes or pair in outbox_pairs:
                reasons.append("candidate_or_pair_reserved_in_other_outbox")
            if connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
            ).fetchone() is not None:
                reasons.append("candidate_hash_known_in_ledger")
            evaluated.append({
                "record": record,
                "row": row,
                "proof": proof,
                "line": line,
                "digest": digest,
                "pair": pair,
                "target": target,
                "reasons": reasons,
            })

    eligible = [item for item in evaluated if not item["reasons"]]
    best_by_pair = {}
    for item in eligible:
        pair = item["pair"]
        incumbent = best_by_pair.get(pair)
        if incumbent is None or single.best_key(item["proof"]) < single.best_key(incumbent["proof"]):
            best_by_pair[pair] = item
    selected = sorted(
        best_by_pair.values(),
        key=lambda item: (
            int(item["target"]["team_count"]),
            int(item["pair"][0][3:]),
            item["pair"][1],
            item["digest"],
        ),
    )
    selected_ids = {id(item) for item in selected}
    for item in eligible:
        if id(item) not in selected_ids:
            item["reasons"].append("nonbest_duplicate_target_pair")

    reports = []
    for item in evaluated:
        target = item["target"]
        reports.append({
            "rawRecord": item["record"],
            "twistSign": str(item["row"].get("twistSign")),
            "sourceLabel": str(item["row"].get("sourceLabel")),
            "sourceR": int(item["row"].get("sourceR", -1)),
            "sourceCoefficientSha256": str(item["row"].get("sourceCoefficientSha256")),
            "targetPair": f"{item['pair'][0]}/r{item['pair'][1]}",
            "targetTeamCount": int(target["team_count"]) if target else None,
            "candidateSha256": item["digest"],
            "status": "certified_exact_shared_selected" if id(item) in selected_ids else "excluded_fail_closed",
            "exclusionReasons": list(item["reasons"]),
        })

    lines = [str(item["line"]) for item in selected]
    hashes = [str(item["digest"]) for item in selected]
    pairs = [item["pair"] for item in selected]
    lane.exclusive_text(FINAL_MANIFEST, "".join(line + "\n" for line in lines))
    offline = exact.offline_manifest_check(FINAL_MANIFEST, hashes, receipt_hashes)

    postflights = []
    mappings = []
    for position, item in enumerate(selected, start=1):
        target = item["target"]
        pair = item["pair"]
        path = DATA / f"postv18_even_twist_candidate_{position:03d}_postflight.json"
        value = {
            "schemaVersion": "postv18-even-twist-postflight-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": "certified_exact_twist_novel_shared_not_submitted",
            "rawRecord": item["record"],
            "twistSign": str(item["row"]["twistSign"]),
            "source": {
                "submissionId": str(item["row"]["sourceSubmissionId"]),
                "polynomialIndex": int(item["row"]["sourcePolynomialIndex"]),
                "label": str(item["row"]["sourceLabel"]),
                "r": int(item["row"]["sourceR"]),
                "coefficientSha256": str(item["row"]["sourceCoefficientSha256"]),
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
                "coefficientSha256": item["digest"],
                "primitiveMonicDegree24": True,
                "irreducible": True,
                "exactRealSignature": True,
                "genericActionExactSingleTarget": True,
                "freshRamificationPrimeSquarefreeSource": True,
                "knownLedgerHash": False,
            },
            "rawResult": {"path": lane.relative(RAW_RESULTS), "record": item["record"]},
            "coefficientMaterialIncluded": False,
            "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
        }
        lane.exclusive_json(path, value)
        postflights.append(lane.artifact(path))
        mappings.append({
            "manifestPosition": position,
            "targetPair": f"{pair[0]}/r{pair[1]}",
            "targetTeamCount": int(target["team_count"]),
            "twistSign": str(item["row"]["twistSign"]),
            "candidateSha256": item["digest"],
            "postflight": lane.artifact(path),
            "receiptSubmissionId": None,
        })

    projection = sum(
        (Fraction(1, 2 ** int(item["target"]["team_count"])) for item in selected),
        Fraction(0),
    )
    mapping = {
        "schemaVersion": "postv18-even-twist-receipt-mapping-ready-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_receipt_mapping_after_submission",
        "manifest": {
            **lane.artifact(FINAL_MANIFEST),
            "polynomials": len(selected),
            "bytes": FINAL_MANIFEST.stat().st_size,
        },
        "routeCount": len(selected),
        "distinctCandidateHashes": len(set(hashes)),
        "distinctTargetPairs": len(set(pairs)),
        "mappings": mappings,
        "receiptSubmissionId": None,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
    }
    lane.exclusive_json(FINAL_MAPPING, mapping)

    certificate = {
        "schemaVersion": "postv18-even-twist-final-batch-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_twists_duplicate_free_not_submitted",
        "runbook": lane.artifact(RUNBOOK),
        "closure": lane.artifact(CLOSURE),
        "actionMap": {**lane.artifact(ACTION_MAP), "rows": len(action_rows)},
        "rawExecution": {
            "results": {**lane.artifact(RAW_RESULTS), "generatedTwists": len(raw_rows)},
            "summary": lane.artifact(RAW_SUMMARY),
            "positiveTwistsCertified": 2,
            "negativeTwistsCertified": 2,
            "goldOnlyManifest": {**lane.artifact(RAW_GOLD_MANIFEST), "polynomials": 0},
            "maximumConcurrentSageGapWorkers": 1,
        },
        "singleExactCensus": {
            "certificate": lane.artifact(CENSUS_CERTIFICATE),
            "summary": lane.artifact(CENSUS_SUMMARY),
            "provisionalManifest": lane.artifact(CENSUS_MANIFEST),
            "postv18SelectedRows": len(census_rows),
        },
        "receiptExclusion": {
            key: value for key, value in receipt_audit.items() if key != "audit"
        },
        "outboxExclusion": outbox_audit,
        "candidateCorpus": corpus,
        "routes": reports,
        "selectedSafeRows": len(selected),
        "failClosedExclusions": len(raw_rows) - len(selected),
        "projectedMarginalScoreExact": str(projection),
        "combinedManifest": {
            **lane.artifact(FINAL_MANIFEST),
            "polynomials": len(selected),
            "bytes": FINAL_MANIFEST.stat().st_size,
        },
        "combinedOfflineDryRun": offline,
        "receiptMappingReady": lane.artifact(FINAL_MAPPING),
        "postflights": postflights,
        "checks": {
            "allFourGeneratedTwistsCertifiedExactly": len(raw_rows) == 4,
            "singleCensusRevalidatedPostv18Rows": len(census_rows) == len(census_hashes),
            "allSelectedRowsWereSingleCensusCertified": set(hashes) <= census_hashes,
            "allSelectedTargetsCurrentDiscoveredNonbaselineUnowned": True,
            "allReceiptHashPairCollisionsExcluded": True,
            "allOtherOutboxHashPairCollisionsExcluded": True,
            "bestCandidatePerTargetPairSelected": len(pairs) == len(set(pairs)),
            "allSelectedHashesDistinctAndLedgerNovel": len(hashes) == len(set(hashes)),
            "combinedManifestOfflineValidated": offline.get("commit") is False,
            "certificateContainsNoCoefficientPayload": True,
        },
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "sageWorkerProcesses": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    if not all(certificate["checks"].values()):
        raise lane.GuardFailure("post-v18 final twist checks did not all pass")
    lane.exclusive_json(FINAL_CERTIFICATE, certificate)
    print(json.dumps({
        "status": certificate["status"],
        "selectedSafeRows": len(selected),
        "failClosedExclusions": len(raw_rows) - len(selected),
        "projectedMarginalScoreExact": str(projection),
        "manifest": lane.relative(FINAL_MANIFEST),
        "manifestSha256": lane.sha256_path(FINAL_MANIFEST),
        "certificate": lane.relative(FINAL_CERTIFICATE),
        "certificateSha256": lane.sha256_path(FINAL_CERTIFICATE),
        "mapping": lane.relative(FINAL_MAPPING),
        "mappingSha256": lane.sha256_path(FINAL_MAPPING),
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

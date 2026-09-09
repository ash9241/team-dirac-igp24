#!/usr/bin/env python3
"""Seal coefficient-free receipt lineage for all fourteen tc2 runbooks."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import run_low_contention_sequential as lane
import sair_api
import stage_single_exact_census as exact_census


DATA = lane.DATA
ROUTE_CERTIFICATE = DATA / "low_contention_tc2_routes_certificate.json"
CERTIFICATE = DATA / "low_contention_tc2_frontier_receipt_mapping.json"
SUMMARY = DATA / "low_contention_tc2_frontier_receipt_mapping_summary.json"
FIRST_RECEIPT = "sub_b80910ae012d49cf9b5dcfa4d00b6f51"
TAIL_RECEIPT = "sub_574f9e56ce804797ab49160e2f9838e6"
RECEIPT_IDS = (FIRST_RECEIPT, TAIL_RECEIPT)
COEFFICIENT_PAYLOAD_RE = re.compile(
    r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])"
)


def load_receipts() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    receipts = {}
    by_hash = defaultdict(list)
    for submission_id in RECEIPT_IDS:
        path = lane.RECEIPTS / f"{submission_id}.json"
        value = lane.read_json(path)
        response = value.get("response") or {}
        manifest = Path(str(value.get("manifest") or "")).expanduser().resolve()
        if (
            value.get("commit") is not True
            or int(value.get("knownLocalHashes", -1)) != 0
            or str(response.get("submissionId")) != submission_id
            or not manifest.is_file()
            or lane.sha256_path(manifest) != str(value.get("manifestHash"))
        ):
            raise lane.GuardFailure(f"tc2 receipt is absent/nonintact: {submission_id}")
        lines, hashes = sair_api.validated_manifest(manifest)
        if len(lines) != int(value.get("polynomials", -1)) or len(hashes) != len(lines):
            raise lane.GuardFailure(f"tc2 receipt count mismatch: {submission_id}")
        fact = {
            "submissionId": submission_id,
            "receipt": lane.artifact(path),
            "manifest": {
                **lane.artifact(manifest),
                "polynomials": len(lines),
                "bytes": manifest.stat().st_size,
            },
            "knownLocalHashesAtCommit": 0,
            "submissionStatusAtCommit": str(response.get("submissionStatus")),
            "queuedCountAtCommit": int(response.get("queuedCount") or 0),
            "rejectedCountAtCommit": int(response.get("rejectedCount") or 0),
        }
        receipts[submission_id] = fact
        for digest in hashes:
            by_hash[digest].append(fact)
    return receipts, by_hash


def main() -> int:
    if CERTIFICATE.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite tc2 frontier seal")
    route_certificate = lane.read_json(ROUTE_CERTIFICATE)
    if (
        route_certificate.get("scope") != "deterministic_tc2_fresh_untested_anchors"
        or len(route_certificate.get("runbooks") or []) != 14
    ):
        raise lane.GuardFailure("tc2 route frontier is not the sealed fourteen-runbook audit")
    receipts, by_hash = load_receipts()
    mappings = []
    for index, route in enumerate(route_certificate["runbooks"], start=1):
        route_id = str(route["routeId"])
        expected_receipt = FIRST_RECEIPT if index == 1 else TAIL_RECEIPT
        paths = lane.planned_paths(route)
        row = lane.one_jsonl(paths["result"])
        line = exact_census.canonical_polynomial_line(row.get("coefficientLine"))
        if line is None:
            raise lane.GuardFailure(f"tc2 result is noncanonical: {route_id}")
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if (
            row.get("status") != "certified"
            or digest != str(row.get("coefficientSha256"))
            or str(row.get("targetLabel")) != str(route["target"]["label"])
            or int(row.get("targetR", -1)) != int(route["target"]["r"])
        ):
            raise lane.GuardFailure(f"tc2 exact result changed: {route_id}")
        matches = by_hash.get(digest) or []
        if len(matches) != 1 or matches[0]["submissionId"] != expected_receipt:
            raise lane.GuardFailure(f"tc2 receipt mapping absent/nonunique/wrong: {route_id}")
        postflight = lane.read_json(paths["postflight"])
        if (
            postflight.get("status") != "certified_exact_novel_live_not_staged_not_submitted"
            or int((postflight.get("target") or {}).get("teamCount", -1)) != 2
            or str((postflight.get("candidate") or {}).get("coefficientSha256")) != digest
        ):
            raise lane.GuardFailure(f"tc2 postflight changed: {route_id}")
        mappings.append({
            "routeId": route_id,
            "sourcePair": f"{route['source']['label']}/r{route['source']['r']}",
            "targetPair": f"{route['target']['label']}/r{route['target']['r']}",
            "candidateSha256": digest,
            "result": lane.artifact(paths["result"]),
            "postflight": lane.artifact(paths["postflight"]),
            "receiptSubmissionId": expected_receipt,
            "receiptManifestSha256": matches[0]["manifest"]["sha256"],
            "knownLocalHashesAtCommit": 0,
        })
    if (
        len(mappings) != 14
        or len({row["candidateSha256"] for row in mappings}) != 14
        or len({row["targetPair"] for row in mappings}) != 14
    ):
        raise lane.GuardFailure("tc2 frontier is not fourteen distinct exact target candidates")
    grouped = defaultdict(list)
    for row in mappings:
        grouped[row["receiptSubmissionId"]].append(row["routeId"])
    if set(grouped) != set(RECEIPT_IDS) or len(grouped[FIRST_RECEIPT]) != 1 or len(grouped[TAIL_RECEIPT]) != 13:
        raise lane.GuardFailure("tc2 receipt batch split is not 1+13")

    certificate = {
        "schemaVersion": "low-contention-deterministic-tc2-frontier-seal-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "sealed_14_of_14_exact_candidates_receipt_mapped",
        "routeCertificate": lane.artifact(ROUTE_CERTIFICATE),
        "routeCount": 14,
        "distinctCandidateHashes": 14,
        "distinctTargetPairs": 14,
        "receiptCount": 2,
        "receiptBatchSplit": {"1": 1, "13": 1},
        "receipts": [
            {**receipts[submission_id], "mappedRouteIds": sorted(grouped[submission_id])}
            for submission_id in RECEIPT_IDS
        ],
        "mappings": mappings,
        "checks": {
            "allRunbooksMappedExactlyOnce": True,
            "allCandidateHashesDistinct": True,
            "allTargetPairsDistinct": True,
            "allReceiptManifestsIntact": True,
            "allCommitDryRunsHadZeroKnownLocalHashes": True,
            "allExactTc2PostflightsPinned": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0,
            "sageRuns": 0, "gapRuns": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if COEFFICIENT_PAYLOAD_RE.search(rendered):
        raise lane.GuardFailure("coefficient payload entered tc2 frontier seal")
    lane.exclusive_text(CERTIFICATE, rendered)
    summary = {
        "schemaVersion": "low-contention-deterministic-tc2-frontier-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "routeCount": 14,
        "receiptCount": 2,
        "receiptBatchSplit": "1+13",
        "mappedExactlyOnce": True,
        "coefficientMaterialIncluded": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    lane.exclusive_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        lane.GuardFailure, FileExistsError, ValueError, OSError, json.JSONDecodeError
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

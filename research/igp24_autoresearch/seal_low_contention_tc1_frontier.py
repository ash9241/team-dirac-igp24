#!/usr/bin/env python3
"""Seal coefficient-free receipt lineage for all twelve deterministic tc1 routes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import run_low_contention_sequential as lane
import sair_api
import stage_single_exact_census as exact_census


ROOT = lane.ROOT
DATA = lane.DATA
CERTIFICATE = DATA / "low_contention_tc1_frontier_receipt_mapping.json"
SUMMARY = DATA / "low_contention_tc1_frontier_receipt_mapping_summary.json"
RECEIPT_IDS = (
    "sub_218295e3a25d413c89fd073588dfe189",
    "sub_67ba82cbecda4192860fea618b883b48",
    "sub_33df887c53e84581888098e85f3d2384",
    "sub_78f8e9ac61274aa8a3229c7f3642f6b3",
    "sub_ab83eb7c71224c26aef46fffee7d51f1",
    "sub_fdaf614deca84c3fb06e00672771f107",
    "sub_78ebbd552555416598881bd5896559ff",
)
EXPECTED_ROUTE_RECEIPTS = {
    "lc01_24T17167_r18_to_24T17216_r12": RECEIPT_IDS[0],
    "lc02_24T10676_r16_to_24T10512_r16": RECEIPT_IDS[1],
    "lc12_24T8316_r18_to_24T9237_r12": RECEIPT_IDS[2],
    "lc04_24T17165_r18_to_24T17148_r12": RECEIPT_IDS[3],
    "lc08_24T15226_r24_to_24T15065_r24": RECEIPT_IDS[4],
    "lc10_24T13539_r20_to_24T13182_r20": RECEIPT_IDS[5],
    "lc07_24T18612_r14_to_24T18869_r8": RECEIPT_IDS[6],
    "lc11_24T14132_r24_to_24T14327_r24": RECEIPT_IDS[6],
    "lc03_24T17157_r18_to_24T17207_r12": RECEIPT_IDS[6],
    "lc06_24T10443_r24_to_24T10829_r24": RECEIPT_IDS[6],
    "lc05_24T5424_r24_to_24T5432_r24": RECEIPT_IDS[6],
    "lc09_24T13861_r24_to_24T13188_r24": RECEIPT_IDS[6],
}
COEFFICIENT_PAYLOAD_RE = re.compile(
    r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])"
)


def load_receipts() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    by_hash: dict[str, list[dict]] = defaultdict(list)
    receipts = {}
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
            raise lane.GuardFailure(f"receipt is absent/noncommit/nonintact: {submission_id}")
        lines, hashes = sair_api.validated_manifest(manifest)
        if len(lines) != int(value.get("polynomials", -1)) or len(hashes) != len(lines):
            raise lane.GuardFailure(f"receipt manifest count mismatch: {submission_id}")
        receipt_fact = {
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
        receipts[submission_id] = receipt_fact
        for digest in hashes:
            by_hash[digest].append(receipt_fact)
    return by_hash, receipts


def ledger_fact(connection: sqlite3.Connection, digest: str) -> dict:
    rows = connection.execute(
        "SELECT v.status,v.scoreable,v.label,v.r,v.scoring_status "
        "FROM polynomials p LEFT JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.coefficient_hash=?",
        (digest,),
    ).fetchall()
    if not rows:
        return {"present": False, "verified": False}
    verified = [row for row in rows if row["label"] is not None]
    return {
        "present": True,
        "verified": bool(verified),
        "statuses": sorted({str(row["status"]) for row in verified}),
        "scoreable": any(int(row["scoreable"] or 0) == 1 for row in verified),
    }


def main() -> int:
    if CERTIFICATE.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite tc1 frontier seal")
    route_certificate = lane.read_json(lane.DEFAULT_CERTIFICATE)
    routes = lane.route_map(route_certificate)
    if set(routes) != set(EXPECTED_ROUTE_RECEIPTS):
        raise lane.GuardFailure("sealed tc1 runbook set is not exactly lc01-lc12")
    by_hash, receipts = load_receipts()

    connection = sqlite3.connect(f"file:{lane.DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    mappings = []
    try:
        for route_id, route in routes.items():
            result_path = lane.planned_paths(route)["result"]
            row = lane.one_jsonl(result_path)
            line = exact_census.canonical_polynomial_line(row.get("coefficientLine"))
            if line is None:
                raise lane.GuardFailure(f"noncanonical exact result: {route_id}")
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            target = route["target"]
            if (
                row.get("status") != "certified"
                or digest != str(row.get("coefficientSha256"))
                or str(row.get("targetLabel")) != str(target["label"])
                or int(row.get("targetR", -1)) != int(target["r"])
            ):
                raise lane.GuardFailure(f"exact result changed: {route_id}")
            matches = by_hash.get(digest) or []
            if len(matches) != 1:
                raise lane.GuardFailure(f"candidate receipt mapping is absent/nonunique: {route_id}")
            receipt = matches[0]
            expected_receipt = EXPECTED_ROUTE_RECEIPTS[route_id]
            if receipt["submissionId"] != expected_receipt:
                raise lane.GuardFailure(f"candidate mapped to the wrong receipt: {route_id}")
            postflight = lane.planned_paths(route)["postflight"]
            if not postflight.is_file():
                raise lane.GuardFailure(f"postflight is absent: {route_id}")
            postflight_value = lane.read_json(postflight)
            if (
                postflight_value.get("status")
                != "certified_exact_novel_live_not_staged_not_submitted"
                or str((postflight_value.get("candidate") or {}).get("coefficientSha256"))
                != digest
            ):
                raise lane.GuardFailure(f"postflight changed: {route_id}")
            mappings.append({
                "routeId": route_id,
                "sourcePair": f"{route['source']['label']}/r{route['source']['r']}",
                "targetPair": f"{target['label']}/r{target['r']}",
                "candidateSha256": digest,
                "result": lane.artifact(result_path),
                "postflight": lane.artifact(postflight),
                "receiptSubmissionId": receipt["submissionId"],
                "receiptManifestSha256": receipt["manifest"]["sha256"],
                "knownLocalHashesAtCommit": 0,
                "ledgerAtSeal": ledger_fact(connection, digest),
            })
    finally:
        connection.close()

    if len(mappings) != 12 or len({row["candidateSha256"] for row in mappings}) != 12:
        raise lane.GuardFailure("tc1 frontier does not contain twelve distinct exact candidates")
    mapped_by_receipt = defaultdict(list)
    for row in mappings:
        mapped_by_receipt[row["receiptSubmissionId"]].append(row["routeId"])
    if set(mapped_by_receipt) != set(RECEIPT_IDS):
        raise lane.GuardFailure("not every sealed receipt is used by the tc1 frontier")

    certificate = {
        "schemaVersion": "low-contention-deterministic-tc1-frontier-seal-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "sealed_12_of_12_exact_candidates_receipt_mapped",
        "routeCertificate": lane.artifact(lane.DEFAULT_CERTIFICATE),
        "routeCount": 12,
        "distinctCandidateHashes": 12,
        "distinctTargetPairs": len({row["targetPair"] for row in mappings}),
        "receiptCount": len(receipts),
        "receipts": [
            {
                **receipts[submission_id],
                "mappedRouteIds": sorted(mapped_by_receipt[submission_id]),
            }
            for submission_id in RECEIPT_IDS
        ],
        "mappings": sorted(mappings, key=lambda row: row["routeId"]),
        "checks": {
            "allRunbooksMappedExactlyOnce": True,
            "allCandidateHashesDistinct": True,
            "allReceiptManifestsIntact": True,
            "allCommitDryRunsHadZeroKnownLocalHashes": True,
            "allExactPostflightsPinned": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "sageRuns": 0,
            "gapRuns": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if COEFFICIENT_PAYLOAD_RE.search(rendered):
        raise lane.GuardFailure("coefficient payload entered the tc1 frontier seal")
    lane.exclusive_text(CERTIFICATE, rendered)
    summary = {
        "schemaVersion": "low-contention-deterministic-tc1-frontier-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "routeCount": 12,
        "receiptCount": len(receipts),
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
        lane.GuardFailure,
        FileExistsError,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

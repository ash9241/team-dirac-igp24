#!/usr/bin/env python3
"""Filter the staged tc5/tc6 batch against the sealed global exact delta."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import run_low_contention_remaining_batch as exact
import run_low_contention_sequential as lane
import run_low_contention_tc5_tc6_batch as batch
import sair_api


DATA = lane.DATA
OUTBOX = lane.OUTBOX
DELTA_CERTIFICATE = DATA / "global_exact_corpus_delta_after_b4f7_20260722_certificate.json"
DELTA_MANIFEST = OUTBOX / "global_exact_corpus_delta_after_b4f7_20260722.txt"
DELTA_CERTIFICATE_SHA256 = "2246a750c3124c821f2a7609afb7174933dff7fa303dcbc57442db40434756b0"
DELTA_MANIFEST_SHA256 = "b87e3332560a268ceecb586c7b464296f9e431a77cbac7da145e70d125a2eb4d"
FINAL_MANIFEST = OUTBOX / "low_contention_tc5_tc6_final_after_global_delta_20260722.txt"
FINAL_MAPPING = DATA / "low_contention_tc5_tc6_final_receipt_mapping_ready.json"
FINAL_CERTIFICATE = DATA / "low_contention_tc5_tc6_final_after_global_delta_certificate.json"


def main() -> int:
    if FINAL_MANIFEST.exists() or FINAL_MAPPING.exists() or FINAL_CERTIFICATE.exists():
        raise lane.GuardFailure("final global-delta-filtered artifact already exists")
    if (
        lane.sha256_path(DELTA_CERTIFICATE) != DELTA_CERTIFICATE_SHA256
        or lane.sha256_path(DELTA_MANIFEST) != DELTA_MANIFEST_SHA256
    ):
        raise lane.GuardFailure("global exact-delta artifact hash changed")
    delta = lane.read_json(DELTA_CERTIFICATE)
    selected = delta.get("selected") or []
    reserved_pairs = {str(row.get("pair")) for row in selected}
    reserved_hashes = {str(row.get("coefficientSha256")) for row in selected}
    _delta_lines, delta_hashes = sair_api.validated_manifest(DELTA_MANIFEST)
    if (
        len(selected) != 3
        or len(reserved_pairs) != 3
        or len(reserved_hashes) != 3
        or reserved_hashes != set(delta_hashes)
        or not all((delta.get("checks") or {}).values())
        or int((delta.get("manifest") or {}).get("polynomials", -1)) != 3
        or str((delta.get("manifest") or {}).get("sha256"))
        != DELTA_MANIFEST_SHA256
    ):
        raise lane.GuardFailure("global exact-delta certificate is not sealed exact-3")

    staged = lane.read_json(batch.BATCH_CERTIFICATE)
    mapping = lane.read_json(batch.MAPPING_READY)
    staged_rows = mapping.get("mappings") or []
    _staged_lines, staged_hashes = sair_api.validated_manifest(batch.BATCH_MANIFEST)
    if (
        staged.get("status")
        != "certified_all_final_tc5_tc6_staged_offline_not_submitted"
        or int(staged.get("successes", -1)) != 14
        or int(staged.get("failClosedSkips", -1)) != 0
        or mapping.get("status") != "ready_for_receipt_mapping_after_submission"
        or len(staged_rows) != 14
        or staged_hashes != [str(row["candidateSha256"]) for row in staged_rows]
    ):
        raise lane.GuardFailure("staged tc5/tc6 batch is absent or changed")

    pair_overlap = sorted(
        {str(row["targetPair"]) for row in staged_rows} & reserved_pairs
    )
    hash_overlap = sorted(set(staged_hashes) & reserved_hashes)
    included = [
        row for row in staged_rows
        if str(row["targetPair"]) not in reserved_pairs
        and str(row["candidateSha256"]) not in reserved_hashes
    ]
    excluded = [
        row for row in staged_rows
        if str(row["targetPair"]) in reserved_pairs
        or str(row["candidateSha256"]) in reserved_hashes
    ]
    line_by_hash = dict(zip(staged_hashes, _staged_lines, strict=True))
    included_hashes = [str(row["candidateSha256"]) for row in included]
    included_lines = [line_by_hash[digest] for digest in included_hashes]
    if (
        len(included) + len(excluded) != 14
        or len(included_hashes) != len(set(included_hashes))
        or any(str(row["targetPair"]) in reserved_pairs for row in included)
        or any(digest in reserved_hashes for digest in included_hashes)
    ):
        raise lane.GuardFailure("global exact-delta filtering did not partition safely")

    lane.exclusive_text(FINAL_MANIFEST, "\n".join(included_lines) + "\n")
    with lane.connect_ro() as connection:
        receipt_hashes, receipt_pairs, receipt_audit = lane.receipt_snapshot(connection)
    offline = exact.offline_manifest_check(
        FINAL_MANIFEST, included_hashes, receipt_hashes
    )
    if any(
        batch.parse_target_pair(row["targetPair"]) in receipt_pairs for row in included
    ):
        raise lane.GuardFailure("filtered final manifest contains a receipt-covered pair")

    final_mappings = [
        {
            **row,
            "manifestPosition": index,
            "receiptSubmissionId": None,
        }
        for index, row in enumerate(included, start=1)
    ]
    final_mapping = {
        "schemaVersion": "low-contention-tc5-tc6-final-receipt-mapping-ready-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_receipt_mapping_after_global_exact_delta_filter",
        "sourceMapping": lane.artifact(batch.MAPPING_READY),
        "globalExactDelta": lane.artifact(DELTA_CERTIFICATE),
        "combinedManifest": {
            **lane.artifact(FINAL_MANIFEST),
            "bytes": FINAL_MANIFEST.stat().st_size,
            "polynomials": len(included),
        },
        "routeCount": len(included),
        "teamCountDistribution": {
            "5": sum(int(row["teamCount"]) == 5 for row in included),
            "6": sum(int(row["teamCount"]) == 6 for row in included),
        },
        "distinctCandidateHashes": len(set(included_hashes)),
        "distinctTargetPairs": len({str(row["targetPair"]) for row in included}),
        "mappings": final_mappings,
        "receiptSubmissionId": None,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    lane.exclusive_json(FINAL_MAPPING, final_mapping)
    certificate = {
        "schemaVersion": "low-contention-tc5-tc6-global-exact-delta-filter-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_global_exact_delta_filtered_duplicate_free_not_submitted",
        "stagedBatchCertificate": lane.artifact(batch.BATCH_CERTIFICATE),
        "stagedMapping": lane.artifact(batch.MAPPING_READY),
        "stagedManifest": lane.artifact(batch.BATCH_MANIFEST),
        "globalExactDeltaCertificate": lane.artifact(DELTA_CERTIFICATE),
        "globalExactDeltaManifest": lane.artifact(DELTA_MANIFEST),
        "globalExactDeltaReservedPairs": sorted(reserved_pairs),
        "globalExactDeltaReservedHashes": sorted(reserved_hashes),
        "pairOverlap": pair_overlap,
        "hashOverlap": hash_overlap,
        "originalRows": 14,
        "excludedRows": len(excluded),
        "excludedRouteIds": [str(row["routeId"]) for row in excluded],
        "finalRows": len(included),
        "finalTeamCountDistribution": final_mapping["teamCountDistribution"],
        "finalManifest": final_mapping["combinedManifest"],
        "finalMapping": lane.artifact(FINAL_MAPPING),
        "receiptExclusion": receipt_audit,
        "offlineDryRun": offline,
        "checks": {
            "globalExactDeltaArtifactHashesPinned": True,
            "globalExactDeltaContainsThreeExactDistinctRows": True,
            "allGlobalExactDeltaChecksPassed": True,
            "stagedTc5Tc6BatchContainedFourteenExactRows": True,
            "allPairOverlapsExcluded": not ({str(row["targetPair"]) for row in included} & reserved_pairs),
            "allHashOverlapsExcluded": not (set(included_hashes) & reserved_hashes),
            "finalCandidateHashesDistinct": len(included_hashes) == len(set(included_hashes)),
            "finalTargetPairsDistinct": len(included) == len({str(row["targetPair"]) for row in included}),
            "finalPairsReceiptSafeAtFilter": True,
            "finalManifestOfflineValidated": offline.get("commit") is False,
        },
        "originalCombinedManifestSupersededForSubmission": True,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0, "sageRuns": 0, "gapRuns": 0},
    }
    if not all(certificate["checks"].values()):
        raise lane.GuardFailure("final duplicate-free checks did not all pass")
    lane.exclusive_json(FINAL_CERTIFICATE, certificate)
    print(json.dumps({
        "status": certificate["status"],
        "originalRows": 14,
        "excludedRows": len(excluded),
        "pairOverlaps": len(pair_overlap),
        "hashOverlaps": len(hash_overlap),
        "finalRows": len(included),
        "finalManifest": lane.relative(FINAL_MANIFEST),
        "finalManifestSha256": lane.sha256_path(FINAL_MANIFEST),
        "finalMapping": lane.relative(FINAL_MAPPING),
        "finalCertificate": lane.relative(FINAL_CERTIFICATE),
        "networkCalls": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (lane.GuardFailure, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

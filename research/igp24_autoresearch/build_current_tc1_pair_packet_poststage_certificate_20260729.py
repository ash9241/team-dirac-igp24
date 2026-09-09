#!/usr/bin/env python3
"""Certify the intentionally staged 17-row current-tc1 pair manifest.

The historical pre-stage certificate remains immutable.  This light-only
post-stage check proves that the one current manifest is exactly the union of
the six packet factors minus the single already accepted 24T5424/r24 factor,
and rechecks ledger, receipt, and live-target guards.  It never submits.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import stage_single_exact_census as exact
from build_current_tc1_pair_packet_certificate_20260729 import (
    ACTION_MAP,
    DATA,
    DB,
    KNOWN_ASSIGNMENTS,
    PACKETS,
    RECEIPTS,
    ROOT,
    action_index,
    artifact,
    fraction_text,
    packet_precheck,
    sha256_path,
    target_state,
    write_new,
)


MANIFEST = ROOT / "outbox" / "current_tc1_pair_packet_full17_20260729.txt"
PRESTAGE_CERTIFICATE = DATA / "current_tc1_pair_packets_certificate_20260729.json"
POSTSTAGE_CERTIFICATE = (
    DATA / "current_tc1_pair_packets_poststage_certificate_20260729.json"
)
EXPECTED_MANIFEST_SHA256 = (
    "400f59ec2d12bf5a62d1e4dac11964de5aca9d5e843fd344feffcd36549fe0be"
)
EXPECTED_PRESTAGE_SHA256 = (
    "80721637c55807a2955b7b126dff788263629e6b5965ca88f6a57c5ebd8d1bc4"
)


def canonical_manifest_lines(path: Path) -> list[str]:
    lines = []
    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        line = exact.canonical_polynomial_line(raw.strip())
        if line is None or line != raw.strip():
            raise ValueError(f"noncanonical manifest row at {path}:{number}")
        lines.append(line)
    if len(lines) != len(set(lines)):
        raise ValueError("manifest contains duplicate polynomial rows")
    return lines


def main() -> int:
    if (
        not PRESTAGE_CERTIFICATE.is_file()
        or sha256_path(PRESTAGE_CERTIFICATE) != EXPECTED_PRESTAGE_SHA256
    ):
        raise ValueError("historical pre-stage certificate is missing or changed")
    if (
        not MANIFEST.is_file()
        or sha256_path(MANIFEST) != EXPECTED_MANIFEST_SHA256
        or MANIFEST.stat().st_size != 3651
    ):
        raise ValueError("staged manifest is missing or changed")

    actions = action_index()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        checked = [packet_precheck(connection, actions, spec) for spec in PACKETS]
        fresh_packet_rows = [
            (packet, candidate)
            for packet in checked
            for candidate in packet["candidateRows"]
            if candidate["knownAssignment"] is None
        ]
        known_packet_rows = [
            (packet, candidate)
            for packet in checked
            for candidate in packet["candidateRows"]
            if candidate["knownAssignment"] is not None
        ]
        if len(fresh_packet_rows) != 17 or len(known_packet_rows) != 1:
            raise ValueError("packet freshness partition changed")

        expected_lines = []
        expected_hashes = []
        for packet in checked:
            raw_candidates = packet["packet"]["candidates"]
            for candidate, public in zip(
                raw_candidates, packet["candidateRows"], strict=True
            ):
                if public["knownAssignment"] is None:
                    expected_lines.append(str(candidate["coefficientLine"]))
                    expected_hashes.append(str(candidate["coefficientSha256"]))
        actual_lines = canonical_manifest_lines(MANIFEST)
        actual_hashes = [
            hashlib.sha256(line.encode("ascii")).hexdigest()
            for line in actual_lines
        ]
        if (
            len(actual_lines) != 17
            or actual_lines != expected_lines
            or actual_hashes != expected_hashes
            or set(actual_hashes) & set(KNOWN_ASSIGNMENTS)
        ):
            raise ValueError(
                "manifest is not the ordered 17-row fresh packet union"
            )

        # Every staged packet hash must occur once and only once among all txt
        # outboxes, specifically in this pinned manifest.
        occurrences: dict[str, list[tuple[str, int]]] = {
            digest: [] for digest in expected_hashes
        }
        for path in sorted((ROOT / "outbox").glob("*.txt")):
            for number, line in enumerate(canonical_manifest_lines(path), start=1):
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest in occurrences:
                    occurrences[digest].append(
                        (str(path.resolve().relative_to(ROOT)), number)
                    )
        expected_locations = [
            (str(MANIFEST.resolve().relative_to(ROOT)), index)
            for index in range(1, 18)
        ]
        actual_locations = [
            occurrences[digest][0]
            for digest in expected_hashes
            if len(occurrences[digest]) == 1
        ]
        if (
            any(len(value) != 1 for value in occurrences.values())
            or actual_locations != expected_locations
        ):
            raise ValueError("staged packet rows are missing or duplicated in outboxes")

        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials "
                "WHERE coefficient_hash IN ({})".format(
                    ",".join("?" for _ in expected_hashes)
                ),
                tuple(expected_hashes),
            )
        }
        if ledger_hashes:
            raise ValueError("a staged packet hash is now present in the ledger")

        candidate_pair_index: dict[str, set[tuple[str, int]]] = {}
        for packet in checked:
            all_pairs = {
                (str(label), 24) for label in packet["actionCounts"]
            }
            known_pairs = {
                tuple(candidate["knownAssignment"]["pair"])
                for candidate in packet["candidateRows"]
                if candidate["knownAssignment"] is not None
            }
            remaining_pairs = all_pairs - known_pairs
            for candidate in packet["candidateRows"]:
                digest = str(candidate["coefficientSha256"])
                assignment = candidate["knownAssignment"]
                candidate_pair_index[digest] = (
                    {tuple(assignment["pair"])}
                    if assignment is not None
                    else set(remaining_pairs)
                )
        receipt_hashes, receipt_pairs, receipt_meta = exact.receipt_exclusions(
            RECEIPTS, DATA, connection, candidate_pair_index
        )
        if set(expected_hashes) & receipt_hashes:
            raise ValueError("a staged packet hash is already in a receipt")

        packet_summaries = []
        full_score = Fraction(0)
        desired_pairs = set()
        for packet in checked:
            spec = packet["spec"]
            desired_pair = (str(spec["desiredTarget"]), 24)
            desired_pairs.add(desired_pair)
            states = [
                target_state(
                    connection,
                    (str(label), 24),
                    receipt_pairs,
                    set(),
                )
                for label in sorted(packet["actionCounts"])
            ]
            desired = next(
                state
                for state in states
                if state["pair"] == f"{desired_pair[0]}/r24"
            )
            if (
                desired["teamCount"] != 1
                or not desired["discovered"]
                or desired["baseline"]
                or desired["locallyOwned"]
                or desired["receiptOrTxtOutboxExcluded"]
                or desired_pair in receipt_pairs
            ):
                raise ValueError(
                    f"staged desired target is no longer fresh tc1: {desired_pair}"
                )
            packet_score = sum(
                (state["_score"] for state in states), Fraction(0)
            )
            full_score += packet_score
            public_states = []
            for state in states:
                state = dict(state)
                state.pop("_score")
                public_states.append(state)
            packet_summaries.append(
                {
                    "sourcePair": f"{spec['sourceLabel']}/r24",
                    "packetArtifact": artifact(packet["path"]),
                    "desiredCurrentTc1Pair": f"{desired_pair[0]}/r24",
                    "targetLabelMultiset": dict(
                        sorted(packet["actionCounts"].items())
                    ),
                    "stagedFreshFactorIndices": [
                        int(candidate["factorIndex"])
                        for candidate in packet["candidateRows"]
                        if candidate["knownAssignment"] is None
                    ],
                    "targetStatesIgnoringThisPinnedOutbox": public_states,
                    "fullFreshPacketMarginalScoreExact": fraction_text(
                        packet_score
                    ),
                }
            )
        if len(desired_pairs) != 6 or full_score != Fraction(59, 16):
            raise ValueError("post-stage target set or projected score changed")
    finally:
        connection.close()

    created_at = datetime.now(timezone.utc).isoformat()
    if POSTSTAGE_CERTIFICATE.exists():
        existing = json.loads(POSTSTAGE_CERTIFICATE.read_text(encoding="utf-8"))
        if (
            existing.get("schemaVersion")
            != "current-tc1-pair-packets-poststage-certificate-v1"
            or not existing.get("createdAt")
        ):
            raise ValueError("existing post-stage certificate identity changed")
        created_at = str(existing["createdAt"])
    certificate = {
        "schemaVersion": "current-tc1-pair-packets-poststage-certificate-v1",
        "createdAt": created_at,
        "status": "certified_17_fresh_pair_rows_staged_not_submitted",
        "coefficientMaterialIncluded": False,
        "prestageCertificate": artifact(PRESTAGE_CERTIFICATE),
        "actionArtifact": artifact(ACTION_MAP),
        "manifestArtifact": {
            **artifact(MANIFEST),
            "bytes": MANIFEST.stat().st_size,
            "canonicalPolynomialRows": 17,
            "distinctCoefficientHashes": 17,
        },
        "packetCount": 6,
        "packetCandidateRows": 18,
        "alreadyAcceptedPacketRowsExcluded": 1,
        "stagedFreshRows": 17,
        "distinctGuaranteedCurrentTc1Pairs": 6,
        "projectedTc1MarginalScoreExact": "3",
        "fullStagedPacketMarginalScoreExact": "59/16",
        "packets": packet_summaries,
        "knownExcludedRow": {
            "coefficientSha256": next(iter(KNOWN_ASSIGNMENTS)),
            **next(iter(KNOWN_ASSIGNMENTS.values())),
        },
        "proofChecks": {
            "historicalPrestageCertificateHashPinned": True,
            "manifestHashSizeAndRowCountPinned": True,
            "manifestRowsCanonicalDistinctAndOrderPinned": True,
            "manifestEqualsFreshPacketUnionMinusKnownAcceptedRow": True,
            "allManifestRowsOccurExactlyOnceAcrossTxtOutboxes": True,
            "allManifestHashesAbsentFromLedger": True,
            "allManifestHashesAbsentFromReceipts": True,
            "allDesiredPairsCurrentTc1NonbaselineAndLocallyUnknown": True,
            "allDesiredPairsAbsentFromReceipts": True,
            "noSubmissionPerformedByThisCertificate": True,
        },
        "exclusionAudit": {
            "receiptCount": int(receipt_meta["receiptCount"]),
            "receiptHashes": len(receipt_hashes),
            "receiptPairs": len(receipt_pairs),
        },
        "sideEffects": {
            "certificateWrites": 1,
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "ledgerWrites": 0,
            "outboxWrites": 0,
            "receiptWrites": 0,
            "submissionCalls": 0,
        },
    }
    write_new(
        POSTSTAGE_CERTIFICATE,
        (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "certificate": artifact(POSTSTAGE_CERTIFICATE),
                "manifest": certificate["manifestArtifact"],
                "stagedFreshRows": 17,
                "projectedMarginalScoreExact": "59/16",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Post-submit seal for the 136-row current tc2--tc6 twist delta."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import stage_single_exact_census as exact
from build_current_tc2_tc6_negative_twist_delta_20260729 import (
    ACTION_MAP,
    DATA,
    DB,
    OUTPUT,
    RECEIPTS,
    ROOT,
    artifact,
    fraction_text,
    pair_text,
    read_jsonl,
    sha256_path,
    write_new,
)


PRESTAGE_CERTIFICATE = (
    DATA / "current_tc2_tc6_negative_twist_delta_certificate_20260729.json"
)
MANIFEST = (
    ROOT / "outbox/current_tc2_tc6_negative_twist_delta_136_20260729.txt"
)
RECEIPT = (
    RECEIPTS / "sub_1a4d9cdf8d3b4545aba39a728ac261df.json"
)
CERTIFICATE = (
    DATA
    / "current_tc2_tc6_negative_twist_delta_poststage_certificate_20260729.json"
)
EXPECTED_CANDIDATE_SHA256 = (
    "ed2592c4086d33965d57d8d9fa8775317660b57dba96da1a50fdd0fe51f33c46"
)
EXPECTED_PRESTAGE_SHA256 = (
    "b6382db534fa38378355c1baf39effaf60194aa333265a60c95dccdeaf60d5ed"
)
EXPECTED_MANIFEST_SHA256 = (
    "a5f19a135fa3848ba55667727e76fb31c92d4396e6ba5263667503bbae3e2ea5"
)
EXPECTED_RECEIPT_SHA256 = (
    "be14fd93f02e8b6307ad15f450dda4eea85e8d6dbb77c3f24cbff40882b564ce"
)
EXPECTED_SUBMISSION_ID = "sub_1a4d9cdf8d3b4545aba39a728ac261df"


def canonical_lines(path: Path) -> list[str]:
    result = []
    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        line = exact.canonical_polynomial_line(raw.strip())
        if line is None or line != raw.strip():
            raise ValueError(f"noncanonical polynomial at {path}:{number}")
        result.append(line)
    if len(result) != len(set(result)):
        raise ValueError(f"duplicate polynomial within {path}")
    return result


def main() -> int:
    if (
        sha256_path(OUTPUT) != EXPECTED_CANDIDATE_SHA256
        or sha256_path(PRESTAGE_CERTIFICATE) != EXPECTED_PRESTAGE_SHA256
        or sha256_path(MANIFEST) != EXPECTED_MANIFEST_SHA256
        or sha256_path(RECEIPT) != EXPECTED_RECEIPT_SHA256
        or MANIFEST.stat().st_size != 67576
        or RECEIPT.stat().st_size != 12581
    ):
        raise ValueError(
            "candidate, pre-stage certificate, manifest, or receipt pin changed"
        )
    rows = read_jsonl(OUTPUT)
    expected_lines = [str(row["coefficientLine"]) for row in rows]
    expected_hashes = [str(row["coefficientSha256"]) for row in rows]
    expected_pairs = {
        (str(row["targetLabel"]), int(row["targetR"])) for row in rows
    }
    manifest_lines = canonical_lines(MANIFEST)
    manifest_hashes = [
        hashlib.sha256(line.encode("ascii")).hexdigest()
        for line in manifest_lines
    ]
    if (
        len(rows) != 136
        or len(expected_pairs) != 136
        or len(set(expected_hashes)) != 136
        or manifest_lines != expected_lines
        or manifest_hashes != expected_hashes
    ):
        raise ValueError("manifest is not the ordered 136-row candidate artifact")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    response = receipt.get("response") or {}
    queued = (response.get("payload") or {}).get("queuedPolynomials") or []
    if (
        receipt.get("commit") is not True
        or int(receipt.get("bytes", -1)) != 67576
        or int(receipt.get("polynomials", -1)) != 136
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or str(receipt.get("manifestHash")) != EXPECTED_MANIFEST_SHA256
        or Path(str(receipt.get("manifest"))).resolve() != MANIFEST.resolve()
        or str(response.get("submissionId")) != EXPECTED_SUBMISSION_ID
        or str(response.get("submissionStatus")) != "queued"
        or int(response.get("queuedCount", -1)) != 136
        or int(response.get("rejectedCount", -1)) != 0
        or response.get("failedPolynomials") != []
        or response.get("verifiedPolynomials") != []
        or [
            (int(item.get("polynomialIndex", -1)), str(item.get("status")))
            for item in queued
        ]
        != [(index, "queued") for index in range(136)]
    ):
        raise ValueError("pinned receipt is not the exact clean 136-row queue receipt")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        corpus_candidates, corpus = exact.scan_candidates(DATA.resolve())
        pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for candidate in corpus_candidates:
            pair_index[str(candidate["coefficientSha256"])].add(
                (str(candidate["targetLabel"]), int(candidate["targetR"]))
            )
        for row in rows:
            pair_index[str(row["coefficientSha256"])].add(
                (str(row["targetLabel"]), int(row["targetR"]))
            )
        # The current receipt intentionally owns these 136 hashes.  Remove
        # their candidate-pair claims from the index so receipt_pairs below
        # remains an exclusion set for every *other* receipt.
        other_receipt_pair_index = {
            digest: set(pairs)
            for digest, pairs in pair_index.items()
            if digest not in set(expected_hashes)
        }
        receipt_hashes, other_receipt_pairs, receipt_meta = (
            exact.receipt_exclusions(
                RECEIPTS, DATA, connection, other_receipt_pair_index
            )
        )
        expected_hash_set = set(expected_hashes)
        if not expected_hash_set <= receipt_hashes:
            raise ValueError("current receipt does not recover every submitted hash")
        other_receipt_hashes = receipt_hashes - expected_hash_set
        if (
            expected_hash_set & other_receipt_hashes
            or expected_pairs & other_receipt_pairs
        ):
            raise ValueError("another receipt collides with the submitted delta")

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
            raise ValueError("a staged delta hash is already in the ledger")

        other_outbox_hashes = set()
        other_outbox_pairs = set()
        occurrences = {digest: [] for digest in expected_hashes}
        other_files = 0
        for path in sorted((ROOT / "outbox").glob("*.txt")):
            lines = canonical_lines(path)
            if path.resolve() != MANIFEST.resolve() and lines:
                other_files += 1
            for number, line in enumerate(lines, start=1):
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest in occurrences:
                    occurrences[digest].append(
                        (str(path.resolve().relative_to(ROOT)), number)
                    )
                if path.resolve() != MANIFEST.resolve():
                    other_outbox_hashes.add(digest)
                    other_outbox_pairs.update(pair_index.get(digest, set()))
        expected_locations = [
            (str(MANIFEST.resolve().relative_to(ROOT)), index)
            for index in range(1, 137)
        ]
        actual_locations = [
            occurrences[digest][0]
            for digest in expected_hashes
            if len(occurrences[digest]) == 1
        ]
        if (
            any(len(value) != 1 for value in occurrences.values())
            or actual_locations != expected_locations
            or set(expected_hashes) & other_outbox_hashes
            or expected_pairs & other_outbox_pairs
        ):
            raise ValueError("delta rows collide with another txt outbox")

        baseline = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT label,r FROM baseline_pairs"
            )
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
            )
        }
        counts = Counter()
        projection = Fraction(0)
        targets = []
        for row in rows:
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            target = connection.execute(
                "SELECT team_count,discovered,generated_at "
                "FROM targets WHERE label=? AND r=?",
                pair,
            ).fetchone()
            if (
                target is None
                or int(target["team_count"]) != int(row["targetTeamCount"])
                or int(target["discovered"]) != 1
                or pair in baseline
                or pair in owned
            ):
                raise ValueError(f"staged target changed: {pair}")
            team_count = int(target["team_count"])
            counts[team_count] += 1
            projection += Fraction(1, 2**team_count)
            targets.append(
                {
                    "pair": pair_text(pair),
                    "teamCount": team_count,
                    "candidateSha256": str(row["coefficientSha256"]),
                }
            )
        if (
            counts != Counter({2: 2, 3: 13, 4: 24, 5: 45, 6: 52})
            or projection != Fraction(187, 32)
        ):
            raise ValueError("post-stage score distribution changed")
    finally:
        connection.close()

    created_at = datetime.now(timezone.utc).isoformat()
    corpus_for_certificate = corpus
    if CERTIFICATE.exists():
        existing = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
        if (
            existing.get("schemaVersion")
            != "current-tc2-tc6-negative-twist-delta-poststage-v1"
            or not existing.get("createdAt")
            or not isinstance(existing.get("candidateCorpusAudit"), dict)
        ):
            raise ValueError("existing post-stage certificate identity changed")
        created_at = str(existing["createdAt"])
        corpus_for_certificate = dict(existing["candidateCorpusAudit"])
    certificate = {
        "schemaVersion": "current-tc2-tc6-negative-twist-delta-postsubmit-v1",
        "createdAt": created_at,
        "status": "certified_136_twist_delta_rows_submitted_cleanly_queued",
        "coefficientMaterialIncluded": False,
        "actionArtifact": artifact(ACTION_MAP),
        "candidateArtifact": artifact(OUTPUT),
        "prestageCertificate": artifact(PRESTAGE_CERTIFICATE),
        "manifestArtifact": {
            **artifact(MANIFEST),
            "bytes": MANIFEST.stat().st_size,
            "canonicalPolynomialRows": 136,
            "distinctCoefficientHashes": 136,
        },
        "submissionReceipt": {
            **artifact(RECEIPT),
            "bytes": RECEIPT.stat().st_size,
            "submissionId": EXPECTED_SUBMISSION_ID,
            "submissionStatus": "queued",
            "queuedRows": 136,
            "rejectedRows": 0,
        },
        "candidateRows": 136,
        "distinctTargetPairs": 136,
        "teamCountDistribution": {
            str(team_count): int(counts[team_count])
            for team_count in sorted(counts)
        },
        "projectedMarginalScoreExact": fraction_text(projection),
        "targets": targets,
        "proofChecks": {
            "prestageCertificateHashPinned": True,
            "manifestHashSizeAndOrderPinned": True,
            "submissionReceiptHashAndSizePinned": True,
            "receiptPinsExactManifestAndAll136OrderedQueueIndices": True,
            "submissionCleanlyQueuedWithZeroImmediateRejections": True,
            "manifestEqualsAll136CandidateRows": True,
            "eachManifestHashOccursExactlyOnceAcrossTxtOutboxes": True,
            "allSubmittedHashesAbsentFromLedgerAtQueueSeal": True,
            "allHashesAndPairsAbsentFromEveryOtherReceipt": True,
            "allHashesAndPairsAbsentFromOtherTxtOutboxes": True,
            "allTargetsCurrentTc2ThroughTc6NonbaselineLocallyUnknown": True,
            "noAdditionalSubmissionPerformedByThisCertificate": True,
        },
        "candidateCorpusAudit": corpus_for_certificate,
        "exclusionAudit": {
            "receiptCount": int(receipt_meta["receiptCount"]),
            "currentReceiptHashes": len(expected_hash_set),
            "otherReceiptHashes": len(other_receipt_hashes),
            "otherReceiptPairs": len(other_receipt_pairs),
            "otherNonemptyOutboxFiles": other_files,
            "otherOutboxHashes": len(other_outbox_hashes),
            "otherOutboxPairs": len(other_outbox_pairs),
        },
        "sideEffects": {
            "certificateWrites": 1,
            "networkCalls": 0,
            "ledgerWrites": 0,
            "outboxWrites": 0,
            "receiptWrites": 0,
            "submissionCalls": 0,
            "sageRuns": 0,
            "gapRuns": 0,
        },
    }
    write_new(
        CERTIFICATE,
        (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "certificate": artifact(CERTIFICATE),
                "manifest": certificate["manifestArtifact"],
                "candidateRows": 136,
                "projectedMarginalScoreExact": fraction_text(projection),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal the receipt-aware zero guaranteed frontier for retained MULTI packets.

The certificate is coefficient-free.  It validates every retained
``certified_multi`` payload against its recorded hash, rejoins every source to
the accepted scoreable ledger, excludes exact-covered/known/owned/baseline/
receipted outcomes, and enumerates every unresolved target-label permutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import stage_frobenius_gold as frobenius
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RECEIPTS = ROOT / "receipts"
DATABASE = DATA / "ledger.sqlite3"
CERTIFICATE = DATA / "uncertified_multi_zero_actionable_frontier_20260722_certificate.json"
SUMMARY = DATA / "uncertified_multi_zero_actionable_frontier_20260722_summary.json"
PINNED_SUBMISSION = "sub_e8301c0d8abe4765bd3d7d82ff0d1199"
METHOD = "receipt-aware-uncertified-multi-guaranteed-frontier-closure-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def write_new(path: Path, payload: bytes) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite sealed output: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def unique_permutations(values: list[str]):
    counts = Counter(values)
    ordered = sorted(counts, key=frobenius.label_t)
    result: list[str] = []

    def visit():
        if len(result) == len(values):
            yield tuple(result)
            return
        for value in ordered:
            if counts[value] == 0:
                continue
            counts[value] -= 1
            result.append(value)
            yield from visit()
            result.pop()
            counts[value] += 1

    yield from visit()


def scan_packets(data_dir: Path) -> tuple[list[dict], dict[str, set[tuple[str, int]]], dict]:
    occurrences = []
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    artifact_hashes = {}
    for artifact in sorted(data_dir.rglob("*.jsonl")):
        if not artifact.is_file():
            continue
        found = False
        try:
            handle = artifact.open(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        with handle:
            for line_number, line in enumerate(handle, start=1):
                if "certified_multi" not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("status") != "certified_multi":
                    continue
                source = frobenius.source_key(row)
                candidates = sorted(
                    row.get("candidates") or [], key=lambda item: int(item["factorIndex"])
                )
                labels = [
                    str(target["targetLabel"])
                    for target in row.get("orbitTargets") or []
                ]
                if (
                    not candidates
                    or [int(item["factorIndex"]) for item in candidates]
                    != list(range(len(candidates)))
                    or len(candidates) != len(labels)
                ):
                    raise ValueError(f"invalid MULTI factor/target layout: {artifact}:{line_number}")
                if int(row.get("workerExitCode", 0)) != 0:
                    raise ValueError(f"failed worker marked certified_multi: {artifact}:{line_number}")
                public_candidates = []
                for candidate in candidates:
                    line_value = frobenius.validated_coefficient_line(candidate, source)
                    digest = str(candidate["coefficientSha256"])
                    byte_count = int(candidate["coefficientBytes"])
                    target_r = int(candidate["targetR"])
                    polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
                    if (
                        byte_count != len(line_value.encode("ascii"))
                        or target_r not in range(0, 25, 2)
                        or polynomial_disc <= 0
                    ):
                        raise ValueError(f"invalid MULTI candidate metadata: {artifact}:{line_number}")
                    pair_index[digest].update((label, target_r) for label in labels)
                    public_candidates.append(
                        {
                            "coefficientBytes": byte_count,
                            "coefficientSha256": digest,
                            "factorIndex": int(candidate["factorIndex"]),
                            "targetR": target_r,
                        }
                    )
                packet_identity = {
                    "candidateHashes": [item["coefficientSha256"] for item in public_candidates],
                    "source": list(source),
                }
                occurrences.append(
                    {
                        "artifact": display_path(artifact),
                        "candidates": public_candidates,
                        "labels": labels,
                        "line": line_number,
                        "packetSha256": sha256_bytes(
                            json.dumps(packet_identity, separators=(",", ":"), sort_keys=True).encode()
                        ),
                        "source": source,
                    }
                )
                found = True
        if found:
            artifact_hashes[display_path(artifact)] = sha256_path(artifact)

    unique = {}
    for packet in occurrences:
        identity = (packet["source"], tuple(row["coefficientSha256"] for row in packet["candidates"]))
        incumbent = unique.get(identity)
        if incumbent is None or (packet["artifact"], packet["line"]) < (
            incumbent["artifact"], incumbent["line"]
        ):
            unique[identity] = packet
    artifact_index = [
        {"path": path, "sha256": digest} for path, digest in sorted(artifact_hashes.items())
    ]
    return list(unique.values()), pair_index, {
        "artifacts": len(artifact_index),
        "artifactIndexSha256": sha256_bytes(json_bytes({"artifacts": artifact_index})),
        "duplicateOccurrences": len(occurrences) - len(unique),
        "occurrences": len(occurrences),
        "uniqueSourcePackets": len(unique),
    }


def exact_source_keys(data_dir: Path) -> tuple[set[tuple], dict]:
    keys = set()
    artifacts = []
    for artifact in sorted(data_dir.rglob("*.json")):
        if not artifact.is_file():
            continue
        try:
            value = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("method") != frobenius.CERTIFICATE_METHOD:
            continue
        row_count = 0
        for row in value.get("rows") or []:
            keys.add(frobenius.source_key(row))
            row_count += 1
        artifacts.append(
            {
                "path": display_path(artifact),
                "rows": row_count,
                "sha256": sha256_path(artifact),
            }
        )
    return keys, {
        "artifacts": len(artifacts),
        "artifactIndexSha256": sha256_bytes(json_bytes({"artifacts": artifacts})),
        "sourceKeys": len(keys),
    }


def validate_pinned_receipt(receipts_dir: Path) -> dict:
    receipt_path = receipts_dir / f"{PINNED_SUBMISSION}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    response = receipt.get("response") or {}
    if str(response.get("submissionId")) != PINNED_SUBMISSION:
        raise ValueError("pinned receipt submission id mismatch")
    manifest = Path(str(receipt["manifest"])).expanduser().resolve()
    manifest_sha = sha256_path(manifest)
    if manifest_sha != str(receipt["manifestHash"]):
        raise ValueError("pinned receipt manifest hash mismatch")
    lines = [
        line
        for raw in manifest.read_text(encoding="utf-8").splitlines()
        if (line := single.canonical_polynomial_line(raw.strip())) is not None
    ]
    if len(lines) != int(receipt["polynomials"]):
        raise ValueError("pinned receipt manifest row count mismatch")
    return {
        "manifest": display_path(manifest),
        "manifestSha256": manifest_sha,
        "polynomials": len(lines),
        "receipt": display_path(receipt_path),
        "receiptSha256": sha256_path(receipt_path),
        "submissionId": PINNED_SUBMISSION,
    }


def source_provenance(connection: sqlite3.Connection, source: tuple) -> None:
    rows = connection.execute(
        "SELECT status,label,r,scoreable FROM verifications "
        "WHERE submission_id=? AND polynomial_index=?",
        source[:2],
    ).fetchall()
    if (
        len(rows) != 1
        or str(rows[0][0]) != "accepted"
        or str(rows[0][1]) != source[2]
        or int(rows[0][2]) != source[3]
        or int(rows[0][3] or 0) != 1
    ):
        raise ValueError(f"source is not uniquely accepted and scoreable: {source}")


def seal(args: argparse.Namespace) -> dict:
    data_dir = args.data.expanduser().resolve()
    receipts_dir = args.receipts.expanduser().resolve()
    database = args.database.expanduser().resolve()
    certificate_output = args.certificate.expanduser().resolve()
    summary_output = args.summary.expanduser().resolve()
    if certificate_output == summary_output or database in {certificate_output, summary_output}:
        raise ValueError("outputs must be distinct and must not overwrite the ledger")

    packets, pair_index, corpus_audit = scan_packets(data_dir)
    exact_keys, exact_audit = exact_source_keys(data_dir)
    pinned_receipt = validate_pinned_receipt(receipts_dir)

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        known_hashes = {
            str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        owned_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        baseline_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        target_rows = list(
            connection.execute("SELECT label,r,team_count,generated_at FROM targets")
        )
        targets = {
            (str(row[0]), int(row[1])): {
                "generatedAt": str(row[3]),
                "teamCount": int(row[2]),
            }
            for row in target_rows
        }
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            receipts_dir, data_dir, connection, pair_index
        )

        classifications = Counter()
        conditional_any = []
        conditional_shared = 0
        conditional_gold = 0
        guaranteed_any = []
        source_keys_checked = set()
        for packet in packets:
            source = packet["source"]
            if source not in source_keys_checked:
                source_provenance(connection, source)
                source_keys_checked.add(source)
            if source in exact_keys:
                classifications["exact_certificate_covers_source"] += 1
                continue
            candidates = packet["candidates"]
            if all(
                row["coefficientSha256"] in known_hashes
                or row["coefficientSha256"] in receipt_hashes
                for row in candidates
            ):
                classifications["all_candidate_hashes_known_or_receipted"] += 1
                continue

            assignment_counts = []
            shared_counts = []
            gold_counts = []
            for assignment in unique_permutations(packet["labels"]):
                eligible = set()
                for candidate, label in zip(candidates, assignment):
                    digest = candidate["coefficientSha256"]
                    pair = (label, candidate["targetR"])
                    if (
                        digest in known_hashes
                        or digest in receipt_hashes
                        or pair in owned_pairs
                        or pair in baseline_pairs
                        or pair in receipt_pairs
                        or pair not in targets
                    ):
                        continue
                    eligible.add(pair)
                assignment_counts.append(len(eligible))
                shared_counts.append(
                    sum(1 for pair in eligible if targets[pair]["teamCount"] > 0)
                )
                gold_counts.append(
                    sum(1 for pair in eligible if targets[pair]["teamCount"] == 0)
                )
            if not assignment_counts or max(assignment_counts) == 0:
                classifications["no_current_unexcluded_outcome_in_any_assignment"] += 1
                continue
            public = {
                "artifact": packet["artifact"],
                "compatibleLabelAssignments": len(assignment_counts),
                "line": packet["line"],
                "maximumEligibleDistinctPairs": max(assignment_counts),
                "minimumEligibleDistinctPairs": min(assignment_counts),
                "packetSha256": packet["packetSha256"],
                "sourceLabel": source[2],
                "sourcePolynomialIndex": source[1],
                "sourceR": source[3],
                "sourceSubmissionId": source[0],
            }
            if min(assignment_counts) > 0:
                guaranteed_any.append(public)
                classifications["guaranteed_actionable"] += 1
            else:
                conditional_any.append(public)
                classifications["conditional_only_zero_floor"] += 1
            if max(shared_counts) > 0:
                conditional_shared += 1
            if max(gold_counts) > 0:
                conditional_gold += 1

    if guaranteed_any:
        raise ValueError(
            f"frontier is not closed: {len(guaranteed_any)} guaranteed packets remain"
        )

    conditional_any.sort(
        key=lambda row: (
            frobenius.label_t(row["sourceLabel"]),
            row["sourceR"],
            row["sourceSubmissionId"],
            row["sourcePolynomialIndex"],
        )
    )
    certificate = {
        "actionableDefinition": (
            "A retained uncertified MULTI packet is actionable only when every "
            "compatible target-label assignment contains at least one distinct "
            "current target pair whose coefficient hash is unknown/unreceipted and "
            "whose pair is nonbaseline, unowned, and unreceipted."
        ),
        "checks": {
            "allCandidatePayloadHashesCanonicalAndMatched": True,
            "allCompatibleLabelAssignmentsEnumerated": True,
            "allRetainedSourcesRejoinedToAcceptedScoreableLedgerRows": True,
            "baselineOwnedKnownAndReceiptHashesPairsExcluded": True,
            "certificateContainsNoCoefficientPayload": True,
            "pinnedTerminalReceiptManifestHashMatched": True,
            "zeroGuaranteedActionablePackets": True,
        },
        "classification": {
            "counts": dict(sorted(classifications.items())),
            "guaranteedActionablePackets": 0,
            "conditionalOnlyPackets": len(conditional_any),
            "conditionalOnlyWithSomeGoldOutcome": conditional_gold,
            "conditionalOnlyWithSomeSharedOutcome": conditional_shared,
            "conditionalPackets": conditional_any,
            "note": (
                "Conditional-only packets have a zero eligible floor across their "
                "unresolved assignments; they are recorded, not silently discarded."
            ),
        },
        "corpus": {
            "certifiedMulti": corpus_audit,
            "exactCertificates": exact_audit,
            "sourceLedgerKeysChecked": len(source_keys_checked),
        },
        "database": {
            "path": display_path(database),
            "sha256": sha256_path(database),
        },
        "method": METHOD,
        "networkCalls": 0,
        "pinnedTerminalReceipt": pinned_receipt,
        "receiptExclusion": {
            "queuedPossiblePairMapRows": receipt_audit["queuedPossiblePairMapRows"],
            "receiptCount": receipt_audit["receiptCount"],
            "receiptPolynomialHashes": receipt_audit["receiptPolynomialHashes"],
            "receiptTargetPairs": receipt_audit["receiptTargetPairs"],
        },
        "status": "zero_guaranteed_actionable_frontier",
        "submissionCalls": 0,
        "targetSnapshot": {
            "generatedAtMaximum": max(str(row[3]) for row in target_rows),
            "pairs": len(targets),
        },
    }
    certificate_payload = json_bytes(certificate)
    if b"coefficientLine" in certificate_payload:
        raise ValueError("coefficient payload key leaked into certificate")
    summary = {
        "certificate": display_path(certificate_output),
        "certificateSha256": sha256_bytes(certificate_payload),
        "conditionalOnlyPackets": len(conditional_any),
        "guaranteedActionablePackets": 0,
        "method": METHOD,
        "pinnedSubmissionId": PINNED_SUBMISSION,
        "status": "zero_guaranteed_actionable_frontier",
        "submissionCalls": 0,
    }
    summary_payload = json_bytes(summary)
    write_new(certificate_output, certificate_payload)
    write_new(summary_output, summary_payload)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--certificate", type=Path, default=CERTIFICATE)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    args = parser.parse_args()
    try:
        result = seal(args)
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

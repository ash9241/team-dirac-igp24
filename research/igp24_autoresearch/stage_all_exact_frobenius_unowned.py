#!/usr/bin/env python3
"""Seal every current locally-unowned polynomial with an exact Frobenius proof.

The stage is deliberately offline.  It discovers the pinned exact Frobenius
certificates below ``data/``, validates each certificate against its immutable
input, checks source provenance against the read-only ledger, excludes every
baseline/owned/known/receipted result, and writes a new manifest plus
coefficient-free certificate and summary.  Outputs are sealed and are never
replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Iterable

import stage_frobenius_gold as frobenius


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DATABASE = DATA / "ledger.sqlite3"

MANIFEST = OUTBOX / "all_exact_frobenius_unowned_20260722.txt"
CERTIFICATE = DATA / "all_exact_frobenius_unowned_20260722_certificate.json"
SUMMARY = DATA / "all_exact_frobenius_unowned_20260722_summary.json"

EXACT_METHOD = frobenius.CERTIFICATE_METHOD
STAGE_METHOD = "all-exact-frobenius-unowned-sealed-stage-v1"
DEFAULT_EXPECTED_CERTIFICATES = 65
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class StagedPairsByManifest(defaultdict):
    """Pair exclusions plus explicit per-polynomial alternative coverage.

    Most sealed stages assign one exact pair per polynomial.  An unresolved
    but all-compatible-safe stage can instead prove a finite set of possible
    pairs for each receipted polynomial.  ``polynomial_counts`` records that
    every polynomial in such a manifest has a complete possible-pair map;
    the normal set value remains the conservative union of all pairs.
    """

    def __init__(self) -> None:
        super().__init__(set)
        self.polynomial_counts: dict[str, int] = {}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path, root: Path = ROOT) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(root.expanduser().resolve()))
    except ValueError:
        return str(resolved)


def json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_line(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.split("#", 1)[0].strip()
    if not stripped:
        return None
    if stripped.count(",") != 24:
        return None
    try:
        coefficients = [int(entry.strip()) for entry in stripped.split(",")]
    except ValueError:
        return None
    if (
        len(coefficients) != 25
        or coefficients[0] == 0
        or coefficients[-1] != 1
        or math.gcd(*coefficients) != 1
    ):
        return None
    return ",".join(str(entry) for entry in coefficients)


def manifest_hashes(path: Path) -> tuple[list[str], dict[str, str]]:
    ordered = []
    payloads = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = canonical_line(raw)
        if line is None:
            if raw.split("#", 1)[0].strip():
                raise ValueError(f"invalid polynomial at {path}:{line_number}")
            continue
        digest = sha256_bytes(line.encode("ascii"))
        previous = payloads.setdefault(digest, line)
        if previous != line:
            raise ValueError(f"hash collision in receipt manifest {path}")
        ordered.append(digest)
    if len(ordered) != len(set(ordered)):
        raise ValueError(f"duplicate polynomial in receipt manifest {path}")
    return ordered, payloads


def resolve_saved_input(certificate_path: Path, value: object, root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"certificate has no saved input path: {certificate_path}")
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
        # Certificates are immutable and may have been copied with the project
        # from a previous workstation.  Remap only the path below the pinned
        # project directory; collect_exact_pool still verifies inputSha256, so
        # a same-named but different file cannot satisfy the certificate.
        parts = candidate.parts
        try:
            project_index = parts.index("igp24_autoresearch")
        except ValueError:
            return resolved
        migrated = (root / Path(*parts[project_index + 1 :])).resolve()
        return migrated if migrated.is_file() else resolved
    root_candidate = (root / candidate).resolve()
    local_candidate = (certificate_path.parent / candidate).resolve()
    if root_candidate.is_file():
        return root_candidate
    return local_candidate


def parse_pair(value: object) -> tuple[str, int] | None:
    if isinstance(value, str) and "/r" in value:
        label, raw_r = value.rsplit("/r", 1)
        frobenius.label_t(label)
        return label, int(raw_r)
    if isinstance(value, dict):
        if "pair" in value:
            return parse_pair(value["pair"])
        label = value.get("label", value.get("targetLabel"))
        raw_r = value.get("r", value.get("targetR"))
        if isinstance(label, str) and raw_r is not None:
            frobenius.label_t(label)
            return label, int(raw_r)
    return None


def scan_json_artifacts(
    data_dir: Path,
) -> tuple[list[tuple[Path, dict]], dict[str, set[tuple[str, int]]]]:
    certificates = []
    staged_pairs_by_manifest = StagedPairsByManifest()
    method_literal = f'"{EXACT_METHOD}"'
    for path in sorted(data_dir.rglob("*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"cannot read JSON artifact {path}") from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            if method_literal in raw:
                raise ValueError(f"malformed exact certificate: {path}") from exc
            continue
        if not isinstance(value, dict):
            continue
        if value.get("method") == EXACT_METHOD:
            certificates.append((path.resolve(), value))

        artifact_sha = value.get("artifactSha256")
        manifest_sha = (
            artifact_sha.get("manifest")
            if isinstance(artifact_sha, dict)
            else value.get("manifestSha256")
        )
        raw_pairs = value.get("stagedPairs")
        if not isinstance(raw_pairs, list):
            raw_pairs = value.get("selectedPairs")
        raw_possible = value.get("receiptPolynomialPossiblePairs")
        if (
            not isinstance(manifest_sha, str)
            or not isinstance(raw_pairs, list)
            and not isinstance(raw_possible, list)
        ):
            continue
        if not SHA256_RE.fullmatch(manifest_sha):
            raise ValueError(f"invalid manifest SHA in stage artifact {path}")
        parsed = set()
        if isinstance(raw_pairs, list):
            for raw_pair in raw_pairs:
                pair = parse_pair(raw_pair)
                if pair is None:
                    raise ValueError(f"invalid staged pair in {path}")
                parsed.add(pair)
        if isinstance(raw_possible, list):
            seen_hashes = set()
            for item in raw_possible:
                if not isinstance(item, dict):
                    raise ValueError(f"invalid possible-pair mapping in {path}")
                digest = item.get("coefficientSha256")
                possible_pairs = item.get("possiblePairs")
                if (
                    not isinstance(digest, str)
                    or not SHA256_RE.fullmatch(digest)
                    or digest in seen_hashes
                    or not isinstance(possible_pairs, list)
                    or not possible_pairs
                ):
                    raise ValueError(f"invalid possible-pair mapping in {path}")
                seen_hashes.add(digest)
                for raw_pair in possible_pairs:
                    pair = parse_pair(raw_pair)
                    if pair is None:
                        raise ValueError(f"invalid possible staged pair in {path}")
                    parsed.add(pair)
            previous_count = staged_pairs_by_manifest.polynomial_counts.get(manifest_sha)
            if previous_count is not None and previous_count != len(raw_possible):
                raise ValueError(
                    f"conflicting possible-pair polynomial counts for {manifest_sha}"
                )
            staged_pairs_by_manifest.polynomial_counts[manifest_sha] = len(raw_possible)
        staged_pairs_by_manifest[manifest_sha].update(parsed)
    return certificates, staged_pairs_by_manifest


def candidate_lookup(rows: list[dict]) -> dict[tuple, tuple[dict, dict]]:
    lookup = {}
    for source in rows:
        if source.get("status") != "certified_multi":
            continue
        key = frobenius.source_key(source)
        candidates = source.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"certified source has no candidates: {key}")
        for candidate in candidates:
            factor_index = int(candidate["factorIndex"])
            item_key = (*key, factor_index)
            if item_key in lookup:
                raise ValueError(f"duplicate candidate factor: {item_key}")
            lookup[item_key] = (source, candidate)
    return lookup


def proof_lookup(certificate: dict) -> dict[tuple, dict]:
    lookup = {}
    for row_index, row in enumerate(certificate.get("rows") or []):
        if row.get("status") != "resolved":
            continue
        remaining = row.get("remainingLabelAssignments")
        if not isinstance(remaining, list) or len(remaining) != 1:
            raise ValueError(
                f"resolved row does not have one label assignment: "
                f"{frobenius.source_key(row)}"
            )
        key = frobenius.source_key(row)
        for assignment_index, assignment in enumerate(row.get("assignments") or []):
            item_key = (*key, int(assignment["factorIndex"]))
            if item_key in lookup:
                raise ValueError(f"duplicate resolved proof factor: {item_key}")
            lookup[item_key] = {
                "assignmentIndex": assignment_index,
                "rowIndex": row_index,
            }
    return lookup


def require_source_provenance(
    connection: sqlite3.Connection, key: tuple[str, int, str, int]
) -> None:
    rows = connection.execute(
        "SELECT label,r,scoreable,status FROM verifications "
        "WHERE submission_id=? AND polynomial_index=?",
        (key[0], key[1]),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"source ledger key is missing or nonunique: {key}")
    label, r, scoreable, _status = rows[0]
    if str(label) != key[2] or int(r) != key[3] or int(scoreable or 0) != 1:
        raise ValueError(f"source provenance/scoreability mismatch: {key} -> {rows[0]}")


def collect_exact_pool(
    certificates: list[tuple[Path, dict]],
    connection: sqlite3.Connection,
    root: Path,
    expected_certificates: int | None,
) -> tuple[dict[str, dict], list[dict], dict]:
    if expected_certificates is not None and len(certificates) != expected_certificates:
        raise ValueError(
            f"expected {expected_certificates} exact certificates, found "
            f"{len(certificates)}"
        )

    pool: dict[str, dict] = {}
    certificate_audit = []
    source_keys = set()
    total_assignments = 0
    resolved_rows = 0
    for certificate_path, certificate in certificates:
        input_path = resolve_saved_input(certificate_path, certificate.get("input"), root)
        candidate_rows = frobenius.read_jsonl(input_path)
        joined = frobenius.join_resolved_assignments(
            certificate,
            candidate_rows,
            input_path,
            allow_unresolved=True,
        )
        candidates = candidate_lookup(candidate_rows)
        proofs = proof_lookup(certificate)
        certificate_sha = sha256_path(certificate_path)
        input_sha = sha256_path(input_path)
        resolved_here = int(certificate["summary"]["resolved"])
        resolved_rows += resolved_here
        total_assignments += len(joined)

        certificate_audit.append(
            {
                "assignments": len(joined),
                "input": display_path(input_path, root),
                "inputSha256": input_sha,
                "path": display_path(certificate_path, root),
                "resolvedRows": resolved_here,
                "sha256": certificate_sha,
                "unresolvedRows": int(certificate["summary"]["unresolved"]),
            }
        )

        for joined_row in joined:
            source = frobenius.source_key(joined_row)
            factor_index = int(joined_row["factorIndex"])
            item_key = (*source, factor_index)
            if item_key not in candidates or item_key not in proofs:
                raise ValueError(f"joined candidate/proof lookup failed: {item_key}")
            source_row, candidate = candidates[item_key]
            proof = proofs[item_key]
            if source not in source_keys:
                require_source_provenance(connection, source)
                source_keys.add(source)
            if "workerExitCode" in source_row and int(source_row["workerExitCode"]) != 0:
                raise ValueError(f"candidate worker failed: {source}")

            line = str(joined_row["coefficientLine"])
            digest = str(joined_row["coefficientSha256"])
            byte_count = len(line.encode("ascii"))
            if "coefficientBytes" in candidate and int(candidate["coefficientBytes"]) != byte_count:
                raise ValueError(f"candidate byte count mismatch: {digest}")
            pair = (str(joined_row["targetLabel"]), int(joined_row["targetR"]))
            polynomial_disc = int(joined_row["polynomialDiscriminantAbs"])
            field_disc = candidate.get("fieldDiscriminantAbs")
            if field_disc is not None and int(field_disc) <= 0:
                raise ValueError(f"invalid field discriminant: {digest}")

            item = pool.get(digest)
            if item is None:
                item = {
                    "coefficientBytes": byte_count,
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "fieldDiscriminants": set(),
                    "pair": pair,
                    "polynomialDiscriminantAbs": polynomial_disc,
                    "proofs": [],
                    "targetT": frobenius.label_t(pair[0]),
                }
                pool[digest] = item
            if (
                item["coefficientLine"] != line
                or item["coefficientBytes"] != byte_count
                or item["pair"] != pair
                or item["polynomialDiscriminantAbs"] != polynomial_disc
            ):
                raise ValueError(f"conflicting exact claim/payload for hash {digest}")
            if field_disc is not None:
                item["fieldDiscriminants"].add(int(field_disc))
                if len(item["fieldDiscriminants"]) > 1:
                    raise ValueError(f"conflicting field discriminants for hash {digest}")
            item["proofs"].append(
                {
                    "artifact": display_path(certificate_path, root),
                    "artifactSha256": certificate_sha,
                    "assignmentPointer": (
                        f"rows[{proof['rowIndex']}].assignments"
                        f"[{proof['assignmentIndex']}]"
                    ),
                    "input": display_path(input_path, root),
                    "inputSha256": input_sha,
                    "sourceLabel": source[2],
                    "sourcePolynomialIndex": source[1],
                    "sourceR": source[3],
                    "sourceSubmissionId": source[0],
                }
            )

    return pool, certificate_audit, {
        "certificates": len(certificates),
        "resolvedRows": resolved_rows,
        "sourceLedgerKeys": len(source_keys),
        "totalAssignments": total_assignments,
        "uniqueCoefficientHashes": len(pool),
    }


def infer_expected_polynomials(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    if value.get("polynomials") is not None:
        return int(value["polynomials"])
    if value.get("queuedCount") is not None or value.get("rejectedCount") is not None:
        return int(value.get("queuedCount", 0)) + int(value.get("rejectedCount", 0))
    return None


def resolve_manifest_path(value: object, root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def receipt_exclusions(
    connection: sqlite3.Connection,
    receipts_dir: Path,
    staged_pairs_by_manifest: dict[str, set[tuple[str, int]]],
    exact_pool: dict[str, dict],
    root: Path,
) -> tuple[set[str], set[tuple[str, int]], dict]:
    records: dict[str, dict] = {}

    def record_for(submission_id: str) -> dict:
        return records.setdefault(
            submission_id,
            {"expected": set(), "filesystem": [], "manifests": {}},
        )

    for path in sorted(receipts_dir.glob("sub_*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid filesystem receipt: {path}") from exc
        if not isinstance(receipt, dict):
            raise ValueError(f"filesystem receipt is not an object: {path}")
        response = receipt.get("response")
        submission_id = str(
            response.get("submissionId")
            if isinstance(response, dict) and response.get("submissionId")
            else path.stem
        )
        record = record_for(submission_id)
        record["filesystem"].append(display_path(path, root))
        expected = infer_expected_polynomials(receipt)
        if expected is not None:
            record["expected"].add(expected)
        manifest_sha = receipt.get("manifestHash")
        manifest = resolve_manifest_path(receipt.get("manifest"), root)
        if isinstance(manifest_sha, str):
            record["manifests"][(str(manifest), manifest_sha)] = manifest

    table_rows = connection.execute(
        "SELECT submission_id,manifest_path,manifest_hash,raw_json "
        "FROM submission_receipts"
    ).fetchall()
    for submission_id, manifest_value, manifest_sha, raw_json in table_rows:
        record = record_for(str(submission_id))
        manifest = resolve_manifest_path(manifest_value, root)
        record["manifests"][(str(manifest), str(manifest_sha))] = manifest
        try:
            raw_value = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            raw_value = None
        expected = infer_expected_polynomials(raw_value)
        if expected is not None:
            record["expected"].add(expected)

    excluded_hashes = set()
    excluded_pairs = set()
    audit_rows = []
    manifest_sha_used = set()
    unsynced_ids = []
    for submission_id in sorted(records):
        record = records[submission_id]
        if len(record["expected"]) > 1:
            raise ValueError(f"conflicting receipt counts for {submission_id}")
        expected = next(iter(record["expected"]), None)
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials WHERE submission_id=?",
                (submission_id,),
            )
        }
        ledger_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT label,r FROM verifications WHERE submission_id=? "
                "AND label IS NOT NULL AND r IS NOT NULL",
                (submission_id,),
            )
        }
        record_hashes = set(ledger_hashes)
        record_pairs = set(ledger_pairs)
        manifest_rows = []
        intact_hash_sets = []
        for (manifest_text, recorded_sha), manifest in sorted(record["manifests"].items()):
            if not SHA256_RE.fullmatch(recorded_sha):
                raise ValueError(f"invalid receipt manifest SHA for {submission_id}")
            status = "missing"
            hashes = []
            if manifest is not None and manifest.is_file():
                if sha256_path(manifest) == recorded_sha:
                    status = "intact"
                    hashes, _ = manifest_hashes(manifest)
                    intact_hash_sets.append(set(hashes))
                    record_hashes.update(hashes)
                    record_pairs.update(staged_pairs_by_manifest.get(recorded_sha, set()))
                    manifest_sha_used.add(recorded_sha)
                else:
                    status = "changed"
            manifest_row = {
                "hashesUsed": len(hashes),
                "path": display_path(manifest, root) if manifest is not None else None,
                "recordedSha256": recorded_sha,
                "status": status,
            }
            mapped_count = getattr(
                staged_pairs_by_manifest, "polynomial_counts", {}
            ).get(recorded_sha)
            if mapped_count is not None:
                manifest_row["possiblePairMappedPolynomials"] = mapped_count
            manifest_rows.append(manifest_row)

        if expected is not None:
            if expected < 0:
                raise ValueError(f"negative receipt count for {submission_id}")
            if len(ledger_hashes) > expected:
                raise ValueError(f"ledger exceeds receipt count for {submission_id}")
            if len(ledger_hashes) < expected:
                if not any(len(hashes) == expected for hashes in intact_hash_sets):
                    raise ValueError(
                        f"unsynced receipt lacks an intact complete manifest: {submission_id}"
                    )
                unsynced_ids.append(submission_id)

        exactly_mapped_hashes = 0
        for digest in record_hashes:
            item = exact_pool.get(digest)
            if item is not None:
                record_pairs.add(item["pair"])
                exactly_mapped_hashes += 1
        if expected is not None and len(ledger_hashes) < expected:
            if exactly_mapped_hashes != expected and not any(
                (
                    getattr(staged_pairs_by_manifest, "polynomial_counts", {}).get(sha)
                    == expected
                    or len(staged_pairs_by_manifest.get(sha, set())) == expected
                )
                for _, sha in record["manifests"]
            ):
                raise ValueError(
                    f"unsynced receipt target pairs are not fully exact-mapped: {submission_id}"
                )

        excluded_hashes.update(record_hashes)
        excluded_pairs.update(record_pairs)
        audit_rows.append(
            {
                "exactMappedHashes": exactly_mapped_hashes,
                "expectedPolynomials": expected,
                "filesystemReceipts": record["filesystem"],
                "ledgerHashes": len(ledger_hashes),
                "ledgerPairs": len(ledger_pairs),
                "manifestEvidence": manifest_rows,
                "receiptHashesUsed": len(record_hashes),
                "receiptPairsUsed": len(record_pairs),
                "submissionId": submission_id,
                "syncedToLedger": expected is None or len(ledger_hashes) >= expected,
            }
        )

    pairs_sorted = sorted(excluded_pairs, key=lambda pair: (frobenius.label_t(pair[0]), pair[1]))
    return excluded_hashes, excluded_pairs, {
        "excludedExactPairs": [f"{label}/r{r}" for label, r in pairs_sorted],
        "excludedHashCount": len(excluded_hashes),
        "excludedHashesSetSha256": sha256_bytes(
            "".join(f"{digest}\n" for digest in sorted(excluded_hashes)).encode("ascii")
        ),
        "excludedPairCount": len(excluded_pairs),
        "filesystemReceiptCount": sum(len(row["filesystemReceipts"]) for row in audit_rows),
        "manifestSha256Used": sorted(manifest_sha_used),
        "receipts": audit_rows,
        "submissionReceiptTableRows": len(table_rows),
        "unsyncedSubmissionIds": unsynced_ids,
    }


def filter_and_dedupe(
    pool: dict[str, dict],
    connection: sqlite3.Connection,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    max_team_count: int | None,
) -> tuple[list[dict], list[dict], Counter]:
    if max_team_count is not None and max_team_count < 0:
        raise ValueError("--max-team-count must be nonnegative")
    skips: Counter = Counter()
    eligible = []
    for digest in sorted(pool):
        row = pool[digest]
        pair = row["pair"]
        if connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
        ).fetchone():
            skips["baseline_pair"] += 1
            continue
        if connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? "
            "AND scoreable=1 LIMIT 1",
            pair,
        ).fetchone():
            skips["locally_owned_scoreable_pair"] += 1
            continue
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
        ).fetchone():
            skips["known_ledger_hash"] += 1
            continue
        target = connection.execute(
            "SELECT team_count,minimum_disc_abs,discovered,generated_at "
            "FROM targets WHERE label=? AND r=?",
            pair,
        ).fetchone()
        if target is None:
            skips["target_missing"] += 1
            continue
        if digest in receipt_hashes:
            skips["receipt_hash"] += 1
            continue
        if pair in receipt_pairs:
            skips["receipt_pair"] += 1
            continue
        team_count = int(target[0])
        if team_count < 0:
            raise ValueError(f"negative target team count: {pair}")
        if not target[3]:
            raise ValueError(f"target has no generation timestamp: {pair}")
        if max_team_count is not None and team_count > max_team_count:
            skips["above_max_team_count"] += 1
            continue
        field_values = row["fieldDiscriminants"]
        row = {
            **row,
            "fieldDiscriminantAbs": next(iter(field_values)) if field_values else None,
            "target": {
                "discovered": bool(target[2]),
                "generatedAt": str(target[3]),
                "minimumDiscAbs": str(target[1]) if target[1] is not None else None,
                "teamCount": team_count,
            },
        }
        eligible.append(row)

    by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in eligible:
        by_pair[row["pair"]].append(row)
    selected = []
    alternatives = []
    for pair, rows in by_pair.items():
        field_complete = all(row["fieldDiscriminantAbs"] is not None for row in rows)
        if field_complete:
            basis = "field_discriminant_abs"
            key = lambda row: (
                row["fieldDiscriminantAbs"],
                row["polynomialDiscriminantAbs"],
                row["coefficientBytes"],
                row["coefficientSha256"],
            )
        else:
            basis = "polynomial_discriminant_abs_fallback"
            key = lambda row: (
                row["polynomialDiscriminantAbs"],
                row["coefficientBytes"],
                row["coefficientSha256"],
            )
        ordered = sorted(rows, key=key)
        winner = ordered[0]
        winner["dedupeBasis"] = basis
        winner["dedupeCandidateCount"] = len(rows)
        selected.append(winner)
        for loser in ordered[1:]:
            alternatives.append(
                {
                    "coefficientSha256": loser["coefficientSha256"],
                    "fieldDiscriminantAbs": (
                        str(loser["fieldDiscriminantAbs"])
                        if loser["fieldDiscriminantAbs"] is not None
                        else None
                    ),
                    "pair": f"{pair[0]}/r{pair[1]}",
                    "polynomialDiscriminantAbs": str(
                        loser["polynomialDiscriminantAbs"]
                    ),
                    "reason": f"deduped_by_{basis}",
                    "selectedCoefficientSha256": winner["coefficientSha256"],
                }
            )
    selected.sort(
        key=lambda row: (
            row["target"]["teamCount"],
            row["targetT"],
            row["pair"][1],
            row["coefficientSha256"],
        )
    )
    selected_hashes = [row["coefficientSha256"] for row in selected]
    if len(selected_hashes) != len(set(selected_hashes)):
        raise ValueError("one selected polynomial is assigned to multiple target pairs")
    return selected, alternatives, skips


def preflight_and_seal(outputs: dict[Path, bytes]) -> dict[str, str]:
    resolved = [path.expanduser().resolve() for path in outputs]
    if len(resolved) != len(set(resolved)):
        raise ValueError("sealed output paths must be distinct")
    for path, payload in outputs.items():
        if path.exists() and path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite nonidentical sealed output: {path}")
    statuses = {}
    for path, payload in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            statuses[str(path)] = "existing_identical"
            continue
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
            statuses[str(path)] = "created"
        finally:
            temporary.unlink(missing_ok=True)
    return statuses


def public_selected(row: dict) -> dict:
    score = Fraction(1, 2 ** row["target"]["teamCount"])
    return {
        "coefficientBytes": row["coefficientBytes"],
        "coefficientSha256": row["coefficientSha256"],
        "dedupeBasis": row["dedupeBasis"],
        "dedupeCandidateCount": row["dedupeCandidateCount"],
        "fieldDiscriminantAbs": (
            str(row["fieldDiscriminantAbs"])
            if row["fieldDiscriminantAbs"] is not None
            else None
        ),
        "pair": f"{row['pair'][0]}/r{row['pair'][1]}",
        "polynomialDiscriminantAbs": str(row["polynomialDiscriminantAbs"]),
        "projectedMarginalScore": float(score),
        "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
        "proofs": row["proofs"],
        "target": {
            **row["target"],
            "label": row["pair"][0],
            "r": row["pair"][1],
        },
    }


def stage(
    *,
    root: Path = ROOT,
    data_dir: Path = DATA,
    receipts_dir: Path = RECEIPTS,
    database: Path = DATABASE,
    manifest: Path = MANIFEST,
    certificate_output: Path = CERTIFICATE,
    summary_output: Path = SUMMARY,
    expected_certificates: int | None = DEFAULT_EXPECTED_CERTIFICATES,
    max_team_count: int | None = None,
) -> dict:
    root = root.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    receipts_dir = receipts_dir.expanduser().resolve()
    database = database.expanduser().resolve()
    manifest = manifest.expanduser().resolve()
    certificate_output = certificate_output.expanduser().resolve()
    summary_output = summary_output.expanduser().resolve()

    certificates, staged_pairs_by_manifest = scan_json_artifacts(data_dir)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        pool, certificate_audit, census = collect_exact_pool(
            certificates, connection, root, expected_certificates
        )
        receipt_hashes, receipt_pairs, receipt_audit = receipt_exclusions(
            connection,
            receipts_dir,
            staged_pairs_by_manifest,
            pool,
            root,
        )
        selected, alternatives, skips = filter_and_dedupe(
            pool,
            connection,
            receipt_hashes,
            receipt_pairs,
            max_team_count,
        )
        if not selected:
            raise ValueError("no exact current locally-unowned unreceipted pairs remain")

        score = sum(
            (Fraction(1, 2 ** row["target"]["teamCount"]) for row in selected),
            Fraction(0, 1),
        )
        k_distribution = Counter(row["target"]["teamCount"] for row in selected)
        target_generated = [row["target"]["generatedAt"] for row in selected]
        public_rows = [public_selected(row) for row in selected]
        manifest_payload = "".join(
            row["coefficientLine"] + "\n" for row in selected
        ).encode("ascii")
        manifest_sha = sha256_bytes(manifest_payload)
        script_path = Path(__file__).resolve()
        script_sha = sha256_path(script_path)

        certificate_value = {
            "certificateCensus": certificate_audit,
            "checks": {
                "allCertificateInputsPinnedByMatchingSha256": True,
                "allCandidatePayloadsAndHashesMatched": True,
                "allExactHashAssignmentsGloballyUnique": True,
                "allResolvedRowsHaveOneRemainingLabelAssignment": True,
                "allSourceLedgerKeysScoreableAndMatched": True,
                "allTargetsCurrent": True,
                "baselineOwnedKnownAndReceiptExclusionsPassed": True,
                "receiptManifestsAndPairsAudited": True,
                "sealedOutputsContainNoCoefficientPayloadExceptManifest": True,
            },
            "database": display_path(database, root),
            "duplicateAlternatives": sorted(
                alternatives, key=lambda row: (row["pair"], row["coefficientSha256"])
            ),
            "exactCensus": census,
            "manifest": {
                "bytes": len(manifest_payload),
                "path": display_path(manifest, root),
                "polynomials": len(selected),
                "sha256": manifest_sha,
            },
            "method": STAGE_METHOD,
            "networkCalls": 0,
            "projectedMarginalScore": float(score),
            "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
            "receiptExclusion": receipt_audit,
            "selected": public_rows,
            "selection": {
                "deduplicatedPairs": len(selected),
                "duplicateAlternativesExcluded": len(alternatives),
                "fieldDiscriminantPolicy": (
                    "use fieldDiscriminantAbs when present for every eligible payload "
                    "of a pair; otherwise use polynomialDiscriminantAbs, payload bytes, "
                    "then coefficientSha256"
                ),
                "kDistribution": {
                    str(key): value for key, value in sorted(k_distribution.items())
                },
                "maximumTeamCount": max_team_count,
                "sequentialSkipCounts": dict(sorted(skips.items())),
            },
            "stageScript": {
                "path": display_path(script_path, root),
                "sha256": script_sha,
            },
            "submissionCalls": 0,
            "targetSnapshot": {
                "generatedAtMax": max(target_generated),
                "generatedAtMin": min(target_generated),
                "targetTableRows": int(
                    connection.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
                ),
            },
        }
        certificate_payload = json_bytes(certificate_value)
        certificate_sha = sha256_bytes(certificate_payload)
        summary_value = {
            "certificate": display_path(certificate_output, root),
            "certificateSha256": certificate_sha,
            "duplicateAlternativesExcluded": len(alternatives),
            "kDistribution": {
                str(key): value for key, value in sorted(k_distribution.items())
            },
            "manifest": display_path(manifest, root),
            "manifestBytes": len(manifest_payload),
            "manifestSha256": manifest_sha,
            "networkCalls": 0,
            "polynomials": len(selected),
            "projectedMarginalScore": float(score),
            "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
            "selectedPairs": [
                {
                    "coefficientSha256": row["coefficientSha256"],
                    "pair": row["pair"],
                    "projectedMarginalScoreExact": row[
                        "projectedMarginalScoreExact"
                    ],
                    "teamCount": row["target"]["teamCount"],
                }
                for row in public_rows
            ],
            "sequentialSkipCounts": dict(sorted(skips.items())),
            "status": "sealed_not_submitted",
            "submissionCalls": 0,
        }
        summary_payload = json_bytes(summary_value)

    protected = {
        database,
        *[path for path, _ in certificates],
        *[
            resolve_saved_input(path, value.get("input"), root)
            for path, value in certificates
        ],
    }
    if {manifest, certificate_output, summary_output} & protected:
        raise ValueError("sealed output must not overwrite an input or the ledger")
    statuses = preflight_and_seal(
        {
            manifest: manifest_payload,
            certificate_output: certificate_payload,
            summary_output: summary_payload,
        }
    )
    return {
        "certificate": display_path(certificate_output, root),
        "certificateSha256": certificate_sha,
        "kDistribution": {
            str(key): value for key, value in sorted(k_distribution.items())
        },
        "manifest": display_path(manifest, root),
        "manifestSha256": manifest_sha,
        "outputs": {
            display_path(Path(path), root): status for path, status in statuses.items()
        },
        "polynomials": len(selected),
        "projectedMarginalScore": float(score),
        "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
        "status": "sealed_not_submitted",
        "summary": display_path(summary_output, root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=MANIFEST)
    parser.add_argument("--certificate-output", type=Path, default=CERTIFICATE)
    parser.add_argument("--summary-output", type=Path, default=SUMMARY)
    parser.add_argument(
        "--expected-certificates",
        type=int,
        default=DEFAULT_EXPECTED_CERTIFICATES,
    )
    parser.add_argument("--max-team-count", type=int)
    args = parser.parse_args()
    try:
        result = stage(
            root=args.root,
            data_dir=args.data,
            receipts_dir=args.receipts,
            database=args.database,
            manifest=args.output,
            certificate_output=args.certificate_output,
            summary_output=args.summary_output,
            expected_certificates=args.expected_certificates,
            max_team_count=args.max_team_count,
        )
    except (
        KeyError,
        OSError,
        ValueError,
        TypeError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

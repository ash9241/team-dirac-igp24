#!/usr/bin/env python3
"""Stage live golds from an exact Frobenius assignment certificate.

This script makes no network requests and never writes the ledger.  It joins
every resolved factor assignment back to the original certified-multi JSONL,
cross-checks coefficient hashes and target multisets, intersects the result
with the current local target/baseline/ownership snapshot, deduplicates target
pairs, and atomically writes an explicit manifest.
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
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_CANDIDATES = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
CERTIFICATE_METHOD = "exact-unramified-frobenius-cycle-type-exclusion-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid degree-24 transitive-group label: {label!r}")
    return int(match.group(1))


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def write_text_atomic(path: Path, text: str) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def source_key(row: dict) -> tuple[str, int, str, int]:
    return (
        str(row["sourceSubmissionId"]),
        int(row["sourcePolynomialIndex"]),
        str(row["sourceLabel"]),
        int(row["sourceR"]),
    )


def selected_rows_digest(rows: list[dict]) -> str:
    payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    return sha256_bytes(payload.encode("utf-8"))


def validated_coefficient_line(candidate: dict, source: tuple) -> str:
    line = str(candidate["coefficientLine"])
    digest = sha256_bytes(line.encode("utf-8"))
    if digest != str(candidate["coefficientSha256"]):
        raise ValueError(
            f"{source} factor {candidate.get('factorIndex')} coefficient hash mismatch"
        )
    try:
        coefficients = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError(f"{source} has a nonintegral coefficient line") from exc
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError(f"{source} candidate is not monic of degree 24")
    if coefficients[0] == 0 or math.gcd(*coefficients) != 1:
        raise ValueError(f"{source} candidate fails primitive/nonzero-constant checks")
    return line


def validate_certificate_header(
    certificate: dict,
    certificate_rows: list[dict],
    candidates_path: Path,
    allow_unresolved: bool = False,
) -> None:
    if certificate.get("method") != CERTIFICATE_METHOD:
        raise ValueError("certificate method is missing or unsupported")
    summary = certificate.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("certificate summary is missing")
    status_counts = Counter(str(row.get("status")) for row in certificate_rows)
    expected = {
        "rows": len(certificate_rows),
        "resolved": status_counts["resolved"],
        "unresolved": status_counts["unresolved"],
        "contradiction": status_counts["contradiction"],
    }
    for key, value in expected.items():
        if int(summary.get(key, -1)) != value:
            raise ValueError(
                f"certificate summary {key}={summary.get(key)!r}, expected {value}"
            )
    if expected["contradiction"]:
        raise ValueError("certificate contains a contradictory row")
    if expected["unresolved"] and not allow_unresolved:
        raise ValueError("certificate contains unresolved rows")
    unexpected = set(status_counts) - {"resolved", "unresolved", "contradiction"}
    if unexpected:
        raise ValueError(f"certificate contains unsupported statuses: {unexpected}")
    candidate_digest = sha256_bytes(candidates_path.read_bytes())
    if str(certificate.get("inputSha256")) != candidate_digest:
        raise ValueError(
            "certificate input SHA-256 does not match the original multi JSONL"
        )


def join_resolved_assignments(
    certificate: dict,
    candidate_rows: list[dict],
    candidates_path: Path,
    allow_unresolved: bool = False,
) -> list[dict]:
    certificate_rows = certificate.get("rows")
    if not isinstance(certificate_rows, list) or not certificate_rows:
        raise ValueError("certificate has no rows")
    validate_certificate_header(
        certificate, certificate_rows, candidates_path, allow_unresolved
    )

    candidate_by_key: dict[tuple, dict] = {}
    for row in candidate_rows:
        if row.get("status") != "certified_multi":
            continue
        key = source_key(row)
        if key in candidate_by_key:
            raise ValueError(f"duplicate certified-multi source key: {key}")
        candidate_by_key[key] = row

    seen_certificate_keys = set()
    selected_source_rows = []
    joined = []
    for certified in certificate_rows:
        key = source_key(certified)
        if key in seen_certificate_keys:
            raise ValueError(f"duplicate certificate source key: {key}")
        seen_certificate_keys.add(key)
        source = candidate_by_key.get(key)
        if source is None:
            raise ValueError(f"certificate source is absent from candidate JSONL: {key}")
        selected_source_rows.append(source)
        if certified.get("status") != "resolved":
            if allow_unresolved and certified.get("status") == "unresolved":
                continue
            raise ValueError(
                f"certificate row {certified.get('sourceLabel')} is not resolved"
            )

        candidates = {
            int(candidate["factorIndex"]): candidate
            for candidate in source.get("candidates") or []
        }
        if sorted(candidates) != list(range(len(candidates))) or not candidates:
            raise ValueError(f"{key} candidate factor indexes are invalid")
        assignments = certified.get("assignments")
        if not isinstance(assignments, list) or len(assignments) != len(candidates):
            raise ValueError(f"{key} assignment count does not match factor count")
        assignment_indexes = [int(row["factorIndex"]) for row in assignments]
        if sorted(assignment_indexes) != sorted(candidates):
            raise ValueError(f"{key} assignment factor indexes are not exact")

        assigned_labels = []
        for assignment in assignments:
            factor_index = int(assignment["factorIndex"])
            candidate = candidates[factor_index]
            candidate_hash = str(candidate["coefficientSha256"])
            if str(assignment["coefficientSha256"]) != candidate_hash:
                raise ValueError(f"{key} factor {factor_index} assignment hash mismatch")
            target_label = str(assignment["targetLabel"])
            target_t = label_t(target_label)
            if int(assignment["targetT"]) != target_t:
                raise ValueError(f"{key} factor {factor_index} target T mismatch")
            target_r = int(assignment["targetR"])
            if target_r != int(candidate["targetR"]):
                raise ValueError(f"{key} factor {factor_index} target r mismatch")
            line = validated_coefficient_line(candidate, key)
            assigned_labels.append(target_label)
            try:
                polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{key} factor {factor_index} has no valid polynomial discriminant"
                ) from exc
            if polynomial_disc <= 0:
                raise ValueError(f"{key} factor {factor_index} has invalid discriminant")
            joined.append(
                {
                    "sourceSubmissionId": key[0],
                    "sourcePolynomialIndex": key[1],
                    "sourceLabel": key[2],
                    "sourceR": key[3],
                    "factorIndex": factor_index,
                    "targetLabel": target_label,
                    "targetT": target_t,
                    "targetR": target_r,
                    "coefficientLine": line,
                    "coefficientSha256": candidate_hash,
                    "polynomialDiscriminantAbs": polynomial_disc,
                }
            )

        slot_labels = [
            str(slot["targetLabel"]) for slot in source.get("orbitTargets") or []
        ]
        if Counter(assigned_labels) != Counter(slot_labels):
            raise ValueError(f"{key} assigned target-label multiset is inconsistent")

    selected_digest = certificate.get("selectedInputRowsSha256")
    if selected_digest is not None and str(selected_digest) != selected_rows_digest(
        selected_source_rows
    ):
        raise ValueError("certificate selected-row SHA-256 does not match joined rows")
    return joined


def filter_live_gold(
    rows: list[dict], database: Path, include_shared: bool = False
) -> tuple[list[dict], Counter]:
    skips: Counter = Counter()
    eligible = []
    connection = sqlite3.connect(
        f"file:{database.expanduser().resolve()}?mode=ro", uri=True
    )
    try:
        for row in rows:
            pair = (row["targetLabel"], int(row["targetR"]))
            target = connection.execute(
                "SELECT team_count,generated_at FROM targets WHERE label=? AND r=?",
                pair,
            ).fetchone()
            if target is None:
                skips["target_missing"] += 1
                continue
            if int(target[0]) != 0 and not include_shared:
                skips["not_team_count_zero"] += 1
                continue
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()
            if baseline is not None:
                skips["baseline_pair"] += 1
                continue
            owned = connection.execute(
                """
                SELECT 1 FROM verifications
                WHERE label=? AND r=? AND scoreable=1 LIMIT 1
                """,
                pair,
            ).fetchone()
            if owned is not None:
                skips["locally_owned_pair"] += 1
                continue
            known_hash = connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (row["coefficientSha256"],),
            ).fetchone()
            if known_hash is not None:
                skips["known_polynomial_hash"] += 1
                continue
            eligible.append(
                {
                    **row,
                    "targetTeamCount": int(target[0]),
                    "targetGeneratedAt": target[1],
                }
            )
    finally:
        connection.close()

    best: dict[tuple[str, int], dict] = {}
    for row in eligible:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        incumbent = best.get(pair)
        key = (
            int(row["polynomialDiscriminantAbs"]),
            len(str(row["coefficientLine"])),
            str(row["coefficientSha256"]),
        )
        if incumbent is None:
            best[pair] = row
        else:
            incumbent_key = (
                int(incumbent["polynomialDiscriminantAbs"]),
                len(str(incumbent["coefficientLine"])),
                str(incumbent["coefficientSha256"]),
            )
            if key < incumbent_key:
                best[pair] = row
            skips["duplicate_target_pair"] += 1
    selected = sorted(
        best.values(),
        key=lambda row: (
            int(row["targetT"]),
            int(row["targetR"]),
            str(row["coefficientSha256"]),
        ),
    )
    hashes = [row["coefficientSha256"] for row in selected]
    if len(hashes) != len(set(hashes)):
        raise ValueError("one polynomial hash was assigned to multiple selected pairs")
    return selected, skips


def stage(
    certificate_path: Path,
    candidates_path: Path,
    database_path: Path,
    output_path: Path,
    allow_unresolved: bool = False,
    include_shared: bool = False,
) -> dict:
    protected = {
        certificate_path.expanduser().resolve(),
        candidates_path.expanduser().resolve(),
        database_path.expanduser().resolve(),
    }
    if output_path.expanduser().resolve() in protected:
        raise ValueError("manifest output must not overwrite an input or the ledger")
    certificate = read_json(certificate_path)
    candidate_rows = read_jsonl(candidates_path)
    joined = join_resolved_assignments(
        certificate, candidate_rows, candidates_path, allow_unresolved
    )
    selected, skips = filter_live_gold(joined, database_path, include_shared)
    if not selected:
        qualifier = "locally unowned pairs" if include_shared else (
            "nonbaseline, team_count=0, locally unowned pairs"
        )
        raise ValueError(f"no current {qualifier}")
    manifest_text = "".join(f"{row['coefficientLine']}\n" for row in selected)
    write_text_atomic(output_path, manifest_text)
    return {
        "certificate": str(certificate_path.expanduser().resolve()),
        "certificateSha256": sha256_bytes(certificate_path.read_bytes()),
        "candidates": str(candidates_path.expanduser().resolve()),
        "candidatesSha256": sha256_bytes(candidates_path.read_bytes()),
        "database": str(database_path.expanduser().resolve()),
        "joinedAssignments": len(joined),
        "skipCounts": dict(sorted(skips.items())),
        "selected": len(selected),
        "goldUpperBoundPoints": sum(
            int(row["targetTeamCount"]) == 0 for row in selected
        ),
        "projectedPoints": sum(
            1.0 / (int(row["targetTeamCount"]) + 1) for row in selected
        ),
        "manifest": str(output_path.expanduser().resolve()),
        "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
        "selectedPairs": [
            {
                "label": row["targetLabel"],
                "r": row["targetR"],
                "sourceLabel": row["sourceLabel"],
                "sourceR": row["sourceR"],
                "factorIndex": row["factorIndex"],
                "coefficientSha256": row["coefficientSha256"],
                "targetTeamCount": row["targetTeamCount"],
                "targetGeneratedAt": row["targetGeneratedAt"],
            }
            for row in selected
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="stage only resolved rows from a mixed resolved/unresolved certificate",
    )
    parser.add_argument(
        "--include-shared",
        action="store_true",
        help="include locally unowned nonbaseline pairs already held by other teams",
    )
    args = parser.parse_args()
    try:
        summary = stage(
            args.certificate,
            args.candidates,
            args.database,
            args.output,
            args.allow_unresolved,
            args.include_shared,
        )
    except (
        KeyError,
        OSError,
        ValueError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

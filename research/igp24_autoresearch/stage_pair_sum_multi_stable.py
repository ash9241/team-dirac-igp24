#!/usr/bin/env python3
"""Safely stage all locally unowned stable pair-sum multi factors.

This script does no arithmetic work and makes no network requests.  It
re-derives the stable factor-to-label assignments in the cached certified
multi results, validates their proof provenance and coefficient payloads,
then intersects them with the current read-only ledger.  Baseline pairs,
locally owned pairs, and already-known polynomial hashes are never staged.

When the target cache contains a pair with ``team_count = k``, the projected
addition is capped at ``2**(-k)`` after Dirac joins the pair.  This is an upper
bound because a worse field discriminant can reduce the actual score further.
A certified assignment whose target label is absent from a partial target
cache may still be staged, but its projection is explicitly left unknown.
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
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CANDIDATES = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
DEFAULT_SIGNATURES = ROOT / "data" / "pair_signature_map.jsonl"
DEFAULT_DATABASE = ROOT / "data" / "ledger.sqlite3"
DEFAULT_OUTPUT = ROOT / "outbox" / "pair_sum_multi_unowned.txt"
DEFAULT_SUMMARY = ROOT / "data" / "pair_sum_multi_unowned_summary.json"
PRODUCTION_MANIFEST = (ROOT / "outbox" / "pair_sum_multi_gold.txt").resolve()
PRODUCTION_SUMMARY = (ROOT / "data" / "pair_sum_multi_summary.json").resolve()
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
PROJECTION_FORMULA = "2^(-cached_team_count), before discriminant penalty"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid degree-24 transitive-group label: {label!r}")
    return int(match.group(1))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def load_signatures(path: Path) -> dict[str, dict]:
    signatures: dict[str, dict] = {}
    for row in read_jsonl(path):
        label = str(row.get("sourceLabel"))
        label_t(label)
        if label in signatures:
            raise ValueError(f"duplicate signature-map source label: {label}")
        signatures[label] = row
    return signatures


def write_text_atomic(path: Path, text: str) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
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


def validate_output_paths(
    candidates: Path,
    signatures: Path,
    database: Path,
    output: Path,
    summary: Path,
) -> None:
    inputs = {
        candidates.expanduser().resolve(),
        signatures.expanduser().resolve(),
        database.expanduser().resolve(),
    }
    outputs = {output.expanduser().resolve(), summary.expanduser().resolve()}
    if len(outputs) != 2:
        raise ValueError("manifest and summary paths must be distinct")
    if outputs & inputs:
        raise ValueError("an output path must not overwrite an input or the ledger")
    protected = {PRODUCTION_MANIFEST, PRODUCTION_SUMMARY}
    if outputs & protected:
        raise ValueError("refusing to overwrite pair-sum multi production outputs")


def source_key(row: dict) -> tuple[str, int, str, int]:
    return (
        str(row["sourceSubmissionId"]),
        int(row["sourcePolynomialIndex"]),
        str(row["sourceLabel"]),
        int(row["sourceR"]),
    )


def validated_coefficient(candidate: dict, source: tuple) -> tuple[str, int]:
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
        raise ValueError(
            f"{source} candidate fails primitive/nonzero-constant checks"
        )
    try:
        polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source} candidate has no valid discriminant") from exc
    if polynomial_disc <= 0:
        raise ValueError(f"{source} candidate has a nonpositive discriminant")
    return line, polynomial_disc


def validate_worker_certificate(row: dict, candidate_count: int, source: tuple) -> None:
    if int(row.get("workerExitCode", -1)) != 0:
        raise ValueError(f"{source} does not have a successful worker exit code")
    certificate = row.get("orbitCertificate")
    if not isinstance(certificate, dict):
        raise ValueError(f"{source} has no orbit factorization certificate")
    actual = [int(value) for value in certificate.get("actualDegrees") or []]
    expected = [int(value) for value in certificate.get("expectedDegrees") or []]
    exponents = [int(value) for value in certificate.get("exponents") or []]
    if not actual or actual != expected or len(exponents) != len(actual):
        raise ValueError(f"{source} has an inconsistent factorization certificate")
    if any(value != 1 for value in exponents):
        raise ValueError(f"{source} resolvent factorization is not squarefree")
    if actual.count(24) != candidate_count:
        raise ValueError(f"{source} degree-24 factor count is inconsistent")


def stable_assignments(result: dict, signature_row: dict) -> tuple[list[dict], dict]:
    """Re-derive only assignments invariant across every compatible profile."""

    source = source_key(result)
    if result.get("status") != "certified_multi":
        return [], {"reason": "source row is not certified_multi"}
    if str(signature_row.get("sourceLabel")) != source[2]:
        raise ValueError(f"{source} signature-map source label mismatch")
    if signature_row.get("status") != "certified":
        raise ValueError(f"{source} signature profile is not certified")

    candidates = result.get("candidates")
    orbit_targets = result.get("orbitTargets")
    if not isinstance(candidates, list) or len(candidates) <= 1:
        raise ValueError(f"{source} does not contain multiple degree-24 candidates")
    if not isinstance(orbit_targets, list) or len(orbit_targets) != len(candidates):
        raise ValueError(f"{source} orbit target count does not match candidates")
    indexes = [int(row["factorIndex"]) for row in candidates]
    if sorted(indexes) != list(range(len(candidates))) or len(set(indexes)) != len(
        indexes
    ):
        raise ValueError(f"{source} candidate factor indexes are invalid")
    if int(signature_row.get("length24OrbitCount", -1)) != len(candidates):
        raise ValueError(f"{source} signature-map orbit count is inconsistent")
    validate_worker_certificate(result, len(candidates), source)

    orbit_indexes = [int(row["orbitIndex"]) for row in orbit_targets]
    if len(set(orbit_indexes)) != len(orbit_indexes):
        raise ValueError(f"{source} has duplicate orbit indexes")
    orbit_labels = Counter(str(row["targetLabel"]) for row in orbit_targets)
    for target in orbit_targets:
        target_label = str(target["targetLabel"])
        if int(target["targetT"]) != label_t(target_label):
            raise ValueError(f"{source} orbit target label/T mismatch")
        if int(target.get("orbitSize", 24)) != 24:
            raise ValueError(f"{source} contains a non-degree-24 orbit target")

    actual_r = sorted(int(row["targetR"]) for row in candidates)
    if any(value not in range(0, 25, 2) for value in actual_r):
        raise ValueError(f"{source} candidate has an invalid real-root count")
    for candidate in candidates:
        validated_coefficient(candidate, source)

    compatible = []
    for profile in signature_row.get("profiles") or []:
        if int(profile["sourceR"]) != source[3]:
            continue
        signatures = profile.get("orbitSignatures") or []
        profile_indexes = [int(row["orbitIndex"]) for row in signatures]
        if sorted(profile_indexes) != sorted(orbit_indexes):
            raise ValueError(f"{source} profile orbit indexes are inconsistent")
        if Counter(str(row["targetLabel"]) for row in signatures) != orbit_labels:
            raise ValueError(f"{source} profile target labels are inconsistent")
        predicted_r = sorted(int(row["targetR"]) for row in signatures)
        if predicted_r == actual_r:
            compatible.append(profile)

    proof: dict = {
        "actualFactorR": actual_r,
        "compatibleClassIndexes": [
            int(row["classIndex"]) for row in compatible
        ],
    }
    if not compatible:
        proof["reason"] = "no complex-conjugation profile matches factor signatures"
        _validate_stored_proof(result, proof, source)
        return [], proof

    assignments = []
    for target_r in sorted(set(actual_r)):
        label_multisets = []
        for profile in compatible:
            labels = sorted(
                str(row["targetLabel"])
                for row in profile["orbitSignatures"]
                if int(row["targetR"]) == target_r
            )
            label_multisets.append(labels)
        if any(labels != label_multisets[0] for labels in label_multisets[1:]):
            continue
        labels = label_multisets[0]
        if len(set(labels)) != 1:
            continue
        label = labels[0]
        factors = [
            row for row in candidates if int(row["targetR"]) == target_r
        ]
        if len(factors) != len(labels):
            raise ValueError(f"{source} factor/profile multiplicity mismatch")
        for factor in factors:
            assignments.append(
                {**factor, "targetLabel": label, "targetR": target_r}
            )
    proof["assignedFactors"] = len(assignments)
    proof["totalFactors"] = len(candidates)
    _validate_stored_proof(result, proof, source)
    return assignments, proof


def _validate_stored_proof(result: dict, proof: dict, source: tuple) -> None:
    stored = result.get("assignmentProof")
    if not isinstance(stored, dict):
        raise ValueError(f"{source} has no stored stable-assignment proof")
    for key, expected in proof.items():
        if stored.get(key) != expected:
            raise ValueError(
                f"{source} stored assignment proof disagrees on {key}"
            )


def derive_assignments(
    candidate_rows: list[dict], signatures: dict[str, dict]
) -> tuple[list[dict], dict]:
    assigned = []
    seen_sources = set()
    certified_rows = 0
    stable_rows = 0
    missing_signature_rows = 0
    for result in candidate_rows:
        if result.get("status") != "certified_multi":
            continue
        certified_rows += 1
        source = source_key(result)
        if source in seen_sources:
            raise ValueError(f"duplicate certified-multi source row: {source}")
        seen_sources.add(source)
        signature = signatures.get(source[2])
        if signature is None:
            # A partial signature map cannot certify this source, but it does
            # not invalidate independently certified sources that are present.
            missing_signature_rows += 1
            continue
        factors, proof = stable_assignments(result, signature)
        if factors:
            stable_rows += 1
        for factor in factors:
            line, polynomial_disc = validated_coefficient(factor, source)
            target_label = str(factor["targetLabel"])
            assigned.append(
                {
                    "sourceSubmissionId": source[0],
                    "sourcePolynomialIndex": source[1],
                    "sourceLabel": source[2],
                    "sourceR": source[3],
                    "factorIndex": int(factor["factorIndex"]),
                    "targetLabel": target_label,
                    "targetT": label_t(target_label),
                    "targetR": int(factor["targetR"]),
                    "coefficientLine": line,
                    "coefficientSha256": str(factor["coefficientSha256"]),
                    "polynomialDiscriminantAbs": polynomial_disc,
                    "assignmentProof": proof,
                }
            )
    return assigned, {
        "inputRows": len(candidate_rows),
        "certifiedMultiRows": certified_rows,
        "missingSignatureRowsSkipped": missing_signature_rows,
        "stableSourceRows": stable_rows,
        "assignedFactors": len(assigned),
    }


def read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.expanduser().resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def filter_current(
    assigned: list[dict], database: Path, max_team_count: int | None = None
) -> tuple[list[dict], Counter, dict]:
    skips: Counter = Counter()
    eligible = []
    connection = read_only_connection(database)
    try:
        target_cache = connection.execute(
            """
            SELECT COUNT(*) AS pairs, COUNT(DISTINCT label) AS labels,
                   MIN(t) AS min_t, MAX(t) AS max_t, MAX(generated_at) AS generated_at
            FROM targets
            """
        ).fetchone()
        for row in assigned:
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            if connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone() is not None:
                skips["baseline_pair"] += 1
                continue
            if connection.execute(
                """
                SELECT 1 FROM verifications
                WHERE label=? AND r=? AND scoreable=1 LIMIT 1
                """,
                pair,
            ).fetchone() is not None:
                skips["locally_owned_pair"] += 1
                continue
            if connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (row["coefficientSha256"],),
            ).fetchone() is not None:
                skips["known_polynomial_hash"] += 1
                continue

            target = connection.execute(
                """
                SELECT t,team_count,minimum_disc_abs,discovered,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                pair,
            ).fetchone()
            if target is None:
                label_cached = connection.execute(
                    "SELECT 1 FROM targets WHERE label=? LIMIT 1", (pair[0],)
                ).fetchone()
                if label_cached is not None:
                    skips["target_signature_missing_in_cache"] += 1
                    continue
                annotated = {
                    **row,
                    "targetCacheKnown": False,
                    "targetTeamCount": None,
                    "targetGeneratedAt": None,
                    "projectedMarginalScore": None,
                    "projectedMarginalScoreExact": None,
                }
            else:
                if int(target["t"]) != int(row["targetT"]):
                    raise ValueError(f"target cache label/T mismatch for {pair}")
                team_count = int(target["team_count"])
                if team_count < 0:
                    raise ValueError(f"negative cached team count for {pair}")
                if max_team_count is not None and team_count > max_team_count:
                    skips["target_above_team_count_cap"] += 1
                    continue
                projection = Fraction(1, 2**team_count)
                annotated = {
                    **row,
                    "targetCacheKnown": True,
                    "targetTeamCount": team_count,
                    "targetMinimumDiscAbs": (
                        str(target["minimum_disc_abs"])
                        if target["minimum_disc_abs"] is not None
                        else None
                    ),
                    "targetDiscovered": bool(target["discovered"]),
                    "targetGeneratedAt": target["generated_at"],
                    "projectedMarginalScore": float(projection),
                    "projectedMarginalScoreExact": str(projection),
                }
            eligible.append(annotated)
    finally:
        connection.close()

    best: dict[tuple[str, int], dict] = {}
    for row in eligible:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        key = (
            int(row["polynomialDiscriminantAbs"]),
            len(str(row["coefficientLine"])),
            str(row["coefficientSha256"]),
        )
        incumbent = best.get(pair)
        if incumbent is None:
            best[pair] = row
            continue
        skips["duplicate_target_pair"] += 1
        incumbent_key = (
            int(incumbent["polynomialDiscriminantAbs"]),
            len(str(incumbent["coefficientLine"])),
            str(incumbent["coefficientSha256"]),
        )
        if key < incumbent_key:
            best[pair] = row

    selected = sorted(
        best.values(),
        key=lambda row: (
            int(row["targetT"]),
            int(row["targetR"]),
            str(row["coefficientSha256"]),
        ),
    )
    hashes = [str(row["coefficientSha256"]) for row in selected]
    if len(hashes) != len(set(hashes)):
        raise ValueError("one polynomial hash is assigned to multiple selected pairs")
    lines = [str(row["coefficientLine"]) for row in selected]
    if len(lines) != len(set(lines)):
        raise ValueError("duplicate coefficient lines remain after pair deduplication")

    cache_summary = {
        "pairs": int(target_cache["pairs"]),
        "labels": int(target_cache["labels"]),
        "minT": (
            int(target_cache["min_t"])
            if target_cache["min_t"] is not None
            else None
        ),
        "maxT": (
            int(target_cache["max_t"])
            if target_cache["max_t"] is not None
            else None
        ),
        "generatedAt": target_cache["generated_at"],
    }
    return selected, skips, cache_summary


def score_projection(selected: list[dict]) -> dict:
    known = [row for row in selected if row["targetTeamCount"] is not None]
    unknown = len(selected) - len(known)
    total = sum(
        (Fraction(1, 2 ** int(row["targetTeamCount"])) for row in known),
        Fraction(0, 1),
    )
    upper = total + unknown
    distribution = Counter(int(row["targetTeamCount"]) for row in known)
    return {
        "formula": PROJECTION_FORMULA,
        "knownPairs": len(known),
        "unknownPairs": unknown,
        "teamCountDistribution": {
            str(key): distribution[key] for key in sorted(distribution)
        },
        "knownMarginalScore": float(total),
        "knownMarginalScoreExact": str(total),
        "upperBoundIncludingUnknown": float(upper),
        "upperBoundIncludingUnknownExact": str(upper),
    }


def stage(
    candidates_path: Path,
    signatures_path: Path,
    database_path: Path,
    output_path: Path,
    summary_path: Path,
    max_team_count: int | None = None,
) -> dict:
    validate_output_paths(
        candidates_path,
        signatures_path,
        database_path,
        output_path,
        summary_path,
    )
    candidate_rows = read_jsonl(candidates_path)
    signatures = load_signatures(signatures_path)
    assigned, assignment_audit = derive_assignments(candidate_rows, signatures)
    selected, skips, target_cache = filter_current(
        assigned, database_path, max_team_count=max_team_count
    )
    if not selected:
        raise ValueError("no stable, nonbaseline, locally unowned factors remain")

    manifest_text = "".join(f"{row['coefficientLine']}\n" for row in selected)
    projection = score_projection(selected)
    summary = {
        "candidates": str(candidates_path.expanduser().resolve()),
        "candidatesSha256": sha256_bytes(candidates_path.read_bytes()),
        "signatures": str(signatures_path.expanduser().resolve()),
        "signaturesSha256": sha256_bytes(signatures_path.read_bytes()),
        "database": str(database_path.expanduser().resolve()),
        **assignment_audit,
        "skipCounts": dict(sorted(skips.items())),
        "selected": len(selected),
        "targetCache": target_cache,
        "scoreProjection": projection,
        "manifest": str(output_path.expanduser().resolve()),
        "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
        "summary": str(summary_path.expanduser().resolve()),
        "selectedPairs": [
            {
                "label": row["targetLabel"],
                "r": row["targetR"],
                "sourceLabel": row["sourceLabel"],
                "sourceR": row["sourceR"],
                "sourceSubmissionId": row["sourceSubmissionId"],
                "sourcePolynomialIndex": row["sourcePolynomialIndex"],
                "factorIndex": row["factorIndex"],
                "coefficientSha256": row["coefficientSha256"],
                "polynomialDiscriminantAbs": str(
                    row["polynomialDiscriminantAbs"]
                ),
                "targetCacheKnown": row["targetCacheKnown"],
                "teamCount": row["targetTeamCount"],
                "projectedMarginalScore": row["projectedMarginalScore"],
                "projectedMarginalScoreExact": row[
                    "projectedMarginalScoreExact"
                ],
                "targetGeneratedAt": row["targetGeneratedAt"],
            }
            for row in selected
        ],
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    write_text_atomic(summary_path, summary_text)
    write_text_atomic(output_path, manifest_text)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--signatures", type=Path, default=DEFAULT_SIGNATURES)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--max-team-count", type=int)
    args = parser.parse_args()
    if args.max_team_count is not None and args.max_team_count < 0:
        parser.error("--max-team-count must be nonnegative")
    try:
        summary = stage(
            args.candidates,
            args.signatures,
            args.database,
            args.output,
            args.summary,
            args.max_team_count,
        )
    except (
        KeyError,
        OSError,
        TypeError,
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

#!/usr/bin/env python3
"""Audit and stage exact single-orbit pair-sum factors that Dirac does not own.

The arithmetic work was already certified by ``pair_sum_one.sage.py`` and
retained in ``data/pair_sum_candidates.jsonl``.  This stager revalidates the
worker certificates and coefficient payloads, then intersects them with the
current read-only ledger.  It excludes baseline pairs, locally owned pairs,
known polynomial hashes, and matching local submission receipts that may not
yet have reached the ledger.

An optional all-row Frobenius certificate supplies a hash-to-pair map for
in-flight multi-orbit manifests.  That lets this lane exclude target-pair
collisions with a concurrently submitted multi-orbit wave, not merely exact
coefficient duplicates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CANDIDATES = ROOT / "data" / "pair_sum_candidates.jsonl"
DEFAULT_MULTI_CANDIDATES = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
DEFAULT_DATABASE = ROOT / "data" / "ledger.sqlite3"
DEFAULT_RECEIPTS = ROOT / "receipts"
DEFAULT_OUTPUT = ROOT / "outbox" / "pair_sum_single_unowned.txt"
DEFAULT_SUMMARY = ROOT / "data" / "pair_sum_single_unowned_summary.json"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(row)
    return rows


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid degree-24 transitive-group label: {label!r}")
    return int(match.group(1))


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
    database: Path,
    output: Path,
    summary: Path,
) -> None:
    inputs = {candidates.resolve(), database.resolve()}
    outputs = {output.resolve(), summary.resolve()}
    if len(outputs) != 2:
        raise ValueError("manifest and summary paths must be distinct")
    if inputs & outputs:
        raise ValueError("an output path must not overwrite an input or the ledger")


def validate_candidate(row: dict, line_number: int) -> dict:
    context = f"candidate row {line_number}"
    if row.get("status") != "certified":
        raise ValueError(f"{context} is not certified")
    if int(row.get("workerExitCode", -1)) != 0:
        raise ValueError(f"{context} has a nonzero worker exit code")

    certificate = row.get("orbitCertificate")
    if not isinstance(certificate, dict):
        raise ValueError(f"{context} has no orbit factorization certificate")
    actual = [int(value) for value in certificate.get("actualDegrees") or []]
    expected = [int(value) for value in certificate.get("expectedDegrees") or []]
    exponents = [int(value) for value in certificate.get("exponents") or []]
    if not actual or actual != expected or len(exponents) != len(actual):
        raise ValueError(f"{context} has an inconsistent factorization certificate")
    if any(value != 1 for value in exponents) or actual.count(24) != 1:
        raise ValueError(f"{context} is not a squarefree single degree-24 action")

    orbit_targets = row.get("orbitTargets")
    if not isinstance(orbit_targets, list) or len(orbit_targets) != 1:
        raise ValueError(f"{context} does not have exactly one orbit target")
    target = orbit_targets[0]
    label = str(row["targetLabel"])
    target_t = label_t(label)
    target_r = int(row["targetR"])
    if not 0 <= target_r <= 24 or target_r % 2:
        raise ValueError(f"{context} has an invalid real-root count")
    if (
        str(target.get("targetLabel")) != label
        or int(target.get("targetT", -1)) != target_t
        or int(target.get("orbitSize", -1)) != 24
        or int(row.get("targetT", -1)) != target_t
    ):
        raise ValueError(f"{context} target metadata is inconsistent")

    line = str(row["coefficientLine"])
    digest = sha256_bytes(line.encode("utf-8"))
    if digest != str(row["coefficientSha256"]):
        raise ValueError(f"{context} coefficient hash mismatch")
    try:
        coefficients = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError(f"{context} contains a nonintegral coefficient") from exc
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError(f"{context} is not monic of degree 24")
    if coefficients[0] == 0 or math.gcd(*coefficients) != 1:
        raise ValueError(f"{context} is not primitive with nonzero constant term")
    try:
        polynomial_discriminant = int(row["polynomialDiscriminantAbs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{context} has no valid polynomial discriminant") from exc
    if polynomial_discriminant <= 0:
        raise ValueError(f"{context} has a nonpositive polynomial discriminant")

    return {
        **row,
        "targetLabel": label,
        "targetT": target_t,
        "targetR": target_r,
        "coefficientLine": line,
        "coefficientSha256": digest,
        "polynomialDiscriminantAbs": polynomial_discriminant,
    }


def frobenius_pair_index(
    certificate_path: Path | None, multi_candidates_path: Path
) -> dict[str, set[tuple[str, int]]]:
    if certificate_path is None:
        return {}
    # Reuse the production certificate joiner so the input digest, method,
    # assignment multiplicities, factor hashes, and target labels are checked
    # identically in both staging lanes.
    from stage_frobenius_gold import (
        join_resolved_assignments,
        read_json,
        read_jsonl as read_multi_jsonl,
    )

    certificate = read_json(certificate_path)
    candidates = read_multi_jsonl(multi_candidates_path)
    joined = join_resolved_assignments(
        certificate,
        candidates,
        multi_candidates_path,
        allow_unresolved=True,
    )
    result: dict[str, set[tuple[str, int]]] = {}
    for row in joined:
        result.setdefault(str(row["coefficientSha256"]), set()).add(
            (str(row["targetLabel"]), int(row["targetR"]))
        )
    return result


def matching_receipt_exclusions(
    receipts_dir: Path,
    pair_index: dict[str, set[tuple[str, int]]],
) -> tuple[set[str], set[tuple[str, int]], list[dict]]:
    hashes: set[str] = set()
    pairs: set[tuple[str, int]] = set()
    audit = []
    if not receipts_dir.exists():
        return hashes, pairs, audit

    for receipt_path in sorted(receipts_dir.glob("sub_*.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read receipt {receipt_path}: {exc}") from exc
        manifest_value = receipt.get("manifest")
        recorded_hash = receipt.get("manifestHash")
        submission_id = str(
            (receipt.get("response") or {}).get("submissionId")
            or receipt_path.stem
        )
        entry = {
            "submissionId": submission_id,
            "receipt": str(receipt_path.resolve()),
            "manifest": str(manifest_value) if manifest_value else None,
            "recordedManifestSha256": str(recorded_hash) if recorded_hash else None,
            "used": False,
        }
        if not manifest_value or not recorded_hash:
            entry["reason"] = "receipt_has_no_manifest_provenance"
            audit.append(entry)
            continue
        manifest = Path(str(manifest_value)).expanduser().resolve()
        if not manifest.exists():
            entry["reason"] = "manifest_missing"
            audit.append(entry)
            continue
        raw = manifest.read_bytes()
        actual_hash = sha256_bytes(raw)
        entry["currentManifestSha256"] = actual_hash
        if actual_hash != str(recorded_hash):
            entry["reason"] = "manifest_replaced_since_receipt"
            audit.append(entry)
            continue

        receipt_hashes = set()
        for line_number, raw_line in enumerate(
            raw.decode("utf-8").splitlines(), start=1
        ):
            # Submission manifests accept trailing ``# ...`` annotations.
            # Hash the same canonical coefficient payload that sair_api.py
            # submitted, rather than treating an annotation as coefficient
            # text.
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                values = [int(value) for value in line.split(",")]
            except ValueError as exc:
                raise ValueError(
                    f"nonintegral receipt manifest line {manifest}:{line_number}"
                ) from exc
            canonical = ",".join(str(value) for value in values)
            receipt_hashes.add(sha256_bytes(canonical.encode("utf-8")))
        receipt_pairs = {
            pair
            for digest in receipt_hashes
            for pair in pair_index.get(digest, set())
        }
        hashes.update(receipt_hashes)
        pairs.update(receipt_pairs)
        entry.update(
            {
                "used": True,
                "polynomialHashes": len(receipt_hashes),
                "mappedTargetPairs": len(receipt_pairs),
            }
        )
        audit.append(entry)
    return hashes, pairs, audit


def stage(
    candidates_path: Path,
    multi_candidates_path: Path,
    certificate_path: Path | None,
    database_path: Path,
    receipts_dir: Path,
    output_path: Path,
    summary_path: Path,
    max_team_count: int | None = None,
) -> dict:
    validate_output_paths(candidates_path, database_path, output_path, summary_path)
    validated = [
        validate_candidate(row, index)
        for index, row in enumerate(read_jsonl(candidates_path), start=1)
    ]

    pair_index: dict[str, set[tuple[str, int]]] = {}
    for row in validated:
        pair_index.setdefault(row["coefficientSha256"], set()).add(
            (row["targetLabel"], row["targetR"])
        )
    for digest, pairs in frobenius_pair_index(
        certificate_path, multi_candidates_path
    ).items():
        pair_index.setdefault(digest, set()).update(pairs)

    receipt_hashes, receipt_pairs, receipt_audit = matching_receipt_exclusions(
        receipts_dir, pair_index
    )
    skips: Counter = Counter()
    eligible = []
    connection = sqlite3.connect(
        f"file:{database_path.expanduser().resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        for row in validated:
            pair = (row["targetLabel"], row["targetR"])
            if row["coefficientSha256"] in receipt_hashes:
                skips["inflight_receipt_hash"] += 1
                continue
            if pair in receipt_pairs:
                skips["inflight_receipt_pair"] += 1
                continue
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
                SELECT t,team_count,minimum_disc_abs,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                pair,
            ).fetchone()
            if target is None:
                skips["target_missing"] += 1
                continue
            if int(target["t"]) != int(row["targetT"]):
                raise ValueError(f"target cache label/T mismatch for {pair}")
            team_count = int(target["team_count"])
            if team_count < 0:
                raise ValueError(f"negative target team count for {pair}")
            if max_team_count is not None and team_count > max_team_count:
                skips["target_above_team_count_cap"] += 1
                continue
            projection = Fraction(1, 2**team_count)
            eligible.append(
                {
                    **row,
                    "targetTeamCount": team_count,
                    "targetGeneratedAt": target["generated_at"],
                    "targetMinimumDiscAbs": (
                        str(target["minimum_disc_abs"])
                        if target["minimum_disc_abs"] is not None
                        else None
                    ),
                    "projectedMarginalScore": float(projection),
                    "projectedMarginalScoreExact": str(projection),
                }
            )
    finally:
        connection.close()

    best: dict[tuple[str, int], dict] = {}
    for row in eligible:
        pair = (row["targetLabel"], row["targetR"])
        key = (
            int(row["polynomialDiscriminantAbs"]),
            len(row["coefficientLine"]),
            row["coefficientSha256"],
        )
        incumbent = best.get(pair)
        if incumbent is None:
            best[pair] = row
            continue
        skips["duplicate_target_pair"] += 1
        incumbent_key = (
            int(incumbent["polynomialDiscriminantAbs"]),
            len(incumbent["coefficientLine"]),
            incumbent["coefficientSha256"],
        )
        if key < incumbent_key:
            best[pair] = row

    selected = sorted(
        best.values(), key=lambda row: (row["targetT"], row["targetR"])
    )
    lines = [row["coefficientLine"] for row in selected]
    if len(lines) != len(set(lines)):
        raise ValueError("one coefficient line remains assigned to multiple pairs")
    if not selected:
        raise ValueError("no exact locally unowned single-orbit candidates remain")

    projection = sum(
        (Fraction(1, 2 ** row["targetTeamCount"]) for row in selected),
        Fraction(0, 1),
    )
    distribution = Counter(row["targetTeamCount"] for row in selected)
    manifest_text = "".join(f"{line}\n" for line in lines)
    summary = {
        "input": str(candidates_path.resolve()),
        "inputSha256": sha256_bytes(candidates_path.read_bytes()),
        "inputRows": len(validated),
        "database": str(database_path.resolve()),
        "receiptsDirectory": str(receipts_dir.resolve()),
        "frobeniusCertificate": (
            str(certificate_path.resolve()) if certificate_path is not None else None
        ),
        "receiptAudit": receipt_audit,
        "inflightReceiptHashes": len(receipt_hashes),
        "inflightReceiptPairs": len(receipt_pairs),
        "skipCounts": dict(sorted(skips.items())),
        "selected": len(selected),
        "scoreProjection": {
            "formula": "2^(-cached_team_count), before discriminant penalty",
            "marginalScore": float(projection),
            "marginalScoreExact": str(projection),
            "teamCountDistribution": {
                str(key): distribution[key] for key in sorted(distribution)
            },
        },
        "manifest": str(output_path.resolve()),
        "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
        "summary": str(summary_path.resolve()),
        "selectedPairs": [
            {
                "label": row["targetLabel"],
                "r": row["targetR"],
                "sourceLabel": row["sourceLabel"],
                "sourceR": row["sourceR"],
                "sourceSubmissionId": row["sourceSubmissionId"],
                "sourcePolynomialIndex": row["sourcePolynomialIndex"],
                "coefficientSha256": row["coefficientSha256"],
                "polynomialDiscriminantAbs": str(
                    row["polynomialDiscriminantAbs"]
                ),
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
    write_text_atomic(output_path, manifest_text)
    write_text_atomic(
        summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument(
        "--multi-candidates", type=Path, default=DEFAULT_MULTI_CANDIDATES
    )
    parser.add_argument("--frobenius-certificate", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--max-team-count", type=int)
    args = parser.parse_args()
    if args.max_team_count is not None and args.max_team_count < 0:
        parser.error("--max-team-count must be nonnegative")
    try:
        summary = stage(
            args.candidates,
            args.multi_candidates,
            args.frobenius_certificate,
            args.database,
            args.receipts,
            args.output,
            args.summary,
            args.max_team_count,
        )
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

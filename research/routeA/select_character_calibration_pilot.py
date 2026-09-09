#!/usr/bin/env python3
"""Select one safe, uncommitted pilot for each uncalibrated character key.

A character key is ``(base_t, norm_squareclass, predicted_target_t)``.  The
server result for one valid polynomial calibrates that key even when the
server's transitive-group label differs from the local prediction.  Existing
ownership is therefore diagnostic only: an owned predicted ``(T, r)`` must
not suppress a calibration seed.

This module is a local, read-only selector.  It never persists a batch or
submits a polynomial.  Candidate validation is fail-closed and output files
are replaced atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, candidate_hash, canonical_coefficients


CharacterKey = tuple[int, int, int]
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_DISC_KEYS = ("field_disc_abs", "nfdisc_abs", "estimated_nfdisc_abs")


@dataclass(frozen=True)
class CandidateRecord:
    row: Mapping[str, Any]
    candidate_hash: str
    coefficients: str
    key: CharacterKey
    target_t: int
    target_r: int
    local_root_count: int
    disc_abs: int
    selectable: bool
    rejection_reason: str | None

    @property
    def identity(self) -> tuple[Any, ...]:
        return (
            self.coefficients,
            self.key,
            self.target_t,
            self.target_r,
            self.local_root_count,
            self.disc_abs,
        )


@dataclass(frozen=True)
class AcceptedVerification:
    candidate_hash: str
    verified_t: int
    verified_r: int
    submission_id: str


def load_candidate_rows(
    paths: Sequence[str | Path],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        counts["source_files"] += 1
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                counts["input_rows"] += 1
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path}:{line_number}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"candidate row is not an object in {path}:{line_number}")
                row = dict(value)
                row["_pilot_source_path"] = str(path)
                row["_pilot_source_line"] = line_number
                rows.append(row)
    return rows, counts


def validate_candidate(row: Mapping[str, Any]) -> CandidateRecord:
    """Validate immutable polynomial/key metadata, then apply proof gates."""

    location = _row_location(row)
    try:
        coefficients = canonical_coefficients(row["coefficients"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid degree-24 coefficients at {location}") from exc
    computed_hash = candidate_hash(coefficients)
    supplied_hash = str(row.get("candidate_hash") or "")
    if _HASH_RE.fullmatch(supplied_hash) is None:
        raise ValueError(f"candidate has no canonical hash at {location}")
    if supplied_hash != computed_hash:
        raise ValueError(f"candidate hash mismatch at {location}: {supplied_hash}")

    base_t = _consistent_int(row, "base_t", nested="parameters")
    norm_squareclass = _consistent_int(
        row,
        "norm_squareclass",
        nested="parameters",
        aliases=("expected_norm_squareclass",),
    )
    target_t = _consistent_int(row, "target_t", nested="parameters")
    predicted_target_t = _optional_int(row.get("predicted_target_t"))
    if predicted_target_t is None:
        predicted_target_t = target_t
    try:
        target_r = int(row["target_r"])
        local_root_count = int(row["local_root_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"candidate has no accepted root metadata at {location}") from exc
    if base_t < 1 or target_t < 1 or predicted_target_t < 1:
        raise ValueError(f"candidate has invalid transitive label metadata at {location}")
    if target_r < 0 or target_r > 24 or target_r % 2:
        raise ValueError(f"candidate has invalid degree-24 root count at {location}")
    if local_root_count != target_r:
        raise ValueError(f"candidate local/target root mismatch at {location}")
    disc_abs = _exact_disc_abs(row, location=location)

    reason: str | None = None
    if row.get("local_irreducible") is not True:
        reason = "not_locally_irreducible"
    elif row.get("submission_ready") is not True:
        reason = "not_submission_ready"
    elif row.get("exact_compatibility_proven") is not True:
        reason = "not_exactly_classified"
    elif not _accepted_validity(row.get("valid_probability")):
        reason = "validity_not_accepted"
    elif predicted_target_t != target_t:
        # A relabeled row can document an old calibration, but it must not be
        # selected as a fresh pilot under a different submitted target label.
        reason = "predicted_target_mismatch"

    return CandidateRecord(
        row=dict(row),
        candidate_hash=computed_hash,
        coefficients=coefficients,
        key=(base_t, norm_squareclass, predicted_target_t),
        target_t=target_t,
        target_r=target_r,
        local_root_count=local_root_count,
        disc_abs=disc_abs,
        selectable=reason is None,
        rejection_reason=reason,
    )


def _accepted_validity(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return False
    return probability == 1.0


def _consistent_int(
    row: Mapping[str, Any],
    name: str,
    *,
    nested: str | None = None,
    aliases: Sequence[str] = (),
) -> int:
    values: list[tuple[str, int]] = []
    for key in (name, *aliases):
        value = _optional_int(row.get(key))
        if value is not None:
            values.append((key, value))
    if nested is not None:
        child = row.get(nested)
        if isinstance(child, Mapping):
            value = _optional_int(child.get(name))
            if value is not None:
                values.append((f"{nested}.{name}", value))
    if not values:
        raise ValueError(f"row has no integer {name} at {_row_location(row)}")
    unique = {value for _, value in values}
    if len(unique) != 1:
        details = ", ".join(f"{key}={value}" for key, value in values)
        raise ValueError(f"conflicting {name} metadata at {_row_location(row)}: {details}")
    return values[0][1]


def _exact_disc_abs(row: Mapping[str, Any], *, location: str) -> int:
    values: list[tuple[str, int]] = []
    for key in _DISC_KEYS:
        raw = row.get(key)
        if raw is None:
            continue
        try:
            parsed = abs(int(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid field discriminant {key} at {location}") from exc
        if parsed <= 1:
            raise ValueError(f"invalid field discriminant {key} at {location}")
        values.append((key, parsed))
    if not values:
        raise ValueError(f"candidate has no accepted field discriminant at {location}")
    unique = {value for _, value in values}
    if len(unique) != 1:
        raise ValueError(f"candidate has conflicting field discriminants at {location}")
    return values[0][1]


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_location(row: Mapping[str, Any]) -> str:
    return f"{row.get('_pilot_source_path', '<memory>')}:{row.get('_pilot_source_line', '?')}"


def load_ledger_state(
    db_path: str | Path,
) -> tuple[
    set[str],
    list[AcceptedVerification],
    set[tuple[int, int]],
    dict[str, int],
]:
    """Read committed hashes, accepted labels, and ownership without writes."""

    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        committed = {
            str(row["candidate_hash"])
            for row in connection.execute(
                """SELECT DISTINCT si.candidate_hash
                   FROM submission_item si
                   JOIN submission s USING(batch_uuid)
                   WHERE s.dry_run=0
                   UNION
                   SELECT DISTINCT candidate_hash FROM verification"""
            )
        }
        accepted_by_hash: dict[str, AcceptedVerification] = {}
        accepted_rows = list(
            connection.execute(
                """SELECT candidate_hash, submission_id, verified_t, verified_r
                   FROM verification WHERE accepted=1
                   UNION ALL
                   SELECT si.candidate_hash, sv.submission_id,
                          sv.verified_t, sv.verified_r
                   FROM server_verification sv
                   JOIN submission s ON s.submission_id=sv.submission_id
                   JOIN submission_item si
                     ON si.batch_uuid=s.batch_uuid
                    AND si.batch_position=sv.polynomial_index
                   WHERE sv.accepted=1"""
            )
        )
        for row in accepted_rows:
            key = str(row["candidate_hash"])
            verified_t = _optional_int(row["verified_t"])
            verified_r = _optional_int(row["verified_r"])
            if verified_t is None or verified_r is None:
                raise ValueError(f"accepted verification for {key} has no T/r label")
            current = AcceptedVerification(
                candidate_hash=key,
                verified_t=verified_t,
                verified_r=verified_r,
                submission_id=str(row["submission_id"]),
            )
            previous = accepted_by_hash.get(key)
            if previous is not None and (
                previous.verified_t,
                previous.verified_r,
            ) != (verified_t, verified_r):
                raise ValueError(f"candidate {key} has conflicting accepted verifications")
            accepted_by_hash[key] = current

        owned_pairs = {
            (int(row["verified_t"]), int(row["verified_r"]))
            for row in connection.execute(
                """SELECT verified_t, verified_r FROM verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL
                   UNION
                   SELECT verified_t, verified_r FROM server_verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL"""
            )
        }
    finally:
        connection.close()
    metadata = {
        "committed_candidate_hashes": len(committed),
        "accepted_candidate_verifications": len(accepted_by_hash),
        "authoritative_owned_pairs": len(owned_pairs),
    }
    return committed, list(accepted_by_hash.values()), owned_pairs, metadata


def calibrated_keys_from_verifications(
    records_by_hash: Mapping[str, CandidateRecord],
    accepted: Iterable[AcceptedVerification],
) -> tuple[set[CharacterKey], list[dict[str, Any]], int]:
    """Join accepted ledger hashes to supplied candidate character metadata."""

    mappings: dict[CharacterKey, dict[str, Any]] = {}
    unmapped = 0
    for verification in accepted:
        record = records_by_hash.get(verification.candidate_hash)
        if record is None:
            unmapped += 1
            continue
        if verification.verified_r != record.target_r:
            raise ValueError(
                "server root-count mismatch for calibration "
                f"{record.candidate_hash}: predicted r={record.target_r}, "
                f"verified r={verification.verified_r}"
            )
        mapping = {
            "base_t": record.key[0],
            "norm_squareclass": record.key[1],
            "predicted_target_t": record.key[2],
            "verified_target_t": verification.verified_t,
            "verified_target_r": verification.verified_r,
            "candidate_hash": record.candidate_hash,
            "submission_id": verification.submission_id,
        }
        previous = mappings.get(record.key)
        if previous is not None and int(previous["verified_target_t"]) != int(
            verification.verified_t
        ):
            raise ValueError(
                "conflicting server calibrations for character key "
                f"base_t={record.key[0]}, norm_squareclass={record.key[1]}, "
                f"predicted_target_t={record.key[2]}"
            )
        mappings.setdefault(record.key, mapping)
    ordered = [mappings[key] for key in sorted(mappings)]
    return set(mappings), ordered, unmapped


def select_calibration_pilots(
    records: Iterable[CandidateRecord],
    *,
    committed_hashes: set[str],
    calibrated_keys: set[CharacterKey],
    owned_pairs: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Choose the lowest-discriminant uncommitted candidate for every new key."""

    counts: Counter[str] = Counter()
    best_by_key: dict[CharacterKey, CandidateRecord] = {}
    for record in records:
        counts["unique_candidate_hashes"] += 1
        if not record.selectable:
            counts[f"rejected_{record.rejection_reason}"] += 1
            continue
        counts["valid_selectable_candidates"] += 1
        if record.candidate_hash in committed_hashes:
            counts["rejected_committed_candidate"] += 1
            continue
        if record.key in calibrated_keys:
            counts["rejected_server_calibrated_key"] += 1
            continue
        previous = best_by_key.get(record.key)
        if previous is None or _candidate_rank(record) < _candidate_rank(previous):
            if previous is not None:
                counts["superseded_same_key"] += 1
            best_by_key[record.key] = record
        else:
            counts["superseded_same_key"] += 1

    selected: list[dict[str, Any]] = []
    for key in sorted(best_by_key):
        record = best_by_key[key]
        pair_owned = (record.target_t, record.target_r) in owned_pairs
        row = {
            name: value
            for name, value in record.row.items()
            if not str(name).startswith("_pilot_source_")
        }
        row["coefficients"] = record.coefficients
        row["candidate_hash"] = record.candidate_hash
        row["calibration_pilot"] = {
            "base_t": key[0],
            "norm_squareclass": key[1],
            "predicted_target_t": key[2],
            "predicted_target_r": record.target_r,
            "field_disc_abs": record.disc_abs,
            "predicted_pair_already_owned": pair_owned,
            "selection_reason": "lowest_discriminant_uncommitted_candidate_for_key",
        }
        selected.append(row)
        if pair_owned:
            counts["selected_owned_predicted_pairs"] += 1
        else:
            counts["selected_unowned_predicted_pairs"] += 1
    counts["selected_calibration_keys"] = len(selected)
    return selected, dict(sorted(counts.items()))


def _candidate_rank(record: CandidateRecord) -> tuple[int, str]:
    # Ownership intentionally does not enter this rank.  It neither blocks nor
    # promotes a calibration seed; polynomial quality and determinism win.
    return record.disc_abs, record.candidate_hash


def select_character_calibration_pilot(
    candidate_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    if not candidate_paths:
        raise ValueError("at least one character candidate JSONL is required")
    sources = [Path(path).resolve() for path in candidate_paths]
    output = Path(output_path).resolve()
    if output in sources:
        raise ValueError("pilot output must not overwrite an input candidate file")

    raw_rows, load_counts = load_candidate_rows(sources)
    records_by_hash: dict[str, CandidateRecord] = {}
    duplicate_rows = 0
    for row in raw_rows:
        record = validate_candidate(row)
        previous = records_by_hash.get(record.candidate_hash)
        if previous is not None:
            duplicate_rows += 1
            if previous.identity != record.identity:
                raise ValueError(
                    f"candidate {record.candidate_hash} has conflicting character metadata"
                )
            if record.selectable and not previous.selectable:
                records_by_hash[record.candidate_hash] = record
            continue
        records_by_hash[record.candidate_hash] = record

    committed, accepted, owned, ledger_metadata = load_ledger_state(db_path)
    calibrated_keys, mappings, unmapped_accepted = calibrated_keys_from_verifications(
        records_by_hash,
        accepted,
    )
    selected, selection_counts = select_calibration_pilots(
        records_by_hash.values(),
        committed_hashes=committed,
        calibrated_keys=calibrated_keys,
        owned_pairs=owned,
    )
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in selected
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(output, payload)
    summary = {
        "output": str(output),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "candidate_sources": [str(path) for path in sources],
        "candidate_sources_sha256": hashlib.sha256(
            ("\n".join(f"{path}:{_sha256(path)}" for path in sources) + "\n").encode(
                "utf-8"
            )
        ).hexdigest(),
        **ledger_metadata,
        "mapped_server_calibrated_keys": len(calibrated_keys),
        "accepted_verifications_unmapped_to_supplied_candidates": unmapped_accepted,
        "counts": {
            **dict(sorted(load_counts.items())),
            "duplicate_candidate_rows": duplicate_rows,
            **selection_counts,
        },
        "calibrated_key_mappings": mappings,
        "selected_keys": [row["calibration_pilot"] for row in selected],
    }
    report = (
        Path(report_path).resolve()
        if report_path is not None
        else output.with_suffix(output.suffix + ".report.json")
    )
    _atomic_text(report, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    summary = select_character_calibration_pilot(
        args.candidates,
        args.output,
        db_path=args.db,
        report_path=args.report,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

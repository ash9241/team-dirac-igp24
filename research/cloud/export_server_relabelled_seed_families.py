#!/usr/bin/env python3
"""Export accepted server relabels as fail-closed character seed families.

The submitted polynomial is used only as an authoritative calibration for its
exact integral-basis seed.  New twists still have to pass the ordinary exact
subgroup certification before they can become submission candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, candidate_hash, canonical_coefficients


def export_server_relabelled_seed_families(
    candidate_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    if not candidate_paths:
        raise ValueError("at least one candidate JSONL is required")
    sources = [Path(path).resolve() for path in candidate_paths]
    output = Path(output_path).resolve()
    if output in sources:
        raise ValueError("output must not overwrite an input candidate file")
    accepted = _accepted_verifications(db_path)
    counts: Counter[str] = Counter()
    by_hash: dict[str, dict[str, Any]] = {}

    for path in sources:
        if not path.is_file():
            raise FileNotFoundError(path)
        counts["source_files"] += 1
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                counts["input_rows"] += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path}:{line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"candidate row is not an object in {path}:{line_number}")
                supplied_hash = str(row.get("candidate_hash") or "").strip()
                verification = accepted.get(supplied_hash)
                if verification is None:
                    continue
                counts["accepted_candidate_rows"] += 1
                if not _has_integral_basis_seed_metadata(row):
                    # Pair resolvents and other exact constructions can share
                    # these candidate files.  They are not character seeds and
                    # are outside this exporter's scope.
                    counts["accepted_non_seed_rows"] += 1
                    continue
                discovery = _relabelled_discovery(
                    row,
                    verification,
                    location=f"{path}:{line_number}",
                )
                if discovery is None:
                    counts["accepted_not_relabelled"] += 1
                    continue
                previous = by_hash.get(supplied_hash)
                if previous is not None:
                    counts["duplicate_candidate_rows"] += 1
                    if _discovery_identity(previous) != _discovery_identity(discovery):
                        raise ValueError(
                            f"accepted candidate {supplied_hash} has conflicting seed metadata"
                        )
                    continue
                discovery["server_relabel_source"] = {
                    "path": str(path),
                    "line": line_number,
                }
                by_hash[supplied_hash] = discovery

    rows = sorted(
        by_hash.values(),
        key=lambda row: (
            int(row["starting_target_t"]),
            int(row["base_t"]),
            int(row["norm_squareclass"]),
            str(row["source_candidate_hash"]),
        ),
    )
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    _atomic_text(output, payload)
    summary = {
        "candidate_sources": [str(path) for path in sources],
        "output": str(output),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "counts": {
            **dict(sorted(counts.items())),
            "accepted_verifications": len(accepted),
            "exported_seed_families": len(rows),
            "exported_target_groups": len({int(row["starting_target_t"]) for row in rows}),
        },
    }
    report = (
        Path(report_path).resolve()
        if report_path is not None
        else output.with_suffix(output.suffix + ".report.json")
    )
    _atomic_text(report, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def _accepted_verifications(db_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT candidate_hash, submission_id, verified_t, verified_r
               FROM verification
               WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL"""
        )
        return {str(row["candidate_hash"]): dict(row) for row in rows}
    finally:
        connection.close()


def _relabelled_discovery(
    row: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    location: str,
) -> dict[str, Any] | None:
    required_true = (
        "exact_compatibility_proven",
        "submission_ready",
        "local_irreducible",
    )
    if any(row.get(key) is not True for key in required_true):
        raise ValueError(f"accepted calibration lacks exact proof flags at {location}")
    try:
        coefficients = canonical_coefficients(row["coefficients"])
        supplied_hash = str(row["candidate_hash"])
        predicted_t = int(row["target_t"])
        predicted_r = int(row["target_r"])
        local_r = int(row["local_root_count"])
        verified_t = int(verification["verified_t"])
        verified_r = int(verification["verified_r"])
        norm_squareclass = int(row["norm_squareclass"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"accepted calibration has invalid core metadata at {location}") from exc
    if candidate_hash(coefficients) != supplied_hash:
        raise ValueError(f"accepted calibration hash mismatch at {location}")
    if local_r != predicted_r or verified_r != predicted_r:
        raise ValueError(f"accepted calibration root-count mismatch at {location}")
    if predicted_t == verified_t:
        return None
    expected_parity = 2 if norm_squareclass < 0 else 0
    if verified_r not in range(expected_parity, 25, 4):
        raise ValueError(f"accepted calibration has incompatible signature parity at {location}")

    parameters = row.get("parameters")
    if not isinstance(parameters, Mapping) or parameters.get("base_t") is None:
        raise ValueError(f"accepted calibration has no base group at {location}")
    base_t = int(parameters["base_t"])
    base_coefficients = _integer_vector(row.get("base_coefficients"), 13, location)
    seed_coefficients = _rational_vector(row.get("h_coefficients"), 12, location)
    disc_abs = _positive_disc(row, location)
    return {
        "base_coefficients": base_coefficients,
        "base_t": base_t,
        "calibration_kind": "accepted_server_relabelled_integral_basis_seed",
        "disc_abs": disc_abs,
        "label": f"server.relabel.{supplied_hash[:16]}",
        "norm_squareclass": norm_squareclass,
        "seed_coefficients": seed_coefficients,
        "server_calibrated": True,
        "server_calibration": {
            "candidate_hash": supplied_hash,
            "predicted_target_t": predicted_t,
            "submission_id": str(verification["submission_id"]),
            "verified_target_r": verified_r,
            "verified_target_t": verified_t,
        },
        "source_candidate_hash": supplied_hash,
        "starting_target_t": verified_t,
    }


def _has_integral_basis_seed_metadata(row: Mapping[str, Any]) -> bool:
    parameters = row.get("parameters")
    return (
        isinstance(row.get("base_coefficients"), list)
        and isinstance(row.get("h_coefficients"), list)
        and row.get("norm_squareclass") is not None
        and isinstance(parameters, Mapping)
        and parameters.get("base_t") is not None
    )


def _integer_vector(value: Any, length: int, location: str) -> list[int]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"accepted calibration has invalid base coefficients at {location}")
    try:
        result = [int(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"accepted calibration has invalid base coefficients at {location}") from exc
    if result[-1] != 1:
        raise ValueError(f"accepted calibration base is not monic at {location}")
    return result


def _rational_vector(value: Any, max_length: int, location: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= max_length:
        raise ValueError(f"accepted calibration has invalid seed coefficients at {location}")
    # Fraction is intentionally avoided here; the downstream constructor is
    # the authoritative rational parser.  Reject whitespace/empty tokens now.
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise ValueError(f"accepted calibration has invalid seed coefficients at {location}")
    return result


def _positive_disc(row: Mapping[str, Any], location: str) -> int:
    for key in ("field_disc_abs", "estimated_nfdisc_abs", "nfdisc_abs"):
        value = row.get(key)
        if value is None:
            continue
        try:
            parsed = abs(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"accepted calibration has invalid discriminant at {location}") from exc
        if parsed > 1:
            return parsed
    raise ValueError(f"accepted calibration has no field discriminant at {location}")


def _discovery_identity(row: Mapping[str, Any]) -> str:
    value = {
        key: row[key]
        for key in (
            "base_coefficients",
            "base_t",
            "norm_squareclass",
            "seed_coefficients",
            "source_candidate_hash",
            "starting_target_t",
        )
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


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
    summary = export_server_relabelled_seed_families(
        args.candidates,
        args.output,
        db_path=args.db,
        report_path=args.report,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

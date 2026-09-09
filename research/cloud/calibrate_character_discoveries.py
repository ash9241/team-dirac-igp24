#!/usr/bin/env python3
"""Relabel character discoveries from authoritative server verifications.

The local character map predicts a degree-24 transitive-group number.  A
successful pilot submission is the authoritative calibration for the
``(base_t, norm_squareclass, predicted_target_t)`` character key.  The target
component is essential: distinct Kummer modules can have the same rational
norm squareclass while realizing different index-two subgroups.  This module
applies only calibrations whose predicted root count was confirmed by the
server and drops every uncalibrated discovery from the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger


CharacterKey = tuple[int, int, int]


def calibrate_discoveries(
    discoveries_path: str | Path,
    candidates_path: str | Path,
    output_path: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB,
    report_path: str | Path | None = None,
    unmapped_output_path: str | Path | None = None,
    additional_candidates_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Write the server-calibrated subset of ``discoveries_path``.

    A candidate contributes a calibration only when its ledger verification is
    accepted and the verified root count equals the candidate's predicted root
    count.  Conflicting server labels for the same character key are fatal;
    different confirmed root counts are valid because signature is determined
    by the individual Kummer seed rather than the character key.
    """

    discoveries_path = Path(discoveries_path)
    candidates_path = Path(candidates_path)
    candidate_paths = [
        candidates_path,
        *(Path(path) for path in additional_candidates_paths),
    ]
    output_path = Path(output_path)
    discoveries = _load_jsonl(discoveries_path)
    candidates = [
        row
        for path in candidate_paths
        for row in _load_jsonl(path)
    ]

    mappings: dict[CharacterKey, dict[str, Any]] = {}
    unverified_candidates = 0
    rejected_candidates = 0
    with Ledger(db_path) as ledger:
        verified = {
            str(row["candidate_hash"]): dict(row)
            for row in ledger.connection.execute("SELECT * FROM verification")
        }

    for candidate in candidates:
        candidate_hash = str(candidate.get("candidate_hash") or "").strip()
        if not candidate_hash:
            raise ValueError("candidate row has no candidate_hash")
        verification = verified.get(candidate_hash)
        if verification is None:
            unverified_candidates += 1
            continue
        if not bool(verification["accepted"]):
            rejected_candidates += 1
            continue

        key = _character_key(candidate)
        predicted_t = _required_int(candidate, "target_t", nested="parameters")
        predicted_r = _required_int(candidate, "target_r")
        verified_t = _optional_int(verification.get("verified_t"))
        verified_r = _optional_int(verification.get("verified_r"))
        if verified_t is None or verified_r is None:
            raise ValueError(
                f"accepted verification for {candidate_hash} has no T/r label"
            )
        if verified_r != predicted_r:
            raise ValueError(
                "server root-count mismatch for calibration "
                f"{candidate_hash}: predicted r={predicted_r}, verified r={verified_r}"
            )

        calibration = {
            "base_t": key[0],
            "norm_squareclass": key[1],
            "predicted_target_t": key[2],
            "verified_target_t": verified_t,
            "verified_target_r": verified_r,
            "verified_target_rs": [verified_r],
            "candidate_hash": candidate_hash,
            "submission_id": str(verification["submission_id"]),
            "exact_target_label": predicted_t == verified_t,
        }
        previous = mappings.get(key)
        if previous is not None:
            if int(previous["verified_target_t"]) != verified_t:
                raise ValueError(
                    "conflicting server calibrations for character key "
                    f"base_t={key[0]}, norm_squareclass={key[1]}, "
                    f"predicted_target_t={key[2]}: "
                    f"24T{previous['verified_target_t']} versus 24T{verified_t}"
                )
            previous_roots = {
                int(value)
                for value in previous.get(
                    "verified_target_rs",
                    [previous["verified_target_r"]],
                )
            }
            previous_roots.add(verified_r)
            previous["verified_target_rs"] = sorted(previous_roots)
            continue
        mappings[key] = calibration

    output_rows: list[dict[str, Any]] = []
    unmapped_rows: list[dict[str, Any]] = []
    filtered_unmapped = 0
    relabeled_rows = 0
    exact_rows = 0
    for discovery in discoveries:
        key = _character_key(discovery)
        calibration = mappings.get(key)
        if calibration is None:
            filtered_unmapped += 1
            unmapped_rows.append(dict(discovery))
            continue
        predicted_t = _required_int(discovery, "starting_target_t")
        verified_t = int(calibration["verified_target_t"])
        row = dict(discovery)
        row["predicted_target_t"] = predicted_t
        row["starting_target_t"] = verified_t
        if "pilot_terminal_t" in row:
            row["pilot_terminal_t"] = verified_t
        row["server_calibration"] = dict(calibration)
        row["server_calibrated"] = True
        if predicted_t == verified_t:
            exact_rows += 1
        else:
            relabeled_rows += 1
        output_rows.append(row)

    output_rows.sort(key=_discovery_sort_key)
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in output_rows
    )
    _atomic_text(output_path, payload)
    unmapped_payload: str | None = None
    if unmapped_output_path is not None:
        unmapped_path = Path(unmapped_output_path)
        unmapped_rows.sort(key=_discovery_sort_key)
        unmapped_payload = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in unmapped_rows
        )
        _atomic_text(unmapped_path, unmapped_payload)
    mapping_rows = [mappings[key] for key in sorted(mappings)]
    exact_keys = sum(bool(row["exact_target_label"]) for row in mapping_rows)
    summary = {
        "discoveries": str(discoveries_path),
        "candidates": str(candidates_path),
        "candidate_sources": [str(path) for path in candidate_paths],
        "output": str(output_path),
        "input_discoveries": len(discoveries),
        "input_candidates": len(candidates),
        "accepted_calibration_candidates": len(candidates)
        - unverified_candidates
        - rejected_candidates,
        "unverified_candidates": unverified_candidates,
        "rejected_candidates": rejected_candidates,
        "calibration_keys": len(mapping_rows),
        "exact_calibration_keys": exact_keys,
        "relabeled_calibration_keys": len(mapping_rows) - exact_keys,
        "output_discoveries": len(output_rows),
        "filtered_unmapped_discoveries": filtered_unmapped,
        "exact_discoveries": exact_rows,
        "relabeled_discoveries": relabeled_rows,
        "output_targets": len({int(row["starting_target_t"]) for row in output_rows}),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "unmapped_output": (
            str(Path(unmapped_output_path))
            if unmapped_output_path is not None
            else None
        ),
        "unmapped_output_sha256": (
            hashlib.sha256(unmapped_payload.encode("utf-8")).hexdigest()
            if unmapped_payload is not None
            else None
        ),
        "mappings": mapping_rows,
    }
    report = (
        Path(report_path)
        if report_path is not None
        else output_path.with_suffix(output_path.suffix + ".report.json")
    )
    _atomic_text(report, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row is not an object in {path}:{number}")
            rows.append(row)
    return rows


def _character_key(row: Mapping[str, Any]) -> CharacterKey:
    predicted_target = _optional_int(row.get("predicted_target_t"))
    if predicted_target is None:
        predicted_target = _optional_int(row.get("target_t"))
    if predicted_target is None:
        predicted_target = _required_int(row, "starting_target_t")
    return (
        _required_int(row, "base_t", nested="parameters"),
        _required_int(row, "norm_squareclass", nested="parameters"),
        predicted_target,
    )


def _required_int(
    row: Mapping[str, Any],
    name: str,
    *,
    nested: str | None = None,
) -> int:
    value = row.get(name)
    if value is None and nested is not None:
        child = row.get(nested)
        if isinstance(child, Mapping):
            value = child.get(name)
    converted = _optional_int(value)
    if converted is None:
        raise ValueError(f"row has no integer {name}")
    return converted


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _discovery_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(row["base_t"]),
        int(row["starting_target_t"]),
        int(row["norm_squareclass"]),
        str(row.get("label") or ""),
        json.dumps(row.get("seed_coefficients"), sort_keys=True),
        int(row.get("shift") or 0),
    )


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discoveries", type=Path)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--unmapped-output", type=Path)
    parser.add_argument(
        "--additional-candidates",
        action="append",
        type=Path,
        default=[],
        help="additional pilot-candidate JSONL file (repeatable)",
    )
    args = parser.parse_args()
    summary = calibrate_discoveries(
        args.discoveries,
        args.candidates,
        args.output,
        db_path=args.db,
        report_path=args.report,
        unmapped_output_path=args.unmapped_output,
        additional_candidates_paths=args.additional_candidates,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

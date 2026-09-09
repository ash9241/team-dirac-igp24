#!/usr/bin/env python3
"""Dry-run-first submission of proven-valid, not-necessarily-labelled candidates.

This lane is deliberately label agnostic.  It is for locally certified degree-24
polynomials whose transitive-group prediction is not trusted, while the server
classification remains authoritative.  Candidates must still pass every local
validity gate used by the character calibration selector.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.api_client import APIClient, queued_count
from routeA.ledger import (
    DEFAULT_DB,
    Ledger,
    candidate_hash,
    canonical_coefficients,
)
from routeA.select_character_calibration_pilot import (
    CandidateRecord,
    load_candidate_rows,
    validate_candidate,
)


@dataclass(frozen=True)
class ValidityRecord:
    row: Mapping[str, Any]
    candidate_hash: str
    coefficients: str
    target_t: int
    target_r: int
    local_root_count: int
    disc_abs: int
    selectable: bool = True
    rejection_reason: str | None = None

    @property
    def identity(self) -> tuple[Any, ...]:
        return (
            self.coefficients,
            self.target_t,
            self.target_r,
            self.local_root_count,
        )


def validate_validity_candidate(row: Mapping[str, Any]) -> ValidityRecord:
    """Validate a degree-24 polynomial without trusting its predicted label."""

    coefficients = canonical_coefficients(row["coefficients"])
    parts = tuple(int(value) for value in coefficients.split(","))
    if len(parts) != 25 or parts[-1] != 1:
        raise ValueError("validity-only candidate must be monic of degree 24")
    computed_hash = candidate_hash(coefficients)
    supplied_hash = str(row.get("candidate_hash") or "")
    if supplied_hash and supplied_hash != computed_hash:
        raise ValueError(f"candidate hash mismatch: {supplied_hash}")
    if row.get("local_irreducible") is not True:
        raise ValueError(f"candidate {computed_hash} lacks local irreducibility proof")
    root_count = int(row.get("local_root_count", row.get("target_r")))
    if root_count < 0 or root_count > 24 or root_count % 2:
        raise ValueError(f"candidate {computed_hash} has invalid root count")
    target_t = int(row.get("target_t") or 0)
    target_r = int(row.get("target_r", root_count))
    if target_r != root_count:
        raise ValueError(f"candidate {computed_hash} has inconsistent root count")
    disc = int(
        row.get("field_disc_abs")
        or row.get("estimated_nfdisc_abs")
        or row.get("nfdisc_abs")
        or 0
    )
    return ValidityRecord(
        row=dict(row),
        candidate_hash=computed_hash,
        coefficients=coefficients,
        target_t=target_t,
        target_r=target_r,
        local_root_count=root_count,
        disc_abs=abs(disc),
    )


def select_exploration_candidates(
    candidate_paths: Sequence[str | Path],
    *,
    ledger: Ledger,
    limit: int = 1000,
    validity_only: bool = False,
) -> tuple[list[CandidateRecord | ValidityRecord], dict[str, int]]:
    """Return deterministic, unique, valid candidates not already committed."""

    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be in [1, 1000]")
    rows, load_counts = load_candidate_rows(candidate_paths)
    counts: Counter[str] = Counter(load_counts)
    unique: dict[str, CandidateRecord | ValidityRecord] = {}
    for row in rows:
        record = (
            validate_validity_candidate(row)
            if validity_only
            else validate_candidate(row)
        )
        previous = unique.get(record.candidate_hash)
        if previous is not None:
            counts["duplicate_candidate_rows"] += 1
            if previous.identity != record.identity:
                raise ValueError(
                    f"candidate {record.candidate_hash} has conflicting metadata"
                )
            continue
        unique[record.candidate_hash] = record

    eligible: list[CandidateRecord | ValidityRecord] = []
    for record in unique.values():
        if not record.selectable:
            counts[f"rejected_{record.rejection_reason}"] += 1
            continue
        if ledger.candidate_committed(record.candidate_hash):
            counts["rejected_committed_candidate"] += 1
            continue
        eligible.append(record)
    eligible.sort(
        key=lambda record: (
            record.target_t,
            record.target_r,
            record.disc_abs,
            record.candidate_hash,
        )
    )
    counts["eligible_candidates"] = len(eligible)
    selected = eligible[: int(limit)]
    counts["selected_candidates"] = len(selected)
    counts["deferred_by_limit"] = len(eligible) - len(selected)
    return selected, dict(sorted(counts.items()))


def run_exploration_batch(
    candidate_paths: Sequence[str | Path],
    *,
    db_path: str | Path = DEFAULT_DB,
    limit: int = 1000,
    execute: bool = False,
    validity_only: bool = False,
    report_path: str | Path | None = None,
    poll_timeout: float = 3600,
    batch_uuid: str | None = None,
) -> dict[str, Any]:
    """Validate, select, and optionally submit one exploration batch."""

    client = APIClient()
    with Ledger(db_path) as ledger:
        selected, counts = select_exploration_candidates(
            candidate_paths,
            ledger=ledger,
            limit=limit,
            validity_only=validity_only,
        )
        if not selected:
            raise RuntimeError("no eligible exploration candidates remain")
        lines = [record.coefficients for record in selected]
        for record in selected:
            ledger.upsert_candidate({
                "coefficients": record.coefficients,
                "candidate_hash": record.candidate_hash,
                "local_irreducible": True,
                "local_root_count": record.local_root_count,
                "source_host": "validated-exploration-harvest",
            })
        batch_uuid = batch_uuid or f"exploration-{uuid.uuid4()}"
        receipt = client.submit_batch(
            lines,
            ledger,
            batch_uuid=batch_uuid,
            dry_run=not execute,
        )
        accepted = scoreable = exact_labels = exact_pairs = 0
        actual_pairs: set[tuple[int, int]] = set()
        relabels: Counter[str] = Counter()
        submission_id = receipt.submission_id
        if execute:
            if not submission_id:
                raise RuntimeError("executed exploration batch has no submission id")
            item = client.poll_submission(submission_id, timeout=poll_timeout)
            if queued_count(item) != 0:
                raise RuntimeError("exploration submission did not reach a terminal state")
            item = _await_final_scoring(
                client,
                submission_id,
                item,
                timeout=min(30.0, float(poll_timeout)),
            )
            client.ingest_completed(batch_uuid, item, ledger)
            results = item.get("verifiedPolynomials") or []
            by_position = {
                int(result["polynomialIndex"]): result
                for result in results
                if isinstance(result, Mapping) and result.get("polynomialIndex") is not None
            }
            for position, record in enumerate(selected):
                result = by_position.get(position)
                if not result or result.get("status") != "accepted":
                    continue
                accepted += 1
                # The terminal verification response can expose the final
                # scoringStatus a few seconds before its redundant boolean
                # scoreable flag flips.  Treat either authoritative signal as
                # final so the immediate report does not undercount a batch.
                scoreable += int(
                    bool(result.get("scoreable"))
                    or result.get("scoringStatus") == "scoreable"
                )
                actual_t = int(result["t"])
                actual_r = int(result["r"])
                actual_pairs.add((actual_t, actual_r))
                exact_labels += int(
                    record.target_t > 0 and actual_t == record.target_t
                )
                exact_pairs += int(
                    record.target_t > 0
                    and actual_t == record.target_t
                    and actual_r == record.target_r
                )
                if record.target_t > 0 and actual_t != record.target_t:
                    relabels[f"24T{record.target_t}->24T{actual_t}"] += 1

    report = {
        "dry_run": not execute,
        "validity_only": bool(validity_only),
        "candidate_sources": [str(Path(path)) for path in candidate_paths],
        "counts": counts,
        "batch_uuid": receipt.batch_uuid,
        "payload_hash": receipt.payload_hash,
        "submission_id": submission_id,
        "accepted": accepted,
        "scoreable": scoreable,
        "actual_pair_count": len(actual_pairs),
        "exact_label_hits": exact_labels,
        "exact_pair_hits": exact_pairs,
        "relabels": dict(sorted(relabels.items())),
    }
    if report_path is not None:
        destination = Path(report_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination.parent, delete=False
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(destination)
    return report


def _await_final_scoring(
    client: APIClient,
    submission_id: str,
    item: Mapping[str, Any],
    *,
    timeout: float,
) -> Mapping[str, Any]:
    """Wait briefly for accepted rows to leave the transient no-score state."""

    deadline = time.monotonic() + max(0.0, float(timeout))
    current: Mapping[str, Any] = item
    while not _has_final_scoring(current) and time.monotonic() < deadline:
        client.sleep(2.0)
        for observed in client.recent_submissions(limit=5):
            if observed.get("submissionId") == submission_id:
                current = observed
                break
    return current


def _has_final_scoring(item: Mapping[str, Any]) -> bool:
    results = item.get("verifiedPolynomials")
    if not isinstance(results, list):
        return False
    return all(
        result.get("status") != "accepted"
        or result.get("scoringStatus") in {"scoreable", "not_scoreable"}
        for result in results
        if isinstance(result, Mapping)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--validity-only",
        action="store_true",
        help="require polynomial validity but do not require an exact local label",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--poll-timeout", type=float, default=3600)
    parser.add_argument(
        "--batch-uuid",
        help="reuse a failed/ambiguous-audited batch UUID for the identical payload",
    )
    args = parser.parse_args()
    report = run_exploration_batch(
        args.candidates,
        db_path=args.db,
        limit=args.limit,
        execute=args.execute,
        validity_only=args.validity_only,
        report_path=args.report,
        poll_timeout=args.poll_timeout,
        batch_uuid=args.batch_uuid,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

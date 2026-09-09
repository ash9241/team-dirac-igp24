#!/usr/bin/env python3
"""Prospectively audit an exact-label construction family.

The cycle-index oracle is useful for eliminating impossible labels, but its
posterior is not a calibration result.  This module compares predictions made
before submission with authoritative server labels, checks independent
lineages, and invalidates a claimed compatibility set if an observed result
falls outside it.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.metrics import wilson_lower_bound
from routeA.structural_oracle import normalize_family


AUDIT_VERSION = "prospective-family-v1"


@dataclass(frozen=True)
class FamilyCalibrationReport:
    audit_version: str
    family: str
    accepted_trials: int
    exact_label_hits: int
    exact_pair_hits: int
    exact_label_precision: float | None
    exact_label_wilson_lower_95: float
    independent_lineages: int
    observed_labels: Mapping[str, int]
    compatibility_violations: Mapping[str, int]
    minimum_trials: int
    minimum_lineages: int
    minimum_precision: float
    passed: bool
    reasons: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def ledger_observations(
    ledger: Ledger, family: str
) -> list[dict[str, Any]]:
    rows = ledger.connection.execute(
        """SELECT c.candidate_hash, r.recipe_id, r.parameters_json,
                  v.accepted, v.verified_t, v.verified_r,
                  p.selected_t, p.selected_r, p.model_version
           FROM verification v
           JOIN candidate c USING(candidate_hash)
           JOIN recipe r USING(recipe_id)
           LEFT JOIN prediction p
             ON p.rowid=(SELECT MAX(p2.rowid) FROM prediction p2
                         WHERE p2.candidate_hash=c.candidate_hash)
           WHERE r.family=?
           ORDER BY v.verified_at, c.candidate_hash""",
        (family,),
    )
    observations = []
    for row in rows:
        try:
            metadata = json.loads(row["parameters_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        compatible = metadata.get("compatible_labels") or []
        observations.append({
            "candidate_hash": str(row["candidate_hash"]),
            "accepted": bool(row["accepted"]),
            "verified_t": row["verified_t"],
            "verified_r": row["verified_r"],
            "selected_t": row["selected_t"],
            "selected_r": row["selected_r"],
            "model_version": row["model_version"],
            "lineage": str(
                metadata.get("recipe_lineage") or row["recipe_id"]
            ),
            "compatible_labels": [int(label) for label in compatible],
        })
    return observations


def evaluate_family_calibration(
    family: str,
    observations: Iterable[Mapping[str, Any]],
    *,
    minimum_trials: int = 5,
    minimum_lineages: int = 3,
    minimum_precision: float = 0.85,
) -> FamilyCalibrationReport:
    if minimum_trials < 1 or minimum_lineages < 1:
        raise ValueError("minimum trials and lineages must be positive")
    if not 0.0 <= minimum_precision <= 1.0:
        raise ValueError("minimum precision must be in [0, 1]")

    eligible = [
        dict(row)
        for row in observations
        if bool(row.get("accepted"))
        and row.get("selected_t") is not None
        and row.get("verified_t") is not None
    ]
    exact_labels = sum(
        int(row["selected_t"]) == int(row["verified_t"])
        for row in eligible
    )
    exact_pairs = sum(
        int(row["selected_t"]) == int(row["verified_t"])
        and row.get("selected_r") is not None
        and row.get("verified_r") is not None
        and int(row["selected_r"]) == int(row["verified_r"])
        for row in eligible
    )
    labels = Counter(int(row["verified_t"]) for row in eligible)
    lineages = {str(row.get("lineage") or "unknown") for row in eligible}
    violations: Counter[int] = Counter()
    for row in eligible:
        claimed = {int(label) for label in row.get("compatible_labels", [])}
        verified = int(row["verified_t"])
        if claimed and verified not in claimed:
            violations[verified] += 1

    trials = len(eligible)
    precision = exact_labels / trials if trials else None
    reasons = []
    if trials < minimum_trials:
        reasons.append(
            f"accepted prospective trials {trials} are below {minimum_trials}"
        )
    if len(lineages) < minimum_lineages:
        reasons.append(
            f"independent lineages {len(lineages)} are below {minimum_lineages}"
        )
    if precision is None or precision < minimum_precision:
        rendered = "none" if precision is None else f"{precision:.6f}"
        reasons.append(
            f"exact-label precision {rendered} is below {minimum_precision:.6f}"
        )
    if violations:
        reasons.append("an observed label falls outside the claimed compatibility set")

    return FamilyCalibrationReport(
        audit_version=AUDIT_VERSION,
        family=family,
        accepted_trials=trials,
        exact_label_hits=exact_labels,
        exact_pair_hits=exact_pairs,
        exact_label_precision=precision,
        exact_label_wilson_lower_95=wilson_lower_bound(exact_labels, trials),
        independent_lineages=len(lineages),
        observed_labels={str(label): count for label, count in sorted(labels.items())},
        compatibility_violations={
            str(label): count for label, count in sorted(violations.items())
        },
        minimum_trials=minimum_trials,
        minimum_lineages=minimum_lineages,
        minimum_precision=minimum_precision,
        passed=not reasons,
        reasons=tuple(reasons),
    )


def annotate_candidates(
    candidates: Sequence[Mapping[str, Any]],
    report: FamilyCalibrationReport,
) -> list[dict[str, Any]]:
    calibration = report.as_json()
    annotated = []
    matched = 0
    for source in candidates:
        row = dict(source)
        family = normalize_family(
            row.get("recipe_family") or row.get("cls") or row.get("fam")
        )
        if family == normalize_family(report.family):
            matched += 1
            row.update({
                "calibration_status": "passed" if report.passed else "blocked",
                "prospective_calibration_passed": report.passed,
                "submission_ready": report.passed,
                "family_calibration": calibration,
            })
        annotated.append(row)
    if not matched:
        raise ValueError(f"candidate shard contains no family {report.family!r}")
    return annotated


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        temporary = handle.name
    os.replace(temporary, target)


def atomic_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as handle:
        json.dump(dict(value), handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_shard")
    parser.add_argument("output")
    parser.add_argument("--family", required=True)
    parser.add_argument("--report")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--minimum-trials", type=int, default=5)
    parser.add_argument("--minimum-lineages", type=int, default=3)
    parser.add_argument("--minimum-precision", type=float, default=0.85)
    args = parser.parse_args()

    with Ledger(args.db) as ledger:
        observations = ledger_observations(ledger, args.family)
    report = evaluate_family_calibration(
        args.family,
        observations,
        minimum_trials=args.minimum_trials,
        minimum_lineages=args.minimum_lineages,
        minimum_precision=args.minimum_precision,
    )
    annotated = annotate_candidates(load_jsonl(args.candidate_shard), report)
    report_path = args.report or f"{args.output}.calibration.json"
    atomic_jsonl(args.output, annotated)
    atomic_json(report_path, report.as_json())
    print(json.dumps(report.as_json(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

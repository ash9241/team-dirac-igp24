#!/usr/bin/env python3
"""Build a current, fail-closed q214 separating-resolvent research frontier.

This program is offline and never stages or submits polynomials.  It rejoins
sealed ambiguous q214 classifications to the current target cache, removes any
candidate already present in submission history, and emits only candidates
whose remaining exact-label catalog still contains a current tc0 pair.
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

ROOT = Path(__file__).resolve().parent
DEFAULT_CLASSIFICATION = ROOT / "data/q214_failclosed_fullgroup_classification_20260731.json"
DEFAULT_DB = ROOT / "data/ledger.sqlite3"
DEFAULT_OUTPUT = ROOT / "data/current_q214_separating_resolvent_frontier_20260806.jsonl"
DEFAULT_SUMMARY = ROOT / "data/current_q214_separating_resolvent_frontier_20260806.summary.json"


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reconstruction_index(classification: dict) -> dict[str, dict]:
    paths = sorted({str(row["inputArtifact"]) for row in classification["rows"]})
    result: dict[str, dict] = {}
    for relative in paths:
        document = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        stack = [document]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                digest = value.get("candidateSha256")
                line = value.get("candidateCoefficientLine")
                if digest is not None and line is not None:
                    canonical = ",".join(str(int(field)) for field in str(line).split(","))
                    actual = hashlib.sha256(canonical.encode("ascii")).hexdigest()
                    if actual != digest:
                        raise ValueError(f"candidate hash mismatch in {relative}: {digest}")
                    prior = result.get(str(digest))
                    record = {"inputArtifact": relative, **value, "candidateCoefficientLine": canonical}
                    if prior is not None and prior["candidateCoefficientLine"] != canonical:
                        raise ValueError(f"candidate hash collision: {digest}")
                    result[str(digest)] = record
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return result


def build_frontier(classification: dict, connection: sqlite3.Connection) -> tuple[list[dict], dict]:
    reconstructions = reconstruction_index(classification)
    generated_at = sorted({str(row[0]) for row in connection.execute("SELECT DISTINCT generated_at FROM targets")})
    if not generated_at:
        raise ValueError("target cache is empty")
    frontier = []
    compatible_before_history_gate = 0
    excluded_submitted = []
    for classification_row in classification["rows"]:
        digest = str(classification_row["candidateSha256"])
        reconstruction = reconstructions.get(digest)
        if reconstruction is None:
            raise ValueError(f"missing q214 reconstruction: {digest}")
        r_value = int(classification_row["r"])
        boundary = []
        tc0 = []
        for label in classification_row["remainingLabels"]:
            target = connection.execute(
                "SELECT team_count,discovered,minimum_disc_abs,generated_at FROM targets WHERE label=? AND r=?",
                (str(label), r_value),
            ).fetchone()
            if target is None:
                raise ValueError(f"target cache is missing {label}/r{r_value}")
            state = {
                "label": str(label),
                "r": r_value,
                "teamCount": int(target[0]),
                "discovered": bool(target[1]),
                "minimumDiscAbs": str(target[2]) if target[2] is not None else None,
                "generatedAt": str(target[3]),
            }
            boundary.append(state)
            if state["teamCount"] == 0 and not state["discovered"]:
                tc0.append(str(label))
        if not tc0:
            continue
        compatible_before_history_gate += 1
        submitted = connection.execute(
            "SELECT v.submission_id,v.label,v.r,v.status FROM polynomials p "
            "JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE p.coefficient_hash=? LIMIT 1",
            (digest,),
        ).fetchone()
        if submitted is not None:
            excluded_submitted.append({
                "candidateSha256": digest,
                "submissionId": str(submitted[0]),
                "verifiedLabel": str(submitted[1]),
                "verifiedR": int(submitted[2]),
                "status": str(submitted[3]),
            })
            continue
        line = str(reconstruction["candidateCoefficientLine"])
        frontier.append({
            "schemaVersion": "current-q214-separating-resolvent-frontier-v1",
            "status": "ambiguous_exact_containment_requires_separating_resolvent",
            "submissionReady": False,
            "candidateSha256": digest,
            "candidateCoefficientLine": line,
            "candidateBytes": len(line.encode("ascii")),
            "fieldCanonicalSha256": str(classification_row["fieldCanonicalSha256"]),
            "r": r_value,
            "realRoots": int(classification_row["realRoots"]),
            "checkedSquarefreePrimes": int(classification_row["checkedSquarefreePrimes"]),
            "remainingLabels": list(map(str, classification_row["remainingLabels"])),
            "currentTc0CompatibleLabels": tc0,
            "currentBoundary": boundary,
            "classificationArtifact": str(DEFAULT_CLASSIFICATION.relative_to(ROOT)),
            "inputArtifact": str(classification_row["inputArtifact"]),
            "requiredNextProof": "exact_group_only_separating_resolvent_then_candidate_factorization",
        })
    frontier.sort(key=lambda row: (
        len(row["remainingLabels"]),
        -len(row["currentTc0CompatibleLabels"]),
        row["candidateBytes"],
        row["candidateSha256"],
    ))
    summary = {
        "schemaVersion": "current-q214-separating-resolvent-frontier-summary-v1",
        "targetCacheGeneratedAt": generated_at,
        "sealedQ214Candidates": len(classification["rows"]),
        "tc0CompatibleBeforeSubmissionHistoryGate": compatible_before_history_gate,
        "alreadySubmittedExcluded": excluded_submitted,
        "frontierCandidates": len(frontier),
        "rDistribution": dict(sorted(Counter(str(row["r"]) for row in frontier).items())),
        "tc0CompatibilityDistribution": dict(sorted(Counter(
            ",".join(row["currentTc0CompatibleLabels"]) for row in frontier
        ).items())),
        "submissionReady": 0,
        "nextExperiment": {
            "pilotCandidates": min(5, len(frontier)),
            "selection": "fewest remaining labels, then smallest coefficient payload",
            "promotionGate": "at least one singleton exact label after an independently separating resolvent",
            "stopRule": "never infer an exact 24T label from maximal exclusion without containment",
        },
    }
    return frontier, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    classification = json.loads(args.classification.read_text(encoding="utf-8"))
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        frontier, summary = build_frontier(classification, connection)
    finally:
        connection.close()
    atomic_write(args.output, "".join(json.dumps(row, sort_keys=True) + "\n" for row in frontier))
    summary["output"] = str(args.output.resolve())
    atomic_write(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

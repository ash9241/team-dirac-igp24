#!/usr/bin/env python3
"""Report prospective precision and score metrics from the control ledger."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.metrics import replacement_ready, wilson_lower_bound


def report(ledger: Ledger, volume_points_per_slot: float = 13.0 / 120.0) -> dict:
    rows = list(ledger.connection.execute(
        """WITH latest_prediction AS (
               SELECT p.* FROM prediction p
               WHERE p.rowid=(SELECT MAX(p2.rowid) FROM prediction p2
                              WHERE p2.candidate_hash=p.candidate_hash)
           ), event_score AS (
               SELECT candidate_hash, SUM(immediate_score) AS score
               FROM score_event GROUP BY candidate_hash
           )
           SELECT v.candidate_hash, v.submission_id, v.accepted, v.verified_t, v.verified_r,
                  p.selected_t, p.selected_r, p.model_version,
                  COALESCE(se.score,0) AS immediate_score
           FROM verification v
           LEFT JOIN latest_prediction p ON p.candidate_hash=v.candidate_hash
           LEFT JOIN event_score se ON se.candidate_hash=v.candidate_hash"""
    ))
    prospective = [row for row in rows if row["selected_t"] is not None]
    accepted = [row for row in prospective if row["accepted"]]
    exact_labels = sum(row["verified_t"] == row["selected_t"] for row in accepted)
    exact_pairs = sum(
        row["verified_t"] == row["selected_t"] and row["verified_r"] == row["selected_r"]
        for row in accepted
    )
    score_by_submission = defaultdict(float)
    for row in prospective:
        score_by_submission[str(row["submission_id"])] += float(row["immediate_score"])
    by_submission = {submission: [score] for submission, score in score_by_submission.items()}
    ready, lower = replacement_ready(by_submission, volume_points_per_slot) if by_submission else (False, 0.0)
    return {
        "prospective_verifications": len(prospective),
        "accepted": len(accepted),
        "exact_label_hits": exact_labels,
        "exact_pair_hits": exact_pairs,
        "exact_label_precision": exact_labels / len(accepted) if accepted else None,
        "exact_label_wilson_lower_95": wilson_lower_bound(exact_labels, len(accepted)),
        "exact_pair_precision": exact_pairs / len(accepted) if accepted else None,
        "immediate_score": sum(float(row["immediate_score"]) for row in prospective),
        "submission_clusters": len(by_submission),
        "targeted_points_per_slot_lower_95": lower,
        "volume_points_per_slot_baseline": volume_points_per_slot,
        "volume_replacement_gate": ready,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--volume-points-per-slot", type=float, default=13.0 / 120.0)
    args = parser.parse_args()
    with Ledger(args.db) as ledger:
        value = report(ledger, args.volume_points_per_slot)
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

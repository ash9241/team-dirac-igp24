#!/usr/bin/env python3
"""Reproduce the submitted even-quartic form-to-label collapse."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from routeA.quartic_invariants import old_form_regime, predicted_old_label


def analyze(
    rows: Iterable[Mapping[str, Any]], verification: Mapping[str, tuple[int, int]]
) -> dict[str, Any]:
    counts: Counter[tuple[str, int]] = Counter()
    regime_totals: Counter[str] = Counter()
    regime_matches: Counter[str] = Counter()
    labels_by_regime: dict[str, Counter[int]] = defaultdict(Counter)
    matched_rows = 0
    for row in rows:
        observed = verification.get(str(row.get("candidate_hash") or ""))
        if observed is None:
            continue
        matched_rows += 1
        form = str((row.get("parameters") or {}).get("form") or "unknown")
        regime = old_form_regime(form)
        label = int(observed[0])
        counts[(form, label)] += 1
        labels_by_regime[regime][label] += 1
        regime_totals[regime] += 1
        regime_matches[regime] += int(label == predicted_old_label(form))
    return {
        "matched_rows": matched_rows,
        "form_label_counts": [
            {"form": form, "label": label, "count": count}
            for (form, label), count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "regimes": {
            regime: {
                "total": regime_totals[regime],
                "predicted_label": {
                    "D4_linked": 19036,
                    "C4_linked": 17757,
                    "V4_common": 7181,
                }.get(regime),
                "predicted_matches": regime_matches[regime],
                "precision": (
                    regime_matches[regime] / regime_totals[regime]
                    if regime_totals[regime]
                    else None
                ),
                "labels": dict(sorted(labels_by_regime[regime].items())),
            }
            for regime in sorted(regime_totals)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--db", type=Path, default=Path("routeA/data/control.sqlite3"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in args.candidates.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    connection = sqlite3.connect(args.db)
    try:
        verification = {
            str(key): (int(label), int(roots))
            for key, label, roots in connection.execute(
                """SELECT candidate_hash, verified_t, verified_r
                   FROM verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL"""
            )
        }
    finally:
        connection.close()
    report = analyze(rows, verification)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()

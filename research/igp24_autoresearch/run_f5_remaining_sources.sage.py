#!/usr/bin/env sage -python
"""Resolve a selected range of the sealed F5 multi-orbit frontier.

This is a coefficient-bearing arithmetic worker, but it is deliberately
submission-free.  It reuses the exact action reconstruction and Frobenius
dispatcher from ``run_f5_multiorbit_plan.sage.py``, then classifies every
resolved factor against the current local target/ownership snapshot.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
WORKER = ROOT / "run_f5_multiorbit_plan.sage.py"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.start < 1 or args.end < args.start:
        raise ValueError("invalid inclusive source-position range")
    output = args.output.expanduser().resolve()
    if output.parent != (ROOT / "data").resolve() or output.exists():
        raise ValueError("output must be a new direct child of data/")

    library = runpy.run_path(str(WORKER), run_name="f5_worker_library")
    read_jsonl = library["read_jsonl"]
    action_sha256 = library["action_sha256"]
    validate_source_plan = library["validate_source_plan"]
    derive_source = library["derive_source"]

    plan_path = args.plan.expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    selected = list(plan["sources"])
    if args.end > len(selected):
        raise ValueError("requested range exceeds the sealed frontier")

    raw_rows = read_jsonl(ROOT / plan["artifacts"]["actionMap"]["path"])
    raw_by_label = {str(row["sourceLabel"]): row for row in raw_rows}
    cached_actions = {}
    for artifact in plan["artifacts"]["actionShards"]:
        for row in read_jsonl(ROOT / artifact["path"]):
            digest = action_sha256(row)
            incumbent = cached_actions.get(digest)
            if incumbent is not None and incumbent != row:
                raise ValueError("conflicting cached exact-action digest")
            cached_actions[digest] = row

    rows = []
    with sqlite3.connect(DB) as connection:
        connection.row_factory = sqlite3.Row
        for position in range(args.start, args.end + 1):
            selected_row = selected[position - 1]
            actions = validate_source_plan(
                selected_row, raw_by_label, cached_actions
            )
            _candidates, result = derive_source(
                selected_row, position, connection, actions
            )
            for candidate in result["candidateResults"]:
                target = candidate["exactTarget"]
                pair = (str(target["label"]), int(target["r"]))
                state = connection.execute(
                    """
                    SELECT t.*,
                           EXISTS(
                             SELECT 1 FROM baseline_pairs b
                             WHERE b.label=t.label AND b.r=t.r
                           ) AS baseline,
                           EXISTS(
                             SELECT 1 FROM verifications v
                             WHERE v.label=t.label AND v.r=t.r
                               AND v.status='accepted' AND v.scoreable=1
                           ) AS owned
                    FROM targets t
                    WHERE t.label=? AND t.r=?
                    """,
                    pair,
                ).fetchone()
                known = bool(
                    connection.execute(
                        "SELECT EXISTS("
                        "SELECT 1 FROM polynomials WHERE coefficient_hash=?"
                        ")",
                        (str(candidate["coefficientSha256"]),),
                    ).fetchone()[0]
                )
                if state is None:
                    live = None
                    status = "resolved_missing_target_state"
                else:
                    live = {
                        "baseline": bool(state["baseline"]),
                        "discovered": bool(state["discovered"]),
                        "generatedAt": state["generated_at"],
                        "minimumDiscAbs": state["minimum_disc_abs"],
                        "owned": bool(state["owned"]),
                        "teamCount": int(state["team_count"]),
                    }
                    if known:
                        status = "resolved_known_coefficient"
                    elif live["baseline"]:
                        status = "resolved_baseline"
                    elif live["owned"]:
                        status = "resolved_owned_pair"
                    elif not live["discovered"] and live["teamCount"] == 0:
                        status = "current_live_gold"
                    else:
                        status = "current_unowned_shared"
                candidate["knownInLedger"] = known
                candidate["liveState"] = live
                candidate["status"] = status
            rows.append(result)

    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    with output.open("x", encoding="utf-8") as handle:
        handle.write(rendered)

    statuses = {}
    for row in rows:
        for candidate in row["candidateResults"]:
            status = str(candidate["status"])
            statuses[status] = statuses.get(status, 0) + 1
    summary = {
        "candidates": sum(len(row["candidateResults"]) for row in rows),
        "end": args.end,
        "output": str(output),
        "sources": len(rows),
        "start": args.start,
        "statusHistogram": statuses,
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

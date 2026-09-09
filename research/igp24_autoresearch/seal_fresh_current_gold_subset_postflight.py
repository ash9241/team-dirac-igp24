#!/usr/bin/env python3
"""Seal the fail-closed postflight for the one authorized k=4 route."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
PACKET = DATA / "fresh_current_gold_subset_route_packet_20260730.json"
RESULTS = DATA / "agent_f9_k4_general_live_v1_24T22560_r12_results.jsonl"
SUMMARY = DATA / "agent_f9_k4_general_live_v1_24T22560_r12_summary.json"
MANIFEST = OUTBOX / "agent_f9_k4_general_live_v1_24T22560_r12_live.txt"
OUTPUT = DATA / "fresh_current_gold_subset_route_postflight_20260730.json"
TARGETS = (("24T20605", 24), ("24T20611", 24))


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    packet = json.loads(PACKET.read_text())
    summary = json.loads(SUMMARY.read_text())
    if (
        summary.get("status") != "blocked_factorization_or_assignment"
        or int(summary.get("pilotSourcesCompleted", -1)) != 0
        or int(summary.get("exactLiveHits", -1)) != 0
        or summary.get("stagedPairs") != []
        or RESULTS.read_text() != ""
        or MANIFEST.read_text() != ""
    ):
        raise ValueError("k=4 postflight is not the expected fail-closed obstruction")

    comparison = {}
    for bound in (2000, 5000):
        path = DATA / f"agent_f9_k4_22560_r16_pb{bound}_summary.json"
        row = json.loads(path.read_text())
        comparison[str(bound)] = {
            "error": row["obstruction"]["error"],
            "sha256": sha256_path(path),
            "status": row["status"],
        }

    connection = sqlite3.connect(f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    states = {}
    for pair in TARGETS:
        row = connection.execute(
            """
            SELECT t.*,
              EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) baseline,
              EXISTS(SELECT 1 FROM verifications v
                     WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) owned
            FROM targets t WHERE t.label=? AND t.r=?
            """,
            pair,
        ).fetchone()
        states[f"{pair[0]}/r{pair[1]}"] = dict(row)
    connection.close()
    if any(
        int(row["team_count"]) != 0
        or int(row["baseline"])
        or int(row["owned"])
        or int(row["discovered"])
        or row["minimum_disc_abs"] is not None
        for row in states.values()
    ):
        raise ValueError("postflight target state changed")

    elapsed_upper_bound = (SUMMARY.stat().st_mtime_ns - PACKET.stat().st_mtime_ns) / 1e9
    payload = {
        "assignmentPrimeBound": 400,
        "candidateCoefficientHashes": [],
        "continuationDecision": {
            "continueAt2000": False,
            "evidence": comparison,
            "reason": (
                "The worker has no checkpoint/reuse path and the same 24T22560 "
                "three-action system remained at six factor assignments and six "
                "target-label assignments through prime 5000 on the sealed r16 "
                "source. Recomputing this r12 degree-495 factorization at 2000 is "
                "therefore not justified."
            ),
        },
        "exactAssignmentsCertified": 0,
        "exactCandidates": 0,
        "heavyWorkerExitCode": 0,
        "networkCalls": 0,
        "obstruction": summary["obstruction"],
        "observedPacketToOutputUpperBoundSeconds": elapsed_upper_bound,
        "outputs": {
            "manifest": {
                "bytes": MANIFEST.stat().st_size,
                "path": str(MANIFEST.relative_to(ROOT)),
                "sha256": sha256_path(MANIFEST),
            },
            "results": {
                "bytes": RESULTS.stat().st_size,
                "path": str(RESULTS.relative_to(ROOT)),
                "sha256": sha256_path(RESULTS),
            },
            "summary": {
                "bytes": SUMMARY.stat().st_size,
                "path": str(SUMMARY.relative_to(ROOT)),
                "sha256": sha256_path(SUMMARY),
            },
        },
        "packet": {
            "path": str(PACKET.relative_to(ROOT)),
            "sha256": sha256_path(PACKET),
        },
        "schemaVersion": "fresh-current-gold-subset-route-postflight-v1",
        "source": packet["source"],
        "stagedPairs": [],
        "stagedPolynomials": 0,
        "status": "fail_closed_unresolved_assignment",
        "submissionCalls": 0,
        "targetStates": states,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(OUTPUT)
    print(
        json.dumps(
            {
                "postflight": str(OUTPUT.relative_to(ROOT)),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "status": payload["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

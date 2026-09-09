#!/usr/bin/env python3
"""Run and exactly assign the sole fresh multi-orbit rank-11 packet."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import agent_rank10_pair_stage2 as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FRONTIER = DATA / "agent_rank11_pair_stage2_frontier.jsonl"
RESULTS = DATA / "agent_rank11_pair_stage2_multi_results.jsonl"
CERTIFICATE = DATA / "agent_rank11_pair_stage2_multi_frobenius.json"
SUMMARY = DATA / "agent_rank11_pair_stage2_multi_summary.json"


def main() -> int:
    task = exact.read_jsonl(FRONTIER)[0]
    if str(task["sourceLabel"]) != "24T7959" or int(task["sourceR"]) != 16:
        raise RuntimeError("fresh multi-orbit frontier head changed")
    task = {**task, "pilotRank": 1}
    result = exact.run_packet(task, timeout=600)
    exact.shared.write_jsonl_atomic(RESULTS, [result], sort_key=lambda _row: 0)
    if result.get("status") != "certified_multi":
        raise RuntimeError(f"multi packet failed: {result.get('status')}")
    completed = subprocess.run(
        [
            "sage", "-python", str(exact.FROBENIUS_WORKER),
            "--input", str(RESULTS),
            "--source-pair", "24T7959", "16",
            "--prime-bound", "5000", "--require-resolved",
            "--output", str(CERTIFICATE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    certificate = json.loads(CERTIFICATE.read_text())
    proof = certificate["rows"][0]
    if completed.returncode != 0 or proof.get("status") != "resolved":
        raise RuntimeError(
            f"multi assignment failed: exit={completed.returncode}, "
            f"status={proof.get('status')}, stderr={completed.stderr[-1200:]}"
        )
    assignments = {int(row["factorIndex"]): row for row in proof["assignments"]}
    factors = []
    for factor in result["candidates"]:
        assignment = assignments[int(factor["factorIndex"])]
        factors.append({**factor, **assignment})
    summary = {
        "launched": 1,
        "completed": 1,
        "certified": 1,
        "resolved": 1,
        "exactFactors": factors,
        "rank11HitsBeforeLiveAudit": sum(
            str(row["targetLabel"]) == "24T9197" and int(row["targetR"]) == 16
            for row in factors
        ),
        "resultsSha256": exact.sha256(RESULTS),
        "certificateSha256": exact.sha256(CERTIFICATE),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    exact.shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

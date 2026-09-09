#!/usr/bin/env python3
"""Run the final two untested 24T1562/r12 pair packets exactly."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
from pathlib import Path

import agent_rank10_pair_stage2 as stage2


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "data" / "agent_rank10_pair_stage2_1562_results.jsonl"
CERTIFICATE = ROOT / "data" / "agent_rank10_pair_stage2_1562_frobenius_certificate.json"
ASSIGNED = ROOT / "data" / "agent_rank10_pair_stage2_1562_assigned_factors.jsonl"
SUMMARY = ROOT / "data" / "agent_rank10_pair_stage2_1562_summary.json"

TASKS = [
    {
        "pilotRank": 1,
        "submissionId": "sub_776c0c943f1042a1b2698dcde4dd5caf",
        "polynomialIndex": 69,
        "sourceLabel": "24T1562",
        "sourceT": 1562,
        "sourceR": 12,
        "sourceFieldDiscAbs": "2949136146623993218249377141202877776134144",
    },
    {
        "pilotRank": 2,
        "submissionId": "sub_1e115dea004741d1ad716e9b6717196b",
        "polynomialIndex": 110,
        "sourceLabel": "24T1562",
        "sourceT": 1562,
        "sourceR": 12,
        "sourceFieldDiscAbs": "20971634820437285107551126337442686408065024",
    },
]


def main() -> int:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda task: stage2.run_packet(task, timeout=600), TASKS)
        )
    results.sort(key=lambda row: int(row["pilotRank"]))
    stage2.shared.write_jsonl_atomic(
        RESULTS, results, sort_key=lambda row: int(row["pilotRank"])
    )
    if any(row.get("status") != "certified_multi" for row in results):
        raise RuntimeError("one or more 24T1562 packets failed exact extraction")

    completed = subprocess.run(
        [
            "sage",
            "-python",
            str(stage2.FROBENIUS_WORKER),
            "--input",
            str(RESULTS),
            "--prime-bound",
            "5000",
            "--require-resolved",
            "--output",
            str(CERTIFICATE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    certificate = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
    if completed.returncode != 0 or certificate["summary"]["resolved"] != 2:
        raise RuntimeError(
            f"Frobenius assignment failed: exit={completed.returncode}; "
            f"summary={certificate.get('summary')}; stderr={completed.stderr[-1200:]}"
        )

    packet_by_key = {
        (str(row["sourceSubmissionId"]), int(row["sourcePolynomialIndex"])): row
        for row in results
    }
    assigned = []
    for proof in certificate["rows"]:
        key = (
            str(proof["sourceSubmissionId"]),
            int(proof["sourcePolynomialIndex"]),
        )
        packet = packet_by_key[key]
        factors = {int(row["factorIndex"]): row for row in packet["candidates"]}
        for assignment in proof["assignments"]:
            factor = factors[int(assignment["factorIndex"])]
            assigned.append(
                {
                    **factor,
                    "sourceSubmissionId": key[0],
                    "sourcePolynomialIndex": key[1],
                    "sourceLabel": "24T1562",
                    "sourceR": 12,
                    "targetLabel": str(assignment["targetLabel"]),
                    "targetT": int(assignment["targetT"]),
                    "targetR": int(assignment["targetR"]),
                    "exactLabelProof": "joint_unramified_frobenius_cycle_profiles",
                }
            )
    stage2.shared.write_jsonl_atomic(
        ASSIGNED,
        assigned,
        sort_key=lambda row: (
            row["sourceSubmissionId"],
            int(row["sourcePolynomialIndex"]),
            int(row["factorIndex"]),
        ),
    )
    hits = [
        row
        for row in assigned
        if row["targetLabel"] == "24T1827" and int(row["targetR"]) == 12
    ]
    summary = {
        "packets": 2,
        "certifiedPackets": 2,
        "resolvedPackets": 2,
        "assignedFactors": len(assigned),
        "targetHitsBeforeLiveCheck": len(hits),
        "hitHashes": [row["coefficientSha256"] for row in hits],
        "results": str(RESULTS),
        "certificate": str(CERTIFICATE),
        "assigned": str(ASSIGNED),
        "resultsSha256": stage2.sha256(RESULTS),
        "certificateSha256": stage2.sha256(CERTIFICATE),
        "assignedSha256": stage2.sha256(ASSIGNED),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    stage2.shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

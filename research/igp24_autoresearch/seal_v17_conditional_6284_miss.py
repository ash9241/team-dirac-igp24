#!/usr/bin/env python3
"""Seal the exact miss for the isolated v17 24T6284/r4 packet."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import run_low_contention_sequential as lane
import stage_frobenius_gold as frobenius


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNBOOK = DATA / "v17_conditional_24T6284_r4_to_24T5971_r12_runbook.json"
CANDIDATES = DATA / "v17_conditional_24T6284_r4_pair_resolvent.jsonl"
FROBENIUS = DATA / "v17_conditional_24T6284_r4_frobenius_certificate.json"
CENSUS = DATA / "autopilot_pair_delta_20260722_v17/missing_pair_all.jsonl"
OUTPUT = DATA / "v17_conditional_24T6284_r4_exact_miss_certificate.json"
SUMMARY = DATA / "v17_conditional_24T6284_r4_exact_miss_summary.json"

SOURCE = ("24T6284", 4)
TARGET_LABEL = "24T5971"
REQUIRED = ("24T5971", 12)
MISS_R = {0, 4}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}


def write_exclusive(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    if OUTPUT.exists() or SUMMARY.exists():
        raise FileExistsError("v17 conditional miss seal already exists")

    runbook = read_json(RUNBOOK)
    if (
        runbook.get("schemaVersion") != "v17-conditional-isolated-factor-frobenius-runbook-v1"
        or runbook.get("coefficientMaterialIncluded") is not False
        or runbook.get("executionOrder") != ["factor", "frobenius"]
        or runbook.get("postResolutionGate", {}).get("requiredExactPair") != "24T5971/r12"
        or runbook.get("postResolutionGate", {}).get("submitAutomatically") is not False
    ):
        raise ValueError("isolated v17 runbook envelope changed")
    for pinned in runbook.get("pinnedEvidence", {}).values():
        path = ROOT / str(pinned["path"])
        if sha256(path) != str(pinned["sha256"]):
            raise ValueError(f"pinned evidence changed: {path}")

    census_rows = read_jsonl(CENSUS)
    source_rows = [row for row in census_rows if str(row.get("sourceLabel")) == SOURCE[0]]
    if len(source_rows) != 1 or source_rows[0].get("status") != "certified":
        raise ValueError("v17 source census row is missing or uncertified")
    matching_routes = [
        route
        for route in source_rows[0].get("routes") or []
        if int(route.get("sourceR", -1)) == SOURCE[1]
        and str(route.get("targetLabel")) == TARGET_LABEL
        and sorted(int(value) for value in route.get("mappedTargetR") or []) == [0, 4, 12]
    ]
    if len(matching_routes) != 1 or matching_routes[0].get("allCompatibleClassesGold") is not False:
        raise ValueError("v17 conditional route envelope changed")

    candidate_rows = read_jsonl(CANDIDATES)
    if len(candidate_rows) != 1:
        raise ValueError("isolated factor artifact must contain exactly one packet")
    packet = candidate_rows[0]
    if (
        packet.get("status") != "certified_multi"
        or int(packet.get("workerExitCode", -1)) != 0
        or (str(packet.get("sourceLabel")), int(packet.get("sourceR", -1))) != SOURCE
        or len(packet.get("candidates") or []) != 3
        or sorted(int(row["factorIndex"]) for row in packet.get("candidates") or []) != [0, 1, 2]
        or packet.get("orbitCertificate", {}).get("actualDegrees")
        != packet.get("orbitCertificate", {}).get("expectedDegrees")
    ):
        raise ValueError("isolated factor packet is not an exact certified three-factor packet")

    proof = read_json(FROBENIUS)
    joined = frobenius.join_resolved_assignments(
        proof, candidate_rows, CANDIDATES, allow_unresolved=False
    )
    if len(joined) != 3:
        raise ValueError("exact Frobenius certificate did not assign all three factors")
    exact_target = [
        row for row in joined if str(row["targetLabel"]) == TARGET_LABEL
    ]
    if len(exact_target) != 1:
        raise ValueError("target label did not resolve uniquely")
    resolved_pair = (TARGET_LABEL, int(exact_target[0]["targetR"]))
    required_hits = [
        row
        for row in joined
        if (str(row["targetLabel"]), int(row["targetR"])) == REQUIRED
    ]
    if required_hits or resolved_pair[1] not in MISS_R:
        raise ValueError("packet is not the authorized exact-miss outcome")

    public_assignments = [
        {
            "factorIndex": int(row["factorIndex"]),
            "targetPair": f"{row['targetLabel']}/r{int(row['targetR'])}",
            "candidateSha256": str(row["coefficientSha256"]),
        }
        for row in sorted(joined, key=lambda row: int(row["factorIndex"]))
    ]
    now = datetime.now(timezone.utc).isoformat()
    certificate = {
        "schemaVersion": "v17-conditional-24T6284-r4-exact-miss-v1",
        "createdAt": now,
        "status": "required_24T5971_r12_absent_exact_miss_sealed",
        "sourcePair": "24T6284/r4",
        "requiredPair": "24T5971/r12",
        "resolvedTargetPair": f"{resolved_pair[0]}/r{resolved_pair[1]}",
        "artifacts": {
            "runbook": artifact(RUNBOOK),
            "census": artifact(CENSUS),
            "factorPacket": artifact(CANDIDATES),
            "frobeniusCertificate": artifact(FROBENIUS),
        },
        "exactAssignments": public_assignments,
        "checks": {
            "pinnedEvidenceHashesMatch": True,
            "conditionalRouteEnvelopeMatches": True,
            "factorPacketCertifiedThreeFactors": True,
            "frobeniusResolved": True,
            "allFactorsAssignedExactlyOnce": True,
            "requiredPairAbsent": True,
            "resolvedOutcomeIsAuthorizedMissR0OrR4": True,
            "manifestNotCreated": True,
            "stagingSuppressed": True,
            "coefficientMaterialExcluded": True,
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "manifestsCreated": 0,
        },
    }
    write_exclusive(OUTPUT, certificate)
    summary = {
        "schemaVersion": "v17-conditional-24T6284-r4-exact-miss-summary-v1",
        "createdAt": now,
        "status": certificate["status"],
        "sourcePair": certificate["sourcePair"],
        "requiredPair": certificate["requiredPair"],
        "resolvedTargetPair": certificate["resolvedTargetPair"],
        "certificate": artifact(OUTPUT),
        "stagedRows": 0,
        "coefficientMaterialIncluded": False,
    }
    write_exclusive(SUMMARY, summary)
    print(json.dumps({
        "status": certificate["status"],
        "resolvedTargetPair": certificate["resolvedTargetPair"],
        "certificate": artifact(OUTPUT),
        "summary": artifact(SUMMARY),
        "stagedRows": 0,
        "submissionCalls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

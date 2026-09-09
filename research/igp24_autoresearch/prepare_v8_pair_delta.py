#!/usr/bin/env python3
"""Freeze and audit the exact v8 unordered-pair signature delta.

This is deliberately a light, offline preparer.  It does not import Sage or
GAP, touch the network, write the ledger, or submit anything.  The expensive
group census is left as one explicit command in the generated plan.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V7 = ROOT / "data/autopilot_pair_delta_20260722_v7"
V8 = ROOT / "data/autopilot_pair_delta_20260722_v8"
GROUP_INPUT = V8 / "group_input.jsonl"
CENSUS_INPUT = V8 / "census_input.jsonl"
INVENTORY = V8 / "exact_source_inventory.json"
PLAN = V8 / "provenance_plan.json"

EXPECTED_DELTA = {("24T6120", 24)}
SUBMISSION_ID = "sub_25a347df242a4160a65e25f21b7851e7"
SOURCE_PAIR = ("24T18462", 0)
F9_RESULTS = ROOT / "data/agent_f9_k4_18462_r0_results.jsonl"
F9_SUMMARY = ROOT / "data/agent_f9_k4_18462_r0_summary.json"
F9_PLAN = ROOT / "data/agent_f9_k4_incidence_pilot_plan.json"

PRIOR_MAPS = [
    "data/autopilot_pair_delta_20260721_v2/pair_prior_sources.jsonl",
    *[
        f"data/autopilot_pair_delta_20260721_v2/missing_pair_shard{index}.jsonl"
        for index in range(6)
    ],
    "data/autopilot_pair_delta_20260722_v3/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v4/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v5/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v6/missing_pair_all.jsonl",
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not one JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} contains a non-object row")
    return rows


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def source_pairs(rows: list[dict]) -> set[tuple[str, int]]:
    return {
        (str(row["label"]), int(signature))
        for row in rows
        if bool(row.get("isOwnedSource"))
        for signature in row.get("sourceR") or []
    }


def relative_artifact(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)}


def main() -> int:
    v7_input = V7 / "group_input.jsonl"
    v7_rows = read_jsonl(v7_input)
    v8_rows = read_jsonl(GROUP_INPUT)
    v7_pairs = source_pairs(v7_rows)
    v8_pairs = source_pairs(v8_rows)
    delta = v8_pairs - v7_pairs
    if delta != EXPECTED_DELTA or v7_pairs - v8_pairs:
        raise ValueError(f"unexpected v8 frozen-source delta: {sorted(delta)}")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    accepted = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    accepted_delta = accepted - v7_pairs
    if accepted_delta != EXPECTED_DELTA:
        raise ValueError(f"unexpected accepted delta from v7: {sorted(accepted_delta)}")
    if EXPECTED_DELTA & baseline:
        raise ValueError("v8 delta intersects the immutable baseline")

    verification = connection.execute(
        "SELECT status,label,r,scoreable,in_baseline FROM verifications "
        "WHERE submission_id=? AND polynomial_index=0",
        (SUBMISSION_ID,),
    ).fetchone()
    if verification != ("accepted", "24T6120", 24, 1, 0):
        raise ValueError("v8 source verification is not the expected exact gold row")

    target_row = connection.execute(
        "SELECT team_count,discovered,generated_at FROM targets "
        "WHERE label='24T6120' AND r=24"
    ).fetchone()
    if target_row is None:
        raise ValueError("v8 source pair is absent from the target snapshot")
    connection.close()

    receipt_path = ROOT / "receipts" / f"{SUBMISSION_ID}.json"
    receipt = read_json(receipt_path)
    response = receipt.get("response")
    if (
        receipt.get("commit") is not True
        or not isinstance(response, dict)
        or response.get("submissionId") != SUBMISSION_ID
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
        or int(receipt.get("polynomials", 1)) != 1
    ):
        raise ValueError("v8 receipt is not a clean one-polynomial commit")
    manifest = Path(str(receipt.get("manifest"))).resolve()
    if not manifest.is_file() or receipt.get("manifestHash") != sha256_path(manifest):
        raise ValueError("v8 receipt manifest hash mismatch")
    manifest_lines = manifest.read_text(encoding="utf-8").splitlines()
    if len(manifest_lines) != 1:
        raise ValueError("v8 receipt manifest is not one line")
    candidate_hash = sha256_bytes(manifest_lines[0].encode())

    summary = read_json(F9_SUMMARY)
    result_rows = read_jsonl(F9_RESULTS)
    if (
        summary.get("status") != "exact_hits_staged"
        or summary.get("family") != "F9_HIGHER_KUMMER_SUBSET_PRODUCTS"
        or summary.get("sourceSelector") != "24T18462/r0"
        or summary.get("stagedPairs") != ["24T6120/r24"]
        or int(summary.get("exactLiveHits", -1)) != 1
        or int(summary.get("stagedPolynomials", -1)) != 1
        or int(summary.get("networkCalls", -1)) != 0
        or int(summary.get("submissionCalls", -1)) != 0
        or len(result_rows) != 1
    ):
        raise ValueError("F9 summary/result envelope mismatch")
    declared_hashes = summary.get("artifactSha256") or {}
    if (
        declared_hashes.get("results") != sha256_path(F9_RESULTS)
        or declared_hashes.get("plan") != sha256_path(F9_PLAN)
        or declared_hashes.get("manifest") != sha256_path(manifest)
    ):
        raise ValueError("F9 declared artifact hash mismatch")

    result = result_rows[0]
    source = result.get("source") or {}
    live = result.get("liveCandidates") or []
    assignment = result.get("assignmentCertificate") or {}
    if (
        result.get("status") != "exact_live_hit"
        or (source.get("label"), int(source.get("r", -1))) != SOURCE_PAIR
        or int(result.get("networkCalls", -1)) != 0
        or int(result.get("submissionCalls", -1)) != 0
        or int(assignment.get("assignmentCount", -1)) != 1
        or int(assignment.get("targetLabelAssignmentCount", -1)) != 1
        or len(live) != 1
    ):
        raise ValueError("F9 exact assignment certificate mismatch")
    live_row = live[0]
    live_target = live_row.get("target") or {}
    if (
        (live_target.get("label"), int(live_target.get("r", -1)))
        != ("24T6120", 24)
        or live_row.get("coefficientSha256") != candidate_hash
        or not all(bool(live_row.get(key)) for key in ("freshHash", "irreducible", "liveHit"))
    ):
        raise ValueError("F9 live candidate does not match the committed manifest")

    # Preserve the complete current gold snapshot, but expose only signatures
    # absent from the v7 frozen source map to the expensive census worker.
    delta_by_label: dict[str, set[int]] = {}
    for label, r in delta:
        delta_by_label.setdefault(label, set()).add(r)
    census_rows = []
    for row in v8_rows:
        selected = sorted(delta_by_label.get(str(row["label"]), set()))
        census_rows.append(
            {
                **row,
                "isOwnedSource": bool(selected),
                "sourceR": selected,
            }
        )
    if source_pairs(census_rows) != EXPECTED_DELTA:
        raise ValueError("v8 census input does not isolate exactly the source delta")
    atomic_text(
        CENSUS_INPUT,
        "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in census_rows
        ),
    )

    inventory = {
        "schemaVersion": "v8-exact-source-inventory-v1",
        "status": "certified",
        "base": {
            "v7GroupInput": relative_artifact(v7_input),
            "v7ProvenancePlan": relative_artifact(V7 / "provenance_plan.json"),
        },
        "v8GroupInput": relative_artifact(GROUP_INPUT),
        "deltaPairs": ["24T6120:24"],
        "deltaPairCount": 1,
        "verifiedPairCount": 1,
        "verification": {
            "submissionId": SUBMISSION_ID,
            "polynomialIndex": 0,
            "receipt": relative_artifact(receipt_path),
            "manifest": relative_artifact(manifest),
            "coefficientSha256": candidate_hash,
        },
        "upstreamExactConstruction": {
            "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS",
            "sourcePair": "24T18462:0",
            "results": relative_artifact(F9_RESULTS),
            "summary": relative_artifact(F9_SUMMARY),
            "plan": relative_artifact(F9_PLAN),
            "assignmentCount": 1,
            "targetLabelAssignmentCount": 1,
        },
        "collisionCensus": {
            "baselinePairCollisions": 0,
            "duplicateDeltaPairs": 0,
            "priorOwnedPairCollisions": 0,
            "unaccountedAcceptedPairs": 0,
            "receiptFailedPolynomials": 0,
            "receiptRejectedPolynomials": 0,
        },
        "targetSnapshot": {
            "pair": "24T6120:24",
            "teamCount": int(target_row[0]),
            "discovered": bool(target_row[1]),
            "generatedAt": str(target_row[2]),
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    atomic_json(INVENTORY, inventory)

    command = [
        "caffeinate",
        "-i",
        "sage",
        "-python",
        "agent_index24_missing_pair_census.sage.py",
        "--input",
        str(CENSUS_INPUT.relative_to(ROOT)),
    ]
    for prior_map in PRIOR_MAPS:
        command.extend(["--prior-map", prior_map])
    command.extend(
        [
            "--output",
            "data/autopilot_pair_delta_20260722_v8/missing_pair_all.jsonl",
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    plan = {
        "schemaVersion": "v8-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker",
        "base": {
            "v7GroupInput": relative_artifact(v7_input),
            "v7ProvenancePlan": relative_artifact(V7 / "provenance_plan.json"),
        },
        "artifacts": {
            "groupInput": relative_artifact(GROUP_INPUT),
            "groupInputSummary": relative_artifact(V8 / "group_input_summary.json"),
            "censusInput": relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": relative_artifact(INVENTORY),
            "preparer": relative_artifact(Path(__file__).resolve()),
            "priorMaps": [relative_artifact(ROOT / path) for path in PRIOR_MAPS],
            "worker": relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"),
        },
        "delta": {
            "selectedLabels": 1,
            "selectedSignatures": 1,
            "distinctPairs": 1,
            "pairs": ["24T6120:24"],
            "acceptedLedgerDeltaPairs": 1,
        },
        "routeExpectation": {
            "sourcePair": "24T6120:24",
            "unorderedPairSetSize": 276,
            "maximumLength24Orbits": 11,
            "expectedLiveRouteCeiling": 11,
            "deterministicTargetR": 24,
            "reason": (
                "For source signature r=24, complex conjugation is the identity, "
                "so every degree-24 unordered-pair action has target r=24.  The "
                "276 unordered pairs contain at most floor(276/24)=11 length-24 "
                "orbits; the exact live subset remains to be censused."
            ),
            "status": "rigorous_upper_bound_not_yet_censused",
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "selectedWorkerRows": 1,
            "plannedCommand": command,
            "requiresRootHeavyWorkerClearance": True,
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    atomic_json(PLAN, plan)
    print(
        json.dumps(
            {
                "status": "ready_for_one_heavy_worker",
                "deltaPairs": 1,
                "selectedWorkerRows": 1,
                "expectedLiveRouteCeiling": 11,
                "plan": str(PLAN.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freeze and audit the exact v9 unordered-pair signature delta.

This is a light, offline preparer.  It does not import Sage or GAP, touch the
network, write the ledger, or submit.  It pins the completed v8 census and the
three newly verified source signatures, then emits one explicit heavy-worker
command for the signature-aware v9 census.
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
V8 = ROOT / "data/autopilot_pair_delta_20260722_v8"
V9 = ROOT / "data/autopilot_pair_delta_20260722_v9"
GROUP_INPUT = V9 / "group_input.jsonl"
CENSUS_INPUT = V9 / "census_input.jsonl"
INVENTORY = V9 / "exact_source_inventory.json"
PLAN = V9 / "provenance_plan.json"

EXPECTED_DELTA = {
    ("24T9833", 24): {
        "submissionId": "sub_6b7e603b96bc4b358fc6d3302bc2b068",
        "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS",
        "results": ROOT / "data/agent_f9_k3_18035_r24_results.jsonl",
        "summary": ROOT / "data/agent_f9_k3_18035_r24_summary.json",
    },
    ("24T15218", 8): {
        "submissionId": "sub_f4dafb7696d249d4bbe72fb987faf101",
        "family": "EVEN_GENERIC_QUADRATIC_TWIST",
        "results": ROOT / "data/autopilot_twist_verified_delta_v2.jsonl",
        "summary": ROOT / "data/autopilot_twist_verified_delta_v2_summary.json",
    },
    ("24T20075", 24): {
        "submissionId": "sub_b53d2b30b49c4c71bc6dee4442dd7c5b",
        "family": "UNORDERED_PAIR_POLYNOMIAL",
        "results": ROOT / "data/autopilot_pair_19729_to_20075_candidates.jsonl",
        "summary": ROOT / "data/autopilot_pair_19729_to_20075_stage_summary.json",
    },
}

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
    "data/autopilot_pair_delta_20260722_v8/missing_pair_all.jsonl",
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


def certificate_is_exact(row: dict) -> bool:
    declared = row.get("exactCertificateSha256")
    unsigned = {key: value for key, value in row.items() if key != "exactCertificateSha256"}
    actual = sha256_bytes(
        json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()
    )
    return isinstance(declared, str) and declared == actual


def result_hashes(rows: list[dict]) -> set[str]:
    hashes: set[str] = set()
    for row in rows:
        value = row.get("coefficientSha256")
        if isinstance(value, str):
            hashes.add(value)
        for candidate in row.get("candidates") or []:
            value = candidate.get("coefficientSha256")
            if isinstance(value, str):
                hashes.add(value)
    return hashes


def prior_action(source_label: str) -> dict | None:
    matches = []
    for relative in PRIOR_MAPS:
        for row in read_jsonl(ROOT / relative):
            if row.get("sourceLabel") == source_label:
                matches.append(row)
    if not matches:
        return None
    # A source can occur in several signature-specific maps.  Its abstract
    # unordered-pair action must nevertheless be identical in every copy.
    signatures = {
        (
            int(row.get("length24OrbitCount", -1)),
            json.dumps(row.get("targetCounts") or {}, sort_keys=True),
        )
        for row in matches
    }
    if len(signatures) != 1:
        raise ValueError(f"conflicting prior unordered-pair actions for {source_label}")
    return matches[-1]


def validate_upstream(pair: tuple[str, int], spec: dict, candidate_hash: str) -> None:
    summary_path = Path(spec["summary"])
    results_path = Path(spec["results"])
    summary = read_json(summary_path)
    rows = read_jsonl(results_path)
    if candidate_hash not in result_hashes(rows):
        raise ValueError(f"committed candidate hash is absent from upstream results for {pair}")

    if pair == ("24T9833", 24):
        if (
            summary.get("status") != "exact_hit_staged"
            or summary.get("family") != spec["family"]
            or summary.get("target") != {"label": pair[0], "r": pair[1]}
            or summary.get("stagedPairs") != ["24T9833/r24"]
            or int(summary.get("networkCalls", -1)) != 0
            or int(summary.get("submissionCalls", -1)) != 0
            or (summary.get("artifactSha256") or {}).get("results")
            != sha256_path(results_path)
        ):
            raise ValueError("k=3 exact-construction envelope mismatch")
    elif pair == ("24T15218", 8):
        live = [
            row
            for row in rows
            if row.get("targetLabel") == pair[0]
            and int(row.get("targetR", -1)) == pair[1]
            and bool(row.get("targetLiveGold"))
        ]
        if (
            len(live) != 1
            or live[0].get("coefficientSha256") != candidate_hash
            or int(summary.get("certifiedCandidates", -1)) != 1
            or int(summary.get("distinctTargetPairs", -1)) != 1
            or int(summary.get("networkCalls", -1)) != 0
            or int(summary.get("submissionCalls", -1)) != 0
            or summary.get("candidateOutputSha256") != sha256_path(results_path)
        ):
            raise ValueError("twist exact-construction envelope mismatch")
    elif pair == ("24T20075", 24):
        exact = [
            row
            for row in rows
            if row.get("status") == "certified"
            and row.get("targetLabel") == pair[0]
            and int(row.get("targetR", -1)) == pair[1]
        ]
        if (
            len(exact) != 1
            or exact[0].get("coefficientSha256") != candidate_hash
            or int(summary.get("selected", -1)) != 1
            or summary.get("inputSha256") != sha256_path(results_path)
        ):
            raise ValueError("pair exact-construction envelope mismatch")


def main() -> int:
    v8_input = V8 / "group_input.jsonl"
    v8_rows = read_jsonl(v8_input)
    v9_rows = read_jsonl(GROUP_INPUT)
    v8_pairs = source_pairs(v8_rows)
    v9_pairs = source_pairs(v9_rows)
    delta = v9_pairs - v8_pairs
    if delta != set(EXPECTED_DELTA) or v8_pairs - v9_pairs:
        raise ValueError(f"unexpected v9 frozen-source delta: {sorted(delta)}")

    v8_census_path = V8 / "missing_pair_all.jsonl"
    v8_census = read_jsonl(v8_census_path)
    if (
        len(v8_census) != 1
        or v8_census[0].get("status") != "certified"
        or v8_census[0].get("sourceLabel") != "24T6120"
        or v8_census[0].get("sourceR") != [24]
        or not certificate_is_exact(v8_census[0])
    ):
        raise ValueError("completed v8 census is not the expected exact certificate")

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
    accepted_delta = accepted - v8_pairs
    if accepted_delta != set(EXPECTED_DELTA):
        raise ValueError(f"unexpected accepted delta from v8: {sorted(accepted_delta)}")
    if set(EXPECTED_DELTA) & baseline:
        raise ValueError("v9 delta intersects the immutable baseline")

    verification_inventory = []
    target_snapshot = []
    for pair, spec in sorted(EXPECTED_DELTA.items()):
        label, r = pair
        submission_id = str(spec["submissionId"])
        verification = connection.execute(
            "SELECT status,label,r,scoreable,in_baseline FROM verifications "
            "WHERE submission_id=? AND polynomial_index=0",
            (submission_id,),
        ).fetchone()
        if verification != ("accepted", label, r, 1, 0):
            raise ValueError(f"v9 source verification mismatch for {pair}")
        target_row = connection.execute(
            "SELECT team_count,discovered,generated_at FROM targets "
            "WHERE label=? AND r=?",
            pair,
        ).fetchone()
        if target_row is None:
            raise ValueError(f"v9 source pair is absent from target snapshot: {pair}")

        receipt_path = ROOT / "receipts" / f"{submission_id}.json"
        receipt = read_json(receipt_path)
        response = receipt.get("response")
        if (
            receipt.get("commit") is not True
            or not isinstance(response, dict)
            or response.get("submissionId") != submission_id
            or int(response.get("rejectedCount", -1)) != 0
            or list(response.get("failedPolynomials") or [])
            or int(receipt.get("polynomials", -1)) != 1
        ):
            raise ValueError(f"v9 receipt is not a clean one-polynomial commit: {pair}")
        manifest = Path(str(receipt.get("manifest"))).resolve()
        if not manifest.is_file() or receipt.get("manifestHash") != sha256_path(manifest):
            raise ValueError(f"v9 receipt manifest hash mismatch: {pair}")
        lines = [line for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        if len(lines) != 1:
            raise ValueError(f"v9 receipt manifest is not one line: {pair}")
        candidate_hash = sha256_bytes(lines[0].encode())
        validate_upstream(pair, spec, candidate_hash)

        verification_inventory.append(
            {
                "pair": f"{label}:{r}",
                "family": spec["family"],
                "submissionId": submission_id,
                "polynomialIndex": 0,
                "coefficientSha256": candidate_hash,
                "receipt": relative_artifact(receipt_path),
                "manifest": relative_artifact(manifest),
                "upstreamResults": relative_artifact(Path(spec["results"])),
                "upstreamSummary": relative_artifact(Path(spec["summary"])),
            }
        )
        target_snapshot.append(
            {
                "pair": f"{label}:{r}",
                "teamCount": int(target_row[0]),
                "discovered": bool(target_row[1]),
                "generatedAt": str(target_row[2]),
            }
        )
    connection.close()

    # Preserve the complete current gold snapshot while exposing only source
    # signatures newly added since v8 to the signature-aware worker.
    delta_by_label: dict[str, set[int]] = {}
    for label, r in delta:
        delta_by_label.setdefault(label, set()).add(r)
    census_rows = []
    for row in v9_rows:
        selected = sorted(delta_by_label.get(str(row["label"]), set()))
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if source_pairs(census_rows) != set(EXPECTED_DELTA):
        raise ValueError("v9 census input does not isolate exactly the signature delta")
    atomic_text(
        CENSUS_INPUT,
        "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in census_rows
        ),
    )

    action_15218 = prior_action("24T15218")
    action_20075 = prior_action("24T20075")
    action_9833 = prior_action("24T9833")
    if (
        action_9833 is not None
        or action_15218 is None
        or int(action_15218.get("length24OrbitCount", -1)) != 1
        or action_15218.get("targetCounts") != {"24T15253": 1}
        or action_20075 is None
        or int(action_20075.get("length24OrbitCount", -1)) != 1
        or action_20075.get("targetCounts") != {"24T19729": 1}
    ):
        raise ValueError("unexpected prior abstract unordered-pair action envelope")
    target_15218 = (action_15218.get("targets") or [None])[0]
    if not isinstance(target_15218, dict) or int(target_15218.get("kernelOrder", -1)) != 1:
        raise ValueError("24T15218 prior pair action is not the expected faithful action")
    gold_by_label = {
        str(row["label"]): {int(value) for value in row.get("goldR") or []}
        for row in v9_rows
    }
    owned_by_label = {
        str(row["label"]): {int(value) for value in row.get("sourceR") or []}
        for row in v9_rows
    }
    if 24 not in owned_by_label.get("24T19729", set()):
        raise ValueError("known reciprocal target 24T19729/r24 is not frozen as owned")

    inventory = {
        "schemaVersion": "v9-exact-source-inventory-v1",
        "status": "certified",
        "base": {
            "v8GroupInput": relative_artifact(v8_input),
            "v8ProvenancePlan": relative_artifact(V8 / "provenance_plan.json"),
            "v8CompletedCensus": relative_artifact(v8_census_path),
            "v8ExactCertificateSha256": v8_census[0]["exactCertificateSha256"],
        },
        "v9GroupInput": relative_artifact(GROUP_INPUT),
        "deltaPairs": [f"{label}:{r}" for label, r in sorted(delta)],
        "deltaPairCount": len(delta),
        "verifiedPairCount": len(verification_inventory),
        "verifications": verification_inventory,
        "collisionCensus": {
            "baselinePairCollisions": 0,
            "duplicateDeltaPairs": 0,
            "priorFrozenSignatureCollisions": 0,
            "unaccountedAcceptedPairs": 0,
            "receiptFailedPolynomials": 0,
            "receiptRejectedPolynomials": 0,
        },
        "targetSnapshot": target_snapshot,
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
            "--signature-aware",
            "--prior-input",
            str(v8_input.relative_to(ROOT)),
            "--output",
            "data/autopilot_pair_delta_20260722_v9/missing_pair_all.jsonl",
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    plan = {
        "schemaVersion": "v9-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker",
        "base": {
            "v8GroupInput": relative_artifact(v8_input),
            "v8ProvenancePlan": relative_artifact(V8 / "provenance_plan.json"),
            "v8CompletedCensus": relative_artifact(v8_census_path),
        },
        "artifacts": {
            "groupInput": relative_artifact(GROUP_INPUT),
            "groupInputSummary": relative_artifact(V9 / "group_input_summary.json"),
            "censusInput": relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": relative_artifact(INVENTORY),
            "preparer": relative_artifact(Path(__file__).resolve()),
            "priorMaps": [relative_artifact(ROOT / path) for path in PRIOR_MAPS],
            "worker": relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"),
        },
        "delta": {
            "selectedLabels": len({label for label, _ in delta}),
            "selectedSignatures": len(delta),
            "distinctPairs": len(delta),
            "pairs": [f"{label}:{r}" for label, r in sorted(delta)],
            "acceptedLedgerDeltaPairs": len(accepted_delta),
            "signatureBaseline": "v8 frozen group input",
        },
        "routeExpectation": {
            "unorderedPairSetSizePerSource": 276,
            "sources": [
                {
                    "sourcePair": "24T9833:24",
                    "knownLength24Orbits": None,
                    "structuralRouteCeiling": 11,
                    "currentLiveRouteCeiling": 11,
                    "deterministicTargetR": 24,
                    "reason": (
                        "This label is absent from every unordered-pair map through v8. "
                        "The 276 unordered pairs contain at most floor(276/24)=11 "
                        "length-24 orbits; full reality forces every target signature to 24."
                    ),
                },
                {
                    "sourcePair": "24T15218:8",
                    "knownLength24Orbits": 1,
                    "knownTargetLabel": "24T15253",
                    "structuralRouteCeiling": 1,
                    "currentLiveRouteCeiling": 1,
                    "knownTargetGoldR": sorted(gold_by_label.get("24T15253", set())),
                    "reason": (
                        "The abstract label action through v8 has one faithful length-24 "
                        "orbit.  Signature r=8 is new and requires exact class profiling."
                    ),
                },
                {
                    "sourcePair": "24T20075:24",
                    "knownLength24Orbits": 1,
                    "knownTargetLabel": "24T19729",
                    "structuralRouteCeiling": 1,
                    "currentLiveRouteCeiling": 0,
                    "deterministicTargetR": 24,
                    "reason": (
                        "The sole prior unordered-pair orbit is the exact reciprocal "
                        "24T19729 action, and 24T19729/r24 is already owned."
                    ),
                },
            ],
            "totalStructuralRouteCeiling": 13,
            "totalCurrentLiveRouteCeiling": 12,
            "status": "rigorous_upper_bounds_not_yet_signature_censused",
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "selectedWorkerRows": len(delta),
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
                "deltaPairs": len(delta),
                "selectedWorkerRows": len(delta),
                "structuralRouteCeiling": 13,
                "currentLiveRouteCeiling": 12,
                "plan": str(PLAN.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

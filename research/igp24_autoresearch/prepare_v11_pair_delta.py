#!/usr/bin/env python3
"""Freeze and audit the exact v11 unordered-pair signature delta.

This is a light, offline preparer.  It does not import Sage or GAP, touch the
network, write the ledger, or submit.  It advances the frozen v10 boundary by
exactly the two post-v10 verified sources 24T5971/r24 and 24T15253/r0,
validates both receipt/construction chains, and emits one explicit command for
a signature-aware, one-worker v11 census.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V10 = ROOT / "data/autopilot_pair_delta_20260722_v10"
V11 = ROOT / "data/autopilot_pair_delta_20260722_v11"
GROUP_INPUT = V11 / "group_input.jsonl"
GROUP_SUMMARY = V11 / "group_input_summary.json"
CENSUS_INPUT = V11 / "census_input.jsonl"
INVENTORY = V11 / "exact_source_inventory.json"
PLAN = V11 / "provenance_plan.json"

SOURCE_5971 = ("24T5971", 24)
SOURCE_5971_SUBMISSION = "sub_b8c16cf7b95b4cc9b642403bf1bd2479"
SOURCE_5971_HASH = "3f1f10bfdcd3fee8135761398c774f6fa3e86feb19ac2ef924bf6dc1853c537e"
SOURCE_5971_RECEIPT = ROOT / f"receipts/{SOURCE_5971_SUBMISSION}.json"
SOURCE_5971_MANIFEST = (
    ROOT / "outbox/autopilot_pair_delta_20260722_v10_safe_5971_gold.txt"
)

SOURCE_15253 = ("24T15253", 0)
SOURCE_15253_SUBMISSION = "sub_256cffd2819942ce9dbb4e06eed3c869"
SOURCE_15253_HASH = "3f4877e3c6f249e7a551a76d2a43e516281ef582cfb675e57f636871a3acb00f"
SOURCE_15253_RECEIPT = ROOT / f"receipts/{SOURCE_15253_SUBMISSION}.json"
SOURCE_15253_MANIFEST = ROOT / "outbox/autopilot_pair_v9_15218_to_15253_gold.txt"
SOURCE_PAIRS = {SOURCE_5971, SOURCE_15253}

V9_CENSUS = ROOT / "data/autopilot_pair_delta_20260722_v9/missing_pair_all.jsonl"
V9_CANDIDATES = ROOT / "data/autopilot_pair_v9_15218_to_15253_candidates.jsonl"
V9_STAGE = ROOT / "data/autopilot_pair_v9_15218_to_15253_stage_summary.json"
V10_CENSUS = V10 / "missing_pair_all.jsonl"
V10_CANDIDATES = V10 / "safe_6284_multi_candidates.jsonl"
V10_FROBENIUS = V10 / "safe_6284_frobenius.json"
V10_STAGE = V10 / "safe_6284_stage_certificate.json"

# These hashes make the intended checkpoint explicit.  A regenerated or
# edited upstream artifact must be consciously re-audited, not silently
# absorbed by the next delta.
PINNED_SHA256 = {
    "v10GroupInput": "1a517d89b41b47416991d57c79ece50772a46a88db60a3d7aa9dd9c728d2b8ec",
    "v10CensusInput": "7c45e149241162d0e11aaade619a61a6f7d7d77167ccf1b8b498d1c2c132f351",
    "v10Inventory": "1fa78a670cd772dcaee43c2a40135eda2fbcf0cedacaa2fa3c01f807194778a6",
    "v10Plan": "33e05880c6e5c11dec8b08822fec5eaaefb2554b3cc8fad96896cf5b38b7bb65",
    "v10Preparer": "19721dd038794adcf3a881fcf6f5383c6fcb69ce051948ac9a0527ceb3720243",
    "v10CompletedCensus": "0e3f6796ba97560b511adcaad7c05fa818852ed9e30070c257b9fd47cb61fca6",
    "v9CompletedCensus": "0df7b1227072728f96c343b47ed652a26ea591c5a616e42dc3070ea99ce6f1a7",
    "receipt5971": "01ac820fed516e26bb3715843bc3c646c5a534c850d73a95e50e8c9dbe195955",
    "manifest5971": "2042054d95187cd4abc6ee3c7ebab5fde55e03127d6cbac2ce2ecfcd1a4c69f1",
    "candidates5971": "80e18566a3fb18944d93ffb72e88db1676541b9a81dc661f558b11271295f4fe",
    "frobenius5971": "9bd356012156b3ce017e039122c1e4581fc42232ebf59c23272c97a1caf69e9a",
    "stage5971": "314f90cd741d97e49131e4ad062eb2858f1def0742e98e21d7d97c8d0830c2b9",
    "receipt15253": "43cec2d8c0bc5571e3dd881784ad37d7785a5ad1d6d9ff0b9fa1e7f563506218",
    "manifest15253": "6d74986a9ecf8ce2a301013d3889d8661c6d09840c713d86515e8ebf988072c5",
    "candidates15253": "3a6671798e92897426e8d9586d5e6eedcd243587c21a2d697d2ca71bccc7ad9b",
    "stage15253": "d630d30bd61b3f3048387e2c27646cb162b66e7700719ac13f0a7eda6d64579c",
}

EXPECTED_V10_DELTA = {
    ("24T3408", 4),
    ("24T4031", 8),
    ("24T6176", 8),
    ("24T6192", 8),
    ("24T6284", 24),
    ("24T8535", 0),
    ("24T8893", 0),
    ("24T11835", 8),
    ("24T13578", 8),
    ("24T13786", 16),
    ("24T14576", 0),
    ("24T15963", 16),
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
    "data/autopilot_pair_delta_20260722_v9/missing_pair_all.jsonl",
    "data/autopilot_pair_delta_20260722_v10/missing_pair_all.jsonl",
]

PAIR_RE = re.compile(r"(24T[1-9][0-9]*)/r(0|2|4|6|8|10|12|14|16|18|20|22|24)\Z")


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


def canonical_jsonl(rows: list[dict]) -> str:
    return "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )


def source_pairs(rows: list[dict]) -> set[tuple[str, int]]:
    return {
        (str(row["label"]), int(signature))
        for row in rows
        if bool(row.get("isOwnedSource"))
        for signature in row.get("sourceR") or []
    }


def gold_pairs(rows: list[dict]) -> set[tuple[str, int]]:
    return {
        (str(row["label"]), int(signature))
        for row in rows
        if bool(row.get("isGoldTarget"))
        for signature in row.get("goldR") or []
    }


def relative_artifact(path: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"artifact escapes project root: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)}


def rooted_artifact(value: object) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError(f"invalid project artifact: {value!r}")
    return path


def require_pinned(path: Path, key: str) -> None:
    actual = sha256_path(path)
    if actual != PINNED_SHA256[key]:
        raise ValueError(f"pinned {key} hash mismatch: {actual}")


def certificate_is_exact(row: dict) -> bool:
    declared = row.get("exactCertificateSha256")
    unsigned = {key: value for key, value in row.items() if key != "exactCertificateSha256"}
    actual = sha256_bytes(
        json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()
    )
    return isinstance(declared, str) and declared == actual


def validated_coefficient_line(line: str, expected_hash: str) -> None:
    if sha256_bytes(line.encode()) != expected_hash:
        raise ValueError("source coefficient hash mismatch")
    try:
        coefficients = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError("source coefficient line is not integral") from exc
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("source polynomial is not monic of degree 24")
    if coefficients[0] == 0 or math.gcd(*coefficients) != 1:
        raise ValueError("source polynomial is not primitive with nonzero constant")


def validate_v10_checkpoint() -> tuple[list[dict], list[dict], dict]:
    pinned = {
        V10 / "group_input.jsonl": "v10GroupInput",
        V10 / "census_input.jsonl": "v10CensusInput",
        V10 / "exact_source_inventory.json": "v10Inventory",
        V10 / "provenance_plan.json": "v10Plan",
        ROOT / "prepare_v10_pair_delta.py": "v10Preparer",
        V10_CENSUS: "v10CompletedCensus",
    }
    for path, key in pinned.items():
        require_pinned(path, key)

    group_rows = read_jsonl(V10 / "group_input.jsonl")
    census_input_rows = read_jsonl(V10 / "census_input.jsonl")
    census_rows = read_jsonl(V10_CENSUS)
    inventory = read_json(V10 / "exact_source_inventory.json")
    plan = read_json(V10 / "provenance_plan.json")

    expected_strings = {f"{label}:{r}" for label, r in EXPECTED_V10_DELTA}
    if (
        inventory.get("schemaVersion") != "v10-exact-source-inventory-v1"
        or inventory.get("status") != "certified"
        or set(inventory.get("deltaPairs") or []) != expected_strings
        or int(inventory.get("deltaPairCount", -1)) != len(EXPECTED_V10_DELTA)
        or int(inventory.get("verifiedPairCount", -1)) != len(EXPECTED_V10_DELTA)
    ):
        raise ValueError("pinned v10 exact-source inventory envelope mismatch")
    if (
        plan.get("schemaVersion") != "v10-pair-delta-provenance-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or set((plan.get("delta") or {}).get("pairs") or []) != expected_strings
        or int((plan.get("execution") or {}).get("selectedWorkerRows", -1))
        != len(EXPECTED_V10_DELTA)
    ):
        raise ValueError("pinned v10 provenance-plan envelope mismatch")
    plan_artifacts = plan.get("artifacts") or {}
    expected_plan_artifacts = {
        "groupInput": V10 / "group_input.jsonl",
        "censusInput": V10 / "census_input.jsonl",
        "exactSourceInventory": V10 / "exact_source_inventory.json",
        "preparer": ROOT / "prepare_v10_pair_delta.py",
    }
    for name, path in expected_plan_artifacts.items():
        item = plan_artifacts.get(name) or {}
        if rooted_artifact(item.get("path")) != path.resolve() or item.get(
            "sha256"
        ) != sha256_path(path):
            raise ValueError(f"v10 plan does not pin {name}")

    census_pairs = {
        (str(row.get("sourceLabel")), int(signature))
        for row in census_rows
        for signature in row.get("sourceR") or []
    }
    if (
        len(census_rows) != len(EXPECTED_V10_DELTA)
        or census_pairs != EXPECTED_V10_DELTA
        or source_pairs(census_input_rows) != EXPECTED_V10_DELTA
        or any(row.get("status") != "certified" for row in census_rows)
        or any(not certificate_is_exact(row) for row in census_rows)
    ):
        raise ValueError("completed v10 census is not the exact 12-pair certificate set")
    return group_rows, census_rows, plan


def validate_5971_provenance(census_rows: list[dict]) -> tuple[str, dict]:
    for path, key in (
        (SOURCE_5971_RECEIPT, "receipt5971"),
        (SOURCE_5971_MANIFEST, "manifest5971"),
        (V10_CANDIDATES, "candidates5971"),
        (V10_FROBENIUS, "frobenius5971"),
        (V10_STAGE, "stage5971"),
    ):
        require_pinned(path, key)

    receipt = read_json(SOURCE_5971_RECEIPT)
    response = receipt.get("response") or {}
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != 1
        or response.get("submissionId") != SOURCE_5971_SUBMISSION
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
        or rooted_artifact(receipt.get("manifest")) != SOURCE_5971_MANIFEST.resolve()
        or receipt.get("manifestHash") != PINNED_SHA256["manifest5971"]
    ):
        raise ValueError("v11 source receipt envelope is not clean")
    lines = SOURCE_5971_MANIFEST.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError("v11 source manifest does not contain exactly one polynomial")
    line = lines[0]
    validated_coefficient_line(line, SOURCE_5971_HASH)

    source_census = [
        row
        for row in census_rows
        if row.get("sourceLabel") == "24T6284" and row.get("sourceR") == [24]
    ]
    if len(source_census) != 1:
        raise ValueError("v10 census has no unique 24T6284/r24 source row")
    structural = source_census[0]
    if (
        structural.get("length24OrbitCount") != 3
        or structural.get("targetCounts") != {"24T5971": 1, "24T6120": 2}
        or len(structural.get("routes") or []) != 1
        or (structural.get("routes") or [])[0].get("targetLabel") != SOURCE_5971[0]
        or int((structural.get("routes") or [])[0].get("deterministicTargetR", -1))
        != SOURCE_5971[1]
        or (structural.get("routes") or [])[0].get("allCompatibleClassesGold")
        is not True
    ):
        raise ValueError("v10 structural route to 24T5971/r24 is not exact and safe")

    stage = read_json(V10_STAGE)
    if (
        stage.get("schema") != "igp24-v10-safe-multifactor-stage-v1"
        or stage.get("status") != "staged_exact"
        or any(int(stage.get(name, -1)) != 0 for name in ("networkCalls", "submissionCalls", "ledgerWrites"))
        or stage.get("routeMode") != "safe"
    ):
        raise ValueError("v10 source stage envelope mismatch")
    stage_source = stage.get("source") or {}
    if (
        stage_source.get("submissionId") != "sub_ac9f8aef820b4ccd82600db05884ecd6"
        or int(stage_source.get("polynomialIndex", -1)) != 0
        or stage_source.get("label") != "24T6284"
        or int(stage_source.get("r", -1)) != 24
        or stage_source.get("coefficientSha256")
        != "22c34cf78c0a1110c0684de15e802a191a5bb1d6368745464d761c52c2e228fc"
        or stage_source.get("status") != "accepted"
        or stage_source.get("scoreable") is not True
    ):
        raise ValueError("v10 construction source provenance mismatch")
    for name, expected_path, expected_hash in (
        ("structuralCertificate", V10_CENSUS, PINNED_SHA256["v10CompletedCensus"]),
        ("candidates", V10_CANDIDATES, PINNED_SHA256["candidates5971"]),
        ("frobenius", V10_FROBENIUS, PINNED_SHA256["frobenius5971"]),
        ("manifest", SOURCE_5971_MANIFEST, PINNED_SHA256["manifest5971"]),
    ):
        item = stage.get(name) or {}
        if rooted_artifact(item.get("path")) != expected_path.resolve() or item.get(
            "sha256"
        ) != expected_hash:
            raise ValueError(f"v10 stage does not pin {name}")
    structural_stage = stage.get("structuralCertificate") or {}
    if (
        structural_stage.get("sourceExactCertificateSha256")
        != structural.get("exactCertificateSha256")
        or int(structural_stage.get("length24OrbitCount", -1)) != 3
        or structural_stage.get("targetMultiset")
        != ["24T5971", "24T6120", "24T6120"]
        or structural_stage.get("allCompatibleClassesGold") is not True
        or int(structural_stage.get("deterministicTargetR", -1)) != 24
    ):
        raise ValueError("v10 stage structural certificate mismatch")

    selected = stage.get("selected") or []
    if (
        len(selected) != 1
        or selected[0].get("label") != SOURCE_5971[0]
        or int(selected[0].get("r", -1)) != SOURCE_5971[1]
        or int(selected[0].get("factorIndex", -1)) != 1
        or selected[0].get("coefficientSha256") != SOURCE_5971_HASH
    ):
        raise ValueError("v10 stage selected row mismatch")

    candidate_rows = read_jsonl(V10_CANDIDATES)
    if len(candidate_rows) != 1:
        raise ValueError("v10 multi-candidate artifact must contain exactly one row")
    candidate_row = candidate_rows[0]
    candidates = candidate_row.get("candidates") or []
    if (
        candidate_row.get("status") != "certified_multi"
        or candidate_row.get("sourceSubmissionId")
        != "sub_ac9f8aef820b4ccd82600db05884ecd6"
        or int(candidate_row.get("sourcePolynomialIndex", -1)) != 0
        or candidate_row.get("sourceLabel") != "24T6284"
        or int(candidate_row.get("sourceR", -1)) != 24
        or candidate_row.get("sourceCoefficientSha256") != stage_source.get("coefficientSha256")
        or int(candidate_row.get("workerExitCode", -1)) != 0
        or len(candidates) != 3
        or sorted(int(value.get("factorIndex", -1)) for value in candidates) != [0, 1, 2]
    ):
        raise ValueError("v10 multi-candidate construction mismatch")
    chosen_candidates = [
        candidate
        for candidate in candidates
        if int(candidate.get("factorIndex", -1)) == 1
        and candidate.get("coefficientSha256") == SOURCE_5971_HASH
        and int(candidate.get("targetR", -1)) == SOURCE_5971[1]
    ]
    if len(chosen_candidates) != 1 or chosen_candidates[0].get("coefficientLine") != line:
        raise ValueError("v10 selected candidate does not match committed manifest")

    frobenius = read_json(V10_FROBENIUS)
    frobenius_rows = frobenius.get("rows") or []
    if (
        frobenius.get("method")
        != "exact-unramified-frobenius-cycle-type-exclusion-v1"
        or frobenius.get("inputSha256") != PINNED_SHA256["candidates5971"]
        or frobenius.get("selectedInputRowsSha256") != PINNED_SHA256["candidates5971"]
        or (frobenius.get("summary") or {})
        != {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0}
        or len(frobenius_rows) != 1
        or frobenius_rows[0].get("status") != "resolved"
    ):
        raise ValueError("v10 exact Frobenius certificate envelope mismatch")
    assignments = frobenius_rows[0].get("assignments") or []
    chosen_assignments = [
        assignment
        for assignment in assignments
        if int(assignment.get("factorIndex", -1)) == 1
        and assignment.get("targetLabel") == SOURCE_5971[0]
        and int(assignment.get("targetR", -1)) == SOURCE_5971[1]
        and assignment.get("coefficientSha256") == SOURCE_5971_HASH
    ]
    if len(assignments) != 3 or len(chosen_assignments) != 1:
        raise ValueError("v10 Frobenius assignment does not certify the source polynomial")
    return line, stage


def validate_15253_provenance() -> tuple[str, dict]:
    for path, key in (
        (V9_CENSUS, "v9CompletedCensus"),
        (SOURCE_15253_RECEIPT, "receipt15253"),
        (SOURCE_15253_MANIFEST, "manifest15253"),
        (V9_CANDIDATES, "candidates15253"),
        (V9_STAGE, "stage15253"),
    ):
        require_pinned(path, key)

    receipt = read_json(SOURCE_15253_RECEIPT)
    response = receipt.get("response") or {}
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != 1
        or response.get("submissionId") != SOURCE_15253_SUBMISSION
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
        or rooted_artifact(receipt.get("manifest")) != SOURCE_15253_MANIFEST.resolve()
        or receipt.get("manifestHash") != PINNED_SHA256["manifest15253"]
    ):
        raise ValueError("24T15253/r0 receipt envelope is not clean")
    lines = SOURCE_15253_MANIFEST.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError("24T15253/r0 manifest does not contain exactly one polynomial")
    line = lines[0]
    validated_coefficient_line(line, SOURCE_15253_HASH)

    census_rows = read_jsonl(V9_CENSUS)
    if any(row.get("status") != "certified" for row in census_rows) or any(
        not certificate_is_exact(row) for row in census_rows
    ):
        raise ValueError("pinned v9 census is not fully exact")
    structural_rows = [
        row
        for row in census_rows
        if row.get("sourceLabel") == "24T15218" and row.get("sourceR") == [8]
    ]
    if len(structural_rows) != 1:
        raise ValueError("v9 census has no unique 24T15218/r8 source row")
    structural = structural_rows[0]
    routes = structural.get("routes") or []
    if (
        int(structural.get("length24OrbitCount", -1)) != 1
        or structural.get("targetCounts") != {"24T15253": 1}
        or len(routes) != 1
        or routes[0].get("targetLabel") != SOURCE_15253[0]
        or int(routes[0].get("sourceR", -1)) != 8
        or routes[0].get("mappedTargetR") != [0, 8, 16]
        or routes[0].get("allCompatibleClassesGold") is not True
    ):
        raise ValueError("v9 structural route to 24T15253/r0 is not exact and safe")

    candidate_rows = read_jsonl(V9_CANDIDATES)
    if len(candidate_rows) != 1:
        raise ValueError("v9 24T15253 candidate artifact must contain one row")
    candidate = candidate_rows[0]
    orbit = candidate.get("orbitCertificate") or {}
    orbit_targets = candidate.get("orbitTargets") or []
    if (
        candidate.get("status") != "certified"
        or candidate.get("sourceSubmissionId")
        != "sub_f4dafb7696d249d4bbe72fb987faf101"
        or int(candidate.get("sourcePolynomialIndex", -1)) != 0
        or candidate.get("sourceLabel") != "24T15218"
        or int(candidate.get("sourceR", -1)) != 8
        or candidate.get("sourceCoefficientSha256")
        != "d3c588ab306977753c49a0dd18e76a8cd66ab506d663ffcc3eef9ec4d96002e1"
        or candidate.get("targetLabel") != SOURCE_15253[0]
        or int(candidate.get("targetR", -1)) != SOURCE_15253[1]
        or candidate.get("coefficientSha256") != SOURCE_15253_HASH
        or candidate.get("coefficientLine") != line
        or int(candidate.get("workerExitCode", -1)) != 0
        or orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or orbit.get("actualDegrees") != [12, 24, 48, 192]
        or orbit.get("exponents") != [1, 1, 1, 1]
        or len(orbit_targets) != 1
        or orbit_targets[0].get("targetLabel") != SOURCE_15253[0]
    ):
        raise ValueError("v9 exact 24T15253 construction mismatch")

    stage = read_json(V9_STAGE)
    selected = stage.get("selectedPairs") or []
    if (
        rooted_artifact(stage.get("input")) != V9_CANDIDATES.resolve()
        or stage.get("inputSha256") != PINNED_SHA256["candidates15253"]
        or int(stage.get("inputRows", -1)) != 1
        or rooted_artifact(stage.get("manifest")) != SOURCE_15253_MANIFEST.resolve()
        or stage.get("manifestSha256") != PINNED_SHA256["manifest15253"]
        or stage.get("frobeniusCertificate") is not None
        or int(stage.get("selected", -1)) != 1
        or len(selected) != 1
        or selected[0].get("label") != SOURCE_15253[0]
        or int(selected[0].get("r", -1)) != SOURCE_15253[1]
        or selected[0].get("coefficientSha256") != SOURCE_15253_HASH
        or selected[0].get("sourceSubmissionId")
        != candidate.get("sourceSubmissionId")
        or int(selected[0].get("sourcePolynomialIndex", -1))
        != int(candidate.get("sourcePolynomialIndex", -2))
        or selected[0].get("sourceLabel") != candidate.get("sourceLabel")
        or int(selected[0].get("sourceR", -1)) != int(candidate.get("sourceR", -2))
        or stage.get("skipCounts") != {}
    ):
        raise ValueError("v9 24T15253 stage envelope mismatch")
    return line, stage


def prior_action(source_label: str) -> dict | None:
    matches = []
    for relative in PRIOR_MAPS:
        for row in read_jsonl(ROOT / relative):
            if row.get("sourceLabel") == source_label:
                matches.append(row)
    if not matches:
        return None
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


def main() -> int:
    v10_rows, v10_census_rows, _v10_plan = validate_v10_checkpoint()
    _line_5971, stage_5971 = validate_5971_provenance(v10_census_rows)
    _line_15253, stage_15253 = validate_15253_provenance()
    v10_pairs = source_pairs(v10_rows)
    v10_gold = gold_pairs(v10_rows)
    if SOURCE_PAIRS & v10_pairs or not SOURCE_PAIRS <= v10_gold:
        raise ValueError("v10 boundary does not expose both exact v11 source pairs")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    baseline = {
        (str(label), int(r)) for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    accepted = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    accepted_delta = accepted - v10_pairs
    if accepted_delta != SOURCE_PAIRS:
        raise ValueError(
            "accepted ledger boundary moved beyond the pinned v11 checkpoint: "
            f"{sorted(accepted_delta)}"
        )
    if SOURCE_PAIRS & baseline:
        raise ValueError("v11 delta intersects the immutable baseline")

    source_specs = {
        SOURCE_5971: (SOURCE_5971_SUBMISSION, SOURCE_5971_HASH),
        SOURCE_15253: (SOURCE_15253_SUBMISSION, SOURCE_15253_HASH),
    }
    verifications = []
    for pair in sorted(SOURCE_PAIRS, key=lambda value: (int(value[0][3:]), value[1])):
        submission_id, coefficient_hash = source_specs[pair]
        verification = connection.execute(
            "SELECT status,label,r,scoreable,in_baseline,scoring_status "
            "FROM verifications WHERE submission_id=? AND polynomial_index=0",
            (submission_id,),
        ).fetchone()
        if verification != ("accepted", pair[0], pair[1], 1, 0, "scoreable"):
            raise ValueError(f"v11 source is not the exact accepted verification: {pair}")
        polynomial = connection.execute(
            "SELECT coefficient_hash FROM polynomials "
            "WHERE submission_id=? AND polynomial_index=0",
            (submission_id,),
        ).fetchone()
        if polynomial != (coefficient_hash,):
            raise ValueError(f"v11 ledger source coefficient hash mismatch: {pair}")
        verifications.append(
            {
                "submissionId": submission_id,
                "polynomialIndex": 0,
                "pair": f"{pair[0]}:{pair[1]}",
                "coefficientSha256": coefficient_hash,
                "status": "accepted",
                "scoreable": True,
                "inBaseline": False,
            }
        )

    target_records = [
        (str(label), int(r), int(team_count), bool(discovered), str(generated_at))
        for label, r, team_count, discovered, generated_at in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    ]
    connection.close()

    target_labels = {label for label, *_rest in target_records}
    if len(target_records) != 165_836 or len(target_labels) != 25_000:
        raise ValueError(
            "authoritative target refresh cardinality mismatch: "
            f"{len(target_labels)} labels/{len(target_records)} pairs"
        )
    owned_pairs = v10_pairs | accepted
    owned_by_label: dict[str, set[int]] = {}
    for label, r in owned_pairs:
        owned_by_label.setdefault(label, set()).add(r)
    gold_by_label: dict[str, set[int]] = {}
    target_snapshot = {}
    generated_at_values = []
    for label, r, team_count, discovered, generated_at in target_records:
        generated_at_values.append(generated_at)
        pair = (label, r)
        if pair in SOURCE_PAIRS:
            target_snapshot[pair] = {
                "pair": f"{label}:{r}",
                "teamCount": team_count,
                "discovered": discovered,
                "generatedAt": generated_at,
            }
        if team_count == 0 and pair not in baseline and pair not in owned_pairs:
            gold_by_label.setdefault(label, set()).add(r)
    if set(target_snapshot) != SOURCE_PAIRS:
        raise ValueError("one or more v11 sources are absent from the target snapshot")

    labels = sorted(
        set(owned_by_label) | set(gold_by_label), key=lambda label: int(label[3:])
    )
    v11_rows = [
        {
            "goldR": sorted(gold_by_label.get(label, set())),
            "isGoldTarget": label in gold_by_label,
            "isOwnedSource": label in owned_by_label,
            "label": label,
            "sourceR": sorted(owned_by_label.get(label, set())),
            "t": int(label[3:]),
        }
        for label in labels
    ]
    v11_pairs = source_pairs(v11_rows)
    if v11_pairs - v10_pairs != SOURCE_PAIRS or v10_pairs - v11_pairs:
        raise ValueError("v11 group input does not advance v10 by exactly two pairs")
    atomic_text(GROUP_INPUT, canonical_jsonl(v11_rows))

    group_summary = {
        "schemaVersion": "v11-frozen-group-input-summary-v1",
        "status": "certified",
        "base": relative_artifact(V10 / "group_input.jsonl"),
        "groupInput": relative_artifact(GROUP_INPUT),
        "rows": len(v11_rows),
        "baseOwnedPairs": len(v10_pairs),
        "frozenOwnedPairs": len(v11_pairs),
        "baseGoldPairs": len(v10_gold),
        "frozenGoldPairs": len(gold_pairs(v11_rows)),
        "targetLabels": len(target_labels),
        "targetRows": len(target_records),
        "targetGeneratedAtMin": min(generated_at_values),
        "targetGeneratedAtMax": max(generated_at_values),
        "exactDeltaPairs": [
            f"{label}:{r}"
            for label, r in sorted(SOURCE_PAIRS, key=lambda value: (int(value[0][3:]), value[1]))
        ],
        "derivation": "refreshed target cache plus accepted ledger, audited against v10",
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    atomic_json(GROUP_SUMMARY, group_summary)

    census_rows = []
    for row in v11_rows:
        selected = sorted(r for label, r in SOURCE_PAIRS if row.get("label") == label)
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if source_pairs(census_rows) != SOURCE_PAIRS:
        raise ValueError("v11 census input does not isolate exactly the two-pair delta")
    atomic_text(CENSUS_INPUT, canonical_jsonl(census_rows))

    v10_signatures: dict[str, set[int]] = {}
    for label, r in v10_pairs:
        v10_signatures.setdefault(label, set()).add(r)
    selected_preflight = {
        (label, r)
        for label, r in SOURCE_PAIRS
        if r not in v10_signatures.get(label, set())
    }
    if selected_preflight != SOURCE_PAIRS:
        raise ValueError("signature-aware worker preflight selected the wrong v11 delta")

    route_sources = []
    structural_total = 0
    live_total = 0
    for label, r in sorted(SOURCE_PAIRS, key=lambda value: (int(value[0][3:]), value[1])):
        action = prior_action(label)
        if action is None:
            structural = 276 // 24
            live = structural
            entry = {
                "sourcePair": f"{label}:{r}",
                "knownLength24Orbits": None,
                "structuralRouteCeiling": structural,
                "currentLiveRouteCeiling": live,
                "reason": (
                    "Label absent from every unordered-pair map through v10; "
                    "276 unordered pairs permit at most floor(276/24)=11 "
                    "degree-24 orbits."
                ),
            }
        else:
            targets = action.get("targets") or []
            if len(targets) != int(action.get("length24OrbitCount", -1)):
                raise ValueError(f"prior action target cardinality mismatch for {label}")
            external = [row for row in targets if row.get("targetLabel") != label]
            structural = len(external)
            live = sum(
                bool(gold_by_label.get(str(row.get("targetLabel")), set()))
                for row in external
            )
            entry = {
                "sourcePair": f"{label}:{r}",
                "knownLength24Orbits": int(action["length24OrbitCount"]),
                "knownExternalLength24Orbits": structural,
                "knownExternalTargetCounts": {
                    target_label: sum(
                        row.get("targetLabel") == target_label for row in external
                    )
                    for target_label in sorted(
                        {str(row.get("targetLabel")) for row in external}
                    )
                },
                "structuralRouteCeiling": structural,
                "currentLiveRouteCeiling": live,
                "reason": (
                    "Abstract action is already frozen; the new source signature "
                    "still requires exact class profiling."
                ),
            }
        structural_total += structural
        live_total += live
        route_sources.append(entry)
    if structural_total != 14:
        raise ValueError(f"unexpected v11 structural route ceiling: {structural_total}")

    inventory = {
        "schemaVersion": "v11-exact-source-inventory-v1",
        "status": "certified",
        "base": {
            "v10GroupInput": relative_artifact(V10 / "group_input.jsonl"),
            "v10CensusInput": relative_artifact(V10 / "census_input.jsonl"),
            "v10ExactSourceInventory": relative_artifact(
                V10 / "exact_source_inventory.json"
            ),
            "v10ProvenancePlan": relative_artifact(V10 / "provenance_plan.json"),
            "v10CompletedCensus": relative_artifact(V10_CENSUS),
        },
        "v11GroupInput": relative_artifact(GROUP_INPUT),
        "deltaPairs": [
            f"{label}:{r}"
            for label, r in sorted(SOURCE_PAIRS, key=lambda value: (int(value[0][3:]), value[1]))
        ],
        "deltaPairCount": 2,
        "verifiedPairCount": 2,
        "verifications": verifications,
        "receipts": {
            "source5971": relative_artifact(SOURCE_5971_RECEIPT),
            "source15253": relative_artifact(SOURCE_15253_RECEIPT),
        },
        "manifests": {
            "source5971": relative_artifact(SOURCE_5971_MANIFEST),
            "source15253": relative_artifact(SOURCE_15253_MANIFEST),
        },
        "constructionProvenance": {
            "source5971": {
                "completedCensus": relative_artifact(V10_CENSUS),
                "multiCandidates": relative_artifact(V10_CANDIDATES),
                "frobeniusCertificate": relative_artifact(V10_FROBENIUS),
                "stageCertificate": relative_artifact(V10_STAGE),
                "stageSchema": stage_5971.get("schema"),
                "sourceCoefficientSha256": SOURCE_5971_HASH,
                "selectedFactorIndex": 1,
            },
            "source15253": {
                "completedCensus": relative_artifact(V9_CENSUS),
                "candidate": relative_artifact(V9_CANDIDATES),
                "stageSummary": relative_artifact(V9_STAGE),
                "frobeniusCertificate": stage_15253.get("frobeniusCertificate"),
                "sourceCoefficientSha256": SOURCE_15253_HASH,
                "selectedFactorIndex": 0,
            },
        },
        "collisionCensus": {
            "baselinePairCollisions": 0,
            "duplicateDeltaPairs": 0,
            "priorFrozenSignatureCollisions": 0,
            "unaccountedAcceptedPairs": 0,
            "receiptFailedPolynomials": 0,
            "receiptRejectedPolynomials": 0,
        },
        "targetSnapshot": [
            target_snapshot[pair]
            for pair in sorted(SOURCE_PAIRS, key=lambda value: (int(value[0][3:]), value[1]))
        ],
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
            "data/autopilot_pair_delta_20260722_v10/group_input.jsonl",
            "--output",
            "data/autopilot_pair_delta_20260722_v11/missing_pair_all.jsonl",
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    plan = {
        "schemaVersion": "v11-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker",
        "base": {
            "v10GroupInput": relative_artifact(V10 / "group_input.jsonl"),
            "v10ProvenancePlan": relative_artifact(V10 / "provenance_plan.json"),
            "v10CompletedCensus": relative_artifact(V10_CENSUS),
        },
        "artifacts": {
            "groupInput": relative_artifact(GROUP_INPUT),
            "groupInputSummary": relative_artifact(GROUP_SUMMARY),
            "censusInput": relative_artifact(CENSUS_INPUT),
            "exactSourceInventory": relative_artifact(INVENTORY),
            "preparer": relative_artifact(Path(__file__).resolve()),
            "priorMaps": [relative_artifact(ROOT / path) for path in PRIOR_MAPS],
            "worker": relative_artifact(ROOT / "agent_index24_missing_pair_census.sage.py"),
        },
        "delta": {
            "selectedLabels": 2,
            "selectedSignatures": 2,
            "distinctPairs": 2,
            "pairs": [
                f"{label}:{r}"
                for label, r in sorted(SOURCE_PAIRS, key=lambda value: (int(value[0][3:]), value[1]))
            ],
            "acceptedLedgerDeltaPairs": 2,
            "signatureBaseline": "v10 frozen group input",
        },
        "routeExpectation": {
            "unorderedPairSetSizePerSource": 276,
            "sources": route_sources,
            "totalStructuralRouteCeiling": structural_total,
            "totalCurrentLiveRouteCeiling": live_total,
            "status": "rigorous_upper_bounds_not_yet_signature_censused",
        },
        "execution": {
            "heavyWorkerLaunched": False,
            "selectedWorkerRows": 2,
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
                "deltaPairs": 2,
                "selectedWorkerRows": 2,
                "structuralRouteCeiling": structural_total,
                "currentLiveRouteCeiling": live_total,
                "plannedCommand": command,
                "plan": str(PLAN.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

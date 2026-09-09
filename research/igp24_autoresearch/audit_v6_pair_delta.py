#!/usr/bin/env python3
"""Seal the exact v6 pair-delta inventory without network or ledger writes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V5 = ROOT / "data/autopilot_pair_delta_20260722_v5"
V6 = ROOT / "data/autopilot_pair_delta_20260722_v6"
OUT = V6 / "exact_source_inventory.json"

EXPECTED_DELTA = {
    ("24T10482", 4),
    ("24T13074", 0),
    ("24T15584", 24),
    ("24T15722", 24),
    ("24T17704", 16),
    ("24T17716", 8),
}
VERIFIED_DELTA = {
    ("24T10482", 4): ("sub_eab938f96b1243c9ae09a108022c84f9", 0),
    ("24T13074", 0): ("sub_eab938f96b1243c9ae09a108022c84f9", 1),
}
STAGED_DELTA = {
    ("24T17716", 8): (
        "sub_fcfc7b8d6da8402db24623a430e56451",
        ROOT / "data/autopilot_pair_delta_20260722_v3/ambiguous_16970_routes/source_16970_r8_stage_certificate.json",
    ),
    ("24T17704", 16): (
        "sub_ba10042fadaa42d0900138cd169e7253",
        ROOT / "data/autopilot_pair_delta_20260722_v3/ambiguous_16972_to_17704/source_16972_r16_stage_certificate.json",
    ),
}
CLOSURE_DELTA = {("24T15584", 24), ("24T15722", 24)}


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
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def source_pairs(path: Path) -> set[tuple[str, int]]:
    return {
        (str(row["label"]), int(r))
        for row in read_jsonl(path)
        if row.get("isOwnedSource")
        for r in row.get("sourceR", [])
    }


def receipt_envelope(submission_id: str) -> tuple[dict, Path, Path, list[str]]:
    receipt_path = ROOT / "receipts" / f"{submission_id}.json"
    receipt = read_json(receipt_path)
    response = receipt.get("response")
    if (
        receipt.get("commit") is not True
        or not isinstance(response, dict)
        or str(response.get("submissionId")) != submission_id
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
    ):
        raise ValueError(f"{submission_id} is not a clean committed receipt")
    manifest = Path(str(receipt.get("manifest"))).resolve()
    if not manifest.is_file() or str(receipt.get("manifestHash")) != sha256_path(manifest):
        raise ValueError(f"{submission_id} manifest hash mismatch")
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if int(receipt.get("polynomials", -1)) != len(lines):
        raise ValueError(f"{submission_id} manifest cardinality mismatch")
    return receipt, receipt_path, manifest, lines


def main() -> int:
    v5_input = V5 / "group_input.jsonl"
    v5_census = V5 / "missing_pair_all.jsonl"
    v6_input = V6 / "group_input.jsonl"
    v5_pairs = source_pairs(v5_input)
    v6_pairs = source_pairs(v6_input)
    delta = v6_pairs - v5_pairs
    if delta != EXPECTED_DELTA or v5_pairs - v6_pairs:
        raise ValueError(f"unexpected v6 source delta: {sorted(delta)}")

    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    if EXPECTED_DELTA & baseline:
        raise ValueError("v6 delta intersects the immutable baseline")

    current_accepted = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    accepted_delta = current_accepted - v5_pairs
    if not set(VERIFIED_DELTA) <= accepted_delta or accepted_delta - EXPECTED_DELTA:
        raise ValueError(f"unexpected accepted delta: {sorted(accepted_delta)}")

    target_snapshot = []
    for label, r in sorted(EXPECTED_DELTA, key=lambda value: (int(value[0][3:]), value[1])):
        row = connection.execute(
            "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
            (label, r),
        ).fetchone()
        if row is None:
            raise ValueError(f"v6 pair absent from target snapshot: {label}/{r}")
        target_snapshot.append(
            {
                "label": label,
                "r": r,
                "teamCount": int(row[0]),
                "discovered": bool(row[1]),
                "generatedAt": str(row[2]),
            }
        )

    verified = []
    verified_receipt_ids = {submission_id for submission_id, _ in VERIFIED_DELTA.values()}
    for submission_id in sorted(verified_receipt_ids):
        receipt, receipt_path, manifest, lines = receipt_envelope(submission_id)
        verified.append(
            {
                "submissionId": submission_id,
                "receipt": str(receipt_path.relative_to(ROOT)),
                "receiptSha256": sha256_path(receipt_path),
                "manifest": str(manifest.relative_to(ROOT)),
                "manifestSha256": sha256_path(manifest),
                "polynomials": len(lines),
            }
        )
    for pair, (submission_id, polynomial_index) in VERIFIED_DELTA.items():
        row = connection.execute(
            "SELECT status,label,r,scoreable,in_baseline,raw_json FROM verifications "
            "WHERE submission_id=? AND polynomial_index=?",
            (submission_id, polynomial_index),
        ).fetchone()
        if (
            row is None
            or str(row[0]) != "accepted"
            or (str(row[1]), int(row[2])) != pair
            or int(row[3]) != 1
            or int(row[4]) != 0
        ):
            raise ValueError(f"verified provenance mismatch for {pair}")

    staged = []
    for pair, (submission_id, stage_path) in sorted(STAGED_DELTA.items()):
        stage = read_json(stage_path)
        if (
            stage.get("status") != "staged_exact"
            or stage.get("certificateVersion") != "staged-queued-source-pair-frobenius-v1"
            or int(stage.get("networkCalls", -1)) != 0
            or int(stage.get("submissionCalls", -1)) != 0
            or int(stage.get("ledgerWrites", -1)) != 0
        ):
            raise ValueError(f"invalid exact stage certificate for {pair}")
        candidates = stage.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError(f"unexpected stage cardinality for {pair}")
        staged_row = candidates[0]
        target = staged_row.get("target")
        candidate = staged_row.get("candidate")
        novelty = staged_row.get("noveltyAudit")
        if (
            not isinstance(target, dict)
            or (str(target.get("label")), int(target.get("r", -1))) != pair
            or not isinstance(candidate, dict)
            or not isinstance(novelty, dict)
            or any(int(value) != 0 for value in novelty.values())
        ):
            raise ValueError(f"stage target/novelty mismatch for {pair}")
        receipt, receipt_path, manifest, lines = receipt_envelope(submission_id)
        if len(lines) != 1 or sha256_bytes(lines[0].encode()) != candidate.get("coefficientSha256"):
            raise ValueError(f"stage manifest line mismatch for {pair}")
        stage_manifest = stage.get("manifest")
        if (
            not isinstance(stage_manifest, dict)
            or Path(str(stage_manifest.get("path"))).resolve() != manifest
            or str(stage_manifest.get("sha256")) != sha256_path(manifest)
        ):
            raise ValueError(f"stage manifest declaration mismatch for {pair}")
        staged.append(
            {
                "pair": f"{pair[0]}:{pair[1]}",
                "submissionId": submission_id,
                "receipt": str(receipt_path.relative_to(ROOT)),
                "receiptSha256": sha256_path(receipt_path),
                "manifest": str(manifest.relative_to(ROOT)),
                "manifestSha256": sha256_path(manifest),
                "stageCertificate": str(stage_path.relative_to(ROOT)),
                "stageCertificateSha256": sha256_path(stage_path),
            }
        )

    staged_missing = set()
    for path in (ROOT / "data").rglob("*.json"):
        try:
            value = read_json(path)
        except (ValueError, json.JSONDecodeError, OSError):
            continue
        if value.get("status") != "staged_exact":
            continue
        for candidate in value.get("candidates") or []:
            target = candidate.get("target") if isinstance(candidate, dict) else None
            if not isinstance(target, dict):
                continue
            pair = (str(target.get("label")), int(target.get("r", -1)))
            if pair not in v5_pairs:
                staged_missing.add(pair)
    if staged_missing != set(STAGED_DELTA):
        raise ValueError(f"unaccounted staged exact delta: {sorted(staged_missing)}")

    closure_submission = "sub_a7edeafdf95a448faee17013d130480e"
    closure_receipt, closure_receipt_path, closure_manifest, closure_lines = receipt_envelope(
        closure_submission
    )
    source_row = connection.execute(
        "SELECT status,label,r,scoreable,in_baseline FROM verifications "
        "WHERE submission_id=? AND polynomial_index=0",
        ("sub_05157be1c91247bfad6feb1e5fd1cad1",),
    ).fetchone()
    if source_row != ("accepted", "24T14913", 24, 1, 0):
        raise ValueError("14913 closure source is not exactly verified")
    candidates_path = ROOT / "data/autopilot_14913_closure_candidates.jsonl"
    frobenius_path = ROOT / "data/autopilot_14913_closure_frobenius.json"
    candidates_rows = read_jsonl(candidates_path)
    frobenius = read_json(frobenius_path)
    if (
        len(candidates_rows) != 1
        or str(frobenius.get("inputSha256")) != sha256_path(candidates_path)
        or frobenius.get("summary") != {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0}
    ):
        raise ValueError("14913 closure candidate/Frobenius envelope mismatch")
    frobenius_rows = frobenius.get("rows")
    if not isinstance(frobenius_rows, list) or len(frobenius_rows) != 1:
        raise ValueError("14913 closure Frobenius row cardinality mismatch")
    closure_row = frobenius_rows[0]
    assignments = {
        (str(value["targetLabel"]), int(value["targetR"])): str(value["coefficientSha256"])
        for value in closure_row.get("assignments", [])
    }
    if (
        closure_row.get("status") != "resolved"
        or int(closure_row.get("remainingSlotAssignmentCount", -1)) != 1
        or set(assignments) != {("24T15093", 24), *CLOSURE_DELTA}
    ):
        raise ValueError("14913 closure assignment proof is not unique")
    closure_hashes = {sha256_bytes(line.encode()) for line in closure_lines}
    expected_closure_hashes = {assignments[pair] for pair in CLOSURE_DELTA}
    if closure_hashes != expected_closure_hashes or len(closure_lines) != 2:
        raise ValueError("14913 closure manifest selection mismatch")

    connection.close()

    v5_summary = read_json(V5 / "run_summary.json")
    freeze = str(v5_summary.get("createdAt"))
    expected_postfreeze = {
        closure_submission,
        *(submission_id for submission_id, _ in STAGED_DELTA.values()),
    }
    inventory_cutoff = max(
        str(
            (
                read_json(ROOT / "receipts" / f"{submission_id}.json").get("response")
                or {}
            ).get("createdAt")
            or ""
        )
        for submission_id in expected_postfreeze
    )
    postfreeze_receipts = []
    for path in (ROOT / "receipts").glob("sub_*.json"):
        receipt = read_json(path)
        created = str((receipt.get("response") or {}).get("createdAt") or "")
        if freeze < created <= inventory_cutoff:
            postfreeze_receipts.append(str((receipt.get("response") or {}).get("submissionId")))
    if set(postfreeze_receipts) != expected_postfreeze:
        raise ValueError(f"unaccounted post-v5 receipts: {sorted(postfreeze_receipts)}")

    result = {
        "schemaVersion": "v6-exact-source-inventory-v1",
        "status": "certified",
        "base": {
            "groupInput": str(v5_input.relative_to(ROOT)),
            "groupInputSha256": sha256_path(v5_input),
            "census": str(v5_census.relative_to(ROOT)),
            "censusSha256": sha256_path(v5_census),
        },
        "v6GroupInput": {
            "path": str(v6_input.relative_to(ROOT)),
            "sha256": sha256_path(v6_input),
        },
        "deltaPairs": [f"{label}:{r}" for label, r in sorted(EXPECTED_DELTA)],
        "deltaPairCount": len(EXPECTED_DELTA),
        "verifiedPairCount": len(VERIFIED_DELTA),
        "verifiedReceipts": verified,
        "stagedPairCount": len(STAGED_DELTA),
        "stagedEntries": staged,
        "closure": {
            "sourcePair": "24T14913:24",
            "sourceSubmissionId": "sub_05157be1c91247bfad6feb1e5fd1cad1",
            "pairs": [f"{label}:{r}" for label, r in sorted(CLOSURE_DELTA)],
            "submissionId": closure_submission,
            "receipt": str(closure_receipt_path.relative_to(ROOT)),
            "receiptSha256": sha256_path(closure_receipt_path),
            "manifest": str(closure_manifest.relative_to(ROOT)),
            "manifestSha256": sha256_path(closure_manifest),
            "candidates": str(candidates_path.relative_to(ROOT)),
            "candidatesSha256": sha256_path(candidates_path),
            "frobenius": str(frobenius_path.relative_to(ROOT)),
            "frobeniusSha256": sha256_path(frobenius_path),
        },
        "collisionCensus": {
            "baselinePairCollisions": 0,
            "duplicateDeltaPairs": 0,
            "priorOwnedPairCollisions": 0,
            "unaccountedAcceptedPairs": 0,
            "unaccountedStagedPairs": 0,
            "unaccountedPostFreezeReceipts": 0,
        },
        "targetSnapshot": target_snapshot,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    atomic_json(OUT, result)
    print(json.dumps({"status": "certified", "deltaPairs": len(EXPECTED_DELTA), "output": str(OUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

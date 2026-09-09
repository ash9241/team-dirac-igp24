#!/usr/bin/env python3
"""Freeze and audit the exact v10 unordered-pair signature delta.

This is a light, offline preparer.  It does not import Sage or GAP, touch the
network, write the ledger, or submit.  It verifies the completed v9 census,
the newly verified v8 pair gold, and the eleven-row sealed legacy supplement,
then emits one explicit command for the signature-aware v10 census.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V8 = ROOT / "data/autopilot_pair_delta_20260722_v8"
V9 = ROOT / "data/autopilot_pair_delta_20260722_v9"
V10 = ROOT / "data/autopilot_pair_delta_20260722_v10"
GROUP_INPUT = V10 / "group_input.jsonl"
CENSUS_INPUT = V10 / "census_input.jsonl"
INVENTORY = V10 / "exact_source_inventory.json"
PLAN = V10 / "provenance_plan.json"

V8_SUBMISSION = "sub_ac9f8aef820b4ccd82600db05884ecd6"
LEGACY_SUBMISSION = "sub_9a4fa0ffea6e4568bcb57f90f289adda"
V8_PAIR = ("24T6284", 24)
LEGACY_PAIRS = (
    ("24T13578", 8),
    ("24T3408", 4),
    ("24T6176", 8),
    ("24T8535", 0),
    ("24T8893", 0),
    ("24T14576", 0),
    ("24T4031", 8),
    ("24T6192", 8),
    ("24T11835", 8),
    ("24T13786", 16),
    ("24T15963", 16),
)
EXPECTED_DELTA = {V8_PAIR, *LEGACY_PAIRS}
EXPECTED_V9_CENSUS = {
    ("24T9833", 24),
    ("24T15218", 8),
    ("24T20075", 24),
}

V8_RESULTS = ROOT / "data/autopilot_pair_v8_6120_to_6284_candidates.jsonl"
V8_STAGE = ROOT / "data/autopilot_pair_v8_6120_to_6284_stage_summary.json"
LEGACY_CERTIFICATE = ROOT / "data/legacy_exact_shortlist_kle4_20260722_certificate.json"

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
]

POINTER_RE = re.compile(r"rows\[([0-9]+)\]\.assignments\[([0-9]+)\]\Z")
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


def source_pairs(rows: list[dict]) -> set[tuple[str, int]]:
    return {
        (str(row["label"]), int(signature))
        for row in rows
        if bool(row.get("isOwnedSource"))
        for signature in row.get("sourceR") or []
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


def certificate_is_exact(row: dict) -> bool:
    declared = row.get("exactCertificateSha256")
    unsigned = {key: value for key, value in row.items() if key != "exactCertificateSha256"}
    actual = sha256_bytes(
        json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()
    )
    return isinstance(declared, str) and declared == actual


def parse_pair(value: object) -> tuple[str, int]:
    match = PAIR_RE.fullmatch(str(value))
    if match is None:
        raise ValueError(f"invalid pair selector: {value!r}")
    return match.group(1), int(match.group(2))


def clean_receipt(submission_id: str, count: int) -> tuple[Path, Path, list[str]]:
    receipt_path = ROOT / "receipts" / f"{submission_id}.json"
    receipt = read_json(receipt_path)
    response = receipt.get("response")
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != count
        or not isinstance(response, dict)
        or response.get("submissionId") != submission_id
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
    ):
        raise ValueError(f"unclean receipt envelope: {submission_id}")
    manifest = rooted_artifact(receipt.get("manifest"))
    if receipt.get("manifestHash") != sha256_path(manifest):
        raise ValueError(f"receipt manifest hash mismatch: {submission_id}")
    lines = [line for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    if len(lines) != count:
        raise ValueError(f"receipt manifest cardinality mismatch: {submission_id}")
    return receipt_path, manifest, lines


def validate_v8_gold(expected_hash: str, manifest: Path) -> dict:
    census_path = V8 / "missing_pair_all.jsonl"
    census = read_jsonl(census_path)
    if (
        len(census) != 1
        or census[0].get("status") != "certified"
        or census[0].get("sourceLabel") != "24T6120"
        or census[0].get("sourceR") != [24]
        or census[0].get("length24OrbitCount") != 1
        or census[0].get("targetCounts") != {"24T6284": 1}
        or not certificate_is_exact(census[0])
    ):
        raise ValueError("v8 pair census certificate mismatch")
    routes = census[0].get("routes") or []
    if (
        len(routes) != 1
        or routes[0].get("targetLabel") != "24T6284"
        or int(routes[0].get("sourceR", -1)) != 24
        or int(routes[0].get("deterministicTargetR", -1)) != 24
        or routes[0].get("mappedTargetR") != [24]
        or routes[0].get("allCompatibleClassesGold") is not True
    ):
        raise ValueError("v8 pair route is not the exact safe route")

    stage = read_json(V8_STAGE)
    if (
        int(stage.get("inputRows", -1)) != 1
        or int(stage.get("selected", -1)) != 1
        or rooted_artifact(stage.get("manifest")) != manifest
        or stage.get("manifestSha256") != sha256_path(manifest)
        or rooted_artifact(stage.get("input")) != V8_RESULTS.resolve()
        or stage.get("inputSha256") != sha256_path(V8_RESULTS)
    ):
        raise ValueError("v8 stage envelope mismatch")
    results = read_jsonl(V8_RESULTS)
    if len(results) != 1:
        raise ValueError("v8 result cardinality mismatch")
    result = results[0]
    factor = result.get("orbitCertificate") or {}
    targets = result.get("orbitTargets") or []
    line = str(result.get("coefficientLine"))
    if (
        result.get("status") != "certified"
        or result.get("sourceSubmissionId") != "sub_25a347df242a4160a65e25f21b7851e7"
        or result.get("sourceLabel") != "24T6120"
        or int(result.get("sourceR", -1)) != 24
        or result.get("targetLabel") != V8_PAIR[0]
        or int(result.get("targetR", -1)) != V8_PAIR[1]
        or result.get("coefficientSha256") != expected_hash
        or sha256_bytes(line.encode()) != expected_hash
        or manifest.read_text(encoding="utf-8").splitlines() != [line]
        or sorted(map(int, factor.get("actualDegrees") or []))
        != sorted(map(int, factor.get("expectedDegrees") or []))
        or any(int(value) != 1 for value in factor.get("exponents") or [])
        or len(targets) != 1
        or targets[0].get("targetLabel") != V8_PAIR[0]
    ):
        raise ValueError("v8 exact construction provenance mismatch")

    v8_inventory = read_json(V8 / "exact_source_inventory.json")
    if (
        v8_inventory.get("status") != "certified"
        or v8_inventory.get("deltaPairs") != ["24T6120:24"]
        or int(v8_inventory.get("verifiedPairCount", -1)) != 1
    ):
        raise ValueError("v8 upstream source inventory mismatch")
    return {
        "completedCensus": relative_artifact(census_path),
        "sourceInventory": relative_artifact(V8 / "exact_source_inventory.json"),
        "stage": relative_artifact(V8_STAGE),
        "results": relative_artifact(V8_RESULTS),
    }


def validate_legacy_supplement(lines: list[str]) -> tuple[list[dict], dict]:
    certificate = read_json(LEGACY_CERTIFICATE)
    selected = certificate.get("selected")
    checks = certificate.get("checks")
    manifest_spec = certificate.get("manifest") or {}
    if (
        certificate.get("method") != "legacy-exact-frobenius-kle4-sealed-stage-v1"
        or int(certificate.get("networkCalls", -1)) != 0
        or int(certificate.get("submissionCalls", -1)) != 0
        or not isinstance(checks, dict)
        or not checks
        or not all(value is True for value in checks.values())
        or not isinstance(selected, list)
        or len(selected) != len(LEGACY_PAIRS)
        or int(manifest_spec.get("polynomials", -1)) != len(lines)
        or manifest_spec.get("sha256") != sha256_bytes(
            ("\n".join(lines) + "\n").encode()
        )
        or rooted_artifact(manifest_spec.get("path")).read_text(encoding="utf-8").splitlines()
        != lines
    ):
        raise ValueError("legacy supplement sealed envelope mismatch")

    stage_script = certificate.get("stageScript") or {}
    if sha256_path(rooted_artifact(stage_script.get("path"))) != stage_script.get("sha256"):
        raise ValueError("legacy stage-script hash mismatch")
    for artifact in certificate.get("legacyManifests") or []:
        if sha256_path(rooted_artifact(artifact.get("path"))) != artifact.get("sha256"):
            raise ValueError("legacy input-manifest hash mismatch")

    inventory = []
    for index, (public_row, line, expected_pair) in enumerate(
        zip(selected, lines, LEGACY_PAIRS, strict=True)
    ):
        digest = sha256_bytes(line.encode())
        target = public_row.get("target") or {}
        if (
            parse_pair(public_row.get("pair")) != expected_pair
            or (target.get("label"), int(target.get("r", -1))) != expected_pair
            or public_row.get("coefficientSha256") != digest
            or not all(value is True for value in (public_row.get("checks") or {}).values())
        ):
            raise ValueError(f"legacy public row mismatch at index {index}")

        proof = public_row.get("proof") or {}
        result_pointer = public_row.get("result") or {}
        proof_path = rooted_artifact(proof.get("artifact"))
        input_path = rooted_artifact(proof.get("input"))
        if (
            proof.get("artifactSha256") != sha256_path(proof_path)
            or proof.get("inputSha256") != sha256_path(input_path)
            or rooted_artifact(result_pointer.get("artifact")) != input_path
            or result_pointer.get("artifactSha256") != sha256_path(input_path)
            or proof.get("method")
            != "exact-unramified-frobenius-cycle-type-exclusion-v1"
            or proof.get("rowStatus") != "resolved"
            or int(proof.get("remainingLabelAssignments", -1)) != 1
        ):
            raise ValueError(f"legacy proof artifact mismatch at index {index}")

        exact = read_json(proof_path)
        if (
            exact.get("method") != proof.get("method")
            or rooted_artifact(exact.get("input")) != input_path
            or exact.get("inputSha256") != sha256_path(input_path)
            or exact.get("selectedInputRowsSha256") != sha256_path(input_path)
        ):
            raise ValueError(f"legacy exact-certificate envelope mismatch at index {index}")
        pointer = POINTER_RE.fullmatch(str(proof.get("pointer")))
        if pointer is None:
            raise ValueError(f"invalid exact-proof pointer at index {index}")
        row_index, assignment_index = map(int, pointer.groups())
        exact_row = (exact.get("rows") or [])[row_index]
        assignment = (exact_row.get("assignments") or [])[assignment_index]
        remaining = exact_row.get("remainingLabelAssignments") or []
        if (
            exact_row.get("status") != "resolved"
            or len(remaining) != 1
            or assignment.get("coefficientSha256") != digest
            or assignment.get("targetLabel") != expected_pair[0]
            or int(assignment.get("targetR", -1)) != expected_pair[1]
        ):
            raise ValueError(f"legacy exact assignment mismatch at index {index}")

        input_rows = read_jsonl(input_path)
        line_number = int(result_pointer.get("lineNumber", -1))
        candidate_index = int(result_pointer.get("candidateIndex", -1))
        source_row = input_rows[line_number - 1]
        candidate = (source_row.get("candidates") or [])[candidate_index]
        if (
            source_row.get("status") != "certified_multi"
            or source_row.get("sourceSubmissionId")
            != result_pointer.get("sourceSubmissionId")
            or source_row.get("sourceLabel") != result_pointer.get("sourceLabel")
            or int(source_row.get("sourceR", -1)) != int(result_pointer.get("sourceR", -2))
            or int(source_row.get("sourcePolynomialIndex", -1))
            != int(result_pointer.get("sourcePolynomialIndex", -2))
            or candidate.get("coefficientSha256") != digest
            or str(candidate.get("coefficientLine")) != line
            or int(candidate.get("targetR", -1)) != expected_pair[1]
            or int(candidate.get("factorIndex", -1))
            != int(assignment.get("factorIndex", -2))
            or int(candidate.get("factorIndex", -1))
            != int(result_pointer.get("factorIndex", -2))
        ):
            raise ValueError(f"legacy candidate payload mismatch at index {index}")
        inventory.append(
            {
                "pair": f"{expected_pair[0]}:{expected_pair[1]}",
                "polynomialIndex": index,
                "coefficientSha256": digest,
                "proof": relative_artifact(proof_path),
                "candidateInput": relative_artifact(input_path),
                "proofPointer": proof.get("pointer"),
            }
        )

    return inventory, {
        "certificate": relative_artifact(LEGACY_CERTIFICATE),
        "stageScript": relative_artifact(rooted_artifact(stage_script.get("path"))),
    }


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
    v9_input = V9 / "group_input.jsonl"
    v9_rows = read_jsonl(v9_input)
    v10_rows = read_jsonl(GROUP_INPUT)
    v9_pairs = source_pairs(v9_rows)
    v10_pairs = source_pairs(v10_rows)
    delta = v10_pairs - v9_pairs
    if delta != EXPECTED_DELTA or v9_pairs - v10_pairs:
        raise ValueError(f"unexpected v10 frozen-source delta: {sorted(delta)}")

    v9_census_path = V9 / "missing_pair_all.jsonl"
    v9_census = read_jsonl(v9_census_path)
    census_pairs = {
        (str(row.get("sourceLabel")), int(signature))
        for row in v9_census
        for signature in row.get("sourceR") or []
    }
    if (
        census_pairs != EXPECTED_V9_CENSUS
        or any(row.get("status") != "certified" for row in v9_census)
        or any(not certificate_is_exact(row) for row in v9_census)
    ):
        raise ValueError("completed v9 census is not the expected exact certificate set")

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
    accepted_delta = accepted - v9_pairs
    if accepted_delta != EXPECTED_DELTA:
        raise ValueError(f"unexpected accepted delta from v9: {sorted(accepted_delta)}")
    if EXPECTED_DELTA & baseline:
        raise ValueError("v10 delta intersects the immutable baseline")

    v8_receipt, v8_manifest, v8_lines = clean_receipt(V8_SUBMISSION, 1)
    legacy_receipt, legacy_manifest, legacy_lines = clean_receipt(
        LEGACY_SUBMISSION, len(LEGACY_PAIRS)
    )
    v8_hash = sha256_bytes(v8_lines[0].encode())
    v8_provenance = validate_v8_gold(v8_hash, v8_manifest)
    legacy_inventory, legacy_provenance = validate_legacy_supplement(legacy_lines)

    verification_inventory = []
    targets = []
    expected_rows = [(V8_SUBMISSION, 0, V8_PAIR, v8_hash)] + [
        (LEGACY_SUBMISSION, index, pair, sha256_bytes(legacy_lines[index].encode()))
        for index, pair in enumerate(LEGACY_PAIRS)
    ]
    for submission_id, polynomial_index, pair, digest in expected_rows:
        verification = connection.execute(
            "SELECT status,label,r,scoreable,in_baseline FROM verifications "
            "WHERE submission_id=? AND polynomial_index=?",
            (submission_id, polynomial_index),
        ).fetchone()
        if verification != ("accepted", pair[0], pair[1], 1, 0):
            raise ValueError(f"v10 verification mismatch for {pair}")
        polynomial = connection.execute(
            "SELECT coefficient_hash FROM polynomials "
            "WHERE submission_id=? AND polynomial_index=?",
            (submission_id, polynomial_index),
        ).fetchone()
        if polynomial != (digest,):
            raise ValueError(f"v10 ledger polynomial hash mismatch for {pair}")
        target = connection.execute(
            "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
            pair,
        ).fetchone()
        if target is None:
            raise ValueError(f"v10 source pair absent from target snapshot: {pair}")
        verification_inventory.append(
            {
                "pair": f"{pair[0]}:{pair[1]}",
                "submissionId": submission_id,
                "polynomialIndex": polynomial_index,
                "coefficientSha256": digest,
            }
        )
        targets.append(
            {
                "pair": f"{pair[0]}:{pair[1]}",
                "teamCount": int(target[0]),
                "discovered": bool(target[1]),
                "generatedAt": str(target[2]),
            }
        )
    connection.close()

    delta_by_label: dict[str, set[int]] = {}
    for label, r in delta:
        delta_by_label.setdefault(label, set()).add(r)
    census_rows = []
    for row in v10_rows:
        selected = sorted(delta_by_label.get(str(row["label"]), set()))
        census_rows.append({**row, "isOwnedSource": bool(selected), "sourceR": selected})
    if source_pairs(census_rows) != EXPECTED_DELTA:
        raise ValueError("v10 census input does not isolate exactly the signature delta")
    atomic_text(
        CENSUS_INPUT,
        "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in census_rows
        ),
    )

    mapped_labels = {
        str(row.get("sourceLabel"))
        for relative in PRIOR_MAPS
        for row in read_jsonl(ROOT / relative)
    }
    v9_signatures: dict[str, set[int]] = {}
    for row in v9_rows:
        if row.get("isOwnedSource"):
            v9_signatures.setdefault(str(row["label"]), set()).update(
                int(value) for value in row.get("sourceR") or []
            )
    selected_preflight = set()
    for row in census_rows:
        if not row.get("isOwnedSource"):
            continue
        label = str(row["label"])
        current = {int(value) for value in row.get("sourceR") or []}
        added = current - v9_signatures.get(label, set())
        if label in mapped_labels and not added:
            continue
        selected_preflight.update((label, value) for value in (added or current))
    if selected_preflight != EXPECTED_DELTA:
        raise ValueError("signature-aware worker preflight selected the wrong delta")

    gold_by_label = {
        str(row["label"]): {int(value) for value in row.get("goldR") or []}
        for row in v10_rows
    }
    route_sources = []
    structural_total = 0
    live_total = 0
    for label, r in sorted(delta, key=lambda pair: (int(pair[0][3:]), pair[1])):
        action = prior_action(label)
        if action is None:
            structural = 11
            live = 11
            entry = {
                "sourcePair": f"{label}:{r}",
                "knownLength24Orbits": None,
                "structuralRouteCeiling": structural,
                "currentLiveRouteCeiling": live,
                "reason": (
                    "Label absent from every unordered-pair map through v9; "
                    "276 unordered pairs permit at most floor(276/24)=11 "
                    "degree-24 orbits."
                ),
            }
        else:
            targets_for_action = action.get("targets") or []
            if len(targets_for_action) != int(action.get("length24OrbitCount", -1)):
                raise ValueError(f"prior action target cardinality mismatch for {label}")
            external = [
                row for row in targets_for_action if row.get("targetLabel") != label
            ]
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
                    target: sum(row.get("targetLabel") == target for row in external)
                    for target in sorted({str(row.get("targetLabel")) for row in external})
                },
                "structuralRouteCeiling": structural,
                "currentLiveRouteCeiling": live,
                "reason": (
                    "Abstract action is already frozen; the new source signature still "
                    "requires exact class profiling."
                ),
            }
        structural_total += structural
        live_total += live
        route_sources.append(entry)
    if (structural_total, live_total) != (84, 81):
        raise ValueError(
            f"unexpected v10 route ceilings: {(structural_total, live_total)}"
        )

    inventory = {
        "schemaVersion": "v10-exact-source-inventory-v1",
        "status": "certified",
        "base": {
            "v9GroupInput": relative_artifact(v9_input),
            "v9ProvenancePlan": relative_artifact(V9 / "provenance_plan.json"),
            "v9CompletedCensus": relative_artifact(v9_census_path),
        },
        "v10GroupInput": relative_artifact(GROUP_INPUT),
        "deltaPairs": [f"{label}:{r}" for label, r in sorted(delta)],
        "deltaPairCount": len(delta),
        "verifiedPairCount": len(verification_inventory),
        "verifications": verification_inventory,
        "receipts": {
            "v8PairGold": relative_artifact(v8_receipt),
            "legacySupplement": relative_artifact(legacy_receipt),
        },
        "manifests": {
            "v8PairGold": relative_artifact(v8_manifest),
            "legacySupplement": relative_artifact(legacy_manifest),
        },
        "provenance": {
            "v8PairGold": v8_provenance,
            "legacySupplement": {
                **legacy_provenance,
                "rows": legacy_inventory,
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
        "targetSnapshot": targets,
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
            str(v9_input.relative_to(ROOT)),
            "--output",
            "data/autopilot_pair_delta_20260722_v10/missing_pair_all.jsonl",
            "--shard-index",
            "0",
            "--shard-count",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    plan = {
        "schemaVersion": "v10-pair-delta-provenance-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_one_heavy_worker",
        "base": {
            "v9GroupInput": relative_artifact(v9_input),
            "v9ProvenancePlan": relative_artifact(V9 / "provenance_plan.json"),
            "v9CompletedCensus": relative_artifact(v9_census_path),
        },
        "artifacts": {
            "groupInput": relative_artifact(GROUP_INPUT),
            "groupInputSummary": relative_artifact(V10 / "group_input_summary.json"),
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
            "signatureBaseline": "v9 frozen group input",
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
                "structuralRouteCeiling": structural_total,
                "currentLiveRouteCeiling": live_total,
                "plan": str(PLAN.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

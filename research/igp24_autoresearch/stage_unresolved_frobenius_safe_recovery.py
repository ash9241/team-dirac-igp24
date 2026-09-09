#!/usr/bin/env python3
"""Seal anchored, constant, and all-compatible-safe unresolved Frobenius hits.

Three exact polynomial payloads are recovered from two immutable unresolved
Frobenius banks:

* a verified sibling anchor forces 24T6221/r0;
* 24T6452/r0 is constant in every remaining assignment, and the lowest saved
  polynomial discriminant is selected across duplicate packets;
* one factor remains either 24T5670/r0 or 24T5676/r0, but every compatible
  outcome is a current nonbaseline, locally unowned, unreceipted shared pair.

The stage is offline, opens the ledger read-only, audits all receipts, performs
no submission, and seals outputs without replacing nonidentical files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from fractions import Fraction
from pathlib import Path

import stage_all_exact_frobenius_unowned as all_exact
import stage_frobenius_gold as frobenius


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DATABASE = DATA / "ledger.sqlite3"

A_INPUT = DATA / "agent_gold_a_remaining_multi_candidates.jsonl"
A_PROOF = DATA / "agent_gold_a_remaining_multi_frobenius_certificate.json"
B_INPUT = DATA / "rank11_solo_pair_sum_candidates.jsonl"
B_PROOF = DATA / "agent_rank10_raid_rank11_frobenius_certificate.json"

MANIFEST = OUTBOX / "autopilot_unresolved_frobenius_safe_20260722.txt"
CERTIFICATE = DATA / "autopilot_unresolved_frobenius_safe_20260722_certificate.json"
SUMMARY = DATA / "autopilot_unresolved_frobenius_safe_20260722_summary.json"

EXPECTED_HASHES = {
    "aInput": "a39f0bde84a5e07d3a7aa7d4585fd0c244205486a8b9fe3730819733d2d7c9f8",
    "aProof": "7093ab1d0d06a21e030f7e37c966fa9e12aa6d7be756c7d43f9eb9044b8a085c",
    "bInput": "e673b1a1ed59ded39cec235eff46a2c4821de3f5a83c3951221269be21933baa",
    "bProof": "718f4e07181571fd128ff714f4de7a0b17aba63d4d7d6de26c8e29ef53e59197",
}
EXPECTED_CERTIFICATES = 65

A_SOURCE = ("sub_9ff8dd684fc54c65b2e93099fcad2098", 13, "24T6471", 0)
A_ANCHOR_FACTOR = 0
A_ANCHOR_HASH = "cb51bff97debbd7f0bc28b688c5811e884a5bbad5fabbc4b18c70369df96be6a"
A_ANCHOR_PAIR = ("24T6471", 8)
A_REMAINING = (
    ("24T6221", "24T5685", "24T6471"),
    ("24T6471", "24T5685", "24T6221"),
)
A_FORCED_FACTOR = 2
A_FORCED_HASH = "2be11b7fa5493d683f001909754715360c54d179ed736a58291685f1b664c30d"
A_FORCED_PAIR = ("24T6221", 0)

B_SOURCE = ("sub_2b2e6111820440a0a68367eb82c987e6", 694, "24T6452", 8)
B_REMAINING = (
    ("24T5670", "24T5676", "24T6452"),
    ("24T5676", "24T5670", "24T6452"),
)
B_CONSTANT_FACTOR = 2
B_CONSTANT_HASH = "705bb0461f33134b3610ec79530d0b5063ecacf6b3c49e32ea81f414842063da"
B_CONSTANT_PAIR = ("24T6452", 0)
B_SAFE_FACTOR = 1
B_SAFE_HASH = "2c3265a71c4eb4301fd8b88e7deed0e1cbc41c46529279e44187d4b6afab477f"
B_SAFE_PAIRS = (("24T5670", 0), ("24T5676", 0))

METHOD = "anchored-and-all-compatible-unresolved-frobenius-sealed-stage-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return all_exact.sha256_path(path)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def validated_candidate(candidate: dict, source: tuple) -> dict:
    line = frobenius.validated_coefficient_line(candidate, source)
    try:
        polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source} candidate has no polynomial discriminant") from exc
    if polynomial_disc <= 0:
        raise ValueError(f"{source} candidate discriminant is not positive")
    byte_count = len(line.encode("ascii"))
    if "coefficientBytes" in candidate and int(candidate["coefficientBytes"]) != byte_count:
        raise ValueError(f"{source} candidate byte count mismatch")
    return {
        "coefficientBytes": byte_count,
        "coefficientLine": line,
        "coefficientSha256": str(candidate["coefficientSha256"]),
        "factorIndex": int(candidate["factorIndex"]),
        "polynomialDiscriminantAbs": polynomial_disc,
        "targetR": int(candidate["targetR"]),
    }


def validate_bank(
    input_path: Path,
    proof_path: Path,
    expected_input_hash: str,
    expected_proof_hash: str,
) -> tuple[list[dict], dict, dict[tuple, dict], dict[tuple, tuple[int, dict]]]:
    if sha256_path(input_path) != expected_input_hash:
        raise ValueError(f"pinned input SHA-256 mismatch: {input_path}")
    if sha256_path(proof_path) != expected_proof_hash:
        raise ValueError(f"pinned Frobenius SHA-256 mismatch: {proof_path}")
    rows = frobenius.read_jsonl(input_path)
    proof = read_json(proof_path)
    frobenius.join_resolved_assignments(proof, rows, input_path, allow_unresolved=True)
    if Path(str(proof.get("input"))).expanduser().resolve() != input_path:
        raise ValueError(f"saved input path mismatch: {proof_path}")
    if str(proof.get("inputSha256")) != expected_input_hash:
        raise ValueError(f"saved input hash mismatch: {proof_path}")
    if str(proof.get("selectedInputRowsSha256")) != expected_input_hash:
        raise ValueError(f"saved selected-row hash mismatch: {proof_path}")
    packets = {}
    for row in rows:
        if row.get("status") != "certified_multi":
            continue
        key = frobenius.source_key(row)
        if key in packets:
            raise ValueError(f"duplicate candidate source key: {key}")
        if "workerExitCode" in row and int(row["workerExitCode"]) != 0:
            raise ValueError(f"candidate worker failed: {key}")
        packets[key] = row
    proofs = {}
    for index, row in enumerate(proof.get("rows") or []):
        key = frobenius.source_key(row)
        if key in proofs:
            raise ValueError(f"duplicate Frobenius source key: {key}")
        proofs[key] = (index, row)
    return rows, proof, packets, proofs


def unresolved_assignment(row: dict, expected: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
    if row.get("status") != "unresolved":
        raise ValueError("pinned proof row is not unresolved")
    remaining = tuple(
        tuple(str(label) for label in assignment)
        for assignment in row.get("remainingLabelAssignments") or []
    )
    if remaining != expected or int(row.get("remainingSlotAssignmentCount", -1)) != 2:
        raise ValueError("pinned remaining assignment set changed")
    return remaining


def candidate_map(packet: dict, source: tuple) -> dict[int, dict]:
    candidates = {
        int(row["factorIndex"]): validated_candidate(row, source)
        for row in packet.get("candidates") or []
    }
    if sorted(candidates) != list(range(len(candidates))) or len(candidates) != 3:
        raise ValueError(f"{source} does not have exactly factors 0,1,2")
    return candidates


def require_source(connection: sqlite3.Connection, source: tuple) -> dict:
    rows = connection.execute(
        "SELECT p.coefficient_hash,p.original_line,v.status,v.label,v.t,v.r,v.scoreable "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        source[:2],
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"source ledger row is missing or nonunique: {source}")
    digest, line, status, label, t_value, r_value, scoreable = rows[0]
    canonical = all_exact.canonical_line(str(line))
    if (
        canonical is None
        or sha256_bytes(canonical.encode("ascii")) != str(digest)
        or str(status) != "accepted"
        or str(label) != source[2]
        or int(t_value) != frobenius.label_t(source[2])
        or int(r_value) != source[3]
        or int(scoreable or 0) != 1
    ):
        raise ValueError(f"source ledger provenance mismatch: {source}")
    return {
        "coefficientSha256": str(digest),
        "label": source[2],
        "polynomialIndex": source[1],
        "r": source[3],
        "status": "accepted",
        "submissionId": source[0],
    }


def require_anchor(
    connection: sqlite3.Connection,
    candidate: dict,
    expected_hash: str,
    expected_pair: tuple[str, int],
) -> dict:
    rows = connection.execute(
        "SELECT p.submission_id,p.polynomial_index,p.original_line,v.status,v.label,v.t,v.r,v.scoreable "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE p.coefficient_hash=? ORDER BY p.submission_id,p.polynomial_index",
        (expected_hash,),
    ).fetchall()
    accepted = []
    for row in rows:
        canonical = all_exact.canonical_line(str(row[2]))
        if canonical != candidate["coefficientLine"]:
            raise ValueError("anchor ledger payload differs from saved factor")
        if row[4] is not None and (str(row[4]), int(row[6])) != expected_pair:
            raise ValueError("anchor hash has a conflicting verifier label")
        if (
            str(row[3]) == "accepted"
            and (str(row[4]), int(row[6])) == expected_pair
            and int(row[5]) == frobenius.label_t(expected_pair[0])
            and int(row[7] or 0) == 1
        ):
            accepted.append(row)
    if not accepted:
        raise ValueError("anchor hash has no accepted scoreable verifier row")
    row = accepted[0]
    return {
        "coefficientSha256": expected_hash,
        "factorIndex": candidate["factorIndex"],
        "label": expected_pair[0],
        "polynomialIndex": int(row[1]),
        "r": expected_pair[1],
        "status": "accepted",
        "submissionId": str(row[0]),
    }


def option_key(row: dict) -> tuple:
    return (
        row["polynomialDiscriminantAbs"],
        row["coefficientBytes"],
        row["coefficientSha256"],
    )


def target_evidence(
    connection: sqlite3.Connection,
    pair: tuple[str, int],
    receipt_pairs: set[tuple[str, int]],
    *,
    require_shared: bool,
) -> dict:
    if connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
    ).fetchone():
        raise ValueError(f"candidate pair is in the baseline: {pair}")
    if connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1", pair
    ).fetchone():
        raise ValueError(f"candidate pair is already locally owned: {pair}")
    if pair in receipt_pairs:
        raise ValueError(f"candidate pair is already receipted: {pair}")
    target = connection.execute(
        "SELECT t,team_count,minimum_disc_abs,discovered,generated_at "
        "FROM targets WHERE label=? AND r=?",
        pair,
    ).fetchone()
    if target is None:
        raise ValueError(f"candidate pair is absent from targets: {pair}")
    t_value, team_count, minimum_disc, discovered, generated_at = target
    if int(t_value) != frobenius.label_t(pair[0]) or int(team_count) < 0 or not generated_at:
        raise ValueError(f"invalid target metadata: {pair}")
    if require_shared and (int(team_count) <= 0 or not bool(discovered)):
        raise ValueError(f"all-compatible outcome is not a current shared pair: {pair}")
    return {
        "discovered": bool(discovered),
        "generatedAt": str(generated_at),
        "label": pair[0],
        "minimumDiscAbs": str(minimum_disc) if minimum_disc is not None else None,
        "r": pair[1],
        "t": int(t_value),
        "teamCount": int(team_count),
    }


def stage(
    *,
    root: Path = ROOT,
    data_dir: Path = DATA,
    receipts_dir: Path = RECEIPTS,
    database: Path = DATABASE,
    a_input: Path = A_INPUT,
    a_proof: Path = A_PROOF,
    b_input: Path = B_INPUT,
    b_proof: Path = B_PROOF,
    manifest: Path = MANIFEST,
    certificate_output: Path = CERTIFICATE,
    summary_output: Path = SUMMARY,
    expected_hashes: dict[str, str] = EXPECTED_HASHES,
    expected_certificates: int | None = EXPECTED_CERTIFICATES,
) -> dict:
    root = root.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    receipts_dir = receipts_dir.expanduser().resolve()
    database = database.expanduser().resolve()
    a_input = a_input.expanduser().resolve()
    a_proof = a_proof.expanduser().resolve()
    b_input = b_input.expanduser().resolve()
    b_proof = b_proof.expanduser().resolve()
    manifest = manifest.expanduser().resolve()
    certificate_output = certificate_output.expanduser().resolve()
    summary_output = summary_output.expanduser().resolve()

    _a_rows, a_certificate, a_packets, a_proofs = validate_bank(
        a_input, a_proof, expected_hashes["aInput"], expected_hashes["aProof"]
    )
    _b_rows, b_certificate, b_packets, b_proofs = validate_bank(
        b_input, b_proof, expected_hashes["bInput"], expected_hashes["bProof"]
    )
    if A_SOURCE not in a_packets or A_SOURCE not in a_proofs:
        raise ValueError("anchored 24T6471/r0 packet is absent")
    a_candidates = candidate_map(a_packets[A_SOURCE], A_SOURCE)
    a_proof_index, a_proof_row = a_proofs[A_SOURCE]
    a_remaining = unresolved_assignment(a_proof_row, A_REMAINING)
    if a_candidates[A_ANCHOR_FACTOR]["coefficientSha256"] != A_ANCHOR_HASH:
        raise ValueError("24T6471 anchor factor hash changed")
    surviving_a = [
        assignment
        for assignment in a_remaining
        if assignment[A_ANCHOR_FACTOR] == A_ANCHOR_PAIR[0]
    ]
    if len(surviving_a) != 1:
        raise ValueError("24T6471 anchor does not force one assignment")
    a_assignment = surviving_a[0]
    a_selected = a_candidates[A_FORCED_FACTOR]
    if (
        a_selected["coefficientSha256"] != A_FORCED_HASH
        or (a_assignment[A_FORCED_FACTOR], a_selected["targetR"]) != A_FORCED_PAIR
    ):
        raise ValueError("forced 24T6221/r0 candidate changed")

    unresolved_b = []
    for source, (proof_index, proof_row) in b_proofs.items():
        if source[2:] != B_SOURCE[2:] or proof_row.get("status") != "unresolved":
            continue
        if source not in b_packets:
            raise ValueError(f"unresolved B source is absent from candidate bank: {source}")
        remaining = unresolved_assignment(proof_row, B_REMAINING)
        candidates = candidate_map(b_packets[source], source)
        unresolved_b.append((source, proof_index, remaining, candidates))
    if len(unresolved_b) != 7:
        raise ValueError(f"expected seven unresolved 24T6452/r8 rows, found {len(unresolved_b)}")
    if B_SOURCE not in {row[0] for row in unresolved_b}:
        raise ValueError("canonical 24T6452/r8 packet is absent")

    constant_options = {}
    safe_options = {}
    for source, proof_index, remaining, candidates in unresolved_b:
        for factor_index, candidate in candidates.items():
            possible_pairs = tuple(
                sorted(
                    {(assignment[factor_index], candidate["targetR"]) for assignment in remaining},
                    key=lambda pair: (frobenius.label_t(pair[0]), pair[1]),
                )
            )
            option = {
                **candidate,
                "possiblePairs": possible_pairs,
                "proofRowIndex": proof_index,
                "source": source,
            }
            if possible_pairs == (B_CONSTANT_PAIR,):
                constant_options.setdefault(candidate["coefficientSha256"], option)
            if possible_pairs == tuple(sorted(B_SAFE_PAIRS, key=lambda pair: (frobenius.label_t(pair[0]), pair[1]))):
                safe_options.setdefault(candidate["coefficientSha256"], option)
    if not constant_options or not safe_options:
        raise ValueError("24T6452 unresolved option census is incomplete")
    best_constant = min(constant_options.values(), key=option_key)
    best_safe = min(safe_options.values(), key=option_key)
    if best_constant["coefficientSha256"] != B_CONSTANT_HASH:
        raise ValueError("best constant 24T6452/r0 payload changed")
    if best_safe["coefficientSha256"] != B_SAFE_HASH:
        raise ValueError("best all-compatible-safe r0 payload changed")
    if best_constant["factorIndex"] != B_CONSTANT_FACTOR or best_safe["factorIndex"] != B_SAFE_FACTOR:
        raise ValueError("best 24T6452 factor indexes changed")

    certificates, staged_pairs_by_manifest = all_exact.scan_json_artifacts(data_dir)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        source_evidence = [
            require_source(connection, source) for source in (A_SOURCE, B_SOURCE)
        ]
        anchor_evidence = require_anchor(
            connection,
            a_candidates[A_ANCHOR_FACTOR],
            A_ANCHOR_HASH,
            A_ANCHOR_PAIR,
        )
        exact_pool, _certificate_audit, exact_census = all_exact.collect_exact_pool(
            certificates, connection, root, expected_certificates
        )
        receipt_hashes, receipt_pairs, receipt_audit = all_exact.receipt_exclusions(
            connection, receipts_dir, staged_pairs_by_manifest, exact_pool, root
        )

        selected_specs = [
            {
                **a_selected,
                "kind": "verifier_anchor_forced_exact",
                "possiblePairs": (A_FORCED_PAIR,),
                "proofPath": a_proof,
                "proofRowIndex": a_proof_index,
                "source": A_SOURCE,
            },
            {
                **best_constant,
                "kind": "constant_across_all_remaining_assignments",
                "proofPath": b_proof,
            },
            {
                **best_safe,
                "kind": "all_compatible_assignments_current_shared_safe",
                "proofPath": b_proof,
            },
        ]
        selected = []
        for spec in selected_specs:
            digest = spec["coefficientSha256"]
            if connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
            ).fetchone():
                raise ValueError(f"selected candidate hash is already in the ledger: {digest}")
            if digest in receipt_hashes:
                raise ValueError(f"selected candidate hash is already receipted: {digest}")
            targets = [
                target_evidence(
                    connection,
                    pair,
                    receipt_pairs,
                    require_shared=(spec["kind"] == "all_compatible_assignments_current_shared_safe"),
                )
                for pair in spec["possiblePairs"]
            ]
            selected.append({**spec, "targets": targets})
        target_table_rows = int(connection.execute("SELECT COUNT(*) FROM targets").fetchone()[0])
        connection.rollback()

    selected.sort(
        key=lambda row: (
            min(target["teamCount"] for target in row["targets"]),
            min(target["t"] for target in row["targets"]),
            row["coefficientSha256"],
        )
    )
    score_values = [
        {Fraction(1, 2 ** target["teamCount"]) for target in row["targets"]}
        for row in selected
    ]
    score_min = sum((min(values) for values in score_values), Fraction(0, 1))
    score_max = sum((max(values) for values in score_values), Fraction(0, 1))
    manifest_payload = "".join(row["coefficientLine"] + "\n" for row in selected).encode("ascii")
    manifest_sha = sha256_bytes(manifest_payload)
    script_path = Path(__file__).resolve()
    script_sha = sha256_path(script_path)

    public_selected = []
    for row, values in zip(selected, score_values):
        public_selected.append(
            {
                "coefficientBytes": row["coefficientBytes"],
                "coefficientSha256": row["coefficientSha256"],
                "factorIndex": row["factorIndex"],
                "kind": row["kind"],
                "polynomialDiscriminantAbs": str(row["polynomialDiscriminantAbs"]),
                "possiblePairs": [f"{target['label']}/r{target['r']}" for target in row["targets"]],
                "projectedMarginalScoreExact": (
                    f"{next(iter(values)).numerator}/{next(iter(values)).denominator}"
                    if len(values) == 1
                    else None
                ),
                "targets": row["targets"],
            }
        )

    artifact_hashes = {
        "aInput": sha256_path(a_input),
        "aProof": sha256_path(a_proof),
        "bInput": sha256_path(b_input),
        "bProof": sha256_path(b_proof),
        "manifest": manifest_sha,
        "stageScript": script_sha,
    }
    exact_pairs = [A_FORCED_PAIR, B_CONSTANT_PAIR]
    certificate_value = {
        "artifactSha256": artifact_hashes,
        "checks": {
            "allCandidatePayloadHashesAndDiscriminantsMatched": True,
            "allCompatibleSafeOutcomesAreCurrentSharedNonbaselineUnownedUnreceipted": True,
            "allReceiptsAndIntactReceiptManifestsExcluded": True,
            "bestPayloadPerCompatiblePairSetSelectedByPolynomialDiscriminantThenBytesHash": True,
            "constantLabelProvedAcrossEveryRemainingAssignment": True,
            "duplicatePacketsAndPayloadsDeduplicated": True,
            "outputsContainNoCoefficientPayloadExceptManifest": True,
            "verifierAnchorLeavesExactlyOneSavedAssignment": True,
        },
        "database": all_exact.display_path(database, root),
        "dedupe": {
            "constantCompatiblePairSetDistinctPayloads": len(constant_options),
            "constantSelectedSha256": best_constant["coefficientSha256"],
            "safeCompatiblePairSetDistinctPayloads": len(safe_options),
            "safeSelectedSha256": best_safe["coefficientSha256"],
            "unresolved6452Rows": len(unresolved_b),
        },
        "exactCertificateCensus": exact_census,
        "frobeniusArtifacts": [
            {
                "input": all_exact.display_path(a_input, root),
                "inputSha256": artifact_hashes["aInput"],
                "method": a_certificate["method"],
                "path": all_exact.display_path(a_proof, root),
                "primeBoundExclusive": int(a_certificate["primeBoundExclusive"]),
                "sha256": artifact_hashes["aProof"],
            },
            {
                "input": all_exact.display_path(b_input, root),
                "inputSha256": artifact_hashes["bInput"],
                "method": b_certificate["method"],
                "path": all_exact.display_path(b_proof, root),
                "primeBoundExclusive": int(b_certificate["primeBoundExclusive"]),
                "sha256": artifact_hashes["bProof"],
            },
        ],
        "ledgerAnchor": anchor_evidence,
        "manifest": {
            "bytes": len(manifest_payload),
            "path": all_exact.display_path(manifest, root),
            "polynomials": len(selected),
            "sha256": manifest_sha,
        },
        "method": METHOD,
        "networkCalls": 0,
        "projectedMarginalScoreRangeExact": {
            "maximum": f"{score_max.numerator}/{score_max.denominator}",
            "minimum": f"{score_min.numerator}/{score_min.denominator}",
        },
        "receiptExclusion": receipt_audit,
        "selected": public_selected,
        "selectedPairs": [{"label": label, "r": r} for label, r in exact_pairs],
        "source": source_evidence,
        "stageScript": {"path": all_exact.display_path(script_path, root), "sha256": script_sha},
        "stagedPairs": [{"label": label, "r": r} for label, r in exact_pairs],
        "submissionCalls": 0,
        "targetSnapshot": {
            "generatedAtMax": max(target["generatedAt"] for row in selected for target in row["targets"]),
            "generatedAtMin": min(target["generatedAt"] for row in selected for target in row["targets"]),
            "targetTableRows": target_table_rows,
        },
    }
    certificate_payload = all_exact.json_bytes(certificate_value)
    certificate_sha = sha256_bytes(certificate_payload)
    summary_value = {
        "certificate": all_exact.display_path(certificate_output, root),
        "certificateSha256": certificate_sha,
        "manifest": all_exact.display_path(manifest, root),
        "manifestBytes": len(manifest_payload),
        "manifestSha256": manifest_sha,
        "method": METHOD,
        "networkCalls": 0,
        "polynomials": len(selected),
        "projectedMarginalScoreRangeExact": {
            "maximum": f"{score_max.numerator}/{score_max.denominator}",
            "minimum": f"{score_min.numerator}/{score_min.denominator}",
        },
        "selected": [
            {
                "coefficientSha256": row["coefficientSha256"],
                "kind": row["kind"],
                "possiblePairs": row["possiblePairs"],
                "projectedMarginalScoreExact": row["projectedMarginalScoreExact"],
                "teamCounts": [target["teamCount"] for target in row["targets"]],
            }
            for row in public_selected
        ],
        "status": "sealed_not_submitted",
        "submissionCalls": 0,
    }
    summary_payload = all_exact.json_bytes(summary_value)

    protected = {database, a_input, a_proof, b_input, b_proof, script_path}
    outputs = {manifest, certificate_output, summary_output}
    if outputs & protected or len(outputs) != 3:
        raise ValueError("sealed outputs overlap or overwrite a protected input")
    statuses = all_exact.preflight_and_seal(
        {manifest: manifest_payload, certificate_output: certificate_payload, summary_output: summary_payload}
    )
    return {
        "certificate": all_exact.display_path(certificate_output, root),
        "certificateSha256": certificate_sha,
        "manifest": all_exact.display_path(manifest, root),
        "manifestSha256": manifest_sha,
        "outputs": {all_exact.display_path(Path(path), root): status for path, status in statuses.items()},
        "polynomials": len(selected),
        "projectedMarginalScoreRangeExact": {
            "maximum": f"{score_max.numerator}/{score_max.denominator}",
            "minimum": f"{score_min.numerator}/{score_min.denominator}",
        },
        "status": "sealed_not_submitted",
        "summary": all_exact.display_path(summary_output, root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--a-input", type=Path, default=A_INPUT)
    parser.add_argument("--a-proof", type=Path, default=A_PROOF)
    parser.add_argument("--b-input", type=Path, default=B_INPUT)
    parser.add_argument("--b-proof", type=Path, default=B_PROOF)
    parser.add_argument("--output", type=Path, default=MANIFEST)
    parser.add_argument("--certificate-output", type=Path, default=CERTIFICATE)
    parser.add_argument("--summary-output", type=Path, default=SUMMARY)
    parser.add_argument("--expected-certificates", type=int, default=EXPECTED_CERTIFICATES)
    args = parser.parse_args()
    try:
        result = stage(
            root=args.root,
            data_dir=args.data,
            receipts_dir=args.receipts,
            database=args.database,
            a_input=args.a_input,
            a_proof=args.a_proof,
            b_input=args.b_input,
            b_proof=args.b_proof,
            manifest=args.output,
            certificate_output=args.certificate_output,
            summary_output=args.summary_output,
            expected_certificates=args.expected_certificates,
        )
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal the two exact 24T6087/r8 siblings forced by a verifier anchor.

The saved Frobenius certificate leaves two label assignments for one exact
three-factor pair-resolvent packet.  One factor has since been accepted by the
verifier as 24T6080/r8.  Intersecting that exact ledger fact with the two saved
assignments leaves one assignment and forces the other two factor labels.

This stage is deliberately offline.  It validates the immutable candidate and
Frobenius artifacts, the source and anchor ledger rows, every local receipt,
and the current target/baseline/ownership snapshot.  It never calls the API or
writes the ledger.  Outputs are sealed and may not replace nonidentical files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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

INPUT = DATA / "agent_index24_ambiguous_pair_multi_packets.jsonl"
FROBENIUS = DATA / "agent_index24_ambiguous_pair_multi_frobenius_certificate.json"
MANIFEST = OUTBOX / "autopilot_verifier_anchor_6087_20260722.txt"
CERTIFICATE = DATA / "autopilot_verifier_anchor_6087_20260722_certificate.json"
SUMMARY = DATA / "autopilot_verifier_anchor_6087_20260722_summary.json"

EXPECTED_INPUT_SHA256 = (
    "4437f7d54b3105724b8859b2c20e772b473a624bf018332db57dcefa57afc2f3"
)
EXPECTED_FROBENIUS_SHA256 = (
    "04aa60b7ea1c063b685959149e3b8c80b83ee0be14ac2deade252fadf9e4241b"
)
EXPECTED_CERTIFICATES = 65

SOURCE_KEY = (
    "sub_9ff8dd684fc54c65b2e93099fcad2098",
    8,
    "24T6087",
    8,
)
ANCHOR_FACTOR_INDEX = 0
ANCHOR_HASH = "e67ca6da21b39fad0769332bcc9896bf6165a4c5abded1f7704172313c95e718"
ANCHOR_PAIR = ("24T6080", 8)
EXPECTED_REMAINING_ASSIGNMENTS = (
    ("24T6080", "24T6364", "24T6407"),
    ("24T6364", "24T6080", "24T6407"),
)
EXPECTED_FORCED = {
    1: ("24T6364", 4),
    2: ("24T6407", 0),
}

METHOD = "verifier-anchored-unresolved-frobenius-sealed-stage-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return all_exact.sha256_path(path)


def display_path(path: Path, root: Path) -> str:
    return all_exact.display_path(path, root)


def json_bytes(value: dict) -> bytes:
    return all_exact.json_bytes(value)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return frobenius.read_jsonl(path)


def source_key(row: dict) -> tuple[str, int, str, int]:
    return frobenius.source_key(row)


def validate_candidate(candidate: dict, key: tuple) -> dict:
    line = frobenius.validated_coefficient_line(candidate, key)
    digest = str(candidate["coefficientSha256"])
    byte_count = len(line.encode("ascii"))
    if "coefficientBytes" in candidate and int(candidate["coefficientBytes"]) != byte_count:
        raise ValueError(f"{key} factor {candidate.get('factorIndex')} byte count mismatch")
    try:
        polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} candidate has no positive polynomial discriminant") from exc
    if polynomial_disc <= 0:
        raise ValueError(f"{key} candidate has no positive polynomial discriminant")
    return {
        "coefficientBytes": byte_count,
        "coefficientLine": line,
        "coefficientSha256": digest,
        "factorIndex": int(candidate["factorIndex"]),
        "polynomialDiscriminantAbs": polynomial_disc,
        "targetR": int(candidate["targetR"]),
    }


def validate_saved_packet(
    input_path: Path,
    certificate_path: Path,
    expected_input_sha256: str,
    expected_certificate_sha256: str,
    expected_anchor_hash: str,
) -> tuple[dict, dict, dict[int, dict], tuple[str, ...], int]:
    input_hash = sha256_path(input_path)
    certificate_hash = sha256_path(certificate_path)
    if input_hash != expected_input_sha256:
        raise ValueError(
            f"candidate input SHA-256 mismatch: {input_hash} != {expected_input_sha256}"
        )
    if certificate_hash != expected_certificate_sha256:
        raise ValueError(
            "Frobenius certificate SHA-256 mismatch: "
            f"{certificate_hash} != {expected_certificate_sha256}"
        )

    candidate_rows = read_jsonl(input_path)
    proof = read_json(certificate_path)
    # Reuse the sealed Frobenius joiner's header, row-selection, and input
    # validation.  The return value intentionally omits unresolved rows.
    frobenius.join_resolved_assignments(
        proof, candidate_rows, input_path, allow_unresolved=True
    )
    if str(proof.get("inputSha256")) != input_hash:
        raise ValueError("Frobenius certificate does not pin the candidate input")
    selected_digest = proof.get("selectedInputRowsSha256")
    if selected_digest is not None and str(selected_digest) != input_hash:
        raise ValueError("Frobenius certificate does not select the full pinned input")
    saved_input = Path(str(proof.get("input"))).expanduser().resolve()
    if saved_input != input_path:
        raise ValueError("Frobenius certificate saved-input path mismatch")

    packet_matches = [row for row in candidate_rows if source_key(row) == SOURCE_KEY]
    proof_rows = [row for row in proof.get("rows") or [] if source_key(row) == SOURCE_KEY]
    if len(packet_matches) != 1 or len(proof_rows) != 1:
        raise ValueError("pinned source is missing or nonunique in saved artifacts")
    packet = packet_matches[0]
    proof_row = proof_rows[0]
    proof_row_index = (proof.get("rows") or []).index(proof_row)
    if packet.get("status") != "certified_multi":
        raise ValueError("pinned source packet is not certified_multi")
    if "workerExitCode" in packet and int(packet["workerExitCode"]) != 0:
        raise ValueError("pinned source packet worker did not exit cleanly")
    if proof_row.get("status") != "unresolved":
        raise ValueError("pinned Frobenius row is no longer the expected unresolved row")

    candidates = {
        int(candidate["factorIndex"]): validate_candidate(candidate, SOURCE_KEY)
        for candidate in packet.get("candidates") or []
    }
    if sorted(candidates) != [0, 1, 2]:
        raise ValueError("pinned source packet does not contain factors 0,1,2 exactly")
    if candidates[ANCHOR_FACTOR_INDEX]["coefficientSha256"] != expected_anchor_hash:
        raise ValueError("pinned factor zero is not the expected verifier anchor")

    orbit_targets = list(packet.get("orbitTargets") or [])
    target_slots = list(proof_row.get("targetSlots") or [])
    if len(orbit_targets) != 3 or len(target_slots) != 3:
        raise ValueError("pinned packet does not have exactly three target slots")
    for position, (source_slot, proof_slot) in enumerate(zip(orbit_targets, target_slots)):
        if (
            int(proof_slot.get("slotPosition", -1)) != position
            or int(proof_slot.get("orbitIndex", -1)) != int(source_slot["orbitIndex"])
            or str(proof_slot.get("targetLabel")) != str(source_slot["targetLabel"])
            or int(proof_slot.get("targetT", -1)) != int(source_slot["targetT"])
            or int(source_slot.get("orbitSize", -1)) != 24
            or int(source_slot.get("kernelOrder", -1)) != 1
        ):
            raise ValueError(f"pinned target slot {position} is inconsistent")

    remaining = tuple(
        tuple(str(label) for label in assignment)
        for assignment in proof_row.get("remainingLabelAssignments") or []
    )
    if remaining != EXPECTED_REMAINING_ASSIGNMENTS:
        raise ValueError("pinned unresolved assignment set changed")
    if int(proof_row.get("remainingSlotAssignmentCount", -1)) != 2:
        raise ValueError("pinned unresolved slot-assignment count changed")
    for assignment in remaining:
        if len(assignment) != len(candidates):
            raise ValueError("saved label assignment has the wrong arity")

    surviving = tuple(
        assignment
        for assignment in remaining
        if assignment[ANCHOR_FACTOR_INDEX] == ANCHOR_PAIR[0]
    )
    if len(surviving) != 1:
        raise ValueError("verifier anchor does not leave exactly one label assignment")
    exact_assignment = surviving[0]
    for factor_index, expected_pair in EXPECTED_FORCED.items():
        actual_pair = (
            exact_assignment[factor_index],
            candidates[factor_index]["targetR"],
        )
        if actual_pair != expected_pair:
            raise ValueError(
                f"factor {factor_index} forced pair changed: {actual_pair} != {expected_pair}"
            )
    return packet, proof_row, candidates, exact_assignment, proof_row_index


def require_source_and_anchor(
    connection: sqlite3.Connection,
    candidates: dict[int, dict],
    expected_anchor_hash: str,
) -> tuple[dict, dict]:
    source_rows = connection.execute(
        "SELECT p.coefficient_hash,p.original_line,v.status,v.label,v.t,v.r,"
        "v.scoreable FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        SOURCE_KEY[:2],
    ).fetchall()
    if len(source_rows) != 1:
        raise ValueError("source ledger row is missing or nonunique")
    source_hash, source_line, status, label, t_value, r_value, scoreable = source_rows[0]
    if (
        str(status) != "accepted"
        or str(label) != SOURCE_KEY[2]
        or int(t_value) != frobenius.label_t(SOURCE_KEY[2])
        or int(r_value) != SOURCE_KEY[3]
        or int(scoreable or 0) != 1
    ):
        raise ValueError("source ledger row is not accepted, scoreable, and exact")
    canonical_source = all_exact.canonical_line(str(source_line))
    if canonical_source is None or sha256_bytes(canonical_source.encode("ascii")) != str(source_hash):
        raise ValueError("source ledger coefficient payload/hash mismatch")

    anchor_rows = connection.execute(
        "SELECT p.submission_id,p.polynomial_index,p.original_line,v.status,"
        "v.label,v.t,v.r,v.scoreable FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.coefficient_hash=? "
        "ORDER BY p.submission_id,p.polynomial_index",
        (expected_anchor_hash,),
    ).fetchall()
    if not anchor_rows:
        raise ValueError("accepted verifier anchor is absent from the ledger")
    accepted = []
    for row in anchor_rows:
        submission_id, polynomial_index, original_line, row_status, label, t_value, r_value, scoreable = row
        canonical = all_exact.canonical_line(str(original_line))
        if canonical != candidates[ANCHOR_FACTOR_INDEX]["coefficientLine"]:
            raise ValueError("ledger anchor payload differs from the saved factor payload")
        if sha256_bytes(canonical.encode("ascii")) != expected_anchor_hash:
            raise ValueError("ledger anchor payload/hash mismatch")
        if label is not None and (str(label), int(r_value)) != ANCHOR_PAIR:
            raise ValueError("ledger contains a conflicting verifier label for the anchor hash")
        if (
            str(row_status) == "accepted"
            and (str(label), int(r_value)) == ANCHOR_PAIR
            and int(t_value) == frobenius.label_t(ANCHOR_PAIR[0])
            and int(scoreable or 0) == 1
        ):
            accepted.append(row)
    if not accepted:
        raise ValueError("anchor hash lacks an accepted scoreable verifier row")
    anchor = accepted[0]
    return (
        {
            "coefficientSha256": str(source_hash),
            "label": SOURCE_KEY[2],
            "polynomialIndex": SOURCE_KEY[1],
            "r": SOURCE_KEY[3],
            "status": "accepted",
            "submissionId": SOURCE_KEY[0],
        },
        {
            "coefficientSha256": expected_anchor_hash,
            "factorIndex": ANCHOR_FACTOR_INDEX,
            "label": ANCHOR_PAIR[0],
            "polynomialIndex": int(anchor[1]),
            "r": ANCHOR_PAIR[1],
            "scoreable": True,
            "status": "accepted",
            "submissionId": str(anchor[0]),
        },
    )


def stage(
    *,
    root: Path = ROOT,
    data_dir: Path = DATA,
    receipts_dir: Path = RECEIPTS,
    database: Path = DATABASE,
    input_path: Path = INPUT,
    frobenius_path: Path = FROBENIUS,
    manifest: Path = MANIFEST,
    certificate_output: Path = CERTIFICATE,
    summary_output: Path = SUMMARY,
    expected_input_sha256: str = EXPECTED_INPUT_SHA256,
    expected_frobenius_sha256: str = EXPECTED_FROBENIUS_SHA256,
    expected_anchor_hash: str = ANCHOR_HASH,
    expected_certificates: int | None = EXPECTED_CERTIFICATES,
) -> dict:
    root = root.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    receipts_dir = receipts_dir.expanduser().resolve()
    database = database.expanduser().resolve()
    input_path = input_path.expanduser().resolve()
    frobenius_path = frobenius_path.expanduser().resolve()
    manifest = manifest.expanduser().resolve()
    certificate_output = certificate_output.expanduser().resolve()
    summary_output = summary_output.expanduser().resolve()

    packet, proof_row, candidates, assignment, proof_row_index = validate_saved_packet(
        input_path,
        frobenius_path,
        expected_input_sha256,
        expected_frobenius_sha256,
        expected_anchor_hash,
    )

    certificates, staged_pairs_by_manifest = all_exact.scan_json_artifacts(data_dir)
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        source_evidence, anchor_evidence = require_source_and_anchor(
            connection, candidates, expected_anchor_hash
        )
        exact_pool, _certificate_audit, exact_census = all_exact.collect_exact_pool(
            certificates, connection, root, expected_certificates
        )
        receipt_hashes, receipt_pairs, receipt_audit = all_exact.receipt_exclusions(
            connection,
            receipts_dir,
            staged_pairs_by_manifest,
            exact_pool,
            root,
        )

        selected = []
        for factor_index, expected_pair in EXPECTED_FORCED.items():
            candidate = candidates[factor_index]
            pair = (assignment[factor_index], candidate["targetR"])
            if pair != expected_pair:
                raise ValueError(f"factor {factor_index} no longer has the pinned pair")
            if connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
            ).fetchone():
                raise ValueError(f"forced pair is in the baseline: {pair}")
            if connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? "
                "AND scoreable=1 LIMIT 1",
                pair,
            ).fetchone():
                raise ValueError(f"forced pair is already locally owned: {pair}")
            if connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (candidate["coefficientSha256"],),
            ).fetchone():
                raise ValueError(f"forced candidate hash is already in the ledger: {pair}")
            if candidate["coefficientSha256"] in receipt_hashes:
                raise ValueError(f"forced candidate hash is already receipted: {pair}")
            if pair in receipt_pairs:
                raise ValueError(f"forced target pair is already receipted: {pair}")
            target = connection.execute(
                "SELECT t,team_count,minimum_disc_abs,discovered,generated_at "
                "FROM targets WHERE label=? AND r=?",
                pair,
            ).fetchone()
            if target is None:
                raise ValueError(f"forced pair is absent from the target snapshot: {pair}")
            target_t, team_count, minimum_disc, discovered, generated_at = target
            if int(target_t) != frobenius.label_t(pair[0]) or int(team_count) < 0:
                raise ValueError(f"forced target metadata is invalid: {pair}")
            if not generated_at:
                raise ValueError(f"forced target has no snapshot timestamp: {pair}")
            selected.append(
                {
                    **candidate,
                    "label": pair[0],
                    "r": pair[1],
                    "t": int(target_t),
                    "target": {
                        "discovered": bool(discovered),
                        "generatedAt": str(generated_at),
                        "minimumDiscAbs": str(minimum_disc) if minimum_disc is not None else None,
                        "teamCount": int(team_count),
                    },
                }
            )

        selected.sort(key=lambda row: (row["target"]["teamCount"], row["t"], row["r"]))
        target_table_rows = int(connection.execute("SELECT COUNT(*) FROM targets").fetchone()[0])
        connection.rollback()

    score = sum(
        (Fraction(1, 2 ** row["target"]["teamCount"]) for row in selected),
        Fraction(0, 1),
    )
    manifest_payload = "".join(row["coefficientLine"] + "\n" for row in selected).encode("ascii")
    manifest_sha = sha256_bytes(manifest_payload)
    input_sha = sha256_path(input_path)
    proof_sha = sha256_path(frobenius_path)
    script_path = Path(__file__).resolve()
    script_sha = sha256_path(script_path)

    public_selected = [
        {
            "coefficientBytes": row["coefficientBytes"],
            "coefficientSha256": row["coefficientSha256"],
            "factorIndex": row["factorIndex"],
            "pair": f"{row['label']}/r{row['r']}",
            "polynomialDiscriminantAbs": str(row["polynomialDiscriminantAbs"]),
            "projectedMarginalScoreExact": str(Fraction(1, 2 ** row["target"]["teamCount"])),
            "target": {**row["target"], "label": row["label"], "r": row["r"], "t": row["t"]},
        }
        for row in selected
    ]
    staged_pairs = [
        {"label": row["label"], "r": row["r"]} for row in selected
    ]
    certificate_value = {
        "artifactSha256": {
            "frobeniusCertificate": proof_sha,
            "input": input_sha,
            "manifest": manifest_sha,
            "stageScript": script_sha,
        },
        "checks": {
            "acceptedScoreableAnchorMatchedSavedFactorPayloadAndHash": True,
            "allCandidatePayloadHashesAndDiscriminantsMatched": True,
            "allReceiptsAndIntactReceiptManifestsExcluded": True,
            "baselineOwnershipKnownHashAndReceiptExclusionsPassed": True,
            "exactlyOneSavedLabelAssignmentSurvivedAnchor": True,
            "outputsContainNoCoefficientPayloadExceptManifest": True,
            "sourceLedgerRowAcceptedScoreableAndMatched": True,
        },
        "database": display_path(database, root),
        "exactCertificateCensus": exact_census,
        "forcedAssignment": {
            "anchorFactorIndex": ANCHOR_FACTOR_INDEX,
            "remainingAssignmentCountBeforeAnchor": len(EXPECTED_REMAINING_ASSIGNMENTS),
            "remainingAssignmentCountAfterAnchor": 1,
            "savedFrobeniusRowPointer": f"rows[{proof_row_index}]",
            "selectedLabelsByFactor": [
                {"factorIndex": index, "targetLabel": label}
                for index, label in enumerate(assignment)
            ],
        },
        "frobeniusCertificate": {
            "input": display_path(input_path, root),
            "inputSha256": input_sha,
            "method": str(read_json(frobenius_path)["method"]),
            "path": display_path(frobenius_path, root),
            "primeBoundExclusive": int(read_json(frobenius_path)["primeBoundExclusive"]),
            "sha256": proof_sha,
            "statusAtSavedRow": str(proof_row["status"]),
        },
        "ledgerAnchor": anchor_evidence,
        "manifest": {
            "bytes": len(manifest_payload),
            "path": display_path(manifest, root),
            "polynomials": len(selected),
            "sha256": manifest_sha,
        },
        "method": METHOD,
        "networkCalls": 0,
        "projectedMarginalScore": float(score),
        "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
        "receiptExclusion": receipt_audit,
        "selected": public_selected,
        "selectedPairs": staged_pairs,
        "source": source_evidence,
        "stageScript": {"path": display_path(script_path, root), "sha256": script_sha},
        "stagedPairs": staged_pairs,
        "submissionCalls": 0,
        "targetSnapshot": {
            "generatedAtMax": max(row["target"]["generatedAt"] for row in selected),
            "generatedAtMin": min(row["target"]["generatedAt"] for row in selected),
            "targetTableRows": target_table_rows,
        },
    }
    certificate_payload = json_bytes(certificate_value)
    certificate_sha = sha256_bytes(certificate_payload)
    summary_value = {
        "certificate": display_path(certificate_output, root),
        "certificateSha256": certificate_sha,
        "manifest": display_path(manifest, root),
        "manifestBytes": len(manifest_payload),
        "manifestSha256": manifest_sha,
        "method": METHOD,
        "networkCalls": 0,
        "polynomials": len(selected),
        "projectedMarginalScore": float(score),
        "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
        "selectedPairs": [
            {
                "coefficientSha256": row["coefficientSha256"],
                "pair": row["pair"],
                "projectedMarginalScoreExact": row["projectedMarginalScoreExact"],
                "teamCount": row["target"]["teamCount"],
            }
            for row in public_selected
        ],
        "status": "sealed_not_submitted",
        "submissionCalls": 0,
    }
    summary_payload = json_bytes(summary_value)

    protected = {database, input_path, frobenius_path, script_path}
    outputs = {manifest, certificate_output, summary_output}
    if outputs & protected or len(outputs) != 3:
        raise ValueError("sealed outputs must be distinct from every protected input")
    statuses = all_exact.preflight_and_seal(
        {
            manifest: manifest_payload,
            certificate_output: certificate_payload,
            summary_output: summary_payload,
        }
    )
    return {
        "certificate": display_path(certificate_output, root),
        "certificateSha256": certificate_sha,
        "manifest": display_path(manifest, root),
        "manifestSha256": manifest_sha,
        "outputs": {
            display_path(Path(path), root): status for path, status in statuses.items()
        },
        "polynomials": len(selected),
        "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
        "status": "sealed_not_submitted",
        "summary": display_path(summary_output, root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--frobenius", type=Path, default=FROBENIUS)
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
            input_path=args.input,
            frobenius_path=args.frobenius,
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

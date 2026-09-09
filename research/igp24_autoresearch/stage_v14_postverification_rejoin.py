#!/usr/bin/env python3
"""Seal the post-verification 24T11118/r12 packet rejoin.

The original three-factor packet could not distinguish the two isomorphic
target labels.  Two factors have since been accepted and labelled by the
competition verifier.  Rejoining those exact hashes to the surviving label
permutations leaves one assignment, which makes the third factor exact.

This script is offline.  It writes one manifest plus coefficient-free
certificate/summary artifacts and never submits.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from fractions import Fraction
from pathlib import Path

import stage_frobenius_gold as frobenius
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
CANDIDATES = DATA / "team1_swing_candidates.jsonl"
FROBENIUS = DATA / "uncertified_multi_11118_r16_frobenius_certificate.json"
MANIFEST = ROOT / "outbox/v14_postverification_rejoin_11118_r12.txt"
CERTIFICATE = DATA / "v14_postverification_rejoin_11118_r12_certificate.json"
SUMMARY = DATA / "v14_postverification_rejoin_11118_r12_summary.json"

SOURCE = {
    "submissionId": "sub_a7305574cd4a4cd091a58718ef465bf9",
    "polynomialIndex": 260,
    "label": "24T11118",
    "r": 16,
}
OBSERVED_FACTORS = {
    0: ("24T11752", 12),
    2: ("24T11752", 8),
}
SELECTED_FACTOR = 1
EXPECTED_TARGET = ("24T11118", 12)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite sealed output: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_source() -> dict:
    matches = []
    for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (
            row.get("status") == "certified_multi"
            and str(row.get("sourceSubmissionId")) == SOURCE["submissionId"]
            and int(row.get("sourcePolynomialIndex", -1)) == SOURCE["polynomialIndex"]
            and str(row.get("sourceLabel")) == SOURCE["label"]
            and int(row.get("sourceR", -1)) == SOURCE["r"]
        ):
            matches.append(row)
    if len(matches) != 1:
        raise ValueError(f"expected one exact source packet, found {len(matches)}")
    source = matches[0]
    orbit = source.get("orbitCertificate") or {}
    if (
        int(source.get("workerExitCode", -1)) != 0
        or orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or not orbit.get("actualDegrees")
        or any(int(value) != 1 for value in orbit.get("exponents") or [])
    ):
        raise ValueError("source packet does not have an exact squarefree orbit certificate")
    return source


def canonical_candidates(source: dict) -> dict[int, dict]:
    rows = {int(row["factorIndex"]): row for row in source.get("candidates") or []}
    if set(rows) != {0, 1, 2}:
        raise ValueError("source packet does not have factors 0, 1, 2")
    for index, row in rows.items():
        line = frobenius.validated_coefficient_line(row, frobenius.source_key(source))
        if hashlib.sha256(line.encode("ascii")).hexdigest() != str(
            row["coefficientSha256"]
        ):
            raise ValueError(f"factor {index} hash mismatch")
        if int(row.get("targetR", -1)) not in range(0, 25, 2):
            raise ValueError(f"factor {index} has an invalid signature")
    return rows


def surviving_assignments(source: dict) -> tuple[dict, list[tuple[str, ...]]]:
    certificate = json.loads(FROBENIUS.read_text(encoding="utf-8"))
    if str(certificate.get("inputSha256")) != sha256_path(CANDIDATES):
        raise ValueError("Frobenius certificate does not pin the candidate artifact")
    matches = [
        row
        for row in certificate.get("rows") or []
        if frobenius.source_key(row) == frobenius.source_key(source)
    ]
    if len(matches) != 1 or matches[0].get("status") != "unresolved":
        raise ValueError("expected one unresolved Frobenius row")
    proof = matches[0]
    slot_labels = [str(row["targetLabel"]) for row in proof.get("targetSlots") or []]
    assignments = []
    for raw in proof.get("remainingLabelAssignments") or []:
        assignment = tuple(str(label) for label in raw)
        if len(assignment) != 3 or sorted(assignment) != sorted(slot_labels):
            raise ValueError("surviving assignment is not an exact slot permutation")
        assignments.append(assignment)
    if len(assignments) < 2 or len(assignments) != len(set(assignments)):
        raise ValueError("Frobenius assignment set is missing or duplicated")
    return proof, assignments


def outbox_hashes() -> set[str]:
    result = set()
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = single.canonical_polynomial_line(raw.strip())
            if line is not None:
                result.add(hashlib.sha256(line.encode("ascii")).hexdigest())
    return result


def resolve_receipt_pair_after_verification(
    connection: sqlite3.Connection,
    target: tuple[str, int],
    receipt_hashes: set[str],
) -> tuple[bool, list[dict]]:
    """Resolve conservative queued pair maps once their hashes are verified.

    Receipt maps deliberately listed every possible label before verification.
    A mapped pair is no longer an occupied receipt outcome when every receipt
    hash that listed it now has a unique accepted ledger label elsewhere.
    """
    target_text = f"{target[0]}/r{target[1]}"
    anchors = []
    for path in sorted(DATA.rglob("*receipt_mapping*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        for row in value.get("receiptPolynomialPossiblePairs") or []:
            if target_text not in {str(item) for item in row.get("possiblePairs") or []}:
                continue
            digest = str(row.get("coefficientSha256", ""))
            if digest not in receipt_hashes:
                raise ValueError("possible-pair map references a nonreceipt hash")
            verified = connection.execute(
                "SELECT v.submission_id,v.polynomial_index,v.status,v.label,v.r "
                "FROM polynomials p JOIN verifications v "
                "USING(submission_id,polynomial_index) WHERE p.coefficient_hash=?",
                (digest,),
            ).fetchall()
            exact = [
                item
                for item in verified
                if str(item["status"]) == "accepted"
                and item["label"] is not None
                and item["r"] is not None
            ]
            exact_pairs = {(str(item["label"]), int(item["r"])) for item in exact}
            if len(exact_pairs) != 1:
                raise ValueError(
                    f"receipt pair possibility remains unresolved for hash {digest}"
                )
            actual = next(iter(exact_pairs))
            anchors.append(
                {
                    "actualLabel": actual[0],
                    "actualR": actual[1],
                    "coefficientSha256": digest,
                    "mappingArtifact": display(path),
                    "polynomialIndex": int(exact[0]["polynomial_index"]),
                    "submissionId": str(exact[0]["submission_id"]),
                }
            )
    return any((row["actualLabel"], row["actualR"]) == target for row in anchors), anchors


def return_existing_seal() -> int | None:
    present = [path.exists() for path in (MANIFEST, CERTIFICATE, SUMMARY)]
    if not any(present):
        return None
    if not all(present):
        raise ValueError("post-verification rejoin outputs are only partially present")
    certificate = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    manifest = certificate.get("manifest") or {}
    if (
        certificate.get("coefficientMaterialIncluded") is not False
        or summary.get("coefficientMaterialIncluded") is not False
        or str(manifest.get("sha256")) != sha256_path(MANIFEST)
        or str((summary.get("certificate") or {}).get("sha256")) != sha256_path(CERTIFICATE)
    ):
        raise ValueError("existing post-verification rejoin seal is inconsistent")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    if (existing := return_existing_seal()) is not None:
        return existing
    source = load_source()
    candidates = canonical_candidates(source)
    proof, assignments = surviving_assignments(source)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        parent = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
            "v.scoring_status FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
            "AND p.polynomial_index=?",
            (SOURCE["submissionId"], SOURCE["polynomialIndex"]),
        ).fetchone()
        if parent is None or (
            str(parent["status"]) != "accepted"
            or str(parent["label"]) != SOURCE["label"]
            or int(parent["r"]) != SOURCE["r"]
            or int(parent["scoreable"] or 0) != 1
            or int(parent["in_baseline"] or 0) != 0
            or str(parent["scoring_status"]) != "scoreable"
        ):
            raise ValueError("source is not an accepted-scoreable ledger anchor")

        observed = {}
        for index, pair in OBSERVED_FACTORS.items():
            digest = str(candidates[index]["coefficientSha256"])
            rows = connection.execute(
                "SELECT v.submission_id,v.polynomial_index,v.status,v.label,v.r,"
                "v.scoreable,v.in_baseline,v.scoring_status FROM polynomials p "
                "JOIN verifications v USING(submission_id,polynomial_index) "
                "WHERE p.coefficient_hash=?",
                (digest,),
            ).fetchall()
            exact = [
                row
                for row in rows
                if str(row["status"]) == "accepted"
                and str(row["label"]) == pair[0]
                and int(row["r"]) == pair[1]
                and int(row["scoreable"] or 0) == 1
                and int(row["in_baseline"] or 0) == 0
                and str(row["scoring_status"]) == "scoreable"
            ]
            if len(exact) != 1:
                raise ValueError(f"factor {index} has no unique accepted verifier anchor")
            observed[index] = {
                "coefficientSha256": digest,
                "factorIndex": index,
                "label": pair[0],
                "polynomialIndex": int(exact[0]["polynomial_index"]),
                "r": pair[1],
                "submissionId": str(exact[0]["submission_id"]),
            }

        refined = [
            assignment
            for assignment in assignments
            if all(assignment[index] == pair[0] for index, pair in OBSERVED_FACTORS.items())
        ]
        if refined != [("24T11752", "24T11118", "24T11752")]:
            raise ValueError(f"post-verification assignment did not become unique: {refined}")
        selected = candidates[SELECTED_FACTOR]
        target = (refined[0][SELECTED_FACTOR], int(selected["targetR"]))
        if target != EXPECTED_TARGET:
            raise ValueError(f"unexpected exact target after rejoin: {target}")

        selected_hash = str(selected["coefficientSha256"])
        ledger_hashes = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        locally_known = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE label IS NOT NULL AND r IS NOT NULL"
            )
        }
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, {selected_hash: {target}}
        )
        receipt_pair_after_verification, receipt_resolution = (
            resolve_receipt_pair_after_verification(
                connection, target, receipt_hashes
            )
        )
        target_row = connection.execute(
            "SELECT discovered,team_count,generated_at FROM targets WHERE label=? AND r=?",
            target,
        ).fetchone()
        if target_row is None:
            raise ValueError("exact target pair is absent from the target catalogue")
        exclusions = {
            "baselinePair": target in baseline,
            "knownLedgerHash": selected_hash in ledger_hashes,
            "locallyKnownPair": target in locally_known,
            "locallyOwnedPair": target in owned,
            "otherOutboxHash": selected_hash in outbox_hashes(),
            "receiptHash": selected_hash in receipt_hashes,
            "receiptPairAfterVerifiedHashResolution": receipt_pair_after_verification,
        }
        if any(exclusions.values()):
            raise ValueError(f"selected row is excluded: {exclusions}")
    finally:
        connection.close()

    line = frobenius.validated_coefficient_line(selected, frobenius.source_key(source))
    manifest_payload = (line + "\n").encode("ascii")
    write_new(MANIFEST, manifest_payload)
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    score = Fraction(1, 2 ** int(target_row["team_count"]))

    certificate = {
        "schemaVersion": "v14-postverification-packet-rejoin-v1",
        "status": "certified_exact_safe_staged_not_submitted",
        "method": "exact-surviving-label-permutation-filtered-by-two-verifier-anchors",
        "coefficientMaterialIncluded": False,
        "source": {
            **SOURCE,
            "coefficientSha256": str(parent["coefficient_hash"]),
            "ledgerStatus": "accepted_scoreable_nonbaseline",
        },
        "inputs": {
            "candidates": {
                "path": display(CANDIDATES),
                "sha256": sha256_path(CANDIDATES),
            },
            "frobeniusCertificate": {
                "path": display(FROBENIUS),
                "sha256": sha256_path(FROBENIUS),
                "status": str(proof["status"]),
                "survivingAssignmentsBeforeVerifierRejoin": len(assignments),
            },
        },
        "verifierAnchors": [observed[index] for index in sorted(observed)],
        "assignmentRefinement": {
            "survivingAssignmentsAfterVerifierRejoin": 1,
            "uniqueAssignmentLabelsByFactor": list(refined[0]),
        },
        "selected": {
            "coefficientSha256": selected_hash,
            "factorIndex": SELECTED_FACTOR,
            "label": target[0],
            "r": target[1],
            "targetDiscovered": bool(target_row["discovered"]),
            "targetTeamCount": int(target_row["team_count"]),
            "projectedMarginalScoreExact": str(score),
        },
        "exclusions": exclusions,
        "receiptExclusionSnapshot": {
            **{key: value for key, value in receipt_audit.items() if key != "audit"},
            "conservativeReceiptPairBeforeVerification": target in receipt_pairs,
            "receiptPairAfterVerifiedHashResolution": receipt_pair_after_verification,
            "verifiedHashResolutionAnchors": receipt_resolution,
        },
        "manifest": {
            "bytes": len(manifest_payload),
            "path": display(MANIFEST),
            "polynomials": 1,
            "sha256": manifest_hash,
        },
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "ledgerWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }
    write_new(CERTIFICATE, json_bytes(certificate))
    summary = {
        "schemaVersion": "v14-postverification-packet-rejoin-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "exactSafeRows": 1,
        "projectedMarginalScoreExact": str(score),
        "target": {"label": target[0], "r": target[1]},
        "certificate": {
            "path": display(CERTIFICATE),
            "sha256": sha256_path(CERTIFICATE),
        },
        "manifest": certificate["manifest"],
        "submissionCalls": 0,
    }
    write_new(SUMMARY, json_bytes(summary))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

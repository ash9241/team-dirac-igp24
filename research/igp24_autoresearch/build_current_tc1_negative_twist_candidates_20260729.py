#!/usr/bin/env python3
"""Build the exact eight-row current-tc1 negative-twist reserve.

The computation is offline and light-only.  It revalidates accepted source
pins against the read-only ledger, the sealed unique two-point-block action,
the exact Sturm signature, a ramification/disjointness witness, and every
local novelty exclusion before writing one candidate JSONL and a
coefficient-free certificate.  It never stages, submits, or changes the
ledger.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as outbox_audit
import stage_single_exact_census as exact
from stage_v14_negative_twist import (
    exact_real_root_count,
    is_prime,
    sha256_path,
    squarefree_mod_prime,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
OUTPUT = DATA / "current_tc1_negative_twist_candidates_20260729.jsonl"
CERTIFICATE = DATA / "current_tc1_negative_twist_certificate_20260729.json"
PRIME_START = 10009

SPECS = (
    (
        "sub_84f2683ad7ce4f8fae8234db68da2646",
        190,
        "985e72dfbdac89877ca77767f0ccedeec4a33bc4fa9827d91e56e56b2a4a0892",
        "24T13430",
        0,
        24,
    ),
    (
        "sub_84f2683ad7ce4f8fae8234db68da2646",
        212,
        "23592c8b0456b6ebcc3478e662aec2afda52b26257a5e27e02ff9b3e4f47eff0",
        "24T13736",
        0,
        24,
    ),
    (
        "sub_7e71d36db151442aa36d3c3613d87835",
        292,
        "ffdf11e5e963df3bf02ce45e8e62ad378cd57ace722aff22302845188e2744b1",
        "24T17141",
        0,
        12,
    ),
    (
        "sub_1da79e6b7050490fa02a20dc368316e5",
        133,
        "6ade1f2c3f2175275e6d6d8aa2904800775195fa1b46549b5e059b320cad902f",
        "24T17626",
        0,
        16,
    ),
    (
        "sub_83fdb16f7dcd4ad09be8998ee3fe444b",
        138,
        "89dbc198c7231bc8074eba75dfa76ccfd3ce2190e2acf19684a2f6480d5fce6c",
        "24T20707",
        4,
        12,
    ),
    (
        "sub_e83d6779a8e64da39b48e3ed37ce186a",
        134,
        "e3dd008e714004ddc6abe92928e8fab49aa28dd851203b486319309a9ffeaa66",
        "24T23372",
        4,
        12,
    ),
    (
        "sub_b00af04d6a8a475482aabe9f23c2d0e4",
        16,
        "7441ea73bd4d62465b14939f06925999c5f53d018f3cf1e405e9ca9a52e57291",
        "24T5876",
        24,
        0,
    ),
    (
        "sub_85d0fda79dc0405ea8be78607d699c9a",
        6,
        "941f59d59ff0316d2076ec9658bf4ef6718c11ef18e525fe59d0835e9ffa039f",
        "24T6873",
        24,
        0,
    ),
)


def canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def artifact(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": sha256_path(path),
    }


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite differing artifact: {path}")
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def action_index() -> dict[str, tuple[int, dict, str]]:
    rows: dict[str, tuple[int, dict, str]] = {}
    for record, raw in enumerate(
        ACTION_MAP.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        label = str(row["sourceLabel"])
        if label in rows:
            raise ValueError(f"duplicate twist action for {label}")
        rows[label] = (
            record,
            row,
            hashlib.sha256(canonical_json(row)).hexdigest(),
        )
    return rows


def source_row(
    connection: sqlite3.Connection, submission_id: str, polynomial_index: int
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.status,v.scoreable,
               v.in_baseline,v.scoring_status,v.label,v.r,v.field_disc_abs
        FROM polynomials p JOIN verifications v
          USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"source key is absent: {submission_id}:{polynomial_index}")
    return row


def build_candidate(
    connection: sqlite3.Connection,
    actions: dict[str, tuple[int, dict, str]],
    ledger_hashes: set[str],
    submission_id: str,
    polynomial_index: int,
    expected_source_hash: str,
    label: str,
    source_r: int,
    target_r: int,
) -> dict:
    source = source_row(connection, submission_id, polynomial_index)
    if (
        str(source["coefficient_hash"]) != expected_source_hash
        or str(source["status"]) != "accepted"
        or int(source["scoreable"] or 0) != 1
        or int(source["in_baseline"] or 0) != 0
        or str(source["scoring_status"]) != "scoreable"
        or str(source["label"]) != label
        or int(source["r"]) != source_r
    ):
        raise ValueError(f"accepted source pin changed: {submission_id}:{polynomial_index}")

    values = [int(value) for value in str(source["coefficients"]).split(",")]
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] == 0
        or math.gcd(*values) != 1
        or any(values[index] != 0 for index in range(1, 25, 2))
    ):
        raise ValueError(f"source is not primitive monic even degree 24: {label}")

    matches = [entry for key, entry in actions.items() if key == label]
    if len(matches) != 1:
        raise ValueError(f"twist action is absent or nonunique: {label}")
    action_record, action, action_row_hash = matches[0]
    systems = list(action.get("systems") or [])
    if (
        int(action.get("systemCount", -1)) != 1
        or list(action.get("targetLabels") or []) != [label]
        or len(systems) != 1
        or systems[0].get("flipInSource") is not True
        or str(systems[0].get("targetLabel")) != label
        or int(systems[0].get("targetT", -1)) != int(label[3:])
    ):
        raise ValueError(f"action does not prove a unique same-label twist: {label}")

    quotient_roots, sturm_length = exact_real_root_count(values[::2])
    actual_target_r = 2 * quotient_roots - source_r
    if actual_target_r != target_r:
        raise ValueError(
            f"negative twist signature changed for {label}: "
            f"r={actual_target_r}, expected r={target_r}"
        )

    prime = PRIME_START
    while True:
        while not is_prime(prime):
            prime += 1
        if squarefree_mod_prime(values, prime):
            break
        prime += 1
    twist_d = -prime
    twisted = [0] * 25
    for index in range(13):
        twisted[2 * index] = values[2 * index] * twist_d ** (12 - index)
    line = ",".join(str(value) for value in twisted)
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if (
        len(twisted) != 25
        or twisted[-1] != 1
        or twisted[0] == 0
        or math.gcd(*twisted) != 1
        or digest in ledger_hashes
    ):
        raise ValueError(f"candidate is noncanonical or already in the ledger: {label}")

    pair = (label, target_r)
    target = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?",
        pair,
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
    ).fetchone()
    locally_known = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
    ).fetchone()
    if (
        target is None
        or int(target["team_count"]) != 1
        or int(target["discovered"]) != 1
        or baseline is not None
        or locally_known is not None
    ):
        raise ValueError(f"target is not current tc1/nonbaseline/unowned: {pair}")

    return {
        "schemaVersion": "current-tc1-negative-twist-candidate-v1",
        "status": "certified_exact_novel_current_tc1_unstaged",
        "coefficientLine": line,
        "coefficientSha256": digest,
        "coefficientBytes": len(line.encode("ascii")),
        "sourceSubmissionId": submission_id,
        "sourcePolynomialIndex": polynomial_index,
        "sourceCoefficientSha256": expected_source_hash,
        "sourceFieldDiscriminantAbs": str(source["field_disc_abs"]),
        "sourceLabel": label,
        "sourceR": source_r,
        "targetLabel": label,
        "targetR": target_r,
        "targetTeamCount": 1,
        "targetGeneratedAt": str(target["generated_at"]),
        "targetMinimumDiscAbs": str(target["minimum_disc_abs"]),
        "projectedMarginalScoreExact": "1/2",
        "actionRecord": action_record,
        "actionRowSha256": action_row_hash,
        "quotientRealRootCount": quotient_roots,
        "sturmSequenceLength": sturm_length,
        "ramificationPrime": prime,
        "twistD": twist_d,
        "primitiveMonicDegree24": True,
        "irreducible": True,
        "irreducibilityProof": (
            "The source polynomial is accepted with a transitive exact 24T label. "
            "It is squarefree modulo the displayed ramification prime, so the "
            "source splitting field is unramified there; Q(sqrt(twistD)) is "
            "ramified there and linearly disjoint. The exact unique block action "
            "contains the global flip, hence the generic twist retains the same "
            "transitive 24T action and the candidate is irreducible."
        ),
    }


def main() -> int:
    actions = action_index()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        candidates = [
            build_candidate(connection, actions, ledger_hashes, *spec)
            for spec in SPECS
        ]
        hashes = {str(row["coefficientSha256"]) for row in candidates}
        pairs = {
            (str(row["targetLabel"]), int(row["targetR"])) for row in candidates
        }
        if len(candidates) != 8 or len(hashes) != 8 or len(pairs) != 8:
            raise ValueError("candidate hashes or pairs are not one-to-one")

        pair_index = {
            str(row["coefficientSha256"]): {
                (str(row["targetLabel"]), int(row["targetR"]))
            }
            for row in candidates
        }
        receipt_hashes, receipt_pairs, receipt_meta = exact.receipt_exclusions(
            RECEIPTS, DATA, connection, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
            pair_index, connection
        )
        if (
            hashes & receipt_hashes
            or pairs & receipt_pairs
            or hashes & outbox_hashes
            or pairs & outbox_pairs
        ):
            raise ValueError("candidate reserve intersects a receipt or txt outbox")
    finally:
        connection.close()

    payload = (
        "\n".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in candidates
        )
        + "\n"
    ).encode("utf-8")
    write_new(OUTPUT, payload)

    created_at = datetime.now(timezone.utc).isoformat()
    certificate = {
        "schemaVersion": "current-tc1-negative-twist-certificate-v1",
        "createdAt": created_at,
        "status": "certified_8_exact_novel_current_tc1_candidates_unstaged",
        "method": (
            "accepted source pins + exact unique same-label block actions + "
            "rational Sturm signatures + ramification/disjointness generic twists"
        ),
        "coefficientMaterialIncluded": False,
        "candidateArtifact": artifact(OUTPUT),
        "candidateRows": len(candidates),
        "distinctCandidateHashes": len(hashes),
        "distinctCurrentTc1Pairs": len(pairs),
        "projectedMarginalScoreExact": "4",
        "actionArtifact": artifact(ACTION_MAP),
        "sourcePins": [
            {
                "submissionId": row["sourceSubmissionId"],
                "polynomialIndex": row["sourcePolynomialIndex"],
                "coefficientSha256": row["sourceCoefficientSha256"],
                "pair": f"{row['sourceLabel']}/r{row['sourceR']}",
            }
            for row in candidates
        ],
        "targets": [
            {
                "pair": f"{row['targetLabel']}/r{row['targetR']}",
                "candidateSha256": row["coefficientSha256"],
                "teamCount": 1,
                "projectedMarginalScoreExact": "1/2",
            }
            for row in candidates
        ],
        "proofChecks": {
            "allSourcesAcceptedScoreableAndHashPinned": True,
            "allSourcesPrimitiveMonicEvenDegree24": True,
            "allActionsUniqueSameLabelWithFlipInSource": True,
            "allSignaturesExactByRationalSturm": True,
            "allGenericTwistsHaveRamificationDisjointnessWitness": True,
            "allCandidatesPrimitiveMonicDegree24": True,
            "allCandidatesIrreducibleByExactTransitiveActionProof": True,
            "allCandidateHashesAbsentFromLedger": True,
            "allTargetsCurrentTc1NonbaselineAndLocallyUnknown": True,
            "allCandidateHashesAndPairsAbsentFromReceipts": True,
            "allCandidateHashesAndPairsAbsentFromTxtOutboxes": True,
        },
        "exclusionAudit": {
            "receiptCount": int(receipt_meta["receiptCount"]),
            "receiptHashes": len(receipt_hashes),
            "receiptPairs": len(receipt_pairs),
            "outboxFiles": int(outbox_meta["outboxFiles"]),
            "outboxHashes": len(outbox_hashes),
            "outboxPairs": len(outbox_pairs),
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "ledgerWrites": 0,
            "outboxWrites": 0,
            "receiptWrites": 0,
            "submissionCalls": 0,
        },
    }
    write_new(
        CERTIFICATE,
        (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "candidates": artifact(OUTPUT),
                "certificate": artifact(CERTIFICATE),
                "projectedMarginalScoreExact": "4",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

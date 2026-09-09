#!/usr/bin/env python3
"""Seal the six current-tc1 unordered-pair packets without staging them.

This is a light, offline verifier.  It checks the accepted source pins, the
sealed unordered-pair action rows, the exact resolvent factor-degree
certificates, coefficient hashes/canonicality, the current target snapshot,
and all ledger/receipt/txt-outbox novelty exclusions.  It deliberately writes
only a coefficient-free certificate: no outbox, receipt, ledger, or network
state is touched.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as outbox_audit
import stage_single_exact_census as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
ACTION_MAP = DATA / "pair_orbit_map.jsonl"
CERTIFICATE = DATA / "current_tc1_pair_packets_certificate_20260729.json"

PACKETS = (
    {
        "artifact": "current_tc1_pair_packet_10448_r24_20260729.jsonl",
        "sourceSubmissionId": "sub_a430d23df6194ff48047555f7ea8a879",
        "sourcePolynomialIndex": 171,
        "sourceCoefficientSha256": "74d5b24a8c500acf0b3cdf913b74c803689548b9b7deaa01dca1c18296b6d90c",
        "sourceLabel": "24T10448",
        "actionRecord": 2290,
        "desiredTarget": "24T10733",
        "minimalSelection": {"allFactorIndices": [0, 1, 2]},
    },
    {
        "artifact": "current_tc1_pair_packet_11044_r24_20260729.jsonl",
        "sourceSubmissionId": "sub_686cdcd542b8479ab0dc0a9b8f84c4e9",
        "sourcePolynomialIndex": 954,
        "sourceCoefficientSha256": "0a6122712d0bc184c46f5884f840f8b2058f647db328ff4529be16b904e0a1b8",
        "sourceLabel": "24T11044",
        "actionRecord": 2376,
        "desiredTarget": "24T11572",
        "minimalSelection": {"allFactorIndices": [0, 1, 2]},
    },
    {
        "artifact": "current_tc1_pair_packet_13195_r24_20260729.jsonl",
        "sourceSubmissionId": "sub_b6a0914a23c44e2f8f992a4d9aa09bb8",
        "sourcePolynomialIndex": 631,
        "sourceCoefficientSha256": "8ca3369c2300d4057f1cacd3072d9dd82c87fafe65ac9e37989bc60fc7c6b9bf",
        "sourceLabel": "24T13195",
        "actionRecord": 2774,
        "desiredTarget": "24T13729",
        "minimalSelection": {"allFactorIndices": [0, 1, 2]},
    },
    {
        "artifact": "current_tc1_pair_packet_17119_r24_20260729.jsonl",
        "sourceSubmissionId": "sub_c6f6ebb4f625451d9b552a523274b1f6",
        "sourcePolynomialIndex": 3,
        "sourceCoefficientSha256": "5a32881fd54823a2495dadfec7bfd605e2e217f181aa90efd3c448bd4fbb239c",
        "sourceLabel": "24T17119",
        "actionRecord": 3656,
        "desiredTarget": "24T17807",
        "minimalSelection": {"anyCount": 2, "fromFactorIndices": [0, 1, 2]},
    },
    {
        "artifact": "current_tc1_pair_packet_5432_r24_20260729.jsonl",
        "sourceSubmissionId": "sub_78ebbd552555416598881bd5896559ff",
        "sourcePolynomialIndex": 4,
        "sourceCoefficientSha256": "e376f5b1acee79aa338c7d0fa65ffbf6ffa0af4eecda6f816c820548c18c0f84",
        "sourceLabel": "24T5432",
        "actionRecord": 1463,
        "desiredTarget": "24T6170",
        "minimalSelection": {"allFactorIndices": [0, 1]},
    },
    {
        "artifact": "current_tc1_pair_packet_7871_r24_20260729.jsonl",
        "sourceSubmissionId": "sub_98c04bd3ae724bc99550112db222d655",
        "sourcePolynomialIndex": 314,
        "sourceCoefficientSha256": "bd017cc2690eaf98143ed8246c16df896a699da2cd461faf96d94702603903bf",
        "sourceLabel": "24T7871",
        "actionRecord": 1875,
        "desiredTarget": "24T8505",
        "minimalSelection": {"anyCount": 2, "fromFactorIndices": [0, 1, 2]},
    },
)

# This factor was independently accepted before this packet was generated.
# Its exact ledger assignment removes 24T5424 from the ambiguity set of the
# other two 24T5432 factors.
KNOWN_ASSIGNMENTS = {
    "06a79e2a97f7bf09e22d61beb0de96cc50b0f1531d5c4b4ab9493fecf9694924": {
        "submissionId": "sub_46223ca3866446a1809bbbd3cd1f1e74",
        "polynomialIndex": 201,
        "pair": ("24T5424", 24),
    }
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def artifact(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": sha256_path(path),
    }


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


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
    result: dict[str, tuple[int, dict, str]] = {}
    for record, raw in enumerate(
        ACTION_MAP.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        label = str(row["sourceLabel"])
        if label in result:
            raise ValueError(f"duplicate pair action row for {label}")
        result[label] = (
            record,
            row,
            hashlib.sha256(canonical_json(row)).hexdigest(),
        )
    return result


def read_packet(path: Path) -> dict:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 1:
        raise ValueError(f"packet must contain exactly one JSON row: {path}")
    return json.loads(lines[0])


def source_row(
    connection: sqlite3.Connection, submission_id: str, polynomial_index: int
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,
               v.scoreable,v.in_baseline,v.scoring_status
        FROM polynomials p JOIN verifications v
          USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"missing source pin: {submission_id}:{polynomial_index}")
    return row


def packet_precheck(
    connection: sqlite3.Connection,
    actions: dict[str, tuple[int, dict, str]],
    spec: dict,
) -> dict:
    path = DATA / str(spec["artifact"])
    packet = read_packet(path)
    source = source_row(
        connection,
        str(spec["sourceSubmissionId"]),
        int(spec["sourcePolynomialIndex"]),
    )
    if (
        str(source["coefficient_hash"]) != spec["sourceCoefficientSha256"]
        or str(source["label"]) != spec["sourceLabel"]
        or int(source["r"]) != 24
        or str(source["status"]) != "accepted"
        or int(source["scoreable"] or 0) != 1
        or int(source["in_baseline"] or 0) != 0
        or str(source["scoring_status"]) != "scoreable"
    ):
        raise ValueError(f"accepted source pin changed for {spec['sourceLabel']}")
    source_coefficients = [
        int(value) for value in str(source["coefficients"]).split(",")
    ]
    if (
        len(source_coefficients) != 25
        or source_coefficients[-1] != 1
        or source_coefficients[0] == 0
        or math.gcd(*source_coefficients) != 1
    ):
        raise ValueError(f"source is not primitive monic degree 24: {spec['sourceLabel']}")

    if (
        packet.get("status") != "certified_multi"
        or int(packet.get("workerExitCode", -1)) != 0
        or packet.get("sourceSubmissionId") != spec["sourceSubmissionId"]
        or int(packet.get("sourcePolynomialIndex", -1))
        != int(spec["sourcePolynomialIndex"])
        or packet.get("sourceCoefficientSha256")
        != spec["sourceCoefficientSha256"]
        or packet.get("sourceLabel") != spec["sourceLabel"]
        or int(packet.get("sourceR", -1)) != 24
        or packet.get("transform") != {"kind": "x+c*x^2", "c": 1}
        or packet.get("reduction") != "best"
    ):
        raise ValueError(f"packet header mismatch for {spec['sourceLabel']}")

    action_record, action, action_row_hash = actions[str(spec["sourceLabel"])]
    if action_record != int(spec["actionRecord"]):
        raise ValueError(f"action record moved for {spec['sourceLabel']}")
    action_targets = list(action.get("targets") or [])
    action_counts = Counter(str(row["targetLabel"]) for row in action_targets)
    declared_counts = {
        str(label): int(count)
        for label, count in dict(action.get("targetCounts") or {}).items()
    }
    if (
        int(action.get("length24OrbitCount", -1)) != 3
        or len(action_targets) != 3
        or action_counts != Counter(declared_counts)
        or list(packet.get("orbitTargets") or []) != action_targets
    ):
        raise ValueError(f"packet/action target mismatch for {spec['sourceLabel']}")

    certificate = dict(packet.get("orbitCertificate") or {})
    expected_degrees = sorted(int(value) for value in action.get("orbitSizes") or [])
    if (
        sorted(int(value) for value in certificate.get("expectedDegrees") or [])
        != expected_degrees
        or sorted(int(value) for value in certificate.get("actualDegrees") or [])
        != expected_degrees
        or any(int(value) != 1 for value in certificate.get("exponents") or [])
    ):
        raise ValueError(f"resolvent orbit certificate failed for {spec['sourceLabel']}")

    candidates = list(packet.get("candidates") or [])
    if len(candidates) != 3:
        raise ValueError(f"expected three candidate factors for {spec['sourceLabel']}")
    candidate_rows = []
    for expected_index, candidate in enumerate(candidates):
        line = str(candidate.get("coefficientLine", ""))
        values = [int(value) for value in line.split(",")]
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if (
            int(candidate.get("factorIndex", -1)) != expected_index
            or int(candidate.get("targetR", -1)) != 24
            or len(values) != 25
            or values[-1] != 1
            or values[0] == 0
            or math.gcd(*values) != 1
            or digest != candidate.get("coefficientSha256")
            or int(candidate.get("coefficientBytes", -1)) != len(line.encode("ascii"))
            or int(candidate.get("polynomialDiscriminantAbs", "0")) <= 0
        ):
            raise ValueError(
                f"candidate canonicality/hash check failed for "
                f"{spec['sourceLabel']} factor {expected_index}"
            )
        candidate_rows.append(
            {
                "factorIndex": expected_index,
                "coefficientSha256": digest,
                "coefficientBytes": len(line.encode("ascii")),
                "targetR": 24,
                "knownAssignment": KNOWN_ASSIGNMENTS.get(digest),
            }
        )
    if len({row["coefficientSha256"] for row in candidate_rows}) != 3:
        raise ValueError(f"duplicate candidate factors for {spec['sourceLabel']}")

    return {
        "spec": spec,
        "path": path,
        "packet": packet,
        "action": action,
        "actionRowSha256": action_row_hash,
        "actionCounts": action_counts,
        "candidateRows": candidate_rows,
    }


def target_state(
    connection: sqlite3.Connection,
    pair: tuple[str, int],
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
) -> dict:
    row = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?",
        pair,
    ).fetchone()
    if row is None:
        raise ValueError(f"target disappeared: {pair}")
    baseline = (
        connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
        ).fetchone()
        is not None
    )
    locally_owned = (
        connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
        ).fetchone()
        is not None
    )
    excluded = pair in receipt_pairs or pair in outbox_pairs
    team_count = int(row["team_count"])
    score = (
        Fraction(1, 2**team_count)
        if not baseline and not locally_owned and not excluded
        else Fraction(0)
    )
    return {
        "pair": f"{pair[0]}/r{pair[1]}",
        "teamCount": team_count,
        "discovered": bool(row["discovered"]),
        "minimumDiscAbs": str(row["minimum_disc_abs"]),
        "generatedAt": str(row["generated_at"]),
        "baseline": baseline,
        "locallyOwned": locally_owned,
        "receiptOrTxtOutboxExcluded": excluded,
        "projectedMarginalScoreExact": fraction_text(score),
        "_score": score,
    }


def selected_indices(selection: dict) -> tuple[int, set[int]]:
    if "allFactorIndices" in selection:
        indices = {int(value) for value in selection["allFactorIndices"]}
        return len(indices), indices
    indices = {int(value) for value in selection["fromFactorIndices"]}
    return int(selection["anyCount"]), indices


def main() -> int:
    actions = action_index()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        checked = [packet_precheck(connection, actions, spec) for spec in PACKETS]
        all_candidate_rows = [
            row for packet in checked for row in packet["candidateRows"]
        ]
        all_hashes = {
            str(row["coefficientSha256"]) for row in all_candidate_rows
        }
        if len(all_candidate_rows) != 18 or len(all_hashes) != 18:
            raise ValueError("the six packets do not contain 18 distinct factors")

        ledger_rows = {
            str(row["coefficient_hash"]): row
            for row in connection.execute(
                """
                SELECT p.coefficient_hash,p.submission_id,p.polynomial_index,
                       v.label,v.r,v.status,v.scoreable
                FROM polynomials p JOIN verifications v
                  USING(submission_id,polynomial_index)
                WHERE p.coefficient_hash IN ({})
                """.format(",".join("?" for _ in all_hashes)),
                tuple(sorted(all_hashes)),
            )
        }
        if set(ledger_rows) != set(KNOWN_ASSIGNMENTS):
            raise ValueError(
                "packet ledger overlap changed: "
                f"{sorted(ledger_rows)} != {sorted(KNOWN_ASSIGNMENTS)}"
            )
        for digest, expected in KNOWN_ASSIGNMENTS.items():
            row = ledger_rows[digest]
            if (
                str(row["submission_id"]) != expected["submissionId"]
                or int(row["polynomial_index"]) != expected["polynomialIndex"]
                or (str(row["label"]), int(row["r"])) != expected["pair"]
                or str(row["status"]) != "accepted"
                or int(row["scoreable"] or 0) != 1
            ):
                raise ValueError(f"known factor assignment changed: {digest}")

        candidate_pair_index: dict[str, set[tuple[str, int]]] = {}
        for packet in checked:
            all_pairs = {
                (str(label), 24) for label in packet["actionCounts"]
            }
            known_pairs = {
                tuple(row["knownAssignment"]["pair"])
                for row in packet["candidateRows"]
                if row["knownAssignment"] is not None
            }
            remaining_pairs = all_pairs - known_pairs
            for row in packet["candidateRows"]:
                digest = str(row["coefficientSha256"])
                assignment = row["knownAssignment"]
                candidate_pair_index[digest] = (
                    {tuple(assignment["pair"])}
                    if assignment is not None
                    else set(remaining_pairs)
                )

        receipt_hashes, receipt_pairs, receipt_meta = exact.receipt_exclusions(
            RECEIPTS, DATA, connection, candidate_pair_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
            candidate_pair_index, connection
        )
        fresh_hashes = all_hashes - set(KNOWN_ASSIGNMENTS)
        if fresh_hashes & receipt_hashes or fresh_hashes & outbox_hashes:
            raise ValueError("a fresh packet hash is already in a receipt/txt outbox")

        packet_summaries = []
        full_score = Fraction(0)
        minimum_score = Fraction(0)
        minimal_row_count = 0
        desired_pairs: set[tuple[str, int]] = set()
        for packet in checked:
            spec = packet["spec"]
            desired_pair = (str(spec["desiredTarget"]), 24)
            desired_pairs.add(desired_pair)
            states = [
                target_state(
                    connection,
                    (str(label), 24),
                    receipt_pairs,
                    outbox_pairs,
                )
                for label in sorted(packet["actionCounts"])
            ]
            state_by_pair = {state["pair"]: state for state in states}
            desired_state = state_by_pair[f"{desired_pair[0]}/r24"]
            if (
                desired_state["teamCount"] != 1
                or not desired_state["discovered"]
                or desired_state["baseline"]
                or desired_state["locallyOwned"]
                or desired_state["receiptOrTxtOutboxExcluded"]
            ):
                raise ValueError(f"desired target is no longer fresh tc1: {desired_pair}")

            fresh_indices = {
                int(row["factorIndex"])
                for row in packet["candidateRows"]
                if row["knownAssignment"] is None
            }
            minimum_count, available_indices = selected_indices(
                dict(spec["minimalSelection"])
            )
            if not available_indices <= fresh_indices or minimum_count < 1:
                raise ValueError(f"invalid minimal selection for {spec['sourceLabel']}")
            desired_multiplicity = int(packet["actionCounts"][desired_pair[0]])
            known_desired = sum(
                1
                for row in packet["candidateRows"]
                if row["knownAssignment"] is not None
                and tuple(row["knownAssignment"]["pair"]) == desired_pair
            )
            remaining_total = len(fresh_indices)
            remaining_desired = desired_multiplicity - known_desired
            undesired_fresh = remaining_total - remaining_desired
            if minimum_count <= undesired_fresh:
                raise ValueError(
                    f"minimal selection does not force desired target: "
                    f"{spec['sourceLabel']}"
                )

            packet_full_score = sum(
                (state["_score"] for state in states), Fraction(0)
            )
            # Every minimal choice forces the desired tc1 pair.  Any distinct
            # ancillary label is counted only when every permitted minimal
            # selection must contain it.  For the four all-row selections this
            # is the full score; for either any-two selection only tc1 is forced.
            if "allFactorIndices" in spec["minimalSelection"]:
                packet_minimum_score = packet_full_score
            else:
                packet_minimum_score = Fraction(1, 2)
            full_score += packet_full_score
            minimum_score += packet_minimum_score
            minimal_row_count += minimum_count

            public_states = []
            for state in states:
                state = dict(state)
                state.pop("_score")
                public_states.append(state)
            packet_summaries.append(
                {
                    "packetArtifact": artifact(packet["path"]),
                    "sourcePin": {
                        "submissionId": spec["sourceSubmissionId"],
                        "polynomialIndex": spec["sourcePolynomialIndex"],
                        "coefficientSha256": spec["sourceCoefficientSha256"],
                        "pair": f"{spec['sourceLabel']}/r24",
                    },
                    "actionRecord": spec["actionRecord"],
                    "actionRowSha256": packet["actionRowSha256"],
                    "targetLabelMultiset": dict(
                        sorted(packet["actionCounts"].items())
                    ),
                    "targetR": 24,
                    "desiredCurrentTc1Pair": f"{desired_pair[0]}/r24",
                    "freshCandidateRows": [
                        row
                        for row in packet["candidateRows"]
                        if row["knownAssignment"] is None
                    ],
                    "knownCandidateRows": [
                        row
                        for row in packet["candidateRows"]
                        if row["knownAssignment"] is not None
                    ],
                    "minimalFreshSelectionForGuaranteedTc1Hit": spec[
                        "minimalSelection"
                    ],
                    "singleUnassignedRowCertifiesDesiredTarget": False,
                    "fullFreshSelectionForCompleteNewTargetMultiset": sorted(
                        fresh_indices
                    ),
                    "targetStates": public_states,
                    "minimalGuaranteedMarginalScoreExact": fraction_text(
                        packet_minimum_score
                    ),
                    "fullFreshPacketMarginalScoreExact": fraction_text(
                        packet_full_score
                    ),
                }
            )

        if len(desired_pairs) != 6 or minimal_row_count != 15:
            raise ValueError("desired pairs or minimal row total changed")
        if minimum_score != Fraction(29, 8) or full_score != Fraction(59, 16):
            raise ValueError(
                f"score projection changed: minimum={minimum_score}, full={full_score}"
            )
    finally:
        connection.close()

    created_at = datetime.now(timezone.utc).isoformat()
    if CERTIFICATE.exists():
        existing = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
        if (
            existing.get("schemaVersion")
            != "current-tc1-pair-packets-certificate-v1"
            or not existing.get("createdAt")
        ):
            raise ValueError("existing certificate has an incompatible identity")
        created_at = str(existing["createdAt"])
    certificate = {
        "schemaVersion": "current-tc1-pair-packets-certificate-v1",
        "createdAt": created_at,
        "status": "certified_6_current_tc1_set_hits_17_fresh_rows_unstaged",
        "method": (
            "accepted source pins + sealed unordered-pair orbit maps + exact "
            "resolvent factor-degree multisets + totally-real source signatures "
            "+ current ledger/receipt/txt-outbox novelty gates"
        ),
        "coefficientMaterialIncluded": False,
        "actionArtifact": artifact(ACTION_MAP),
        "packetCount": 6,
        "candidateRows": 18,
        "freshCandidateRows": 17,
        "knownCandidateRows": 1,
        "distinctGuaranteedCurrentTc1Pairs": 6,
        "minimalFreshRowsForSixGuaranteedTc1Hits": 15,
        "fullFreshRowsForCompleteNewTargetMultisets": 17,
        "projectedTc1MarginalScoreExact": "3",
        "minimalGuaranteedMarginalScoreExact": "29/8",
        "fullFreshPacketMarginalScoreExact": "59/16",
        "packets": packet_summaries,
        "proofChecks": {
            "allSourcesAcceptedScoreableNonbaselineAndHashPinned": True,
            "allSourcesPrimitiveMonicDegree24AndTotallyReal": True,
            "allActionRowsUniqueAndHashPinned": True,
            "allResolventFactorDegreeMultisetsMatchExactOrbitCensuses": True,
            "allResolventFactorsSquarefreeWithExponentOne": True,
            "allReducedCandidatesWorkerCheckedIrreducible": True,
            "allCandidatesPrimitiveMonicDegree24AndHashRevalidated": True,
            "allCandidateSignaturesExactlyR24": True,
            "exactlyOneCandidateAlreadyAcceptedAndAssigned": True,
            "allOtherCandidateHashesAbsentFromLedger": True,
            "allFreshCandidateHashesAbsentFromReceipts": True,
            "allFreshCandidateHashesAbsentFromTxtOutboxes": True,
            "allDesiredPairsCurrentTc1NonbaselineLocallyUnknown": True,
            "allDesiredPairsAbsentFromReceiptsAndTxtOutboxes": True,
            "fullFreshRowsRealizeEachRemainingExactTargetMultiset": True,
            "minimalSelectionsForceEveryDesiredTc1PairByPigeonhole": True,
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
            "certificateWrites": 1,
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
                "certificate": artifact(CERTIFICATE),
                "minimalFreshRows": 15,
                "freshRows": 17,
                "minimalGuaranteedMarginalScoreExact": "29/8",
                "fullFreshPacketMarginalScoreExact": "59/16",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

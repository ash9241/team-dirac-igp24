#!/usr/bin/env python3
"""Seal the exact 24T10513/r24 pair-action pilot without staging it."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import analyze_rank12_routes as shared
import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PREFIX = DATA / "rank10_t00134_top10000_placements_20260730.jsonl"
PACKET = DATA / "fresh_t00134_top10000_pair_pilot_10513_r24_idx863.jsonl"
FROBENIUS = (
    DATA
    / "fresh_t00134_top10000_pair_pilot_10513_r24_idx863_frobenius.json"
)
OUTPUT = (
    DATA / "fresh_t00134_top10000_pair_pilot_10513_r24_idx863_audit.json"
)

SOURCE_SUBMISSION_ID = "sub_d3d5ec8995bd4d4b896095b2fb27095c"
SOURCE_POLYNOMIAL_INDEX = 863
SOURCE_LABEL = "24T10513"
SOURCE_R = 24
SOURCE_HASH = "1f36cf6ee20d84364820d45931314c4b601d5ce8baca83f884e42920a9d99c37"
TARGET_PAIR = ("24T10298", 24)
TARGET_HASH = "197ec87b3e56ccb08fdc1359d823a13d97f42b4d34b7a47b24fc1ab5b16e4f3c"


def one_jsonl(path: Path) -> dict:
    rows = shared.read_jsonl(path)
    if len(rows) != 1:
        raise ValueError(f"expected exactly one row: {path}")
    return rows[0]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    packet = one_jsonl(PACKET)
    frobenius = json.loads(FROBENIUS.read_text(encoding="utf-8"))
    packet_sha = shared.sha256_file(PACKET)
    frobenius_sha = shared.sha256_file(FROBENIUS)

    require(packet.get("status") == "certified_multi", "packet is not certified")
    require(
        (
            packet.get("sourceSubmissionId"),
            int(packet.get("sourcePolynomialIndex", -1)),
            packet.get("sourceLabel"),
            int(packet.get("sourceR", -1)),
            packet.get("sourceCoefficientSha256"),
        )
        == (
            SOURCE_SUBMISSION_ID,
            SOURCE_POLYNOMIAL_INDEX,
            SOURCE_LABEL,
            SOURCE_R,
            SOURCE_HASH,
        ),
        "source provenance mismatch",
    )
    certificate = packet.get("orbitCertificate") or {}
    require(
        certificate.get("actualDegrees") == [12, 24, 24, 24, 96, 96]
        and certificate.get("expectedDegrees") == [12, 24, 24, 24, 96, 96]
        and certificate.get("exponents") == [1, 1, 1, 1, 1, 1],
        "factorization/orbit certificate mismatch",
    )
    require(
        sorted(
            (str(row["targetLabel"]), int(row["kernelOrder"]))
            for row in packet["orbitTargets"]
        )
        == [("24T10298", 1), ("24T11781", 1), ("24T11781", 1)],
        "faithful target action mismatch",
    )

    require(
        frobenius.get("inputSha256") == packet_sha
        and frobenius.get("summary")
        == {"contradiction": 0, "resolved": 1, "rows": 1, "unresolved": 0},
        "Frobenius certificate does not seal the packet",
    )
    frobenius_rows = frobenius.get("rows") or []
    require(len(frobenius_rows) == 1, "unexpected Frobenius row count")
    proof = frobenius_rows[0]
    require(
        proof.get("status") == "resolved"
        and (proof.get("jointProof") or {}).get("used") is True,
        "multi-orbit assignment was not exactly resolved",
    )
    assignments = {
        int(row["factorIndex"]): row for row in proof.get("assignments", [])
    }
    require(
        sorted((row["targetLabel"], int(row["targetR"])) for row in assignments.values())
        == [("24T10298", 24), ("24T11781", 24), ("24T11781", 24)],
        "assigned target multiset mismatch",
    )
    chosen_assignment = next(
        row for row in assignments.values() if row["targetLabel"] == TARGET_PAIR[0]
    )
    require(
        chosen_assignment["coefficientSha256"] == TARGET_HASH
        and int(chosen_assignment["targetR"]) == TARGET_PAIR[1],
        "chosen factor assignment mismatch",
    )
    candidates = {
        int(row["factorIndex"]): row for row in packet.get("candidates", [])
    }
    chosen = candidates[int(chosen_assignment["factorIndex"])]
    require(
        chosen.get("coefficientSha256") == TARGET_HASH
        and int(chosen.get("targetR", -1)) == TARGET_PAIR[1],
        "chosen candidate packet mismatch",
    )

    placements = [
        row
        for row in shared.read_jsonl(PREFIX)
        if (str(row["label"]), int(row["r"])) == TARGET_PAIR
    ]
    require(len(placements) == 1, "target placement is not unique in prefix")
    placement = placements[0]
    k_teams = int(placement["kTeams"])
    current_points = float(placement["points"])
    minimum_disc = int(placement["minScoringDiscAbs"])
    holder_disc = int(placement["scoringDiscAbs"])
    candidate_disc = int(chosen["fieldDiscriminantAbs"])
    require(
        k_teams == 6
        and candidate_disc > 1
        and minimum_disc > 1
        and holder_disc >= minimum_disc,
        "target scoring state mismatch",
    )

    old_base = 2.0 ** (-(k_teams - 1))
    new_base = 2.0 ** (-k_teams)
    new_minimum = min(minimum_disc, candidate_disc)
    current_recomputed = old_base * min(
        1.0, math.log(minimum_disc) / math.log(holder_disc)
    )
    opponent_after = new_base * min(
        1.0, math.log(new_minimum) / math.log(holder_disc)
    )
    candidate_after = new_base * min(
        1.0, math.log(new_minimum) / math.log(candidate_disc)
    )
    net_published = current_points - opponent_after + candidate_after
    net_recomputed = current_recomputed - opponent_after + candidate_after
    require(
        abs(current_recomputed - current_points) <= 5.1e-7,
        "published points disagree with exact discriminants beyond rounding",
    )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        ledger_rows = list(
            connection.execute(
                """
                SELECT p.submission_id,p.polynomial_index,v.status,v.label,v.r,
                       v.scoreable,v.in_baseline
                FROM polynomials AS p
                LEFT JOIN verifications AS v
                  USING(submission_id,polynomial_index)
                WHERE p.coefficient_hash=?
                """,
                (TARGET_HASH,),
            )
        )
        source = connection.execute(
            """
            SELECT v.status,v.scoreable,v.label,v.r,v.field_disc_abs,
                   p.coefficient_hash
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.submission_id=? AND v.polynomial_index=?
            """,
            (SOURCE_SUBMISSION_ID, SOURCE_POLYNOMIAL_INDEX),
        ).fetchone()
        require(
            source is not None
            and source["status"] == "accepted"
            and int(source["scoreable"]) == 1
            and (source["label"], int(source["r"])) == (SOURCE_LABEL, SOURCE_R)
            and source["coefficient_hash"] == SOURCE_HASH,
            "ledger source pin failed",
        )
        exact_pool, _single, _exact_meta, pair_index = raid.exact_corpus(
            connection
        )
        receipt_hashes, receipt_pairs, receipt_meta = (
            exact_single.receipt_exclusions(
                lane.RECEIPTS, DATA, connection, pair_index
            )
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(pair_index, connection)
        )
        snapshot = pair_routes.load_ledger_snapshot(connection)
        known_hashes = exact_single.query_known_hashes(
            connection, {TARGET_HASH}
        )
    finally:
        connection.close()

    novelty = {
        "ledgerRows": len(ledger_rows),
        "knownLedgerHash": TARGET_HASH in known_hashes,
        "receiptHash": TARGET_HASH in receipt_hashes,
        "receiptPair": TARGET_PAIR in receipt_pairs,
        "outboxHash": TARGET_HASH in outbox_hashes,
        "outboxPair": TARGET_PAIR in outbox_pairs,
        "targetOwned": TARGET_PAIR in snapshot["owned"],
        "targetBaseline": TARGET_PAIR in snapshot["baseline"],
        "targetKnownVerificationPair": TARGET_PAIR in snapshot["knownPairs"],
        "localExactCorpusContainsGeneratedPilot": TARGET_HASH in exact_pool,
        "receiptFilesAudited": receipt_meta.get("manifestFiles"),
        "outboxFilesAudited": outbox_meta.get("outboxFiles"),
    }
    require(
        all(
            not novelty[key]
            for key in (
                "ledgerRows",
                "knownLedgerHash",
                "receiptHash",
                "receiptPair",
                "outboxHash",
                "outboxPair",
                "targetOwned",
                "targetBaseline",
                "targetKnownVerificationPair",
            )
        ),
        "candidate or target is not novel/eligible",
    )

    joint = proof["jointProof"]
    result = {
        "schemaVersion": "fresh-t00134-top10000-pair-pilot-audit-v1",
        "status": "certified_exact_novel_not_staged_or_submitted",
        "source": {
            "submissionId": SOURCE_SUBMISSION_ID,
            "polynomialIndex": SOURCE_POLYNOMIAL_INDEX,
            "label": SOURCE_LABEL,
            "r": SOURCE_R,
            "coefficientSha256": SOURCE_HASH,
            "fieldDiscriminantAbs": str(source["field_disc_abs"]),
            "ledgerStatus": "accepted_scoreable",
        },
        "construction": {
            "transform": packet["transform"],
            "reduction": packet["reduction"],
            "factorDegrees": certificate["actualDegrees"],
            "factorExponents": certificate["exponents"],
            "faithfulDegree24TargetMultiplicity": {
                "24T10298": 1,
                "24T11781": 2,
            },
            "allCandidateRValues": [
                int(row["targetR"]) for row in packet["candidates"]
            ],
        },
        "assignmentProof": {
            "method": frobenius["method"],
            "status": proof["status"],
            "jointFallbackUsed": joint["used"],
            "eliminatingPrime": int(joint["eliminatingObservations"][0]["prime"]),
            "jointCycleProfileSetSha256": joint["census"][
                "jointCycleProfileSetSha256"
            ],
            "uniqueLabelAssignment": True,
            "unresolvedPermutationOnlyExchangesIdentical24T11781Slots": True,
        },
        "candidate": {
            "factorIndex": int(chosen["factorIndex"]),
            "label": TARGET_PAIR[0],
            "r": TARGET_PAIR[1],
            "coefficientSha256": TARGET_HASH,
            "coefficientBytes": int(chosen["coefficientBytes"]),
            "fieldDiscriminantAbs": str(candidate_disc),
            "polynomialDiscriminantAbs": str(
                chosen["polynomialDiscriminantAbs"]
            ),
            "nfdiscSeconds": float(chosen["nfdiscSeconds"]),
        },
        "targetStateAtCrawl": {
            "prefix": str(PREFIX.relative_to(ROOT)),
            "prefixSha256": shared.sha256_file(PREFIX),
            "teamCount": k_teams,
            "publishedOpponentPoints": current_points,
            "minimumScoringDiscAbs": str(minimum_disc),
            "opponentScoringDiscAbs": str(holder_disc),
        },
        "scoreProjection": {
            "contestBaseFormula": "2^(-(teamCount-1)) before join; 2^(-teamCount) after join",
            "candidateImprovesMinimum": candidate_disc < minimum_disc,
            "candidateDiscOverOldMinimum": candidate_disc / minimum_disc,
            "newMinimumScoringDiscAbs": str(new_minimum),
            "candidateScoreAfterJoin": candidate_after,
            "opponentScoreAfterJoin": opponent_after,
            "opponentReductionUsingPublishedPoints": (
                current_points - opponent_after
            ),
            "netRelativeSwingUsingPublishedPoints": net_published,
            "currentScoreRecomputedFromExactDiscs": current_recomputed,
            "netRelativeSwingRecomputedFromExactDiscs": net_recomputed,
            "currentMinPreservingRatioOneConservativeProjection": (
                new_base + current_points / 2.0
            ),
        },
        "noveltyAndEligibility": novelty,
        "artifacts": {
            "auditProgram": {
                "path": str(Path(__file__).resolve().relative_to(ROOT)),
                "sha256": shared.sha256_file(Path(__file__).resolve()),
            },
            "packet": {
                "path": str(PACKET.relative_to(ROOT)),
                "sha256": packet_sha,
            },
            "frobenius": {
                "path": str(FROBENIUS.relative_to(ROOT)),
                "sha256": frobenius_sha,
            },
        },
        "coefficientPayloadIncludedInAudit": False,
        "networkCalls": 0,
        "stagingCalls": 0,
        "submissionCalls": 0,
    }
    shared.write_json_atomic(OUTPUT, result)
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "outputSha256": shared.sha256_file(OUTPUT),
                "status": result["status"],
                "candidate": f"{TARGET_PAIR[0]}/r{TARGET_PAIR[1]}",
                "netRelativeSwing": net_published,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

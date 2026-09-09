#!/usr/bin/env python3
"""Seal the exact light-only 24T17141/r6 -> r18 pair-then-twist route.

The input is an already certified ``pair_sum_single_v1`` degree-24 factor.
This script does not claim that the non-homogeneous pair constructor commutes
with scalar twisting.  It only applies a generic negative quadratic twist to
the exact cached factor, proves its signature by rational Sturm arithmetic,
and checks the complete current ledger/receipt/outbox/saved/tested boundary.

No Sage, GAP, network, submission, or outbox operation is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import fresh_t00134_twist_route_audit as twist_audit
import fresh_t00134_twist_stage_top10000 as twist_stage
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single
from stage_v14_negative_twist import (
    exact_real_root_count,
    is_prime,
    squarefree_mod_prime,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
CANDIDATES = DATA / "fresh_pair_then_twist_17141_r18_candidate_20260730.jsonl"
CERTIFICATE = DATA / "fresh_pair_then_twist_17141_r18_certificate_20260730.json"
SUMMARY = DATA / "fresh_pair_then_twist_17141_r18_summary_20260730.json"

SOURCE_HASH = "212a0f390face7c7e213dab07e172a7f9579d2b012d84de42cfff3efb29f0726"
SOURCE_PAIR = ("24T17141", 6)
TARGET_PAIR = ("24T17141", 18)
EXPECTED_ACTION_MAP_SHA256 = (
    "88c3b265f9b3ece23c9faf1cd5867289998efeecf29ba35d322edd4fa2f70e27"
)
DISCRIMINANT_EXPONENT = 24 * 23 // 2


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def artifact(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": twist_audit.sha256_path(path),
    }


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return sha256_bytes(payload)


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite sealed output: {path}")
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


def primitive_monic_even(values: list[int]) -> bool:
    return (
        len(values) == 25
        and values[-1] == 1
        and values[0] != 0
        and math.gcd(*values) == 1
        and not any(values[index] for index in range(1, 25, 2))
    )


def historical_candidate_hashes() -> tuple[set[str], dict]:
    hashes: set[str] = set()
    files = []
    for path in sorted(DATA.rglob("*candidate*.jsonl")):
        if path.resolve() == CANDIDATES.resolve() or not path.is_file():
            continue
        before = len(hashes)
        for row in twist_audit.read_jsonl(path):
            for key, value in twist_audit.walk_values(row):
                if key in {
                    "coefficientSha256",
                    "candidateSha256",
                    "candidateCoefficientSha256",
                } and value:
                    hashes.add(str(value))
        files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": twist_audit.sha256_path(path),
                "newHashes": len(hashes) - before,
            }
        )
    return hashes, {
        "files": len(files),
        "distinctHashes": len(hashes),
        "hashSetSha256": canonical_digest(sorted(hashes)),
    }


def load_action() -> dict:
    digest = twist_audit.sha256_path(ACTION_MAP)
    if digest != EXPECTED_ACTION_MAP_SHA256:
        raise ValueError(f"twist action-map hash changed: {digest}")
    matches = [
        row
        for row in twist_audit.read_jsonl(ACTION_MAP)
        if str(row.get("sourceLabel")) == SOURCE_PAIR[0]
    ]
    if len(matches) != 1:
        raise ValueError("source twist action is missing or nonunique")
    action = matches[0]
    systems = action.get("systems") or []
    if (
        int(action.get("sourceT", -1)) != int(SOURCE_PAIR[0][3:])
        or int(action.get("systemCount", -1)) != 1
        or len(systems) != 1
        or twist_audit.unanimous_target(action) != TARGET_PAIR[0]
        or str(systems[0].get("targetLabel")) != TARGET_PAIR[0]
        or int(systems[0].get("targetT", -1)) != int(TARGET_PAIR[0][3:])
        or systems[0].get("flipInSource") is not True
    ):
        raise ValueError("twist action does not prove one exact same-label action")
    return action


def source_factor(single_rows: list[dict]) -> tuple[dict, list[int], dict]:
    rows = [
        row
        for row in single_rows
        if str(row.get("coefficientSha256")) == SOURCE_HASH
        and (row.get("proof") or {}).get("schema") == "pair_sum_single_v1"
    ]
    if not rows:
        raise ValueError("exact pair factor disappeared from the validated corpus")
    cores = {
        (
            str(row["coefficientLine"]),
            str(row["targetLabel"]),
            int(row["targetR"]),
            canonical_digest(row.get("sourcePins") or []),
        )
        for row in rows
    }
    if len(cores) != 1:
        raise ValueError("exact pair factor has conflicting normalized claims")
    row = rows[0]
    if (
        (str(row["targetLabel"]), int(row["targetR"])) != SOURCE_PAIR
        or sha256_bytes(str(row["coefficientLine"]).encode("ascii")) != SOURCE_HASH
        or len(row.get("sourcePins") or []) != 1
    ):
        raise ValueError("exact pair factor pair/hash/source-pin claim changed")
    values = [int(value) for value in str(row["coefficientLine"]).split(",")]
    if not primitive_monic_even(values):
        raise ValueError("exact pair factor is not primitive monic even degree 24")
    claimed_disc = row.get("polynomialDiscriminantAbs")
    computed_disc = twist_stage.exact_even_polynomial_discriminant(values)
    if claimed_disc is None or int(claimed_disc) != computed_disc:
        raise ValueError("exact pair factor polynomial discriminant mismatch")
    return row, values, {
        "claimedPolynomialDiscriminantAbs": str(claimed_disc),
        "computedPolynomialDiscriminantAbs": str(computed_disc),
        "match": True,
    }


def candidate_for_prime(
    values: list[int],
    source_polynomial_disc: int,
    forbidden_hashes: set[str],
) -> dict:
    prime = 3
    attempts = 0
    while prime < 100_000:
        while not is_prime(prime):
            prime += 1
        attempts += 1
        if not squarefree_mod_prime(values, prime):
            prime += 1
            continue
        twist_d = -prime
        candidate = [0] * 25
        for index in range(13):
            candidate[2 * index] = (
                values[2 * index] * twist_d ** (12 - index)
            )
        if not primitive_monic_even(candidate):
            raise ArithmeticError("constructed twist is not primitive monic even")
        line = ",".join(str(value) for value in candidate)
        digest = sha256_bytes(line.encode("ascii"))
        if digest in forbidden_hashes:
            prime += 1
            continue
        return {
            "coefficientLine": line,
            "coefficientSha256": digest,
            "coefficientBytes": len(line.encode("ascii")),
            "polynomialDiscriminantAbs": str(
                source_polynomial_disc * prime**DISCRIMINANT_EXPONENT
            ),
            "primeAttempts": attempts,
            "ramificationPrime": prime,
            "sourceSquarefreeModuloRamificationPrime": True,
            "twistD": twist_d,
        }
    raise ArithmeticError("no fresh squarefree prime below 100000")


def build() -> tuple[dict, dict, bytes]:
    if twist_audit.sha256_path(ACTION_MAP) != EXPECTED_ACTION_MAP_SHA256:
        raise ValueError("pinned twist action map changed")
    action = load_action()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exact_pool, single_rows, exact_meta, pair_index = raid.exact_corpus(
            connection
        )
        factor, values, source_disc_audit = source_factor(single_rows)
        tested_sources, tested_meta = twist_audit.tested_twist_sources(
            single_rows
        )
        if SOURCE_HASH in tested_sources:
            raise ValueError("exact pair factor was already used as a twist source")

        snapshot = pair_routes.load_ledger_snapshot(connection)
        receipt_snapshot = pair_routes.exact_candidate_and_receipt_snapshot(
            connection
        )
        supplemental, supplemental_artifacts = (
            outbox_audit.supplemental_exact_pair_index()
        )
        merged_index = outbox_audit.merged_pair_index(
            receipt_snapshot, supplemental
        )
        named_receipts, named_receipt_pairs = (
            outbox_audit.validate_named_receipts(
                receipt_snapshot, merged_index
            )
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(merged_index, connection)
        )
        saved_pairs = {
            pair for pairs in pair_index.values() for pair in pairs
        }
        pair_exclusions = {
            "baseline": TARGET_PAIR in snapshot["baseline"],
            "acceptedOwned": TARGET_PAIR in snapshot["owned"],
            "knownVerification": TARGET_PAIR in snapshot["knownPairs"],
            "receiptReserved": TARGET_PAIR
            in (receipt_snapshot["receiptPairs"] | named_receipt_pairs),
            "outboxReserved": TARGET_PAIR in outbox_pairs,
            "savedExactCandidatePair": TARGET_PAIR in saved_pairs,
        }
        target = snapshot["targets"].get(TARGET_PAIR)
        if target is None:
            raise ValueError("target pair disappeared from the target cache")
        if (
            int(target["teamCount"]) != 0
            or bool(target["discovered"])
            or any(pair_exclusions.values())
        ):
            raise ValueError(
                f"target is not a fresh current tc0 pair: {pair_exclusions}"
            )

        quotient_roots, sturm_length = exact_real_root_count(values[::2])
        negative_roots = 2 * quotient_roots - SOURCE_PAIR[1]
        if negative_roots != TARGET_PAIR[1]:
            raise ValueError(
                f"negative twist signature changed: r={negative_roots}"
            )

        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        historical_hashes, historical_meta = historical_candidate_hashes()
        forbidden_hashes = (
            ledger_hashes
            | set(exact_pool)
            | set(pair_index)
            | receipt_snapshot["receiptHashes"]
            | outbox_hashes
            | historical_hashes
        )
        candidate = candidate_for_prime(
            values,
            int(source_disc_audit["computedPolynomialDiscriminantAbs"]),
            forbidden_hashes,
        )
        digest = str(candidate["coefficientSha256"])
        hash_exclusions = {
            "ledger": digest in ledger_hashes,
            "validatedExactCorpus": digest in exact_pool,
            "anyExactPairIndex": digest in pair_index,
            "receipt": digest in receipt_snapshot["receiptHashes"],
            "outbox": digest in outbox_hashes,
            "historicalCandidate": digest in historical_hashes,
        }
        if any(hash_exclusions.values()):
            raise ValueError(f"candidate hash is excluded: {hash_exclusions}")

        factor_ledger_rows = [
            {
                "submissionId": str(row["submission_id"]),
                "polynomialIndex": int(row["polynomial_index"]),
                "status": str(row["status"]),
                "scoreable": bool(row["scoreable"]),
                "label": str(row["label"]) if row["label"] is not None else None,
                "r": int(row["r"]) if row["r"] is not None else None,
            }
            for row in connection.execute(
                """
                SELECT p.submission_id,p.polynomial_index,v.status,v.scoreable,
                       v.label,v.r
                FROM polynomials AS p JOIN verifications AS v
                  USING(submission_id,polynomial_index)
                WHERE p.coefficient_hash=?
                """,
                (SOURCE_HASH,),
            )
        ]
    finally:
        connection.close()

    candidate_row = {
        "schemaVersion": "fresh-pair-then-signed-scalar-twist-candidate-v1",
        "status": "certified_pair_then_signed_scalar_twist_exact_unstaged",
        "coefficientLine": candidate["coefficientLine"],
        "coefficientSha256": candidate["coefficientSha256"],
        "coefficientBytes": candidate["coefficientBytes"],
        "polynomialDiscriminantAbs": candidate[
            "polynomialDiscriminantAbs"
        ],
        "sourceCoefficientSha256": SOURCE_HASH,
        "sourcePair": {"label": SOURCE_PAIR[0], "r": SOURCE_PAIR[1]},
        "targetLabel": TARGET_PAIR[0],
        "targetR": TARGET_PAIR[1],
        "targetT": int(TARGET_PAIR[0][3:]),
        "twistSign": "negative",
        "twistD": candidate["twistD"],
        "ramificationPrime": candidate["ramificationPrime"],
        "sourceSquarefreeModuloRamificationPrime": True,
        "proof": {
            "kind": "generic_negative_quadratic_twist_of_exact_pair_factor",
            "pairFactorArtifact": str(
                (ROOT / str((factor.get("proof") or {})["artifact"]))
                .resolve()
                .relative_to(ROOT)
            ),
            "pairFactorArtifactRecord": int(
                (factor.get("proof") or {})["record"]
            ),
            "pairFactorSchema": "pair_sum_single_v1",
            "acceptedSourcePins": factor.get("sourcePins") or [],
            "genericAction": (
                "one cached negation block system; global flip lies in source"
            ),
            "signature": (
                "2*exactRealRoots(sourceEvenQuotient)-sourceRealRoots"
            ),
        },
    }
    candidate_payload = (
        json.dumps(candidate_row, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    candidate_artifact = {
        "path": str(CANDIDATES.relative_to(ROOT)),
        "bytes": len(candidate_payload),
        "sha256": sha256_bytes(candidate_payload),
        "rows": 1,
    }
    certificate = {
        "schemaVersion": "fresh-pair-then-signed-scalar-twist-certificate-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_executable_unstaged_not_submitted",
        "coefficientMaterialIncluded": False,
        "scope": {
            "certifiedOrder": "pair_then_twist",
            "twistThenPairClaimed": False,
            "reason": (
                "the cached pair worker uses h(x)=x+c*x^2 and polredbest, "
                "so coefficient-level scalar commutation is not assumed"
            ),
        },
        "source": {
            "coefficientSha256": SOURCE_HASH,
            "label": SOURCE_PAIR[0],
            "r": SOURCE_PAIR[1],
            "exactCorpusProof": factor.get("proof") or {},
            "acceptedSourcePins": factor.get("sourcePins") or [],
            "factorAcceptedLedgerOccurrences": factor_ledger_rows,
            "factorAcceptanceRequiredBySubmissionRules": False,
            "exactCertifiedFactorRequiredByThisAudit": True,
        },
        "sourcePolynomialDiscriminant": source_disc_audit,
        "action": {
            "artifact": artifact(ACTION_MAP),
            "rowSha256": canonical_digest(action),
            "systemCount": 1,
            "flipInSource": True,
            "targetLabel": TARGET_PAIR[0],
        },
        "signature": {
            "method": "exact-rational-Sturm-sequence-on-even-quotient",
            "quotientDegree": 12,
            "quotientRealRootCount": quotient_roots,
            "sourceRealRootCount": SOURCE_PAIR[1],
            "formula": "2*quotientRealRootCount-sourceRealRootCount",
            "targetRealRootCount": negative_roots,
            "sturmSequenceLength": sturm_length,
        },
        "genericTwist": {
            "ramificationPrime": candidate["ramificationPrime"],
            "twistD": candidate["twistD"],
            "sourceSquarefreeModuloRamificationPrime": True,
            "primeAttempts": candidate["primeAttempts"],
            "proof": (
                "The exact source factor is squarefree modulo p, hence its "
                "splitting field is unramified at p. Q(sqrt(-p)) is ramified "
                "at p and therefore disjoint. The one cached block action "
                "has its global flip in the source group, so the twist keeps "
                "the exact transitive label and remains irreducible."
            ),
        },
        "target": {
            "label": TARGET_PAIR[0],
            "r": TARGET_PAIR[1],
            "teamCount": int(target["teamCount"]),
            "discovered": bool(target["discovered"]),
            "minimumDiscAbs": target["minimumDiscAbs"],
            "candidateCoefficientSha256": candidate["coefficientSha256"],
            "candidatePolynomialDiscriminantAbs": candidate[
                "polynomialDiscriminantAbs"
            ],
            "polynomialDiscriminantScaling": {
                "exponent": DISCRIMINANT_EXPONENT,
                "formula": "absDisc(twist)=absDisc(source)*p^276",
                "exactMatchByConstruction": True,
            },
        },
        "pairExclusions": pair_exclusions,
        "hashExclusions": hash_exclusions,
        "savedAndTestedExclusions": {
            "sourceFactorPreviouslyTwistTested": False,
            "testedTwistSourceSnapshot": tested_meta,
            "historicalCandidateSnapshot": historical_meta,
        },
        "receiptOutboxBoundary": {
            "receiptHashes": len(receipt_snapshot["receiptHashes"]),
            "receiptPairs": len(
                receipt_snapshot["receiptPairs"] | named_receipt_pairs
            ),
            "namedReceiptAudits": named_receipts,
            "supplementalExactPairArtifacts": supplemental_artifacts,
            "outboxHashes": len(outbox_hashes),
            "outboxPairs": len(outbox_pairs),
            "outboxSnapshot": {
                key: value
                for key, value in outbox_meta.items()
                if key != "artifacts"
            },
        },
        "exactCorpusSnapshot": exact_meta,
        "candidateArtifact": candidate_artifact,
        "reproduceCommand": (
            "python3 fresh_pair_then_twist_17141_r18.py --write"
        ),
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "outboxWrites": 0,
        },
    }
    summary = {
        "schemaVersion": "fresh-pair-then-signed-scalar-twist-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "source": {"label": SOURCE_PAIR[0], "r": SOURCE_PAIR[1]},
        "target": {
            "label": TARGET_PAIR[0],
            "r": TARGET_PAIR[1],
            "teamCount": 0,
        },
        "candidateCoefficientSha256": candidate["coefficientSha256"],
        "candidatePolynomialDiscriminantAbs": candidate[
            "polynomialDiscriminantAbs"
        ],
        "ramificationPrime": candidate["ramificationPrime"],
        "candidateArtifact": candidate_artifact,
        "certificate": {
            "path": str(CERTIFICATE.relative_to(ROOT)),
        },
        "submissionCalls": 0,
    }
    return certificate, summary, candidate_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write new sealed candidate/certificate/summary artifacts",
    )
    args = parser.parse_args()
    if args.write and any(
        path.exists() for path in (CANDIDATES, CERTIFICATE, SUMMARY)
    ):
        raise FileExistsError("refusing to overwrite pair-then-twist artifacts")
    certificate, summary, candidate_payload = build()
    if args.write:
        certificate_payload = (
            json.dumps(certificate, indent=2, sort_keys=True) + "\n"
        ).encode()
        write_new(CANDIDATES, candidate_payload)
        write_new(CERTIFICATE, certificate_payload)
        summary["certificate"]["sha256"] = sha256_bytes(certificate_payload)
        summary_payload = (
            json.dumps(summary, indent=2, sort_keys=True) + "\n"
        ).encode()
        write_new(SUMMARY, summary_payload)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

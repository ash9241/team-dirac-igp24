#!/usr/bin/env python3
"""Seal fresh exact pair-then-twist candidates for the current tc0/tc1 boundary.

The inputs are validated cached ``pair_sum_single_v1`` factors.  Each is an
even primitive monic irreducible degree-24 polynomial with one exact twist
block system.  A fresh negative quadratic twist is constructed with pure
integer arithmetic.  This script does not claim twist/pair commutation.

No Sage, GAP, network, submission, or ledger mutation is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import fresh_pair_then_twist_17141_r18 as prior
import fresh_t00134_twist_route_audit as twist_audit
import fresh_t00134_twist_stage_top10000 as twist_stage
from stage_v14_negative_twist import exact_real_root_count


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
CANDIDATES = (
    DATA / "fresh_pair_then_twist_lowtc_followup_candidates_20260730.jsonl"
)
CERTIFICATE = (
    DATA / "fresh_pair_then_twist_lowtc_followup_certificate_20260730.json"
)
SUMMARY = DATA / "fresh_pair_then_twist_lowtc_followup_summary_20260730.json"
MANIFEST = ROOT / "outbox/fresh_pair_then_twist_lowtc_followup_20260730.txt"

ROUTES = (
    {
        "sourceHash": (
            "9cb0ef69876a76a6157d4414b018a875e1129d50d7a68cc18292ac2bafd2da53"
        ),
        "sourcePair": ("24T12302", 8),
        "targetPair": ("24T12302", 16),
    },
    {
        "sourceHash": (
            "b760cbcd9c5625ca1ae6b1283203c32834dfe3230fa6515d8f362159d9a330ef"
        ),
        "sourcePair": ("24T12302", 4),
        "targetPair": ("24T12302", 20),
    },
    {
        "sourceHash": (
            "fe2387e90416e41eaa5e3fdd4a0b7ff75946eb48f3a185803177e95f2ec46dc8"
        ),
        "sourcePair": ("24T9763", 0),
        "targetPair": ("24T9763", 24),
    },
)


def factor_row(
    single_rows: list[dict], source_hash: str, source_pair: tuple[str, int]
) -> tuple[dict, list[int], int]:
    matches = [
        row
        for row in single_rows
        if str(row.get("coefficientSha256")) == source_hash
        and (row.get("proof") or {}).get("schema") == "pair_sum_single_v1"
    ]
    if not matches:
        raise ValueError(f"validated pair factor disappeared: {source_hash}")
    claims = {
        (
            str(row["coefficientLine"]),
            str(row["targetLabel"]),
            int(row["targetR"]),
            prior.canonical_digest(row.get("sourcePins") or []),
        )
        for row in matches
    }
    if len(claims) != 1:
        raise ValueError(f"conflicting exact claims for {source_hash}")
    row = matches[0]
    if (
        (str(row["targetLabel"]), int(row["targetR"])) != source_pair
        or hashlib.sha256(
            str(row["coefficientLine"]).encode("ascii")
        ).hexdigest()
        != source_hash
        or len(row.get("sourcePins") or []) != 1
    ):
        raise ValueError(f"pair factor provenance changed: {source_hash}")
    values = [int(value) for value in str(row["coefficientLine"]).split(",")]
    if not prior.primitive_monic_even(values):
        raise ValueError(f"pair factor is not primitive monic even: {source_hash}")
    computed_disc = twist_stage.exact_even_polynomial_discriminant(values)
    claimed_disc = row.get("polynomialDiscriminantAbs")
    if claimed_disc is None or int(claimed_disc) != computed_disc:
        raise ValueError(f"pair-factor discriminant mismatch: {source_hash}")
    return row, values, computed_disc


def action_for(label: str, actions: dict[str, dict]) -> dict:
    action = actions.get(label)
    systems = (action or {}).get("systems") or []
    if (
        action is None
        or int(action.get("sourceT", -1)) != int(label[3:])
        or int(action.get("systemCount", -1)) != 1
        or len(systems) != 1
        or twist_audit.unanimous_target(action) != label
        or systems[0].get("flipInSource") is not True
        or str(systems[0].get("targetLabel")) != label
        or int(systems[0].get("targetT", -1)) != int(label[3:])
    ):
        raise ValueError(f"label lacks one exact same-label twist action: {label}")
    return action


def build() -> tuple[bytes, dict, dict, bytes]:
    if twist_audit.sha256_path(ACTION_MAP) != prior.EXPECTED_ACTION_MAP_SHA256:
        raise ValueError("pinned exact twist action map changed")
    actions = {
        str(row["sourceLabel"]): row
        for row in twist_audit.read_jsonl(ACTION_MAP)
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exact_pool, single_rows, exact_meta, pair_index = raid.exact_corpus(
            connection
        )
        tested_sources, tested_meta = twist_audit.tested_twist_sources(
            single_rows
        )
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
        named_audits, named_pairs = outbox_audit.validate_named_receipts(
            receipt_snapshot, merged_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(merged_index, connection)
        )
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        saved_pairs = {
            pair for pairs in pair_index.values() for pair in pairs
        }
        historical_hashes, historical_meta = prior.historical_candidate_hashes()
        forbidden = (
            ledger_hashes
            | set(exact_pool)
            | set(pair_index)
            | receipt_snapshot["receiptHashes"]
            | outbox_hashes
            | historical_hashes
        )

        candidate_rows = []
        route_audits = []
        selected_hashes: set[str] = set()
        for spec in ROUTES:
            source_hash = str(spec["sourceHash"])
            source_pair = tuple(spec["sourcePair"])
            target_pair = tuple(spec["targetPair"])
            if source_hash in tested_sources:
                raise ValueError(f"pair factor was already twist-tested: {source_hash}")
            row, values, source_disc = factor_row(
                single_rows, source_hash, source_pair
            )
            action = action_for(source_pair[0], actions)
            quotient_roots, sturm_length = exact_real_root_count(values[::2])
            realized_r = 2 * quotient_roots - int(source_pair[1])
            if realized_r != int(target_pair[1]):
                raise ValueError(
                    f"negative signature changed for {source_hash}: r={realized_r}"
                )

            target = snapshot["targets"].get(target_pair)
            pair_exclusions = {
                "baseline": target_pair in snapshot["baseline"],
                "acceptedOwned": target_pair in snapshot["owned"],
                "knownVerification": target_pair in snapshot["knownPairs"],
                "receiptReserved": target_pair
                in (receipt_snapshot["receiptPairs"] | named_pairs),
                "outboxReserved": target_pair in outbox_pairs,
                "savedExactCandidatePair": target_pair in saved_pairs,
            }
            if (
                target is None
                or int(target["teamCount"]) not in (0, 1)
                or any(pair_exclusions.values())
            ):
                raise ValueError(
                    f"target pair is no longer fresh low-tc: "
                    f"{target_pair} {pair_exclusions}"
                )

            candidate = prior.candidate_for_prime(
                values, source_disc, forbidden | selected_hashes
            )
            digest = str(candidate["coefficientSha256"])
            hash_exclusions = {
                "ledger": digest in ledger_hashes,
                "validatedExactCorpus": digest in exact_pool,
                "anyExactPairIndex": digest in pair_index,
                "receipt": digest in receipt_snapshot["receiptHashes"],
                "outbox": digest in outbox_hashes,
                "historicalCandidate": digest in historical_hashes,
                "selectedDuplicate": digest in selected_hashes,
            }
            if any(hash_exclusions.values()):
                raise ValueError(f"new candidate hash excluded: {hash_exclusions}")
            selected_hashes.add(digest)
            candidate_rows.append(
                {
                    "schemaVersion": (
                        "fresh-pair-then-signed-scalar-twist-candidate-v1"
                    ),
                    "status": (
                        "certified_pair_then_signed_scalar_twist_exact_staged"
                    ),
                    "coefficientLine": candidate["coefficientLine"],
                    "coefficientSha256": digest,
                    "coefficientBytes": int(candidate["coefficientBytes"]),
                    "polynomialDiscriminantAbs": candidate[
                        "polynomialDiscriminantAbs"
                    ],
                    "sourceCoefficientSha256": source_hash,
                    "sourcePair": {
                        "label": source_pair[0],
                        "r": int(source_pair[1]),
                    },
                    "targetLabel": target_pair[0],
                    "targetR": int(target_pair[1]),
                    "targetT": int(target_pair[0][3:]),
                    "targetTeamCountAtSeal": int(target["teamCount"]),
                    "projectedNewTeamPointsExact": str(
                        Fraction(1, 2 ** int(target["teamCount"]))
                    ),
                    "twistSign": "negative",
                    "twistD": int(candidate["twistD"]),
                    "ramificationPrime": int(candidate["ramificationPrime"]),
                    "sourceSquarefreeModuloRamificationPrime": True,
                    "proof": {
                        "kind": (
                            "generic_negative_quadratic_twist_of_exact_pair_factor"
                        ),
                        "pairFactorArtifact": str(
                            (row.get("proof") or {}).get("artifact")
                        ),
                        "pairFactorArtifactRecord": int(
                            (row.get("proof") or {}).get("record", -1)
                        ),
                        "pairFactorSchema": "pair_sum_single_v1",
                        "acceptedSourcePins": row.get("sourcePins") or [],
                        "actionSystemCount": 1,
                        "flipInSource": True,
                        "signatureMethod": (
                            "exact-rational-Sturm-on-even-quotient"
                        ),
                    },
                }
            )
            route_audits.append(
                {
                    "sourceCoefficientSha256": source_hash,
                    "sourcePair": f"{source_pair[0]}/r{source_pair[1]}",
                    "sourcePolynomialDiscriminantAbs": str(source_disc),
                    "sourceClaimedComputedDiscriminantMatch": True,
                    "sourcePreviouslyTwistTested": False,
                    "actionRowSha256": prior.canonical_digest(action),
                    "actionSystemCount": 1,
                    "quotientRealRootCount": quotient_roots,
                    "sturmSequenceLength": sturm_length,
                    "targetPair": f"{target_pair[0]}/r{target_pair[1]}",
                    "targetTeamCount": int(target["teamCount"]),
                    "targetDiscovered": bool(target["discovered"]),
                    "pairExclusions": pair_exclusions,
                    "candidateCoefficientSha256": digest,
                    "candidatePolynomialDiscriminantAbs": candidate[
                        "polynomialDiscriminantAbs"
                    ],
                    "ramificationPrime": int(candidate["ramificationPrime"]),
                    "hashExclusions": hash_exclusions,
                }
            )
    finally:
        connection.close()

    candidate_payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in candidate_rows
    ).encode()
    manifest_payload = "".join(
        str(row["coefficientLine"]) + "\n" for row in candidate_rows
    ).encode("ascii")
    candidate_artifact = {
        "path": str(CANDIDATES.relative_to(ROOT)),
        "rows": len(candidate_rows),
        "bytes": len(candidate_payload),
        "sha256": hashlib.sha256(candidate_payload).hexdigest(),
    }
    manifest_artifact = {
        "path": str(MANIFEST.relative_to(ROOT)),
        "polynomials": len(candidate_rows),
        "bytes": len(manifest_payload),
        "sha256": hashlib.sha256(manifest_payload).hexdigest(),
    }
    certificate = {
        "schemaVersion": "fresh-pair-then-twist-lowtc-followup-certificate-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_safe_staged_not_submitted",
        "coefficientMaterialIncluded": False,
        "scope": {
            "targetTeamCounts": [0, 1],
            "certifiedOrder": "pair_then_twist",
            "twistThenPairClaimed": False,
            "priorSingleOrbitWaveRerun": False,
            "priorMultiOrbitWaveRerun": False,
        },
        "routeCount": len(route_audits),
        "routes": route_audits,
        "candidateArtifact": candidate_artifact,
        "manifest": manifest_artifact,
        "actionMap": prior.artifact(ACTION_MAP),
        "exactCorpusSnapshot": exact_meta,
        "testedTwistSourceSnapshot": tested_meta,
        "historicalCandidateSnapshot": historical_meta,
        "receiptOutboxBoundary": {
            "receiptHashes": len(receipt_snapshot["receiptHashes"]),
            "receiptPairs": len(receipt_snapshot["receiptPairs"] | named_pairs),
            "namedReceiptAudits": named_audits,
            "supplementalExactPairArtifacts": supplemental_artifacts,
            "outboxHashesBeforeThisManifest": len(outbox_hashes),
            "outboxPairsBeforeThisManifest": len(outbox_pairs),
            "outboxSnapshot": {
                key: value
                for key, value in outbox_meta.items()
                if key != "artifacts"
            },
        },
        "proof": {
            "genericity": (
                "For each source factor, the chosen prime is squarefree modulo "
                "the factor. Its splitting field is unramified at that prime, "
                "whereas Q(sqrt(-p)) is ramified, proving disjointness."
            ),
            "group": (
                "The single exact cached negation block system has global flip "
                "inside the source group, so the generic twist retains the "
                "same exact transitive label and irreducibility."
            ),
            "signature": (
                "For even P(x)=Q(x^2), negative-twist real roots equal "
                "2*number_of_real_roots(Q)-number_of_real_roots(P)."
            ),
        },
        "reproduceCommand": (
            "python3 fresh_pair_then_twist_lowtc_followup.py --write"
        ),
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "outboxWrites": 1,
        },
    }
    summary = {
        "schemaVersion": "fresh-pair-then-twist-lowtc-followup-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "exactCandidates": len(candidate_rows),
        "targets": [
            {
                "label": row["targetLabel"],
                "r": int(row["targetR"]),
                "teamCount": int(row["targetTeamCountAtSeal"]),
                "coefficientSha256": row["coefficientSha256"],
                "projectedNewTeamPointsExact": row[
                    "projectedNewTeamPointsExact"
                ],
            }
            for row in candidate_rows
        ],
        "candidateArtifact": candidate_artifact,
        "manifest": manifest_artifact,
        "certificate": {"path": str(CERTIFICATE.relative_to(ROOT))},
        "submissionCalls": 0,
    }
    return candidate_payload, certificate, summary, manifest_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    outputs = (CANDIDATES, CERTIFICATE, SUMMARY, MANIFEST)
    if args.write and any(path.exists() for path in outputs):
        raise FileExistsError("refusing to overwrite low-tc follow-up artifacts")
    candidate_payload, certificate, summary, manifest_payload = build()
    if args.write:
        certificate_payload = (
            json.dumps(certificate, indent=2, sort_keys=True) + "\n"
        ).encode()
        prior.write_new(CANDIDATES, candidate_payload)
        prior.write_new(MANIFEST, manifest_payload)
        prior.write_new(CERTIFICATE, certificate_payload)
        summary["certificate"]["sha256"] = hashlib.sha256(
            certificate_payload
        ).hexdigest()
        summary_payload = (
            json.dumps(summary, indent=2, sort_keys=True) + "\n"
        ).encode()
        prior.write_new(SUMMARY, summary_payload)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

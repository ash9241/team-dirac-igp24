#!/usr/bin/env python3
"""Post-submission receipt audit and remaining light twist reintersection.

This worker verifies the 43-row signed-twist receipt against its manifest and
ledger hash set, then reintersects every still-fresh accepted even source with
the complete current target cache.  Baseline, every known verification,
receipt, and text-outbox pairs/hashes are excluded.  Tested twist source
presentations are excluded before exact rational Sturm arithmetic.

It is coefficient-free, read-only apart from its two audit outputs, and has
no network, heavy-arithmetic, staging, or submission path.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import fresh_t00134_twist_route_audit as base
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
SUBMISSION_ID = "sub_59aee45fb1a743a58308305dda394ea1"
RECEIPT = ROOT / f"receipts/{SUBMISSION_ID}.json"
MANIFEST = ROOT / "outbox/fresh_t00134_twist_top10000_43.txt"
EXACT_INDEX = (
    DATA / "fresh_t00134_twist_top10000_exact_index_20260730.jsonl"
)
PREVIOUS_ROUTES = DATA / "fresh_t00134_twist_light_routes_20260730.jsonl"

AUDIT = (
    DATA / "fresh_t00134_twist_postsubmission_light_audit_20260730.json"
)
ROUTES = (
    DATA / "fresh_t00134_twist_postsubmission_light_routes_20260730.jsonl"
)
EXPECTED_MANIFEST_SHA256 = (
    "ec6f85c5bbab47881896702aeb4b0e06fd6eeb4dee51828e7d52ec538b641440"
)
EXPECTED_EXACT_INDEX_SHA256 = (
    "2e4484a878f13e21dfc63e4f46937d2ef12156f818797d064baea6be31e06fd4"
)


def fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def verify_receipt(connection: sqlite3.Connection) -> dict:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    response = receipt.get("response") or {}
    if (
        str(response.get("submissionId")) != SUBMISSION_ID
        or int(receipt.get("polynomials", -1)) != 43
        or int(response.get("queuedCount", -1)) != 43
        or int(response.get("rejectedCount", -1)) != 0
        or response.get("failedPolynomials") != []
        or str(receipt.get("manifestHash")) != EXPECTED_MANIFEST_SHA256
        or base.sha256_path(MANIFEST) != EXPECTED_MANIFEST_SHA256
        or Path(str(receipt.get("manifest"))).resolve()
        != MANIFEST.resolve()
    ):
        raise ValueError("signed-twist receipt/manifest identity failed")
    lines = [
        exact_single.canonical_polynomial_line(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 43 or any(line is None for line in lines):
        raise ValueError("receipt manifest is not 43 canonical polynomials")
    manifest_hashes = {
        hashlib.sha256(line.encode("ascii")).hexdigest() for line in lines
    }
    ledger_hashes = {
        str(row[0])
        for row in connection.execute(
            "SELECT coefficient_hash FROM polynomials WHERE submission_id=?",
            (SUBMISSION_ID,),
        )
    }
    verification = connection.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END),
               SUM(CASE WHEN scoreable=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN label IS NOT NULL AND r IS NOT NULL THEN 1 ELSE 0 END)
        FROM verifications WHERE submission_id=?
        """,
        (SUBMISSION_ID,),
    ).fetchone()
    if manifest_hashes != ledger_hashes or len(ledger_hashes) != 43:
        raise ValueError("receipt manifest and ledger hash sets disagree")
    return {
        "submissionId": SUBMISSION_ID,
        "batchId": str(response["batchId"]),
        "submissionStatus": str(response["submissionStatus"]),
        "queuedCount": int(response["queuedCount"]),
        "rejectedCount": int(response["rejectedCount"]),
        "failedPolynomials": len(response["failedPolynomials"]),
        "receiptSha256": base.sha256_path(RECEIPT),
        "manifestPath": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256Recorded": str(receipt["manifestHash"]),
        "manifestSha256Actual": base.sha256_path(MANIFEST),
        "manifestBytesRecorded": int(receipt["bytes"]),
        "manifestBytesActual": MANIFEST.stat().st_size,
        "manifestRows": len(lines),
        "ledgerPolynomialHashes": len(ledger_hashes),
        "manifestLedgerHashSetsEqual": True,
        "ledgerVerificationRows": int(verification[0] or 0),
        "ledgerAcceptedRows": int(verification[1] or 0),
        "ledgerScoreableRows": int(verification[2] or 0),
        "ledgerRowsWithResolvedPair": int(verification[3] or 0),
    }


def current_target_boundary(
    connection: sqlite3.Connection,
    snapshot: dict,
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
) -> dict:
    rows = []
    for row in connection.execute(
        """
        SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
        FROM targets ORDER BY t,r
        """
    ):
        value = dict(row)
        rows.append(
            {
                "label": str(value["label"]),
                "t": int(value["t"]),
                "r": int(value["r"]),
                "kTeams": int(value["team_count"]),
                "points": 0.0,
                "scoringDiscAbs": value["minimum_disc_abs"],
                "minScoringDiscAbs": value["minimum_disc_abs"],
                "discovered": bool(value["discovered"]),
                "generatedAt": str(value["generated_at"]),
            }
        )
    if len(rows) != 165_836 or len({row["label"] for row in rows}) != 25_000:
        raise ValueError("current target cache is incomplete")
    pairs = {(str(row["label"]), int(row["r"])) for row in rows}
    by_pair = {
        (str(row["label"]), int(row["r"])): row for row in rows
    }
    excluded = (
        snapshot["baseline"]
        | snapshot["owned"]
        | snapshot["knownPairs"]
        | receipt_pairs
        | outbox_pairs
    )
    eligible = pairs - excluded
    return {
        "pairs": pairs,
        "byPair": by_pair,
        "eligiblePairs": eligible,
        "summary": {
            "targetRows": len(rows),
            "targetLabels": len({row["label"] for row in rows}),
            "rawTeamCountDistribution": dict(
                sorted(Counter(int(row["kTeams"]) for row in rows).items())
            ),
            "eligiblePairs": len(eligible),
            "eligibleLabels": len(
                {label for label, _r in eligible}
            ),
            "eligibleTeamCountDistribution": dict(
                sorted(
                    Counter(
                        int(by_pair[pair]["kTeams"]) for pair in eligible
                    ).items()
                )
            ),
            "eligiblePairSetSha256": base.canonical_digest(
                [[label, r] for label, r in sorted(eligible)]
            ),
            "generatedAtRange": [
                min(row["generatedAt"] for row in rows),
                max(row["generatedAt"] for row in rows),
            ],
        },
    }


def lane_summary(
    routes: list[dict], sign: str | None = None
) -> dict:
    filtered = [
        row for row in routes if sign is None or row["sign"] == sign
    ]
    by_pair = {}
    for row in filtered:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        by_pair.setdefault(pair, row)
    score = sum(
        (
            Fraction(
                1,
                2
                ** int(
                    row["boundaries"]["currentAll"][
                        "placement"
                    ]["kTeams"]
                ),
            )
            for row in by_pair.values()
        ),
        Fraction(),
    )
    return {
        "routeOccurrences": len(filtered),
        "distinctPairs": len(by_pair),
        "teamCountDistributionByDistinctPair": dict(
            sorted(
                Counter(
                    int(
                        row["boundaries"]["currentAll"][
                            "placement"
                        ]["kTeams"]
                    )
                    for row in by_pair.values()
                ).items()
            )
        ),
        "marginalBaseTotalExact": fraction_text(score),
        "marginalBaseTotal": float(score),
        "exceedsPointOne": score > Fraction(1, 10),
    }


def main() -> int:
    started = time.monotonic()
    if AUDIT.exists() or ROUTES.exists():
        raise FileExistsError("refusing to overwrite post-submission audit")
    if base.sha256_path(EXACT_INDEX) != EXPECTED_EXACT_INDEX_SHA256:
        raise ValueError("staged exact index changed")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        receipt_identity = verify_receipt(connection)
        exact_started = time.monotonic()
        exact_pool, single_rows, exact_meta, pair_index = raid.exact_corpus(
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
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        exact_runtime = time.monotonic() - exact_started
        submitted_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials "
                "WHERE submission_id=?",
                (SUBMISSION_ID,),
            )
        }
        if (
            not submitted_hashes <= receipt_hashes
            or len(submitted_hashes) != 43
        ):
            raise ValueError("new receipt hashes are absent from exclusions")
        submitted_pairs = {
            tuple(exact_pool[digest]["pair"])
            for digest in submitted_hashes
            if digest in exact_pool
        }
        if (
            len(submitted_pairs) != 43
            or not submitted_pairs <= receipt_pairs
            or not submitted_pairs <= outbox_pairs
        ):
            raise ValueError("new receipt/outbox pairs are not exactly indexed")

        tested_sources, tested_meta = base.tested_twist_sources(single_rows)
        boundary = current_target_boundary(
            connection, snapshot, receipt_pairs, outbox_pairs
        )
        actions = {
            str(row["sourceLabel"]): row
            for row in base.read_jsonl(base.ACTION_MAP)
        }
        eligible_labels = {
            label for label, _r in boundary["eligiblePairs"]
        }
        relevant_source_labels = {
            label
            for label, action in actions.items()
            if base.unanimous_target(action) in eligible_labels
        }
        sources, source_meta = base.accepted_even_sources(
            connection, relevant_source_labels, tested_sources
        )
        routes, route_meta = base.even_twist_routes(
            sources,
            actions,
            {"currentAll": boundary},
            snapshot,
            receipt_pairs,
            outbox_pairs,
        )
        final_routes = [
            row
            for row in routes
            if row["boundaries"]["currentAll"][
                "survivesAllPairExclusions"
            ]
        ]

        cached_twist_hits = []
        cached_twist_tested_source_veto = 0
        excluded_hashes = ledger_hashes | receipt_hashes | outbox_hashes
        for digest, candidate in exact_pool.items():
            if "generic_quadratic_twist_exact_v1" not in set(
                candidate.get("families") or []
            ):
                continue
            pair = tuple(candidate["pair"])
            if pair not in boundary["eligiblePairs"] or digest in excluded_hashes:
                continue
            source_hashes = {
                str(
                    connection.execute(
                        "SELECT coefficient_hash FROM polynomials "
                        "WHERE submission_id=? AND polynomial_index=?",
                        (str(key[0]), int(key[1])),
                    ).fetchone()[0]
                )
                for key in candidate.get("sourceKeys") or []
            }
            if source_hashes & tested_sources:
                cached_twist_tested_source_veto += 1
                continue
            cached_twist_hits.append((digest, pair))
    finally:
        connection.close()

    # The earlier T00134 route rows must now all be reservation-vetoed.
    previous = base.read_jsonl(PREVIOUS_ROUTES)
    previous_final = [
        row
        for row in previous
        if (row.get("boundaries") or {})
        .get("top10000", {})
        .get("survivesAllPairExclusions")
    ]
    remaining_t00134_pairs = {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in previous_final
        if (
            str(row["targetLabel"]),
            int(row["targetR"]),
        )
        not in receipt_pairs | outbox_pairs
    }
    if len(previous_final) != 104 or remaining_t00134_pairs:
        raise ValueError("submitted T00134 twist frontier was not fully reserved")

    representative = {}
    alternative_counts = Counter()
    signs_by_pair = defaultdict(set)
    for row in final_routes:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        alternative_counts[pair] += 1
        signs_by_pair[pair].add(str(row["sign"]))
        key = (
            int(row["sourceCoefficientBytes"]),
            int(row["sourceFieldDiscriminantAbs"] or 10**1000),
            str(row["sourceCoefficientSha256"]),
        )
        old = representative.get(pair)
        if old is None or key < old[0]:
            representative[pair] = (key, row)
    route_output = []
    for pair, (_key, row) in representative.items():
        team_count = int(
            row["boundaries"]["currentAll"]["placement"]["kTeams"]
        )
        route_output.append(
            {
                **row,
                "alternativeFreshSourceOccurrences": int(
                    alternative_counts[pair]
                ),
                "reachableSigns": sorted(signs_by_pair[pair]),
                "projectedMarginalBaseExact": fraction_text(
                    Fraction(1, 2**team_count)
                ),
                "projectedMarginalBase": 2.0 ** (-team_count),
                "status": (
                    "exact_fresh_source_route_unconstructed_unreserved"
                ),
            }
        )
    route_output.sort(
        key=lambda row: (
            int(
                row["boundaries"]["currentAll"]["placement"]["kTeams"]
            ),
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
        )
    )

    all_lane = lane_summary(final_routes)
    positive_lane = lane_summary(final_routes, "positive")
    negative_lane = lane_summary(final_routes, "negative")
    audit = {
        "schemaVersion": (
            "fresh-t00134-twist-postsubmission-light-audit-v1"
        ),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "remaining_exact_low_cost_lane_above_point_one"
            if all_lane["exceedsPointOne"]
            else "no_remaining_exact_low_cost_lane_above_point_one"
        ),
        "receipt": receipt_identity,
        "newSubmissionExclusion": {
            "submittedHashes": len(submitted_hashes),
            "exactIndexedPairs": len(submitted_pairs),
            "allHashesInReceiptExclusion": True,
            "allPairsInReceiptExclusion": True,
            "allPairsInOutboxExclusion": True,
        },
        "t00134Top10000": {
            "previousExactRouteOccurrences": len(previous_final),
            "previousDistinctPairs": len(
                {
                    (str(row["targetLabel"]), int(row["targetR"]))
                    for row in previous_final
                }
            ),
            "remainingExactRoutePairsAfterNewReceipt": len(
                remaining_t00134_pairs
            ),
            "remainingMarginalBase": 0.0,
        },
        "currentBoundary": boundary["summary"],
        "freshSourceCensus": {
            **source_meta,
            **route_meta,
        },
        "remainingFreshGenericTwist": {
            "allSignsPairDeduplicated": all_lane,
            "positive": positive_lane,
            "negative": negative_lane,
            "representativeRouteRows": len(route_output),
            "routeOutput": str(ROUTES.relative_to(ROOT)),
            "interpretation": (
                "2^(-current team count) is the exact contest marginal base; "
                "candidate field-discriminant ratios are unknown until a "
                "specific fresh-prime twist is constructed"
            ),
        },
        "remainingCachedExactTwistCandidates": {
            "survivingFreshSourceRows": len(cached_twist_hits),
            "testedSourcePresentationVetoes": (
                cached_twist_tested_source_veto
            ),
        },
        "exclusions": {
            "baselinePairs": len(snapshot["baseline"]),
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "ledgerHashes": len(ledger_hashes),
            "receiptPairs": len(receipt_pairs),
            "receiptHashes": len(receipt_hashes),
            "outboxPairs": len(outbox_pairs),
            "outboxHashes": len(outbox_hashes),
            "testedSources": tested_meta,
            "receiptMeta": receipt_meta,
            "outboxMeta": outbox_meta,
        },
        "exactCorpus": {
            "uniqueCandidateHashes": len(exact_pool),
            "meta": exact_meta,
            "runtimeSeconds": exact_runtime,
        },
        "inputSha256": {
            str(path.relative_to(ROOT)): base.sha256_path(path)
            for path in (
                RECEIPT,
                MANIFEST,
                EXACT_INDEX,
                PREVIOUS_ROUTES,
                base.ACTION_MAP,
                DB,
            )
        },
        "runtimeSeconds": time.monotonic() - started,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    base.exclusive_text(
        ROUTES,
        "".join(base.canonical_json(row) + "\n" for row in route_output),
    )
    base.exclusive_text(
        AUDIT, json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "status": audit["status"],
                "audit": str(AUDIT.relative_to(ROOT)),
                "auditSha256": base.sha256_path(AUDIT),
                "routes": str(ROUTES.relative_to(ROOT)),
                "routesSha256": base.sha256_path(ROUTES),
                "receipt": receipt_identity,
                "remainingT00134Pairs": len(remaining_t00134_pairs),
                "remainingAllCurrent": all_lane,
                "remainingPositive": positive_lane,
                "remainingNegative": negative_lane,
                "runtimeSeconds": audit["runtimeSeconds"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

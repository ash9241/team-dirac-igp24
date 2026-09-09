#!/usr/bin/env python3
"""Reintersect exact local twist/product caches with current tc0/tc1.

The current target cache is treated as a pinned opportunity boundary.  This
worker is read-only except for new coefficient-free ``fresh_t00134_twist_*``
artifacts.  It performs exact rational Sturm arithmetic for fresh accepted
even-source presentations, rejoins cached exact twist/scalar/product
candidates, and applies baseline, every verification, receipt, and current
text-outbox exclusions.  It never constructs, stages, submits, or calls the
network.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
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
AUDIT = DATA / "fresh_t00134_twist_current_tc01_audit_20260730.json"
ROUTES = DATA / "fresh_t00134_twist_current_tc01_routes_20260730.jsonl"
HEAVY_TARGETS = (
    DATA / "fresh_t00134_twist_current_tc01_heavy_targets_20260730.json"
)


def target_rows(connection: sqlite3.Connection) -> list[dict]:
    rows = []
    generated = set()
    for row in connection.execute(
        """
        SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
        FROM targets WHERE team_count IN (0,1) ORDER BY team_count,t,r
        """
    ):
        value = dict(row)
        team_count = int(value["team_count"])
        discovered = bool(value["discovered"])
        if discovered != (team_count == 1):
            raise ValueError(
                "current tc0/tc1 discovery flag disagrees with team count"
            )
        generated.add(str(value["generated_at"]))
        rows.append(
            {
                "label": str(value["label"]),
                "t": int(value["t"]),
                "r": int(value["r"]),
                "kTeams": team_count,
                # These placeholders are never used for the exact marginal
                # score below; they only satisfy the common gate serializer.
                "points": 0.0 if team_count == 0 else 1.0,
                "scoringDiscAbs": value["minimum_disc_abs"],
                "minScoringDiscAbs": value["minimum_disc_abs"],
                "discovered": discovered,
                "generatedAt": str(value["generated_at"]),
            }
        )
    if (
        len(rows) != 28_314
        or Counter(int(row["kTeams"]) for row in rows)
        != Counter({0: 13_475, 1: 14_839})
        or not generated
    ):
        raise ValueError("current tc0/tc1 target boundary changed")
    return rows


def marginal(team_count: int) -> dict:
    value = Fraction(1, 2**team_count)
    return {
        "kind": "exact_contest_marginal_base",
        "formula": "2^(-currentTeamCount)",
        "projectedMarginalScoreExact": (
            str(value.numerator)
            if value.denominator == 1
            else f"{value.numerator}/{value.denominator}"
        ),
        "projectedMarginalScore": float(value),
    }


def source_hashes_for_candidate(
    connection: sqlite3.Connection, candidate: dict
) -> set[str]:
    result = set()
    for key in candidate.get("sourceKeys") or []:
        row = connection.execute(
            """
            SELECT p.coefficient_hash
            FROM polynomials AS p JOIN verifications AS v
              USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
              AND v.label=? AND v.r=?
            """,
            (str(key[0]), int(key[1]), str(key[2]), int(key[3])),
        ).fetchone()
        if row is None:
            raise ValueError(f"exact-corpus source key disappeared: {key}")
        result.add(str(row[0]))
    return result


def cached_family(candidate: dict) -> list[str]:
    markers = sorted(str(value) for value in candidate.get("families") or [])
    text = base.canonical_json(
        {
            "families": markers,
            "proofs": candidate.get("proofs"),
        }
    ).lower()
    result = []
    for family, needles in {
        "quadratic_twist_or_signflip": ("twist", "signflip", "sign_flip"),
        "scalar_or_kummer": ("scalar", "kummer"),
        "cartesian_product_or_compositum": (
            "cartesian",
            "composit",
            "direct_product",
            "product_action",
        ),
    }.items():
        if any(needle in text for needle in needles):
            result.append(family)
    return result


def main() -> int:
    started = time.monotonic()
    if any(path.exists() for path in (AUDIT, ROUTES, HEAVY_TARGETS)):
        raise FileExistsError("refusing to overwrite current tc0/tc1 artifacts")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = target_rows(connection)
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
        tested_sources, tested_meta = base.tested_twist_sources(single_rows)
        boundary = base.placement_boundary(
            rows, snapshot, receipt_pairs, outbox_pairs
        )

        action_rows = base.read_jsonl(base.ACTION_MAP)
        actions = {str(row["sourceLabel"]): row for row in action_rows}
        if len(actions) != len(action_rows):
            raise ValueError("duplicate even-twist action label")
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
        twist_rows, twist_meta = base.even_twist_routes(
            sources,
            actions,
            {"currentTc01": boundary},
            snapshot,
            receipt_pairs,
            outbox_pairs,
        )

        final_twists = []
        for route in twist_rows:
            state = route["boundaries"]["currentTc01"]
            if not state["survivesAllPairExclusions"]:
                continue
            team_count = int(state["placement"]["kTeams"])
            state["score"] = marginal(team_count)
            state.pop("scoreBounds", None)
            final_twists.append(route)

        cached_hits = []
        cached_rejected_tested_source = 0
        cached_rejected_hash = 0
        for digest, candidate in exact_pool.items():
            families = cached_family(candidate)
            if not families:
                continue
            pair = tuple(candidate["pair"])
            if pair not in boundary["eligiblePairs"]:
                continue
            if (
                digest in ledger_hashes
                or digest in receipt_hashes
                or digest in outbox_hashes
            ):
                cached_rejected_hash += 1
                continue
            source_hashes = source_hashes_for_candidate(
                connection, candidate
            )
            if source_hashes & tested_sources:
                cached_rejected_tested_source += 1
                continue
            placement = boundary["byPair"][pair]
            cached_hits.append(
                {
                    "routeFamily": "cached_exact_candidate",
                    "candidateFamilies": families,
                    "coefficientSha256": str(digest),
                    "coefficientBytes": int(candidate["coefficientBytes"]),
                    "fieldDiscriminantAbs": (
                        str(candidate["fieldDiscriminantAbs"])
                        if candidate["fieldDiscriminantAbs"] is not None
                        else None
                    ),
                    "sourceCoefficientSha256": sorted(source_hashes),
                    "targetLabel": pair[0],
                    "targetR": pair[1],
                    "targetTeamCount": int(placement["kTeams"]),
                    "score": marginal(int(placement["kTeams"])),
                    "proofArtifacts": raid.proof_artifacts(
                        candidate["proofs"]
                    ),
                    "status": (
                        "exact_hash_and_pair_novel_current_tc0_tc1"
                    ),
                }
            )
    finally:
        connection.close()

    scalar_rows = base.read_jsonl(base.SCALAR_ACTIONS)
    scalar_summary, scalar_heavy = base.scalar_prefilter(
        scalar_rows, {"currentTc01": boundary}
    )
    compositum = json.loads(
        base.COMPOSITUM_PILOT.read_text(encoding="utf-8")
    )
    pilot_summary = base.compositum_pilot_audit(
        compositum,
        {"currentTc01": boundary},
        snapshot,
        receipt_pairs,
        outbox_pairs,
        ledger_hashes | receipt_hashes | outbox_hashes,
    )

    final_twists.sort(
        key=lambda row: (
            int(
                row["boundaries"]["currentTc01"]["placement"]["kTeams"]
            ),
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
            row["sign"],
            int(row["sourceCoefficientBytes"]),
            row["sourceCoefficientSha256"],
        )
    )
    cached_hits.sort(
        key=lambda row: (
            int(row["targetTeamCount"]),
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
            int(row["coefficientBytes"]),
            row["coefficientSha256"],
        )
    )
    output_rows = [
        {"kind": "fresh_even_twist_route", **row} for row in final_twists
    ] + [{"kind": "cached_exact_route", **row} for row in cached_hits]

    twist_pairs = {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in final_twists
    }
    cached_pairs = {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in cached_hits
    }
    best_pairs = twist_pairs | cached_pairs
    by_team_count = Counter(
        int(boundary["byPair"][pair]["kTeams"]) for pair in best_pairs
    )
    projected = sum(
        (
            Fraction(
                1,
                2 ** int(boundary["byPair"][pair]["kTeams"]),
            )
            for pair in best_pairs
        ),
        Fraction(),
    )
    projected_text = (
        str(projected.numerator)
        if projected.denominator == 1
        else f"{projected.numerator}/{projected.denominator}"
    )

    heavy = {
        "schemaVersion": "fresh-t00134-current-tc01-heavy-targets-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "eligiblePairs": [
            {
                "label": label,
                "r": r,
                "teamCount": int(boundary["byPair"][(label, r)]["kTeams"]),
                "minimumDiscAbs": boundary["byPair"][(label, r)][
                    "minScoringDiscAbs"
                ],
                "score": marginal(
                    int(boundary["byPair"][(label, r)]["kTeams"])
                ),
            }
            for label, r in sorted(boundary["eligiblePairs"])
        ],
        "scalarActions": scalar_heavy,
        "productFactorDegreeFamilies": [[3, 8], [4, 6]],
        "candidateHashExclusionSha256": base.canonical_digest(
            sorted(ledger_hashes | receipt_hashes | outbox_hashes)
        ),
        "coefficientMaterialIncluded": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }

    audit = {
        "schemaVersion": "fresh-t00134-current-tc01-twist-audit-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "exact_novel_current_tc0_tc1_routes"
            if output_rows
            else "zero_exact_novel_current_tc0_tc1_routes_before_heavy"
        ),
        "targetBoundary": {
            **boundary["summary"],
            "rawTeamCountDistribution": dict(
                sorted(Counter(int(row["kTeams"]) for row in rows).items())
            ),
            "generatedAtDistinct": len(
                {str(row["generatedAt"]) for row in rows}
            ),
            "generatedAtRange": [
                min(str(row["generatedAt"]) for row in rows),
                max(str(row["generatedAt"]) for row in rows),
            ],
            "finalEligibleTeamCountDistribution": dict(
                sorted(
                    Counter(
                        int(boundary["byPair"][pair]["kTeams"])
                        for pair in boundary["eligiblePairs"]
                    ).items()
                )
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
            "testedTwistSources": tested_meta,
            "receiptAudit": receipt_meta,
            "outboxAudit": outbox_meta,
        },
        "exactCorpus": {
            "uniqueCandidateHashes": len(exact_pool),
            "runtimeSeconds": exact_runtime,
            "meta": exact_meta,
        },
        "freshEvenTwist": {
            **source_meta,
            **twist_meta,
            "finalRouteOccurrences": len(final_twists),
            "finalDistinctPairs": len(twist_pairs),
            "teamCountDistributionByOccurrence": dict(
                sorted(
                    Counter(
                        int(
                            row["boundaries"]["currentTc01"][
                                "placement"
                            ]["kTeams"]
                        )
                        for row in final_twists
                    ).items()
                )
            ),
            "signDistribution": dict(
                sorted(Counter(row["sign"] for row in final_twists).items())
            ),
        },
        "cachedExactTwistScalarProduct": {
            "finalCandidateRows": len(cached_hits),
            "finalDistinctPairs": len(cached_pairs),
            "rejectedTestedSourcePresentations": (
                cached_rejected_tested_source
            ),
            "rejectedLedgerReceiptOutboxHashes": cached_rejected_hash,
            "familyDistribution": dict(
                sorted(
                    Counter(
                        family
                        for row in cached_hits
                        for family in row["candidateFamilies"]
                    ).items()
                )
            ),
        },
        "scalarPrefilter": scalar_summary["currentTc01"],
        "simpleCompositumPilot": pilot_summary["currentTc01"],
        "combined": {
            "routeRows": len(output_rows),
            "distinctPairs": len(best_pairs),
            "teamCountDistributionByDistinctPair": dict(
                sorted(by_team_count.items())
            ),
            "projectedMarginalScoreExactWithoutPairDoubleCount": (
                projected_text
            ),
        },
        "outputs": {
            "routes": str(ROUTES.relative_to(ROOT)),
            "heavyTargets": str(HEAVY_TARGETS.relative_to(ROOT)),
        },
        "inputSha256": {
            str(path.relative_to(ROOT)): base.sha256_path(path)
            for path in (
                DB,
                base.ACTION_MAP,
                base.SCALAR_ACTIONS,
                base.COMPOSITUM_PILOT,
                base.RECOVERED_BANK,
            )
        },
        "runtimeSeconds": time.monotonic() - started,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "ledgerWrites": 0,
        "outboxWrites": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }

    base.exclusive_text(
        ROUTES,
        "".join(base.canonical_json(row) + "\n" for row in output_rows),
    )
    base.exclusive_text(
        HEAVY_TARGETS,
        json.dumps(heavy, indent=2, sort_keys=True) + "\n",
    )
    base.exclusive_text(
        AUDIT, json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "audit": str(AUDIT.relative_to(ROOT)),
                "auditSha256": base.sha256_path(AUDIT),
                "routes": str(ROUTES.relative_to(ROOT)),
                "routesSha256": base.sha256_path(ROUTES),
                "freshTwistOccurrences": len(final_twists),
                "cachedExactCandidates": len(cached_hits),
                "distinctPairs": len(best_pairs),
                "teamCountDistribution": dict(
                    sorted(by_team_count.items())
                ),
                "projectedMarginalScoreExact": projected_text,
                "scalarActionsForHeavyAlignment": len(scalar_heavy),
                "runtimeSeconds": audit["runtimeSeconds"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

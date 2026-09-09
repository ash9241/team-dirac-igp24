#!/usr/bin/env python3
"""Stage the exact current-team-count <=7 fresh twist frontier.

The post-submission light audit found 53 distinct exact structural pairs at
current team counts 4--7.  This worker:

* reconstructs every eligible fresh accepted even-source alternative for
  those pairs;
* chooses the smallest admissible odd ramification prime per source;
* chooses the candidate with the minimum exact polynomial discriminant per
  target pair;
* rechecks all ledger, receipt, and text-outbox hash/pair exclusions; and
* stages a 53-row manifest plus a coefficient-free certificate.

The exact polynomial discriminant is used for deterministic selection because
field discriminants require heavy PARI arithmetic, which is expressly outside
this worker.  Exact labels and signatures do not require that arithmetic:
they follow from the accepted source pin, unanimous cached all-block-system
action, exact rational Sturm count, and fresh-prime ramification
disjointness.  No network or submission operation exists here.
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
import fresh_t00134_twist_stage_top10000 as prior_stage
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single
from stage_v14_negative_twist import exact_real_root_count


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ROUTE_AUDIT = (
    DATA / "fresh_t00134_twist_postsubmission_light_audit_20260730.json"
)
ROUTE_ROWS = (
    DATA / "fresh_t00134_twist_postsubmission_light_routes_20260730.jsonl"
)

CANDIDATES = (
    DATA / "fresh_t00134_twist_current_tc_le7_candidates_20260730.jsonl"
)
EXACT_INDEX = (
    DATA / "fresh_t00134_twist_current_tc_le7_exact_index_20260730.jsonl"
)
CERTIFICATE = (
    DATA / "fresh_t00134_twist_current_tc_le7_stage_certificate_20260730.json"
)
MANIFEST = ROOT / "outbox/fresh_t00134_twist_current_tc_le7_53.txt"

EXPECTED_AUDIT_SHA256 = (
    "dfec505295fd9b9d4367b97760f4371377ae8616e26d1d3e10b2b6af16defa33"
)
EXPECTED_ROUTES_SHA256 = (
    "bbeea80ff7ea5a7e2803bf6340a966ae6d0904bb8c091ac514885b22058fd1fa"
)


def tested_sources_excluding_audit_routes(
    single_rows: list[dict],
) -> tuple[set[str], dict]:
    """Retain real prior twist tests but not coefficient-free route audits."""

    hashes = set()
    files = []
    paths = set(DATA.glob("*twist*.jsonl"))
    paths.update(DATA.glob("*signflip*/**/results*.jsonl"))
    for path in sorted(paths):
        if (
            not path.is_file()
            or path.resolve() in {CANDIDATES.resolve(), EXACT_INDEX.resolve()}
            or (
                path.name.startswith("fresh_t00134_twist_")
                and "routes" in path.name
            )
        ):
            continue
        before = len(hashes)
        rows = base.read_jsonl(path)
        for row in rows:
            for key, value in base.walk_values(row):
                if key == "sourceCoefficientSha256" and value:
                    hashes.add(str(value))
        files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "rows": len(rows),
                "newSourceHashes": len(hashes) - before,
                "sha256": base.sha256_path(path),
            }
        )
    exact_pins = set()
    for row in single_rows:
        marker = base.canonical_json(
            {
                "family": row.get("family"),
                "families": row.get("families"),
                "proof": row.get("proof"),
                "schema": row.get("schema"),
            }
        ).lower()
        if "twist" not in marker and "signflip" not in marker:
            continue
        for pin in row.get("sourcePins") or []:
            digest = pin.get("coefficientSha256")
            if digest:
                exact_pins.add(str(digest))
    hashes.update(exact_pins)
    return hashes, {
        "definition": (
            "prior twist/signflip source presentations and validated exact "
            "twist source pins; coefficient-free fresh_t00134 route-audit "
            "files are not treated as arithmetic attempts"
        ),
        "files": files,
        "exactCorpusSourcePins": len(exact_pins),
        "distinctSourceHashes": len(hashes),
        "sourceHashSetSha256": base.canonical_digest(sorted(hashes)),
    }


def exact_index_row(candidate: dict, line: str, action: dict) -> dict:
    systems = action.get("systems") or []
    return {
        "status": "certified_generic_quadratic_twist_staged",
        "coefficientLine": line,
        "coefficientSha256": str(candidate["coefficientSha256"]),
        "coefficientBytes": int(candidate["coefficientBytes"]),
        "polynomialDiscriminantAbs": str(
            candidate["polynomialDiscriminantAbs"]
        ),
        "fieldDiscriminantAbs": None,
        "sourceSubmissionId": str(candidate["sourceSubmissionId"]),
        "sourcePolynomialIndex": int(candidate["sourcePolynomialIndex"]),
        "sourceCoefficientSha256": str(
            candidate["sourceCoefficientSha256"]
        ),
        "sourceLabel": str(candidate["sourceLabel"]),
        "sourceR": int(candidate["sourceR"]),
        "sourceFieldDiscAbs": str(
            candidate["sourceFieldDiscriminantAbs"]
        ),
        "targetLabel": str(candidate["targetLabel"]),
        "targetT": int(str(candidate["targetLabel"])[3:]),
        "targetR": int(candidate["targetR"]),
        "twistSign": str(candidate["sign"]),
        "twistD": int(candidate["twistD"]),
        "ramificationPrime": int(candidate["ramificationPrime"]),
        "twistDirectRealRootCount": int(candidate["targetR"]),
        "twistIrreducible": True,
        "sourceSquarefreeModRamificationPrime": True,
        "actionResolutionMethod": "all-block-systems-same-target",
        "actionSystemCount": len(systems),
        "allBlockSystemsTargetLabels": [
            str(system["targetLabel"]) for system in systems
        ],
        "genericActionProof": (
            "The accepted source is squarefree modulo the displayed odd "
            "prime, so its splitting field is unramified there; the "
            "quadratic twist field is ramified and linearly disjoint. Every "
            "exact 12x2 block system gives the same displayed transitive "
            "global-flip target label."
        ),
        "networkCalls": 0,
        "submissionCalls": 0,
    }


def main() -> int:
    started = time.monotonic()
    outputs = (CANDIDATES, EXACT_INDEX, CERTIFICATE, MANIFEST)
    if any(path.exists() for path in outputs):
        raise FileExistsError("refusing to overwrite current tc<=7 stage")
    if base.sha256_path(ROUTE_AUDIT) != EXPECTED_AUDIT_SHA256:
        raise ValueError("post-submission route audit hash changed")
    if base.sha256_path(ROUTE_ROWS) != EXPECTED_ROUTES_SHA256:
        raise ValueError("post-submission route rows hash changed")

    representative_rows = base.read_jsonl(ROUTE_ROWS)
    target_pairs = {
        (str(row["targetLabel"]), int(row["targetR"]))
        for row in representative_rows
        if int(
            row["boundaries"]["currentAll"]["placement"]["kTeams"]
        )
        <= 7
    }
    if len(target_pairs) != 53:
        raise ValueError(f"expected 53 tc<=7 targets, got {len(target_pairs)}")

    actions = {
        str(row["sourceLabel"]): row
        for row in base.read_jsonl(base.ACTION_MAP)
    }
    historical_hashes, historical_meta = (
        prior_stage.historical_candidate_hashes()
    )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
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
        pair_exclusions = (
            snapshot["baseline"]
            | snapshot["owned"]
            | snapshot["knownPairs"]
            | receipt_pairs
            | outbox_pairs
        )
        if target_pairs & pair_exclusions:
            raise ValueError("one tc<=7 target is no longer unreserved")

        target_rows = {}
        for pair in sorted(target_pairs):
            row = connection.execute(
                """
                SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                pair,
            ).fetchone()
            if row is None or int(row["team_count"]) > 7:
                raise ValueError(f"target state changed: {pair}")
            target_rows[pair] = {
                "label": str(row["label"]),
                "t": int(row["t"]),
                "r": int(row["r"]),
                "kTeams": int(row["team_count"]),
                "points": 0.0,
                "scoringDiscAbs": row["minimum_disc_abs"],
                "minScoringDiscAbs": row["minimum_disc_abs"],
                "discovered": bool(row["discovered"]),
                "generatedAt": str(row["generated_at"]),
            }
        boundary = {
            "pairs": set(target_pairs),
            "byPair": target_rows,
            "eligiblePairs": set(target_pairs),
        }
        target_labels = {label for label, _r in target_pairs}
        source_labels = {
            label
            for label, action in actions.items()
            if base.unanimous_target(action) in target_labels
        }
        tested_sources, tested_meta = tested_sources_excluding_audit_routes(
            single_rows
        )
        sources, source_meta = base.accepted_even_sources(
            connection, source_labels, tested_sources
        )
        routes, route_meta = base.even_twist_routes(
            sources,
            actions,
            {"currentTcLe7": boundary},
            snapshot,
            receipt_pairs,
            outbox_pairs,
        )
        final_routes = [
            row
            for row in routes
            if row["boundaries"]["currentTcLe7"][
                "survivesAllPairExclusions"
            ]
        ]
        by_pair = defaultdict(list)
        for route in final_routes:
            by_pair[
                (str(route["targetLabel"]), int(route["targetR"]))
            ].append(route)
        if set(by_pair) != target_pairs:
            raise ValueError(
                f"fresh route coverage changed: missing={target_pairs-set(by_pair)}"
            )

        forbidden_hashes = (
            ledger_hashes
            | receipt_hashes
            | outbox_hashes
            | set(exact_pool)
            | historical_hashes
        )
        source_disc_cache = {}
        selected = []
        selected_hashes = set()
        for pair in sorted(
            target_pairs,
            key=lambda value: (
                int(target_rows[value]["kTeams"]),
                int(value[0][3:]),
                int(value[1]),
            ),
        ):
            proposals = []
            for route in by_pair[pair]:
                source, coefficients = prior_stage.source_row(
                    connection, route
                )
                source_hash = str(route["sourceCoefficientSha256"])
                polynomial_disc = source_disc_cache.get(source_hash)
                if polynomial_disc is None:
                    polynomial_disc = (
                        prior_stage.exact_even_polynomial_discriminant(
                            coefficients
                        )
                    )
                    recorded = source.get("poly_disc_abs")
                    if (
                        recorded is not None
                        and int(recorded) != polynomial_disc
                    ):
                        raise ValueError(
                            "source polynomial discriminant mismatch"
                        )
                    source_disc_cache[source_hash] = polynomial_disc
                source["poly_disc_abs"] = str(polynomial_disc)
                quotient_roots, sturm_length = exact_real_root_count(
                    coefficients[::2]
                )
                negative_r = 2 * quotient_roots - int(route["sourceR"])
                expected_r = (
                    int(route["sourceR"])
                    if route["sign"] == "positive"
                    else negative_r
                )
                if expected_r != pair[1]:
                    raise ValueError("route signature changed")
                proposal = prior_stage.first_candidate_for_route(
                    source,
                    coefficients,
                    route,
                    forbidden_hashes | selected_hashes,
                )
                proposals.append(
                    {
                        "_line": proposal.pop("line"),
                        **proposal,
                        "route": route,
                        "source": source,
                        "action": actions[str(route["sourceLabel"])],
                        "quotientRealRootCount": quotient_roots,
                        "negativeTwistRealRootCount": negative_r,
                        "sturmSequenceLength": sturm_length,
                    }
                )
            chosen = min(
                proposals,
                key=lambda row: (
                    int(row["polynomialDiscriminantAbs"]),
                    int(row["coefficientBytes"]),
                    int(row["ramificationPrime"]),
                    str(row["coefficientSha256"]),
                ),
            )
            selected.append((pair, chosen, len(proposals)))
            selected_hashes.add(str(chosen["coefficientSha256"]))
        if len(selected) != 53 or len(selected_hashes) != 53:
            raise ValueError("tc<=7 candidate selection is not one-to-one")

        candidate_pair_index = {
            **pair_index,
            **{
                str(row["coefficientSha256"]): {pair}
                for pair, row, _alternatives in selected
            },
        }
        final_receipt_hashes, final_receipt_pairs, final_receipt_meta = (
            exact_single.receipt_exclusions(
                lane.RECEIPTS, DATA, connection, candidate_pair_index
            )
        )
        final_outbox_hashes, final_outbox_pairs, final_outbox_meta = (
            outbox_audit.outbox_exclusions(
                candidate_pair_index, connection
            )
        )
        if (
            selected_hashes & ledger_hashes
            or selected_hashes & final_receipt_hashes
            or selected_hashes & final_outbox_hashes
            or target_pairs & snapshot["baseline"]
            or target_pairs & snapshot["owned"]
            or target_pairs & snapshot["knownPairs"]
            or target_pairs & final_receipt_pairs
            or target_pairs & final_outbox_pairs
        ):
            raise ValueError("final tc<=7 novelty/reservation gate failed")
    finally:
        connection.close()

    candidate_rows = []
    exact_rows = []
    manifest_lines = []
    for index, (pair, row, alternative_count) in enumerate(selected):
        route = row["route"]
        target = target_rows[pair]
        manifest_lines.append(str(row["_line"]))
        candidate = {
            "status": "certified_exact_safe_staged_not_submitted",
            "manifestIndex": index,
            "coefficientSha256": str(row["coefficientSha256"]),
            "coefficientBytes": int(row["coefficientBytes"]),
            "fieldDiscriminantAbs": None,
            "fieldDiscriminantStatus": (
                "not_computed_no_heavy_arithmetic_authorized"
            ),
            "polynomialDiscriminantAbs": str(
                row["polynomialDiscriminantAbs"]
            ),
            "selectionRule": (
                "minimum exact polynomial discriminant across all fresh "
                "eligible source presentations for this target pair"
            ),
            "eligibleSourceAlternatives": int(alternative_count),
            "sourceSubmissionId": str(route["sourceSubmissionId"]),
            "sourcePolynomialIndex": int(route["sourcePolynomialIndex"]),
            "sourceCoefficientSha256": str(
                route["sourceCoefficientSha256"]
            ),
            "sourceLabel": str(route["sourceLabel"]),
            "sourceR": int(route["sourceR"]),
            "sourceFieldDiscriminantAbs": str(
                route["sourceFieldDiscriminantAbs"]
            ),
            "sign": str(route["sign"]),
            "targetLabel": pair[0],
            "targetR": pair[1],
            "targetCurrentTeamCount": int(target["kTeams"]),
            "targetMinimumDiscAbs": target["minScoringDiscAbs"],
            "projectedMarginalBaseExact": (
                f"1/{2 ** int(target['kTeams'])}"
            ),
            "projectedMarginalBase": 2.0 ** (-int(target["kTeams"])),
            "ramificationPrime": int(row["ramificationPrime"]),
            "twistD": int(row["twistD"]),
            "primeAttempts": int(row["primeAttempts"]),
            "exactProof": {
                "acceptedScoreableSourceHashPinned": True,
                "sourcePrimitiveMonicEvenDegree24": True,
                "allBlockSystemsSameTargetLabel": True,
                "actionSystemCount": int(row["action"]["systemCount"]),
                "targetActionTransitive": True,
                "quotientRealRootCount": int(
                    row["quotientRealRootCount"]
                ),
                "negativeTwistRealRootCount": int(
                    row["negativeTwistRealRootCount"]
                ),
                "sturmSequenceLength": int(row["sturmSequenceLength"]),
                "sourceSquarefreeModuloRamificationPrime": True,
                "freshPrimeRamificationDisjointness": True,
                "exactFieldLabel": True,
                "exactSignature": True,
                "irreducible": True,
                "polynomialDiscriminantRule": (
                    "abs(Disc(candidate))="
                    "abs(Disc(source))*prime^276"
                ),
            },
        }
        candidate_rows.append(candidate)
        exact_row = exact_index_row(candidate, str(row["_line"]), row["action"])
        accepted = exact_single.validate_twist(
            exact_row,
            (),
            EXACT_INDEX,
            index + 1,
            "/",
        )
        if (
            accepted is None
            or str(accepted["coefficientSha256"])
            != str(candidate["coefficientSha256"])
            or (
                str(accepted["targetLabel"]),
                int(accepted["targetR"]),
            )
            != pair
        ):
            raise ValueError("exact generic-twist index validation failed")
        exact_rows.append(exact_row)

    k_distribution = Counter(
        int(row["targetCurrentTeamCount"]) for row in candidate_rows
    )
    if k_distribution != Counter({7: 49, 6: 2, 5: 1, 4: 1}):
        raise ValueError(f"tc<=7 distribution changed: {k_distribution}")
    marginal = sum(
        (
            Fraction(1, 2 ** int(row["targetCurrentTeamCount"]))
            for row in candidate_rows
        ),
        Fraction(),
    )
    if marginal != Fraction(65, 128):
        raise ValueError(f"corrected marginal total changed: {marginal}")

    candidate_payload = "".join(
        base.canonical_json(row) + "\n" for row in candidate_rows
    )
    exact_payload = "".join(
        base.canonical_json(row) + "\n" for row in exact_rows
    )
    manifest_payload = "".join(line + "\n" for line in manifest_lines)
    certificate = {
        "schemaVersion": "fresh-t00134-current-tc-le7-twist-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_53_exact_safe_staged_not_submitted",
        "candidateRows": len(candidate_rows),
        "distinctCandidateHashes": len(selected_hashes),
        "distinctTargetPairs": len(target_pairs),
        "manifestRows": len(manifest_lines),
        "manifestBytes": len(manifest_payload.encode("ascii")),
        "teamCountDistribution": dict(sorted(k_distribution.items())),
        "signDistribution": dict(
            sorted(Counter(row["sign"] for row in candidate_rows).items())
        ),
        "projectedScore": {
            "formula": "sum 2^(-currentTeamCount), one candidate per pair",
            "marginalBaseTotalExact": "65/128",
            "marginalBaseTotal": float(marginal),
            "candidateRatioAdjustedScore": None,
            "reason": (
                "field discriminants were not computed because heavy "
                "arithmetic was forbidden"
            ),
        },
        "selection": {
            "criterion": "minimum exact polynomial discriminant per pair",
            "sourceRouteOccurrencesConsidered": len(final_routes),
            "allCandidatePolynomialDiscriminantsExact": True,
            "fieldDiscriminantsComputed": 0,
        },
        "proofChecks": {
            "allSourcesAcceptedScoreableAndHashPinned": True,
            "allSourcesFreshUnderTestedPresentationBoundary": True,
            "allSourcesPrimitiveMonicEvenDegree24": True,
            "allActionsResolvedByAllBlockSystemsSameTarget": True,
            "allSignaturesExactByRationalSturm": True,
            "allGenericTwistsHaveFreshPrimeRamificationProof": True,
            "allFieldLabelsExact": True,
            "allSignaturesExact": True,
            "allCandidatesIrreducible": True,
            "allCandidateHashesAbsentFromLedgerReceiptsOutboxes": True,
            "allTargetPairsAbsentFromBaselineLedgerReceiptsOutboxes": True,
        },
        "sourceCensus": {
            **source_meta,
            **route_meta,
            "testedSourceExclusion": tested_meta,
        },
        "historicalCandidateExclusion": historical_meta,
        "exactCorpusMeta": exact_meta,
        "finalReservationAudit": {
            "baselinePairs": len(snapshot["baseline"]),
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "ledgerHashes": len(ledger_hashes),
            "receiptHashes": len(final_receipt_hashes),
            "receiptPairs": len(final_receipt_pairs),
            "outboxHashesBeforeThisManifest": len(final_outbox_hashes),
            "outboxPairsBeforeThisManifest": len(final_outbox_pairs),
            "receiptMeta": final_receipt_meta,
            "outboxMeta": final_outbox_meta,
        },
        "artifacts": {
            "candidates": {
                "path": str(CANDIDATES.relative_to(ROOT)),
                "rows": len(candidate_rows),
                "sha256": hashlib.sha256(
                    candidate_payload.encode("utf-8")
                ).hexdigest(),
                "coefficientMaterialIncluded": False,
            },
            "exactIndex": {
                "path": str(EXACT_INDEX.relative_to(ROOT)),
                "rows": len(exact_rows),
                "sha256": hashlib.sha256(
                    exact_payload.encode("utf-8")
                ).hexdigest(),
                "coefficientMaterialIncluded": True,
            },
            "manifest": {
                "path": str(MANIFEST.relative_to(ROOT)),
                "rows": len(manifest_lines),
                "bytes": len(manifest_payload.encode("ascii")),
                "sha256": hashlib.sha256(
                    manifest_payload.encode("ascii")
                ).hexdigest(),
            },
        },
        "inputSha256": {
            str(path.relative_to(ROOT)): base.sha256_path(path)
            for path in (
                ROUTE_AUDIT,
                ROUTE_ROWS,
                base.ACTION_MAP,
                DB,
            )
        },
        "sideEffects": {
            "outboxWrites": 1,
            "ledgerWrites": 0,
            "receiptWrites": 0,
            "heavyArithmeticCalls": 0,
            "sageCalls": 0,
            "gapCalls": 0,
            "pariCalls": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "runtimeSeconds": time.monotonic() - started,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
    }

    base.exclusive_text(CANDIDATES, candidate_payload)
    base.exclusive_text(MANIFEST, manifest_payload)
    base.exclusive_text(EXACT_INDEX, exact_payload)
    base.exclusive_text(
        CERTIFICATE,
        json.dumps(certificate, indent=2, sort_keys=True) + "\n",
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "rows": len(candidate_rows),
                "bytes": certificate["manifestBytes"],
                "teamCountDistribution": dict(
                    sorted(k_distribution.items())
                ),
                "marginalBaseTotalExact": "65/128",
                "allFieldLabelsExact": True,
                "allSignaturesExact": True,
                "candidatesSha256": base.sha256_path(CANDIDATES),
                "exactIndexSha256": base.sha256_path(EXACT_INDEX),
                "manifestSha256": base.sha256_path(MANIFEST),
                "certificateSha256": base.sha256_path(CERTIFICATE),
                "runtimeSeconds": certificate["runtimeSeconds"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

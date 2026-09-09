#!/usr/bin/env python3
"""Construct and stage one exact fresh-prime twist per surviving T00134 pair.

The input route audit already proves the target label and exact signature for
fresh accepted even source presentations.  This worker rechecks the complete
live baseline/ledger/receipt/text-outbox boundary, chooses a small odd
ramification prime at which the source polynomial is squarefree, constructs
the generic signed twist, and stages one coefficient row per target pair.

The fresh ramification prime proves disjointness from the source splitting
field.  Together with the unanimous cached block action this proves the exact
transitive target label and irreducibility without Sage/GAP.  Exact
polynomial discriminants follow from

    Disc(d^12 P(x/sqrt(d))) = d^276 Disc(P).

No network or submission operation exists in this script.
"""

from __future__ import annotations

import hashlib
import json
import math
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
from stage_v14_negative_twist import (
    exact_real_root_count,
    is_prime,
    squarefree_mod_prime,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ROUTE_AUDIT = DATA / "fresh_t00134_twist_light_audit_20260730.json"
ROUTE_ROWS = DATA / "fresh_t00134_twist_light_routes_20260730.jsonl"
PLACEMENTS = DATA / "rank10_t00134_top10000_placements_20260730.jsonl"

CANDIDATES = DATA / "fresh_t00134_twist_top10000_candidates_20260730.jsonl"
CERTIFICATE = (
    DATA / "fresh_t00134_twist_top10000_stage_certificate_20260730.json"
)
MANIFEST = ROOT / "outbox/fresh_t00134_twist_top10000_43.txt"

EXPECTED_ROUTE_AUDIT_SHA256 = (
    "64974c260e1915082e605d7310bdd95ddae631a40bafae1da44273755732da90"
)
EXPECTED_ROUTE_ROWS_SHA256 = (
    "3c8d6d79c54129ef37102dbf4a982e996766f0c34da92d18802fd57d78ecc3ed"
)
DISCRIMINANT_EXPONENT = 24 * 23 // 2


def historical_candidate_hashes() -> tuple[set[str], dict]:
    hashes = set()
    files = []
    for path in sorted(DATA.rglob("*candidate*.jsonl")):
        if path.resolve() == CANDIDATES.resolve() or not path.is_file():
            continue
        before = len(hashes)
        rows = base.read_jsonl(path)
        for row in rows:
            for key, value in base.walk_values(row):
                if key in (
                    "coefficientSha256",
                    "candidateSha256",
                    "candidateCoefficientSha256",
                ) and value:
                    hashes.add(str(value))
        files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "rows": len(rows),
                "newHashes": len(hashes) - before,
                "sha256": base.sha256_path(path),
            }
        )
    return hashes, {
        "files": len(files),
        "distinctHashes": len(hashes),
        "hashSetSha256": base.canonical_digest(sorted(hashes)),
    }


def bareiss_determinant(matrix: list[list[int]]) -> int:
    """Exact fraction-free determinant over Z."""

    values = [list(map(int, row)) for row in matrix]
    size = len(values)
    if any(len(row) != size for row in values):
        raise ValueError("Bareiss matrix is not square")
    if size == 0:
        return 1
    sign = 1
    previous = 1
    for pivot_index in range(size - 1):
        if values[pivot_index][pivot_index] == 0:
            swap = next(
                (
                    row
                    for row in range(pivot_index + 1, size)
                    if values[row][pivot_index] != 0
                ),
                None,
            )
            if swap is None:
                return 0
            values[pivot_index], values[swap] = (
                values[swap],
                values[pivot_index],
            )
            sign = -sign
        pivot = values[pivot_index][pivot_index]
        for row in range(pivot_index + 1, size):
            for column in range(pivot_index + 1, size):
                numerator = (
                    values[row][column] * pivot
                    - values[row][pivot_index]
                    * values[pivot_index][column]
                )
                if numerator % previous:
                    raise ArithmeticError("Bareiss division was not exact")
                values[row][column] = numerator // previous
        for row in range(pivot_index + 1, size):
            values[row][pivot_index] = 0
        previous = pivot
    return sign * values[-1][-1]


def resultant(first: list[int], second: list[int]) -> int:
    """Exact resultant for ascending coefficient lists."""

    while len(first) > 1 and first[-1] == 0:
        first.pop()
    while len(second) > 1 and second[-1] == 0:
        second.pop()
    first_degree = len(first) - 1
    second_degree = len(second) - 1
    if first_degree < 0 or second_degree < 0:
        raise ValueError("zero polynomial has no finite resultant here")
    first_desc = list(reversed(first))
    second_desc = list(reversed(second))
    size = first_degree + second_degree
    matrix = []
    for shift in range(second_degree):
        matrix.append(
            [0] * shift
            + first_desc
            + [0] * (size - shift - len(first_desc))
        )
    for shift in range(first_degree):
        matrix.append(
            [0] * shift
            + second_desc
            + [0] * (size - shift - len(second_desc))
        )
    return bareiss_determinant(matrix)


def exact_even_polynomial_discriminant(coefficients: list[int]) -> int:
    """Return |Disc(P)| for monic P(x)=Q(x^2), using degree-12 Q."""

    quotient = list(coefficients[::2])
    degree = len(quotient) - 1
    if degree != 12 or quotient[-1] != 1 or quotient[0] == 0:
        raise ValueError("discriminant helper requires monic degree-12 Q")
    derivative = [
        index * quotient[index] for index in range(1, len(quotient))
    ]
    quotient_disc = abs(
        ((-1) ** (degree * (degree - 1) // 2))
        * resultant(quotient, derivative)
    )
    if quotient_disc == 0:
        raise ArithmeticError("source quotient polynomial is not squarefree")
    # |Disc(Q(x^2))| = 4^n |Q(0)| |Disc(Q)|^2.
    return 4**degree * abs(quotient[0]) * quotient_disc**2


def source_row(
    connection: sqlite3.Connection, route: dict
) -> tuple[dict, list[int]]:
    row = connection.execute(
        """
        SELECT v.status,v.scoreable,v.label,v.t,v.r,v.field_disc_abs,
               v.poly_disc_abs,p.coefficients,p.coefficient_hash
        FROM verifications AS v JOIN polynomials AS p
          USING(submission_id,polynomial_index)
        WHERE v.submission_id=? AND v.polynomial_index=?
        """,
        (
            str(route["sourceSubmissionId"]),
            int(route["sourcePolynomialIndex"]),
        ),
    ).fetchone()
    if row is None:
        raise ValueError("route source disappeared from the ledger")
    value = dict(row)
    if (
        str(value["status"]) != "accepted"
        or int(value["scoreable"] or 0) != 1
        or str(value["label"]) != str(route["sourceLabel"])
        or int(value["r"]) != int(route["sourceR"])
        or str(value["coefficient_hash"])
        != str(route["sourceCoefficientSha256"])
    ):
        raise ValueError("route source pin changed")
    coefficients = [
        int(item) for item in str(value["coefficients"]).split(",")
    ]
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
        or any(coefficients[index] for index in range(1, 25, 2))
    ):
        raise ValueError("route source is no longer primitive monic even")
    return value, coefficients


def first_candidate_for_route(
    source: dict,
    coefficients: list[int],
    route: dict,
    forbidden_hashes: set[str],
) -> dict:
    sign = str(route["sign"])
    if sign not in ("positive", "negative"):
        raise ValueError(f"invalid twist sign {sign}")
    prime = 3
    attempts = 0
    while prime < 100_000:
        while not is_prime(prime):
            prime += 1
        attempts += 1
        if not squarefree_mod_prime(coefficients, prime):
            prime += 1
            continue
        twist_d = prime if sign == "positive" else -prime
        twisted = [0] * 25
        for index in range(13):
            twisted[2 * index] = (
                coefficients[2 * index] * twist_d ** (12 - index)
            )
        if (
            twisted[-1] != 1
            or twisted[0] == 0
            or math.gcd(*twisted) != 1
            or any(twisted[index] for index in range(1, 25, 2))
        ):
            raise ArithmeticError("constructed twist is not primitive monic even")
        line = ",".join(str(value) for value in twisted)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if digest in forbidden_hashes:
            prime += 1
            continue
        polynomial_disc = (
            int(source["poly_disc_abs"])
            * prime**DISCRIMINANT_EXPONENT
        )
        return {
            "line": line,
            "coefficientSha256": digest,
            "coefficientBytes": len(line.encode("ascii")),
            "ramificationPrime": prime,
            "twistD": twist_d,
            "primeAttempts": attempts,
            "polynomialDiscriminantAbs": str(polynomial_disc),
        }
    raise ArithmeticError("no fresh squarefree ramification prime below 100000")


def main() -> int:
    started = time.monotonic()
    if any(path.exists() for path in (CANDIDATES, CERTIFICATE, MANIFEST)):
        raise FileExistsError("refusing to overwrite signed-twist stage artifacts")
    if base.sha256_path(ROUTE_AUDIT) != EXPECTED_ROUTE_AUDIT_SHA256:
        raise ValueError("route audit hash changed")
    if base.sha256_path(ROUTE_ROWS) != EXPECTED_ROUTE_ROWS_SHA256:
        raise ValueError("route-row hash changed")

    placements = {
        (str(row["label"]), int(row["r"])): row
        for row in base.validate_placements(PLACEMENTS, 10_000)
    }
    routes = base.read_jsonl(ROUTE_ROWS)
    alternatives = defaultdict(list)
    for route in routes:
        state = (route.get("boundaries") or {}).get("top10000")
        if state and state.get("survivesAllPairExclusions"):
            alternatives[
                (str(route["targetLabel"]), int(route["targetR"]))
            ].append(route)
    if len(alternatives) != 43 or sum(map(len, alternatives.values())) != 104:
        raise ValueError("sealed top-10,000 final route boundary changed")

    actions = {
        str(row["sourceLabel"]): row
        for row in base.read_jsonl(base.ACTION_MAP)
    }
    historical_hashes, historical_meta = historical_candidate_hashes()

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
        tested_sources, tested_meta = base.tested_twist_sources(single_rows)
        pair_exclusions = (
            snapshot["baseline"]
            | snapshot["owned"]
            | snapshot["knownPairs"]
            | receipt_pairs
            | outbox_pairs
        )
        if set(alternatives) & pair_exclusions:
            raise ValueError("one sealed target pair is no longer unreserved")

        forbidden_hashes = (
            ledger_hashes
            | receipt_hashes
            | outbox_hashes
            | set(exact_pool)
            | historical_hashes
        )
        selected = []
        selected_hashes = set()
        source_polynomial_discriminants: dict[str, int] = {}
        for pair, pair_routes_rows in sorted(
            alternatives.items(),
            key=lambda item: (
                int(placements[item[0]]["kTeams"]),
                int(item[0][0][3:]),
                int(item[0][1]),
            ),
        ):
            proposals = []
            for route in pair_routes_rows:
                if str(route["sourceCoefficientSha256"]) in tested_sources:
                    raise ValueError("sealed route source became previously tested")
                action = actions.get(str(route["sourceLabel"]))
                target_label = base.unanimous_target(action or {})
                if target_label != pair[0]:
                    raise ValueError("route action is no longer unanimous")
                source, coefficients = source_row(connection, route)
                source_hash = str(route["sourceCoefficientSha256"])
                source_polynomial_disc = source_polynomial_discriminants.get(
                    source_hash
                )
                if source_polynomial_disc is None:
                    source_polynomial_disc = (
                        exact_even_polynomial_discriminant(coefficients)
                    )
                    recorded = source.get("poly_disc_abs")
                    if (
                        recorded is not None
                        and int(recorded) != source_polynomial_disc
                    ):
                        raise ValueError(
                            "exact source polynomial discriminant disagrees "
                            "with the ledger"
                        )
                    source_polynomial_discriminants[source_hash] = (
                        source_polynomial_disc
                    )
                source["poly_disc_abs"] = str(source_polynomial_disc)
                quotient_roots, sturm_length = exact_real_root_count(
                    coefficients[::2]
                )
                negative_r = 2 * quotient_roots - int(route["sourceR"])
                expected_r = (
                    int(route["sourceR"])
                    if route["sign"] == "positive"
                    else negative_r
                )
                if (
                    expected_r != pair[1]
                    or negative_r
                    != int(route["negativeTwistRealRootCount"])
                ):
                    raise ValueError("route exact signature changed")
                candidate = first_candidate_for_route(
                    source,
                    coefficients,
                    route,
                    forbidden_hashes | selected_hashes,
                )
                proposals.append(
                    {
                        "_line": candidate.pop("line"),
                        **candidate,
                        "route": route,
                        "source": source,
                        "quotientRealRootCount": quotient_roots,
                        "negativeTwistRealRootCount": negative_r,
                        "sturmSequenceLength": sturm_length,
                        "action": action,
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
            selected.append((pair, chosen))
            selected_hashes.add(str(chosen["coefficientSha256"]))

        if len(selected) != 43 or len(selected_hashes) != 43:
            raise ValueError("candidate selection is not one-to-one")

        # Re-scan every receipt/outbox with the candidate mappings immediately
        # before the atomic writes, covering concurrent local staging.
        candidate_pair_index = {
            **pair_index,
            **{
                str(row["coefficientSha256"]): {pair}
                for pair, row in selected
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
        selected_pairs = {pair for pair, _row in selected}
        if (
            selected_hashes & ledger_hashes
            or selected_hashes & final_receipt_hashes
            or selected_hashes & final_outbox_hashes
            or selected_pairs & snapshot["baseline"]
            or selected_pairs & snapshot["owned"]
            or selected_pairs & snapshot["knownPairs"]
            or selected_pairs & final_receipt_pairs
            or selected_pairs & final_outbox_pairs
        ):
            raise ValueError("final candidate novelty/reservation recheck failed")
    finally:
        connection.close()

    candidate_rows = []
    manifest_lines = []
    for manifest_index, (pair, row) in enumerate(selected):
        route = row["route"]
        action = row["action"]
        placement = placements[pair]
        manifest_lines.append(str(row["_line"]))
        candidate_rows.append(
            {
                "status": "certified_exact_safe_staged_not_submitted",
                "manifestIndex": manifest_index,
                "coefficientSha256": str(row["coefficientSha256"]),
                "coefficientBytes": int(row["coefficientBytes"]),
                "fieldDiscriminantAbs": None,
                "fieldDiscriminantStatus": (
                    "not_computed_by_light_exact_label_worker"
                ),
                "polynomialDiscriminantAbs": str(
                    row["polynomialDiscriminantAbs"]
                ),
                "sourceSubmissionId": str(route["sourceSubmissionId"]),
                "sourcePolynomialIndex": int(
                    route["sourcePolynomialIndex"]
                ),
                "sourceCoefficientSha256": str(
                    route["sourceCoefficientSha256"]
                ),
                "sourceLabel": str(route["sourceLabel"]),
                "sourceR": int(route["sourceR"]),
                "sourceFieldDiscriminantAbs": route[
                    "sourceFieldDiscriminantAbs"
                ],
                "sign": str(route["sign"]),
                "targetLabel": pair[0],
                "targetR": pair[1],
                "targetKTeamsAtCrawl": int(placement["kTeams"]),
                "targetOpponentPointsAtCrawl": float(placement["points"]),
                "targetMinimumScoringDiscAbsAtCrawl": placement.get(
                    "minScoringDiscAbs"
                ),
                "targetHolderScoringDiscAbsAtCrawl": placement.get(
                    "scoringDiscAbs"
                ),
                "projectedMarginalBase": 2.0
                ** (-int(placement["kTeams"])),
                "ramificationPrime": int(row["ramificationPrime"]),
                "twistD": int(row["twistD"]),
                "primeAttempts": int(row["primeAttempts"]),
                "exactProof": {
                    "acceptedScoreableSourceHashPinned": True,
                    "sourcePrimitiveMonicEvenDegree24": True,
                    "actionArtifact": str(base.ACTION_MAP.relative_to(ROOT)),
                    "actionArtifactSha256": base.sha256_path(
                        base.ACTION_MAP
                    ),
                    "actionRowSha256": hashlib.sha256(
                        base.canonical_json(action).encode("utf-8")
                    ).hexdigest(),
                    "actionSystemCount": int(action["systemCount"]),
                    "unanimousGenericTargetLabel": pair[0],
                    "quotientRealRootCount": int(
                        row["quotientRealRootCount"]
                    ),
                    "negativeTwistRealRootCount": int(
                        row["negativeTwistRealRootCount"]
                    ),
                    "sturmSequenceLength": int(
                        row["sturmSequenceLength"]
                    ),
                    "signatureRule": (
                        "positive r=source_r; negative "
                        "r=2*SturmRealRoots(Q)-source_r"
                    ),
                    "sourceSquarefreeModuloRamificationPrime": True,
                    "ramificationDisjointness": (
                        "source splitting field is unramified at the "
                        "displayed odd prime; Q(sqrt(twistD)) is ramified, "
                        "so the quadratic extension is linearly disjoint"
                    ),
                    "irreducible": (
                        "the exact generic target action is transitive"
                    ),
                    "polynomialDiscriminantRule": (
                        "abs(Disc(candidate))="
                        "abs(Disc(source))*prime^276"
                    ),
                },
            }
        )

    k_distribution = Counter(
        int(row["targetKTeamsAtCrawl"]) for row in candidate_rows
    )
    marginal = sum(
        (
            Fraction(1, 2 ** int(row["targetKTeamsAtCrawl"]))
            for row in candidate_rows
        ),
        Fraction(),
    )
    opponent_half = sum(
        Fraction(str(row["targetOpponentPointsAtCrawl"])) / 2
        for row in candidate_rows
    )
    nominal_relative = marginal + opponent_half
    maximum_relative = marginal + 2 * opponent_half

    candidate_payload = "".join(
        base.canonical_json(row) + "\n" for row in candidate_rows
    )
    manifest_payload = "".join(line + "\n" for line in manifest_lines)
    certificate = {
        "schemaVersion": "fresh-t00134-top10000-twist-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_43_exact_safe_staged_not_submitted",
        "candidateRows": len(candidate_rows),
        "distinctCandidateHashes": len(selected_hashes),
        "distinctTargetPairs": len(selected_pairs),
        "signDistribution": dict(
            sorted(Counter(row["sign"] for row in candidate_rows).items())
        ),
        "teamCountDistribution": dict(sorted(k_distribution.items())),
        "correctedScoring": {
            "marginalBaseFormula": "2^(-currentTeamCount)",
            "distinctPairMarginalBaseTotalExact": (
                f"{marginal.numerator}/{marginal.denominator}"
            ),
            "distinctPairMarginalBaseTotal": float(marginal),
            "nominalRelativeSwingEstimate": float(nominal_relative),
            "maximumRelativeSwingUpperBound": float(maximum_relative),
            "note": (
                "field discriminants remain uncomputed, so these are "
                "current-min-preserving prioritization values rather than "
                "candidate-ratio-adjusted scores"
            ),
        },
        "discriminants": {
            "polynomialDiscriminantsComputedExactly": len(candidate_rows),
            "fieldDiscriminantsComputed": 0,
            "fieldDiscriminantReason": (
                "PARI nfdisc was intentionally deferred while the shared "
                "heavy slot was reserved for higher-value character work"
            ),
        },
        "sourceExclusion": tested_meta,
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
        "inputs": {
            str(path.relative_to(ROOT)): {
                "sha256": base.sha256_path(path),
            }
            for path in (
                ROUTE_AUDIT,
                ROUTE_ROWS,
                PLACEMENTS,
                base.ACTION_MAP,
                DB,
            )
        },
        "candidateArtifact": {
            "path": str(CANDIDATES.relative_to(ROOT)),
            "rows": len(candidate_rows),
            "coefficientMaterialIncluded": False,
            "sha256": hashlib.sha256(
                candidate_payload.encode("utf-8")
            ).hexdigest(),
        },
        "manifest": {
            "path": str(MANIFEST.relative_to(ROOT)),
            "rows": len(manifest_lines),
            "bytes": len(manifest_payload.encode("ascii")),
            "sha256": hashlib.sha256(
                manifest_payload.encode("ascii")
            ).hexdigest(),
        },
        "sideEffects": {
            "outboxWrites": 1,
            "ledgerWrites": 0,
            "receiptWrites": 0,
            "sageCalls": 0,
            "gapCalls": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "runtimeSeconds": time.monotonic() - started,
    }

    # All expensive scans and final novelty checks precede these exclusive
    # atomic creations.  Writing the coefficient manifest is staging only.
    base.exclusive_text(CANDIDATES, candidate_payload)
    base.exclusive_text(MANIFEST, manifest_payload)
    base.exclusive_text(
        CERTIFICATE,
        json.dumps(certificate, indent=2, sort_keys=True) + "\n",
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "candidates": str(CANDIDATES.relative_to(ROOT)),
                "candidatesSha256": base.sha256_path(CANDIDATES),
                "certificate": str(CERTIFICATE.relative_to(ROOT)),
                "certificateSha256": base.sha256_path(CERTIFICATE),
                "manifest": str(MANIFEST.relative_to(ROOT)),
                "manifestSha256": base.sha256_path(MANIFEST),
                "teamCountDistribution": dict(sorted(k_distribution.items())),
                "marginalBaseTotalExact": (
                    f"{marginal.numerator}/{marginal.denominator}"
                ),
                "nominalRelativeSwingEstimate": float(nominal_relative),
                "maximumRelativeSwingUpperBound": float(maximum_relative),
                "fieldDiscriminantsComputed": 0,
                "runtimeSeconds": certificate["runtimeSeconds"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

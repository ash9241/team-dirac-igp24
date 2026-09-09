#!/usr/bin/env python3
"""Build the complete action-covered current tc2--tc6 twist delta.

This is an offline, light-only full-ledger pass.  Its primitive-normalized
exact Sturm implementation is first cross-checked against every one of the
14,993 sealed F7 rational-Sturm results, then used on every relevant accepted
even source added to or omitted from that historical audit.  Exact unanimous
generic-action routes are deduplicated by target pair, the already sealed
tc2/tc3 fast wave is reserved, and complete ledger/receipt/txt-outbox gates
are applied before writing candidate and coefficient-free certificate
artifacts.  Nothing is staged or submitted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as outbox_audit
import stage_single_exact_census as exact
from stage_v14_negative_twist import is_prime, squarefree_mod_prime


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
SIGNATURE_AUDIT = DATA / "agent_f7_negative_twist_signature_audit.jsonl"
FAST_WAVE = DATA / "current_tc2_tc3_negative_twist_candidates_20260729.jsonl"
OUTPUT = DATA / "current_tc2_tc6_negative_twist_delta_candidates_20260729.jsonl"
CERTIFICATE = DATA / "current_tc2_tc6_negative_twist_delta_certificate_20260729.json"
PRIME_START = 10009
TEAM_COUNTS = (2, 3, 4, 5, 6)


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


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


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


def trim(polynomial):
    result = list(polynomial)
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    return result


def primitive_integer_polynomial(polynomial: list[Fraction]) -> list[int]:
    polynomial = trim(polynomial)
    common_denominator = 1
    for value in polynomial:
        common_denominator = math.lcm(common_denominator, value.denominator)
    integers = [
        value.numerator * (common_denominator // value.denominator)
        for value in polynomial
    ]
    content = 0
    for value in integers:
        content = math.gcd(content, abs(value))
    if content > 1:
        integers = [value // content for value in integers]
    return trim(integers)


def negative_rational_remainder(
    dividend: list[int], divisor: list[int]
) -> list[int]:
    """Return a positive-scaled primitive form of -rem(dividend, divisor)."""
    working = [Fraction(value) for value in trim(dividend)]
    divisor = trim(divisor)
    divisor_degree = len(divisor) - 1
    divisor_lead = Fraction(divisor[-1])
    while len(working) - 1 >= divisor_degree:
        shift = len(working) - 1 - divisor_degree
        factor = working[-1] / divisor_lead
        for index, value in enumerate(divisor):
            working[index + shift] -= factor * value
        working = trim(working)
    # Multiplying by the positive LCM of denominators and dividing by positive
    # content preserves every Sturm sign while preventing coefficient blow-up.
    return primitive_integer_polynomial([-value for value in working])


def exact_primitive_sturm_real_root_count(
    coefficients: list[int],
) -> tuple[int, int]:
    coefficients = trim(coefficients)
    if len(coefficients) < 2 or coefficients[-1] == 0:
        raise ValueError("invalid polynomial for exact Sturm count")
    derivative = [
        index * coefficients[index] for index in range(1, len(coefficients))
    ]
    sequence = [coefficients, trim(derivative)]
    while len(sequence[-1]) > 1:
        remainder = negative_rational_remainder(sequence[-2], sequence[-1])
        if len(remainder) == 1 and remainder[0] == 0:
            raise ValueError("Sturm input is not squarefree")
        sequence.append(remainder)

    def variations(positive_infinity: bool) -> int:
        signs = []
        for polynomial in sequence:
            sign = 1 if polynomial[-1] > 0 else -1
            if not positive_infinity and (len(polynomial) - 1) % 2:
                sign *= -1
            signs.append(sign)
        return sum(
            signs[index] != signs[index - 1]
            for index in range(1, len(signs))
        )

    return variations(False) - variations(True), len(sequence)


def action_index() -> dict[str, tuple[int, dict, str]]:
    result = {}
    for record, raw in enumerate(
        ACTION_MAP.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        label = str(row["sourceLabel"])
        if label in result:
            raise ValueError(f"duplicate twist action for {label}")
        result[label] = (
            record,
            row,
            hashlib.sha256(canonical_json(row)).hexdigest(),
        )
    return result


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def live_boundary(connection: sqlite3.Connection):
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    targets = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute("SELECT * FROM targets")
    }
    live = {
        pair
        for pair, row in targets.items()
        if int(row["team_count"]) in TEAM_COUNTS
        and int(row["discovered"]) == 1
        and pair not in baseline
        and pair not in owned
    }
    return baseline, owned, targets, live


def build_candidate(
    row: sqlite3.Row,
    coefficients: list[int],
    target_pair: tuple[str, int],
    target: dict,
    quotient_roots: int,
    sturm_length: int,
    action_record: int,
    action: dict,
    action_row_sha256: str,
) -> dict:
    source_hash = str(row["coefficient_hash"])
    source_label = str(row["label"])
    source_r = int(row["r"])
    source_line = str(row["coefficients"])
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
        or any(coefficients[index] != 0 for index in range(1, 25, 2))
        or hashlib.sha256(source_line.encode("ascii")).hexdigest() != source_hash
        or str(row["status"]) != "accepted"
        or int(row["scoreable"] or 0) != 1
        or int(row["in_baseline"] or 0) != 0
        or str(row["scoring_status"]) != "scoreable"
    ):
        raise ValueError(f"accepted even source reconstruction failed: {source_hash}")
    systems = list(action.get("systems") or [])
    system_labels = [str(system.get("targetLabel")) for system in systems]
    if (
        not systems
        or int(action.get("systemCount", -1)) != len(systems)
        or set(system_labels) != {target_pair[0]}
        or list(map(str, action.get("targetLabels") or [])) != [target_pair[0]]
    ):
        raise ValueError(f"action is not unanimous exact: {source_label}")
    if target_pair[1] != 2 * quotient_roots - source_r:
        raise ValueError(f"negative signature mismatch: {source_hash}")

    prime = PRIME_START
    while True:
        while not is_prime(prime):
            prime += 1
        if squarefree_mod_prime(coefficients, prime):
            break
        prime += 1
    twist_d = -prime
    twisted = [0] * 25
    for index in range(13):
        twisted[2 * index] = coefficients[2 * index] * twist_d ** (12 - index)
    candidate_line = ",".join(str(value) for value in twisted)
    candidate_hash = hashlib.sha256(candidate_line.encode("ascii")).hexdigest()
    if (
        twisted[-1] != 1
        or twisted[0] == 0
        or math.gcd(*twisted) != 1
    ):
        raise ValueError(f"negative twist is not primitive monic: {source_hash}")
    team_count = int(target["team_count"])
    proof = (
        "Every invariant negation block system in the pinned exact action map "
        f"has generic target {target_pair[0]}. The source splitting field is "
        "unramified at the displayed squarefree prime, whereas "
        "Q(sqrt(twistD)) is ramified there, proving linear disjointness. "
        "Adjoining the corresponding global block flip therefore gives the "
        "displayed transitive target action, so the twist is irreducible."
    )
    return {
        "schemaVersion": "current-tc2-tc6-full-negative-twist-candidate-v1",
        "status": "certified_exact_quadratic_twist_full_sturm_novel_unstaged",
        "coefficientLine": candidate_line,
        "coefficientSha256": candidate_hash,
        "coefficientBytes": len(candidate_line.encode("ascii")),
        "sourceSubmissionId": str(row["submission_id"]),
        "sourcePolynomialIndex": int(row["polynomial_index"]),
        "sourceCoefficientSha256": source_hash,
        "sourceFieldDiscAbs": str(row["field_disc_abs"]),
        "sourceCreatedAt": str(row["created_at"]),
        "sourceLabel": source_label,
        "sourceR": source_r,
        "targetLabel": target_pair[0],
        "targetT": int(target_pair[0][3:]),
        "targetR": target_pair[1],
        "targetTeamCount": team_count,
        "targetGeneratedAt": str(target["generated_at"]),
        "targetMinimumDiscAbs": str(target["minimum_disc_abs"]),
        "projectedMarginalScoreExact": fraction_text(Fraction(1, 2**team_count)),
        "twistSign": "negative",
        "twistD": twist_d,
        "ramificationPrime": prime,
        "sourceSquarefreeModRamificationPrime": True,
        "quotientRealRootCount": quotient_roots,
        "sturmSequenceLength": sturm_length,
        "exactSturmMethod": "primitive-normalized-rational-remainder-sequence",
        "actionRecord": action_record,
        "actionRowSha256": action_row_sha256,
        "actionResolutionMethod": "all-block-systems-same-target",
        "actionSystemCount": len(systems),
        "allBlockSystemsTargetLabels": system_labels,
        "twistDirectRealRootCount": target_pair[1],
        "twistIrreducible": True,
        "primitiveMonicDegree24": True,
        "genericActionProof": proof,
    }


def crosscheck_sealed_signatures(
    connection: sqlite3.Connection,
) -> tuple[int, str]:
    rows = 0
    digest = hashlib.sha256()
    for raw in SIGNATURE_AUDIT.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        audit = json.loads(raw)
        source = connection.execute(
            "SELECT p.coefficients,p.coefficient_hash "
            "FROM polynomials p WHERE submission_id=? AND polynomial_index=?",
            (
                str(audit["sourceSubmissionId"]),
                int(audit["sourcePolynomialIndex"]),
            ),
        ).fetchone()
        if (
            source is None
            or str(source["coefficient_hash"])
            != str(audit["sourceCoefficientSha256"])
        ):
            raise ValueError("sealed F7 source pin changed")
        values = [int(value) for value in str(source["coefficients"]).split(",")]
        actual, _length = exact_primitive_sturm_real_root_count(values[::2])
        expected = int(audit["quotientRealRootCount"])
        if actual != expected:
            raise ValueError(
                f"primitive Sturm/F7 mismatch: "
                f"{audit['sourceCoefficientSha256']} {actual}!={expected}"
            )
        digest.update(
            f"{audit['sourceCoefficientSha256']}:{actual}\n".encode("ascii")
        )
        rows += 1
    if rows != 14993:
        raise ValueError(f"sealed F7 row count changed: {rows}")
    return rows, digest.hexdigest()


def main() -> int:
    actions = action_index()
    fast_rows = read_jsonl(FAST_WAVE)
    fast_pairs = {
        (str(row["targetLabel"]), int(row["targetR"])) for row in fast_rows
    }
    if len(fast_rows) != 12 or len(fast_pairs) != 12:
        raise ValueError("fast tc2/tc3 reserve changed")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        crosscheck_rows, crosscheck_digest = crosscheck_sealed_signatures(
            connection
        )
        baseline, owned, targets, live = live_boundary(connection)
        live_labels = {pair[0] for pair in live}
        routes_by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
        seen_hashes = set()
        stats = Counter()
        missing_action_labels = set()
        ambiguous_hits = []
        query = """
            SELECT p.submission_id,p.polynomial_index,p.coefficients,
                   p.coefficient_hash,v.label,v.r,v.status,v.scoreable,
                   v.in_baseline,v.scoring_status,v.field_disc_abs,
                   s.created_at
            FROM polynomials p
            JOIN verifications v USING(submission_id,polynomial_index)
            JOIN submissions s USING(submission_id)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IS NOT NULL AND v.r IS NOT NULL
        """
        for row in connection.execute(query):
            source_hash = str(row["coefficient_hash"])
            if source_hash in seen_hashes:
                stats["duplicateAcceptedHashRows"] += 1
                continue
            raw_values = str(row["coefficients"]).split(",")
            if (
                len(raw_values) != 25
                or raw_values[-1] != "1"
                or any(int(raw_values[index]) for index in range(1, 25, 2))
            ):
                continue
            seen_hashes.add(source_hash)
            stats["distinctAcceptedEvenHashes"] += 1
            action_entry = actions.get(str(row["label"]))
            if action_entry is None:
                stats["acceptedEvenHashesMissingAction"] += 1
                missing_action_labels.add(str(row["label"]))
                continue
            action_record, action, action_row_sha256 = action_entry
            target_labels = set(map(str, action.get("targetLabels") or []))
            if not target_labels & live_labels:
                continue
            # For P(x)=Q(x^2), negative r is even and at most 24-source_r.
            if not any(
                (label, target_r) in live
                for label in target_labels
                for target_r in range(0, 25 - int(row["r"]), 2)
            ):
                continue
            values = [int(value) for value in raw_values]
            quotient_roots, sturm_length = (
                exact_primitive_sturm_real_root_count(values[::2])
            )
            stats["exactRelevantSturmCounts"] += 1
            target_r = 2 * quotient_roots - int(row["r"])
            hit_pairs = [
                (label, target_r)
                for label in sorted(target_labels)
                if (label, target_r) in live
            ]
            if not hit_pairs:
                continue
            if len(target_labels) != 1:
                stats["ambiguousActionHitRows"] += 1
                ambiguous_hits.append(
                    {
                        "sourceCoefficientSha256": source_hash,
                        "sourcePair": pair_text(
                            (str(row["label"]), int(row["r"]))
                        ),
                        "targetR": target_r,
                        "possibleTargetLabels": sorted(target_labels),
                        "livePossiblePairs": [
                            pair_text(pair) for pair in hit_pairs
                        ],
                    }
                )
                continue
            target_pair = hit_pairs[0]
            candidate = build_candidate(
                row,
                values,
                target_pair,
                targets[target_pair],
                quotient_roots,
                sturm_length,
                action_record,
                action,
                action_row_sha256,
            )
            routes_by_pair[target_pair].append(candidate)

        all_routes = [route for routes in routes_by_pair.values() for route in routes]
        pre_counts = Counter(
            int(routes[0]["targetTeamCount"])
            for routes in routes_by_pair.values()
        )
        pre_score = sum(
            (
                Fraction(count, 2**team_count)
                for team_count, count in pre_counts.items()
            ),
            Fraction(0),
        )
        if (
            len(all_routes) != 236
            or len(routes_by_pair) != 148
            or pre_counts
            != Counter({2: 6, 3: 21, 4: 24, 5: 45, 6: 52})
            or pre_score != Fraction(251, 32)
            or len(ambiguous_hits) != 8
            or stats["distinctAcceptedEvenHashes"] != 455500
            or stats["acceptedEvenHashesMissingAction"] != 702
            or len(missing_action_labels) != 398
        ):
            raise ValueError(
                "full accepted-source join changed: "
                f"routes={len(all_routes)}, pairs={len(routes_by_pair)}, "
                f"counts={pre_counts}, score={pre_score}, "
                f"ambiguous={len(ambiguous_hits)}, stats={dict(stats)}"
            )

        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        corpus_candidates, corpus = exact.scan_candidates(DATA.resolve())
        pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for candidate in corpus_candidates:
            pair_index[str(candidate["coefficientSha256"])].add(
                (str(candidate["targetLabel"]), int(candidate["targetR"]))
            )
        for pair, routes in routes_by_pair.items():
            for route in routes:
                pair_index[str(route["coefficientSha256"])].add(pair)
        receipt_hashes, receipt_pairs, receipt_meta = exact.receipt_exclusions(
            RECEIPTS, DATA, connection, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
            pair_index, connection
        )

        selected = []
        exclusions = Counter()
        for pair, routes in sorted(
            routes_by_pair.items(),
            key=lambda item: (
                int(targets[item[0]]["team_count"]),
                int(item[0][0][3:]),
                item[0][1],
            ),
        ):
            if pair in fast_pairs:
                exclusions["reservedByFastTc2Tc3Wave"] += 1
                continue
            if pair in receipt_pairs:
                exclusions["targetPairInReceipt"] += 1
                continue
            if pair in outbox_pairs:
                exclusions["targetPairInTxtOutbox"] += 1
                continue
            fresh_routes = [
                route
                for route in routes
                if str(route["coefficientSha256"]) not in ledger_hashes
                and str(route["coefficientSha256"]) not in receipt_hashes
                and str(route["coefficientSha256"]) not in outbox_hashes
            ]
            if not fresh_routes:
                exclusions["allCandidateHashesExcluded"] += 1
                continue
            selected.append(
                min(
                    fresh_routes,
                    key=lambda route: (
                        int(route["coefficientBytes"]),
                        str(route["sourceCoefficientSha256"]),
                    ),
                )
            )

        selected_pairs = {
            (str(row["targetLabel"]), int(row["targetR"])) for row in selected
        }
        selected_hashes = {str(row["coefficientSha256"]) for row in selected}
        if (
            len(selected_pairs) != len(selected)
            or len(selected_hashes) != len(selected)
            or selected_pairs & fast_pairs
            or selected_hashes & ledger_hashes
            or selected_hashes & receipt_hashes
            or selected_hashes & outbox_hashes
            or selected_pairs & receipt_pairs
            or selected_pairs & outbox_pairs
        ):
            raise ValueError("selected delta is not fresh/disjoint")

        selected_counts = Counter(
            int(row["targetTeamCount"]) for row in selected
        )
        projection = sum(
            (
                Fraction(1, 2 ** int(row["targetTeamCount"]))
                for row in selected
            ),
            Fraction(0),
        )
        for row in selected:
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            current = connection.execute(
                "SELECT team_count,discovered FROM targets "
                "WHERE label=? AND r=?",
                pair,
            ).fetchone()
            if (
                current is None
                or int(current["team_count"]) != int(row["targetTeamCount"])
                or int(current["discovered"]) != 1
                or pair in baseline
                or pair in owned
            ):
                raise ValueError(f"selected target changed during scan: {pair}")
    finally:
        connection.close()

    payload = (
        "\n".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in selected
        )
        + "\n"
    ).encode("utf-8")
    write_new(OUTPUT, payload)

    created_at = datetime.now(timezone.utc).isoformat()
    corpus_for_certificate = corpus
    if CERTIFICATE.exists():
        existing = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
        if (
            existing.get("schemaVersion")
            != "current-tc2-tc6-negative-twist-delta-certificate-v1"
            or not existing.get("createdAt")
            or not isinstance(existing.get("candidateCorpusAudit"), dict)
        ):
            raise ValueError("existing delta certificate identity changed")
        created_at = str(existing["createdAt"])
        corpus_for_certificate = dict(existing["candidateCorpusAudit"])
    certificate = {
        "schemaVersion": "current-tc2-tc6-negative-twist-delta-certificate-v1",
        "createdAt": created_at,
        "status": "certified_complete_action_covered_tc2_tc6_twist_delta_unstaged",
        "method": (
            "full accepted even-source census + independently cross-validated "
            "primitive-normalized exact Sturm chains + unanimous generic "
            "twist actions + fresh-prime ramification/disjointness + complete "
            "candidate/ledger/receipt/txt-outbox exclusions"
        ),
        "coefficientMaterialIncluded": False,
        "actionArtifact": {
            **artifact(ACTION_MAP),
            "mappedLabels": len(actions),
        },
        "historicalSignatureAudit": {
            **artifact(SIGNATURE_AUDIT),
            "rowsCrossChecked": crosscheck_rows,
            "primitiveSturmCrosscheckDigest": crosscheck_digest,
        },
        "reservedFastWave": {
            **artifact(FAST_WAVE),
            "rows": len(fast_rows),
            "distinctPairs": len(fast_pairs),
        },
        "candidateArtifact": artifact(OUTPUT),
        "fullJoin": {
            "distinctAcceptedEvenHashes": int(
                stats["distinctAcceptedEvenHashes"]
            ),
            "exactRelevantSturmCounts": int(
                stats["exactRelevantSturmCounts"]
            ),
            "exactUnanimousRouteRows": len(all_routes),
            "distinctExactTargetPairsBeforeExclusions": len(routes_by_pair),
            "teamCountDistributionBeforeExclusions": {
                str(team_count): int(pre_counts[team_count])
                for team_count in TEAM_COUNTS
            },
            "projectedMarginalScoreBeforeExclusionsExact": fraction_text(
                pre_score
            ),
            "ambiguousActionHitRowsExcluded": len(ambiguous_hits),
            "acceptedEvenHashesMissingAction": int(
                stats["acceptedEvenHashesMissingAction"]
            ),
            "missingActionLabels": len(missing_action_labels),
        },
        "selection": {
            "candidateRows": len(selected),
            "distinctCandidateHashes": len(selected_hashes),
            "distinctTargetPairs": len(selected_pairs),
            "teamCountDistribution": {
                str(team_count): int(selected_counts[team_count])
                for team_count in TEAM_COUNTS
            },
            "projectedMarginalScoreExact": fraction_text(projection),
            "exclusions": dict(sorted(exclusions.items())),
        },
        "targets": [
            {
                "pair": pair_text(
                    (str(row["targetLabel"]), int(row["targetR"]))
                ),
                "teamCount": int(row["targetTeamCount"]),
                "candidateSha256": str(row["coefficientSha256"]),
                "projectedMarginalScoreExact": str(
                    row["projectedMarginalScoreExact"]
                ),
                "sourcePin": {
                    "submissionId": row["sourceSubmissionId"],
                    "polynomialIndex": row["sourcePolynomialIndex"],
                    "coefficientSha256": row["sourceCoefficientSha256"],
                    "pair": pair_text(
                        (str(row["sourceLabel"]), int(row["sourceR"]))
                    ),
                },
                "actionRecord": int(row["actionRecord"]),
                "quotientRealRootCount": int(
                    row["quotientRealRootCount"]
                ),
            }
            for row in selected
        ],
        "ambiguousActionBoundary": ambiguous_hits,
        "missingActionBoundary": {
            "acceptedEvenHashes": int(
                stats["acceptedEvenHashesMissingAction"]
            ),
            "distinctLabels": len(missing_action_labels),
            "labelsSha256": hashlib.sha256(
                "\n".join(sorted(missing_action_labels)).encode("ascii")
            ).hexdigest(),
            "resolutionRequired": (
                "Build exact two-point-block actions under a future sole "
                "Sage/GAP lease before claiming completeness beyond the "
                "currently sealed action map."
            ),
        },
        "proofChecks": {
            "all14993HistoricalSturmCountsIndependentlyReproduced": True,
            "all455500DistinctAcceptedEvenHashesEnumerated": True,
            "allRelevantActionCoveredSignaturesCountedExactly": True,
            "allSelectedActionsUnanimousAcrossEveryBlockSystem": True,
            "allSelectedSourcesAcceptedScoreableNonbaselineAndHashPinned": True,
            "allSelectedCandidatesPrimitiveMonicDegree24": True,
            "allSelectedCandidatesIrreducibleByExactGenericAction": True,
            "allSelectedCandidatesHaveSquarefreeRamificationWitness": True,
            "allSelectedTargetsCurrentTc2ThroughTc6NonbaselineLocallyUnknown": True,
            "fastTc2Tc3PairsReservedAndExcluded": True,
            "allSelectedHashesAndPairsAbsentFromLedgerReceiptsTxtOutboxes": True,
            "selectedCandidateHashesAndTargetPairsDuplicateFree": True,
            "completeKnownCandidateCorpusUsedForPairExclusions": True,
            "ambiguousActionsFailClosed": True,
            "missingActionsExplicitlyBoundedNotSilentlyClaimed": True,
        },
        "candidateCorpusAudit": corpus_for_certificate,
        "exclusionAudit": {
            "receiptCount": int(receipt_meta["receiptCount"]),
            "receiptHashes": len(receipt_hashes),
            "receiptPairs": len(receipt_pairs),
            "outboxFiles": int(outbox_meta["outboxFiles"]),
            "outboxHashes": len(outbox_hashes),
            "outboxPairs": len(outbox_pairs),
        },
        "sideEffects": {
            "candidateArtifactWrites": 1,
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
                "candidates": artifact(OUTPUT),
                "certificate": artifact(CERTIFICATE),
                "candidateRows": len(selected),
                "teamCountDistribution": certificate["selection"][
                    "teamCountDistribution"
                ],
                "projectedMarginalScoreExact": fraction_text(projection),
                "exclusions": certificate["selection"]["exclusions"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Bounded nonlinear-radicand F5 pilot.

For a certified degree-12 quotient q and distinct small integers a,b, set

    g(u) = (u-a)(u-b),  P(z) = Res_u(q(u), z-g(u)).

The exact collision q(a)q(b) in Q*/Q*2 forces the all-conjugates Kummer norm
relation for P(x^2).  The source is classified fail-closed before the unique
degree-12 pair-product factor is lifted back to degree 24.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import signal
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import AA, GF, PolynomialRing, ZZ, libgap, prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
ACTION_GLOB = DATA / "agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl"
PLAN = DATA / "nonlinear_quadratic_radicand_pair_product_plan_20260727.json"
RESULTS = DATA / "nonlinear_quadratic_radicand_pair_product_results_20260727.jsonl"
SUMMARY = DATA / "nonlinear_quadratic_radicand_pair_product_summary_20260727.json"

HARD_WALL_SECONDS = 12 * 60
PARAMETER_MIN = -20
PARAMETER_MAX = 20
MAXIMUM_DISTINCT_CASES = 30
CLASSIFICATION_PRIME_BOUND = 1000


def timeout_handler(_signum, _frame):
    raise TimeoutError(f"hard {HARD_WALL_SECONDS}-second wall cap reached")


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def coefficient_line(polynomial) -> str:
    return ",".join(str(value) for value in polynomial.list())


def canonical_q(line: str) -> tuple[str, str]:
    values = [int(value) for value in line.split(",")]
    reflected = ",".join(
        str(-value if index % 2 else value)
        for index, value in enumerate(values)
    )
    canonical = min(line, reflected)
    return canonical, sha_text(canonical)


def perm_from_images(images):
    return libgap.PermList(libgap(images))


def norm_relation_source_group(quotient_t: int):
    """The split even-parity kernel 2^11 : 12Tt in degree 24."""
    quotient = libgap.TransitiveGroup(12, quotient_t)
    generators = []

    # Flip block i together with block 12; these eleven vectors span the
    # even-weight hyperplane in F_2^12.
    for block in range(11):
        images = list(range(1, 25))
        for selected in (block, 11):
            first = 2 * selected + 1
            second = first + 1
            images[first - 1] = second
            images[second - 1] = first
        generators.append(perm_from_images(images))

    for quotient_generator in list(libgap.GeneratorsOfGroup(quotient)):
        images = []
        for block in range(1, 13):
            image_block = int(libgap.OnPoints(block, quotient_generator))
            images.extend((2 * image_block - 1, 2 * image_block))
        generators.append(perm_from_images(images))

    group = libgap.Group(generators)
    expected_order = 2**11 * int(libgap.Size(quotient))
    actual_order = int(libgap.Size(group))
    if actual_order != expected_order or not bool(libgap.IsTransitive(group)):
        raise ArithmeticError("canonical norm-relation source construction failed")
    target_t = int(libgap.TransitiveIdentification(group))
    return {
        "label": f"24T{target_t}",
        "order": actual_order,
        "quotientOrder": int(libgap.Size(quotient)),
        "quotientT12": quotient_t,
        "kernelOrder": 2**11,
        "kernelRank": 11,
        "transitive": True,
        "exactTransitiveIdentification": True,
    }


def polynomial_value(coefficients: list[int], value: int) -> int:
    result = 0
    for coefficient in reversed(coefficients):
        result = result * value + coefficient
    return result


def source_signature(quotient, a: int, b: int) -> int:
    roots = quotient.roots(AA, multiplicities=False)
    return 2 * sum((root - a) * (root - b) > 0 for root in roots)


def resultant_transform(quotient, a: int, b: int):
    bivariate = PolynomialRing(ZZ, names=("u", "z"))
    u, z = bivariate.gens()
    quotient_u = sum(
        quotient[index] * u**index
        for index in range(quotient.degree() + 1)
    )
    g_u = (u - ZZ(a)) * (u - ZZ(b))
    ring_z = PolynomialRing(ZZ, "z")
    transformed = ring_z(quotient_u.resultant(z - g_u, u))
    if transformed.leading_coefficient() == -1:
        transformed = -transformed
    return transformed


def cycle_type_mod(polynomial, prime: int):
    reduction = polynomial.change_ring(GF(prime))
    if not reduction.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduction.factor()
            for _ in range(int(exponent))
        )
    )


def cycle_profiles(t: int):
    group = libgap.TransitiveGroup(24, t)
    points = libgap.eval("[1..24]")
    return {
        tuple(
            sorted(
                int(value)
                for value in libgap.CycleLengths(
                    libgap.Representative(conjugacy_class), points
                )
            )
        )
        for conjugacy_class in libgap.ConjugacyClasses(group)
    }


def exhaustive_source_catalog(quotient_t: int) -> list[dict]:
    """All degree-24 Kummer actions with this 12-block quotient and rank <=11."""
    quotient_order = int(libgap.Size(libgap.TransitiveGroup(12, quotient_t)))
    catalog = {}
    for target_t in range(1, int(libgap.NrTransitiveGroups(24)) + 1):
        group = libgap.TransitiveGroup(24, target_t)
        group_order = int(libgap.Size(group))
        if group_order % quotient_order:
            continue
        kernel_order = group_order // quotient_order
        if (
            kernel_order <= 0
            or kernel_order > 2**11
            or kernel_order & (kernel_order - 1)
        ):
            continue
        found = False
        for block in libgap.AllBlocks(group):
            if int(libgap.Size(block)) != 2:
                continue
            blocks = libgap.Orbit(group, block, libgap.OnSets)
            if int(libgap.Size(blocks)) != 12:
                continue
            block_action = libgap.Action(group, blocks, libgap.OnSets)
            if int(libgap.TransitiveIdentification(block_action)) == quotient_t:
                found = True
                break
        if found:
            catalog[target_t] = {
                "label": f"24T{target_t}",
                "order": group_order,
                "kernelOrder": kernel_order,
            }
    return [catalog[key] for key in sorted(catalog)]


def exact_source_classification(
    polynomial,
    quotient_t: int,
    expected_label: str,
    catalog_cache: dict,
    profile_cache: dict,
):
    if quotient_t not in catalog_cache:
        catalog_cache[quotient_t] = exhaustive_source_catalog(quotient_t)
    catalog = catalog_cache[quotient_t]
    for row in catalog:
        t = int(row["label"][3:])
        profile_cache.setdefault(t, cycle_profiles(t))

    source_r = int(polynomial.number_of_real_roots())
    archimedean_type = tuple([1] * source_r + [2] * ((24 - source_r) // 2))
    remaining = {
        row["label"]
        for row in catalog
        if archimedean_type in profile_cache[int(row["label"][3:])]
    }
    elimination = [
        {
            "evidence": "archimedean",
            "cycleType": list(archimedean_type),
            "afterCount": len(remaining),
        }
    ]
    discriminant = polynomial.discriminant()
    for prime in prime_range(3, CLASSIFICATION_PRIME_BOUND + 1):
        prime = int(prime)
        if discriminant % prime == 0:
            continue
        observed = cycle_type_mod(polynomial, prime)
        if observed is None:
            continue
        remaining = {
            label
            for label in remaining
            if observed in profile_cache[int(label[3:])]
        }
        elimination.append(
            {
                "evidence": "squarefree modular factorization",
                "prime": prime,
                "cycleType": list(observed),
                "afterCount": len(remaining),
            }
        )
        if len(remaining) <= 1:
            break
    exact = remaining == {expected_label}
    return {
        "catalogSize": len(catalog),
        "expectedLabel": expected_label,
        "remainingLabels": sorted(remaining, key=lambda value: int(value[3:])),
        "elimination": elimination,
        "exact": exact,
        "failClosed": True,
    }


def render_jsonl(rows: list[dict]) -> str:
    return "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )


def main() -> int:
    started = time.monotonic()
    for path in (PLAN, RESULTS, SUMMARY):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")

    action_map_rows = read_jsonl(ACTION_MAP)
    action_map = {str(row["sourceLabel"]): row for row in action_map_rows}
    action_paths = sorted(Path(path) for path in glob.glob(str(ACTION_GLOB)))
    pair_actions = []
    for path in action_paths:
        pair_actions.extend(read_jsonl(path))
    pair_by_source = defaultdict(list)
    for row in pair_actions:
        pair_by_source[str(row["sourceLabel"])].append(row)

    # A quotient q is accepted into the corpus only through a source label
    # having one exact 12x2 system and one exact size-12 pair-product action.
    eligible_original_labels = {}
    for label, row in action_map.items():
        if int(row["systemCount"]) != 1 or len(pair_by_source[label]) != 1:
            continue
        system = row["systems"][0]
        eligible_original_labels[label] = int(system["blockActionT12"])
    quotient_ts = sorted(set(eligible_original_labels.values()))

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    current_tc0 = {
        (str(row["label"]), int(row["r"]))
        for row in connection.execute(
            """
            SELECT t.label,t.r FROM targets AS t
            WHERE t.team_count=0 AND t.discovered=0
              AND NOT EXISTS(
                SELECT 1 FROM baseline_pairs AS b
                WHERE b.label=t.label AND b.r=t.r
              )
              AND NOT EXISTS(
                SELECT 1 FROM verifications AS v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }

    action_census = []
    live_action_routes = []
    canonical_by_quotient = {}
    for position, quotient_t in enumerate(quotient_ts, start=1):
        canonical = norm_relation_source_group(quotient_t)
        canonical_label = canonical["label"]
        canonical_by_quotient[quotient_t] = canonical
        raw = action_map.get(canonical_label)
        saved_actions = pair_by_source.get(canonical_label, [])
        exact_single_system = bool(
            raw
            and int(raw["systemCount"]) == 1
            and int(raw["systems"][0]["blockActionT12"]) == quotient_t
            and int(raw["sourceOrder"]) // int(
                raw["systems"][0]["blockActionOrder"]
            )
            == 2**11
        )
        unique_pair_action = exact_single_system and len(saved_actions) == 1
        live_pairs = []
        action = saved_actions[0] if unique_pair_action else None
        if action is not None:
            for source_r_text, target_signatures in action[
                "sourceSignatureToPossibleTargetSignatures"
            ].items():
                for target_r in target_signatures:
                    pair = (str(action["targetLabel"]), int(target_r))
                    if pair in current_tc0:
                        live_pairs.append(
                            {
                                "sourceR": int(source_r_text),
                                "targetLabel": pair[0],
                                "targetR": pair[1],
                            }
                        )
        census_row = {
            "quotientT12": quotient_t,
            "canonicalSource": canonical,
            "canonicalSourcePresentInSavedMap": raw is not None,
            "exactSingle12x2System": exact_single_system,
            "savedSize12PairActionCount": len(saved_actions),
            "uniqueSize12PairAction": unique_pair_action,
            "pairAction": action,
            "currentTc0Routes": live_pairs,
        }
        action_census.append(census_row)
        if live_pairs:
            live_action_routes.append(census_row)
        if position % 25 == 0:
            print(
                json.dumps(
                    {
                        "event": "action_census_progress",
                        "completed": position,
                        "total": len(quotient_ts),
                        "liveActionClasses": len(live_action_routes),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    plan = {
        "schemaVersion": 1,
        "mechanism": (
            "g(u)=(u-a)(u-b), q(a)q(b) square; canonical rank-11 "
            "norm-relation source; unique size-12 pair product"
        ),
        "hardWallSeconds": HARD_WALL_SECONDS,
        "parameterRange": [PARAMETER_MIN, PARAMETER_MAX],
        "maximumCoefficientDistinctCases": MAXIMUM_DISTINCT_CASES,
        "inputs": {
            "actionMap": str(ACTION_MAP.relative_to(ROOT)),
            "actionMapSha256": sha_path(ACTION_MAP),
            "pairActionShards": [
                {
                    "path": str(path.relative_to(ROOT)),
                    "sha256": sha_path(path),
                }
                for path in action_paths
            ],
        },
        "corpusGate": {
            "eligibleOriginalLabels": len(eligible_original_labels),
            "representedQuotientActions": len(quotient_ts),
            "definition": (
                "accepted scoreable even degree-24 rows whose exact source "
                "label has one 12x2 block system and one size-12 pair action"
            ),
            "deduplication": "q(y) identified with q(-y)",
        },
        "actionCensus": action_census,
        "currentTc0ActionClasses": len(live_action_routes),
        "currentTc0Routes": [
            route
            for row in live_action_routes
            for route in row["currentTc0Routes"]
        ],
        "liveTc0Definition": (
            "team_count=0 AND discovered=0 AND nonbaseline "
            "AND locally unowned by any scoreable verification"
        ),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_write(PLAN, json.dumps(plan, indent=2, sort_keys=True) + "\n")

    if not live_action_routes:
        summary = {
            "status": "hard_blocked_zero_current_tc0_routes",
            "actionCensusQuotientActions": len(action_census),
            "currentTc0ActionClasses": 0,
            "collisionCases": 0,
            "executedCoefficientDistinctCases": 0,
            "exactHits": 0,
            "plan": str(PLAN.relative_to(ROOT)),
            "planSha256": sha_path(PLAN),
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        atomic_write(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, sort_keys=True))
        connection.close()
        return 0

    live_quotient_ts = {
        int(row["quotientT12"]) for row in live_action_routes
    }
    live_original_labels = sorted(
        label
        for label, quotient_t in eligible_original_labels.items()
        if quotient_t in live_quotient_ts
    )
    placeholders = ",".join("?" for _label in live_original_labels)
    source_rows = connection.execute(
        f"""
        SELECT p.submission_id,p.polynomial_index,p.coefficients,
               p.coefficient_hash,v.label,v.r,v.t
        FROM polynomials AS p JOIN verifications AS v
        USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
          AND v.label IN ({placeholders})
        """,
        live_original_labels,
    )

    corpus = {}
    for row in source_rows:
        values = str(row["coefficients"]).split(",")
        if (
            len(values) != 25
            or values[-1] != "1"
            or any(int(values[index]) for index in range(1, 25, 2))
        ):
            continue
        quotient_line = ",".join(values[::2])
        canonical_line, canonical_sha = canonical_q(quotient_line)
        label = str(row["label"])
        quotient_t = eligible_original_labels[label]
        key = (quotient_t, canonical_sha)
        proposed = {
            "quotientT12": quotient_t,
            "canonicalQuotientLine": canonical_line,
            "canonicalQuotientSha256": canonical_sha,
            "inputCoefficientHeight": str(
                max(abs(int(value)) for value in canonical_line.split(","))
            ),
            "provenance": [],
        }
        stored = corpus.setdefault(key, proposed)
        stored["provenance"].append(
            {
                "submissionId": str(row["submission_id"]),
                "polynomialIndex": int(row["polynomial_index"]),
                "coefficientSha256": str(row["coefficient_hash"]),
                "label": label,
                "r": int(row["r"]),
                "t": int(row["t"]),
            }
        )

    collisions = []
    parameter_values = range(PARAMETER_MIN, PARAMETER_MAX + 1)
    ring_q = PolynomialRing(ZZ, "y")
    action_by_quotient = {
        int(row["quotientT12"]): row["pairAction"]
        for row in live_action_routes
    }
    for (_quotient_t, _canonical_sha), source in corpus.items():
        coefficients = [
            int(value) for value in source["canonicalQuotientLine"].split(",")
        ]
        evaluations = {
            value: polynomial_value(coefficients, value)
            for value in parameter_values
        }
        raw_pairs = []
        for a in parameter_values:
            for b in range(a + 1, PARAMETER_MAX + 1):
                product = evaluations[a] * evaluations[b]
                if product <= 0:
                    continue
                square_root = math.isqrt(product)
                if square_root * square_root != product:
                    continue
                raw_pairs.append((a, b, evaluations[a], evaluations[b], square_root))
        if not raw_pairs:
            continue

        quotient = ring_q(coefficients)
        if (
            quotient.degree() != 12
            or not quotient.is_monic()
            or not quotient.is_irreducible()
        ):
            raise ArithmeticError("certified quotient failed exact polynomial gates")
        action = action_by_quotient[int(source["quotientT12"])]
        signature_targets = action[
            "sourceSignatureToPossibleTargetSignatures"
        ]
        for a, b, q_a, q_b, square_root in raw_pairs:
            signature = source_signature(quotient, a, b)
            live_targets = [
                {
                    "label": str(action["targetLabel"]),
                    "r": int(target_r),
                }
                for target_r in signature_targets.get(str(signature), [])
                if (str(action["targetLabel"]), int(target_r)) in current_tc0
            ]
            if not live_targets:
                continue
            collisions.append(
                {
                    **source,
                    "a": a,
                    "b": b,
                    "qAtA": str(q_a),
                    "qAtB": str(q_b),
                    "qAtAqAtBSquareRoot": str(square_root),
                    "sourceR": signature,
                    "liveTargets": live_targets,
                    "coverage": len(
                        {(row["label"], row["r"]) for row in live_targets}
                    ),
                }
            )

    collisions.sort(
        key=lambda row: (
            -int(row["coverage"]),
            int(row["inputCoefficientHeight"]),
            max(abs(int(row["a"])), abs(int(row["b"]))),
            abs(int(row["a"])) + abs(int(row["b"])),
            row["canonicalQuotientSha256"],
            int(row["a"]),
            int(row["b"]),
        )
    )
    plan["corpusGate"]["acceptedEvenRowsForLiveActions"] = sum(
        len(row["provenance"]) for row in corpus.values()
    )
    plan["corpusGate"]["canonicalDistinctQuotientsForLiveActions"] = len(corpus)
    plan["collisionCasesWithLiveSignatureCoverage"] = len(collisions)
    plan["rankedCollisionCases"] = collisions
    plan["selectionRule"] = (
        "maximize distinct current-tc0 pair coverage; then minimum quotient "
        "coefficient height, parameter height, canonical quotient hash, a, b"
    )
    atomic_write(PLAN, json.dumps(plan, indent=2, sort_keys=True) + "\n")

    results = []
    exact_hits = []
    seen_transformed_hashes = set()
    catalog_cache = {}
    profile_cache = {}
    for candidate in collisions:
        if len(seen_transformed_hashes) >= MAXIMUM_DISTINCT_CASES:
            break
        quotient = ring_q(
            [ZZ(value) for value in candidate["canonicalQuotientLine"].split(",")]
        )
        transformed = resultant_transform(
            quotient, int(candidate["a"]), int(candidate["b"])
        )
        transformed_line = coefficient_line(transformed)
        transformed_hash = sha_text(transformed_line)
        if transformed_hash in seen_transformed_hashes:
            continue
        seen_transformed_hashes.add(transformed_hash)
        result = {
            "casePosition": len(seen_transformed_hashes),
            "a": int(candidate["a"]),
            "b": int(candidate["b"]),
            "quotientT12": int(candidate["quotientT12"]),
            "quotientCoefficientLine": candidate["canonicalQuotientLine"],
            "quotientCoefficientSha256": candidate[
                "canonicalQuotientSha256"
            ],
            "qAtA": candidate["qAtA"],
            "qAtB": candidate["qAtB"],
            "qAtAqAtBSquareRoot": candidate["qAtAqAtBSquareRoot"],
            "normRelationExact": True,
            "predictedSourceR": int(candidate["sourceR"]),
            "liveTargets": candidate["liveTargets"],
            "transformed": {
                "coefficientLine": transformed_line,
                "coefficientSha256": transformed_hash,
                "degree": int(transformed.degree()),
                "monic": bool(transformed.is_monic()),
                "irreducible": bool(transformed.is_irreducible()),
            },
        }
        if (
            transformed.degree() != 12
            or not transformed.is_monic()
            or not result["transformed"]["irreducible"]
        ):
            result["transformed"]["factorDegrees"] = sorted(
                int(factor.degree())
                for factor, exponent in transformed.factor()
                for _ in range(int(exponent))
            )
            result["status"] = "nonseparating_or_reducible_resultant"
            results.append(result)
            atomic_write(RESULTS, render_jsonl(results))
            print(
                json.dumps(
                    {
                        "event": "case_checkpointed",
                        "position": result["casePosition"],
                        "status": result["status"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            continue

        ring_x = PolynomialRing(ZZ, f"x{len(results)+1}")
        x = ring_x.gen()
        source_polynomial = ring_x(transformed)(x**2)
        source_line = coefficient_line(source_polynomial)
        result["sourcePolynomial"] = {
            "coefficientLine": source_line,
            "coefficientSha256": sha_text(source_line),
            "degree": int(source_polynomial.degree()),
            "monic": bool(source_polynomial.is_monic()),
            "irreducible": bool(source_polynomial.is_irreducible()),
            "r": int(source_polynomial.number_of_real_roots()),
        }
        if (
            source_polynomial.degree() != 24
            or not source_polynomial.is_monic()
            or not result["sourcePolynomial"]["irreducible"]
            or result["sourcePolynomial"]["r"] != int(candidate["sourceR"])
        ):
            result["status"] = "source_polynomial_exact_gate_miss"
            results.append(result)
            atomic_write(RESULTS, render_jsonl(results))
            continue

        canonical_source = canonical_by_quotient[int(candidate["quotientT12"])]
        classification = exact_source_classification(
            source_polynomial,
            int(candidate["quotientT12"]),
            str(canonical_source["label"]),
            catalog_cache,
            profile_cache,
        )
        result["sourceClassification"] = classification
        if not classification["exact"]:
            result["status"] = "source_group_unclassified_fail_closed"
            results.append(result)
            atomic_write(RESULTS, render_jsonl(results))
            continue

        pair_resolvent = transformed.symmetric_power(2, monic=True)
        factors = [
            (factor, int(exponent))
            for factor, exponent in pair_resolvent.factor()
        ]
        degree_twelve = [
            factor
            for factor, exponent in factors
            if factor.degree() == 12 and exponent == 1
        ]
        result["pairProductFactorDegrees"] = [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ]
        if len(degree_twelve) != 1:
            result["status"] = "unique_degree12_pair_factor_miss"
            results.append(result)
            atomic_write(RESULTS, render_jsonl(results))
            continue

        final_polynomial = ring_x(degree_twelve[0])(x**2)
        final_line = coefficient_line(final_polynomial)
        final_hash = sha_text(final_line)
        action = action_by_quotient[int(candidate["quotientT12"])]
        target_pair = (
            str(action["targetLabel"]),
            int(final_polynomial.number_of_real_roots()),
        )
        ledger_occurrences = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (final_hash,),
            ).fetchone()[0]
        )
        result["finalCandidate"] = {
            "coefficientLine": final_line,
            "coefficientSha256": final_hash,
            "degree": int(final_polynomial.degree()),
            "monic": bool(final_polynomial.is_monic()),
            "irreducible": bool(final_polynomial.is_irreducible()),
            "r": target_pair[1],
            "ledgerCoefficientOccurrences": ledger_occurrences,
        }
        result["exactAction"] = action
        result["target"] = {
            "label": target_pair[0],
            "r": target_pair[1],
            "t": int(action["targetT"]),
        }
        if (
            final_polynomial.degree() == 24
            and final_polynomial.is_monic()
            and result["finalCandidate"]["irreducible"]
            and target_pair in current_tc0
            and ledger_occurrences == 0
        ):
            result["status"] = "exact_tc0_hit"
            exact_hits.append(result)
        else:
            result["status"] = "exact_resolved_not_novel_live_tc0"
        results.append(result)
        atomic_write(RESULTS, render_jsonl(results))
        print(
            json.dumps(
                {
                    "event": "case_checkpointed",
                    "position": result["casePosition"],
                    "status": result["status"],
                    "target": f"{target_pair[0]}/r{target_pair[1]}",
                },
                sort_keys=True,
            ),
            flush=True,
        )

    connection.close()
    status_counts = Counter(row["status"] for row in results)
    summary = {
        "status": (
            "complete_exact_tc0_hits"
            if exact_hits
            else "complete_no_exact_tc0_hits"
        ),
        "actionCensusQuotientActions": len(action_census),
        "currentTc0ActionClasses": len(live_action_routes),
        "corpusAcceptedRowsForLiveActions": plan["corpusGate"].get(
            "acceptedEvenRowsForLiveActions", 0
        ),
        "corpusCanonicalDistinctQuotientsForLiveActions": len(corpus),
        "collisionCasesWithLiveSignatureCoverage": len(collisions),
        "executedCoefficientDistinctCases": len(seen_transformed_hashes),
        "exactHits": len(exact_hits),
        "hitTargets": [row["target"] for row in exact_hits],
        "statusHistogram": dict(sorted(status_counts.items())),
        "plan": str(PLAN.relative_to(ROOT)),
        "planSha256": sha_path(PLAN),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha_path(RESULTS) if RESULTS.exists() else None,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "hardWallSeconds": HARD_WALL_SECONDS,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_write(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(HARD_WALL_SECONDS)
    try:
        raise SystemExit(main())
    finally:
        signal.alarm(0)

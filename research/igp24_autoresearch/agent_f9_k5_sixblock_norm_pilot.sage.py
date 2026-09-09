#!/usr/bin/env sage -python
"""Strict two-source k=5 pilot via exact 6+6 subfield norms.

The relevant size-12 five-subset orbits are the five-of-six subsets inside a
G-invariant 6+6 block system.  If q=f_1*f_2 over the corresponding quadratic
subfield and C_i=f_i(0), the six products in that block are C_i/beta.  Their
polynomial is y^6*f_i(C_i/y)/C_i.  Multiplying the two conjugate sextics gives
the required degree-12 factor directly, without constructing the degree-792
fifth symmetric power.  No network or submission calls are made.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from sage.all import GF, Matrix, NumberField, PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes.jsonl"
K2_ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
K2_ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
FROZEN = DATA / "agent_f7_frozen_23018_live_pairs.jsonl"
PLAN = DATA / "agent_f9_k5_incidence_pilot_plan.json"
RESULTS = DATA / "agent_f9_k5_sixblock_norm_pilot_results.jsonl"
SUMMARY = DATA / "agent_f9_k5_sixblock_norm_pilot_summary.json"
MANIFEST = OUTBOX / "agent_f9_k5_sixblock_norm_live.txt"


EXPECTED_SHA256 = {
    ACTIONS: "d191f5d5ed251f2e7af6ede4b930da24a75ca6593b62f7f1e6e01f061abc4d00",
    ROUTES: "487e9d8e8dd83aae279af87740d1b6d0836d7dcff380c524791474571b54c5b2",
    K2_ACTIONS: "6304b195a109317db89413300a51567c9f7c09c6227616591cf15fd344031f40",
    K2_ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN: "e15386893d951acf921d2cce598accf7500e6f306b6ab7dbb73d0e3e5fc7eab2",
    PLAN: "bef3bec7b99665233d89f3b6f8c116b0d8fb8680ecc601dfce21c1cb0f10926f",
}

EXPECTED_SOURCES = {
    ("24T22560", 672): {
        "coefficientSha256": "2bd40497267e50aafb87cacea6f842c486b9339a7da9148ba3ae3700a4cad58c",
        "pair": ("24T22560", 20),
        "quotientPolynomialSha256": "f980c9de0d0b7dcf1d5bcd72a2070da3222bfab2671e66720e13b7bab01502dc",
    },
    ("24T22562", 165): {
        "coefficientSha256": "3ea4f28f217e288ee1c7deac9a85d4c3334f9d30d7e3d55505d298ea18addf19",
        "pair": ("24T22562", 20),
        "quotientPolynomialSha256": "e85d3e98954ba7b4ebb6c15763204b86602a8408d130b2aa5b4ba10a5b7551d8",
    },
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, payload: dict) -> str:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return sha256_bytes(rendered.encode())


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return sha256_bytes(rendered.encode())


def polynomial_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def canonical_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def outbox_hashes() -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        if path == MANIFEST:
            continue
        for line in path.read_text(errors="ignore").splitlines():
            try:
                values = [ZZ(value.strip()) for value in line.split(",")]
            except Exception:
                continue
            if len(values) == 25 and values[-1] == 1:
                normalized = ",".join(str(value) for value in values)
                hashes.add(sha256_bytes(normalized.encode()))
    return hashes


def target_state(connection: sqlite3.Connection, label: str, r: int) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        raise ValueError(f"target row missing for {label}/r{r}")
    return {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }


def six_block_partition(orbit: list[list[int]]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    observed = {tuple(sorted(int(value) for value in subset)) for subset in orbit}
    matches = []
    universe = set(range(12))
    for block in itertools.combinations(range(12), 6):
        if 0 not in block:
            continue
        first = set(block)
        second = universe.difference(first)
        expected = {
            tuple(sorted(subset))
            for side in (first, second)
            for subset in itertools.combinations(sorted(side), 5)
        }
        if expected == observed:
            matches.append((tuple(sorted(first)), tuple(sorted(second))))
    if len(matches) != 1:
        raise ValueError(f"five-subset orbit has {len(matches)} compatible 6+6 systems")
    return matches[0]


def reciprocal_scaled_sextic(factor, variable):
    constant = factor[0]
    if not constant:
        raise ValueError("sextic factor has zero constant term")
    transformed = sum(
        factor[index] * constant ** index * variable ** (6 - index) / constant
        for index in range(7)
    )
    if transformed.degree() != 6 or transformed[6] != 1:
        raise ValueError("reciprocal-scaled sextic identity failed")
    return transformed


def rational_integer_polynomial(polynomial, name: str):
    rational_coefficients = []
    for coefficient in polynomial.list():
        try:
            rational_coefficients.append(QQ(coefficient))
        except (TypeError, ValueError) as exc:
            raise ValueError("quadratic norm polynomial is not rational") from exc
    if any(value.denominator() != 1 for value in rational_coefficients):
        raise ValueError("quadratic norm polynomial is rational but nonintegral")
    ring = PolynomialRing(ZZ, name)
    return ring([ZZ(value) for value in rational_coefficients])


def main() -> int:
    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch for {path}")

    plan = json.loads(PLAN.read_text())
    actions = load_jsonl(ACTIONS)
    routes = load_jsonl(ROUTES)
    frozen_rows = load_jsonl(FROZEN)
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    k5_actions_by_label = defaultdict(list)
    for action in actions:
        if int(action["subsetSize"]) == 5 and action["sourceLabel"] in {
            key[0] for key in EXPECTED_SOURCES
        }:
            k5_actions_by_label[str(action["sourceLabel"])].append(action)
    routes_by_source = defaultdict(list)
    for route in routes:
        if int(route["action"]["subsetSize"]) == 5:
            key = (str(route["source"]["label"]), int(route["source"]["polynomialIndex"]))
            if key in EXPECTED_SOURCES:
                routes_by_source[key].append(route)

    selected = []
    for source_plan in plan["pilotSourcesInOrder"]:
        key = (str(source_plan["sourceLabel"]), int(source_plan["polynomialIndex"]))
        expected = EXPECTED_SOURCES[key]
        source_routes = routes_by_source[key]
        source_actions = k5_actions_by_label[key[0]]
        if len(source_actions) != 3 or not source_routes:
            raise ValueError("planned source lacks exactly three k=5 actions and a live route")
        if {row["targetLabel"] for row in source_actions} != {key[0]}:
            raise ValueError("three k=5 actions do not share the exact self target")
        signature_maps = {
            json.dumps(row["sourceSignatureToPossibleTargetSignatures"], sort_keys=True)
            for row in source_actions
        }
        if len(signature_maps) != 1:
            raise ValueError("three k=5 actions have different signature frontiers")
        partitions = [six_block_partition(row["subsetOrbit"]) for row in source_actions]
        if len(set(partitions)) != 3:
            raise ValueError("three k=5 actions do not give three distinct 6+6 systems")
        for row in source_actions:
            incidence = Matrix(GF(2), row["incidenceRows"])
            if (
                incidence.rank() != 12
                or int(row["sourceKummerRank"]) != 11
                or int(row["targetKummerRank"]) != 11
            ):
                raise ValueError("k=5 incidence-module automorphism certificate changed")
        first = source_routes[0]
        quotient_lines = {str(row["sourceQuotientLine"]) for row in source_routes}
        quotient_hashes = {str(row["sourceQuotientPolynomialSha256"]) for row in source_routes}
        eligible_pairs = {
            (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
            for row in source_routes
        }
        if (
            first["source"]["coefficientSha256"] != expected["coefficientSha256"]
            or quotient_hashes != {expected["quotientPolynomialSha256"]}
            or len(quotient_lines) != 1
            or expected["pair"] not in eligible_pairs
        ):
            raise ValueError("planned source provenance or live pair changed")
        selected.append(
            {
                "actions": source_actions,
                "eligiblePairs": sorted(eligible_pairs),
                "partitions": partitions,
                "source": first["source"],
                "sourceQuotientLine": next(iter(quotient_lines)),
                "sourceQuotientPolynomialSha256": next(iter(quotient_hashes)),
            }
        )
    if len(selected) != 2 or len(selected) > int(plan["pilotCap"]):
        raise ValueError("strict two-source pilot changed")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    known_hashes.update(outbox_hashes())
    result_rows = []
    exact_live_hits = []
    obstruction = None

    for position, route in enumerate(selected, start=1):
        source = route["source"]
        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("historical source missing from ledger")
        source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
        if (
            source_row["coefficient_hash"] != source["coefficientSha256"]
            or source_row["label"] != source["label"]
            or int(source_row["r"]) != int(source["r"])
            or source_row["status"] != "accepted"
            or int(source_row["scoreable"]) != 1
            or any(source_coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("historical source provenance mismatch")

        ring_q = PolynomialRing(ZZ, f"q{position}")
        quotient_line = route["sourceQuotientLine"]
        if sha256_bytes(quotient_line.encode()) != route["sourceQuotientPolynomialSha256"]:
            raise ValueError("source quotient hash mismatch")
        quotient = ring_q([ZZ(value) for value in quotient_line.split(",")])
        if source_coefficients[::2] != quotient.list():
            raise ValueError("source polynomial does not equal q(x^2)")
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("source quotient is not monic irreducible degree 12")

        try:
            started = time.monotonic()
            quotient_field = NumberField(quotient, f"b{position}")
            quadratic_subfields = quotient_field.subfields(2)
            subfield_seconds = time.monotonic() - started
            if len(quadratic_subfields) != 3:
                raise RuntimeError(f"found {len(quadratic_subfields)} quadratic subfields, expected 3")
            degree_twelve_factors = []
            subfield_rows = []
            for subfield_index, subfield_row in enumerate(quadratic_subfields, start=1):
                subfield = subfield_row[0]
                defining = subfield.defining_polynomial()
                if defining.degree() != 2 or QQ(defining[0]) != QQ(quotient[0]):
                    raise RuntimeError("quadratic block-product polynomial has wrong norm")
                ring_subfield = PolynomialRing(subfield, f"z{position}_{subfield_index}")
                z = ring_subfield.gen()
                started_factor = time.monotonic()
                factorization = ring_subfield(quotient).factor()
                factor_seconds = time.monotonic() - started_factor
                sextics = [factor for factor, exponent in factorization if factor.degree() == 6 and int(exponent) == 1]
                if len(sextics) != 2 or sextics[0] * sextics[1] != ring_subfield(quotient):
                    raise RuntimeError("q does not factor exactly into two conjugate sextics")
                if sextics[0][0] * sextics[1][0] != subfield(quotient[0]):
                    raise RuntimeError("sextic block products do not have norm q(0)")
                transformed = [reciprocal_scaled_sextic(factor, z) for factor in sextics]
                norm_polynomial = rational_integer_polynomial(
                    transformed[0] * transformed[1], f"h{position}_{subfield_index}"
                )
                if norm_polynomial.degree() != 12 or not norm_polynomial.is_monic():
                    raise RuntimeError("derived quadratic norm is not monic degree 12")
                degree_twelve_factors.append(norm_polynomial)
                subfield_rows.append(
                    {
                        "derivedFactorCoefficientLine": polynomial_line(norm_polynomial),
                        "derivedFactorSha256": sha256_bytes(polynomial_line(norm_polynomial).encode()),
                        "factorizationSeconds": factor_seconds,
                        "quadraticDefiningPolynomial": ",".join(str(value) for value in defining.list()),
                        "quadraticDiscriminant": str(ZZ(defining.discriminant())),
                        "sexticConstantTerms": [str(factor[0]) for factor in sextics],
                    }
                )
            if len({polynomial_line(factor) for factor in degree_twelve_factors}) != 3:
                raise RuntimeError("three quadratic subfields did not yield three distinct factors")
        except (RuntimeError, ArithmeticError) as exc:
            obstruction = {
                "error": f"{type(exc).__name__}: {exc}",
                "position": position,
                "reason": "exact 6+6 quadratic-subfield norm gate failed",
                "source": source,
            }
            print(json.dumps({"event": "k5_obstruction", **obstruction}, sort_keys=True), flush=True)
            break

        candidate_rows = []
        signature_map = route["actions"][0]["sourceSignatureToPossibleTargetSignatures"]
        possible_signatures = {int(value) for value in signature_map[str(source["r"])]}
        for factor_index, factor in enumerate(degree_twelve_factors, start=1):
            ring_x = PolynomialRing(ZZ, f"x{position}_{factor_index}")
            x = ring_x.gen()
            candidate = ring_x(factor)(x**2)
            if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
                raise ValueError("derived k=5 candidate is not monic irreducible degree 24")
            signature = int(candidate.number_of_real_roots())
            if signature not in possible_signatures:
                raise ValueError("candidate signature contradicts all three exact k=5 actions")
            target_pair = (str(source["label"]), signature)
            state = target_state(connection, *target_pair)
            coefficient_line = canonical_line(candidate)
            coefficient_sha = sha256_bytes(coefficient_line.encode())
            fresh_hash = coefficient_sha not in known_hashes
            field_disc = abs(ZZ(NumberField(candidate, f"a{position}_{factor_index}").absolute_discriminant()))
            live_hit = (
                target_pair in set(route["eligiblePairs"])
                and target_pair in frozen
                and state["teamCount"] == 0
                and not state["baseline"]
                and not state["owned"]
                and fresh_hash
            )
            candidate_row = {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha,
                "derivedFactorSha256": sha256_bytes(polynomial_line(factor).encode()),
                "fieldDiscriminantAbs": str(field_disc),
                "freshHash": fresh_hash,
                "irreducible": True,
                "liveHit": live_hit,
                "localTargetState": state,
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "r": signature,
                "target": {"label": target_pair[0], "r": target_pair[1]},
            }
            candidate_rows.append(candidate_row)
            known_hashes.add(coefficient_sha)
            if live_hit:
                exact_live_hits.append(candidate_row)

        result = {
            "candidateRows": candidate_rows,
            "eligiblePairs": [f"{label}/r{r}" for label, r in route["eligiblePairs"]],
            "exactMechanismCertificate": {
                "actions": route["actions"],
                "assignmentFree": True,
                "degree792Constructed": False,
                "partitions": [[list(side) for side in partition] for partition in route["partitions"]],
                "proof": (
                    "Each action orbit is all five-of-six subsets in one exact 6+6 block "
                    "system. Over its quadratic fixed field q=f_1*f_2 with sextic "
                    "constants C_i. The identity prod_beta(y-C_i/beta)="
                    "y^6*f_i(C_i/y)/C_i is exact; multiplying conjugates produces the "
                    "displayed rational degree-12 factor. All three actions have the same "
                    "exact self target label and signature map, so assignment is unnecessary."
                ),
                "quadraticSubfieldSeconds": subfield_seconds,
                "quadraticSubfields": subfield_rows,
            },
            "networkCalls": 0,
            "position": position,
            "source": source,
            "status": "exact_live_hit" if any(row["liveHit"] for row in candidate_rows) else "exact_miss_live_signature",
            "submissionCalls": 0,
        }
        result_rows.append(result)
        print(
            json.dumps(
                {
                    "event": "source_complete",
                    "live": [
                        f"{row['target']['label']}/r{row['target']['r']}:{row['liveHit']}"
                        for row in candidate_rows
                    ],
                    "position": position,
                    "sourceLabel": source["label"],
                    "subfieldSeconds": subfield_seconds,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    connection.close()

    best_by_pair = {}
    for row in exact_live_hits:
        pair = (row["target"]["label"], int(row["target"]["r"]))
        current = best_by_pair.get(pair)
        if current is None or ZZ(row["fieldDiscriminantAbs"]) < ZZ(current["fieldDiscriminantAbs"]):
            best_by_pair[pair] = row
    staged = [best_by_pair[pair] for pair in sorted(best_by_pair)]
    manifest_text = "".join(f"{row['coefficientLine']}\n" for row in staged)
    temporary_manifest = MANIFEST.with_suffix(MANIFEST.suffix + ".tmp")
    temporary_manifest.write_text(manifest_text, encoding="utf-8")
    temporary_manifest.replace(MANIFEST)
    results_sha = write_jsonl(RESULTS, result_rows)

    summary = {
        "artifactSha256": {
            "manifest": sha256_path(MANIFEST),
            "plan": sha256_path(PLAN),
            "results": results_sha,
        },
        "degree792Constructed": False,
        "derivedDegree12Factors": sum(len(row["candidateRows"]) for row in result_rows),
        "exactLiveHits": len(exact_live_hits),
        "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS",
        "inputSha256": input_hashes,
        "k5NovelPairsBeyondK2ToK4": 2,
        "k6NovelPairsBeyondK2ToK4": 0,
        "mechanism": "k5-sixblock-quadratic-subfield-norm-reduction",
        "networkCalls": 0,
        "obstruction": obstruction,
        "pilotSourcesCompleted": len(result_rows),
        "pilotSourcesPlanned": len(selected),
        "realizedPairs": sorted(
            f"{candidate['target']['label']}/r{candidate['target']['r']}"
            for result in result_rows
            for candidate in result["candidateRows"]
        ),
        "stagedPairs": [f"{label}/r{r}" for label, r in sorted(best_by_pair)],
        "stagedPolynomials": len(staged),
        "status": (
            "exact_hits_staged"
            if staged
            else "blocked_subfield_norm_gate"
            if obstruction
            else "blocked_signature_miss_on_capped_pilot"
        ),
        "submissionCalls": 0,
    }
    summary_sha = write_json(SUMMARY, summary)
    print(json.dumps({"summary": str(SUMMARY), "summarySha256": summary_sha, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

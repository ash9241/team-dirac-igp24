#!/usr/bin/env sage -python
"""F10 class-group/Selmer reopening audit for the frozen F1 census.

This is deliberately not an auxiliary-prime search.  It reconstructs every
completed character-search support attached to a canonical degree-12 F1
field, computes the exact K(S,2) space on the union support, and compares it
with the span of the S-unit squareclasses from the supports that were actually
tested.  Class-group 2-torsion is exposed by the standard exact sequence

    O_{K,S}^*/O_{K,S}^{*2} -> K(S,2) -> Cl_{K,S}[2].

When the underlying GF(2) quotient is zero, its H-module quotient is zero for
every possible H-action, so no group-action calculation or pilot is needed.
Positive underlying quotients are recorded but are not advanced without a
separate exact H-module and frozen-live target certificate.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    NumberField,
    PolynomialRing,
    QQ,
    RealIntervalField,
    ZZ,
    prod,
    proof,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = Path("/private/tmp/igp24_gold_c_stable_20260721_pilot2.sqlite3")
DEFAULT_CENSUS = DATA / "agent_gold_c_character_field_census.jsonl"
DEFAULT_LIVE = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_INVENTORY = (
    DATA / "agent_gold_c_f10_class_group_selmer_tested_support_inventory.jsonl"
)
DEFAULT_AUDIT = DATA / "agent_gold_c_f10_class_group_selmer_reopen_audit.json"
DEFAULT_FREEZE = DATA / "agent_gold_c_f10_class_group_selmer_reopen_freeze.json"
DEFAULT_CACHE = Path("/private/tmp/igp24_f10_exact_selmer_cache_v2")
DEFAULT_CERTIFIED_PILOT = DATA / "agent_gold_c_f10_q97_core103_r6_exact_pilot.json"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rendered_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def source_key(source: dict) -> tuple[str, int] | None:
    try:
        return str(source["submissionId"]), int(source["polynomialIndex"])
    except (KeyError, TypeError, ValueError):
        return None


def canonical_hash_from_db(
    connection: sqlite3.Connection,
    key: tuple[str, int],
) -> str | None:
    row = connection.execute(
        "SELECT coefficients FROM polynomials "
        "WHERE submission_id=? AND polynomial_index=?",
        key,
    ).fetchone()
    if row is None:
        return None
    coefficients = [ZZ(value) for value in str(row[0]).split(",")]
    if len(coefficients) != 25 or any(
        coefficients[index] for index in range(1, 25, 2)
    ):
        return None
    quotient = PolynomialRing(QQ, "y")(coefficients[::2])
    if quotient.degree() != 12 or not quotient.is_irreducible():
        return None
    field = NumberField(quotient, "a")
    reduced = PolynomialRing(ZZ, "z")(field.pari_polynomial("z").polredabs())
    line = ",".join(str(value) for value in reduced)
    return hashlib.sha256(line.encode()).hexdigest()


def result_candidates(path: Path, payload: dict) -> list[dict]:
    """Extract completed S-unit support computations from one JSON artifact."""
    rows = []
    audit = payload.get("audit")
    search = payload.get("search")
    if (
        isinstance(audit, dict)
        and isinstance(search, dict)
        and isinstance(audit.get("source"), dict)
        and isinstance(search.get("corePrimes"), list)
        and isinstance(search.get("auxiliaryPrimes"), list)
    ):
        support = sorted(
            {
                *[int(value) for value in search["corePrimes"]],
                *[int(value) for value in search["auxiliaryPrimes"]],
            }
        )
        rows.append(
            {
                "kind": "character_kernel_search_output",
                "searchStatus": str(search.get("status")),
                "source": audit["source"],
                "support": support,
            }
        )

    # The earlier exact feasibility stage computed the complete S-unit image
    # for matrix.rationalPrimes but did not use the character-search schema.
    source = payload.get("source")
    matrix = payload.get("matrix")
    if (
        isinstance(source, dict)
        and isinstance(matrix, dict)
        and isinstance(matrix.get("rationalPrimes"), list)
        and isinstance(payload.get("results"), list)
    ):
        rows.append(
            {
                "kind": "character_feasibility_output",
                "searchStatus": "feasibility_matrix_completed",
                "source": source,
                "support": sorted(
                    {int(value) for value in matrix["rationalPrimes"]}
                ),
            }
        )
    return rows


def build_inventory(census: list[dict], db: Path) -> tuple[list[dict], dict]:
    census_hashes = {str(row["fieldCanonicalSha256"]) for row in census}
    direct_map = {}
    for row in census:
        field_hash = str(row["fieldCanonicalSha256"])
        for representative in row["representatives"]:
            key = source_key(representative["source"])
            if key is not None:
                prior = direct_map.setdefault(key, field_hash)
                if prior != field_hash:
                    raise ValueError("one source maps to two canonical census fields")

    candidates = []
    for name in sorted(glob.glob(str(DATA / "**" / "*.json"), recursive=True)):
        path = Path(name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for candidate in result_candidates(path, payload):
            key = source_key(candidate["source"])
            if key is None:
                continue
            candidates.append({**candidate, "path": path, "sourceKey": key})

    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    derived_map = {}
    try:
        for key in sorted({row["sourceKey"] for row in candidates} - direct_map.keys()):
            canonical_hash = canonical_hash_from_db(connection, key)
            if canonical_hash in census_hashes:
                derived_map[key] = canonical_hash
    finally:
        connection.close()

    inventory = []
    outside_census = 0
    for candidate in candidates:
        key = candidate["sourceKey"]
        field_hash = direct_map.get(key, derived_map.get(key))
        if field_hash not in census_hashes:
            outside_census += 1
            continue
        path = candidate["path"]
        inventory.append(
            {
                "artifact": str(path.relative_to(ROOT)),
                "artifactSha256": sha256_path(path),
                "fieldCanonicalSha256": field_hash,
                "kind": candidate["kind"],
                "rationalPrimeSupport": candidate["support"],
                "searchStatus": candidate["searchStatus"],
                "source": {
                    "polynomialIndex": key[1],
                    "submissionId": key[0],
                },
                "sourceToCanonicalMap": (
                    "census_representative" if key in direct_map else "exact_polredabs"
                ),
            }
        )
    inventory.sort(
        key=lambda row: (
            row["fieldCanonicalSha256"],
            row["rationalPrimeSupport"],
            row["artifact"],
        )
    )
    summary = {
        "acceptedCompletedArtifacts": len(inventory),
        "candidateArtifactsOutsideF1Census": outside_census,
        "censusRepresentativeSourceKeys": len(direct_map),
        "exactPolredabsAdditionalSourceKeys": len(derived_map),
    }
    return inventory, summary


def element_key(value) -> tuple[str, ...]:
    return tuple(str(coefficient) for coefficient in value.list())


def element_payload(field, representative, vector=None, basis_index=None) -> dict:
    factors = list(field.ideal(representative).factor())
    payload = {
        "coordinatesInCanonicalPowerBasis": [
            str(value) for value in representative.list()
        ],
        "exactIdealFactorization": [
            {"exponent": int(exponent), "primeIdeal": str(ideal)}
            for ideal, exponent in factors
        ],
        "rationalNorm": str(representative.norm()),
        "representative": str(representative),
    }
    if vector is not None:
        payload["selmerVector"] = [int(value) for value in vector]
    if basis_index is not None:
        payload["basisIndex"] = int(basis_index)
    return payload


def exact_sign_rows(field, elements: list) -> tuple[list[list[int]], int]:
    for precision in (128, 256, 512, 1024, 2048):
        embeddings = field.embeddings(RealIntervalField(precision))
        evaluated = [[embedding(value) for value in elements] for embedding in embeddings]
        if all(not value.contains_zero() for row in evaluated for value in row):
            return [
                [int(value < 0) for value in row]
                for row in evaluated
            ], precision
    raise ValueError("could not certify every generator sign by interval arithmetic")


def computed_s_class_upper_bound(field, prime_ideals: list) -> dict:
    """Return an unconditional upper quotient for Cl_{K,S} and its 2-rank.

    PARI bnfcertify(...,1) proves that the true class group is a quotient of
    the computed group.  After quotienting by the images of S, the true
    S-class group remains a quotient of the computed S-class group.  Its
    mod-2 dimension is therefore bounded by C/(<S>+2C).
    """
    class_group = field.class_group(proof=False)
    bnf = field.pari_bnf(proof=False)
    if int(bnf.bnfcertify(1)) != 1:
        raise ValueError("PARI failed the unconditional class-group quotient certificate")
    invariants = [int(value) for value in class_group.invariants()]
    classes = [class_group(prime_ideal) for prime_ideal in prime_ideals]
    subgroup = class_group.subgroup(classes)
    quotient_order_upper = int(class_group.order() // subgroup.order())
    even_coordinates = [
        index for index, invariant in enumerate(invariants) if invariant % 2 == 0
    ]
    if even_coordinates and classes:
        image_matrix = Matrix(
            GF(2),
            [
                [int(value.exponents()[index]) & 1 for index in even_coordinates]
                for value in classes
            ],
        )
        support_image_rank = int(image_matrix.rank())
    else:
        support_image_rank = 0
    two_rank_upper = len(even_coordinates) - support_image_rank
    return {
        "bnfcertifyClassGroupQuotientFlag": 1,
        "bnfcertifyClassGroupQuotientPassed": True,
        "computedClassGroupInvariants": invariants,
        "computedClassGroupOrder": int(class_group.order()),
        "computedSClassGroupOrderUpperBound": quotient_order_upper,
        "computedSClassGroupTwoRankUpperBound": two_rank_upper,
        "supportImageMod2Rank": support_image_rank,
    }


def validate_selmer_generators(
    field,
    generators: list,
    union_prime_ideals: list,
    expected_dimension: int,
) -> dict:
    """Certify actual K(S,2) generators attain an unconditional upper bound."""
    if len(generators) != expected_dimension:
        raise ValueError(
            "candidate Selmer generator count does not attain the certified upper bound"
        )
    support_set = set(union_prime_ideals)
    ideals = [field.ideal(value) for value in generators]
    validity = []
    for ideal in ideals:
        factors = list(ideal.factor())
        valid = all(
            prime_ideal in support_set or int(exponent) % 2 == 0
            for prime_ideal, exponent in factors
        )
        validity.append(valid)
    if not all(validity):
        raise ValueError("a candidate Selmer generator has odd valuation outside S")

    sign_rows, sign_precision = exact_sign_rows(field, generators)
    valuation_rows = [
        [int(ideal.valuation(prime_ideal)) & 1 for ideal in ideals]
        for prime_ideal in union_prime_ideals
    ]
    visible_matrix = Matrix(GF(2), [*sign_rows, *valuation_rows])
    invisible_kernel = visible_matrix.right_kernel()
    if invisible_kernel.dimension() > 14:
        raise ValueError("sign/valuation kernel is too large for exhaustive square proof")
    tested_masks = []
    for vector in invisible_kernel:
        if not vector:
            continue
        representative = prod(
            (
                generator
                for generator, exponent in zip(generators, vector)
                if int(exponent)
            ),
            field(1),
        )
        if representative.is_square():
            raise ValueError("candidate Selmer generators have an exact square relation")
        tested_masks.append(
            [index for index, exponent in enumerate(vector) if int(exponent)]
        )
    return {
        "allOutsideSupportValuationsEven": True,
        "candidateGeneratorCount": len(generators),
        "exactNonSquareKernelMasks": tested_masks,
        "exactNonSquareTests": len(tested_masks),
        "intervalSignPrecisionBits": sign_precision,
        "signAndValuationKernelDimension": int(invisible_kernel.dimension()),
        "signAndValuationRank": int(visible_matrix.rank()),
        "upperBoundAttained": True,
    }


def audit_field(field_row: dict, inventory_rows: list[dict]) -> dict:
    coefficients = [
        ZZ(value) for value in str(field_row["fieldCanonicalPolynomial"]).split(",")
    ]
    polynomial = PolynomialRing(QQ, "x")(coefficients)
    if polynomial.degree() != 12 or not polynomial.is_irreducible():
        raise ValueError("canonical census polynomial is not irreducible degree 12")
    if polynomial.number_of_real_roots() != 12:
        raise ValueError("canonical census field is not totally real")
    field = NumberField(polynomial, "a")
    discriminant = abs(ZZ(field.discriminant()))
    if str(discriminant) != str(field_row["fieldDiscAbs"]):
        raise ValueError("canonical field discriminant does not match the census")

    supports = sorted(
        {
            tuple(int(value) for value in row["rationalPrimeSupport"])
            for row in inventory_rows
        },
        key=lambda values: (len(values), values),
    )
    if not supports:
        raise ValueError("canonical field has no completed tested support")
    union_primes = sorted({prime for support in supports for prime in support})
    union_prime_ideals = [
        prime_ideal
        for prime in union_primes
        for prime_ideal in field.primes_above(prime)
    ]

    print(
        json.dumps(
            {
                "event": "f10_exact_two_primary_certificate_started",
                "fieldCanonicalSha256": field_row["fieldCanonicalSha256"],
                "unionPrimeIdealSupportCount": len(union_prime_ideals),
                "unionRationalPrimeSupportCount": len(union_primes),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    class_upper = computed_s_class_upper_bound(field, union_prime_ideals)
    unit_squareclass_dimension = 12  # totally real degree 12: rank 11 plus -1
    selmer_upper_dimension = (
        unit_squareclass_dimension
        + len(union_prime_ideals)
        + class_upper["computedSClassGroupTwoRankUpperBound"]
    )

    # Construction may use the certified quotient data before full unit-index
    # certification.  Every resulting algebraic element and every coordinate
    # is independently verified below, so no GRH assumption enters the result.
    selmer, selmer_generators, from_selmer, to_selmer = field.selmer_space(
        union_prime_ideals, ZZ(2), proof=False
    )
    selmer_generators = list(selmer_generators)
    if int(selmer.dimension()) != len(selmer_generators):
        raise ValueError("Selmer candidate vector space and generator counts disagree")
    independence = validate_selmer_generators(
        field,
        selmer_generators,
        union_prime_ideals,
        selmer_upper_dimension,
    )
    print(
        json.dumps(
            {
                "event": "f10_exact_two_primary_certificate_finished",
                "fieldCanonicalSha256": field_row["fieldCanonicalSha256"],
                "selmerDimension": selmer_upper_dimension,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    coordinate_cache = {}
    coordinate_verifications = 0

    def exact_coordinate(value):
        nonlocal coordinate_verifications
        key = element_key(value)
        cached = coordinate_cache.get(key)
        if cached is not None:
            return cached
        vector = to_selmer(value)
        quotient = value / from_selmer(vector)
        if not quotient.is_square():
            raise ValueError("a tested S-unit coordinate failed exact square verification")
        coordinate_verifications += 1
        coordinate_cache[key] = vector
        return vector

    tested_vectors = []
    schedule_audit = []
    for support in supports:
        schedule_prime_ideals = [
            prime_ideal
            for prime in support
            for prime_ideal in field.primes_above(prime)
        ]
        schedule_set = set(schedule_prime_ideals)
        s_units = field.S_unit_group(
            proof=False, S=prod(support) if support else 1
        )
        generators = list(s_units.gens_values())
        for value in generators:
            if any(
                prime_ideal not in schedule_set
                for prime_ideal, exponent in field.ideal(value).factor()
                if int(exponent)
            ):
                raise ValueError("a tested generator is not an exact S-unit")
        vectors = [exact_coordinate(value) for value in generators]
        image = selmer.subspace(vectors)
        before = selmer.subspace(tested_vectors).dimension() if tested_vectors else 0
        tested_vectors.extend(vectors)
        cumulative = selmer.subspace(tested_vectors)
        schedule_audit.append(
            {
                "cumulativeTestedSpanDimensionAfter": int(cumulative.dimension()),
                "cumulativeTestedSpanDimensionBefore": int(before),
                "primeIdealSupportCount": len(schedule_prime_ideals),
                "rationalPrimeSupport": list(support),
                "sUnitGeneratorCount": len(generators),
                "sUnitImageDimension": int(image.dimension()),
            }
        )
    tested_span = selmer.subspace(tested_vectors)

    extra_basis = []
    enlarged = tested_span
    for vector in selmer.basis():
        if vector in enlarged:
            continue
        extra_basis.append(vector)
        enlarged = selmer.subspace([*enlarged.basis(), vector])
    strict_dimension = int(selmer_upper_dimension - tested_span.dimension())
    if len(extra_basis) != strict_dimension:
        raise ValueError("failed to construct a basis of the strict quotient")
    representatives = [
        element_payload(field, from_selmer(vector), vector=vector)
        for vector in extra_basis
    ]
    exact_basis = [
        element_payload(
            field,
            representative,
            vector=selmer.basis()[index],
            basis_index=index,
        )
        for index, representative in enumerate(selmer_generators)
    ]

    s_class_two_torsion_dimension = class_upper[
        "computedSClassGroupTwoRankUpperBound"
    ]
    full_s_unit_dimension = unit_squareclass_dimension + len(union_prime_ideals)
    support_combination_dimension = int(
        full_s_unit_dimension - tested_span.dimension()
    )
    if strict_dimension != (
        support_combination_dimension + s_class_two_torsion_dimension
    ):
        raise ValueError("Selmer quotient does not split into S-unit and class components")

    frozen_live_signatures = field_row.pop("_frozenLiveSignatures")
    return {
        "canonicalPolynomial": str(field_row["fieldCanonicalPolynomial"]),
        "classGroupCertificate": class_upper,
        "completedArtifactCount": len(inventory_rows),
        "distinctTestedSupportCount": len(supports),
        "exactSelmerBasisRepresentatives": exact_basis,
        "exactSelmerCertificate": independence,
        "fieldCanonicalSha256": str(field_row["fieldCanonicalSha256"]),
        "fieldDiscriminantAbs": str(discriminant),
        "frozenLiveTargetSignatures": frozen_live_signatures,
        "fullUnionSUnitSpanDimension": full_s_unit_dimension,
        "hModuleComparison": {
            "certification": (
                "the underlying GF(2) quotient is zero, hence its quotient "
                "as an H-module is zero under the faithful forgetful functor"
                if strict_dimension == 0
                else "not certified: exact H-action analysis is required before a pilot"
            ),
            "newHSubmoduleDimension": 0 if strict_dimension == 0 else None,
            "status": "certified_zero" if strict_dimension == 0 else "not_advanced",
        },
        "mappedSUnitCoordinateVerificationCount": coordinate_verifications,
        "quotientT": int(field_row["quotientT"]),
        "scheduleAudit": schedule_audit,
        "selmerDimension": selmer_upper_dimension,
        "selmerGeneratorCount": len(selmer_generators),
        "strictNewQuotientDimension": strict_dimension,
        "strictNewQuotientRepresentatives": representatives,
        "sClassGroupTwoTorsionDimension": s_class_two_torsion_dimension,
        "supportCombinationContributionDimension": support_combination_dimension,
        "testedSUnitAuxiliarySpanDimension": int(tested_span.dimension()),
        "unionPrimeIdealSupportCount": len(union_prime_ideals),
        "unionRationalPrimeSupport": union_primes,
        "unitSquareclassDimension": unit_squareclass_dimension,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--certified-pilot", type=Path, default=DEFAULT_CERTIFIED_PILOT
    )
    parser.add_argument(
        "--field-indices",
        default="",
        help="comma-separated one-based census indices; empty selects all fields",
    )
    parser.add_argument("--worker-only", action="store_true")
    args = parser.parse_args()

    # Avoid PARI's much stronger full regulator/unit-index certification while
    # constructing candidate elements.  The 2-primary result is unconditional:
    # bnfcertify(flag=1) supplies the class-group upper quotient, and exact
    # valuation, interval-sign, and algebraic-square checks prove that the
    # candidate basis attains it.
    proof.number_field(False)

    if not args.worker_only:
        for path in (args.inventory, args.audit, args.freeze):
            if path.exists():
                raise ValueError(f"refusing to overwrite frozen F10 artifact: {path}")
    for path in (args.db, args.census, args.live):
        if not path.exists():
            raise ValueError(f"required frozen input is missing: {path}")

    census = read_jsonl(args.census)
    if len(census) != 37:
        raise ValueError(f"expected 37 canonical F1 fields, got {len(census)}")
    field_hashes = [str(row["fieldCanonicalSha256"]) for row in census]
    if len(set(field_hashes)) != len(field_hashes):
        raise ValueError("canonical F1 census contains duplicate field hashes")
    if args.field_indices:
        selected_indices = sorted(
            {int(value) for value in args.field_indices.split(",") if value.strip()}
        )
    else:
        selected_indices = list(range(1, len(census) + 1))
    if not selected_indices or any(
        index < 1 or index > len(census) for index in selected_indices
    ):
        raise ValueError("--field-indices contains an out-of-range census index")
    if not args.worker_only and selected_indices != list(range(1, len(census) + 1)):
        raise ValueError("final assembly requires all 37 census fields")

    live_rows = read_jsonl(args.live)
    target_labels = {label for row in census for label in row["targetNormCores"]}
    frozen_live = defaultdict(list)
    for row in live_rows:
        if str(row.get("label")) in target_labels and int(row.get("teamCount", -1)) == 0:
            frozen_live[str(row["label"])].append(
                {
                    "frozenAt": row.get("frozenAt"),
                    "label": str(row["label"]),
                    "minimumDiscAbs": row.get("minimumDiscAbs"),
                    "r": int(row["r"]),
                    "targetGeneratedAt": row.get("targetGeneratedAt"),
                    "teamCount": int(row["teamCount"]),
                }
            )
    for label in frozen_live:
        frozen_live[label].sort(key=lambda row: row["r"])

    inventory, inventory_summary = build_inventory(census, args.db)
    by_field = defaultdict(list)
    for row in inventory:
        by_field[row["fieldCanonicalSha256"]].append(row)
    missing = sorted(set(field_hashes) - by_field.keys())
    if missing:
        raise ValueError(f"canonical fields lack completed support artifacts: {missing}")

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    audit_rows = []
    for index, original in enumerate(census, 1):
        if index not in selected_indices:
            continue
        row = dict(original)
        row["_frozenLiveSignatures"] = sorted(
            [
                signature
                for label in row["targetNormCores"]
                for signature in frozen_live.get(label, [])
            ],
            key=lambda value: (value["label"], value["r"]),
        )
        field_inventory_rows = by_field[row["fieldCanonicalSha256"]]
        support_fingerprint = hashlib.sha256(
            json.dumps(
                [
                    {
                        "artifact": value["artifact"],
                        "artifactSha256": value["artifactSha256"],
                        "rationalPrimeSupport": value["rationalPrimeSupport"],
                    }
                    for value in field_inventory_rows
                ],
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        cache_path = args.cache_dir / (
            f"{index:02d}_{row['fieldCanonicalSha256']}.json"
        )
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("fieldCanonicalSha256") != row["fieldCanonicalSha256"]
                or cached.get("testedSupportFingerprint") != support_fingerprint
            ):
                raise ValueError(f"stale or mismatched exact cache row: {cache_path}")
            result = cached
            cache_status = "reused"
        else:
            result = audit_field(row, field_inventory_rows)
            result["testedSupportFingerprint"] = support_fingerprint
            write_atomic(cache_path, rendered_json(result))
            cache_status = "created"
        audit_rows.append(result)
        print(
            json.dumps(
                {
                    "audited": index,
                    "event": "f10_field_audited",
                    "exactCache": cache_status,
                    "fieldCanonicalSha256": result["fieldCanonicalSha256"],
                    "quotientT": result["quotientT"],
                    "selmerDimension": result["selmerDimension"],
                    "strictNewQuotientDimension": result[
                        "strictNewQuotientDimension"
                    ],
                    "testedSpanDimension": result[
                        "testedSUnitAuxiliarySpanDimension"
                    ],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    if args.worker_only:
        print(
            json.dumps(
                {
                    "auditedIndices": selected_indices,
                    "cacheDirectory": str(args.cache_dir.resolve()),
                    "event": "f10_exact_worker_completed",
                    "positiveStrictQuotients": sum(
                        row["strictNewQuotientDimension"] > 0 for row in audit_rows
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0

    strict_dimensions = [row["strictNewQuotientDimension"] for row in audit_rows]
    all_zero = all(value == 0 for value in strict_dimensions)
    positive_field_hashes = {
        row["fieldCanonicalSha256"]
        for row in audit_rows
        if row["strictNewQuotientDimension"] > 0
    }
    certified_routes = []
    if args.certified_pilot.exists():
        pilot = json.loads(args.certified_pilot.read_text(encoding="utf-8"))
        candidate = pilot.get("candidate", {})
        pilot_audit = pilot.get("audit", {})
        field_hash = str(pilot_audit.get("censusFieldCanonicalSha256", ""))
        h_module = candidate.get("hOrbitSubmoduleCertificate", {})
        live_target = pilot_audit.get("frozenLiveTarget", {})
        if (
            field_hash in positive_field_hashes
            and str(candidate.get("status", "")).startswith("certified_")
            and int(h_module.get("hOrbitSquareclassSpanDimension", -1)) == 11
            and int(live_target.get("teamCount", -1)) == 0
        ):
            certified_routes.append(
                {
                    "candidateSha256": candidate["candidateSha256"],
                    "fieldCanonicalSha256": field_hash,
                    "frozenLiveTarget": live_target,
                    "hOrbitSquareclassSpanDimension": 11,
                    "pilot": str(args.certified_pilot.resolve()),
                    "pilotSha256": sha256_path(args.certified_pilot),
                    "status": candidate["status"],
                }
            )
    routed_fields = {row["fieldCanonicalSha256"] for row in certified_routes}
    all_positive_routed = bool(positive_field_hashes) and (
        positive_field_hashes <= routed_fields
    )
    inventory_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in inventory
    )
    inventory_sha = hashlib.sha256(inventory_text.encode()).hexdigest()
    input_hashes = {
        "censusSha256": sha256_path(args.census),
        "frozenLiveSha256": sha256_path(args.live),
        "stableDatabaseSha256": sha256_path(args.db),
        "testedSupportInventorySha256": inventory_sha,
    }
    if args.certified_pilot.exists():
        input_hashes["certifiedPilotSha256"] = sha256_path(args.certified_pilot)
    payload = {
        "audit": audit_rows,
        "decision": {
            "f10Blocked": all_zero,
            "minimalExactPilotBuilt": bool(certified_routes),
            "reason": (
                "all 37 underlying Selmer/tested-span quotients are zero; "
                "therefore all new H-module quotients are zero"
                if all_zero
                else (
                    "every positive underlying quotient field has an exact full-rank "
                    "H-module route to a frozen-live target and a minimal exact pilot"
                    if all_positive_routed
                    else "a positive underlying quotient exists but at least one field "
                    "lacks exact H-module and frozen-live target certification"
                )
            ),
            "status": (
                "blocked_zero_new_h_quotient_all_fields"
                if all_zero
                else (
                    "advanced_certified_h_route"
                    if all_positive_routed
                    else "not_advanced_pending_exact_h_module_certificate"
                )
            ),
        },
        "certifiedRoutes": certified_routes,
        "exactness": {
            "classGroupUpperQuotientCertifiedByBnfcertifyFlag1": True,
            "constructionNumberFieldProofFlag": False,
            "method": (
                "unconditional 2-primary upper bound from the certified class-group "
                "quotient, attained by algebraic generators with exact valuation, "
                "interval-sign, non-square, and coordinate-square verification"
            ),
            "selmerBasisIndependenceVerifiedExactly": True,
            "sUnitCoordinatesVerifiedExactly": True,
            "unconditionalTwoPrimaryCertificate": True,
        },
        "fieldCount": len(audit_rows),
        "frozenLiveCensusSignatureCount": sum(
            len(rows) for rows in frozen_live.values()
        ),
        "inputHashes": input_hashes,
        "inventorySummary": inventory_summary,
        "networkCalls": 0,
        "splitPrimeAuxiliaryEnumerationCalls": 0,
        "submissionCalls": 0,
        "strictNewQuotientDimensions": strict_dimensions,
    }
    audit_text = rendered_json(payload)
    audit_sha = hashlib.sha256(audit_text.encode()).hexdigest()
    freeze_payload = {
        "audit": str(args.audit.resolve()),
        "auditSha256": audit_sha,
        "census": str(args.census.resolve()),
        "decisionStatus": payload["decision"]["status"],
        "fieldCount": len(audit_rows),
        "frozenLive": str(args.live.resolve()),
        "inputHashes": input_hashes,
        "inventory": str(args.inventory.resolve()),
        "networkCalls": 0,
        "splitPrimeAuxiliaryEnumerationCalls": 0,
        "submissionCalls": 0,
    }
    freeze_text = rendered_json(freeze_payload)

    write_atomic(args.inventory, inventory_text)
    write_atomic(args.audit, audit_text)
    write_atomic(args.freeze, freeze_text)
    print(
        json.dumps(
            {
                "audit": str(args.audit.resolve()),
                "auditSha256": audit_sha,
                "decisionStatus": payload["decision"]["status"],
                "freeze": str(args.freeze.resolve()),
                "freezeSha256": hashlib.sha256(freeze_text.encode()).hexdigest(),
                "inventory": str(args.inventory.resolve()),
                "inventorySha256": inventory_sha,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 2 if all_zero else 3


if __name__ == "__main__":
    raise SystemExit(main())

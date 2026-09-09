#!/usr/bin/env sage -python
"""Certify the full-Kummer outcome of an affine shifted even lift.

For an accepted even source ``q(x^2)`` and integers ``M != 0`` and ``a``,
construct

    Q(t) = M^12 q((t + a) / M),    f(x) = Q(x^2).

The source label must have a unique two-point block-quotient structure.  The
shifted lift is certified to have the exact full-wreath degree-24 label when a
prime p satisfies

    p does not divide M,
    q modulo p is squarefree, and
    v_p(Q(0)) = 1.

Indeed, in the splitting field of q, reduction at a prime over p singles out
exactly one of the twelve conjugate radicands with odd valuation.  Conjugating
that prime through the transitive quotient gives twelve independent valuation
coordinates, hence Kummer rank 12 and the full C2^12 : Gal(q) action.  If the
accepted source has a smaller block kernel, the same witness rigorously
refutes preservation of its 24T label.

When no rank-12 witness is found, a squarefree modular factorization whose
cycle type is absent from every conjugacy class of the source 24T group still
gives an exact, fail-closed rejection of source-label preservation.

This worker is read-only, writes no artifacts, and has no network or
submission path.  Coefficients are omitted from stdout unless explicitly
requested.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, PolynomialRing, ZZ, is_prime, libgap, prime_range


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
DEGREE = 12
FULL_KERNEL_ORDER = 2**DEGREE


def coefficient_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    values += [ZZ(0)] * (2 * DEGREE + 1 - len(values))
    if (
        len(values) != 2 * DEGREE + 1
        or values[-1] != 1
        or values[0] == 0
        or math.gcd(*(int(value) for value in values)) != 1
    ):
        raise ValueError(
            "candidate is not primitive monic degree 24 with nonzero constant"
        )
    return ",".join(str(value) for value in values)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def parse_label(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid source label: {label!r}")
    return int(match.group(1))


def load_source(
    db: Path,
    submission_id: str,
    polynomial_index: int,
    expected_label: str,
    expected_hash: str,
) -> dict:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,
                   v.scoreable
            FROM polynomials AS p
            JOIN verifications AS v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (submission_id, polynomial_index),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("source row is absent from the local ledger")
    if (
        str(row["label"]) != expected_label
        or str(row["coefficient_hash"]) != expected_hash
        or str(row["status"]) != "accepted"
        or int(row["scoreable"]) != 1
    ):
        raise ValueError("accepted source provenance does not match its pins")

    values = [ZZ(value) for value in str(row["coefficients"]).split(",")]
    if (
        len(values) != 2 * DEGREE + 1
        or values[-1] != 1
        or any(values[index] for index in range(1, 2 * DEGREE + 1, 2))
    ):
        raise ValueError("accepted source is not a monic even degree-24 polynomial")
    canonical = ",".join(str(value) for value in values)
    if sha256_text(canonical) != expected_hash:
        raise ValueError("source coefficient hash is not canonical")

    ring_y = PolynomialRing(ZZ, "y")
    quotient = ring_y(values[::2])
    if quotient.degree() != DEGREE or not quotient.is_monic():
        raise ValueError("source quotient is not monic degree 12")
    return {
        "coefficientSha256": expected_hash,
        "label": expected_label,
        "polynomialIndex": polynomial_index,
        "quotient": quotient,
        "r": int(row["r"]),
        "submissionId": submission_id,
    }


def quotient_structures(source_t: int) -> list[dict]:
    """Return the distinct two-point quotient structures of a 24T group."""
    group = libgap.TransitiveGroup(2 * DEGREE, source_t)
    structures = {}
    for block in libgap.AllBlocks(group):
        if int(libgap.Length(block)) != 2:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        if int(libgap.Length(blocks)) != DEGREE:
            continue
        action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
        quotient = libgap.Image(action)
        row = {
            "blockCount": DEGREE,
            "blockKernelOrder": int(libgap.Size(libgap.Kernel(action))),
            "quotientOrder": int(libgap.Size(quotient)),
            "quotientT12": int(libgap.TransitiveIdentification(quotient)),
        }
        structures[
            (
                row["blockKernelOrder"],
                row["quotientOrder"],
                row["quotientT12"],
            )
        ] = row
    return [structures[key] for key in sorted(structures)]


def full_wreath_certificate(structure: dict) -> dict:
    quotient = libgap.TransitiveGroup(DEGREE, int(structure["quotientT12"]))
    if int(libgap.Size(quotient)) != int(structure["quotientOrder"]):
        raise ValueError("standard quotient order disagrees with source block action")
    wreath = libgap.WreathProduct(libgap.SymmetricGroup(2), quotient)
    expected_order = FULL_KERNEL_ORDER * int(structure["quotientOrder"])
    if (
        int(libgap.Size(wreath)) != expected_order
        or not bool(libgap.IsTransitive(wreath, libgap.eval("[1..24]")))
    ):
        raise ValueError("canonical full-wreath construction failed")
    wreath_t = int(libgap.TransitiveIdentification(wreath))
    return {
        "fullKernelOrder": FULL_KERNEL_ORDER,
        "fullKummerRank": DEGREE,
        "order": expected_order,
        "targetLabel": f"24T{wreath_t}",
        "targetT": wreath_t,
    }


def irreducibility_certificate(polynomial, prime_bound: int) -> dict:
    for prime in prime_range(2, prime_bound + 1):
        prime = int(prime)
        reduced = polynomial.change_ring(GF(prime))
        if (
            reduced.degree() == polynomial.degree()
            and reduced.is_irreducible()
        ):
            return {
                "method": "irreducible_reduction",
                "prime": prime,
                "proved": True,
            }
    if not polynomial.is_irreducible():
        raise ValueError("polynomial is reducible")
    return {
        "method": "exact_rational_factorization",
        "prime": None,
        "proved": True,
    }


def shifted_polynomials(quotient, a: int, m: int):
    if m == 0:
        raise ValueError("M must be nonzero")
    ring_t = PolynomialRing(ZZ, "t")
    t = ring_t.gen()
    shifted = ring_t(
        sum(
            ZZ(quotient[index])
            * ZZ(m) ** (DEGREE - index)
            * (t + ZZ(a)) ** index
            for index in range(DEGREE + 1)
        )
    )
    if shifted.degree() != DEGREE or not shifted.is_monic():
        raise ValueError("shifted quotient is not monic degree 12")
    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    candidate = ring_x(shifted(x**2))
    line = coefficient_line(candidate)
    return shifted, candidate, line


def witness_test(quotient, shifted, m: int, prime: int) -> dict | None:
    if prime < 2 or not is_prime(prime) or m % prime == 0:
        return None
    constant = ZZ(shifted[0])
    residue_mod_p2 = int(constant % (prime * prime))
    if residue_mod_p2 % prime != 0 or residue_mod_p2 == 0:
        return None
    reduced_q = quotient.change_ring(GF(prime))
    if not reduced_q.is_squarefree():
        return None
    reduced_shifted = shifted.change_ring(GF(prime))
    derivative_at_zero = int(reduced_shifted.derivative()(0))
    if derivative_at_zero == 0:
        raise ArithmeticError("squarefree affine reduction has a multiple zero root")
    return {
        "prime": prime,
        "qModuloPrimeSquarefree": True,
        "q0ResidueModuloPrimeSquared": residue_mod_p2,
        "shiftedDerivativeAtZeroModuloPrime": derivative_at_zero,
        "shiftedQ0Valuation": 1,
    }


def find_witness(
    quotient,
    shifted,
    m: int,
    prime_bound: int,
    requested_prime: int | None,
) -> dict | None:
    if requested_prime is not None:
        return witness_test(quotient, shifted, m, requested_prime)
    constant = ZZ(shifted[0])
    for prime in prime_range(2, prime_bound + 1):
        prime = int(prime)
        if constant % prime:
            continue
        witness = witness_test(quotient, shifted, m, prime)
        if witness is not None:
            return witness
    return None


def cycle_type(polynomial, prime: int) -> tuple[int, ...] | None:
    reduced = polynomial.change_ring(GF(prime))
    if reduced.degree() != polynomial.degree() or not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def group_cycle_profiles(source_t: int) -> set[tuple[int, ...]]:
    group = libgap.TransitiveGroup(2 * DEGREE, source_t)
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


def permutation_cycles(permutation) -> list[tuple[int, ...]]:
    seen = set()
    cycles = []
    for point in range(1, DEGREE + 1):
        if point in seen:
            continue
        cycle = []
        image = point
        while image not in seen:
            seen.add(image)
            cycle.append(image - 1)
            image = int(libgap.OnPoints(image, permutation))
        cycles.append(tuple(cycle))
    return cycles


def permute_vector(vector: tuple[int, ...], permutation) -> tuple[int, ...]:
    result = [0] * DEGREE
    for index, value in enumerate(vector):
        if value:
            result[int(libgap.OnPoints(index + 1, permutation)) - 1] = 1
    return tuple(result)


def orbit_span_rank(vector: tuple[int, ...], generators: list) -> int:
    orbit = {vector}
    queue = [vector]
    while queue:
        current = queue.pop()
        for generator in generators:
            image = permute_vector(current, generator)
            if image not in orbit:
                orbit.add(image)
                queue.append(image)
    return int(Matrix(GF(2), list(orbit)).rank())


def signed_kernel_rank_options(
    quotient_group,
    quotient_cycle_type: tuple[int, ...],
    candidate_cycle_type: tuple[int, ...],
) -> list[int]:
    """Possible ranks forced by powering one signed Frobenius lift.

    For a quotient cycle C of length ell, its signed lift consists of two
    ell-cycles when the fiber-sign sum on C is even and one 2*ell-cycle when
    it is odd.  Raising the lift to the quotient element order produces an
    explicit block-flip vector in the Kummer kernel.  Its quotient orbit span
    is therefore a rigorous submodule of the candidate kernel.
    """
    generators = list(libgap.GeneratorsOfGroup(quotient_group))
    options = set()
    for conjugacy_class in libgap.ConjugacyClasses(quotient_group):
        representative = libgap.Representative(conjugacy_class)
        cycles = permutation_cycles(representative)
        if tuple(sorted(len(cycle) for cycle in cycles)) != quotient_cycle_type:
            continue
        order = math.lcm(*(len(cycle) for cycle in cycles))
        for signs in itertools.product((0, 1), repeat=len(cycles)):
            lifted_type = tuple(
                sorted(
                    value
                    for cycle, sign in zip(cycles, signs)
                    for value in (
                        [2 * len(cycle)]
                        if sign
                        else [len(cycle), len(cycle)]
                    )
                )
            )
            if lifted_type != candidate_cycle_type:
                continue
            vector = [0] * DEGREE
            for cycle, sign in zip(cycles, signs):
                if sign and (order // len(cycle)) % 2:
                    for index in cycle:
                        vector[index] = 1
            options.add(orbit_span_rank(tuple(vector), generators))
    return sorted(options)


def kernel_rank_lower_bound_certificate(
    quotient,
    candidate,
    quotient_t: int,
    prime_bound: int,
) -> dict:
    quotient_group = libgap.TransitiveGroup(DEGREE, quotient_t)
    cache = {}
    best = {
        "candidateCycleType": None,
        "minimumForcedKummerRank": 0,
        "possibleForcedRanks": [0],
        "prime": None,
        "quotientCycleType": None,
    }
    checked = 0
    for prime in prime_range(2, prime_bound + 1):
        prime = int(prime)
        candidate_profile = cycle_type(candidate, prime)
        quotient_profile = cycle_type(quotient, prime)
        if candidate_profile is None or quotient_profile is None:
            continue
        checked += 1
        key = (quotient_profile, candidate_profile)
        if key not in cache:
            cache[key] = signed_kernel_rank_options(
                quotient_group,
                quotient_profile,
                candidate_profile,
            )
        options = cache[key]
        if not options:
            raise ArithmeticError(
                "candidate Frobenius type is incompatible with its quotient"
            )
        forced = min(options)
        if forced > int(best["minimumForcedKummerRank"]):
            best = {
                "candidateCycleType": list(candidate_profile),
                "minimumForcedKummerRank": forced,
                "possibleForcedRanks": options,
                "prime": prime,
                "quotientCycleType": list(quotient_profile),
            }
            if forced == DEGREE:
                break
    return {
        **best,
        "checkedSquarefreePrimes": checked,
        "method": "powered_signed_frobenius_orbit_span",
        "primeBound": prime_bound,
        "proof": (
            "For every quotient conjugacy class and every fiber-sign pattern "
            "compatible with the two displayed modular factorization types, "
            "powering the signed lift by the quotient element order gives a "
            "kernel vector. The minimum GF(2) rank of its full quotient orbit "
            "is the displayed rigorous lower bound on the Kummer kernel."
        ),
    }


def compatible_transitive_catalog(
    quotient_t: int,
    quotient_order: int,
    minimum_kummer_rank: int,
) -> dict:
    labels = set()
    buckets = []
    for rank in range(minimum_kummer_rank, DEGREE + 1):
        order = quotient_order * 2**rank
        groups = libgap.AllTransitiveGroups(
            libgap.NrMovedPoints,
            2 * DEGREE,
            libgap.Size,
            order,
        )
        library_count = int(libgap.Length(groups))
        matching = []
        for group in groups:
            target_t = int(libgap.TransitiveIdentification(group))
            for block in libgap.AllBlocks(group):
                if int(libgap.Length(block)) != 2:
                    continue
                blocks = libgap.Orbit(group, block, libgap.OnSets)
                if int(libgap.Length(blocks)) != DEGREE:
                    continue
                action = libgap.Action(group, blocks, libgap.OnSets)
                if int(libgap.TransitiveIdentification(action)) == quotient_t:
                    matching.append(target_t)
                    labels.add(target_t)
                    break
        buckets.append(
            {
                "compatibleLabels": sorted(set(matching)),
                "groupOrder": order,
                "kummerRank": rank,
                "libraryGroupsOfOrder": library_count,
            }
        )
        # GAP's degree-24 library objects are large; release each order bucket
        # before loading the next one.
        del groups
        try:
            del action, block, blocks, group
        except UnboundLocalError:
            pass
        libgap.collect()
    return {
        "buckets": buckets,
        "labels": sorted(labels),
        "minimumKummerRank": minimum_kummer_rank,
    }


def exact_catalog_classification(
    candidate,
    quotient_t: int,
    quotient_order: int,
    minimum_kummer_rank: int,
    prime_bound: int,
) -> dict:
    catalog = compatible_transitive_catalog(
        quotient_t,
        quotient_order,
        minimum_kummer_rank,
    )
    profiles = {
        target_t: group_cycle_profiles(target_t)
        for target_t in catalog["labels"]
    }
    candidate_r = int(candidate.number_of_real_roots())
    archimedean_type = tuple(
        [1] * candidate_r + [2] * ((2 * DEGREE - candidate_r) // 2)
    )
    remaining = {
        target_t
        for target_t in catalog["labels"]
        if archimedean_type in profiles[target_t]
    }
    elimination = [
        {
            "afterCount": len(remaining),
            "cycleType": list(archimedean_type),
            "evidence": "archimedean_complex_conjugation",
        }
    ]
    for prime in prime_range(2, prime_bound + 1):
        prime = int(prime)
        observed = cycle_type(candidate, prime)
        if observed is None:
            continue
        reduced = {
            target_t
            for target_t in remaining
            if observed in profiles[target_t]
        }
        if len(reduced) < len(remaining):
            elimination.append(
                {
                    "afterCount": len(reduced),
                    "cycleType": list(observed),
                    "evidence": "squarefree_modular_factorization",
                    "prime": prime,
                }
            )
            remaining = reduced
        if len(remaining) <= 1:
            break
    exact_t = next(iter(remaining)) if len(remaining) == 1 else None
    return {
        "catalogBuckets": catalog["buckets"],
        "catalogLabels": [f"24T{value}" for value in catalog["labels"]],
        "catalogSize": len(catalog["labels"]),
        "elimination": elimination,
        "exactLabel": None if exact_t is None else f"24T{exact_t}",
        "method": "exhaustive_degree24_transitive_catalog_and_frobenius",
        "primeBound": prime_bound,
        "remainingLabels": [
            f"24T{value}" for value in sorted(remaining)
        ],
        "proof": (
            "Candidate irreducibility makes its Galois action transitive. The "
            "affine quotient has the pinned exact 12T action, and the kernel "
            "is an elementary abelian subgroup of C2^12. GAP's exhaustive "
            "degree-24 transitive library is filtered by every possible order "
            "at or above the proved kernel-rank bound and by an exact 12-block "
            "quotient identification. Archimedean and Dedekind Frobenius "
            "cycle types eliminate the displayed labels; a singleton is the "
            "exact 24T identification."
        ),
    }


def find_source_label_exclusion(
    candidate,
    source_t: int,
    prime_bound: int,
) -> dict | None:
    profiles = group_cycle_profiles(source_t)
    for prime in prime_range(2, prime_bound + 1):
        prime = int(prime)
        profile = cycle_type(candidate, prime)
        if profile is not None and profile not in profiles:
            return {
                "candidateCycleType": list(profile),
                "method": "dedekind_frobenius_cycle_type_exclusion",
                "prime": prime,
                "proof": (
                    "The candidate reduction is squarefree with the displayed "
                    "factor degrees. Dedekind's theorem puts that cycle type "
                    "in the candidate Galois group, while exhaustive GAP "
                    "conjugacy classes show it is absent from the pinned "
                    "source 24T group."
                ),
                "sourceCycleProfileCount": len(profiles),
            }
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--expected-source-label", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--a", required=True, type=int)
    parser.add_argument("--M", required=True, type=int)
    parser.add_argument("--prime-bound", type=int, default=100_000)
    parser.add_argument("--frobenius-prime-bound", type=int, default=10_000)
    parser.add_argument("--kernel-rank-prime-bound", type=int, default=1_000)
    parser.add_argument("--witness-prime", type=int)
    parser.add_argument("--emit-coefficients", action="store_true")
    args = parser.parse_args()
    if args.prime_bound < 2:
        raise ValueError("--prime-bound must be at least 2")
    if args.frobenius_prime_bound < 2:
        raise ValueError("--frobenius-prime-bound must be at least 2")
    if args.kernel_rank_prime_bound < 2:
        raise ValueError("--kernel-rank-prime-bound must be at least 2")

    source_t = parse_label(args.expected_source_label)
    source = load_source(
        args.db,
        args.submission_id,
        args.polynomial_index,
        args.expected_source_label,
        args.expected_source_sha256,
    )
    quotient = source.pop("quotient")
    structures = quotient_structures(source_t)
    if len(structures) != 1:
        raise ValueError(
            "source label does not have one unique two-point quotient structure"
        )
    structure = structures[0]
    wreath = full_wreath_certificate(structure)
    source_is_full_wreath = (
        int(structure["blockKernelOrder"]) == FULL_KERNEL_ORDER
        and int(wreath["targetT"]) == source_t
    )
    irreducibility = irreducibility_certificate(quotient, args.prime_bound)
    shifted, candidate, line = shifted_polynomials(quotient, args.a, args.M)
    candidate_irreducibility = irreducibility_certificate(
        candidate, args.prime_bound
    )
    witness = find_witness(
        quotient,
        shifted,
        args.M,
        args.prime_bound,
        args.witness_prime,
    )
    source_label_exclusion = (
        None
        if witness is not None
        else find_source_label_exclusion(
            candidate,
            source_t,
            args.frobenius_prime_bound,
        )
    )
    kernel_rank_lower_bound = (
        None
        if witness is not None
        else kernel_rank_lower_bound_certificate(
            shifted,
            candidate,
            int(structure["quotientT12"]),
            args.kernel_rank_prime_bound,
        )
    )
    catalog_classification = (
        None
        if witness is not None
        else exact_catalog_classification(
            candidate,
            int(structure["quotientT12"]),
            int(structure["quotientOrder"]),
            int(kernel_rank_lower_bound["minimumForcedKummerRank"]),
            args.frobenius_prime_bound,
        )
    )
    candidate_exact_label = (
        wreath["targetLabel"]
        if witness is not None
        else catalog_classification["exactLabel"]
    )

    if candidate_exact_label == args.expected_source_label:
        status = "exact_same_24T_label"
        preserves_source_label = True
    elif (
        candidate_exact_label is not None
        or source_label_exclusion is not None
    ):
        status = "exact_different_24T_label"
        preserves_source_label = False
    else:
        status = "incomplete_no_label_decision_in_bounds"
        preserves_source_label = None

    if witness is not None:
        comparison_proof = (
            "The affine root map preserves the exact degree-12 quotient. "
            "The valuation witness proves the complete C2^12 kernel and hence "
            "the exact full imprimitive wreath action. Its 24T identification "
            "is then compared with the accepted source."
        )
    elif candidate_exact_label is not None:
        comparison_proof = catalog_classification["proof"]
    elif source_label_exclusion is not None:
        comparison_proof = source_label_exclusion["proof"]
    else:
        comparison_proof = (
            "The bounded searches found neither a rank-12 valuation witness "
            "nor a Frobenius cycle type excluding the source label."
        )

    result = {
        "schemaVersion": "shifted-full-kummer-label-comparison-certificate-v2",
        "status": status,
        "preservesSourceLabel": preserves_source_label,
        "source": source,
        "sourceQuotient": {
            **structure,
            "coefficientSha256": sha256_text(
                ",".join(str(value) for value in quotient.list())
            ),
            "irreducibility": irreducibility,
        },
        "parameters": {"M": args.M, "a": args.a},
        "candidate": {
            "coefficientBytes": len(line.encode("ascii")),
            "coefficientSha256": sha256_text(line),
            "degree": int(candidate.degree()),
            "exactLabel": candidate_exact_label,
            "irreducibility": candidate_irreducibility,
            "monic": bool(candidate.is_monic()),
            "primitive": int(candidate.content()) == 1,
            "r": int(candidate.number_of_real_roots()),
        },
        "fullWreathAction": {
            **wreath,
            "provedForCandidate": (
                candidate_exact_label == wreath["targetLabel"]
            ),
        },
        "catalogClassification": catalog_classification,
        "kernelRankLowerBound": kernel_rank_lower_bound,
        "sourceAction": {
            "isFullWreath": source_is_full_wreath,
            "label": args.expected_source_label,
            "order": int(structure["blockKernelOrder"])
            * int(structure["quotientOrder"]),
        },
        "rankCertificate": None
        if witness is None
        else {
            **witness,
            "conclusion": (
                "the twelve conjugate radicand squareclasses are independent"
            ),
            "kummerRank": DEGREE,
            "proof": (
                "At a prime of the quotient splitting field above p, the "
                "squarefree affine reduction has exactly one zero root. "
                "v_p(Q(0))=1 makes that root's valuation odd and all other "
                "root valuations zero. Quotient transitivity conjugates this "
                "valuation coordinate to all twelve roots."
            ),
        },
        "sourceLabelExclusion": source_label_exclusion,
        "proof": comparison_proof,
        "networkCalls": 0,
        "submissionCalls": 0,
        "writes": 0,
    }
    if args.emit_coefficients:
        result["candidate"]["coefficientLine"] = line
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if preserves_source_label is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())

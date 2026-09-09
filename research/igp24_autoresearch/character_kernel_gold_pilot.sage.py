#!/usr/bin/env sage -python
"""Offline same-character pilots for degree-24 character-kernel gold pairs.

Given a locally verified even polynomial ``q(x^2)`` whose group is the target
24T label, search the totally positive S-unit squareclasses of ``Q[y]/q`` for
another element with the same rational norm squareclass.  The resulting
``minpoly(h)(x^2)`` is contained in the same character-kernel action.  Exact
joint Frobenius witnesses against every proper transitive maximal subgroup
then certify equality with the target group.

This program is deliberately offline: it reads an immutable ledger snapshot,
makes no network calls, and never submits a polynomial.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import random
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, NumberField, PolynomialRing, QQ, RealField, ZZ, kronecker, libgap, prime_range, prod, vector


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"


def load_shared_helpers():
    path = ROOT / "character_core2_20778_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_core2_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import shared pilot helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHARED = load_shared_helpers()


def parse_label(label: str) -> int:
    if not label.startswith("24T"):
        raise ValueError(f"not a degree-24 transitive label: {label}")
    return int(label[3:])


def load_source(db: Path, submission_id: str, polynomial_index: int):
    connection = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    try:
        row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.scoreable,
                   v.status,v.field_disc_abs
            FROM polynomials AS p
            JOIN verifications AS v USING (submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (submission_id, polynomial_index),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("source row is absent from the ledger")
    coefficients = [ZZ(value) for value in str(row[0]).split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("source is not monic degree 24")
    if any(coefficients[index] for index in range(1, 25, 2)):
        raise ValueError("source is not an even polynomial q(x^2)")
    if int(row[4]) != 1:
        raise ValueError("source is not a scoreable verification")
    ring = PolynomialRing(ZZ, "y")
    quotient = ring(coefficients[::2])
    if not quotient.is_irreducible() or quotient.number_of_real_roots() != 12:
        raise ValueError("the degree-12 quotient is not irreducible and totally real")
    core = ZZ(quotient[0]).squarefree_part()
    if core <= 0:
        raise ValueError("the source norm squareclass is not positive")
    field = NumberField(quotient.change_ring(QQ), "a")
    return {
        "coefficientSha256": str(row[1]),
        "fieldDiscAbs": str(row[6]) if row[6] else None,
        "label": str(row[2]),
        "polynomialIndex": polynomial_index,
        "quotient": quotient,
        "quotientFieldDiscAbs": str(abs(ZZ(field.discriminant()))),
        "r": int(row[3]),
        "scoreable": bool(row[4]),
        "squarefreeNormCore": int(core),
        "status": str(row[5]),
        "submissionId": submission_id,
    }


def load_live_gold(db: Path, target_label: str, target_r: int, max_team_count: int):
    connection = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    try:
        row = connection.execute(
            """
            SELECT t.team_count,t.minimum_disc_abs,t.generated_at,
                   CASE WHEN b.label IS NULL THEN 0 ELSE 1 END,
                   CASE WHEN owned.label IS NULL THEN 0 ELSE 1 END
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=? AND t.r=?
            """,
            (target_label, target_r),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"missing live target row for {target_label}/r{target_r}")
    result = {
        "baseline": bool(row[3]),
        "generatedAt": str(row[2]) if row[2] else None,
        "locallyOwned": bool(row[4]),
        "minimumDiscAbs": str(row[1]) if row[1] else None,
        "pair": f"{target_label}/r{target_r}",
        "teamCount": int(row[0]),
    }
    if (
        result["teamCount"] > max_team_count
        or result["baseline"]
        or result["locallyOwned"]
    ):
        raise ValueError(f"target is outside the requested live value tier: {result}")
    result["valueTier"] = "gold" if result["teamCount"] == 0 else "solo"
    return result


def target_structure(target_t: int):
    group = libgap.TransitiveGroup(24, target_t)
    block = next(
        value for value in libgap.AllBlocks(group) if int(libgap.Length(value)) == 2
    )
    blocks = libgap.Orbit(group, block, libgap.OnSets)
    action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
    quotient = libgap.Image(action)
    kernel_order = int(libgap.Size(libgap.Kernel(action)))
    if int(libgap.Length(blocks)) != 12 or kernel_order != 2**11:
        raise ValueError("target is not a 12-block character-kernel action")
    return {
        "blockCount": int(libgap.Length(blocks)),
        "blockKernelOrder": kernel_order,
        "groupOrder": int(libgap.Size(group)),
        "quotientOrder": int(libgap.Size(quotient)),
        "quotientT": int(libgap.TransitiveIdentification(quotient)),
        "targetLabel": f"24T{target_t}",
    }


def character_alignment(quotient, quotient_t: int, probe_cores=None):
    """Recover rational squareclasses for every index-2 quotient character."""
    group = libgap.TransitiveGroup(12, quotient_t)
    generators = list(libgap.GeneratorsOfGroup(group))
    kernels = [group] + [
        subgroup
        for subgroup in libgap.NormalSubgroups(group)
        if int(libgap.Size(group)) == 2 * int(libgap.Size(subgroup))
    ]
    flips = [libgap.eval(f"({2 * index - 1},{2 * index})") for index in range(1, 13)]
    even_flips = [flips[0] * flips[index] for index in range(1, 12)]

    def lifted(permutation):
        images = []
        for point in range(1, 13):
            image = int(libgap.OnPoints(point, permutation))
            images.extend([2 * image - 1, 2 * image])
        return libgap.PermList(images)

    lifted_generators = [lifted(generator) for generator in generators]
    labels = []
    for kernel in kernels:
        action_generators = list(even_flips)
        for generator, lifted_generator in zip(generators, lifted_generators):
            action_generators.append(
                lifted_generator
                * (flips[0] if generator not in kernel else libgap.One(flips[0]))
            )
        action = libgap.Group(action_generators)
        if int(libgap.Size(action)) != 2**11 * int(libgap.Size(group)):
            raise ValueError("constructed character action has the wrong order")
        labels.append(f"24T{int(libgap.TransitiveIdentification(action))}")

    points = libgap.eval("[1..12]")
    profiles = []
    for conjugacy_class in libgap.ConjugacyClasses(group):
        representative = libgap.Representative(conjugacy_class)
        cycle_type = tuple(
            sorted(int(value) for value in libgap.CycleLengths(representative, points))
        )
        profiles.append(
            (
                cycle_type,
                [0 if representative in kernel else 1 for kernel in kernels],
            )
        )

    discriminant = ZZ(quotient.discriminant())
    observations = [[] for _kernel in kernels]
    for prime in prime_range(3, 5000):
        prime = int(prime)
        if discriminant % prime == 0:
            continue
        reduced = quotient.change_ring(GF(prime))
        if not reduced.is_squarefree():
            continue
        cycle_type = tuple(
            sorted(
                int(factor.degree())
                for factor, exponent in reduced.factor()
                for _ in range(int(exponent))
            )
        )
        matching = [bits for profile, bits in profiles if profile == cycle_type]
        for index in range(len(kernels)):
            values = {bits[index] for bits in matching}
            if len(values) == 1:
                observations[index].append((prime, values.pop()))

    if probe_cores is not None:
        probe_cores = sorted({int(value) for value in probe_cores})
        label_to_cores = {}
        rows = []
        for label, kernel, equations in zip(labels, kernels, observations):
            possible = [
                core
                for core in probe_cores
                if all(
                    (0 if kronecker(core, prime) == 1 else 1) == value
                    for prime, value in equations
                )
            ]
            label_to_cores.setdefault(label, set()).update(possible)
            rows.append(
                {
                    "characterKernelOrder": int(libgap.Size(kernel)),
                    "frobeniusEquations": len(equations),
                    "label": label,
                    "squarefreeNormCores": possible,
                }
            )
        core_to_labels = {}
        for label, cores in label_to_cores.items():
            for core in cores:
                core_to_labels.setdefault(core, set()).add(label)
        unambiguous = {}
        for core, core_labels in core_to_labels.items():
            if len(core_labels) == 1:
                label = next(iter(core_labels))
                unambiguous.setdefault(label, set()).add(core)
        return {
            "characters": rows,
            "coreToPossibleLabels": {
                str(core): sorted(values)
                for core, values in sorted(core_to_labels.items())
            },
            "labelToSquarefreeNormCores": {
                label: sorted(values)
                for label, values in sorted(label_to_cores.items())
            },
            "labelToUnambiguousSquarefreeNormCores": {
                label: sorted(values)
                for label, values in sorted(unambiguous.items())
            },
            "method": "provided-squareclass-frobenius-probe",
            "probedSquarefreeNormCores": probe_cores,
            "quotientOrder": int(libgap.Size(group)),
            "quotientT": quotient_t,
            "ramifiedPrimes": None,
        }

    ramified_primes = [int(prime) for prime, _exponent in discriminant.factor()]
    label_to_cores = {}
    rows = []
    for label, kernel, equations in zip(labels, kernels, observations):
        possible = []
        for mask in range(1 << len(ramified_primes)):
            core = 1
            for index, prime in enumerate(ramified_primes):
                if (mask >> index) & 1:
                    core *= prime
            if all(
                (0 if kronecker(core, prime) == 1 else 1) == value
                for prime, value in equations
            ):
                possible.append(core)
        if not possible:
            raise ValueError(
                f"no character squareclass satisfies Frobenius data for {label}"
            )
        label_to_cores.setdefault(label, set()).update(possible)
        rows.append(
            {
                "characterKernelOrder": int(libgap.Size(kernel)),
                "frobeniusEquations": len(equations),
                "label": label,
                "squarefreeNormCores": possible,
            }
        )
    core_to_labels = {}
    for label, cores in label_to_cores.items():
        for core in cores:
            core_to_labels.setdefault(core, set()).add(label)
    unambiguous = {}
    for core, core_labels in core_to_labels.items():
        if len(core_labels) == 1:
            label = next(iter(core_labels))
            unambiguous.setdefault(label, set()).add(core)
    return {
        "characters": rows,
        "coreToPossibleLabels": {
            str(core): sorted(values) for core, values in sorted(core_to_labels.items())
        },
        "labelToSquarefreeNormCores": {
            label: sorted(values) for label, values in sorted(label_to_cores.items())
        },
        "labelToUnambiguousSquarefreeNormCores": {
            label: sorted(values) for label, values in sorted(unambiguous.items())
        },
        "quotientOrder": int(libgap.Size(group)),
        "quotientT": quotient_t,
        "ramifiedPrimes": ramified_primes,
    }


def state_bits(sign_mask: int, norm, rational_primes: list[int]) -> list[int]:
    result = [(sign_mask >> index) & 1 for index in range(12)]
    rational_norm = QQ(norm)
    result.extend(int(rational_norm.valuation(prime)) & 1 for prime in rational_primes)
    return result


def search(source, target_t: int, target_r: int, core: ZZ, auxiliary_primes: list[int], maximum: int, witnesses: int, seed: int):
    quotient = source["quotient"]
    core = ZZ(core)
    core_primes = [int(value) for value in core.prime_divisors()]
    rational_primes = core_primes + auxiliary_primes
    field = NumberField(quotient.change_ring(QQ), "a")
    s_units = field.S_unit_group(proof=False, S=prod(rational_primes))
    generators = list(s_units.gens_values())
    embeddings = field.embeddings(RealField(160))
    columns = []
    for unit in generators:
        sign_mask = sum(
            1 << index
            for index, embedding in enumerate(embeddings)
            if embedding(unit) < 0
        )
        columns.append(state_bits(sign_mask, unit.norm(), rational_primes))
    matrix = Matrix(
        GF(2),
        12 + len(rational_primes),
        len(generators),
        lambda row, column: columns[column][row],
    )
    negative_count = 12 - target_r // 2
    sign_masks = [
        sum(1 << index for index in indexes)
        for indexes in itertools.combinations(range(12), negative_count)
    ]
    random.Random(seed).shuffle(sign_masks)
    solutions = []
    solvable_sign_masks = []
    norm_bits = [int(core.valuation(prime)) & 1 for prime in core_primes]
    for sign_mask in sign_masks:
        target = vector(
            GF(2),
            [(sign_mask >> index) & 1 for index in range(12)]
            + norm_bits
            + [0] * len(auxiliary_primes),
        )
        try:
            affine = SHARED.affine_solutions(
                matrix, target, maximum - len(solutions), seed ^ sign_mask
            )
            first = next(affine)
        except (ValueError, StopIteration):
            continue
        solvable_sign_masks.append(sign_mask)
        solutions.append((sign_mask, *first))
        for solution_mask, kernel_dimension in affine:
            solutions.append((sign_mask, solution_mask, kernel_dimension))
            if len(solutions) >= maximum:
                break
        if len(solutions) >= maximum:
            break
    if not solutions:
        return {
            "auxiliaryPrimes": auxiliary_primes,
            "candidateResults": [],
            "corePrimes": core_primes,
            "requestedR": target_r,
            "status": "target_signature_absent_from_s_unit_space",
        }

    SHARED.TARGET_T = target_t
    SHARED.TARGET_LABEL = f"24T{target_t}"
    profiles, identities = SHARED.maximal_joint_profiles()
    ring = PolynomialRing(ZZ, "x")
    x = ring.gen()
    rows = []
    for target_sign_mask, mask, kernel_dimension in solutions:
        element = field.one()
        selected = []
        for index, unit in enumerate(generators):
            if (mask >> index) & 1:
                element *= unit
                selected.append(index)
        scaled, square_scale = SHARED.integral_square_scale(element)
        norm = QQ(scaled.norm())
        row = {
            "affineKernelDimension": kernel_dimension,
            "negativeEmbeddingCount": sum(
                1 for embedding in embeddings if embedding(scaled) < 0
            ),
            "norm": str(norm),
            "normOverCoreIsSquare": SHARED.rational_is_square(norm / core),
            "selectedSUnitGeneratorIndexes": selected,
            "squareScale": str(square_scale),
            "targetSignMask": target_sign_mask,
        }
        if (
            not row["normOverCoreIsSquare"]
            or row["negativeEmbeddingCount"] != negative_count
        ):
            row["status"] = "failed_exact_norm_or_sign_check"
            rows.append(row)
            continue
        minimal = scaled.minpoly().change_ring(QQ)
        if minimal.degree() != 12 or any(QQ(value).denominator() != 1 for value in minimal):
            row["status"] = "nonintegral_or_nonprimitive_minpoly"
            rows.append(row)
            continue
        minimal_zz = PolynomialRing(ZZ, "z")([ZZ(value) for value in minimal.list()])
        candidate = ring(minimal_zz(x**2))
        line = SHARED.coefficient_line(candidate)
        row.update(
            {
                "candidateCoefficientLine": line,
                "candidateSha256": hashlib.sha256(line.encode()).hexdigest(),
                "containmentProof": {
                    "sameDegree12Field": True,
                    "targetSquarefreeNormCore": int(core),
                    "theorem": "the Frobenius-aligned rational norm character implies containment in the target character-kernel action",
                },
                "irreducible": bool(candidate.is_irreducible()),
                "minimalPolynomial": str(minimal_zz),
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "realRoots": int(candidate.number_of_real_roots()),
            }
        )
        if not row["irreducible"] or row["realRoots"] != target_r:
            row["status"] = "failed_degree24_checks"
            rows.append(row)
            continue
        row["maximalSubgroupCertificate"] = SHARED.frobenius_maximal_certificate(
            candidate, quotient, profiles, identities, witnesses
        )
        row["status"] = (
            f"certified_24T{target_t}_r{target_r}"
            if row["maximalSubgroupCertificate"]["complete"]
            else "contained_candidate_incomplete_maximal_certificate"
        )
        if row["status"].startswith("certified_"):
            row["fieldDiscriminantAbs"] = str(
                abs(ZZ(NumberField(candidate.change_ring(QQ), "b").discriminant()))
            )
        rows.append(row)
        if row["status"].startswith("certified_"):
            break
    return {
        "auxiliaryPrimes": auxiliary_primes,
        "candidateResults": rows,
        "corePrimes": core_primes,
        "requestedR": target_r,
        "solvableSignMasks": solvable_sign_masks,
        "sUnitGeneratorCount": len(generators),
        "sUnitPrimeIdealCount": len(s_units.primes()),
        "status": "completed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--target-r", type=int, default=24)
    parser.add_argument("--aux-primes", default="")
    parser.add_argument("--max-candidates", type=int, default=16)
    parser.add_argument("--witness-primes", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--norm-core", type=int)
    parser.add_argument("--max-team-count", type=int, choices=(0, 1, 2, 3, 4), default=0)
    args = parser.parse_args()
    if args.target_r < 0 or args.target_r > 24 or args.target_r % 2:
        raise ValueError("--target-r must be an even integer from 0 through 24")
    target_t = parse_label(args.target_label)
    auxiliary = [int(value) for value in args.aux_primes.split(",") if value.strip()]
    source = load_source(args.db, args.submission_id, args.polynomial_index)
    live_gold = load_live_gold(
        args.db, args.target_label, args.target_r, args.max_team_count
    )
    structure = target_structure(target_t)
    alignment = character_alignment(
        source["quotient"],
        structure["quotientT"],
        (
            [source["squarefreeNormCore"], args.norm_core]
            if args.norm_core is not None
            else None
        ),
    )
    source_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(source["label"], [])
    if source["squarefreeNormCore"] not in source_cores:
        raise ValueError(
            "arithmetic character alignment does not recover the verified source"
        )
    target_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(args.target_label, [])
    if not target_cores:
        raise ValueError("target label is absent from the aligned character actions")
    if args.norm_core is None:
        norm_core = min(target_cores)
    elif args.norm_core not in target_cores:
        raise ValueError(
            f"requested norm core {args.norm_core} is not aligned with target: {target_cores}"
        )
    else:
        norm_core = args.norm_core
    audit = {
        "networkCalls": 0,
        "liveTarget": live_gold,
        "characterAlignment": alignment,
        "source": {key: value for key, value in source.items() if key != "quotient"},
        "submissionCalls": 0,
        "targetStructure": structure,
    }
    result = {
        "audit": audit,
        "search": search(
            source,
            target_t,
            args.target_r,
            ZZ(norm_core),
            auxiliary,
            args.max_candidates,
            args.witness_primes,
            args.seed,
        ),
    }
    certified = [
        row
        for row in result["search"].get("candidateResults", [])
        if str(row.get("status", "")).startswith("certified_")
    ]
    if args.manifest is not None:
        if len(certified) != 1:
            raise ValueError(
                f"refusing to stage: expected one certified candidate, found {len(certified)}"
            )
        manifest_text = certified[0]["candidateCoefficientLine"] + "\n"
        args.manifest.resolve().write_text(manifest_text, encoding="utf-8")
        result["staging"] = {
            "bytes": len(manifest_text.encode()),
            "candidateSha256": certified[0]["candidateSha256"],
            "manifest": str(args.manifest.resolve()),
            "manifestSha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
            "polynomials": 1,
            "submissionCalls": 0,
        }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.resolve().write_text(rendered, encoding="utf-8")
        print(json.dumps({"output": str(args.output.resolve()), "status": result["search"]["status"]}))
    else:
        print(rendered, end="")
    return 0 if certified else 2


if __name__ == "__main__":
    raise SystemExit(main())

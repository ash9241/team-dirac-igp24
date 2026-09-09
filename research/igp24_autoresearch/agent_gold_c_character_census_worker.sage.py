#!/usr/bin/env sage -python
"""Exact field fingerprint and character alignment for one census source.

The worker is deliberately process-isolated because Sage/PARI/GAP state is
not thread safe.  It reads one locally verified even degree-24 polynomial,
computes a PARI-reduced defining polynomial for exact degree-12 field
deduplication, verifies the source character, and records every requested
unambiguous target norm core.  It performs no network, submission, search, or
staging operation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    NumberField,
    PolynomialRing,
    QQ,
    ZZ,
    kronecker,
    libgap,
    prime_range,
    prod,
    vector,
)


ROOT = Path(__file__).resolve().parent


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("gold_c_census_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def exact_linear_character_alignment(quotient, quotient_t: int):
    """Equivalent to the shared alignment, with a GF(2) squareclass solve.

    The shared implementation loops over all ``2^omega(discriminant)``
    ramified squareclasses.  Every Frobenius/Kronecker equation is linear in
    the ramified-prime exponent bits, so an exact affine solve produces the
    identical set without the exponential outer scan.
    """
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
    for prime_value in prime_range(3, 5000):
        prime = int(prime_value)
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

    ramified_primes = [int(prime) for prime, _exponent in discriminant.factor()]
    label_to_cores = {}
    rows = []
    for label, kernel, equations in zip(labels, kernels, observations):
        rhs = vector(GF(2), [value for _prime, value in equations])
        matrix = Matrix(
            GF(2),
            len(equations),
            len(ramified_primes),
            lambda row, column: (
                0
                if kronecker(ramified_primes[column], equations[row][0]) == 1
                else 1
            ),
        )
        try:
            particular = matrix.solve_right(rhs)
        except ValueError as exc:
            raise ValueError(
                f"no character squareclass satisfies Frobenius data for {label}"
            ) from exc
        kernel_basis = list(matrix.right_kernel().basis())
        if len(kernel_basis) > 20:
            raise ValueError(
                f"character squareclass affine dimension {len(kernel_basis)} is too large"
            )
        possible = []
        for mask in range(1 << len(kernel_basis)):
            bits = particular + sum(
                (kernel_basis[index] for index in range(len(kernel_basis)) if (mask >> index) & 1),
                vector(GF(2), len(ramified_primes)),
            )
            possible.append(
                int(
                    prod(
                        ramified_primes[index]
                        for index, bit in enumerate(bits)
                        if bit
                    )
                )
            )
        possible = sorted(set(possible))
        label_to_cores.setdefault(label, set()).update(possible)
        rows.append(
            {
                "affineSolutionDimension": len(kernel_basis),
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
        "method": "exact-gf2-ramified-squareclass-solve",
        "quotientOrder": int(libgap.Size(group)),
        "quotientT": quotient_t,
        "ramifiedPrimes": ramified_primes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--quotient-t", required=True, type=int)
    parser.add_argument("--target-label", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    helper = load_helper()
    source = helper.load_source(args.db, args.submission_id, args.polynomial_index)
    quotient_line = ",".join(str(value) for value in source["quotient"])
    quotient_sha256 = hashlib.sha256(quotient_line.encode()).hexdigest()

    field = NumberField(source["quotient"].change_ring(QQ), "a")
    reduced_pari = field.pari_polynomial("z").polredabs()
    reduced = PolynomialRing(ZZ, "z")(reduced_pari)
    reduced_line = ",".join(str(value) for value in reduced)
    field_sha256 = hashlib.sha256(reduced_line.encode()).hexdigest()

    alignment = exact_linear_character_alignment(source["quotient"], args.quotient_t)
    source_cores = [
        int(value)
        for value in alignment[
            "labelToUnambiguousSquarefreeNormCores"
        ].get(source["label"], [])
    ]
    source_aligned = int(source["squarefreeNormCore"]) in source_cores
    requested = sorted(set(args.target_label))
    target_cores = {
        label: [
            int(value)
            for value in alignment[
                "labelToUnambiguousSquarefreeNormCores"
            ].get(label, [])
        ]
        for label in requested
    }
    target_cores = {label: values for label, values in target_cores.items() if values}

    result = {
        "alignment": alignment,
        "fieldCanonicalPolynomial": reduced_line,
        "fieldCanonicalSha256": field_sha256,
        "networkCalls": 0,
        "quotientPolynomialSha256": quotient_sha256,
        "requestedTargetLabels": requested,
        "source": {key: value for key, value in source.items() if key != "quotient"},
        "sourceAlignmentRecovered": source_aligned,
        "submissionCalls": 0,
        "targetNormCores": target_cores,
    }
    if not source_aligned:
        status = "source_alignment_mismatch"
    elif not target_cores:
        status = "no_unambiguous_target_core"
    else:
        status = "aligned_target"
    result["status"] = status

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    output = args.output.resolve()
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "alignedTargetLabels": sorted(target_cores) if source_aligned else [],
                "artifact": str(output),
                "artifactSha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "fieldCanonicalSha256": field_sha256,
                "fieldDiscAbs": source["quotientFieldDiscAbs"],
                "polynomialIndex": args.polynomial_index,
                "quotientPolynomialSha256": quotient_sha256,
                "quotientT": args.quotient_t,
                "sourceLabel": source["label"],
                "status": status,
                "submissionId": args.submission_id,
            },
            sort_keys=True,
        )
    )
    return 0 if status == "aligned_target" else 2


if __name__ == "__main__":
    raise SystemExit(main())

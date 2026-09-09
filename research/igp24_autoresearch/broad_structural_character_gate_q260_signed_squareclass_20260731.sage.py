#!/usr/bin/env sage -python
"""Exact q260 broad gate with the infinite-place squareclass included.

The generic ramified-squareclass alignment only spans the positive products
of finite ramified primes.  The live q260 target ``24T23756/r18`` needs three
negative real embeddings, so its rational norm squareclass is negative.
This wrapper adds ``-1`` as the exact infinite-place basis element and then
runs the normal compact-squareclass broad gate.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    ZZ,
    kronecker,
    libgap,
    prime_range,
    prod,
    vector,
)
from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
DRIVER_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"


def squareclass_compact_ideal_generator(ideal):
    """Return an exact compact principal generator modulo squares."""
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()
    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    if any(int(value) for value in principal_data[0]):
        raise ValueError("p-Selmer requested a generator of a nonprincipal ideal")
    compact = principal_data[1]
    rows, columns = (int(value) for value in compact.matsize())
    if columns != 2:
        raise ValueError(
            f"unexpected compact factor matrix shape {(rows, columns)}"
        )
    representative = field.one()
    for row in range(rows):
        if int(compact[row, 1]) % 2:
            representative *= field(bnf.nfbasistoalg(compact[row, 0]))
    return representative


def exact_signed_linear_character_alignment(quotient, quotient_t: int):
    """Recover signed ramified norm cores by an exact GF(2) solve."""
    group = libgap.TransitiveGroup(12, quotient_t)
    generators = list(libgap.GeneratorsOfGroup(group))
    kernels = [group] + [
        subgroup
        for subgroup in libgap.NormalSubgroups(group)
        if int(libgap.Size(group)) == 2 * int(libgap.Size(subgroup))
    ]
    flips = [
        libgap.eval(f"({2 * index - 1},{2 * index})")
        for index in range(1, 13)
    ]
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
        for generator, lifted_generator in zip(
            generators, lifted_generators
        ):
            action_generators.append(
                lifted_generator
                * (
                    flips[0]
                    if generator not in kernel
                    else libgap.One(flips[0])
                )
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
            sorted(
                int(value)
                for value in libgap.CycleLengths(representative, points)
            )
        )
        profiles.append(
            (
                cycle_type,
                [
                    0 if representative in kernel else 1
                    for kernel in kernels
                ],
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
        matching = [
            bits for profile, bits in profiles if profile == cycle_type
        ]
        for index in range(len(kernels)):
            values = {bits[index] for bits in matching}
            if len(values) == 1:
                observations[index].append((prime, values.pop()))

    ramified_primes = [
        int(prime) for prime, _exponent in discriminant.factor()
    ]
    squareclass_basis = [-1, *ramified_primes]
    label_to_cores = {}
    rows = []
    for label, kernel, equations in zip(labels, kernels, observations):
        rhs = vector(GF(2), [value for _prime, value in equations])
        matrix = Matrix(
            GF(2),
            len(equations),
            len(squareclass_basis),
            lambda row, column: (
                0
                if kronecker(
                    squareclass_basis[column], equations[row][0]
                )
                == 1
                else 1
            ),
        )
        try:
            particular = matrix.solve_right(rhs)
        except ValueError as exc:
            raise ValueError(
                f"no signed squareclass satisfies Frobenius data for {label}"
            ) from exc
        kernel_basis = list(matrix.right_kernel().basis())
        if len(kernel_basis) > 20:
            raise ValueError(
                f"character squareclass affine dimension "
                f"{len(kernel_basis)} is too large"
            )
        possible = []
        for mask in range(1 << len(kernel_basis)):
            bits = particular + sum(
                (
                    kernel_basis[index]
                    for index in range(len(kernel_basis))
                    if (mask >> index) & 1
                ),
                vector(GF(2), len(squareclass_basis)),
            )
            possible.append(
                int(
                    prod(
                        squareclass_basis[index]
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
        "method": "exact-gf2-signed-ramified-squareclass-solve",
        "quotientOrder": int(libgap.Size(group)),
        "quotientT": quotient_t,
        "ramifiedPrimes": ramified_primes,
        "squareclassBasis": squareclass_basis,
    }


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "q260_signed_broad_driver", DRIVER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {DRIVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    driver = load_driver()
    original_load_base = driver.load_base

    def load_signed_base(quotient_t: int, target_labels: list[str]):
        base = original_load_base(quotient_t, target_labels)
        base.ALIGNMENT.exact_linear_character_alignment = (
            exact_signed_linear_character_alignment
        )
        return base

    driver.load_base = load_signed_base
    return int(driver.main())


if __name__ == "__main__":
    raise SystemExit(main())

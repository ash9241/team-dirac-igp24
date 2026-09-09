"""Shared quadratic-resolvent 8x3 fiber-product construction."""

from __future__ import annotations

from dataclasses import replace

from routeA.constructions.compositum_8x3 import (
    ComponentPolynomial,
    CompositumCandidate,
    generate_compositum,
)


def shares_sign_resolvent(octic: ComponentPolynomial, cubic: ComponentPolynomial) -> bool:
    """Whether both stem polynomials define the same discriminant quadratic field."""

    return (
        octic.degree == 8
        and cubic.degree == 3
        and octic.discriminant_squareclass not in (None, 1)
        and octic.discriminant_squareclass == cubic.discriminant_squareclass
    )


def generate_fiber_compositum(
    octic: ComponentPolynomial,
    cubic: ComponentPolynomial,
    *,
    primitive_parameter: int,
    target_t: int,
    label_probability: float = 0.9,
) -> CompositumCandidate:
    if not shares_sign_resolvent(octic, cubic):
        raise ValueError("components do not share the discriminant quadratic resolvent")
    candidate = generate_compositum(
        octic,
        cubic,
        primitive_parameter=primitive_parameter,
        target_t=target_t,
        label_probability=label_probability,
    )
    lineage = f"8x3-fiber-c2:{octic.component_id}:{cubic.component_id}"
    return replace(
        candidate,
        recipe_family="fiber_product_8x3_c2",
        recipe_lineage=lineage,
        recipe_id=f"{lineage}:c={primitive_parameter}",
        construction_overgroup=f"{octic.group_label}xC2{cubic.group_label}",
        discriminant_disjoint=False,
    )

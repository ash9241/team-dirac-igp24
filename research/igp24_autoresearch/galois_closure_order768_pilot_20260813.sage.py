#!/usr/bin/env sage
"""Feasibility pilot for sibling degree-24 fixed fields of a 24T2053 field."""

import time

from sage.all import NumberField, PolynomialRing, QQ


COEFFICIENTS = [
    10522600191603712,
    -29244419533527040,
    32868375144148224,
    -19110696511380480,
    6911879076437040,
    -2548954908944064,
    1095812080689088,
    -304592795602656,
    60906881918800,
    -21632618580528,
    5114076922120,
    -506502564816,
    172355974374,
    -29885314544,
    2669774932,
    -124022768,
    90958205,
    -8448012,
    362256,
    -9192,
    17297,
    -512,
    26,
    0,
    1,
]


ring = PolynomialRing(QQ, "x")
polynomial = ring(COEFFICIENTS)
print(
    {
        "event": "source_ready",
        "degree": int(polynomial.degree()),
        "irreducible": bool(polynomial.is_irreducible()),
        "real_roots": int(polynomial.number_of_real_roots()),
    },
    flush=True,
)
started = time.time()
source = NumberField(polynomial, "a")
closure, embedding = source.galois_closure(names="z", map=True)
print(
    {
        "event": "closure_ready",
        "degree": int(closure.degree()),
        "seconds": time.time() - started,
        "source_image_minpoly_degree": int(embedding(source.gen()).minpoly().degree()),
    },
    flush=True,
)

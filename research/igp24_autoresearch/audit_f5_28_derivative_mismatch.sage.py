#!/usr/bin/env sage -python
"""Preserve the coefficient-exact evidence that blocks the derivative candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "outbox" / "agent_f5_28_derivative_24T15337_r20.txt"
OUTPUT = ROOT / "data" / "f5_28_derivative_mismatch_audit.json"
QUOTIENT_LINE = (
    "14565989,-2114295053,11853787534,-18764977906,13941011872,"
    "-5699782331,1370959475,-199518023,17549862,-900974,24660,-293,1"
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def line(polynomial) -> str:
    return ",".join(str(value) for value in polynomial.list())


def main() -> int:
    if OUTPUT.exists():
        raise ValueError(f"refusing to overwrite {OUTPUT}")
    ring_t = PolynomialRing(ZZ, "t")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    t = ring_t.gen()
    x = ring_x.gen()
    q = ring_t([ZZ(value) for value in QUOTIENT_LINE.split(",")])

    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    g = t * q.derivative()
    g_y = sum(g[index] * y**index for index in range(g.degree() + 1))
    h = ring_z(q_y.resultant(z - g_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    p = ring_x(h(x**2))

    manifest_line = MANIFEST.read_text(encoding="utf-8").strip()
    manifest_polynomial = ring_x(
        [ZZ(value) for value in manifest_line.split(",")]
    )
    h_line = line(h)
    p_line = line(p)
    manifest_hash = sha(manifest_line)

    factors = [
        (factor, int(exponent))
        for factor, exponent in h.symmetric_power(2, monic=True).factor()
    ]
    degree_twelve = [
        factor
        for factor, exponent in factors
        if factor.degree() == 12 and exponent == 1
    ]
    reconstructed = (
        ring_x(degree_twelve[0](x**2)) if len(degree_twelve) == 1 else None
    )
    reconstructed_line = line(reconstructed) if reconstructed is not None else None

    audit = {
        "blocked": True,
        "blockReason": (
            "The stated transform g(t)=t*q'(t) reconstructs a different "
            "degree-24 polynomial from the staged manifest."
        ),
        "claimedManifest": {
            "coefficientLine": manifest_line,
            "coefficientSha256": manifest_hash,
            "irreducible": bool(manifest_polynomial.is_irreducible()),
            "r": int(manifest_polynomial.number_of_real_roots()),
        },
        "exactReconstruction": {
            "candidateAfterPairFactor": {
                "coefficientLine": reconstructed_line,
                "coefficientSha256": (
                    sha(reconstructed_line)
                    if reconstructed_line is not None
                    else None
                ),
                "equalsClaimedManifest": reconstructed_line == manifest_line,
                "r": (
                    int(reconstructed.number_of_real_roots())
                    if reconstructed is not None
                    else None
                ),
            },
            "factorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent}
                for factor, exponent in factors
            ],
            "resultantLift": {
                "coefficientLine": p_line,
                "coefficientSha256": sha(p_line),
                "equalsClaimedManifest": p_line == manifest_line,
                "hCoefficientLine": h_line,
                "hCoefficientSha256": sha(h_line),
                "irreducible": bool(p.is_irreducible()),
                "r": int(p.number_of_real_roots()),
            },
        },
        "quotientLine": QUOTIENT_LINE,
        "quotientSha256": sha(QUOTIENT_LINE),
        "submissionAllowed": False,
    }
    rendered = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "audit": str(OUTPUT.relative_to(ROOT)),
                "auditSha256": sha(rendered),
                "manifestSha256": manifest_hash,
                "resultantLiftSha256": sha(p_line),
                "reconstructedPairCandidateSha256": (
                    sha(reconstructed_line)
                    if reconstructed_line is not None
                    else None
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

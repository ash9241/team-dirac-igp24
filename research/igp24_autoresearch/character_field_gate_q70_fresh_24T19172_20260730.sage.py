#!/usr/bin/env sage -python
"""Run the exact character gate on fresh diagonal-invariant 12T70 fields."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sage.all import AA, RealIntervalField


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_field_gate_q138_20260730.sage.py"
INVENTORY = (
    ROOT
    / "data"
    / "character_fresh_field_inventory_q70_diagonal_invariant_20260730.json"
)


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "character_gate_q70_fresh_24T19172", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
DRIVER.DEFAULT_LOCAL_OUTPUT = (
    ROOT
    / "data"
    / "character_field_gate_q70_fresh_24T19172_local_20260730.json"
)
DRIVER.QUOTIENT_T = 70
DRIVER.FAMILIES = {
    70: {
        "ambiguousTargetLabels": ["24T19172"],
        "targetLabels": ["24T19172"],
    }
}
DRIVER.EXPECTED_LIVE = {
    "24T19172": [4, 20, 24],
}
DRIVER.BASE.FAMILIES = DRIVER.FAMILIES


def robust_sign_mask(field, element, embeddings) -> int:
    """Certify difficult Selmer signs with adaptive interval precision."""
    precision = 160
    while precision <= 10240:
        current = (
            embeddings
            if precision == 160
            else field.embeddings(RealIntervalField(precision))
        )
        values = [embedding(element) for embedding in current]
        if all(not value.contains_zero() for value in values):
            return sum(
                1 << index
                for index, value in enumerate(values)
                if value < 0
            )
        precision *= 2
    # A badly conditioned power-basis expression can keep interval evaluation
    # inconclusive even at very high precision.  Algebraic-real embeddings use
    # exact root isolation and therefore give a rigorous final sign decision.
    exact_embeddings = field.embeddings(AA)
    exact_values = [embedding(element) for embedding in exact_embeddings]
    if len(exact_values) != len(embeddings) or any(
        value.is_zero() for value in exact_values
    ):
        raise ArithmeticError(
            "could not certify the sign of a nonzero Selmer element"
        )
    return sum(
        1 << index
        for index, value in enumerate(exact_values)
        if value.sign() < 0
    )


DRIVER.BASE.sign_mask = robust_sign_mask


def fresh_fields(_db: Path, _structures: Path, _ring) -> list[dict]:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    rows = payload["freshCanonicalTotallyRealFields"]
    if not rows:
        raise ValueError("fresh q70 inventory is empty")
    output = []
    for row in rows:
        if row.get("alreadyAudited"):
            raise ValueError("fresh q70 inventory contains an audited field")
        if int(row["quotientT12"]) != 70:
            raise ValueError("fresh inventory contains a non-q70 field")
        output.append(
            {
                "canonicalPolynomial": str(row["canonicalPolynomial"]),
                "construction": dict(row["construction"]),
                "fieldCanonicalSha256": str(
                    row["fieldCanonicalSha256"]
                ),
                "family": "12T70",
                "provenance": str(row["provenance"]),
                "quotientT12": 70,
                "sourceRows": list(row["sourceRows"]),
            }
        )
    return sorted(output, key=lambda row: row["fieldCanonicalSha256"])


DRIVER.EXPECTED_CANONICAL_FIELDS = len(
    fresh_fields(None, None, None)
)
DRIVER.EXPECTED_SOURCE_LABELS = {"synthetic-12T70"}
DRIVER.canonical_fields = fresh_fields


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

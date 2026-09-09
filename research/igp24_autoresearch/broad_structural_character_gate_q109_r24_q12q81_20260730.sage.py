#!/usr/bin/env sage -python
"""Exact q109 character gate restricted to the unqueued 24T19727/r24 route."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.all import AA


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "q109_r24_broad_character_gate", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
_ORIGINAL_LIVE = DRIVER.strict_live_pairs
_ORIGINAL_LOAD_BASE = DRIVER.load_base
_EXACT_EMBEDDINGS = {}


def r24_only_live(db: Path, target_labels: list[str]) -> dict[str, list[dict]]:
    live = _ORIGINAL_LIVE(db, target_labels)
    filtered = {
        label: [row for row in rows if int(row["r"]) == 24]
        for label, rows in live.items()
    }
    filtered = {label: rows for label, rows in filtered.items() if rows}
    expected = {"24T19727": [24]}
    actual = {
        label: [int(row["r"]) for row in rows]
        for label, rows in filtered.items()
    }
    if actual != expected:
        raise ValueError(
            f"q109 unqueued live-pair mismatch: {actual}, expected {expected}"
        )
    return filtered


def exact_sign_mask(field, element, _embeddings) -> int:
    """Use exact algebraic-real signs, cached in the common root ordering."""
    key = id(field)
    embeddings = _EXACT_EMBEDDINGS.get(key)
    if embeddings is None:
        embeddings = field.embeddings(AA)
        _EXACT_EMBEDDINGS[key] = embeddings
    values = [embedding(element) for embedding in embeddings]
    if any(value.is_zero() for value in values):
        raise ArithmeticError("zero encountered in a Selmer sign vector")
    return sum(
        1 << index
        for index, value in enumerate(values)
        if value.sign() < 0
    )


def robust_load_base(quotient_t: int, target_labels: list[str]):
    module = _ORIGINAL_LOAD_BASE(quotient_t, target_labels)
    module.sign_mask = exact_sign_mask
    return module


DRIVER.strict_live_pairs = r24_only_live
DRIVER.load_base = robust_load_base


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

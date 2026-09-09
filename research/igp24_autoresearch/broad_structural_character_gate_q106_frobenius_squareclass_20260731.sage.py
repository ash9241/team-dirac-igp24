#!/usr/bin/env sage -python
"""Exact q106 lifts using accepted-parent Frobenius disambiguation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = (
    ROOT / "broad_structural_character_gate_q191_frobenius_squareclass_20260731.sage.py"
)
QUOTIENT_T = 106
QUOTIENT_ORDER = 192


def load_base():
    spec = importlib.util.spec_from_file_location(
        "q106_frobenius_broad", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.QUOTIENT_T = QUOTIENT_T
    module.QUOTIENT_ORDER = QUOTIENT_ORDER
    return module


def main() -> int:
    base = load_base()

    def q106_load_base(quotient_t: int, target_labels: list[str]):
        if int(quotient_t) != QUOTIENT_T:
            raise ValueError("q106 launcher received another quotient")
        return base.ORIGINAL_LOAD_BASE(quotient_t, target_labels)

    base.DRIVER.load_base = q106_load_base
    base.DRIVER.exact_galois_certificate = (
        base.structural_frobenius_certificate
    )
    selmer_group._ideal_generator = base.squareclass_compact_ideal_generator
    return base.DRIVER.main()


if __name__ == "__main__":
    raise SystemExit(main())

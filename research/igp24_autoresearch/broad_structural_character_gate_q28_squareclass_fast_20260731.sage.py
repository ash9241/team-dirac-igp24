#!/usr/bin/env sage -python
"""Run q28 lifts from the uniquely pinned 24T12832 parent."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE = ROOT / "broad_structural_character_gate_q195_squareclass_fast_20260731.sage.py"


def load_base():
    spec = importlib.util.spec_from_file_location("q28_fast_base", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE_MODULE = load_base()
BASE_MODULE.QUOTIENT_T = 28
BASE_MODULE.QUOTIENT_ORDER = 48
BASE_MODULE.SOURCE_LABEL = "24T12832"
BASE_MODULE.DRIVER.exact_galois_certificate = (
    BASE_MODULE.structural_certificate
)


if __name__ == "__main__":
    selmer_group._ideal_generator = (
        BASE_MODULE.squareclass_compact_ideal_generator
    )
    raise SystemExit(BASE_MODULE.DRIVER.main())

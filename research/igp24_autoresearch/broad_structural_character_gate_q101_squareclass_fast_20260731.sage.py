#!/usr/bin/env sage -python
"""Run q101 lifts from uniquely pinned parents with compact squareclasses."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = (
    ROOT / "broad_structural_character_gate_q185_squareclass_fast_20260731.sage.py"
)


def load_base():
    spec = importlib.util.spec_from_file_location("q101_fast_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.QUOTIENT_T = 101
    module.QUOTIENT_ORDER = 192
    return module


if __name__ == "__main__":
    base = load_base()
    selmer_group._ideal_generator = base.squareclass_compact_ideal_generator
    raise SystemExit(base.DRIVER.main())

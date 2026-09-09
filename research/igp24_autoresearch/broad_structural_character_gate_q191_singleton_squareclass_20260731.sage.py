#!/usr/bin/env sage -python
"""Run only singleton-core q191 lifts with compact squareclass generators."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"


def squareclass_compact_ideal_generator(ideal):
    """Return an exact compact principal generator modulo squares."""
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()
    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    if any(int(value) for value in principal_data[0]):
        raise ValueError("2-Selmer requested a generator of a nonprincipal ideal")
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


def load_driver():
    spec = importlib.util.spec_from_file_location("q191_singleton_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
ORIGINAL_LOAD_BASE = DRIVER.load_base


def singleton_core_load_base(quotient_t: int, target_labels: list[str]):
    if int(quotient_t) != 191:
        raise ValueError("q191 singleton launcher received another quotient")
    base = ORIGINAL_LOAD_BASE(quotient_t, target_labels)
    base.FAMILIES[quotient_t]["ambiguousTargetLabels"] = []
    return base


DRIVER.load_base = singleton_core_load_base


if __name__ == "__main__":
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    raise SystemExit(DRIVER.main())

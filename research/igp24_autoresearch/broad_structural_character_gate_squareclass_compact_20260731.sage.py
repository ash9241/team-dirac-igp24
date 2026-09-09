#!/usr/bin/env sage -python
"""Run the broad structural gate with compact generators modulo squares."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BROAD_GATE = ROOT / "broad_structural_character_gate_20260730.sage.py"


def squareclass_compact_ideal_generator(ideal):
    """Return the exact compact principal generator's squareclass."""
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()

    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    if any(int(value) for value in principal_data[0]):
        raise ValueError(
            "p-Selmer requested a generator of a nonprincipal ideal"
        )

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


def load_broad_gate():
    spec = importlib.util.spec_from_file_location(
        "broad_character_gate_squareclass_compact", BROAD_GATE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BROAD_GATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    return int(load_broad_gate().main())


if __name__ == "__main__":
    raise SystemExit(main())

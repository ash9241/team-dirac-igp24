#!/usr/bin/env sage -python
"""Run the exact character gate with factorwise PARI compact generators."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
PRIORITY_GATE = ROOT / "character_field_gate_priority_20260730.sage.py"


def pari_compact_ideal_generator(ideal):
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()

    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    if any(int(value) for value in principal_data[0]):
        raise ValueError("p-Selmer requested a generator of a nonprincipal ideal")

    compact = principal_data[1]
    rows, columns = (int(value) for value in compact.matsize())
    if columns != 2:
        raise ValueError(
            f"unexpected compact factor matrix shape {(rows, columns)}"
        )

    generator = None
    for row in range(rows):
        exponent = int(compact[row, 1])
        factor = bnf.nfbasistoalg(compact[row, 0])
        powered = factor ** exponent
        generator = powered if generator is None else generator * powered
    if generator is None:
        return field.one()
    return field(generator)


def load_priority_gate():
    spec = importlib.util.spec_from_file_location(
        "character_gate_pari_compact_priority", PRIORITY_GATE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {PRIORITY_GATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    selmer_group._ideal_generator = pari_compact_ideal_generator
    return int(load_priority_gate().main())


if __name__ == "__main__":
    raise SystemExit(main())

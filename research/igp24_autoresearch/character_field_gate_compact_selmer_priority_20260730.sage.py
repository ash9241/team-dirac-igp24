#!/usr/bin/env sage -python
"""Run the priority character gate with exact compact ideal factorback.

Sage's p-Selmer implementation asks PARI for a certified compact
principal-ideal generator and then expands the entire compact product in
one ``nffactorback`` call.  Some degree-12 fields overflow the PARI stack
during that expansion.  This launcher preserves the same p-Selmer
algorithm, but expands the certified factors one at a time and verifies
that the resulting element generates the requested ideal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
PRIORITY_GATE = ROOT / "character_field_gate_priority_20260730.sage.py"


def compact_ideal_generator(ideal):
    """Return an exact generator using PARI's certified compact factors."""
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()

    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    class_log = [int(value) for value in principal_data[0]]
    if any(class_log):
        raise ValueError("p-Selmer requested a generator of a nonprincipal ideal")

    compact = principal_data[1]
    rows, columns = (int(value) for value in compact.matsize())
    if columns != 2:
        raise ValueError(f"unexpected compact factor matrix shape {(rows, columns)}")

    generator = field.one()
    for row in range(rows):
        factor = field(bnf.nfbasistoalg(compact[row, 0]))
        generator *= factor ** int(compact[row, 1])

    if field.ideal(generator) != ideal:
        raise ValueError("compact factorback generator failed its ideal check")
    return generator


def load_priority_gate():
    spec = importlib.util.spec_from_file_location(
        "character_gate_compact_priority", PRIORITY_GATE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {PRIORITY_GATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    selmer_group._ideal_generator = compact_ideal_generator
    return int(load_priority_gate().main())


if __name__ == "__main__":
    raise SystemExit(main())

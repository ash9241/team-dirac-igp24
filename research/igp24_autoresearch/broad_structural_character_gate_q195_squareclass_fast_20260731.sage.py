#!/usr/bin/env sage -python
"""Run q195 lifts from the uniquely pinned 24T14327 parent."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
BASE_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"
QUOTIENT_T = 195
QUOTIENT_ORDER = 1152
SOURCE_LABEL = "24T14327"


def squareclass_compact_ideal_generator(ideal):
    """Return an exact compact principal generator modulo squares."""
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
    representative = field.one()
    for row in range(rows):
        if int(compact[row, 1]) % 2:
            representative *= field(bnf.nfbasistoalg(compact[row, 0]))
    return representative


def load_driver():
    spec = importlib.util.spec_from_file_location("q195_fast_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()


def structural_certificate(field_row: dict, quotient_t: int, _ring) -> dict:
    if int(quotient_t) != QUOTIENT_T:
        raise ValueError("q195 fast launcher received another quotient")
    source_rows = field_row.get("sourceRows", [])
    if not source_rows:
        raise ValueError("q195 source rows are absent")
    if {str(row.get("label")) for row in source_rows} != {SOURCE_LABEL}:
        raise ValueError("q195 field is not pinned solely by 24T14327")
    if not all(row.get("scoreable") is True for row in source_rows):
        raise ValueError("q195 source row is not accepted/scoreable")

    parents = []
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("label") == SOURCE_LABEL:
                parents.append(row)
    if len(parents) != 1:
        raise ValueError("q195 structural parent is absent or duplicated")
    systems = [
        system
        for system in parents[0].get("blockSystems", [])
        if system.get("shape") == "12x2"
    ]
    if len(systems) != 1:
        raise ValueError("24T14327 lacks a unique 12x2 block system")
    system = systems[0]
    if (
        system.get("quotientActionLabel") != f"12T{QUOTIENT_T}"
        or int(system.get("quotientActionOrder", -1)) != QUOTIENT_ORDER
    ):
        raise ValueError("24T14327 q195 quotient certificate changed")
    return {
        "algorithm": (
            "verifier-accepted parent label plus unique exact 12x2 block action"
        ),
        "blockKernelOrder": int(system.get("blockKernelOrder", -1)),
        "degree": 12,
        "order": QUOTIENT_ORDER,
        "parentLabel": SOURCE_LABEL,
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{QUOTIENT_T}",
        "transitiveNumber": QUOTIENT_T,
        "uniqueParentBlockSystem": True,
    }


DRIVER.exact_galois_certificate = structural_certificate


if __name__ == "__main__":
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    raise SystemExit(DRIVER.main())

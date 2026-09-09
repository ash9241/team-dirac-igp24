#!/usr/bin/env sage -python
"""Run singleton-core q214 lifts on the fresh dense-parent fixed field."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
BASE_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"
QUOTIENT_T = 214
QUOTIENT_ORDER = 1296
SOURCE_LABEL = "24T22565"
BLOCK_KERNEL_ORDER = 2048


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
    spec = importlib.util.spec_from_file_location("q214_dense_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
ORIGINAL_LOAD_BASE = DRIVER.load_base


def singleton_core_load_base(quotient_t: int, target_labels: list[str]):
    base = ORIGINAL_LOAD_BASE(quotient_t, target_labels)
    base.FAMILIES[quotient_t]["ambiguousTargetLabels"] = []
    return base


def structural_certificate(field_row: dict, quotient_t: int, _ring) -> dict:
    if int(quotient_t) != QUOTIENT_T:
        raise ValueError("q214 dense launcher received another quotient")
    source_rows = field_row.get("sourceRows", [])
    if not source_rows or {
        str(row.get("label")) for row in source_rows
    } != {SOURCE_LABEL}:
        raise ValueError("q214 dense source-label pin changed")
    if not all(row.get("scoreable") is True or row.get("corpus") for row in source_rows):
        raise ValueError("q214 dense source row is not accepted/scoreable")

    parents = []
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("label") == SOURCE_LABEL:
                parents.append(row)
    if len(parents) != 1:
        raise ValueError("q214 dense structural parent is absent or duplicated")
    systems = [
        system
        for system in parents[0].get("blockSystems", [])
        if system.get("shape") == "12x2"
    ]
    if len(systems) != 1:
        raise ValueError("q214 dense parent lacks a unique 12x2 system")
    system = systems[0]
    if (
        system.get("quotientActionLabel") != f"12T{QUOTIENT_T}"
        or int(system.get("quotientActionOrder", -1)) != QUOTIENT_ORDER
        or int(system.get("blockKernelOrder", -1)) != BLOCK_KERNEL_ORDER
    ):
        raise ValueError("q214 dense parent quotient certificate changed")
    return {
        "algorithm": (
            "verifier-accepted dense parent, its unique degree-12 subfield, "
            "and its unique exact 12x2 block action"
        ),
        "blockKernelOrder": BLOCK_KERNEL_ORDER,
        "degree": 12,
        "order": QUOTIENT_ORDER,
        "parentLabel": SOURCE_LABEL,
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{QUOTIENT_T}",
        "transitiveNumber": QUOTIENT_T,
        "uniqueDegree12ParentSubfield": True,
        "uniqueParentBlockSystem": True,
    }


DRIVER.load_base = singleton_core_load_base
DRIVER.exact_galois_certificate = structural_certificate


if __name__ == "__main__":
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    raise SystemExit(DRIVER.main())

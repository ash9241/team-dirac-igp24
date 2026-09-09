#!/usr/bin/env sage -python
"""Run q109 exact lifts using an accepted parent's unique block quotient."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
BASE_PATH = ROOT / "broad_structural_character_gate_20260730.sage.py"
QUOTIENT_T = 109
QUOTIENT_ORDER = 192
SOURCE_LABEL = "24T13285"
BLOCK_KERNEL_ORDER = 128


def load_driver():
    spec = importlib.util.spec_from_file_location("q109_fast_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()


def structural_certificate(field_row: dict, quotient_t: int, _ring) -> dict:
    if int(quotient_t) != QUOTIENT_T:
        raise ValueError("q109 fast launcher received another quotient")
    source_rows = field_row.get("sourceRows", [])
    if not source_rows or {
        str(row.get("label")) for row in source_rows
    } != {SOURCE_LABEL}:
        raise ValueError("q109 source-label pin changed")
    if not all(row.get("scoreable") is True for row in source_rows):
        raise ValueError("q109 source row is not accepted/scoreable")

    parents = []
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("label") == SOURCE_LABEL:
                parents.append(row)
    if len(parents) != 1:
        raise ValueError("q109 structural parent is absent or duplicated")
    systems = [
        system
        for system in parents[0].get("blockSystems", [])
        if system.get("shape") == "12x2"
    ]
    if len(systems) != 1:
        raise ValueError("q109 parent no longer has a unique 12x2 system")
    system = systems[0]
    if (
        system.get("quotientActionLabel") != f"12T{QUOTIENT_T}"
        or int(system.get("quotientActionOrder", -1)) != QUOTIENT_ORDER
        or int(system.get("blockKernelOrder", -1)) != BLOCK_KERNEL_ORDER
    ):
        raise ValueError("q109 structural quotient certificate changed")
    return {
        "algorithm": (
            "verifier-accepted parent label plus unique exact 12x2 "
            "block action"
        ),
        "blockKernelOrder": BLOCK_KERNEL_ORDER,
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
    raise SystemExit(DRIVER.main())

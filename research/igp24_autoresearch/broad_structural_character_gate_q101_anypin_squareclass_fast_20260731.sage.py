#!/usr/bin/env sage -python
"""Run q101 lifts when at least one accepted source uniquely pins q101."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = (
    ROOT / "broad_structural_character_gate_q185_squareclass_fast_20260731.sage.py"
)
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
QUOTIENT_T = 101
QUOTIENT_ORDER = 192


def load_base():
    spec = importlib.util.spec_from_file_location("q101_anypin_broad", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.QUOTIENT_T = QUOTIENT_T
    module.QUOTIENT_ORDER = QUOTIENT_ORDER
    return module


def structural_certificate(field_row: dict, quotient_t: int, _ring) -> dict:
    if int(quotient_t) != QUOTIENT_T:
        raise ValueError("q101 any-pin launcher received another quotient")
    source_rows = field_row.get("sourceRows", [])
    if not source_rows:
        raise ValueError("q101 source rows are absent")

    source_labels = sorted(
        {
            str(row["label"])
            for row in source_rows
            if row.get("scoreable") is True
        }
    )
    registry = {}
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if str(row.get("label")) in source_labels:
                    registry[str(row["label"])] = row

    pinned = {}
    for label in source_labels:
        systems = [
            system
            for system in registry.get(label, {}).get("blockSystems", [])
            if system.get("shape") == "12x2"
        ]
        if (
            len(systems) == 1
            and systems[0].get("quotientActionLabel") == f"12T{QUOTIENT_T}"
            and int(systems[0].get("quotientActionOrder", -1))
            == QUOTIENT_ORDER
        ):
            pinned[label] = int(systems[0].get("blockKernelOrder", -1))
    if not pinned:
        raise ValueError("no accepted source parent uniquely pins q101")

    return {
        "algorithm": (
            "verifier-accepted source parent with a unique exact 12x2 "
            "block action"
        ),
        "degree": 12,
        "order": QUOTIENT_ORDER,
        "pinnedParentBlockKernelOrders": pinned,
        "pinnedParentLabels": sorted(pinned),
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{QUOTIENT_T}",
        "transitiveNumber": QUOTIENT_T,
        "uniquePinnedParentExists": True,
    }


if __name__ == "__main__":
    base = load_base()
    base.DRIVER.exact_galois_certificate = structural_certificate
    selmer_group._ideal_generator = base.squareclass_compact_ideal_generator
    raise SystemExit(base.DRIVER.main())

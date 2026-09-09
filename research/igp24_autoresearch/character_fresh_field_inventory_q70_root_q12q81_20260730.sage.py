#!/usr/bin/env sage -python
"""Configure the all-kernel direct quotient inventory for q70."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = (
    ROOT
    / "character_fresh_field_inventory_q138_root_q12q81_20260730.sage.py"
)


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "fresh_field_inventory_q70_root_q12q81", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
DRIVER.QUOTIENT_T = 70
DRIVER.EXPECTED_PRIOR_FIELDS = 1
DRIVER.PRIOR = (
    ROOT / "data" / "character_field_gate_q70_ambiguous_local_20260730.json"
)
DRIVER.OUTPUT = (
    ROOT
    / "data"
    / "character_fresh_field_inventory_q70_root_q12q81_direct_20260730.json"
)


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

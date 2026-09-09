#!/usr/bin/env sage -python
"""Exact character gate configuration for the q12T170 cluster."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_field_gate_q138_20260730.sage.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("character_gate_q170", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
DRIVER.DEFAULT_LOCAL_OUTPUT = (
    ROOT / "data" / "character_field_gate_q170_local_20260730.json"
)
DRIVER.QUOTIENT_T = 170
DRIVER.FAMILIES = {
    170: {
        "targetLabels": ["24T21581"],
    }
}
DRIVER.EXPECTED_LIVE = {
    "24T21581": [0, 4, 8, 12, 16, 20, 24],
}
DRIVER.EXPECTED_SOURCE_LABELS = {
    "24T21580",
    "24T21581",
    "24T21582",
}
DRIVER.EXPECTED_CANONICAL_FIELDS = 2
DRIVER.BASE.FAMILIES = DRIVER.FAMILIES


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

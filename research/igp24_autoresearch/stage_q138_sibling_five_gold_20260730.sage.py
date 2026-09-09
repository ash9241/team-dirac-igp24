#!/usr/bin/env sage -python
"""Seal the five certified 24T20768 sibling gold witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q138_sibling_live_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q138_ambiguous_sibling_exact_517a67f3_coset16_20260730.json":
        "b71db252d7e47912a308a0f33370e4969010eaf89a627e8dc1a9ecb51152cd68",
}
STAGER.EXPECTED_SELECTION = {
    ("24T20768", 8):
        "f10b4e660834593542b5b6f4812809cf0d6a705d141427ba4ccf328787e2125b",
    ("24T20768", 12):
        "e339520119713e7a4f9247bf8a6015e812020afb3d6dc6c3bcb538343de51063",
    ("24T20768", 16):
        "4a2bd1122a191fd3968e56143e11e924c77547cd4cb7d348a5bd68da00829e40",
    ("24T20768", 20):
        "707ab933644896496981aafe1b5e930c4ea3984fa6869fa0a2c50cb3025919dd",
    ("24T20768", 24):
        "112a315f7e238ee4cd71876ef2736e28cf7074db467a30ead811daf6b07373d6",
}
STAGER.MANIFEST = ROOT / "outbox" / "q138_sibling_five_gold_20260730.txt"
STAGER.CERTIFICATE = (
    ROOT / "data" / "q138_sibling_five_gold_stage_20260730.json"
)


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

#!/usr/bin/env sage -python
"""Seal one exact q28 tc0 structural-lift witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q28_one_gold_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT
    / "data"
    / "broad_structural_character_gate_q28_exact_862af371_squareclass_fast_20260731.json":
        "e8c4108843b8c77c7131cede4cd9f331a0fddb861588104548a53ea14673108e",
}
STAGER.EXPECTED_SELECTION = {
    ("24T16723", 20):
        "1bad655c79f5be88c7bfc8379184284b47da04ea1e897cc4fe22d8fc8705ecc7",
}
STAGER.MANIFEST = ROOT / "outbox" / "q28_one_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q28_one_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

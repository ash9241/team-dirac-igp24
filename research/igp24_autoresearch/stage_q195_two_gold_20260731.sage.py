#!/usr/bin/env sage -python
"""Seal two exact q195 tc0 broad structural-lift witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q195_two_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q195_exact_f33db95b_squareclass_fast_20260731.json":
        "417af5ee05dadb56a1cf79523f7ec11adf6409a53f10affba21f3366513d0fb0",
}
STAGER.EXPECTED_SELECTION = {
    ("24T22364", 12):
        "a4ef615eeebf6b5d44d5c44d01ff269f478907190a7dbfb1a8477475b2d4d108",
    ("24T22364", 20):
        "146b9a0699dd8b872d65be2cc9a1490a3e1457848fb5585442fc5b0aae68fd39",
}
STAGER.MANIFEST = ROOT / "outbox" / "q195_two_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q195_two_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

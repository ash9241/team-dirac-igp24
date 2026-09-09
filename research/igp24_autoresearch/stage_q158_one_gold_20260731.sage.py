#!/usr/bin/env sage -python
"""Seal the lower-discriminant exact q158 tc0 character-lift witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q158_one_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q158_exact_6a95d10d_squareclass16_20260731.json":
        "e46cb76b8b5bd0e316c95d4a719b2c19bae1339302377b14c5e899fa5914b19d",
}
STAGER.EXPECTED_SELECTION = {
    ("24T21456", 24):
        "68e958f343ae780f7f925472592f3272f3e91db5cdd3bf6b13a62c724523bbcb",
}
STAGER.MANIFEST = ROOT / "outbox" / "q158_one_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q158_one_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

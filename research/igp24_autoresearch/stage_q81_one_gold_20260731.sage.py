#!/usr/bin/env sage -python
"""Seal one exact q81 tc0 structural-lift witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q81_one_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q81_exact_703ed6d7_squareclass_fast_20260731.json":
        "f957d5473e8fb3d066eeb3a8c43a11427dad4f8ef16a84ca5ab45db836983dfd",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19341", 24):
        "2678b50fba2b8f95e4f2b25ba43ff288c3022aa69678222be9aaaf6d02e6f196",
}
STAGER.MANIFEST = ROOT / "outbox" / "q81_one_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q81_one_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

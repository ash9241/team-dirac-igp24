#!/usr/bin/env sage -python
"""Seal the exact q28 r24 tc0 structural-lift witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q28_r24_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q28_exact_788a6360_squareclass_20260731.json":
        "43e5f4eaeb07a70609cb0e9fcc9a788dbff9d409d250614706aec107302043d8",
}
STAGER.EXPECTED_SELECTION = {
    ("24T16723", 24):
        "ae73a258f52280f87fdb18843c28500e4a212098b31ace8a239f4ba680a13f4c",
}
STAGER.MANIFEST = ROOT / "outbox" / "q28_r24_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q28_r24_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

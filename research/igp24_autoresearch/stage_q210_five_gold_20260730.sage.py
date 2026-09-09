#!/usr/bin/env sage -python
"""Seal the five certified 24T22544 shared-character gold witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q210_shared_character_live_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q210_ambiguous_exact_d2eef78b_coset16_20260730.json":
        "64f9c65916cbafdbd38a07372022f510bdc4c15c72aeb4aa579e18dfd9c31884",
}
STAGER.EXPECTED_SELECTION = {
    ("24T22544", 8):
        "5b3de7573c981ce02025a435979486d0a3d178ed6a83280ee79cd2d00bd04b72",
    ("24T22544", 12):
        "1d4a8b9d885801ac963e4af6fb310918a6b8640ffdb021c3ace61070e1719921",
    ("24T22544", 16):
        "eba6a356a915dea9e9586bfad982bb79f566394f7265e25518f9c0dccadc5aca",
    ("24T22544", 20):
        "32f04439fa0b3d21b8be1e23905675c644c20b1d29200b7554163eec5f0ac259",
    ("24T22544", 24):
        "5ab37f13e37529e2c2a5c6359e419f829bc64992124c4978cb68f7f4d27971b0",
}
STAGER.MANIFEST = ROOT / "outbox" / "q210_five_gold_20260730.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q210_five_gold_stage_20260730.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

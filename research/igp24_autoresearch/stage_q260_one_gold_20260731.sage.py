#!/usr/bin/env sage -python
"""Seal one exact q260 tc0 structural-lift witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q260_one_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q260_23756r18_exact_df4f8ac7_signed_squareclass_fixedsig_20260731.json":
        "ab9824c76c7578064935f60786ede14be22bbddce34decc5e7d9000e1907db49",
}
STAGER.EXPECTED_SELECTION = {
    ("24T23756", 18):
        "e9f455af8a3aea04e0c093c5f0b43a5416143a02da5d38dc5b7119bea82c36a2",
}
STAGER.MANIFEST = ROOT / "outbox" / "q260_one_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q260_one_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

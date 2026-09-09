#!/usr/bin/env sage -python
"""Seal two exact q80 tc0 structural-lift witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q80_two_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q80_exact_c83ac320_squareclass_fast_20260731.json":
        "73adff60abbbeea7ec74516b129d5e181e54cdbe5fc90d6c7cfce0a7564d0dd8",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19334", 20):
        "7a12a727c21efb847b1e6efa62ca6b4a3e8707da04b8b15643563e73767cc671",
    ("24T19336", 24):
        "3accee7dde9a9dd85e2a1e3b07e3689925b3aa6d6da2fa50941e49076039a2ae",
}
STAGER.MANIFEST = ROOT / "outbox" / "q80_two_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q80_two_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

#!/usr/bin/env sage -python
"""Seal the certified 24T19727/r20 shared-character gold witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q109_shared_character_live_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q109_ambiguous_exact_4d9c0f5d_coset16_20260730.json":
        "bb9895b5069132aed76d8280ed1c28665ed0a4cc0aa2587bd5a9e84cf509a353",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19727", 20):
        "e6e834c5eb73c7b33bea9da3e15f93b58d2199869f557094e9a6e7c4f7f69e70",
}
STAGER.MANIFEST = ROOT / "outbox" / "q109_one_gold_20260730.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q109_one_gold_stage_20260730.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

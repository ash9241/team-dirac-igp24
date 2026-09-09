#!/usr/bin/env sage -python
"""Seal the two exact q185 tc0 broad structural-lift witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q185_two_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q185_exact_dd356a26_squareclass_fast_20260731.json":
        "8249fd9fc29bb81d71b8f3db129d1b17fbc861c544a2fc169f1580515b8cc6c7",
}
STAGER.EXPECTED_SELECTION = {
    ("24T21879", 24):
        "95d87faecb6655d7a30a391a2005d334980b30aff690d73a0a0633dfa582c7c9",
    ("24T21880", 24):
        "615a84b1658c70be8a60e9c19ef48d3a4f74d49e1e410c4db97b29ae2ed8ad07",
}
STAGER.MANIFEST = ROOT / "outbox" / "q185_two_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q185_two_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

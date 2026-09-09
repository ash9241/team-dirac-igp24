#!/usr/bin/env sage -python
"""Seal the two exact q155 tc0 character-lift witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q155_two_gold_stager", BASE_PATH
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
    / "character_field_gate_q155_ambiguous_exact_9ac2b875_squareclass_20260731.json":
        "0148c806ee3b1e97653ce5590b6133c098c0f5b93eacecaa93a6114ca08594e7",
}
STAGER.EXPECTED_SELECTION = {
    ("24T20834", 20):
        "be851c1c7691271672b44bf6c5e81927c494f939a1dcf0acee3577c6920d87d4",
    ("24T20834", 24):
        "103a9a0d07a67441aa136494a4934eb9eb41e1922d6187a622655c419812a22f",
}
STAGER.MANIFEST = ROOT / "outbox" / "q155_two_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q155_two_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

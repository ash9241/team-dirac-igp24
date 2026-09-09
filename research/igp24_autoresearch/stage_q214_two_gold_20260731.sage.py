#!/usr/bin/env sage -python
"""Seal the two exact q214 tc0 structural-lift witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q214_two_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q214_exact_a186512c_fast_20260731.json":
        "de003d6afe87d48aeba75f2565c50277f2da44b15490aa9eb62ea631356212fe",
}
STAGER.EXPECTED_SELECTION = {
    ("24T22560", 20):
        "68b62e8d5830bba7334eb86128e813661f711a43e70dfd5445b556622d77c324",
    ("24T22560", 24):
        "6cbc288d36cfb851f430e0c65154d71a25885eea10c1ec8f0a9e055fe8175b61",
}
STAGER.MANIFEST = ROOT / "outbox" / "q214_two_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q214_two_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

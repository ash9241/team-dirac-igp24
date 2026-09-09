#!/usr/bin/env sage -python
"""Seal two certified tc0 character routes omitted from earlier packets."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "resume_q136_q138_two_gold_stager", BASE_PATH
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
    / "character_field_gate_q136_ambiguous_20757_exact_30f64cb4_coset16_20260730.json":
        "4b621fe9f8e9185090caef582de7d17b01ae18c68166af71bc7b5df7b2254bd1",
    ROOT
    / "data"
    / "character_field_gate_q138_ambiguous_sibling_exact_517a67f3_coset16_20260730.json":
        "b71db252d7e47912a308a0f33370e4969010eaf89a627e8dc1a9ecb51152cd68",
}
STAGER.EXPECTED_SELECTION = {
    ("24T20757", 20):
        "0da8e474388a1635794f4585aa368d451be31707d74bcfd05f0008ebea5603f0",
    ("24T20768", 20):
        "c3ed0c4b9bb9b99d3ee80aa0871315aebdc63b67e66c2404b9fddabf46b78746",
}
STAGER.MANIFEST = (
    ROOT / "outbox" / "resume_q136_q138_two_gold_20260730.txt"
)
STAGER.CERTIFICATE = (
    ROOT / "data" / "resume_q136_q138_two_gold_stage_20260730.json"
)


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

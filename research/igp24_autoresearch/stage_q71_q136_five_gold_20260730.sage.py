#!/usr/bin/env sage -python
"""Seal five certified q71/q136 shared-character gold witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q71_q136_shared_character_live_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q71_ambiguous_exact_690f5c2a_coset16_20260730.json":
        "011affccd01b5cff4a9b4981d16b631608545532931123b8d5e5a285058281c8",
    ROOT / "data" / "character_field_gate_q136_ambiguous_20757_exact_78319052_coset16_20260730.json":
        "c779da922f13f4a88b996f32509ebb3085ec775364e1450d9f5cdc51f94e54d1",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19174", 4):
        "a8ebdadc3993e6e565a8e95d40ee95a00833f398a12e54fabf029619015cda66",
    ("24T19174", 12):
        "691edcd89a65076dd7fdd0d2c3616032de70049b125ae1ece048e0c1daa6a062",
    ("24T19174", 20):
        "153da339278164707cfe0e5fa657c9b517916d745742e93fdea170ae0d1249c0",
    ("24T20757", 20):
        "49e9e3f7cf53708b0ca6c708964a13ae3fc7ff237f4db8be4d723078b60d211e",
    ("24T20757", 24):
        "45aa294f80d929fb6fe78f687413605d3dde5030f7981ff8a96ed9633a3d326e",
}
STAGER.MANIFEST = ROOT / "outbox" / "q71_q136_five_gold_20260730.txt"
STAGER.CERTIFICATE = (
    ROOT / "data" / "q71_q136_five_gold_stage_20260730.json"
)


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

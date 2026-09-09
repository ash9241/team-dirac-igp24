#!/usr/bin/env sage -python
"""Seal the six certified 24T19321 shared-character gold witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q77_shared_character_live_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q77_ambiguous_exact_4eb67f18_coset16_20260730.json":
        "2ceae102b970e6d0f4ea6846ee5760c78c050000d40b21856863727550b4df96",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19321", 4):
        "751a0ebfcb54d9b570b66a79e7cb2f10b2692355356cb476dc228ea7ec39eaf7",
    ("24T19321", 8):
        "1294282b07acf04a1e0f87644816cfb58efeeb2c31d6842a3364b1a27926175b",
    ("24T19321", 12):
        "d7e4b173a5b9cdbf085e4078a30531d7c2e587faddb52218455a431283d7ce24",
    ("24T19321", 16):
        "5c65d104565cafaa7c773bb1064cb2f09f482ebe57005f9628fe8d8653e03760",
    ("24T19321", 20):
        "11414922d92f409110a2d950ce0e03cda435450226c52e7bedde7aa951a7adba",
    ("24T19321", 24):
        "4c26f14ff6a40e064bee19aa88a453320134ca725a54adb1e0e93d0ece741166",
}
STAGER.MANIFEST = ROOT / "outbox" / "q77_six_gold_20260730.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q77_six_gold_stage_20260730.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

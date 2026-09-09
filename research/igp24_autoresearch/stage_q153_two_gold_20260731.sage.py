#!/usr/bin/env sage -python
"""Seal the two exact q153 tc0 character-lift witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q153_two_gold_stager", BASE_PATH
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
    / "broad_structural_character_gate_q153_exact_765e57d0_fast2_20260731.json":
        "6a63f973f5950cc5da058315080585752512765b4e97e6e80f0b413f0df55238",
}
STAGER.EXPECTED_SELECTION = {
    ("24T20825", 24):
        "e2615c93603216404733207baea66e17299663560f6fc0682b511323561e75c6",
    ("24T20827", 24):
        "d8afc4129e2400433710311bdf264c430a0cf9348e148f60ccc881617c2e96a2",
}
STAGER.MANIFEST = ROOT / "outbox" / "q153_two_gold_20260731.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q153_two_gold_stage_20260731.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

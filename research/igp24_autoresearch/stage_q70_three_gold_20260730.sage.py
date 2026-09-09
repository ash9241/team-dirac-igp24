#!/usr/bin/env sage -python
"""Seal the three certified 24T19171 shared-character gold witnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_character_live_gold_13_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q70_shared_character_live_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STAGER


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q70_ambiguous_exact_6d0c0ed8_coset16_20260730.json":
        "f3e410dbaf6e23c4ba1219462f6ce857c2e9645dc4d9cc42159cc232b56f2dc5",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19171", 4):
        "1999001be993b27a8e784897cd34da54ce320e429621df770c21ff29ece854f0",
    ("24T19171", 20):
        "a1d6b8006537007c2ac81ef85ff6ae6638569690f281867b8984b5dc97313fab",
    ("24T19171", 24):
        "971520e44d5f34242680b1e2fc8a039f44fdaad0babf160e633dc20a7b87d8ff",
}
STAGER.MANIFEST = ROOT / "outbox" / "q70_three_gold_20260730.txt"
STAGER.CERTIFICATE = ROOT / "data" / "q70_three_gold_stage_20260730.json"


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

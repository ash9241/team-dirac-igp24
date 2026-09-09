#!/usr/bin/env sage -python
"""Configure the generic character stager for the certified q125 gold pair."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_q136_q193_character_packet_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "character_q125_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q125_exact_3a92b9fc_coset16_20260730.json":
        "70df3542d84e44a41916bd7f83186fb4efa4a4846961e52e34875f12f380723a",
}
STAGER.EXPECTED_SELECTION = {
    ("24T20436", 24):
        "e2d0e017a5fa20d2f3f0c73d8c7561e589ad087bdf17591785396b58d9cfc6dd",
}
STAGER.MANIFEST = ROOT / "outbox" / "q125_character_gold_20260730.txt"
STAGER.CERTIFICATE = (
    ROOT / "data" / "q125_character_gold_stage_20260730.json"
)


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

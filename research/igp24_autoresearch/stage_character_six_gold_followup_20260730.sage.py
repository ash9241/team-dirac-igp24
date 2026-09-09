#!/usr/bin/env sage -python
"""Configure the generic character stager for six follow-up gold pairs."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_q136_q193_character_packet_20260730.sage.py"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "character_followup_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q193_exact_57768ed9_coset16_20260730.json":
        "dd12919ec0e8a560431ee333f3ea38e3b1370bb0d08d7e0fe1be38d836e435b5",
    ROOT / "data" / "character_field_gate_q260_exact_c1a54002_coset16_20260730.json":
        "3484ff4bea6c10951cabc566d704599f4568ff78c7d51165e213d9519f50088f",
    ROOT / "data" / "character_field_gate_q88_exact_feb5a65c_coset16_20260730.json":
        "cf8f53d4e398ae4a636e659bbfcfe5960644ac036bc48b18abc63bf15f93d1cc",
    ROOT / "data" / "character_field_gate_q197_exact_a7d17541_coset16_20260730.json":
        "bcd2d173168fa2e9ad0793608161808bd3be9b6294dd9e8b74414df40b291803",
    ROOT / "data" / "character_field_gate_q221_exact_b89fc4e2_coset16_20260730.json":
        "d49d3646668c97fa29be8e916f10a26954edf71847106618c1830c2dfac6cf36",
    ROOT / "data" / "character_field_gate_q199_exact_30ad7f92_coset16_20260730.json":
        "527305f18da884d34a88c36b97b8ecabafc9f254e68eb7c79f20dd89638e19d0",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19669", 24):
        "5c8333f2362901e942043ddc2521a0bdd58b43bb4f44217a76e9e8322d27e6c7",
    ("24T21913", 24):
        "bcf8f90797d4c23ae8e5c01dab34afcae96cddaaa73312e5d303fcb3b5f130d5",
    ("24T22371", 20):
        "867104424931b6c06af69eeee56bdd9d31dc3febbd89773f355e50616e04a0f7",
    ("24T22377", 0):
        "cd7fafd527bce6f6b088b4475f20965bfd737d36c3c9a4296fe4b3d5f1a31bc0",
    ("24T22796", 20):
        "4faa5cd5d6c7da56ed5e8a5420f5eadba9464c43f330b079df4d56cfac15b660",
    ("24T23760", 24):
        "0f6630c0a8875bc66d6b227976d401293446b9d1bdc4c916f6dc202985e668c2",
}
STAGER.MANIFEST = (
    ROOT / "outbox" / "character_six_gold_followup_20260730.txt"
)
STAGER.CERTIFICATE = (
    ROOT / "data" / "character_six_gold_followup_stage_20260730.json"
)


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

#!/usr/bin/env sage -python
"""Enumerate ambiguous core-1 lifts on the fresh dense q214 field."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = (
    ROOT / "broad_structural_character_gate_q214_dense_squareclass_20260731.sage.py"
)


def load_launcher():
    spec = importlib.util.spec_from_file_location("q214_dense_launcher", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAUNCHER = load_launcher()
DRIVER = LAUNCHER.DRIVER
UNRESTRICTED_LOAD_BASE = LAUNCHER.ORIGINAL_LOAD_BASE


def enumeration_load_base(quotient_t: int, target_labels: list[str]):
    base = UNRESTRICTED_LOAD_BASE(quotient_t, target_labels)

    def retain_without_containment_claim(
        _candidate, _quotient, profiles, _identities, _witness_primes
    ):
        return {
            "checkedSquarefreePrimes": 0,
            "complete": False,
            "properTransitiveMaximals": [],
            "reason": (
                "core-1 containment is ambiguous; candidate retained for "
                "independent full-group identification"
            ),
            "requestedProfileCount": len(profiles),
        }

    base.HELPER.SHARED.frobenius_maximal_certificate = (
        retain_without_containment_claim
    )
    return base


DRIVER.load_base = enumeration_load_base


if __name__ == "__main__":
    selmer_group._ideal_generator = LAUNCHER.squareclass_compact_ideal_generator
    raise SystemExit(DRIVER.main())

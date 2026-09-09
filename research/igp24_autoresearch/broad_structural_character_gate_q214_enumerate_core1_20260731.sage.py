#!/usr/bin/env sage -python
"""Enumerate every core-1 q214 Selmer reconstruction without mislabeling it.

The ordinary target-maximal certificate assumes containment in its requested
target.  Core 1 is shared by 24T22560 and 24T22565, so that assumption is not
available.  This launcher deliberately makes every target certificate
incomplete, retaining all exact reconstructions for a separate exhaustive
classification against every degree-24 group with the exact 12T214 quotient.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "broad_structural_character_gate_q214_fast_20260731.sage.py"


def squareclass_compact_ideal_generator(ideal):
    try:
        field = ideal.number_field()
    except AttributeError:
        return ideal.abs()
    bnf = field.pari_bnf(False)
    principal_data = bnf.bnfisprincipal(ideal.pari_hnf(), 5)
    if any(int(value) for value in principal_data[0]):
        raise ValueError("2-Selmer requested a generator of a nonprincipal ideal")
    compact = principal_data[1]
    rows, columns = (int(value) for value in compact.matsize())
    if columns != 2:
        raise ValueError(
            f"unexpected compact factor matrix shape {(rows, columns)}"
        )
    representative = field.one()
    for row in range(rows):
        if int(compact[row, 1]) % 2:
            representative *= field(bnf.nfbasistoalg(compact[row, 0]))
    return representative


def load_launcher():
    spec = importlib.util.spec_from_file_location("q214_original_launcher", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAUNCHER = load_launcher()
DRIVER = LAUNCHER.DRIVER
ORIGINAL_LOAD_BASE = DRIVER.load_base


def enumeration_load_base(quotient_t: int, target_labels: list[str]):
    base = ORIGINAL_LOAD_BASE(quotient_t, target_labels)

    def retain_without_containment_claim(
        _candidate, _quotient, profiles, _identities, _witness_primes
    ):
        return {
            "checkedSquarefreePrimes": 0,
            "complete": False,
            "properTransitiveMaximals": [],
            "reason": (
                "core-1 containment is ambiguous; candidate retained for "
                "exhaustive exact-quotient catalog classification"
            ),
            "requestedProfileCount": len(profiles),
        }

    base.HELPER.SHARED.frobenius_maximal_certificate = (
        retain_without_containment_claim
    )
    return base


DRIVER.load_base = enumeration_load_base


if __name__ == "__main__":
    selmer_group._ideal_generator = squareclass_compact_ideal_generator
    raise SystemExit(DRIVER.main())

#!/usr/bin/env sage -python
"""Run the preserved q138 gate on fresh fields for only 24T20768/r24."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_field_gate_q138_fresh_root_q12q81_20260730.sage.py"


def load_fresh_driver():
    spec = importlib.util.spec_from_file_location(
        "q138_fresh_r24_portfolio_driver", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DRIVER


DRIVER = load_fresh_driver()
DRIVER.DEFAULT_LOCAL_OUTPUT = (
    ROOT / "data/character_field_gate_q138_fresh_r24_local_20260812.json"
)
DRIVER.EXPECTED_LIVE = {"24T20768": [24]}
DRIVER.FAMILIES = {
    138: {
        "ambiguousTargetLabels": ["24T20768"],
        "targetLabels": ["24T20768"],
    }
}
DRIVER.BASE.FAMILIES = DRIVER.FAMILIES


def remaining_r24_only(_db: Path) -> dict[str, list[dict]]:
    """Pin the sole remaining q138 target without copying the 2 GB ledger."""
    return {
        "24T20768": [
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": "2026-08-12T22:29:00Z",
                "locallyOwned": False,
                "r": 24,
                "teamCount": 0,
            }
        ]
    }


DRIVER.strict_live_pairs = remaining_r24_only


_AUDIT_FIELD = DRIVER.BASE.audit_field


def audit_field_with_alignment_obstruction(*args, **kwargs):
    field_row = args[0]
    try:
        return _AUDIT_FIELD(*args, **kwargs)
    except ValueError as exc:
        if "no character squareclass satisfies Frobenius data" not in str(exc):
            raise
        return {
            **field_row,
            "alignmentObstruction": str(exc),
            "exactSelmerSignGate": {
                "pairs": [],
                "status": "skipped_character_alignment_obstruction",
            },
            "localPairGates": [],
            "status": "character_alignment_obstruction",
        }


DRIVER.BASE.audit_field = audit_field_with_alignment_obstruction


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

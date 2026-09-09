#!/usr/bin/env sage -python
"""Run the preserved q138 character gate on newly sourced quotient fields."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_field_gate_q138_20260730.sage.py"
INVENTORY = (
    ROOT
    / "data"
    / "character_fresh_field_inventory_q138_root_q12q81_direct_20260730.json"
)


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "character_gate_q138_fresh_root_q12q81", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
DRIVER.DEFAULT_LOCAL_OUTPUT = (
    ROOT
    / "data"
    / "character_field_gate_q138_fresh_root_q12q81_local_20260730.json"
)
DRIVER.FAMILIES = {
    138: {
        "ambiguousTargetLabels": ["24T20768"],
        "targetLabels": ["24T20767", "24T20768"],
    }
}
DRIVER.EXPECTED_LIVE = {
    "24T20767": [4, 8, 12, 16, 20, 24],
    "24T20768": [8, 12, 16, 20, 24],
}
DRIVER.EXPECTED_CANONICAL_FIELDS = 24
DRIVER.BASE.FAMILIES = DRIVER.FAMILIES


def fresh_fields(_db: Path, _structures: Path, _ring) -> list[dict]:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    rows = payload["freshCanonicalTotallyRealFields"]
    if len(rows) != DRIVER.EXPECTED_CANONICAL_FIELDS:
        raise ValueError(
            f"expected {DRIVER.EXPECTED_CANONICAL_FIELDS} fresh q138 fields, "
            f"found {len(rows)}"
        )
    output = []
    for row in rows:
        if row.get("alreadyAudited"):
            raise ValueError("fresh inventory contains an already-audited field")
        output.append(
            {
                "canonicalPolynomial": str(row["canonicalPolynomial"]),
                "fieldCanonicalSha256": str(row["fieldCanonicalSha256"]),
                "family": "12T138",
                "provenance": str(row["provenance"]),
                "quotientT12": 138,
                "sourceRows": list(row["sourceRows"]),
            }
        )
    return sorted(output, key=lambda row: row["fieldCanonicalSha256"])


DRIVER.canonical_fields = fresh_fields
DRIVER.EXPECTED_SOURCE_LABELS = {
    str(row["label"])
    for field in fresh_fields(None, None, None)
    for row in field["sourceRows"]
}


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

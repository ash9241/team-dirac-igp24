#!/usr/bin/env sage -python
"""Extract every exact degree-12 q214 fixed field from accepted parents.

This is the q214 specialization of the fail-closed q210 source census.  It
includes dense degree-24 presentations via their unique degree-12 subfield,
which the earlier even-polynomial-only q214 census omitted.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_q210_freshfields_agent_source_census_20260730.sage.py"
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
QUOTIENT_T = 214
EXPECTED_SYSTEM_SHA256 = (
    "c955155f4d7e005577da7ccafedbfc62bd697d3b7ea3bf598e1542babe7f9bac"
)
PRIOR_AUDITED_FIELD_HASHES = {
    "a186512c3169fd016050fc4e70db4755548bd730cece976f46b1ff6cc5239399",
}


def load_base():
    spec = importlib.util.spec_from_file_location("q214_dense_census_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def exact_source_labels() -> set[str]:
    labels = set()
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            systems = [
                system
                for system in row.get("blockSystems", [])
                if (
                    system.get("shape") == "12x2"
                    and system.get("quotientActionLabel") == f"12T{QUOTIENT_T}"
                )
            ]
            if systems:
                if len(systems) != 1:
                    raise ValueError(
                        f"{row['label']} has {len(systems)} q214 two-block systems"
                    )
                labels.add(str(row["label"]))
    if not labels:
        raise ValueError("q214 source-label census is empty")
    return labels


def main() -> int:
    base = load_base()
    base.QUOTIENT_T = QUOTIENT_T
    base.EXPECTED_SYSTEM_SHA256 = EXPECTED_SYSTEM_SHA256
    base.EXPECTED_SOURCE_LABELS = exact_source_labels()
    base.PRIOR_AUDITED_FIELD_HASHES = PRIOR_AUDITED_FIELD_HASHES
    base.DEFAULT_OUTPUT = (
        ROOT / "data" / "character_q214_freshfields_dense_census_20260731.json"
    )
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())

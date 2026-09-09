#!/usr/bin/env sage -python
"""Refresh the shifted full-Kummer tc0 census without overwriting history."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "shifted_kummer_gold_census.sage.py"


def load_worker():
    spec = importlib.util.spec_from_file_location(
        "shifted_kummer_gold_census_refresh", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORKER = load_worker()
WORKER.OUTPUT = (
    ROOT / "data" / "shifted_kummer_gold_census_20260731.json"
)


if __name__ == "__main__":
    raise SystemExit(WORKER.main())

#!/usr/bin/env sage -python
"""Independent artifact rerun of the all-character derivative screen."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "gold_f5_f6_20260728_all_character_derivative_screen.sage.py"
SPEC = importlib.util.spec_from_file_location(
    "gold_f5_f6_all_character_agent_screen_impl", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"cannot load {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MODULE.RESULTS = (
    ROOT / "data" / "gold_f5_f6_20260728_all_character_agent_screen.jsonl"
)
MODULE.SUMMARY = (
    ROOT
    / "data"
    / "gold_f5_f6_20260728_all_character_agent_screen_summary.json"
)
MODULE.MANIFEST = (
    ROOT / "outbox" / "gold_f5_f6_20260728_all_character_agent_screen.txt"
)


if __name__ == "__main__":
    raise SystemExit(MODULE.main())

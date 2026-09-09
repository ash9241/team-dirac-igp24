#!/usr/bin/env sage -python
"""Run the standard exact pair worker with the agent-local extended GAP map."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "agent_gold_b_pair_sum_shared", ROOT / "pair_sum_one.sage.py"
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot import pair_sum_one.sage.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
shared.ORBIT_MAP_PATH = ROOT / "data" / "agent_gold_b_combined_pair_orbit_map.jsonl"


if __name__ == "__main__":
    raise SystemExit(shared.main())

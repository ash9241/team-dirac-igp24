#!/usr/bin/env python3
"""Use the locked batch runner with the agent-local extended pair map."""

from __future__ import annotations

from pathlib import Path

import agent_gold_a_run_pair_sibling_batch as shared


shared.WORKER = Path(__file__).resolve().parent / "agent_gold_b_pair_sum_one.sage.py"


if __name__ == "__main__":
    raise SystemExit(shared.main())

#!/usr/bin/env sage -python
"""Recover the exact character-to-norm-core map for one owned even base.

This is a small process-isolated worker for the durable character task bank.
It performs no search, network call, submission, or staging action.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_kernel_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    helper = load_helper()
    source = helper.load_source(args.db, args.submission_id, args.polynomial_index)
    structure = helper.target_structure(helper.parse_label(source["label"]))
    alignment = helper.character_alignment(source["quotient"], structure["quotientT"])
    source_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(
        source["label"], []
    )
    if source["squarefreeNormCore"] not in source_cores:
        raise ValueError("alignment does not recover the verified source core")

    result = {
        "alignment": alignment,
        "networkCalls": 0,
        "source": {key: value for key, value in source.items() if key != "quotient"},
        "submissionCalls": 0,
        "targetStructure": structure,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.resolve().write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "quotientT": structure["quotientT"],
                    "sourceLabel": source["label"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

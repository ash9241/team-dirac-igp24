#!/usr/bin/env sage -python
"""Build an isolated exact generic-twist action map for pinned source labels.

This is a non-overwriting adapter around ``build_even_twist_action_map``.  It
exists so a later heavy lease can identify only the requested owned-even
labels without changing the historical full action map.  It has no network,
ledger-write, or submission calls.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
BASE_PATH = ROOT / "build_even_twist_action_map.sage.py"


def load_base():
    specification = importlib.util.spec_from_file_location("even_twist_action_base", BASE_PATH)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import action builder: {BASE_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


BASE = load_base()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-label", action="append", required=True)
    args = parser.parse_args()

    requested = set(args.source_label)
    if len(requested) != len(args.source_label):
        raise ValueError("duplicate --source-label selector")
    output = args.output.resolve()
    temporary = output.with_suffix(output.suffix + ".tmp")
    if output.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite isolated action output: {output}")
    if not output.parent.is_dir():
        raise FileNotFoundError(f"action output parent does not exist: {output.parent}")

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        available = dict(BASE.owned_even_labels(connection))
    finally:
        connection.close()
    missing = sorted(requested - set(available))
    if missing:
        raise ValueError(
            "requested label is absent from the owned even census: " + ", ".join(missing)
        )

    rows = [BASE.action_row(label, int(available[label])) for label in sorted(requested, key=lambda value: int(value[3:]))]
    if {str(row["sourceLabel"]) for row in rows} != requested:
        raise ArithmeticError("isolated action builder did not cover every requested label")
    BASE.write_atomic(output, rows)
    print(json.dumps({
        "mappedLabels": len(rows),
        "networkCalls": 0,
        "sourceLabels": sorted(requested, key=lambda value: int(value[3:])),
        "submissionCalls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

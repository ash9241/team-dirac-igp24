#!/usr/bin/env python3
"""Launch local squareclass gates for selected wave-3 quotient lanes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("q", type=int, nargs="+")
    args = parser.parse_args()
    source = json.loads((DATA / "current_squareclass_sources_wave3_20260813.json").read_text())
    lanes = {int(row["quotientT12"]): row for row in source["lanes"]}
    launched = []
    for q in args.q:
        labels = sorted({str(row["label"]) for row in lanes[q]["currentGoldTargets"]})
        for shard in range(8):
            census = DATA / f"current_squareclass_q{q}_census_w3_shard{shard:02d}_of08.json"
            payload = json.loads(census.read_text())
            if not payload.get("fields"):
                continue
            output = DATA / f"q{q}_w3_livegold_local_shard{shard:02d}_20260813.json"
            log = DATA / f"q{q}_w3_livegold_local_shard{shard:02d}_20260813.log"
            command = [
                "/usr/bin/sage", "-python",
                "broad_structural_character_gate_squareclass_20260731.sage.py",
                "--quotient-t", str(q),
            ]
            for label in labels:
                command += ["--target-label", label]
            command += [
                "--census", str(census.relative_to(ROOT)),
                "--db", f"data/current_squareclass_q{q}_gate_ledger_w3_20260813.sqlite3",
                "--phase", "local", "--output", str(output.relative_to(ROOT)),
            ]
            handle = log.open("w")
            subprocess.Popen(
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "SAGE_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            )
            launched.append({"q": q, "shard": shard, "fields": len(payload["fields"])})
    print(json.dumps({"launched": launched, "workers": len(launched)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

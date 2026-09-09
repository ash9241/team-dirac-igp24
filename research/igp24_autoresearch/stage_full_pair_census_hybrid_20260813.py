#!/usr/bin/env python3
"""Stage every fresh certified factor from the completed pair census."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-glob", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite pair-census hybrid wave")
    paths = sorted({Path(p) for pattern in args.input_glob for p in glob.glob(pattern)})
    connection = sqlite3.connect(ROOT / "data" / "ledger.sqlite3")
    candidates = {}
    provenance = {}
    for path in paths:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            factors = [row] if row.get("status") == "certified" else row.get("candidates", [])
            for factor in factors:
                line = str(factor["coefficientLine"])
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest != str(factor["coefficientSha256"]):
                    raise ValueError(f"candidate hash mismatch in {path}")
                if connection.execute(
                    "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
                ).fetchone():
                    continue
                candidates.setdefault(digest, line)
                provenance.setdefault(digest, []).append(
                    {
                        "file": path.name,
                        "sourceLabel": row["sourceLabel"],
                        "targetLabels": sorted(
                            {target["targetLabel"] for target in row["orbitTargets"]}
                        ),
                        "targetR": int(factor["targetR"]),
                    }
                )
    connection.close()
    ordered = sorted(candidates, key=lambda value: (len(candidates[value]), value))
    manifest = "".join(candidates[digest] + "\n" for digest in ordered)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "inputFiles": len(paths),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "rows": len(ordered),
        "ambiguous2030R0Candidates": [
            digest
            for digest in ordered
            if any(
                item["targetR"] == 0 and "24T2030" in item["targetLabels"]
                for item in provenance[digest]
            )
        ],
        "provenance": {digest: provenance[digest] for digest in ordered},
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "provenance"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

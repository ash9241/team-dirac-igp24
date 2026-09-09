#!/usr/bin/env python3
"""Split a squareclass census into deterministic field shards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=8)
    args = parser.parse_args()
    if args.shards <= 0:
        parser.error("--shards must be positive")

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    fields = list(payload.get("fields", []))
    if not fields:
        raise ValueError("census contains no fields")
    outputs = []
    for index in range(args.shards):
        output = Path(f"{args.output_prefix}{index:02d}_of{args.shards:02d}.json")
        if output.exists():
            raise FileExistsError(output)
        selected = fields[index::args.shards]
        shard = dict(payload)
        shard["fields"] = selected
        shard["freshFields"] = [
            row for row in selected if not bool(row.get("previouslyAudited"))
        ]
        summary = dict(payload.get("summary", {}))
        summary["canonicalFields"] = len(selected)
        summary["freshCanonicalTotallyRealFields"] = len(shard["freshFields"])
        summary["sourcePresentations"] = sum(
            len(row.get("sourceRows", [])) for row in selected
        )
        shard["summary"] = summary
        output.write_text(json.dumps(shard, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        outputs.append({"path": str(output), "fields": len(selected)})
    print(json.dumps({"inputFields": len(fields), "outputs": outputs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

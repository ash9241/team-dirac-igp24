#!/usr/bin/env python3
"""Split a squareclass census into deterministic independent field shards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.shards <= 0:
        raise ValueError("--shards must be positive")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    fields = payload.get("freshFields", [])
    outputs = []
    for shard in range(args.shards):
        selected = fields[shard::args.shards]
        result = dict(payload)
        result["fields"] = selected
        result["freshFields"] = selected
        result["summary"] = {
            **payload.get("summary", {}),
            "shard": shard,
            "shardCount": args.shards,
            "shardFields": len(selected),
        }
        output = args.input.parent / f"{args.tag}_shard{shard:02d}_of{args.shards:02d}_20260812.json"
        if output.exists():
            raise FileExistsError(output)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        outputs.append(str(output))
    print(json.dumps({"fields": len(fields), "outputs": outputs, "shards": args.shards}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

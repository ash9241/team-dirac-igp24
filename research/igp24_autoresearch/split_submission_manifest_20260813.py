#!/usr/bin/env python3
"""Split a pre-ranked manifest into immutable SAIR-sized chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        required=True,
        help="ranked input manifest; repeat to concatenate in priority order",
    )
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--exclude-manifest",
        action="append",
        type=Path,
        default=[],
        help="drop canonical rows found in another staged manifest (repeatable)",
    )
    parser.add_argument("--max-lines", type=int, default=1000)
    parser.add_argument("--max-bytes", type=int, default=950_000)
    args = parser.parse_args()
    if args.summary.exists():
        raise FileExistsError(args.summary)

    rows = []
    for path in args.input:
        rows.extend(
            raw.split("#", 1)[0].strip()
            for raw in path.read_text().splitlines()
            if raw.split("#", 1)[0].strip()
        )
    if len(rows) != len(set(rows)):
        raise ValueError("input manifest contains duplicate coefficient lines")
    excluded = set()
    for path in args.exclude_manifest:
        if not path.is_file():
            raise FileNotFoundError(path)
        excluded.update(
            raw.split("#", 1)[0].strip()
            for raw in path.read_text().splitlines()
            if raw.split("#", 1)[0].strip()
        )
    input_rows = len(rows)
    rows = [row for row in rows if row not in excluded]

    chunks: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for row in rows:
        row_bytes = len(row.encode("ascii")) + 1
        if current and (len(current) >= args.max_lines or current_bytes + row_bytes > args.max_bytes):
            chunks.append(current)
            current, current_bytes = [], 0
        if row_bytes > args.max_bytes:
            raise ValueError("one row exceeds byte limit")
        current.append(row)
        current_bytes += row_bytes
    if current:
        chunks.append(current)

    outputs = []
    for index, chunk in enumerate(chunks):
        path = Path(f"{args.output_prefix}_{index:03d}.txt")
        if path.exists():
            raise FileExistsError(path)
        text = "".join(row + "\n" for row in chunk)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii")
        outputs.append({
            "path": str(path.resolve()),
            "rows": len(chunk),
            "bytes": len(text.encode("ascii")),
            "sha256": hashlib.sha256(text.encode("ascii")).hexdigest(),
        })

    summary = {
        "schemaVersion": "ranked-submission-manifest-split-v1",
        "inputs": [str(path.resolve()) for path in args.input],
        "inputSha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in args.input],
        "rows": len(rows),
        "inputRows": input_rows,
        "excludedRows": input_rows - len(rows),
        "chunks": outputs,
        "checks": {
            "allRowsPreservedInOrder": sum((chunk for chunk in chunks), []) == rows,
            "allRowsUnique": len(rows) == len(set(rows)),
            "allChunksWithinLineLimit": all(item["rows"] <= args.max_lines for item in outputs),
            "allChunksWithinByteLimit": all(item["bytes"] <= args.max_bytes for item in outputs),
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(rows), "chunkCount": len(outputs), "checks": summary["checks"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

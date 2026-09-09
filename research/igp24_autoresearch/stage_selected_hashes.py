#!/usr/bin/env python3
"""Atomically stage an audited set of polynomial hashes from retained outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def polynomial_line(value) -> str | None:
    if not isinstance(value, str):
        return None
    line = value.strip()
    pieces = line.split(",")
    if len(pieces) != 25:
        return None
    try:
        coefficients = [int(piece) for piece in pieces]
    except ValueError:
        return None
    if coefficients[-1] != 1 or coefficients[0] == 0 or math.gcd(*coefficients) != 1:
        return None
    return line


def nested_coefficient_lines(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from nested_coefficient_lines(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_coefficient_lines(child)
    else:
        line = polynomial_line(value)
        if line is not None:
            yield line


def candidate_lines(path: Path):
    payload = path.read_text(encoding="utf-8")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError:
        document = None
    if document is not None:
        yield from nested_coefficient_lines(document)
        return
    for raw in payload.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            row = json.loads(line)
            yield from nested_coefficient_lines(row)
        else:
            candidate = polynomial_line(line)
            if candidate is not None:
                yield candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--sha256", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        parser.error(f"refusing to overwrite existing output: {args.output}")

    wanted = list(dict.fromkeys(args.sha256))
    if len(wanted) != len(args.sha256):
        parser.error("duplicate requested hash")
    if any(len(digest) != 64 for digest in wanted):
        parser.error("every requested digest must be a 64-character SHA-256")

    found: dict[str, str] = {}
    for source in args.source:
        for line in candidate_lines(source):
            digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
            if digest not in wanted:
                continue
            incumbent = found.get(digest)
            if incumbent is not None and incumbent != line:
                raise ValueError(f"hash collision for {digest}")
            found[digest] = line

    missing = [digest for digest in wanted if digest not in found]
    if missing:
        raise ValueError(f"requested hashes not found: {missing}")
    lines = [found[digest] for digest in wanted]
    if len(lines) != len(set(lines)):
        raise ValueError("duplicate polynomial lines selected")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "count": len(lines),
                "hashes": wanted,
                "manifest": str(args.output),
                "manifestSha256": hashlib.sha256(
                    args.output.read_bytes()
                ).hexdigest(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write a canonical manifest delta containing only locally unseen rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(raw: str) -> str:
    return ",".join(str(int(value)) for value in raw.split(","))


def receipt_hashes(receipts: Path) -> set[str]:
    output: set[str] = set()
    for path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_file():
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != str(receipt["manifestHash"]):
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    output.add(hashlib.sha256(canonical(line).encode("ascii")).hexdigest())
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    pending = receipt_hashes(args.receipts)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    selected: list[str] = []
    skipped = {"knownLedger": 0, "knownReceipt": 0, "duplicateInput": 0}
    seen: set[str] = set()
    try:
        for raw in args.input.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            line = canonical(line)
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            if digest in seen:
                skipped["duplicateInput"] += 1
            elif digest in pending:
                skipped["knownReceipt"] += 1
            elif connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone():
                skipped["knownLedger"] += 1
            else:
                selected.append(line)
            seen.add(digest)
    finally:
        connection.close()
    if not selected:
        raise ValueError("no unseen rows remain")
    rendered = "".join(line + "\n" for line in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps({
        "inputRows": len(seen),
        "output": str(args.output.resolve()),
        "outputRows": len(selected),
        "outputSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "skipped": skipped,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

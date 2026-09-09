#!/usr/bin/env python3
"""Seal every fresh exact degree-24 factor from live pair-resolvent jobs."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def receipt_hashes(receipts: Path) -> set[str]:
    result: set[str] = set()
    for receipt_path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text())
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_absolute():
                manifest = ROOT / manifest
            body = manifest.read_bytes()
            if hashlib.sha256(body).hexdigest() != str(receipt["manifestHash"]):
                continue
            for raw in body.decode().splitlines():
                value = raw.split("#", 1)[0].strip()
                if value:
                    canonical = ",".join(str(int(x)) for x in value.split(","))
                    result.add(hashlib.sha256(canonical.encode()).hexdigest())
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite pair-resolvent stage outputs")
    paths: list[Path] = []
    for pattern in args.input:
        paths.extend(Path(value) for value in sorted(glob.glob(str(ROOT / pattern))))
    if not paths:
        raise FileNotFoundError("no pair-resolvent results matched")

    receipts = receipt_hashes(args.receipts)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    skips: Counter[str] = Counter()
    selected: dict[str, dict] = {}
    try:
        for path in sorted(set(paths)):
            for raw in path.read_text().splitlines():
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                if payload.get("status") == "certified_multi":
                    candidates = payload.get("candidates", [])
                elif payload.get("status") == "certified":
                    candidates = [payload]
                else:
                    skips["uncertified_result"] += 1
                    continue
                for candidate in candidates:
                    line = ",".join(str(int(x)) for x in str(candidate["coefficientLine"]).split(","))
                    digest = hashlib.sha256(line.encode()).hexdigest()
                    if digest != str(candidate["coefficientSha256"]):
                        raise ValueError(f"candidate hash mismatch in {path}")
                    if digest in receipts:
                        skips["receipt_hash"] += 1
                        continue
                    if connection.execute(
                        "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
                    ).fetchone():
                        skips["known_hash"] += 1
                        continue
                    selected.setdefault(
                        digest,
                        {
                            "artifact": str(path.relative_to(ROOT)),
                            "coefficientLine": line,
                            "coefficientSha256": digest,
                            "r": int(candidate["targetR"]),
                            "sourceLabel": str(payload["sourceLabel"]),
                            "sourceR": int(payload["sourceR"]),
                        },
                    )
    finally:
        connection.close()
    rows = sorted(
        selected.values(),
        key=lambda row: (int(row["sourceLabel"][3:]), row["sourceR"], row["r"], row["coefficientSha256"]),
    )
    if not rows:
        raise ValueError("no fresh exact pair-resolvent factors remain")
    manifest = "".join(str(row["coefficientLine"]) + "\n" for row in rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest)
    summary = {
        "inputArtifacts": [str(path.relative_to(ROOT)) for path in sorted(set(paths))],
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "selectedRows": len(rows),
        "skipCounts": dict(sorted(skips.items())),
        "sourceCount": len({(row["sourceLabel"], row["sourceR"]) for row in rows}),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

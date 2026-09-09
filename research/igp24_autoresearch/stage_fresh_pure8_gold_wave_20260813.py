#!/usr/bin/env python3
"""Stage a receipt-safe wave from the fresh PURE8C3 gold sweep."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path

import build_architecture_discovery_portfolio_20260813 as architecture


ROOT = Path(__file__).resolve().parent


def height(line: str) -> tuple[int, int, str]:
    values = [abs(int(item)) for item in line.split(",")]
    return max(values).bit_length(), sum(item.bit_length() for item in values), line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--wave", type=int, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument(
        "--input-glob",
        action="append",
        default=[],
        help=(
            "JSONL input glob, relative to the repository root; repeat for "
            "multiple sweep directories. Defaults to the original negative sweep."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite PURE8C3 wave")

    patterns = args.input_glob or [
        "data/pure8_gold_negative_host*_20260813/**/*.jsonl"
    ]
    files = sorted(
        {
            Path(item)
            for pattern in patterns
            for item in glob.glob(str(ROOT / pattern), recursive=True)
        }
    )
    if not files:
        raise ValueError(f"no JSONL inputs matched: {patterns}")
    candidates: dict[str, dict] = {}
    duplicate_rows = 0
    for path in files:
        for raw in path.open(encoding="utf-8"):
            row = json.loads(raw)
            line = architecture.canonical(row["coefficients"])
            digest = architecture.digest(line)
            if digest != str(row["candidate_hash"]):
                raise ValueError(f"hash mismatch in {path}")
            normalized = {
                "hash": digest,
                "line": line,
                "root": int(row["local_root_count"]),
                "cubic": (
                    ("C3", int(row["parameters"]["t"]))
                    if "t" in row["parameters"]
                    else (
                        "S3",
                        int(row["parameters"]["p"]),
                        int(row["parameters"]["q"]),
                    )
                ),
                "a": tuple(int(value) for value in row["parameters"]["a"]),
                "source": str(path.relative_to(ROOT)),
            }
            if digest in candidates:
                duplicate_rows += 1
            else:
                candidates[digest] = normalized

    excluded = architecture.receipt_hashes(args.receipts)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    hashes = list(candidates)
    for offset in range(0, len(hashes), 800):
        batch = hashes[offset : offset + 800]
        marks = ",".join("?" for _ in batch)
        excluded.update(
            str(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT coefficient_hash FROM polynomials WHERE coefficient_hash IN ({marks})",
                batch,
            )
        )
    connection.close()

    chambers: dict[tuple[int, tuple[int, ...]], list[dict]] = defaultdict(list)
    for digest, row in candidates.items():
        if digest not in excluded:
            # t and coefficient sign/zero pattern are cheap proxies for distinct
            # cyclic cubic and Kummer local behavior.
            pattern = tuple(0 if value == 0 else (1 if value > 0 else -1) for value in row["a"])
            chambers[(row["cubic"], pattern)].append(row)
    queues = {}
    for key, rows in chambers.items():
        rows.sort(key=lambda row: (height(row["line"]), row["a"], row["hash"]))
        queues[key] = deque(rows)
    keys = sorted(queues)
    if keys:
        shift = (args.wave - 1) % len(keys)
        keys = keys[shift:] + keys[:shift]
    active = deque(keys)
    selected = []
    while active and len(selected) < args.size:
        key = active.popleft()
        queue = queues[key]
        selected.append(queue.popleft())
        if queue:
            active.append(key)
    if len(selected) != args.size:
        raise ValueError(f"only {len(selected)} eligible PURE8C3 candidates remain")

    manifest = "".join(row["line"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "purpose": "fresh cyclic-cubic pure-octic order-768 gold probe",
        "wave": args.wave,
        "inputGlobs": patterns,
        "files": len(files),
        "inputRows": sum(1 for path in files for _ in path.open(encoding="utf-8")),
        "duplicateRows": duplicate_rows,
        "candidateUniverse": len(candidates),
        "excludedKnownOrReceipted": len(set(candidates) & excluded),
        "eligibleCandidates": len(candidates) - len(set(candidates) & excluded),
        "eligibleChambers": len(chambers),
        "selectedRows": len(selected),
        "rootDistribution": dict(sorted(Counter(row["root"] for row in selected).items())),
        "cubicDistribution": dict(sorted(
            Counter(":".join(map(str, row["cubic"])) for row in selected).items()
        )),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a dense, receipt-safe raid from selected architecture families."""

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
ARCHIVE = ROOT.parent / "routeA" / "data"


def height(line: str) -> tuple[int, int, str]:
    values = [abs(int(item)) for item in line.split(",")]
    return max(values).bit_length(), sum(item.bit_length() for item in values), line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", action="append", required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite targeted family raid")
    requested = set(args.family)

    candidates: dict[str, dict] = {}
    skip = Counter()
    paths = architecture.candidate_files()
    for path in paths:
        for raw in path.open(encoding="utf-8", errors="replace"):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                skip["invalidJson"] += 1
                continue
            if not isinstance(row, dict) or row.get("local_irreducible") is not True:
                continue
            family = architecture.family(row, path.name)
            if family not in requested:
                continue
            try:
                line = architecture.canonical(row["coefficients"])
            except (KeyError, TypeError, ValueError):
                skip["invalidCoefficients"] += 1
                continue
            digest = architecture.digest(line)
            if row.get("candidate_hash") and str(row["candidate_hash"]) != digest:
                raise ValueError(f"candidate hash mismatch in {path}")
            parameters = row.get("parameters") if isinstance(row.get("parameters"), dict) else {}
            normalized = {
                "hash": digest,
                "line": line,
                "family": family,
                "form": str(parameters.get("form", "")),
                "root": int(row.get("local_root_count", row.get("target_r", -1))),
                "base": tuple(parameters.get("base_coefficients") or ()),
                "source": path.name,
            }
            incumbent = candidates.get(digest)
            if incumbent is None or (normalized["source"], normalized["form"]) < (incumbent["source"], incumbent["form"]):
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

    # Keep every eligible field, but interleave root/form/base chambers so one
    # high-volume parameter choice cannot consume the whole API batch.
    chambers: dict[tuple, list[dict]] = defaultdict(list)
    for digest, row in candidates.items():
        if digest not in excluded:
            chambers[(row["root"], row["form"], row["base"], row["source"])].append(row)
    queues = {}
    for key, rows in chambers.items():
        rows.sort(key=lambda row: (height(row["line"]), row["hash"]))
        queues[key] = deque(rows)
    roots = sorted({key[0] for key in queues})
    active = deque(
        key
        for root in roots
        for key in sorted((item for item in queues if item[0] == root), key=lambda item: (item[1], item[2], item[3]))
    )
    selected = []
    while active and len(selected) < args.size:
        key = active.popleft()
        queue = queues[key]
        selected.append(queue.popleft())
        if queue:
            active.append(key)
    if len(selected) != args.size:
        raise ValueError(f"only {len(selected)} eligible targeted candidates remain")

    manifest = "".join(row["line"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "purpose": "dense classified-yield-guided architecture raid",
        "families": sorted(requested),
        "candidateUniverse": len(candidates),
        "excludedKnownOrReceipted": len(set(candidates) & excluded),
        "eligibleCandidates": len(candidates) - len(set(candidates) & excluded),
        "eligibleChambers": len(chambers),
        "selectedRows": len(selected),
        "rootDistribution": dict(sorted(Counter(row["root"] for row in selected).items())),
        "formDistribution": dict(sorted(Counter(row["form"] for row in selected).items())),
        "sourceDistribution": dict(sorted(Counter(row["source"] for row in selected).items())),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

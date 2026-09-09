#!/usr/bin/env python3
"""Stage every unseen exact degree-24 row from all local campaign archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent


def canonical(value: object) -> str:
    if isinstance(value, list):
        parts = [int(item) for item in value]
    else:
        parts = [int(item.strip()) for item in str(value).split(",")]
    if (
        len(parts) != 25
        or parts[0] == 0
        or parts[-1] != 1
        or math.gcd(*parts) != 1
    ):
        raise ValueError("not a primitive monic degree-24 polynomial")
    return ",".join(map(str, parts))


def nested_dicts(value: object) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_dicts(child)


def archive_rows(path: Path) -> Iterator[tuple[int, dict]]:
    yielded = False
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if '"submission_ready"' not in raw or "true" not in raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for row in nested_dicts(value):
            yielded = True
            yield line_number, row
    if yielded:
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for row in nested_dicts(value):
        yield 0, row


def receipt_hashes(directory: Path) -> set[str]:
    output: set[str] = set()
    for receipt_path in directory.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
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
    parser.add_argument("--archive-root", action="append", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--max-lines", type=int, default=900)
    parser.add_argument("--max-bytes", type=int, default=900_000)
    args = parser.parse_args()
    if args.certificate.exists():
        raise FileExistsError(args.certificate)

    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    ledger_hashes = len(known)
    receipt = receipt_hashes(args.receipts)
    known.update(receipt)

    skips = Counter()
    selected: dict[str, dict] = {}
    archive_files = []
    for archive_root in args.archive_root:
        paths = sorted(set(archive_root.rglob("*.jsonl")) | set(archive_root.rglob("*.json")))
        archive_files.extend(paths)
        for path in paths:
            try:
                rows = archive_rows(path)
                for line_number, row in rows:
                    if row.get("submission_ready") is not True:
                        skips["not_submission_ready"] += 1
                        continue
                    if row.get("exact_compatibility_proven") is not True:
                        skips["not_exact"] += 1
                        continue
                    if "label_probability" in row and float(row["label_probability"]) != 1.0:
                        skips["nonunit_label_probability"] += 1
                        continue
                    if "valid_probability" in row and float(row["valid_probability"]) != 1.0:
                        skips["nonunit_valid_probability"] += 1
                        continue
                    if row.get("local_irreducible") is False:
                        skips["reducible"] += 1
                        continue
                    try:
                        line = canonical(row["coefficients"])
                    except (KeyError, TypeError, ValueError):
                        skips["invalid_polynomial"] += 1
                        continue
                    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                    claimed = row.get("candidate_hash")
                    if claimed is not None and str(claimed) != digest:
                        skips["hash_mismatch"] += 1
                        continue
                    if digest in known:
                        skips["known_ledger_or_receipt"] += 1
                        continue
                    if digest in selected:
                        skips["duplicate_archive"] += 1
                        continue
                    selected[digest] = {
                        "line": line,
                        "source": str(path.resolve()),
                        "sourceLine": line_number,
                        "offlineTargetT": row.get("target_t"),
                        "offlineRootCount": row.get("local_root_count", row.get("target_r")),
                        "construction": row.get("construction_overgroup"),
                    }
            except (OSError, UnicodeDecodeError):
                skips["unreadable_file"] += 1

    prefix = args.output_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    current: list[tuple[str, dict]] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        path = prefix.with_name(f"{prefix.name}_{len(chunks):03d}.txt")
        if path.exists():
            raise FileExistsError(path)
        rendered = "".join(item[1]["line"] + "\n" for item in current)
        path.write_text(rendered, encoding="utf-8")
        chunks.append(
            {
                "path": str(path.resolve()),
                "rows": len(current),
                "bytes": len(rendered.encode("ascii")),
                "sha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
            }
        )
        current = []
        current_bytes = 0

    for item in sorted(selected.items()):
        size = len(item[1]["line"].encode("ascii")) + 1
        if current and (len(current) >= args.max_lines or current_bytes + size > args.max_bytes):
            flush()
        current.append(item)
        current_bytes += size
    flush()

    certificate = {
        "archiveFilesScanned": len(set(archive_files)),
        "archiveRoots": [str(path.resolve()) for path in args.archive_root],
        "checks": {
            "allSelectedExactCompatibilityProven": True,
            "allSelectedSubmissionReady": True,
            "allSelectedDegree24PrimitiveMonic": True,
            "allClaimedHashesMatched": skips["hash_mismatch"] == 0,
            "ledgerAndReceiptHashesExcluded": True,
        },
        "chunks": chunks,
        "ledgerHashes": ledger_hashes,
        "receiptHashes": len(receipt),
        "selectedRows": len(selected),
        "skipCounts": dict(sorted(skips.items())),
    }
    args.certificate.parent.mkdir(parents=True, exist_ok=True)
    args.certificate.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(certificate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

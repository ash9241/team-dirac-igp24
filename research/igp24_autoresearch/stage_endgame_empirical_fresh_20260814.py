#!/usr/bin/env python3
"""Stage fresh locally irreducible endgame exploration fields into API-safe chunks."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(value: object) -> str | None:
    raw = value if isinstance(value, list) else str(value).strip().split(",")
    try:
        coefficients = [int(item) for item in raw]
    except (TypeError, ValueError):
        return None
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
    ):
        return None
    return ",".join(map(str, coefficients))


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def manifest_hashes(paths: list[Path]) -> set[str]:
    result: set[str] = set()
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for raw in lines:
            line = canonical(raw.split("#", 1)[0].strip())
            if line is not None:
                result.add(digest(line))
    return result


def resolve_inputs(patterns: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        absolute = pattern if Path(pattern).is_absolute() else str(ROOT.parent / pattern)
        matches = [Path(value).resolve() for value in glob.glob(absolute)]
        if not matches:
            raise FileNotFoundError(pattern)
        paths.update(path for path in matches if path.is_file())
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--outbox", type=Path, default=ROOT / "outbox")
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--max-lines", type=int, default=1000)
    parser.add_argument("--max-bytes", type=int, default=900_000)
    args = parser.parse_args()
    if args.certificate.exists():
        raise FileExistsError(args.certificate)
    if args.max_lines < 1 or args.max_bytes < 1:
        raise ValueError("chunk bounds must be positive")

    inputs = resolve_inputs(args.input)
    excluded_paths = [path for path in args.outbox.rglob("*") if path.is_file()]
    for receipt_path in args.receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            recorded = str(receipt["manifestHash"])
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
        if not manifest.is_absolute():
            manifest = ROOT / manifest
        if (
            manifest.is_file()
            and hashlib.sha256(manifest.read_bytes()).hexdigest() == recorded
        ):
            excluded_paths.append(manifest)
    excluded = manifest_hashes(excluded_paths)

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        ledger = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
    finally:
        connection.close()
    excluded.update(ledger)

    skips: Counter[str] = Counter()
    candidates: dict[str, dict] = {}
    for path in inputs:
        relative = str(path.relative_to(ROOT.parent))
        with path.open(encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
                if not isinstance(row, dict) or row.get("local_irreducible") is not True:
                    skips["not_locally_irreducible"] += 1
                    continue
                polynomial = canonical(row.get("coefficients"))
                if polynomial is None:
                    skips["invalid_polynomial"] += 1
                    continue
                candidate_hash = digest(polynomial)
                if candidate_hash != str(row.get("candidate_hash", "")):
                    raise ValueError(f"candidate hash mismatch at {path}:{line_number}")
                try:
                    roots = int(row["local_root_count"])
                    target_r = int(row["target_r"])
                    target_t = int(row.get("target_t", 0))
                except (KeyError, TypeError, ValueError):
                    skips["invalid_signature"] += 1
                    continue
                if roots != target_r or roots < 0 or roots > 24 or roots % 2:
                    skips["invalid_signature"] += 1
                    continue
                if target_t != 0:
                    skips["unexpected_exact_label_claim"] += 1
                    continue
                if candidate_hash in excluded:
                    skips["known_ledger_receipt_or_outbox"] += 1
                    continue
                if candidate_hash in candidates:
                    skips["duplicate_input_hash"] += 1
                    continue
                parameters = row.get("parameters")
                form = str(parameters.get("form", "")) if isinstance(parameters, dict) else ""
                candidates[candidate_hash] = {
                    "hash": candidate_hash,
                    "line": polynomial,
                    "family": str(row.get("recipe_family", "unknown")),
                    "form": form,
                    "root": roots,
                    "source": relative,
                }

    # Round-robin every source/family/signature chamber.  This preserves the
    # complete fresh pool while making every early API chunk architecture-rich.
    buckets: dict[tuple[str, int, str, str], deque[dict]] = defaultdict(deque)
    for row in candidates.values():
        buckets[(row["family"], row["root"], row["form"], row["source"])].append(row)
    for queue in buckets.values():
        ordered = sorted(queue, key=lambda row: row["hash"])
        queue.clear()
        queue.extend(ordered)
    active = deque(sorted(buckets))
    selected: list[dict] = []
    while active:
        key = active.popleft()
        queue = buckets[key]
        selected.append(queue.popleft())
        if queue:
            active.append(key)

    prefix = args.output_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    current: list[dict] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        path = prefix.with_name(f"{prefix.name}_{len(chunks):03d}.txt")
        if path.exists():
            raise FileExistsError(path)
        rendered = "".join(row["line"] + "\n" for row in current)
        path.write_text(rendered, encoding="ascii")
        chunks.append(
            {
                "path": str(path.resolve().relative_to(ROOT)),
                "rows": len(current),
                "bytes": len(rendered.encode("ascii")),
                "sha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
                "familyDistribution": dict(sorted(Counter(row["family"] for row in current).items())),
                "rootDistribution": dict(sorted(Counter(row["root"] for row in current).items())),
            }
        )
        current = []
        current_bytes = 0

    for row in selected:
        size = len(row["line"].encode("ascii")) + 1
        if current and (
            len(current) >= args.max_lines or current_bytes + size > args.max_bytes
        ):
            flush()
        current.append(row)
        current_bytes += size
    flush()

    certificate = {
        "schemaVersion": "endgame-empirical-fresh-stage-v1",
        "inputs": [str(path.relative_to(ROOT.parent)) for path in inputs],
        "inputFiles": len(inputs),
        "selectedRows": len(selected),
        "chunks": chunks,
        "chunkCount": len(chunks),
        "familyDistribution": dict(sorted(Counter(row["family"] for row in selected).items())),
        "rootDistribution": dict(sorted(Counter(row["root"] for row in selected).items())),
        "skipCounts": dict(sorted(skips.items())),
        "checks": {
            "allRowsPrimitiveMonicDegree24": True,
            "allRowsLocallyIrreducible": True,
            "allRowsHaveMatchingDeclaredHash": True,
            "allRowsHaveValidExactLocalSignature": True,
            "allLedgerReceiptAndOutboxHashesExcluded": True,
            "allChunksWithinLineLimit": all(item["rows"] <= args.max_lines for item in chunks),
            "allChunksWithinByteLimit": all(item["bytes"] <= args.max_bytes for item in chunks),
        },
    }
    args.certificate.parent.mkdir(parents=True, exist_ok=True)
    args.certificate.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "inputFiles": len(inputs),
        "selectedRows": len(selected),
        "chunkCount": len(chunks),
        "skipCounts": dict(sorted(skips.items())),
        "familyDistribution": certificate["familyDistribution"],
        "rootDistribution": certificate["rootDistribution"],
        "checks": certificate["checks"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

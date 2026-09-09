#!/usr/bin/env python3
"""Rebuild transient immutable-SQLite failures against a stable DB backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-bank", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    args = parser.parse_args()
    if args.output_bank.exists() or args.summary.exists():
        raise ValueError("refusing to overwrite an existing retry bank")
    connection = sqlite3.connect(f"file:{args.snapshot.resolve()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("stable snapshot fails SQLite quick_check")
        snapshot_counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("submissions", "polynomials", "verifications", "targets")
        }
    finally:
        connection.close()

    failed = {
        int(row["globalCommandIndex"]): row
        for row in load_jsonl(args.results)
        if row.get("workerFailure")
        and "database disk image is malformed" in str(row.get("stderrTail", ""))
    }
    flattened = []
    for row in load_jsonl(args.bank):
        for attempt in row.get("attempts", []):
            flattened.append((row, attempt))
    retry_rows = []
    for index in sorted(failed):
        row, attempt = flattened[index]
        cloned_attempt = dict(attempt)
        command = [str(value) for value in attempt["command"]]
        command[3:3] = ["--db", str(args.snapshot.resolve())]
        suffix = hashlib.sha256(
            json.dumps(attempt["identity"], separators=(",", ":")).encode()
        ).hexdigest()[:10]
        output = args.outputs.resolve() / (
            f"{row['logicalTaskId']}__sqlite_retry__{suffix}.json"
        )
        output_index = command.index("--output") + 1
        command[output_index] = str(output.relative_to(ROOT))
        cloned_attempt.update(
            {
                "command": command,
                "output": str(output),
                "retryOfGlobalCommandIndex": index,
                "stableSnapshot": str(args.snapshot.resolve()),
            }
        )
        retry_rows.append(
            {
                "attempts": [cloned_attempt],
                "logicalTaskId": row["logicalTaskId"],
                "status": "retry_transient_sqlite_snapshot_failure",
                "target": row["target"],
            }
        )
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in retry_rows
    )
    write_atomic(args.output_bank, rendered)
    summary = {
        "bank": str(args.bank.resolve()),
        "bankSha256": sha256_path(args.bank),
        "networkCalls": 0,
        "outputBank": str(args.output_bank.resolve()),
        "outputBankSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "results": str(args.results.resolve()),
        "resultsSha256": sha256_path(args.results),
        "retryCommands": len(retry_rows),
        "snapshot": str(args.snapshot.resolve()),
        "snapshotCounts": snapshot_counts,
        "snapshotSha256": sha256_path(args.snapshot),
        "submissionCalls": 0,
    }
    write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

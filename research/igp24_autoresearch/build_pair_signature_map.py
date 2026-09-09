#!/usr/bin/env python3
"""Build complex-conjugation profiles for multi-orbit source actions."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
ORBIT_MAP = ROOT / "data" / "pair_orbit_map.jsonl"
WORKER = ROOT / "pair_signature_one.sage.py"
OUTPUT = ROOT / "data" / "pair_signature_map.jsonl"
RANK12_TEAM_ID = "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"


def harvest_pairs(conn: sqlite3.Connection) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    """Return current nonbaseline gold and the latest rank-12 unique pairs."""
    gold = {
        (str(label), int(r))
        for label, r in conn.execute(
            """
            SELECT t.label,t.r
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            WHERE t.team_count=0 AND b.label IS NULL
            """
        )
    }
    latest_crawl = conn.execute(
        """
        SELECT MAX(crawl_id)
        FROM public_team_placement_crawls
        WHERE complete=1 AND team_id=?
        """,
        (RANK12_TEAM_ID,),
    ).fetchone()[0]
    rank12_unique: set[tuple[str, int]] = set()
    if latest_crawl is not None:
        rank12_unique = {
            (str(label), int(r))
            for label, r in conn.execute(
                """
                SELECT label,r
                FROM public_team_placements
                WHERE crawl_id=? AND team_id=? AND k_teams=1
                """,
                (int(latest_crawl), RANK12_TEAM_ID),
            )
        }
    return gold, rank12_unique


def sources() -> list[tuple[str, int]]:
    mapped = [
        json.loads(line)
        for line in ORBIT_MAP.read_text(encoding="utf-8").splitlines()
        if line
    ]
    with sqlite3.connect(DB_PATH) as conn:
        available = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT label FROM verifications WHERE scoreable=1"
            )
        }
        gold, rank12_unique = harvest_pairs(conn)
    interesting_labels = {label for label, _r in gold | rank12_unique}

    # The orbit census knows target labels but not real-root signatures.  A
    # target-label intersection is therefore the strongest safe prefilter
    # before computing the exact complex-conjugation profiles.
    multi = {
        str(row["sourceLabel"]): int(row["sourceT"])
        for row in mapped
        if int(row["length24OrbitCount"]) > 1
        and str(row["sourceLabel"]) in available
        and any(
            str(target["targetLabel"]) in interesting_labels
            for target in row["targets"]
        )
    }
    return sorted(
        multi.items(),
        key=lambda item: item[1],
    )


def load_checkpoint(eligible_labels: set[str], fresh: bool) -> dict[str, dict]:
    if fresh or not OUTPUT.exists():
        return {}
    rows: dict[str, dict] = {}
    for line in OUTPUT.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        label = str(row["sourceLabel"])
        if label in eligible_labels:
            rows[label] = row
    return rows


def write_checkpoint(rows_by_label: dict[str, dict]) -> None:
    rows = sorted(rows_by_label.values(), key=lambda row: int(row["sourceT"]))
    temporary = OUTPUT.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(OUTPUT)


def split_cached_sources(
    source_rows: list[tuple[str, int]], rows_by_label: dict[str, dict]
) -> tuple[set[str], list[tuple[str, int]]]:
    cached_certified = {
        label
        for label, t in source_rows
        if (row := rows_by_label.get(label)) is not None
        and row.get("status") == "certified"
        and int(row.get("sourceT", -1)) == t
    }
    pending = [source for source in source_rows if source[0] not in cached_certified]
    return cached_certified, pending


def run_one(source: tuple[str, int], timeout: int) -> dict:
    label, t = source
    try:
        completed = subprocess.run(
            ["sage", "-python", str(WORKER), label, str(t)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"sourceLabel": label, "sourceT": t, "status": "timeout"}
    if completed.returncode != 0:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "error",
            "error": completed.stderr[-2000:],
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "invalid_output",
            "error": str(exc),
        }
    row["status"] = "certified"
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    if args.checkpoint_every < 1:
        parser.error("--checkpoint-every must be at least 1")

    source_rows = sources()
    eligible_labels = {label for label, _t in source_rows}
    rows_by_label = load_checkpoint(eligible_labels, args.fresh)
    cached_certified, pending = split_cached_sources(source_rows, rows_by_label)
    completed_count = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(run_one, source, args.timeout): source
                for source in pending
            }
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                rows_by_label[str(row["sourceLabel"])] = row
                completed_count += 1
                if completed_count % args.checkpoint_every == 0:
                    write_checkpoint(rows_by_label)
                if completed_count % 25 == 0 or completed_count == len(pending):
                    print(
                        f"profiled {len(cached_certified) + completed_count}/"
                        f"{len(source_rows)}: {row['sourceLabel']} "
                        f"status={row['status']}",
                        file=sys.stderr,
                        flush=True,
                    )
    finally:
        write_checkpoint(rows_by_label)

    rows = list(rows_by_label.values())
    summary = {
        "sources": len(source_rows),
        "cachedCertified": len(cached_certified),
        "profiledThisRun": completed_count,
        "certified": sum(row["status"] == "certified" for row in rows),
        "failed": sum(row["status"] != "certified" for row in rows),
        "output": str(OUTPUT),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

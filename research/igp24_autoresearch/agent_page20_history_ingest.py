#!/usr/bin/env python3
"""Checkpointed, bounded ingestion of exactly history page 20.

The public history list is traversed in 100-submission pages.  Pages 1--19
are evidence-only and page 20 is frozen before any detail download.  Network
reads are parallel, while every SQLite write is performed serially by the
main thread through the standard audited ledger writer.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
from pathlib import Path

import sair_api


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PRE = DATA / "agent_page20_pre_ingest_checkpoint.json"
CURSOR_STATE = DATA / "agent_page20_history_cursor_checkpoint.json"
PAGE_ITEMS = DATA / "agent_page20_history_page_items.jsonl"
INGEST_RESULTS = DATA / "agent_page20_history_ingest_results.jsonl"
SUMMARY = DATA / "agent_page20_history_ingest_summary.json"
PAGE_SIZE = 100
TARGET_PAGE = 20


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for value in values:
                handle.write(json.dumps(value, separators=(",", ":"), sort_keys=True))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def retry(callable_, *, attempts: int, label: str):
    errors = []
    for attempt in range(1, attempts + 1):
        try:
            return callable_(), errors
        except (RuntimeError, OSError, TimeoutError, urllib.error.URLError) as exc:
            errors.append({"attempt": attempt, "error": str(exc)})
            if attempt == attempts:
                raise RuntimeError(f"{label} failed after {attempts} attempts: {exc}") from exc
            time.sleep(min(2 ** (attempt - 1), 16))
    raise AssertionError("unreachable retry exit")


def list_page(cursor: str | None, timeout: int, attempts: int):
    query: dict[str, str | int] = {"limit": PAGE_SIZE}
    if cursor:
        query["cursor"] = cursor
    path = "/submissions/me?" + urllib.parse.urlencode(query)
    return retry(
        lambda: sair_api.api_json("GET", path, timeout=timeout),
        attempts=attempts,
        label="history list page",
    )


def fetch_bundle(submission_id: str, timeout: int, attempts: int):
    def fetch():
        detail = sair_api.api_json(
            "GET", f"/submissions/{submission_id}", timeout=timeout
        )
        _status, _headers, raw = sair_api.api_request(
            "GET", f"/submissions/{submission_id}/download", timeout=timeout
        )
        if str(detail.get("submissionId")) != submission_id:
            raise ValueError("detail response submission ID mismatch")
        downloaded = raw.decode("utf-8")
        sair_api.parse_download(downloaded)
        return detail, downloaded

    bundle, errors = retry(
        fetch, attempts=attempts, label=f"submission bundle {submission_id}"
    )
    return bundle[0], bundle[1], errors


def ledger_counts() -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        return {
            "submissions": int(connection.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]),
            "polynomials": int(connection.execute("SELECT COUNT(*) FROM polynomials").fetchone()[0]),
            "verifications": int(connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]),
            "scoreableVerifications": int(connection.execute("SELECT COUNT(*) FROM verifications WHERE scoreable=1").fetchone()[0]),
        }
    finally:
        connection.close()


def freeze_page(timeout: int, attempts: int) -> tuple[list[dict], dict]:
    pre = json.loads(PRE.read_text(encoding="utf-8"))
    boundary = str(pre["oldestCreatedAtExclusive"])
    if PAGE_ITEMS.exists():
        items = [
            json.loads(line)
            for line in PAGE_ITEMS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(items) != PAGE_SIZE:
            raise RuntimeError("existing frozen page-20 item set is not 100 rows")
        return items, {"resumedFrozenPage": True, "listRetryErrors": []}

    if CURSOR_STATE.exists():
        state = json.loads(CURSOR_STATE.read_text(encoding="utf-8"))
    else:
        state = {
            "completedPages": 0,
            "nextCursor": None,
            "pageBoundaries": [],
            "pageSize": PAGE_SIZE,
            "targetPage": TARGET_PAGE,
        }
        write_json(CURSOR_STATE, state)

    all_errors = []
    frozen_items = None
    while int(state["completedPages"]) < TARGET_PAGE:
        page_number = int(state["completedPages"]) + 1
        data, errors = list_page(state.get("nextCursor"), timeout, attempts)
        all_errors.extend({"page": page_number, **row} for row in errors)
        raw_items = list(data.get("items") or [])
        if len(raw_items) != PAGE_SIZE:
            raise RuntimeError(
                f"history page {page_number} returned {len(raw_items)}, expected {PAGE_SIZE}"
            )
        ids = [str(item.get("submissionId") or "") for item in raw_items]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            raise RuntimeError(f"history page {page_number} has invalid submission IDs")
        created = [str(item.get("createdAt") or "") for item in raw_items]
        page_evidence = {
            "page": page_number,
            "firstCreatedAt": created[0],
            "firstSubmissionId": ids[0],
            "lastCreatedAt": created[-1],
            "lastSubmissionId": ids[-1],
            "rowCount": len(ids),
        }
        next_cursor = data.get("nextCursor")
        if page_number < TARGET_PAGE and not isinstance(next_cursor, str):
            raise RuntimeError(f"history ended before page {TARGET_PAGE}")
        state["completedPages"] = page_number
        state["nextCursor"] = next_cursor
        state["pageBoundaries"] = [
            *[row for row in state["pageBoundaries"] if int(row["page"]) < page_number],
            page_evidence,
        ]
        write_json(CURSOR_STATE, state)
        print(json.dumps({"event": "history_page", **page_evidence}), flush=True)
        if page_number == TARGET_PAGE:
            frozen_items = [
                {
                    "createdAt": str(item.get("createdAt") or ""),
                    "failedCountFromList": len(item.get("failedPolynomials") or []),
                    "submissionId": str(item["submissionId"]),
                    "updatedAt": str(item.get("updatedAt") or ""),
                    "verifiedCountFromList": len(item.get("verifiedPolynomials") or []),
                }
                for item in raw_items
            ]

    if frozen_items is None:
        raise RuntimeError("cursor state reached page 20 without freezing its rows")
    if any(str(row["createdAt"]) >= boundary for row in frozen_items):
        raise RuntimeError("page-20 row overlaps the page-19 createdAt boundary")
    write_jsonl(PAGE_ITEMS, frozen_items)
    return frozen_items, {
        "resumedFrozenPage": False,
        "listRetryErrors": all_errors,
        "pageBoundaries": state["pageBoundaries"],
    }


def ingest(items: list[dict], workers: int, timeout: int, attempts: int):
    ids = [str(row["submissionId"]) for row in items]
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    known = {
        str(row[0])
        for row in connection.execute(
            f"SELECT submission_id FROM submissions WHERE submission_id IN ({','.join('?' for _ in ids)})",
            ids,
        )
    }
    connection.close()
    pending = [value for value in ids if value not in known]
    results = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_bundle, submission_id, timeout, attempts): submission_id
            for submission_id in pending
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            submission_id = futures[future]
            try:
                detail, downloaded, retry_errors = future.result()
                with sair_api.connect_db() as writable:
                    result = sair_api.store_submission(writable, detail, downloaded)
                result["retryErrors"] = retry_errors
                results.append(result)
                print(
                    json.dumps(
                        {
                            "completed": index,
                            "event": "history_ingest",
                            "submissionId": submission_id,
                            "verified": result["verified"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                failures.append({"submissionId": submission_id, "error": str(exc)})
    results.sort(key=lambda row: row["submissionId"])
    write_jsonl(INGEST_RESULTS, results)
    if failures:
        raise RuntimeError(f"page-20 bundle failures: {failures}")
    return results, len(known), len(pending)


def run(*, workers: int, timeout: int, attempts: int) -> int:
    if not 1 <= workers <= 12:
        raise ValueError("workers must be in 1..12")
    if timeout < 30 or not 1 <= attempts <= 8:
        raise ValueError("invalid timeout or attempts")

    started = time.monotonic()
    before = ledger_counts()
    items, traversal = freeze_page(timeout, attempts)
    results, known, pending = ingest(items, workers, timeout, attempts)
    after = ledger_counts()
    frozen_ids = {str(row["submissionId"]) for row in items}
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    ledger_ids = {
        str(row[0])
        for row in connection.execute(
            f"SELECT submission_id FROM submissions WHERE submission_id IN ({','.join('?' for _ in frozen_ids)})",
            sorted(frozen_ids),
        )
    }
    connection.close()
    if ledger_ids != frozen_ids:
        raise RuntimeError("not every frozen page-20 submission is present in the ledger")

    summary = {
        "after": after,
        "before": before,
        "boundedHistoryPage": TARGET_PAGE,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "frozenPageRows": len(items),
        "ingestedThisRun": len(results),
        "knownBeforeRun": known,
        "networkBundleReadsThisRun": pending,
        "parallelNetworkWorkers": workers,
        "serializedLedgerWrites": True,
        "traversal": traversal,
        "paths": {
            "cursorCheckpoint": {"path": str(CURSOR_STATE), "sha256": sha256_path(CURSOR_STATE)},
            "frozenPageItems": {"path": str(PAGE_ITEMS), "sha256": sha256_path(PAGE_ITEMS)},
            "ingestResults": {"path": str(INGEST_RESULTS), "sha256": sha256_path(INGEST_RESULTS)},
            "preIngestCheckpoint": {"path": str(PRE), "sha256": sha256_path(PRE)},
        },
    }
    write_json(SUMMARY, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--attempts", type=int, default=4)
    args = parser.parse_args()
    return run(workers=args.workers, timeout=args.timeout, attempts=args.attempts)


if __name__ == "__main__":
    raise SystemExit(main())

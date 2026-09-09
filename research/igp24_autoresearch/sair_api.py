#!/usr/bin/env python3
"""Narrow, auditable SAIR IGP24 API client for Team Dirac autoresearch.

The client only talks to the fixed public IGP24 API base. It never prints or
copies the bearer token. Network-facing commands are intentionally constrained
to competition status, target snapshots, submission synchronization, and
validated polynomial submission.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import csv
import errno
import hashlib
import json
import math
import os
import socket
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


API_BASE = "https://api.sair.foundation/api/public/v1/competitions/igp24"
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
BASELINE_CSV = Path("/path/to/private-file")
READ_RETRY_DELAYS = (0.25, 0.5, 1.0)
READ_METHODS = frozenset({"GET", "HEAD"})
TRANSIENT_CONNECTION_ERRNOS = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
    }
)


def load_token() -> str:
    token = os.environ.get("SAIR_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Set SAIR_API_KEY explicitly for authenticated API operations")
    return token


def transient_read_error(exc: BaseException) -> bool:
    """Return whether a failed read is safe and useful to retry."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return True
    if isinstance(reason, (TimeoutError, ConnectionError)):
        return True
    return (
        isinstance(reason, OSError)
        and reason.errno in TRANSIENT_CONNECTION_ERRNOS
    )


def api_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 90,
) -> tuple[int, dict[str, str], bytes]:
    if path != "" and (not path.startswith("/") or path.startswith("//")):
        raise ValueError("API paths must be relative to the fixed IGP24 base")
    url = API_BASE + path
    headers = {
        "Authorization": f"Bearer {load_token()}",
        "Accept": "application/json, text/plain;q=0.9",
        "User-Agent": "team-dirac-igp24-autoresearch/1.0",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    normalized_method = method.upper()
    request = urllib.request.Request(
        url, data=body, headers=headers, method=normalized_method
    )
    retry_delays = READ_RETRY_DELAYS if normalized_method in READ_METHODS else ()
    for attempt in range(len(retry_delays) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            finally:
                exc.close()
            raise RuntimeError(f"SAIR HTTP {exc.code}: {error_body[:2000]}") from exc
        except OSError as exc:
            if attempt >= len(retry_delays) or not transient_read_error(exc):
                raise
            time.sleep(retry_delays[attempt])
    raise AssertionError("unreachable API retry state")


def api_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    _status, _headers, raw = api_request(method, path, payload=payload, timeout=timeout)
    parsed = json.loads(raw.decode("utf-8"))
    if not parsed.get("ok", False):
        raise RuntimeError(f"SAIR returned a non-ok response: {parsed}")
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("SAIR response has no data object")
    return data


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            submission_id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            queued_count INTEGER NOT NULL DEFAULT 0,
            verified_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            raw_json TEXT NOT NULL,
            synced_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS polynomials (
            submission_id TEXT NOT NULL,
            polynomial_index INTEGER NOT NULL,
            original_line TEXT NOT NULL,
            coefficients TEXT NOT NULL,
            coefficient_hash TEXT NOT NULL,
            PRIMARY KEY (submission_id, polynomial_index),
            FOREIGN KEY (submission_id) REFERENCES submissions(submission_id)
        );
        CREATE INDEX IF NOT EXISTS idx_polynomials_hash
            ON polynomials(coefficient_hash);

        CREATE TABLE IF NOT EXISTS verifications (
            submission_id TEXT NOT NULL,
            polynomial_index INTEGER NOT NULL,
            status TEXT,
            label TEXT,
            t INTEGER,
            r INTEGER,
            scoring_status TEXT,
            scoreable INTEGER,
            in_baseline INTEGER,
            baseline_unlocked INTEGER,
            disc_source TEXT,
            field_disc_abs TEXT,
            poly_disc_abs TEXT,
            mixed_disc_abs TEXT,
            scoring_disc_abs TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (submission_id, polynomial_index),
            FOREIGN KEY (submission_id) REFERENCES submissions(submission_id)
        );
        CREATE INDEX IF NOT EXISTS idx_verifications_pair
            ON verifications(label, r);

        CREATE TABLE IF NOT EXISTS failures (
            submission_id TEXT NOT NULL,
            polynomial_index INTEGER NOT NULL,
            reason TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (submission_id, polynomial_index),
            FOREIGN KEY (submission_id) REFERENCES submissions(submission_id)
        );

        CREATE TABLE IF NOT EXISTS targets (
            label TEXT NOT NULL,
            t INTEGER NOT NULL,
            r INTEGER NOT NULL,
            team_count INTEGER NOT NULL,
            minimum_disc_abs TEXT,
            discovered INTEGER NOT NULL,
            generated_at TEXT,
            PRIMARY KEY (label, r)
        );

        CREATE TABLE IF NOT EXISTS baseline_pairs (
            label TEXT NOT NULL,
            r INTEGER NOT NULL,
            best_nfdisc_abs TEXT NOT NULL,
            source_rows INTEGER NOT NULL,
            PRIMARY KEY (label, r)
        );

        CREATE TABLE IF NOT EXISTS submission_receipts (
            submission_id TEXT PRIMARY KEY,
            manifest_path TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            description TEXT,
            submitted_at REAL NOT NULL,
            raw_json TEXT NOT NULL
        );
        """
    )
    return conn


def coefficient_part(line: str) -> str:
    return line.split("#", 1)[0].strip()


def canonical_coefficients(line: str) -> tuple[str, list[int]]:
    part = coefficient_part(line)
    fields = [field.strip() for field in part.split(",")]
    if len(fields) != 25:
        raise ValueError(f"expected 25 coefficients, found {len(fields)}")
    try:
        values = [int(field) for field in fields]
    except ValueError as exc:
        raise ValueError("all coefficients must be decimal integers") from exc
    return ",".join(str(value) for value in values), values


def coefficient_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def parse_download(text: str) -> list[tuple[str, str, str]]:
    parsed: list[tuple[str, str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        canonical, _values = canonical_coefficients(line)
        parsed.append((line, canonical, coefficient_hash(canonical)))
    return parsed


def download_submission(submission_id: str) -> str:
    _status, _headers, raw = api_request(
        "GET", f"/submissions/{submission_id}/download", timeout=120
    )
    return raw.decode("utf-8")


def _as_int_bool(value: Any) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def store_submission(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    downloaded_text: str | None,
) -> dict[str, Any]:
    submission_id = str(data["submissionId"])
    verified = data.get("verifiedPolynomials") or []
    failed = data.get("failedPolynomials") or []
    queued = (data.get("payload") or {}).get("queuedPolynomials") or []
    description = (data.get("meta") or {}).get("description")
    now = time.time()

    conn.execute(
        """
        INSERT INTO submissions (
            submission_id, created_at, updated_at, queued_count,
            verified_count, failed_count, description, raw_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(submission_id) DO UPDATE SET
            created_at=excluded.created_at,
            updated_at=excluded.updated_at,
            queued_count=excluded.queued_count,
            verified_count=excluded.verified_count,
            failed_count=excluded.failed_count,
            description=excluded.description,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            submission_id,
            data.get("createdAt"),
            data.get("updatedAt"),
            len(queued),
            len(verified),
            len(failed),
            description,
            json.dumps(data, separators=(",", ":"), sort_keys=True),
            now,
        ),
    )

    if downloaded_text is not None:
        lines = parse_download(downloaded_text)
        conn.execute("DELETE FROM polynomials WHERE submission_id=?", (submission_id,))
        conn.executemany(
            """
            INSERT INTO polynomials (
                submission_id, polynomial_index, original_line,
                coefficients, coefficient_hash
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (submission_id, index, original, canonical, digest)
                for index, (original, canonical, digest) in enumerate(lines)
            ],
        )

    conn.execute("DELETE FROM verifications WHERE submission_id=?", (submission_id,))
    for row in verified:
        conn.execute(
            """
            INSERT INTO verifications (
                submission_id, polynomial_index, status, label, t, r,
                scoring_status, scoreable, in_baseline, baseline_unlocked,
                disc_source, field_disc_abs, poly_disc_abs, mixed_disc_abs,
                scoring_disc_abs, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                row.get("polynomialIndex"),
                row.get("status"),
                row.get("label"),
                row.get("t"),
                row.get("r"),
                row.get("scoringStatus"),
                _as_int_bool(row.get("scoreable")),
                _as_int_bool(row.get("inBaseline")),
                _as_int_bool(row.get("baselineUnlocked")),
                row.get("discSource"),
                row.get("fieldDiscAbs"),
                row.get("polyDiscAbs"),
                row.get("mixedDiscAbs"),
                row.get("scoringDiscAbs"),
                json.dumps(row, separators=(",", ":"), sort_keys=True),
            ),
        )

    conn.execute("DELETE FROM failures WHERE submission_id=?", (submission_id,))
    for position, row in enumerate(failed):
        index = row.get("polynomialIndex", position)
        conn.execute(
            """
            INSERT INTO failures (
                submission_id, polynomial_index, reason, raw_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                submission_id,
                index,
                row.get("reason"),
                json.dumps(row, separators=(",", ":"), sort_keys=True),
            ),
        )
    conn.commit()

    pairs = Counter((row.get("label"), row.get("r")) for row in verified)
    return {
        "submissionId": submission_id,
        "createdAt": data.get("createdAt"),
        "queued": len(queued),
        "verified": len(verified),
        "failed": len(failed),
        "distinctPairs": len(pairs),
        "duplicateRows": max(0, len(verified) - len(pairs)),
        "downloadedLines": len(parse_download(downloaded_text))
        if downloaded_text is not None
        else None,
    }


def sync_one(submission_id: str) -> dict[str, Any]:
    data = api_json("GET", f"/submissions/{submission_id}", timeout=180)
    text = download_submission(submission_id)
    with connect_db() as conn:
        return store_submission(conn, data, text)


def fetch_submission_bundle(submission_id: str) -> tuple[dict[str, Any], str]:
    """Fetch one submission and its coefficients without writing the ledger."""
    data = api_json("GET", f"/submissions/{submission_id}", timeout=180)
    text = download_submission(submission_id)
    return data, text


def command_check(_args: argparse.Namespace) -> None:
    competition = api_json("GET", "")
    me = api_json("GET", "/me")
    spec = competition.get("submissionSpec") or {}
    result = {
        "team": me.get("team"),
        "enrolled": me.get("enrolled"),
        "canSubmit": me.get("canSubmit"),
        "submitBlockedReason": me.get("submitBlockedReason"),
        "window": spec.get("window"),
        "limits": spec.get("limits"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


def command_recent(args: argparse.Namespace) -> None:
    limit = max(1, min(args.limit, 100))
    data = api_json("GET", f"/submissions/me?limit={limit}", timeout=180)
    summaries = []
    for item in data.get("items") or []:
        verified = item.get("verifiedPolynomials") or []
        pairs = Counter((row.get("label"), row.get("r")) for row in verified)
        summaries.append(
            {
                "submissionId": item.get("submissionId"),
                "createdAt": item.get("createdAt"),
                "queued": len((item.get("payload") or {}).get("queuedPolynomials") or []),
                "verified": len(verified),
                "failed": len(item.get("failedPolynomials") or []),
                "distinctPairs": len(pairs),
                "duplicateRows": max(0, len(verified) - len(pairs)),
            }
        )
    print(json.dumps({"items": summaries, "nextCursor": data.get("nextCursor")}, indent=2))


def command_sync(args: argparse.Namespace) -> None:
    print(json.dumps(sync_one(args.submission_id), indent=2, sort_keys=True))


def command_sync_history(args: argparse.Namespace) -> None:
    """Ingest Team Dirac's submission history into the reproducible ledger."""
    if getattr(args, "bounded_page20", False):
        import agent_page20_history_ingest

        agent_page20_history_ingest.run(
            workers=args.workers,
            timeout=args.request_timeout,
            attempts=args.attempts,
        )
        return
    if args.page_size < 1 or args.page_size > 100:
        raise ValueError("--page-size must be between 1 and 100")
    if args.workers < 1 or args.workers > 16:
        raise ValueError("--workers must be between 1 and 16")
    cursor: str | None = None
    pages = 0
    seen = 0
    synced = 0
    skipped = 0
    failures = []
    while True:
        query: dict[str, str | int] = {"limit": args.page_size}
        if cursor:
            query["cursor"] = cursor
        data = api_json(
            "GET", "/submissions/me?" + urllib.parse.urlencode(query), timeout=180
        )
        pages += 1
        items = data.get("items") or []
        pending: list[str] = []
        for item in items:
            submission_id = str(item.get("submissionId") or "")
            if not submission_id:
                continue
            seen += 1
            with connect_db() as conn:
                known = conn.execute(
                    "SELECT 1 FROM submissions WHERE submission_id=?", (submission_id,)
                ).fetchone()
            if known and not args.refresh_known:
                skipped += 1
                continue
            pending.append(submission_id)
        if pending:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(args.workers, len(pending))
            ) as executor:
                futures = {
                    executor.submit(fetch_submission_bundle, submission_id): submission_id
                    for submission_id in pending
                }
                for future in concurrent.futures.as_completed(futures):
                    submission_id = futures[future]
                    try:
                        detail, downloaded_text = future.result()
                        with connect_db() as conn:
                            summary = store_submission(conn, detail, downloaded_text)
                    except (
                        RuntimeError,
                        ValueError,
                        OSError,
                        sqlite3.Error,
                        json.JSONDecodeError,
                    ) as exc:
                        failures.append(
                            {"submissionId": submission_id, "error": str(exc)}
                        )
                        print(
                            f"history sync failed for {submission_id}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    synced += 1
                    print(
                        f"history page={pages} seen={seen} synced={synced} "
                        f"{submission_id} verified={summary['verified']}",
                        file=sys.stderr,
                        flush=True,
                    )
        if args.since and any(
            str(item.get("createdAt") or "") < args.since for item in items
        ):
            break
        cursor = data.get("nextCursor")
        if not cursor:
            break
        if pages >= args.max_pages:
            raise RuntimeError(
                f"history exceeds --max-pages={args.max_pages}; increase the explicit limit"
            )
    print(
        json.dumps(
            {
                "pages": pages,
                "seen": seen,
                "synced": synced,
                "skippedKnown": skipped,
                "failures": failures,
                "database": str(DB_PATH),
            },
            indent=2,
            sort_keys=True,
        )
    )


def command_sync_history_page20(args: argparse.Namespace) -> None:
    """Run the checkpointed, exactly-one-page historical recovery lane."""
    import agent_page20_history_ingest

    agent_page20_history_ingest.run(
        workers=args.workers,
        timeout=args.timeout,
        attempts=args.attempts,
    )


def command_targets(_args: argparse.Namespace) -> None:
    cursor: str | None = None
    total_labels = 0
    total_pairs = 0
    target_rows: list[tuple[Any, ...]] = []
    while True:
        query: dict[str, str | int] = {"limit": 5000, "includeEmpty": "true"}
        if cursor:
            query["cursor"] = cursor
        path = "/labels/progress?" + urllib.parse.urlencode(query)
        data = api_json("GET", path, timeout=180)
        generated_at = data.get("generatedAt")
        labels = data.get("labels") or []
        for label_row in labels:
            label = label_row.get("label")
            t = label_row.get("t")
            for signature in label_row.get("signatures") or []:
                target_rows.append(
                    (
                        label,
                        t,
                        signature.get("r"),
                        signature.get("teamCount", 0),
                        signature.get("minimumDiscAbs"),
                        int(bool(signature.get("discovered"))),
                        generated_at,
                    )
                )
                total_pairs += 1
        total_labels += len(labels)
        cursor = data.get("nextCursor")
        if not cursor:
            break

    conn = connect_db()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM targets")
            conn.executemany(
                """
                INSERT OR REPLACE INTO targets (
                    label, t, r, team_count, minimum_disc_abs,
                    discovered, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                target_rows,
            )
    finally:
        conn.close()
    print(
        json.dumps(
            {"labels": total_labels, "pairs": total_pairs, "database": str(DB_PATH)},
            indent=2,
        )
    )


def command_inspect_targets(args: argparse.Namespace) -> None:
    """Print the complete live progress DTO for a small explicit label list."""
    labels = []
    for raw in args.labels:
        label = raw.strip()
        if not label.startswith("24T") or not label[3:].isdigit():
            raise ValueError(f"invalid degree-24 label: {raw}")
        labels.append(label)
    if not labels or len(labels) > 20:
        raise ValueError("inspect-targets requires between 1 and 20 labels")
    output = []
    for label in labels:
        query = urllib.parse.urlencode(
            {"label": label, "limit": 1, "includeEmpty": "true"}
        )
        data = api_json("GET", f"/labels/progress?{query}", timeout=90)
        output.extend(data.get("labels") or [])
    print(json.dumps({"labels": output}, indent=2, sort_keys=True))


def command_owned_pairs(args: argparse.Namespace) -> None:
    """Search Team Dirac's submission history for a small explicit pair set."""
    wanted: set[tuple[str, int]] = set()
    for raw in args.pairs:
        try:
            label, raw_r = raw.split(":", 1)
            r = int(raw_r)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid pair {raw!r}; expected 24T<number>:<r>") from exc
        if not label.startswith("24T") or not label[3:].isdigit() or r < 0 or r > 24:
            raise ValueError(f"invalid pair: {raw}")
        wanted.add((label, r))
    if not wanted or len(wanted) > 50:
        raise ValueError("owned-pairs requires between 1 and 50 explicit pairs")

    cursor: str | None = None
    pages = 0
    submissions = 0
    matches: dict[tuple[str, int], list[dict[str, Any]]] = {
        pair: [] for pair in wanted
    }
    while True:
        query: dict[str, str | int] = {"limit": args.page_size}
        if cursor:
            query["cursor"] = cursor
        data = api_json(
            "GET", "/submissions/me?" + urllib.parse.urlencode(query), timeout=180
        )
        pages += 1
        items = data.get("items") or []
        submissions += len(items)
        for item in items:
            for row in item.get("verifiedPolynomials") or []:
                pair = (str(row.get("label")), int(row.get("r")))
                if pair in matches:
                    matches[pair].append(
                        {
                            "submissionId": item.get("submissionId"),
                            "createdAt": item.get("createdAt"),
                            "polynomialIndex": row.get("polynomialIndex"),
                            "scoreable": row.get("scoreable"),
                            "scoringStatus": row.get("scoringStatus"),
                        }
                    )
        if args.since and any(
            str(item.get("createdAt") or "") < args.since for item in items
        ):
            break
        cursor = data.get("nextCursor")
        if not cursor:
            break
        if pages >= args.max_pages:
            raise RuntimeError(
                f"submission history exceeds --max-pages={args.max_pages}; refusing partial ownership result"
            )
    output = [
        {
            "label": label,
            "r": r,
            "owned": bool(matches[(label, r)]),
            "matches": matches[(label, r)],
        }
        for label, r in sorted(wanted)
    ]
    print(
        json.dumps(
            {"pages": pages, "submissions": submissions, "pairs": output},
            indent=2,
            sort_keys=True,
        )
    )


def command_leaderboard(args: argparse.Namespace) -> None:
    mine = api_json("GET", "/leaderboard/me", timeout=90)
    if args.top:
        board = api_json("GET", "/leaderboard", timeout=90)
        entries = (
            board.get("items")
            or board.get("entries")
            or board.get("leaderboard")
            or []
        )
        print(
            json.dumps(
                {
                    "mine": mine.get("entry", mine),
                    "generatedAt": board.get("generatedAt"),
                    "top": entries[: args.top],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(mine, indent=2, sort_keys=True))
    crawl_pages = int(args.rank10_crawl_pages)
    if not 0 <= crawl_pages <= 20:
        raise ValueError("rank10-crawl-pages must be between 0 and 20")
    for _page in range(crawl_pages):
        command_rank10_crawl_step(
            argparse.Namespace(checkpoint="", output="", max_pages=20)
        )
        checkpoint = RANK10_CRAWL_CHECKPOINT
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("complete") is True:
                break
    prefix_pages = int(args.rank10_prefix_crawl_pages)
    if not 0 <= prefix_pages <= 50:
        raise ValueError("rank10-prefix-crawl-pages must be between 0 and 50")
    for _page in range(prefix_pages):
        prefix_checkpoint_arg = str(args.rank10_prefix_checkpoint)
        prefix_output_arg = str(args.rank10_prefix_output)
        command_rank10_prefix_step(
            argparse.Namespace(
                checkpoint=prefix_checkpoint_arg,
                output=prefix_output_arg,
                page_limit=int(args.rank10_prefix_page_limit),
            )
        )
        checkpoint = _rank10_generated_path(
            prefix_checkpoint_arg,
            RANK10_PREFIX_CHECKPOINT,
            "prefix checkpoint",
        )
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("complete") is True:
                break


def command_rank11_placements(args: argparse.Namespace) -> None:
    """Refresh the pinned rank-11 placement cache through the fixed API client."""

    import authenticated_placement_crawler as crawler

    result = crawler.run_crawl(
        scope=args.scope,
        page_size=args.page_size,
        max_pages=args.max_pages,
        delay=args.delay,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


RANK10_TEAM_ID = "teamv2_32d618912a0e471fb418e886de946622"
RANK10_TEAM_NUMBER = "IGP24-T00134"
RANK10_TEAM_NAME = ""
RANK10_CRAWL_CHECKPOINT = (
    ROOT / "data" / "rank10_t00134_20260730_unique_checkpoint.json"
)
RANK10_CRAWL_OUTPUT = (
    ROOT
    / "data"
    / "rank10_t00134_authenticated_nested_20260730_unique_placements.jsonl"
)
RANK10_PREFIX_CHECKPOINT = (
    ROOT / "data" / "rank10_t00134_top5000_checkpoint_20260730.json"
)
RANK10_PREFIX_OUTPUT = (
    ROOT / "data" / "rank10_t00134_top5000_placements_20260730.jsonl"
)


def _atomic_json_replace(path: Path, value: dict[str, Any]) -> None:
    """Durably replace one generated JSON checkpoint in its own directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _rank10_generated_path(raw: str, default: Path, description: str) -> Path:
    path = Path(raw).expanduser().resolve() if raw else default.resolve()
    data_root = (ROOT / "data").resolve()
    if not path.is_relative_to(data_root):
        raise ValueError(f"{description} must stay inside {data_root}")
    return path


def command_rank10_crawl_step(args: argparse.Namespace) -> None:
    """Fetch and checkpoint exactly one rank-10 unique-placement page."""

    import authenticated_placement_crawler as crawler

    # The shared validator/persistence module deliberately pins its identity in
    # module globals.  Set all three before validating or persisting so a crawl
    # cannot inherit the fallback opponent's metadata.
    crawler.TEAM_ID = RANK10_TEAM_ID
    crawler.TEAM_NUMBER = RANK10_TEAM_NUMBER
    crawler.TEAM_NAME = RANK10_TEAM_NAME

    checkpoint = _rank10_generated_path(
        args.checkpoint, RANK10_CRAWL_CHECKPOINT, "checkpoint"
    )
    output = _rank10_generated_path(args.output, RANK10_CRAWL_OUTPUT, "output")
    if checkpoint == output:
        raise ValueError("checkpoint and output paths must differ")
    if not 1 <= int(args.max_pages) <= 100:
        raise ValueError("max-pages must be between 1 and 100")

    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("rank-10 crawl checkpoint is not an object")
    else:
        state = {
            "schemaVersion": "rank10-unique-placement-checkpoint-v1",
            "teamId": RANK10_TEAM_ID,
            "teamNumber": RANK10_TEAM_NUMBER,
            "teamName": RANK10_TEAM_NAME,
            "scope": "unique",
            "startedAt": crawler.utc_now(),
            "pages": 0,
            "cursor": None,
            "seenCursors": [],
            "rows": [],
            "complete": False,
        }

    if (
        state.get("schemaVersion")
        != "rank10-unique-placement-checkpoint-v1"
        or state.get("teamId") != RANK10_TEAM_ID
        or state.get("teamNumber") != RANK10_TEAM_NUMBER
        or state.get("teamName") != RANK10_TEAM_NAME
        or state.get("scope") != "unique"
        or not isinstance(state.get("rows"), list)
        or not isinstance(state.get("seenCursors"), list)
        or isinstance(state.get("pages"), bool)
        or not isinstance(state.get("pages"), int)
    ):
        raise ValueError("rank-10 crawl checkpoint identity or schema is invalid")

    if state.get("complete") is True:
        print(
            json.dumps(
                {
                    "status": "complete",
                    "crawlId": state.get("crawlId"),
                    "pages": state["pages"],
                    "uniqueHeld": len(state["rows"]),
                    "checkpoint": str(checkpoint),
                    "jsonl": str(output),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if state["pages"] >= int(args.max_pages):
        raise ValueError("rank-10 unique crawl exceeded the configured page bound")

    cursor = state.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise ValueError("rank-10 crawl checkpoint cursor is invalid")
    query: dict[str, str | int] = {"limit": 100}
    if cursor is not None:
        query["cursor"] = cursor
    endpoint = (
        "/leaderboard/teams/"
        + urllib.parse.quote(RANK10_TEAM_ID, safe="")
        + "/placements?"
        + urllib.parse.urlencode(query)
    )
    data = api_json("GET", endpoint, timeout=12)
    if data.get("teamId") != RANK10_TEAM_ID:
        raise RuntimeError("rank-10 placements response changed the pinned team ID")
    raw_rows = data.get("placements")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError("rank-10 placements response has no placement rows")

    retained = [crawler.validate_placement(row) for row in state["rows"]]
    seen_pairs = {(row["label"], row["r"]) for row in retained}
    previous_points = retained[-1]["points"] if retained else math.inf
    hit_non_unique = False
    for raw in raw_rows:
        row = crawler.validate_placement(raw)
        if row["points"] > previous_points + 1e-12:
            raise RuntimeError("rank-10 placements are not sorted by descending points")
        previous_points = row["points"]
        pair = (row["label"], row["r"])
        if pair in seen_pairs:
            raise RuntimeError(f"duplicate rank-10 placement across pages: {pair}")
        seen_pairs.add(pair)
        if row["kTeams"] == 1:
            if hit_non_unique:
                raise RuntimeError("unique rank-10 placement followed a shared placement")
            if abs(row["points"] - 1.0) > 1e-12:
                raise RuntimeError(f"unexpected non-unit unique score for {pair}")
            retained.append(row)
        else:
            hit_non_unique = True

    state["rows"] = retained
    state["pages"] += 1
    next_cursor = data.get("nextCursor")
    if next_cursor in ("",):
        raise RuntimeError("rank-10 placements returned an empty cursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise RuntimeError("rank-10 placements returned a malformed cursor")

    done = hit_non_unique or next_cursor is None
    if not done:
        if next_cursor in state["seenCursors"]:
            raise RuntimeError("rank-10 placements repeated a cursor")
        state["seenCursors"].append(next_cursor)
        state["cursor"] = next_cursor
        _atomic_json_replace(checkpoint, state)
        print(
            json.dumps(
                {
                    "status": "checkpointed",
                    "pages": state["pages"],
                    "uniqueHeld": len(retained),
                    "hasNextPage": True,
                    "checkpoint": str(checkpoint),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    completed_at = crawler.utc_now()
    crawl_id = crawler.persist_crawl(
        db_path=crawler.DB_PATH,
        output_path=output,
        scope="unique",
        leaderboard_generated_at=None,
        started_at=str(state["startedAt"]),
        completed_at=completed_at,
        pages=int(state["pages"]),
        rows=retained,
        unique_rows=len(retained),
    )
    state.update(
        {
            "cursor": None,
            "complete": True,
            "completedAt": completed_at,
            "crawlId": crawl_id,
        }
    )
    _atomic_json_replace(checkpoint, state)
    print(
        json.dumps(
            {
                "status": "complete",
                "crawlId": crawl_id,
                "pages": state["pages"],
                "uniqueHeld": len(retained),
                "checkpoint": str(checkpoint),
                "jsonl": str(output),
            },
            indent=2,
            sort_keys=True,
        )
    )


def command_rank10_prefix_step(args: argparse.Namespace) -> None:
    """Fetch one page of a pinned, bounded high-value placement prefix."""

    import authenticated_placement_crawler as crawler

    crawler.TEAM_ID = RANK10_TEAM_ID
    crawler.TEAM_NUMBER = RANK10_TEAM_NUMBER
    crawler.TEAM_NAME = RANK10_TEAM_NAME
    checkpoint = _rank10_generated_path(
        args.checkpoint, RANK10_PREFIX_CHECKPOINT, "prefix checkpoint"
    )
    output = _rank10_generated_path(
        args.output, RANK10_PREFIX_OUTPUT, "prefix output"
    )
    page_limit = int(args.page_limit)
    if checkpoint == output:
        raise ValueError("prefix checkpoint and output paths must differ")
    if not 1 <= page_limit <= 1000:
        raise ValueError("page-limit must be between 1 and 1000")

    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("rank-10 prefix checkpoint is not an object")
    else:
        state = {
            "schemaVersion": "rank10-placement-prefix-checkpoint-v1",
            "teamId": RANK10_TEAM_ID,
            "teamNumber": RANK10_TEAM_NUMBER,
            "teamName": RANK10_TEAM_NAME,
            "startedAt": crawler.utc_now(),
            "pageLimit": page_limit,
            "pages": 0,
            "cursor": None,
            "seenCursors": [],
            "rows": [],
            "complete": False,
        }
    if (
        state.get("schemaVersion")
        != "rank10-placement-prefix-checkpoint-v1"
        or state.get("teamId") != RANK10_TEAM_ID
        or state.get("teamNumber") != RANK10_TEAM_NUMBER
        or state.get("teamName") != RANK10_TEAM_NAME
        or state.get("pageLimit") != page_limit
        or not isinstance(state.get("rows"), list)
        or not isinstance(state.get("seenCursors"), list)
        or isinstance(state.get("pages"), bool)
        or not isinstance(state.get("pages"), int)
        or not 0 <= state["pages"] <= page_limit
    ):
        raise ValueError("rank-10 prefix checkpoint identity or schema is invalid")
    if state.get("complete") is True:
        if not output.is_file():
            raise ValueError("completed rank-10 prefix checkpoint has no output")
        print(
            json.dumps(
                {
                    "status": "complete",
                    "pages": state["pages"],
                    "placements": len(state["rows"]),
                    "checkpoint": str(checkpoint),
                    "jsonl": str(output),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if state["pages"] >= page_limit:
        raise ValueError("incomplete rank-10 prefix reached its page bound")

    retained = [crawler.validate_placement(row) for row in state["rows"]]
    seen_pairs = {(row["label"], row["r"]) for row in retained}
    if len(seen_pairs) != len(retained):
        raise ValueError("rank-10 prefix checkpoint contains duplicate pairs")
    previous_points = retained[-1]["points"] if retained else math.inf
    cursor = state.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise ValueError("rank-10 prefix checkpoint cursor is invalid")
    query: dict[str, str | int] = {"limit": 100}
    if cursor is not None:
        query["cursor"] = cursor
    endpoint = (
        "/leaderboard/teams/"
        + urllib.parse.quote(RANK10_TEAM_ID, safe="")
        + "/placements?"
        + urllib.parse.urlencode(query)
    )
    data = api_json("GET", endpoint, timeout=12)
    if data.get("teamId") != RANK10_TEAM_ID:
        raise RuntimeError("rank-10 prefix response changed the pinned team ID")
    raw_rows = data.get("placements")
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= 100:
        raise RuntimeError("rank-10 prefix response has an invalid placement page")
    for raw in raw_rows:
        row = crawler.validate_placement(raw)
        if row["points"] > previous_points + 1e-12:
            raise RuntimeError("rank-10 prefix is not sorted by descending points")
        previous_points = row["points"]
        pair = (row["label"], row["r"])
        if pair in seen_pairs:
            raise RuntimeError(f"duplicate rank-10 prefix placement: {pair}")
        seen_pairs.add(pair)
        retained.append(row)

    state["rows"] = retained
    state["pages"] += 1
    next_cursor = data.get("nextCursor")
    if next_cursor in ("",):
        raise RuntimeError("rank-10 prefix returned an empty cursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise RuntimeError("rank-10 prefix returned a malformed cursor")
    done = next_cursor is None or state["pages"] >= page_limit
    if not done:
        if next_cursor in state["seenCursors"]:
            raise RuntimeError("rank-10 prefix repeated a cursor")
        state["seenCursors"].append(next_cursor)
        state["cursor"] = next_cursor
        _atomic_json_replace(checkpoint, state)
        print(
            json.dumps(
                {
                    "status": "checkpointed",
                    "pages": state["pages"],
                    "placements": len(retained),
                    "lastPoints": previous_points,
                    "checkpoint": str(checkpoint),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    completed_at = crawler.utc_now()
    staged = crawler._stage_jsonl(output, retained, fetched_at=completed_at)
    os.replace(staged, output)
    state.update(
        {
            "cursor": None,
            "complete": True,
            "completedAt": completed_at,
            "lastPoints": previous_points,
        }
    )
    _atomic_json_replace(checkpoint, state)
    print(
        json.dumps(
            {
                "status": "complete",
                "pages": state["pages"],
                "placements": len(retained),
                "lastPoints": previous_points,
                "checkpoint": str(checkpoint),
                "jsonl": str(output),
            },
            indent=2,
            sort_keys=True,
        )
    )


def command_baseline(_args: argparse.Namespace) -> None:
    if not BASELINE_CSV.exists():
        raise OSError(f"baseline CSV is missing: {BASELINE_CSV}")
    best: dict[tuple[str, int], int] = {}
    counts: Counter[tuple[str, int]] = Counter()
    with BASELINE_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row["label"], int(row["r"]))
            value = int(row["nfdisc_abs"])
            counts[key] += 1
            if key not in best or value < best[key]:
                best[key] = value
    with connect_db() as conn:
        conn.execute("DELETE FROM baseline_pairs")
        conn.executemany(
            """
            INSERT INTO baseline_pairs(label,r,best_nfdisc_abs,source_rows)
            VALUES (?, ?, ?, ?)
            """,
            [
                (label, r, str(value), counts[(label, r)])
                for (label, r), value in sorted(best.items())
            ],
        )
        conn.commit()
    print(
        json.dumps(
            {
                "baselineRows": sum(counts.values()),
                "baselinePairs": len(best),
                "source": str(BASELINE_CSV),
            },
            indent=2,
        )
    )


def validated_manifest(path: Path) -> tuple[list[str], list[str]]:
    resolved = path.expanduser().resolve()
    outbox_root = OUTBOX.resolve()
    if not resolved.is_relative_to(outbox_root):
        raise ValueError(f"submission manifests must be inside {outbox_root}")
    raw_lines = resolved.read_text(encoding="utf-8").splitlines()
    lines: list[str] = []
    hashes: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        canonical, values = canonical_coefficients(line)
        if values[0] == 0:
            raise ValueError(f"line {line_number}: constant coefficient is zero")
        if values[-1] != 1:
            raise ValueError(f"line {line_number}: leading coefficient is not 1")
        if math.gcd(*values) != 1:
            raise ValueError(f"line {line_number}: coefficient gcd is not 1")
        digest = coefficient_hash(canonical)
        if digest in seen:
            raise ValueError(f"line {line_number}: duplicate polynomial in manifest")
        seen.add(digest)
        lines.append(line)
        hashes.append(digest)
    if not lines:
        raise ValueError("manifest has no polynomial lines")
    if len(lines) > 1000:
        raise ValueError("manifest exceeds the 1000-polynomial limit")
    return lines, hashes


def command_submit(args: argparse.Namespace) -> None:
    manifest = Path(args.file).expanduser().resolve()
    lines, hashes = validated_manifest(manifest)
    competition = api_json("GET", "")
    me = api_json("GET", "/me")
    if me.get("enrolled") is not True:
        raise RuntimeError("team is not currently enrolled in IGP24")
    if me.get("canSubmit") is not True:
        reason = me.get("submitBlockedReason") or "unspecified live block"
        raise RuntimeError(f"team cannot currently submit: {reason}")
    limits = ((competition.get("submissionSpec") or {}).get("limits") or {})
    max_polynomials = int(limits.get("maxPolynomials", 1000))
    max_bytes = int(limits.get("maxBytes", 1_000_000))
    encoded_bytes = len(("\n".join(lines) + "\n").encode("utf-8"))
    if len(lines) > max_polynomials:
        raise ValueError(f"manifest has {len(lines)} lines; live limit is {max_polynomials}")
    if encoded_bytes > max_bytes:
        raise ValueError(f"manifest is {encoded_bytes} bytes; live limit is {max_bytes}")

    with connect_db() as conn:
        placeholders = ",".join("?" for _ in hashes)
        known = set()
        if hashes:
            known = {
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT coefficient_hash FROM polynomials "
                    f"WHERE coefficient_hash IN ({placeholders})",
                    hashes,
                )
            }
    if known and not args.allow_known:
        raise ValueError(
            f"manifest contains {len(known)} polynomial(s) already present in the local ledger"
        )

    manifest_hash = hashlib.sha256(
        ("\n".join(lines) + "\n").encode("utf-8")
    ).hexdigest()
    dry_run = {
        "manifest": str(manifest),
        "manifestHash": manifest_hash,
        "polynomials": len(lines),
        "bytes": encoded_bytes,
        "knownLocalHashes": len(known),
        "enrolled": True,
        "canSubmit": True,
        "submitBlockedReason": me.get("submitBlockedReason"),
        "commit": bool(args.commit),
    }
    if not args.commit:
        print(json.dumps(dry_run, indent=2, sort_keys=True))
        return

    description = (args.description or "").strip()
    if len(description) > 500:
        raise ValueError("description exceeds the 500-character limit")
    request_payload: dict[str, Any] = {"payload": {"polynomials": lines}}
    if description:
        request_payload["meta"] = {"description": description}
    data = api_json("POST", "/submissions", payload=request_payload, timeout=180)
    submission_id = data.get("submissionId")
    if not submission_id:
        raise RuntimeError(f"submission response has no submissionId: {data}")

    RECEIPTS.mkdir(parents=True, exist_ok=True)
    receipt_path = RECEIPTS / f"{submission_id}.json"
    receipt = {
        **dry_run,
        "description": description,
        "submittedAtUnix": time.time(),
        "response": data,
    }
    temporary = receipt_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(receipt_path)
    with connect_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO submission_receipts (
                submission_id, manifest_path, manifest_hash,
                description, submitted_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                str(manifest),
                manifest_hash,
                description,
                time.time(),
                json.dumps(data, separators=(",", ":"), sort_keys=True),
            ),
        )
        conn.commit()
    print(
        json.dumps(
            {"submissionId": submission_id, "receipt": str(receipt_path), **dry_run},
            indent=2,
            sort_keys=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="read competition and team status")
    check.set_defaults(func=command_check)

    recent = subparsers.add_parser("recent", help="summarize recent submissions")
    recent.add_argument("--limit", type=int, default=20)
    recent.set_defaults(func=command_recent)

    sync = subparsers.add_parser("sync", help="sync one submission and its source lines")
    sync.add_argument("submission_id")
    sync.set_defaults(func=command_sync)

    sync_history = subparsers.add_parser(
        "sync-history", help="ingest our submission history into the local ledger"
    )
    sync_history.add_argument("--page-size", type=int, default=5)
    sync_history.add_argument("--max-pages", type=int, default=1000)
    sync_history.add_argument("--since", default="")
    sync_history.add_argument("--refresh-known", action="store_true")
    sync_history.add_argument(
        "--bounded-page20",
        action="store_true",
        help="use the frozen resumable exactly-page-20 recovery lane",
    )
    sync_history.add_argument("--request-timeout", type=int, default=300)
    sync_history.add_argument("--attempts", type=int, default=4)
    sync_history.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel detail/download fetches; ledger writes stay serialized",
    )
    sync_history.set_defaults(func=command_sync_history)

    page20 = subparsers.add_parser(
        "sync-history-page20",
        help="checkpoint and ingest exactly the next frozen 100-row history page",
    )
    page20.add_argument("--workers", type=int, default=6)
    page20.add_argument("--timeout", type=int, default=300)
    page20.add_argument("--attempts", type=int, default=4)
    page20.set_defaults(func=command_sync_history_page20)

    targets = subparsers.add_parser("targets", help="refresh the complete live target table")
    targets.set_defaults(func=command_targets)

    inspect_targets = subparsers.add_parser(
        "inspect-targets", help="show the live progress DTO for explicit labels"
    )
    inspect_targets.add_argument("labels", nargs="+")
    inspect_targets.set_defaults(func=command_inspect_targets)

    owned_pairs = subparsers.add_parser(
        "owned-pairs", help="search our complete submission history for explicit pairs"
    )
    owned_pairs.add_argument("pairs", nargs="+")
    owned_pairs.add_argument("--max-pages", type=int, default=100)
    owned_pairs.add_argument("--page-size", type=int, default=10)
    owned_pairs.add_argument(
        "--since",
        default="",
        help="stop after processing a page containing an older ISO-8601 creation time",
    )
    owned_pairs.set_defaults(func=command_owned_pairs)

    leaderboard = subparsers.add_parser("leaderboard", help="read Team Dirac's live rank and score")
    leaderboard.add_argument("--top", type=int, default=0)
    leaderboard.add_argument(
        "--rank10-crawl-pages",
        type=int,
        default=0,
        help="also fetch up to this many checkpointed rank-10 placement pages",
    )
    leaderboard.add_argument(
        "--rank10-prefix-crawl-pages",
        type=int,
        default=0,
        help="also fetch up to this many pages of the rank-10 placement prefix",
    )
    leaderboard.add_argument(
        "--rank10-prefix-page-limit",
        type=int,
        default=50,
        help="fixed page bound for the rank-10 placement prefix",
    )
    leaderboard.add_argument(
        "--rank10-prefix-checkpoint",
        default="",
        help="optional generated checkpoint path inside data/",
    )
    leaderboard.add_argument(
        "--rank10-prefix-output",
        default="",
        help="optional generated JSONL path inside data/",
    )
    leaderboard.set_defaults(func=command_leaderboard)

    rank11_placements = subparsers.add_parser(
        "rank11-placements",
        help="refresh the pinned rank-11 placement cache (read-only)",
    )
    rank11_placements.add_argument("--scope", choices=("unique", "all"), default="unique")
    rank11_placements.add_argument("--page-size", type=int, default=100)
    rank11_placements.add_argument("--max-pages", type=int, default=1000)
    rank11_placements.add_argument("--delay", type=float, default=0.1)
    rank11_placements.set_defaults(func=command_rank11_placements)

    rank10_crawl_step = subparsers.add_parser(
        "rank10-crawl-step",
        help="fetch and checkpoint one pinned rank-10 unique-placement page",
    )
    rank10_crawl_step.add_argument("--checkpoint", default="")
    rank10_crawl_step.add_argument("--output", default="")
    rank10_crawl_step.add_argument("--max-pages", type=int, default=20)
    rank10_crawl_step.set_defaults(func=command_rank10_crawl_step)

    rank10_prefix_step = subparsers.add_parser(
        "rank10-prefix-step",
        help="fetch and checkpoint one pinned rank-10 placement-prefix page",
    )
    rank10_prefix_step.add_argument("--checkpoint", default="")
    rank10_prefix_step.add_argument("--output", default="")
    rank10_prefix_step.add_argument("--page-limit", type=int, default=50)
    rank10_prefix_step.set_defaults(func=command_rank10_prefix_step)

    baseline = subparsers.add_parser("baseline", help="ingest the frozen local LMFDB baseline")
    baseline.set_defaults(func=command_baseline)

    submit = subparsers.add_parser("submit", help="validate or submit one outbox manifest")
    submit.add_argument("--file", required=True)
    submit.add_argument("--description", default="")
    submit.add_argument("--commit", action="store_true")
    submit.add_argument("--allow-known", action="store_true")
    submit.set_defaults(func=command_submit)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        args.func(args)
    except (RuntimeError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

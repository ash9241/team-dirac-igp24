#!/usr/bin/env python3
"""Credential-free crawler for public SAIR IGP24 holder evidence.

The narrow default target is the team immediately above Team Dirac at the time
this tool was created: "Low hanging fruit" (IGP24-T00013).  The public
leaderboard is used to validate the internal team ID before any placements are
accepted.  This file contains no submission path and never loads a SAIR token.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PUBLIC_ORIGIN = "https://server-9527.sair.foundation"
TEAM_ID = "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"
TEAM_NUMBER = "IGP24-T00013"
TEAM_NAME = "Low hanging fruit"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "ledger.sqlite3"
USER_AGENT = "team-dirac-igp24-public-holder-audit/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public_json(path: str, *, timeout: int = 30, attempts: int = 5) -> dict[str, Any]:
    """Read one fixed-origin public endpoint with bounded retry/backoff."""
    if not path.startswith("/api/igp24/") or path.startswith("//"):
        raise ValueError("public paths must stay below /api/igp24/")
    url = PUBLIC_ORIGIN + path
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict) or not parsed.get("ok", False):
                raise RuntimeError(f"public SAIR response was not ok: {parsed!r}")
            data = parsed.get("data")
            if not isinstance(data, dict):
                raise RuntimeError("public SAIR response has no data object")
            return data
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt + 1 >= attempts:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"public SAIR HTTP {exc.code}: {detail[:1000]}") from exc
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 >= attempts:
                raise RuntimeError(f"public SAIR request failed: {exc}") from exc
            delay = 2**attempt
        time.sleep(min(delay, 30.0))
    raise AssertionError("retry loop exhausted unexpectedly")


def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS public_team_placement_crawls (
            crawl_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id TEXT NOT NULL,
            team_number TEXT NOT NULL,
            team_name TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('unique', 'all')),
            leaderboard_generated_at TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            pages INTEGER NOT NULL DEFAULT 0,
            placement_rows INTEGER NOT NULL DEFAULT 0,
            unique_rows INTEGER NOT NULL DEFAULT 0,
            jsonl_path TEXT,
            complete INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS public_team_placements (
            crawl_id INTEGER NOT NULL,
            team_id TEXT NOT NULL,
            team_number TEXT NOT NULL,
            team_name TEXT NOT NULL,
            t INTEGER NOT NULL,
            label TEXT NOT NULL,
            r INTEGER NOT NULL,
            points REAL NOT NULL,
            k_teams INTEGER NOT NULL,
            scoring_disc_abs TEXT,
            minimum_disc_abs TEXT,
            disc_source TEXT,
            is_solvable INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (crawl_id, label, r),
            FOREIGN KEY (crawl_id)
                REFERENCES public_team_placement_crawls(crawl_id)
        );
        CREATE INDEX IF NOT EXISTS idx_public_team_placements_pair
            ON public_team_placements(label, r, team_id);
        CREATE INDEX IF NOT EXISTS idx_public_team_placements_team_k
            ON public_team_placements(team_id, k_teams, t, r);

        CREATE TABLE IF NOT EXISTS public_pair_holder_evidence (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL,
            t INTEGER NOT NULL,
            label TEXT NOT NULL,
            r INTEGER NOT NULL,
            team_id TEXT NOT NULL,
            team_number TEXT,
            team_name TEXT,
            points REAL NOT NULL,
            k_teams INTEGER NOT NULL,
            scoring_disc_abs TEXT,
            minimum_disc_abs TEXT,
            disc_source TEXT,
            is_solvable INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_public_pair_holder_evidence_pair
            ON public_pair_holder_evidence(label, r, fetched_at);
        """
    )
    return conn


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"malformed {name}: expected integer, got {value!r}")
    return value


def validate_placement(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("malformed placement: expected object")
    t = _require_int(raw.get("t"), "placement.t")
    r = _require_int(raw.get("r"), "placement.r")
    k_teams = _require_int(raw.get("kTeams"), "placement.kTeams")
    label = raw.get("label")
    points = raw.get("points")
    if not 1 <= t <= 25_000 or label != f"24T{t}":
        raise RuntimeError(f"malformed placement label/t: {label!r}/{t!r}")
    if r not in range(0, 25, 2):
        raise RuntimeError(f"malformed placement r: {r!r}")
    if k_teams < 1:
        raise RuntimeError(f"malformed placement kTeams: {k_teams!r}")
    if isinstance(points, bool) or not isinstance(points, (int, float)):
        raise RuntimeError(f"malformed placement points: {points!r}")
    points = float(points)
    if not math.isfinite(points) or points < 0.0:
        raise RuntimeError(f"malformed placement points: {points!r}")
    if not isinstance(raw.get("isSolvable"), bool):
        raise RuntimeError("malformed placement isSolvable")
    for key in ("scoringDiscAbs", "minScoringDiscAbs"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.isdigit()):
            raise RuntimeError(f"malformed placement {key}: {value!r}")
    return {
        "t": t,
        "label": label,
        "r": r,
        "points": points,
        "kTeams": k_teams,
        "scoringDiscAbs": raw.get("scoringDiscAbs"),
        "minScoringDiscAbs": raw.get("minScoringDiscAbs"),
        "discSource": raw.get("discSource"),
        "isSolvable": raw["isSolvable"],
    }


def resolve_target_team(
    team_id: str, team_number: str, team_name: str
) -> tuple[dict[str, Any], str | None]:
    """Validate an internal ID against the live public leaderboard."""
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _page in range(10):
        query: dict[str, str | int] = {"limit": 100}
        if cursor:
            query["cursor"] = cursor
        data = public_json("/api/igp24/leaderboard?" + urllib.parse.urlencode(query))
        for entry in data.get("items") or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("team"), dict):
                continue
            team = entry["team"]
            if team.get("teamId") == team_id:
                if team.get("teamNumber") != team_number or team.get("teamName") != team_name:
                    raise RuntimeError(
                        "internal team ID resolved to an unexpected public team: "
                        f"{team!r}"
                    )
                return entry, data.get("generatedAt")
        cursor = data.get("nextCursor")
        if not cursor:
            break
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise RuntimeError("leaderboard returned an invalid or repeated cursor")
        seen_cursors.add(cursor)
    raise RuntimeError(
        f"could not validate {team_id} as {team_number} / {team_name!r}"
    )


def crawl_placements(
    *, team_id: str, scope: str, page_size: int, max_pages: int, delay: float
) -> tuple[list[dict[str, Any]], int, int]:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_pairs: set[tuple[str, int]] = set()
    rows: list[dict[str, Any]] = []
    pages = 0
    previous_points = math.inf
    unique_rows = 0

    while True:
        query: dict[str, str | int] = {"limit": page_size}
        if cursor:
            query["cursor"] = cursor
        path = (
            f"/api/igp24/leaderboard/teams/{urllib.parse.quote(team_id)}/placements?"
            + urllib.parse.urlencode(query)
        )
        data = public_json(path)
        if data.get("teamId") != team_id:
            raise RuntimeError(f"placements response changed team ID: {data.get('teamId')!r}")
        raw_rows = data.get("placements")
        if not isinstance(raw_rows, list):
            raise RuntimeError("placements response has no placements array")
        pages += 1
        hit_non_unique = False
        for raw in raw_rows:
            row = validate_placement(raw)
            if row["points"] > previous_points + 1e-12:
                raise RuntimeError("placements are not sorted by descending points")
            previous_points = row["points"]
            pair = (row["label"], row["r"])
            if pair in seen_pairs:
                raise RuntimeError(f"duplicate placement across pages: {pair}")
            seen_pairs.add(pair)
            if row["kTeams"] == 1:
                if abs(row["points"] - 1.0) > 1e-12:
                    raise RuntimeError(
                        f"unexpected non-unit score for unique-held pair {pair}: "
                        f"{row['points']}"
                    )
                unique_rows += 1
                rows.append(row)
            elif scope == "all":
                rows.append(row)
            else:
                hit_non_unique = True

        if scope == "unique" and hit_non_unique:
            break
        cursor = data.get("nextCursor")
        if not cursor:
            break
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise RuntimeError("placements returned an invalid or repeated cursor")
        seen_cursors.add(cursor)
        if pages >= max_pages:
            raise RuntimeError(
                f"placements exceed --max-pages={max_pages}; refusing a partial snapshot"
            )
        if delay:
            time.sleep(delay)
    return rows, pages, unique_rows


def write_jsonl_atomic(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fetched_at: str,
    team_id: str,
    team_number: str,
    team_name: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                output = {
                    "fetchedAt": fetched_at,
                    "teamId": team_id,
                    "teamNumber": team_number,
                    "teamName": team_name,
                    **row,
                }
                handle.write(json.dumps(output, separators=(",", ":"), sort_keys=True))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def command_crawl(args: argparse.Namespace) -> None:
    team_id = args.team_id.strip()
    team_number = args.team_number.strip()
    team_name = args.team_name
    if not team_id.startswith("teamv2_") or not team_number.startswith("IGP24-T"):
        raise ValueError("invalid --team-id or --team-number")
    prefix = args.output_prefix
    if prefix is None:
        prefix = (
            "low_hanging_fruit"
            if team_id == TEAM_ID
            else team_number.lower().replace("-", "_")
        )
    if not prefix or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in prefix):
        raise ValueError("--output-prefix may contain only letters, digits, '_' and '-'")

    entry, generated_at = resolve_target_team(team_id, team_number, team_name)
    started_at = utc_now()
    rows, pages, unique_rows = crawl_placements(
        team_id=team_id,
        scope=args.scope,
        page_size=args.page_size,
        max_pages=args.max_pages,
        delay=args.delay,
    )
    completed_at = utc_now()
    output_path = DATA_DIR / f"{prefix}_{args.scope}_placements.jsonl"

    with connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO public_team_placement_crawls (
                team_id, team_number, team_name, scope,
                leaderboard_generated_at, started_at, completed_at,
                pages, placement_rows, unique_rows, jsonl_path, complete
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                team_id,
                team_number,
                team_name,
                args.scope,
                generated_at,
                started_at,
                completed_at,
                pages,
                len(rows),
                unique_rows,
                str(output_path),
            ),
        )
        crawl_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO public_team_placements (
                crawl_id, team_id, team_number, team_name,
                t, label, r, points, k_teams,
                scoring_disc_abs, minimum_disc_abs, disc_source,
                is_solvable, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    crawl_id,
                    team_id,
                    team_number,
                    team_name,
                    row["t"],
                    row["label"],
                    row["r"],
                    row["points"],
                    row["kTeams"],
                    row["scoringDiscAbs"],
                    row["minScoringDiscAbs"],
                    row["discSource"],
                    int(row["isSolvable"]),
                    json.dumps(row, separators=(",", ":"), sort_keys=True),
                )
                for row in rows
            ],
        )
        # Keep an unsuccessful file write from creating a completed DB crawl.
        # The SQLite context rolls back both inserts if this raises.
        write_jsonl_atomic(
            output_path,
            rows,
            fetched_at=completed_at,
            team_id=team_id,
            team_number=team_number,
            team_name=team_name,
        )
        conn.execute(
            "UPDATE public_team_placement_crawls SET complete = 1 WHERE crawl_id = ?",
            (crawl_id,),
        )
        conn.commit()
    print(
        json.dumps(
            {
                "crawlId": crawl_id,
                "team": entry.get("team"),
                "rank": entry.get("rank"),
                "score": entry.get("score"),
                "leaderboardGeneratedAt": generated_at,
                "scope": args.scope,
                "pages": pages,
                "placements": len(rows),
                "uniqueHeld": unique_rows,
                "jsonl": str(output_path),
                "database": str(DB_PATH),
            },
            indent=2,
            sort_keys=True,
        )
    )


def command_search(args: argparse.Namespace) -> None:
    if not 1 <= args.t <= 25_000 or args.r not in range(0, 25, 2):
        raise ValueError("search requires t in 1..25000 and even r in 0..24")
    query = urllib.parse.urlencode({"t": args.t, "r": args.r})
    data = public_json("/api/igp24/leaderboard/search?" + query)
    raw_rows = data.get("placements")
    if not isinstance(raw_rows, list):
        raise RuntimeError("search response has no placements array")
    fetched_at = utc_now()
    holders: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = validate_placement(raw)
        team = raw.get("team")
        if not isinstance(team, dict) or not isinstance(team.get("teamId"), str):
            raise RuntimeError("search placement has no public team identity")
        holders.append({**row, "team": team})
    if args.record:
        with connect_db() as conn:
            conn.executemany(
                """
                INSERT INTO public_pair_holder_evidence (
                    fetched_at, t, label, r, team_id, team_number, team_name,
                    points, k_teams, scoring_disc_abs, minimum_disc_abs,
                    disc_source, is_solvable, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        fetched_at,
                        row["t"],
                        row["label"],
                        row["r"],
                        row["team"]["teamId"],
                        row["team"].get("teamNumber"),
                        row["team"].get("teamName"),
                        row["points"],
                        row["kTeams"],
                        row["scoringDiscAbs"],
                        row["minScoringDiscAbs"],
                        row["discSource"],
                        int(row["isSolvable"]),
                        json.dumps(row, separators=(",", ":"), sort_keys=True),
                    )
                    for row in holders
                ],
            )
            conn.commit()
    print(
        json.dumps(
            {
                "fetchedAt": fetched_at,
                "query": {"t": args.t, "r": args.r},
                "holders": holders,
                "recorded": bool(args.record),
            },
            indent=2,
            sort_keys=True,
        )
    )


def command_summary(args: argparse.Namespace) -> None:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT COUNT(*)
                      FROM public_team_placements p
                     WHERE p.crawl_id = c.crawl_id AND p.k_teams = 1) AS stored_unique
              FROM public_team_placement_crawls c
             WHERE c.team_id = ? AND c.complete = 1
             ORDER BY c.crawl_id DESC
             LIMIT 10
            """,
            (args.team_id,),
        ).fetchall()
    print(json.dumps({"crawls": [dict(row) for row in rows]}, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    crawl = subparsers.add_parser(
        "crawl", help="validate Low hanging fruit and snapshot its public placements"
    )
    crawl.add_argument("--scope", choices=("unique", "all"), default="unique")
    crawl.add_argument("--team-id", default=TEAM_ID)
    crawl.add_argument("--team-number", default=TEAM_NUMBER)
    crawl.add_argument("--team-name", default=TEAM_NAME)
    crawl.add_argument("--output-prefix")
    crawl.add_argument("--page-size", type=int, default=100)
    crawl.add_argument("--max-pages", type=int, default=1000)
    crawl.add_argument("--delay", type=float, default=0.1)
    crawl.set_defaults(func=command_crawl)

    search = subparsers.add_parser(
        "search", help="show public holder evidence for one exact (t,r) pair"
    )
    search.add_argument("t", type=int)
    search.add_argument("r", type=int)
    search.add_argument("--record", action="store_true")
    search.set_defaults(func=command_search)

    summary = subparsers.add_parser("summary", help="summarize stored placement snapshots")
    summary.add_argument("--team-id", default=TEAM_ID)
    summary.set_defaults(func=command_summary)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if getattr(args, "max_pages", 1) < 1:
            raise ValueError("--max-pages must be positive")
        if not 1 <= getattr(args, "page_size", 100) <= 100:
            raise ValueError("--page-size must be between 1 and 100")
        if getattr(args, "delay", 0.0) < 0.0 or getattr(args, "delay", 0.0) > 5.0:
            raise ValueError("--delay must be between 0 and 5 seconds")
        args.func(args)
    except (RuntimeError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

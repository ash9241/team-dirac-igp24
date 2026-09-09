#!/usr/bin/env python3
"""Authenticated, read-only fallback for the Low hanging fruit placement crawl.

The transport is deliberately narrower than the general SAIR client.  It uses
``sair_api.api_json`` and permits GET requests only to the competition
leaderboard and the pinned team's placements endpoint.  It never loads,
prints, copies, or accepts a credential itself.

The persisted rows and tables match ``public_holder_crawler.py`` so existing
offline consumers can use either transport without a migration.  No
competition mutation is available from this program.
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
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import sair_api


TEAM_ID = "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"
TEAM_NUMBER = "IGP24-T00013"
TEAM_NAME = "Low hanging fruit"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "ledger.sqlite3"
OUTPUT_PATHS = {
    "unique": DATA_DIR / "low_hanging_fruit_unique_placements.jsonl",
    "all": DATA_DIR / "low_hanging_fruit_all_placements.jsonl",
}
REQUEST_TIMEOUT_SECONDS = 90
TRANSIENT_READ_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)

Fetch = Callable[[str], dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_read_path(path: str) -> None:
    """Reject every route except the two allowlisted read-only resources."""

    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("authenticated crawl path must be relative to the fixed API base")
    placement_path = (
        "/leaderboard/teams/"
        + urllib.parse.quote(TEAM_ID, safe="")
        + "/placements"
    )
    if parsed.path not in ("/leaderboard", placement_path):
        raise ValueError("authenticated crawl path is outside the read-only allowlist")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - {"limit", "cursor"} or any(len(values) != 1 for values in query.values()):
        raise ValueError("authenticated crawl query has an unsupported parameter")
    if "limit" in query:
        try:
            limit = int(query["limit"][0])
        except ValueError as exc:
            raise ValueError("authenticated crawl limit is not an integer") from exc
        if not 1 <= limit <= 100:
            raise ValueError("authenticated crawl limit must be between 1 and 100")
    if "cursor" in query and not query["cursor"][0]:
        raise ValueError("authenticated crawl cursor is empty")


def authenticated_json(path: str) -> dict[str, Any]:
    _validate_read_path(path)
    # Credential handling remains encapsulated by sair_api.  Do not add a
    # token/header argument or diagnostic here.
    for attempt in range(len(TRANSIENT_READ_RETRY_DELAYS) + 1):
        try:
            return sair_api.api_json(
                "GET", path, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except OSError as exc:
            if (
                attempt >= len(TRANSIENT_READ_RETRY_DELAYS)
                or not sair_api.transient_read_error(exc)
            ):
                raise
            time.sleep(TRANSIENT_READ_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable authenticated read retry state")


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"malformed {name}: expected integer, got {value!r}")
    return value


def _require_generated_at(value: Any) -> str:
    # The live API may explicitly return null while the leaderboard is being
    # regenerated.  Treat that documented absence as a stable sentinel; all
    # non-null values still receive strict ISO-8601 validation below.
    if value is None:
        return "unavailable"
    if not isinstance(value, str) or not value:
        raise RuntimeError("leaderboard response has a malformed generatedAt timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("leaderboard generatedAt is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("leaderboard generatedAt is not timezone-aware")
    return value


def validate_placement(raw: Any) -> dict[str, Any]:
    """Validate and normalize the established public placement row schema."""

    if not isinstance(raw, dict):
        raise RuntimeError("malformed placement: expected object")
    t_value = _require_int(raw.get("t"), "placement.t")
    r_value = _require_int(raw.get("r"), "placement.r")
    k_teams = _require_int(raw.get("kTeams"), "placement.kTeams")
    label = raw.get("label")
    points = raw.get("points")
    if not 1 <= t_value <= 25_000 or label != f"24T{t_value}":
        raise RuntimeError(f"malformed placement label/t: {label!r}/{t_value!r}")
    if r_value not in range(0, 25, 2):
        raise RuntimeError(f"malformed placement r: {r_value!r}")
    if k_teams < 1:
        raise RuntimeError(f"malformed placement kTeams: {k_teams!r}")
    if isinstance(points, bool) or not isinstance(points, (int, float)):
        raise RuntimeError(f"malformed placement points: {points!r}")
    normalized_points = float(points)
    if not math.isfinite(normalized_points) or normalized_points < 0:
        raise RuntimeError(f"malformed placement points: {points!r}")
    if not isinstance(raw.get("isSolvable"), bool):
        raise RuntimeError("malformed placement isSolvable")
    for key in ("scoringDiscAbs", "minScoringDiscAbs"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.isdigit()):
            raise RuntimeError(f"malformed placement {key}: {value!r}")
    disc_source = raw.get("discSource")
    if disc_source is not None and not isinstance(disc_source, str):
        raise RuntimeError("malformed placement discSource")
    return {
        "t": t_value,
        "label": label,
        "r": r_value,
        "points": normalized_points,
        "kTeams": k_teams,
        "scoringDiscAbs": raw.get("scoringDiscAbs"),
        "minScoringDiscAbs": raw.get("minScoringDiscAbs"),
        "discSource": disc_source,
        "isSolvable": raw["isSolvable"],
    }


def resolve_target_team(
    fetch: Fetch, *, page_size: int, max_pages: int
) -> tuple[dict[str, Any], str]:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_team_ids: set[str] = set()
    previous_rank = 0
    generated_at: str | None = None

    for _page in range(max_pages):
        query: dict[str, str | int] = {"limit": page_size}
        if cursor is not None:
            query["cursor"] = cursor
        path = "/leaderboard?" + urllib.parse.urlencode(query)
        data = fetch(path)
        if not isinstance(data, dict):
            raise RuntimeError("leaderboard data is not an object")
        page_generated_at = _require_generated_at(data.get("generatedAt"))
        if generated_at is None:
            generated_at = page_generated_at
        elif page_generated_at != generated_at:
            raise RuntimeError("leaderboard generatedAt changed during pagination")
        items = data.get("items")
        if not isinstance(items, list):
            raise RuntimeError("leaderboard response has no items array")

        match = None
        for entry in items:
            if not isinstance(entry, dict):
                raise RuntimeError("leaderboard entry is not an object")
            nested_team = entry.get("team")
            if nested_team is None:
                # The live endpoint also serves a flattened leaderboard DTO.
                team = {
                    "teamId": entry.get("teamId"),
                    "teamNumber": entry.get("teamNumber"),
                    "teamName": entry.get("teamName"),
                }
            elif isinstance(nested_team, dict):
                team = nested_team
            else:
                raise RuntimeError("leaderboard entry has a malformed team object")
            team_id = team.get("teamId")
            if not isinstance(team_id, str) or not team_id:
                raise RuntimeError("leaderboard team has no teamId")
            if team_id in seen_team_ids:
                raise RuntimeError("leaderboard repeated a team across pages")
            seen_team_ids.add(team_id)
            rank = _require_int(entry.get("rank"), "leaderboard.rank")
            if rank < 1 or rank < previous_rank:
                raise RuntimeError("leaderboard entries are not ordered by rank")
            previous_rank = rank
            score = entry.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or float(score) < 0
            ):
                raise RuntimeError("leaderboard entry has malformed score")
            if team_id == TEAM_ID:
                if (
                    team.get("teamNumber") != TEAM_NUMBER
                    or team.get("teamName") != TEAM_NAME
                ):
                    raise RuntimeError(
                        "pinned team ID resolved to an unexpected leaderboard identity"
                    )
                match = entry
        if match is not None:
            assert generated_at is not None
            return match, generated_at

        next_cursor = data.get("nextCursor")
        if next_cursor in (None, ""):
            break
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
            raise RuntimeError("leaderboard returned an invalid or repeated cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise RuntimeError(
            f"leaderboard exceeds max_pages={max_pages}; refusing partial identity validation"
        )
    raise RuntimeError(
        f"could not validate {TEAM_ID} as {TEAM_NUMBER} / {TEAM_NAME!r}"
    )


def crawl_placements(
    fetch: Fetch,
    *,
    scope: str,
    page_size: int,
    max_pages: int,
    delay: float,
) -> tuple[list[dict[str, Any]], int, int]:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_pairs: set[tuple[str, int]] = set()
    rows: list[dict[str, Any]] = []
    pages = 0
    unique_rows = 0
    previous_points = math.inf
    endpoint = (
        "/leaderboard/teams/"
        + urllib.parse.quote(TEAM_ID, safe="")
        + "/placements"
    )

    while True:
        query: dict[str, str | int] = {"limit": page_size}
        if cursor is not None:
            query["cursor"] = cursor
        data = fetch(endpoint + "?" + urllib.parse.urlencode(query))
        if not isinstance(data, dict):
            raise RuntimeError("placements data is not an object")
        if data.get("teamId") != TEAM_ID:
            raise RuntimeError("placements response changed the pinned team ID")
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
            pair = (str(row["label"]), int(row["r"]))
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
        next_cursor = data.get("nextCursor")
        if next_cursor in (None, ""):
            break
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
            raise RuntimeError("placements returned an invalid or repeated cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if pages >= max_pages:
            raise RuntimeError(
                f"placements exceed max_pages={max_pages}; refusing a partial snapshot"
            )
        if delay:
            time.sleep(delay)
    if pages < 1:
        raise RuntimeError("placements crawl returned no pages")
    if scope == "unique" and unique_rows != len(rows):
        raise RuntimeError("unique crawl retained a non-unique placement")
    return rows, pages, unique_rows


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
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
        """
    )
    return connection


def _stage_jsonl(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fetched_at: str,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                output = {
                    "fetchedAt": fetched_at,
                    "teamId": TEAM_ID,
                    "teamNumber": TEAM_NUMBER,
                    "teamName": TEAM_NAME,
                    **row,
                }
                handle.write(json.dumps(output, separators=(",", ":"), sort_keys=True))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _reserve_backup(path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".backup", dir=path.parent
    )
    os.close(descriptor)
    backup = Path(name)
    backup.unlink()
    return backup


def persist_crawl(
    *,
    db_path: Path,
    output_path: Path,
    scope: str,
    leaderboard_generated_at: str,
    started_at: str,
    completed_at: str,
    pages: int,
    rows: list[dict[str, Any]],
    unique_rows: int,
) -> int:
    """Install one JSONL snapshot and matching completed crawl transaction."""

    staged = _stage_jsonl(output_path, rows, fetched_at=completed_at)
    backup: Path | None = None
    installed = False
    connection = connect_db(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            INSERT INTO public_team_placement_crawls (
                team_id, team_number, team_name, scope,
                leaderboard_generated_at, started_at, completed_at,
                pages, placement_rows, unique_rows, jsonl_path, complete
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                TEAM_ID, TEAM_NUMBER, TEAM_NAME, scope,
                leaderboard_generated_at, started_at, completed_at,
                pages, len(rows), unique_rows, str(output_path.resolve()),
            ),
        )
        crawl_id = int(cursor.lastrowid)
        connection.executemany(
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
                    crawl_id, TEAM_ID, TEAM_NUMBER, TEAM_NAME,
                    row["t"], row["label"], row["r"], row["points"],
                    row["kTeams"], row["scoringDiscAbs"],
                    row["minScoringDiscAbs"], row["discSource"],
                    int(row["isSolvable"]),
                    json.dumps(row, separators=(",", ":"), sort_keys=True),
                )
                for row in rows
            ],
        )
        if output_path.exists():
            backup = _reserve_backup(output_path)
            # Preserve the old inode under a same-directory recovery name;
            # os.replace below can then install the new snapshot atomically
            # without any interval in which the stable JSONL path is absent.
            os.link(output_path, backup)
        os.replace(staged, output_path)
        installed = True
        connection.execute(
            "UPDATE public_team_placement_crawls SET complete=1 WHERE crawl_id=?",
            (crawl_id,),
        )
        connection.commit()
        if backup is not None:
            backup.unlink(missing_ok=True)
        return crawl_id
    except BaseException:
        connection.rollback()
        if installed:
            if backup is not None and backup.exists():
                os.replace(backup, output_path)
            else:
                output_path.unlink(missing_ok=True)
        elif backup is not None and backup.exists():
            os.replace(backup, output_path)
        raise
    finally:
        connection.close()
        staged.unlink(missing_ok=True)
        if backup is not None:
            backup.unlink(missing_ok=True)


def run_crawl(
    *,
    scope: str,
    page_size: int,
    max_pages: int,
    delay: float,
    db_path: Path | None = None,
    output_path: Path | None = None,
    fetch: Fetch | None = None,
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    if scope not in ("unique", "all"):
        raise ValueError("scope must be 'unique' or 'all'")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    if not 0 <= delay <= 5:
        raise ValueError("delay must be between 0 and 5 seconds")
    if output_path is None:
        output_path = OUTPUT_PATHS[scope]
    if db_path is None:
        db_path = DB_PATH
    if fetch is None:
        fetch = authenticated_json

    entry, generated_at = resolve_target_team(
        fetch, page_size=page_size, max_pages=max_pages
    )
    started_at = now()
    rows, pages, unique_rows = crawl_placements(
        fetch,
        scope=scope,
        page_size=page_size,
        max_pages=max_pages,
        delay=delay,
    )
    completed_at = now()
    crawl_id = persist_crawl(
        db_path=db_path,
        output_path=output_path,
        scope=scope,
        leaderboard_generated_at=generated_at,
        started_at=started_at,
        completed_at=completed_at,
        pages=pages,
        rows=rows,
        unique_rows=unique_rows,
    )
    return {
        "crawlId": crawl_id,
        "team": {
            "teamId": TEAM_ID,
            "teamNumber": TEAM_NUMBER,
            "teamName": TEAM_NAME,
        },
        "rank": int(entry["rank"]),
        "score": float(entry["score"]),
        "leaderboardGeneratedAt": generated_at,
        "scope": scope,
        "pages": pages,
        "placements": len(rows),
        "uniqueHeld": unique_rows,
        "jsonl": str(output_path.resolve()),
        "database": str(db_path.resolve()),
        "transport": "authenticated_fixed_base_read_only",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("unique", "all"), default="unique")
    parser.add_argument("--team-id", default=TEAM_ID)
    parser.add_argument("--team-number", default=TEAM_NUMBER)
    parser.add_argument("--team-name", default=TEAM_NAME)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--delay", type=float, default=0.1)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    global TEAM_ID, TEAM_NUMBER, TEAM_NAME
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        team_id = str(args.team_id).strip()
        team_number = str(args.team_number).strip()
        team_name = str(args.team_name)
        if (
            not team_id.startswith("teamv2_")
            or not team_number.startswith("IGP24-T")
        ):
            raise ValueError("invalid --team-id or --team-number")
        TEAM_ID = team_id
        TEAM_NUMBER = team_number
        TEAM_NAME = team_name
        output_path = args.output
        if output_path is None and (
            TEAM_ID != "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"
        ):
            prefix = TEAM_NUMBER.lower().replace("-", "_")
            output_path = DATA_DIR / f"{prefix}_{args.scope}_placements.jsonl"
        result = run_crawl(
            scope=args.scope,
            page_size=args.page_size,
            max_pages=args.max_pages,
            delay=args.delay,
            output_path=output_path,
        )
    except (RuntimeError, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

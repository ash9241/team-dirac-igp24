from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import audit_rank11_low_hanging_fruit_raid as raid
import authenticated_placement_crawler as crawler


GENERATED_AT = "2026-07-22T05:00:00Z"


def placement(
    t_value: int,
    r_value: int,
    *,
    points: float = 1.0,
    k_teams: int = 1,
) -> dict:
    return {
        "t": t_value,
        "label": f"24T{t_value}",
        "r": r_value,
        "points": points,
        "kTeams": k_teams,
        "scoringDiscAbs": str(10 ** (t_value % 10 + 2)),
        "minScoringDiscAbs": str(10 ** (t_value % 10 + 2)),
        "discSource": "field",
        "isSolvable": False,
    }


def leaderboard(*, wrong_name: bool = False) -> dict:
    return {
        "generatedAt": GENERATED_AT,
        "items": [
            {
                "rank": 1,
                "score": 1200.0,
                "team": {
                    "teamId": "teamv2_first",
                    "teamNumber": "IGP24-T00001",
                    "teamName": "First",
                },
            },
            {
                "rank": 11,
                "score": 1058.0,
                "team": {
                    "teamId": crawler.TEAM_ID,
                    "teamNumber": crawler.TEAM_NUMBER,
                    "teamName": "Wrong name" if wrong_name else crawler.TEAM_NAME,
                },
            },
        ],
        "nextCursor": None,
    }


class AuthenticatedPlacementCrawlerTests(unittest.TestCase):
    def test_null_leaderboard_generation_time_uses_stable_sentinel(self) -> None:
        self.assertEqual(crawler._require_generated_at(None), "unavailable")

    def test_flat_leaderboard_team_schema_is_supported(self) -> None:
        board = leaderboard()
        board["generatedAt"] = None
        board["items"] = [
            {
                "rank": row["rank"],
                "score": row["score"],
                "teamId": row["team"]["teamId"],
                "teamNumber": row["team"]["teamNumber"],
                "teamName": row["team"]["teamName"],
            }
            for row in board["items"]
        ]
        entry, generated_at = crawler.resolve_target_team(
            lambda _path: board, page_size=100, max_pages=1
        )
        self.assertEqual(entry["rank"], 11)
        self.assertEqual(generated_at, "unavailable")

    def test_unique_crawl_preserves_format_and_is_audit_compatible(self) -> None:
        calls = []

        def fetch(path: str) -> dict:
            calls.append(path)
            parsed = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/leaderboard":
                return leaderboard()
            self.assertEqual(
                parsed.path,
                f"/leaderboard/teams/{crawler.TEAM_ID}/placements",
            )
            if "cursor" not in query:
                return {
                    "teamId": crawler.TEAM_ID,
                    "placements": [placement(100, 0), placement(101, 2)],
                    "nextCursor": "page-2",
                }
            self.assertEqual(query["cursor"], ["page-2"])
            return {
                "teamId": crawler.TEAM_ID,
                "placements": [placement(102, 4, points=0.5, k_teams=2)],
                # A unique crawl must stop here because score ordering proves
                # the later slice cannot return to a unit-score sole pair.
                "nextCursor": "unused-page-3",
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "ledger.sqlite3"
            output_path = root / "placements.jsonl"
            timestamps = iter(
                ["2026-07-22T05:01:00+00:00", "2026-07-22T05:01:01+00:00"]
            )
            result = crawler.run_crawl(
                scope="unique",
                page_size=100,
                max_pages=10,
                delay=0,
                db_path=db_path,
                output_path=output_path,
                fetch=fetch,
                now=lambda: next(timestamps),
            )
            self.assertEqual(result["placements"], 2)
            self.assertEqual(result["uniqueHeld"], 2)
            self.assertEqual(result["pages"], 2)
            self.assertFalse(any("unused-page-3" in path for path in calls))

            jsonl = [json.loads(line) for line in output_path.read_text().splitlines()]
            self.assertEqual(len(jsonl), 2)
            self.assertTrue(all(row["teamId"] == crawler.TEAM_ID for row in jsonl))
            self.assertTrue(all(row["kTeams"] == 1 for row in jsonl))

            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            crawl = connection.execute(
                "SELECT * FROM public_team_placement_crawls"
            ).fetchone()
            self.assertEqual(int(crawl["complete"]), 1)
            self.assertEqual(crawl["scope"], "unique")
            self.assertEqual(int(crawl["placement_rows"]), 2)
            self.assertEqual(int(crawl["unique_rows"]), 2)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM public_team_placements"
                ).fetchone()[0],
                2,
            )
            latest, placements = raid.latest_public_placements(
                connection,
                datetime(2026, 7, 22, 6, tzinfo=timezone.utc),
            )
            self.assertTrue(latest["freshAtAudit"])
            self.assertEqual(set(placements), {("24T100", 0), ("24T101", 2)})
            connection.close()

    def test_all_scope_follows_cursor_and_retains_nonunique(self) -> None:
        def fetch(path: str) -> dict:
            parsed = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/leaderboard":
                return leaderboard()
            if "cursor" not in query:
                return {
                    "teamId": crawler.TEAM_ID,
                    "placements": [placement(200, 0)],
                    "nextCursor": "two",
                }
            return {
                "teamId": crawler.TEAM_ID,
                "placements": [placement(201, 2, points=0.5, k_teams=2)],
                "nextCursor": None,
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = crawler.run_crawl(
                scope="all",
                page_size=100,
                max_pages=10,
                delay=0,
                db_path=root / "ledger.sqlite3",
                output_path=root / "all.jsonl",
                fetch=fetch,
            )
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["placements"], 2)
            self.assertEqual(result["uniqueHeld"], 1)

    def test_wrong_team_identity_fails_before_placement_fetch_or_write(self) -> None:
        calls = []

        def fetch(path: str) -> dict:
            calls.append(path)
            return leaderboard(wrong_name=True)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "unexpected leaderboard identity"):
                crawler.run_crawl(
                    scope="unique",
                    page_size=100,
                    max_pages=10,
                    delay=0,
                    db_path=root / "ledger.sqlite3",
                    output_path=root / "placements.jsonl",
                    fetch=fetch,
                )
            self.assertEqual(len(calls), 1)
            self.assertFalse((root / "ledger.sqlite3").exists())
            self.assertFalse((root / "placements.jsonl").exists())

    def test_out_of_order_placement_fails_without_persistence(self) -> None:
        def fetch(path: str) -> dict:
            if urllib.parse.urlsplit(path).path == "/leaderboard":
                return leaderboard()
            return {
                "teamId": crawler.TEAM_ID,
                "placements": [
                    placement(300, 0, points=0.5, k_teams=2),
                    placement(301, 2, points=1.0, k_teams=1),
                ],
                "nextCursor": None,
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "descending points"):
                crawler.run_crawl(
                    scope="all",
                    page_size=100,
                    max_pages=10,
                    delay=0,
                    db_path=root / "ledger.sqlite3",
                    output_path=root / "placements.jsonl",
                    fetch=fetch,
                )
            self.assertFalse((root / "ledger.sqlite3").exists())
            self.assertFalse((root / "placements.jsonl").exists())

    def test_authenticated_transport_is_get_only_and_emits_no_credential(self) -> None:
        calls = []

        def api_json(method: str, path: str, *, timeout: int) -> dict:
            calls.append((method, path, timeout))
            if urllib.parse.urlsplit(path).path == "/leaderboard":
                return leaderboard()
            return {
                "teamId": crawler.TEAM_ID,
                "placements": [placement(400, 0)],
                "nextCursor": None,
            }

        secret = "credential-must-never-appear-9c7b"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(crawler, "DB_PATH", root / "ledger.sqlite3"),
                mock.patch.object(
                    crawler,
                    "OUTPUT_PATHS",
                    {
                        "unique": root / "unique.jsonl",
                        "all": root / "all.jsonl",
                    },
                ),
                mock.patch.object(crawler.sair_api, "api_json", side_effect=api_json),
                mock.patch.dict(os.environ, {"SAIR_API_KEY": secret}),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = crawler.main(["--scope", "unique", "--delay", "0"])
            self.assertEqual(exit_code, 0)
            self.assertNotIn(secret, stdout.getvalue())
            self.assertNotIn(secret, stderr.getvalue())
            self.assertGreaterEqual(len(calls), 2)
            for method, path, timeout in calls:
                self.assertEqual(method, "GET")
                self.assertEqual(timeout, crawler.REQUEST_TIMEOUT_SECONDS)
                crawler._validate_read_path(path)

        source = Path(crawler.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "/submissions", '"/submit', "load_token(", "api_request(",
            "Authorization", "Bearer ",
        ):
            self.assertNotIn(forbidden, source)

    def test_path_allowlist_rejects_every_mutating_or_external_shape(self) -> None:
        for path in (
            "/leaderboard/me",
            "/targets",
            "/submit",
            "//leaderboard",
            "https://api.sair.foundation/leaderboard",
            "/leaderboard?limit=100&extra=1",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    crawler._validate_read_path(path)

    def test_jsonl_install_failure_restores_old_file_and_rolls_back_crawl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "ledger.sqlite3"
            output_path = root / "placements.jsonl"
            output_path.write_text("old-snapshot\n", encoding="utf-8")
            real_replace = os.replace
            calls = 0

            def fail_first_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("injected atomic install failure")
                return real_replace(source, destination)

            with mock.patch.object(
                crawler.os, "replace", side_effect=fail_first_replace
            ):
                with self.assertRaisesRegex(OSError, "injected atomic install failure"):
                    crawler.persist_crawl(
                        db_path=db_path,
                        output_path=output_path,
                        scope="unique",
                        leaderboard_generated_at=GENERATED_AT,
                        started_at="2026-07-22T05:01:00+00:00",
                        completed_at="2026-07-22T05:01:01+00:00",
                        pages=1,
                        rows=[crawler.validate_placement(placement(500, 0))],
                        unique_rows=1,
                    )
            self.assertEqual(
                output_path.read_text(encoding="utf-8"), "old-snapshot\n"
            )
            connection = sqlite3.connect(db_path)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM public_team_placement_crawls"
                ).fetchone()[0],
                0,
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()

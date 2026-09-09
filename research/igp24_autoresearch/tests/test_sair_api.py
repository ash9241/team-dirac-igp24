from __future__ import annotations

import contextlib
import io
import json
import socket
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import authenticated_placement_crawler as placement_crawler
import sair_api


class FakeResponse:
    status = 200
    headers: dict[str, str] = {}

    def __init__(self, body: bytes = b'{"ok":true,"data":{}}') -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def target_page(
    label: str,
    r: int,
    *,
    next_cursor: str | None,
    generated_at: str,
) -> dict:
    return {
        "generatedAt": generated_at,
        "labels": [
            {
                "label": label,
                "t": int(label[3:]),
                "signatures": [
                    {
                        "r": r,
                        "teamCount": 0,
                        "minimumDiscAbs": "123",
                        "discovered": False,
                    }
                ],
            }
        ],
        "nextCursor": next_cursor,
    }


class SairApiTests(unittest.TestCase):
    def test_get_retries_transient_dns_with_bounded_backoff(self) -> None:
        dns_error = urllib.error.URLError(socket.gaierror(8, "temporary DNS failure"))
        urlopen = mock.Mock(side_effect=[dns_error, dns_error, FakeResponse(b"ok")])
        with (
            mock.patch.object(sair_api, "load_token", return_value="secret-token"),
            mock.patch.object(sair_api.urllib.request, "urlopen", urlopen),
            mock.patch.object(sair_api.time, "sleep") as sleep,
        ):
            status, headers, body = sair_api.api_request("GET", "/leaderboard")

        self.assertEqual((status, headers, body), (200, {}, b"ok"))
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            list(sair_api.READ_RETRY_DELAYS[:2]),
        )

    def test_get_stops_after_the_bounded_retry_budget(self) -> None:
        failure = urllib.error.URLError(socket.gaierror(8, "DNS unavailable"))
        urlopen = mock.Mock(side_effect=failure)
        with (
            mock.patch.object(sair_api, "load_token", return_value="secret-token"),
            mock.patch.object(sair_api.urllib.request, "urlopen", urlopen),
            mock.patch.object(sair_api.time, "sleep") as sleep,
            self.assertRaises(urllib.error.URLError),
        ):
            sair_api.api_request("GET", "/leaderboard")

        self.assertEqual(urlopen.call_count, len(sair_api.READ_RETRY_DELAYS) + 1)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            list(sair_api.READ_RETRY_DELAYS),
        )

    def test_get_http_failure_is_not_retried(self) -> None:
        failure = urllib.error.HTTPError(
            "https://example.invalid",
            503,
            "unavailable",
            {},
            io.BytesIO(b"temporary service failure"),
        )
        urlopen = mock.Mock(side_effect=failure)
        with (
            mock.patch.object(sair_api, "load_token", return_value="secret-token"),
            mock.patch.object(sair_api.urllib.request, "urlopen", urlopen),
            mock.patch.object(sair_api.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "SAIR HTTP 503"),
        ):
            sair_api.api_request("GET", "/leaderboard")

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_post_connection_failure_is_never_retried_or_logged(self) -> None:
        secret = "must-not-appear"
        failure = urllib.error.URLError(socket.gaierror(8, "DNS unavailable"))
        urlopen = mock.Mock(side_effect=failure)
        with (
            mock.patch.object(sair_api, "load_token", return_value=secret),
            mock.patch.object(sair_api.urllib.request, "urlopen", urlopen),
            mock.patch.object(sair_api.time, "sleep") as sleep,
            self.assertRaises(urllib.error.URLError) as raised,
        ):
            sair_api.api_request("POST", "/submissions", payload={"payload": {}})

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn(secret, str(raised.exception))

    def test_target_refresh_failure_preserves_complete_old_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "ledger.sqlite3"
            with mock.patch.object(sair_api, "DB_PATH", database):
                connection = sair_api.connect_db()
                try:
                    with connection:
                        connection.execute(
                            """
                            INSERT INTO targets (
                                label,t,r,team_count,minimum_disc_abs,
                                discovered,generated_at
                            ) VALUES ('24T1',1,0,7,'999',1,'old-snapshot')
                            """
                        )
                finally:
                    connection.close()

                pages = [
                    target_page(
                        "24T2",
                        0,
                        next_cursor="page-2",
                        generated_at="new-snapshot",
                    ),
                    urllib.error.URLError(
                        socket.gaierror(8, "second page DNS failure")
                    ),
                ]
                with (
                    mock.patch.object(sair_api, "api_json", side_effect=pages),
                    self.assertRaises(urllib.error.URLError),
                ):
                    sair_api.command_targets(mock.Mock())

                connection = sqlite3.connect(database)
                try:
                    rows = connection.execute(
                        """
                        SELECT label,r,team_count,generated_at
                        FROM targets ORDER BY label,r
                        """
                    ).fetchall()
                finally:
                    connection.close()
                self.assertEqual(rows, [("24T1", 0, 7, "old-snapshot")])

    def test_successful_target_refresh_swaps_all_pages_at_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "ledger.sqlite3"
            with mock.patch.object(sair_api, "DB_PATH", database):
                connection = sair_api.connect_db()
                try:
                    with connection:
                        connection.execute(
                            """
                            INSERT INTO targets (
                                label,t,r,team_count,minimum_disc_abs,
                                discovered,generated_at
                            ) VALUES ('24T1',1,0,7,'999',1,'old-snapshot')
                            """
                        )
                finally:
                    connection.close()

                pages = [
                    target_page(
                        "24T2",
                        0,
                        next_cursor="page-2",
                        generated_at="new-snapshot-1",
                    ),
                    target_page(
                        "24T3",
                        4,
                        next_cursor=None,
                        generated_at="new-snapshot-2",
                    ),
                ]
                output = io.StringIO()
                with (
                    mock.patch.object(sair_api, "api_json", side_effect=pages),
                    contextlib.redirect_stdout(output),
                ):
                    sair_api.command_targets(mock.Mock())

                connection = sqlite3.connect(database)
                try:
                    rows = connection.execute(
                        """
                        SELECT label,r,team_count,generated_at
                        FROM targets ORDER BY label,r
                        """
                    ).fetchall()
                finally:
                    connection.close()
                self.assertEqual(
                    rows,
                    [
                        ("24T2", 0, 0, "new-snapshot-1"),
                        ("24T3", 4, 0, "new-snapshot-2"),
                    ],
                )
                summary = json.loads(output.getvalue())
                self.assertEqual(summary["labels"], 2)
                self.assertEqual(summary["pairs"], 2)

    def test_rank10_crawl_step_pins_identity_before_persistence(self) -> None:
        unique = {
            "t": 1,
            "label": "24T1",
            "r": 0,
            "points": 1.0,
            "kTeams": 1,
            "scoringDiscAbs": "101",
            "minScoringDiscAbs": "101",
            "discSource": "exact_nfdisc",
            "isSolvable": True,
        }
        shared = {
            "t": 2,
            "label": "24T2",
            "r": 0,
            "points": 0.5,
            "kTeams": 2,
            "scoringDiscAbs": "202",
            "minScoringDiscAbs": "202",
            "discSource": "exact_nfdisc",
            "isSolvable": True,
        }
        response = {
            "teamId": sair_api.RANK10_TEAM_ID,
            "placements": [unique, shared],
            "nextCursor": "not-used-after-shared-row",
        }
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            database = temporary_path / "ledger.sqlite3"
            checkpoint = temporary_path / "checkpoint.json"
            output = temporary_path / "placements.jsonl"
            stdout = io.StringIO()
            with (
                mock.patch.object(placement_crawler, "DB_PATH", database),
                mock.patch.object(
                    sair_api, "_rank10_generated_path", side_effect=[checkpoint, output]
                ),
                mock.patch.object(
                    placement_crawler, "TEAM_ID", "fallback-team-id"
                ),
                mock.patch.object(
                    placement_crawler, "TEAM_NUMBER", "fallback-team-number"
                ),
                mock.patch.object(
                    placement_crawler, "TEAM_NAME", "fallback-team-name"
                ),
                mock.patch.object(sair_api, "api_json", return_value=response),
                contextlib.redirect_stdout(stdout),
            ):
                sair_api.command_rank10_crawl_step(
                    mock.Mock(checkpoint="", output="", max_pages=20)
                )
                self.assertEqual(placement_crawler.TEAM_ID, sair_api.RANK10_TEAM_ID)
                self.assertEqual(
                    placement_crawler.TEAM_NUMBER, sair_api.RANK10_TEAM_NUMBER
                )
                self.assertEqual(placement_crawler.TEAM_NAME, sair_api.RANK10_TEAM_NAME)

            connection = sqlite3.connect(database)
            try:
                crawl = connection.execute(
                    """
                    SELECT team_id,team_number,team_name,complete,placement_rows
                    FROM public_team_placement_crawls
                    """
                ).fetchone()
                placement = connection.execute(
                    """
                    SELECT team_id,team_number,team_name,label,r,k_teams,points
                    FROM public_team_placements
                    """
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(
                crawl,
                (
                    sair_api.RANK10_TEAM_ID,
                    sair_api.RANK10_TEAM_NUMBER,
                    sair_api.RANK10_TEAM_NAME,
                    1,
                    1,
                ),
            )
            self.assertEqual(
                placement,
                (
                    sair_api.RANK10_TEAM_ID,
                    sair_api.RANK10_TEAM_NUMBER,
                    sair_api.RANK10_TEAM_NAME,
                    "24T1",
                    0,
                    1,
                    1.0,
                ),
            )
            jsonl = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(jsonl["teamId"], sair_api.RANK10_TEAM_ID)
            self.assertEqual(jsonl["teamNumber"], sair_api.RANK10_TEAM_NUMBER)
            self.assertEqual(jsonl["teamName"], sair_api.RANK10_TEAM_NAME)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "complete")


if __name__ == "__main__":
    unittest.main()

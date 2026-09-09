from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import audit_rank11_low_hanging_fruit_raid as raid


class Rank11RaidAuditTests(unittest.TestCase):
    def test_head_to_head_projection_caps_full_swing(self) -> None:
        value = raid.head_to_head_projection(101, 10_000)
        self.assertAlmostEqual(value["candidateScoreAfterJoin"], 0.5)
        self.assertAlmostEqual(value["projectedNetRelativeSwing"], 1.0)
        self.assertAlmostEqual(value["maximumNetRelativeSwing"], 1.0)

    def test_head_to_head_projection_penalizes_worse_discriminant(self) -> None:
        value = raid.head_to_head_projection(10_000, 100)
        self.assertAlmostEqual(value["candidateScoreAfterJoin"], 0.25)
        self.assertAlmostEqual(value["projectedNetRelativeSwing"], 0.75)
        self.assertAlmostEqual(value["maximumNetRelativeSwing"], 1.0)

    def test_unknown_discriminant_preserves_only_maximum(self) -> None:
        value = raid.head_to_head_projection(None, 100)
        self.assertEqual(value, {
            "candidateScoreAfterJoin": None,
            "projectedNetRelativeSwing": None,
            "maximumNetRelativeSwing": 1.0,
        })

    def test_merge_exact_rejects_conflicting_pair(self) -> None:
        pool = {}
        raid.merge_exact(
            pool, "a" * 64, ("24T1", 0), None, {"one"}, [], set(), 100
        )
        with self.assertRaisesRegex(ValueError, "conflicting exact pair"):
            raid.merge_exact(
                pool, "a" * 64, ("24T2", 0), None, {"two"}, [], set(), 90
            )

    def test_latest_public_placements_marks_old_complete_crawl_stale(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE public_team_placement_crawls (
              crawl_id INTEGER PRIMARY KEY, team_id TEXT, team_number TEXT,
              team_name TEXT, scope TEXT, leaderboard_generated_at TEXT,
              started_at TEXT, completed_at TEXT, pages INTEGER,
              placement_rows INTEGER, unique_rows INTEGER, jsonl_path TEXT,
              complete INTEGER
            );
            CREATE TABLE public_team_placements (
              crawl_id INTEGER, team_id TEXT, team_number TEXT, team_name TEXT,
              t INTEGER, label TEXT, r INTEGER, points REAL, k_teams INTEGER,
              scoring_disc_abs TEXT, minimum_disc_abs TEXT, disc_source TEXT,
              is_solvable INTEGER, raw_json TEXT
            );
            """
        )
        now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        old = now - timedelta(hours=7)
        connection.execute(
            "INSERT INTO public_team_placement_crawls VALUES "
            "(1,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                raid.TEAM_ID, raid.TEAM_NUMBER, raid.TEAM_NAME, "unique",
                old.isoformat(), old.isoformat(), old.isoformat(), 1, 1, 1,
                "cache.jsonl", 1,
            ),
        )
        connection.execute(
            "INSERT INTO public_team_placements VALUES "
            "(1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                raid.TEAM_ID, raid.TEAM_NUMBER, raid.TEAM_NAME, 7, "24T7", 0,
                1.0, 1, "100", "100", "field", 0, "{}",
            ),
        )
        crawl, placements = raid.latest_public_placements(connection, now)
        self.assertFalse(crawl["freshAtAudit"])
        self.assertEqual(crawl["ageSecondsAtAudit"], 7 * 60 * 60)
        self.assertEqual(set(placements), {("24T7", 0)})

    def test_public_objects_do_not_contain_coefficient_payload(self) -> None:
        value = {
            "source": {"coefficientSha256": "f" * 64, "coefficientBytes": 91},
            "targetPair": "24T13536/r24",
        }
        rendered = json.dumps(value, sort_keys=True)
        self.assertIsNone(raid.pair_routes.COEFFICIENT_LINE_RE.search(rendered))


if __name__ == "__main__":
    unittest.main()

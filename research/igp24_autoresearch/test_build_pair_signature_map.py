from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_pair_signature_map as subject


class PairSignatureMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "ledger.sqlite3"
        self.orbit_map = self.root / "pair_orbit_map.jsonl"
        self.output = self.root / "pair_signature_map.jsonl"
        with sqlite3.connect(self.database) as conn:
            conn.executescript(
                """
                CREATE TABLE verifications(label TEXT, scoreable INTEGER);
                CREATE TABLE targets(label TEXT, r INTEGER, team_count INTEGER);
                CREATE TABLE baseline_pairs(label TEXT, r INTEGER);
                CREATE TABLE public_team_placement_crawls(
                    crawl_id INTEGER PRIMARY KEY,
                    team_id TEXT,
                    complete INTEGER
                );
                CREATE TABLE public_team_placements(
                    crawl_id INTEGER,
                    team_id TEXT,
                    label TEXT,
                    r INTEGER,
                    k_teams INTEGER
                );
                """
            )
            conn.executemany(
                "INSERT INTO verifications VALUES (?,?)",
                [
                    ("24T10", 1),
                    ("24T20", 1),
                    ("24T30", 1),
                    ("24T40", 1),
                    ("24T50", 0),
                ],
            )
            conn.executemany(
                "INSERT INTO targets VALUES (?,?,?)",
                [
                    ("24T900", 24, 0),
                    ("24T901", 24, 0),
                    ("24T999", 24, 2),
                ],
            )
            conn.execute("INSERT INTO baseline_pairs VALUES (?,?)", ("24T901", 24))
            conn.executemany(
                "INSERT INTO public_team_placement_crawls VALUES (?,?,?)",
                [
                    (1, subject.RANK12_TEAM_ID, 1),
                    (2, subject.RANK12_TEAM_ID, 0),
                    (3, subject.RANK12_TEAM_ID, 1),
                    (4, "another-team", 1),
                ],
            )
            conn.executemany(
                "INSERT INTO public_team_placements VALUES (?,?,?,?,?)",
                [
                    (1, subject.RANK12_TEAM_ID, "24T910", 24, 1),
                    (2, subject.RANK12_TEAM_ID, "24T911", 24, 1),
                    (3, subject.RANK12_TEAM_ID, "24T920", 12, 1),
                    (3, subject.RANK12_TEAM_ID, "24T921", 12, 2),
                    (4, "another-team", "24T930", 12, 1),
                ],
            )

        orbit_rows = [
            self.orbit_row("24T20", 20, 2, ["24T900"]),
            self.orbit_row("24T10", 10, 3, ["24T920"]),
            self.orbit_row("24T30", 30, 2, ["24T999"]),
            self.orbit_row("24T40", 40, 1, ["24T900"]),
            self.orbit_row("24T50", 50, 2, ["24T900"]),
        ]
        self.orbit_map.write_text(
            "".join(json.dumps(row) + "\n" for row in orbit_rows),
            encoding="utf-8",
        )
        self.paths = mock.patch.multiple(
            subject,
            DB_PATH=self.database,
            ORBIT_MAP=self.orbit_map,
            OUTPUT=self.output,
        )
        self.paths.start()

    def tearDown(self) -> None:
        self.paths.stop()
        self.temporary.cleanup()

    @staticmethod
    def orbit_row(label: str, t: int, count: int, targets: list[str]) -> dict:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "length24OrbitCount": count,
            "targets": [{"targetLabel": target} for target in targets],
        }

    def test_sources_are_scoreable_multi_orbit_groups_touching_current_targets(self) -> None:
        with sqlite3.connect(self.database) as conn:
            gold, rank12 = subject.harvest_pairs(conn)
        self.assertEqual(gold, {("24T900", 24)})
        self.assertEqual(rank12, {("24T920", 12)})
        self.assertEqual(subject.sources(), [("24T10", 10), ("24T20", 20)])

    def test_checkpoint_prunes_stale_rows_and_retries_noncertified_rows(self) -> None:
        checkpoint_rows = [
            {"sourceLabel": "24T20", "sourceT": 20, "status": "certified"},
            {"sourceLabel": "24T10", "sourceT": 10, "status": "timeout"},
            {"sourceLabel": "24T30", "sourceT": 30, "status": "certified"},
        ]
        self.output.write_text(
            "".join(json.dumps(row) + "\n" for row in checkpoint_rows),
            encoding="utf-8",
        )

        loaded = subject.load_checkpoint({"24T10", "24T20"}, fresh=False)
        cached, pending = subject.split_cached_sources(
            [("24T10", 10), ("24T20", 20)], loaded
        )
        self.assertEqual(cached, {"24T20"})
        self.assertEqual(pending, [("24T10", 10)])

        subject.write_checkpoint(loaded)
        written = [
            json.loads(line)
            for line in self.output.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([row["sourceT"] for row in written], [10, 20])
        self.assertFalse(self.output.with_suffix(".jsonl.tmp").exists())

    def test_changed_source_t_invalidates_a_certified_checkpoint(self) -> None:
        rows = {
            "24T10": {
                "sourceLabel": "24T10",
                "sourceT": 999,
                "status": "certified",
            }
        }
        cached, pending = subject.split_cached_sources([("24T10", 10)], rows)
        self.assertEqual(cached, set())
        self.assertEqual(pending, [("24T10", 10)])


if __name__ == "__main__":
    unittest.main()

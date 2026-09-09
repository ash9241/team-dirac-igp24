from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_pair_sum_multi
import build_pair_sum_pilot


def create_ledger(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE verifications (
                submission_id TEXT NOT NULL,
                polynomial_index INTEGER NOT NULL,
                label TEXT,
                r INTEGER,
                scoreable INTEGER NOT NULL,
                PRIMARY KEY (submission_id, polynomial_index)
            );
            CREATE TABLE polynomials (
                submission_id TEXT NOT NULL,
                polynomial_index INTEGER NOT NULL,
                PRIMARY KEY (submission_id, polynomial_index)
            );
            CREATE TABLE targets (
                label TEXT NOT NULL,
                r INTEGER NOT NULL,
                team_count INTEGER NOT NULL
            );
            CREATE TABLE baseline_pairs (
                label TEXT NOT NULL,
                r INTEGER NOT NULL
            );
            """
        )


def insert_source(
    path: Path,
    submission_id: str,
    polynomial_index: int,
    label: str,
    r: int,
    *,
    scoreable: int = 1,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO polynomials VALUES (?, ?)",
            (submission_id, polynomial_index),
        )
        conn.execute(
            "INSERT INTO verifications VALUES (?, ?, ?, ?, ?)",
            (submission_id, polynomial_index, label, r, scoreable),
        )


class SourceTaskCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "ledger.sqlite3"
        create_ledger(self.database)

        # Insert out of lexical order to prove selection is deterministic and
        # not dependent on SQLite's scan order.
        for submission_id, polynomial_index, r in (
            ("sub-c", 0, 4),
            ("sub-a", 1, 4),
            ("sub-a", 0, 4),
            ("sub-e", 0, 8),
            ("sub-d", 0, 8),
        ):
            insert_source(
                self.database,
                submission_id,
                polynomial_index,
                "24T10",
                r,
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_single_orbit_cap_is_per_label_and_r_and_keeps_provenance(self) -> None:
        orbit_map = self.root / "pair_orbit_map.jsonl"
        orbit_map.write_text(
            json.dumps(
                {
                    "sourceLabel": "24T10",
                    "length24OrbitCount": 1,
                    "targets": [{"targetLabel": "24T20"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO targets VALUES ('24T20', 0, 0)")

        with (
            mock.patch.object(build_pair_sum_pilot, "DB_PATH", self.database),
            mock.patch.object(
                build_pair_sum_pilot, "ORBIT_MAP_PATH", orbit_map
            ),
        ):
            tasks = build_pair_sum_pilot.load_tasks(2)

        self.assertEqual(
            tasks,
            [
                {
                    "submissionId": "sub-a",
                    "polynomialIndex": 0,
                    "sourceLabel": "24T10",
                    "sourceR": 4,
                    "targetLabel": "24T20",
                },
                {
                    "submissionId": "sub-a",
                    "polynomialIndex": 1,
                    "sourceLabel": "24T10",
                    "sourceR": 4,
                    "targetLabel": "24T20",
                },
                {
                    "submissionId": "sub-d",
                    "polynomialIndex": 0,
                    "sourceLabel": "24T10",
                    "sourceR": 8,
                    "targetLabel": "24T20",
                },
                {
                    "submissionId": "sub-e",
                    "polynomialIndex": 0,
                    "sourceLabel": "24T10",
                    "sourceR": 8,
                    "targetLabel": "24T20",
                },
            ],
        )

    def test_multi_orbit_default_cap_keeps_one_exact_source_row(self) -> None:
        orbit_map = self.root / "pair_orbit_map.jsonl"
        orbit_map.write_text(
            json.dumps(
                {
                    "sourceLabel": "24T10",
                    "length24OrbitCount": 2,
                    "targets": [
                        {"targetLabel": "24T20"},
                        {"targetLabel": "24T30"},
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        signature_map = self.root / "pair_signature_map.jsonl"
        signature_map.write_text(
            json.dumps({"sourceLabel": "24T10", "status": "certified"})
            + "\n",
            encoding="utf-8",
        )
        with sqlite3.connect(self.database) as conn:
            conn.execute("INSERT INTO targets VALUES ('24T20', 0, 0)")

        with (
            mock.patch.object(build_pair_sum_multi, "DB_PATH", self.database),
            mock.patch.object(build_pair_sum_multi, "ORBIT_MAP", orbit_map),
            mock.patch.object(
                build_pair_sum_multi, "SIGNATURE_MAP", signature_map
            ),
        ):
            tasks = build_pair_sum_multi.load_tasks()

        self.assertEqual(
            tasks,
            [
                {
                    "submissionId": "sub-a",
                    "polynomialIndex": 0,
                    "sourceLabel": "24T10",
                    "sourceR": 4,
                },
                {
                    "submissionId": "sub-d",
                    "polynomialIndex": 0,
                    "sourceLabel": "24T10",
                    "sourceR": 8,
                },
            ],
        )

    def test_nonpositive_caps_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            build_pair_sum_pilot.load_tasks(0)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            build_pair_sum_multi.load_tasks(-1)


if __name__ == "__main__":
    unittest.main()

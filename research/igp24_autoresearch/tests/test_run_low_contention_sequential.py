from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import run_low_contention_sequential as lane


class LowContentionSequentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan, cls.certificate, cls.routes = lane.validate_plan(
            lane.DEFAULT_PLAN, lane.DEFAULT_CERTIFICATE
        )

    def test_lc12_is_first_and_plan_is_coefficient_free(self) -> None:
        self.assertEqual(
            self.routes[0]["routeId"],
            "lc12_24T8316_r18_to_24T9237_r12",
        )
        text = lane.DEFAULT_PLAN.read_text(encoding="utf-8")
        self.assertIsNone(lane.COEFFICIENT_PAYLOAD_RE.search(text))
        self.assertFalse(self.plan["coefficientMaterialIncluded"])
        self.assertFalse(self.plan["submissionAuthorized"])

    def test_planned_artifact_paths_are_unique_and_scoped(self) -> None:
        seen = set()
        for route in self.routes:
            paths = lane.planned_paths(route)
            for name, path in paths.items():
                self.assertTrue(path.is_relative_to(lane.ROOT), (name, path))
                if name != "resultTemporary":
                    self.assertNotIn(path, seen)
                    seen.add(path)

    def test_exclusive_writer_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory(dir=lane.DATA) as directory:
            path = Path(directory) / "sealed.txt"
            lane.exclusive_text(path, "first\n")
            with self.assertRaises(lane.GuardFailure):
                lane.exclusive_text(path, "second\n")
            self.assertEqual(path.read_text(encoding="ascii"), "first\n")

    def test_heavy_command_is_pinned_and_no_commit_path_exists(self) -> None:
        lane.validate_heavy_command(self.routes[0])
        source = Path(lane.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"--commit"', source)

    def test_local_guard_requires_exact_tc1_and_novel_hash(self) -> None:
        route = copy.deepcopy(self.routes[0])
        source, target = route["source"], route["target"]
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE polynomials (
              submission_id TEXT, polynomial_index INTEGER,
              coefficient_hash TEXT
            );
            CREATE TABLE verifications (
              submission_id TEXT, polynomial_index INTEGER, status TEXT,
              scoreable INTEGER, label TEXT, r INTEGER
            );
            CREATE TABLE baseline_pairs (label TEXT, r INTEGER);
            CREATE TABLE targets (
              label TEXT, r INTEGER, team_count INTEGER, discovered INTEGER,
              minimum_disc_abs TEXT, generated_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO polynomials VALUES (?,?,?)",
            (source["submissionId"], source["polynomialIndex"], source["coefficientSha256"]),
        )
        connection.execute(
            "INSERT INTO verifications VALUES (?,?,?,?,?,?)",
            (
                source["submissionId"], source["polynomialIndex"], "accepted", 1,
                source["label"], source["r"],
            ),
        )
        connection.execute(
            "INSERT INTO targets VALUES (?,?,?,?,?,?)",
            (target["label"], target["r"], 1, 1, target["minimumDiscAbsAtSeal"], "test"),
        )
        digest = "a" * 64
        result = lane.local_guard(connection, route, set(), set(), digest)
        self.assertEqual(result["targetTeamCount"], 1)
        with self.assertRaises(lane.GuardFailure):
            lane.local_guard(connection, route, {digest}, set(), digest)
        connection.execute(
            "UPDATE targets SET team_count=2 WHERE label=? AND r=?",
            (target["label"], target["r"]),
        )
        with self.assertRaises(lane.GuardFailure):
            lane.local_guard(connection, route, set(), set(), digest)


if __name__ == "__main__":
    unittest.main()

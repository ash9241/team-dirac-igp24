from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_deterministic_frontier_coordinator as coordinator


class CoordinatorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "data"
        self.outbox = self.root / "outbox"
        self.receipts = self.root / "receipts"
        for path in (self.data, self.outbox, self.receipts):
            path.mkdir()
        self.database = self.data / "ledger.sqlite3"
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(
                """
                CREATE TABLE polynomials(
                    submission_id TEXT,
                    polynomial_index INTEGER,
                    coefficient_hash TEXT,
                    coefficients TEXT
                );
                CREATE TABLE verifications(
                    submission_id TEXT,
                    polynomial_index INTEGER,
                    status TEXT,
                    label TEXT,
                    r INTEGER,
                    scoreable INTEGER
                );
                CREATE TABLE baseline_pairs(label TEXT,r INTEGER);
                CREATE TABLE targets(
                    label TEXT,
                    r INTEGER,
                    team_count INTEGER,
                    discovered INTEGER,
                    minimum_disc_abs TEXT,
                    generated_at TEXT
                );
                """
            )
            connection.commit()
        self.action = self.data / "action.jsonl"
        self.action.write_text("{}\n", encoding="utf-8")
        self.routes = [self.make_route(1), self.make_route(2)]
        self.certificate = self.data / "frontier.json"
        self.write_certificate(self.routes)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def source_line(index: int) -> str:
        return ",".join(str(value) for value in [index + 1, *([0] * 23), 1])

    @staticmethod
    def candidate_line(index: int) -> str:
        return ",".join(str(value) for value in [index + 11, *([0] * 23), 1])

    def make_route(self, index: int) -> dict:
        source_label = f"24T{index}"
        target_label = f"24T{100 + index}"
        submission = f"sub_source_{index}"
        source_line = self.source_line(index)
        source_hash = hashlib.sha256(source_line.encode("ascii")).hexdigest()
        generated = "2026-07-22T03:00:00Z"
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO polynomials VALUES(?,?,?,?)",
                (submission, 0, source_hash, source_line),
            )
            connection.execute(
                "INSERT INTO verifications VALUES(?,?,?,?,?,?)",
                (submission, 0, "accepted", source_label, 24, 1),
            )
            connection.execute(
                "INSERT INTO targets VALUES(?,?,?,?,?,?)",
                (target_label, 24, 7, 1, str(1000 + index), generated),
            )
            connection.commit()
        output = f"data/route_{index}_result.jsonl"
        route_id = f"tc7_route_{index}"
        return {
            "routeId": route_id,
            "priorityRank": index,
            "status": "finalized_waiting_for_explicit_heavy_execution",
            "source": {
                "submissionId": submission,
                "polynomialIndex": 0,
                "label": source_label,
                "r": 24,
                "coefficientSha256": source_hash,
            },
            "target": {
                "label": target_label,
                "r": 24,
                "teamCountAtSeal": 7,
                "discoveredAtSeal": True,
                "minimumDiscAbsAtSeal": str(1000 + index),
                "generatedAtSeal": generated,
                "projectedMarginalScoreExact": "1/128",
            },
            "exactAction": {
                "actionArtifact": {
                    "path": "data/action.jsonl",
                    "sha256": coordinator.sha256_path(self.action),
                },
                "deterministicAcrossCompatibleClasses": True,
                "length24OrbitCount": 1,
                "orbitIndex": index,
            },
            "routeReliability": {"exactDeterministic": True},
            "output": output,
            "heavyCommand": [
                "/usr/local/bin/sage",
                "-python",
                "pair_sum_one.sage.py",
                submission,
                "0",
                "--orbit-map",
                "data/action.jsonl",
                "--expected-source-hash",
                source_hash,
                "--expected-target",
                target_label,
                "--output-jsonl",
                output,
            ],
        }

    def write_certificate(self, routes: list[dict]) -> None:
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            boundary = coordinator.boundary_snapshot(connection)
        value = {
            "schemaVersion": "test-finalized-deterministic-frontier-v1",
            "status": "certified_final_receipt_aware_runbooks_ready",
            "scope": "test_tc7",
            "coefficientMaterialIncluded": False,
            "credentialMaterialIncluded": False,
            "checks": {
                "allRoutesDeterministic": True,
                "certificateContainsNoCoefficientPayload": True,
            },
            "boundary": boundary,
            "runbooks": routes,
        }
        self.certificate.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def args(self, *, batch: str = "future_tc7", execute: bool = False, workers: int = 1):
        values = [
            "--root",
            str(self.root),
            "--certificate",
            "data/frontier.json",
            "--batch-name",
            batch,
            "--max-new-workers",
            str(workers),
        ]
        if execute:
            values.append("--execute")
        return values

    def write_result(self, route: dict) -> None:
        index = int(str(route["routeId"]).rsplit("_", 1)[1])
        line = self.candidate_line(index)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        value = {
            "status": "certified",
            "workerExitCode": 0,
            "sourceSubmissionId": route["source"]["submissionId"],
            "sourcePolynomialIndex": route["source"]["polynomialIndex"],
            "sourceCoefficientSha256": route["source"]["coefficientSha256"],
            "sourceLabel": route["source"]["label"],
            "sourceR": route["source"]["r"],
            "targetLabel": route["target"]["label"],
            "targetR": route["target"]["r"],
            "coefficientLine": line,
            "coefficientSha256": digest,
            "coefficientBytes": len(line.encode("ascii")),
            "orbitCertificate": {
                "actualDegrees": [24],
                "expectedDegrees": [24],
                "exponents": [1],
            },
            "orbitTargets": [
                {
                    "orbitIndex": route["exactAction"]["orbitIndex"],
                    "targetLabel": route["target"]["label"],
                }
            ],
        }
        path = self.root / route["output"]
        path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")

    def invoke(self, args: list[str]) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(coordinator.main(args), 0)
        rendered = output.getvalue()
        self.assertIsNone(coordinator.COEFFICIENT_RE.search(rendered))
        return json.loads(rendered)


class CoordinatorTests(CoordinatorFixture):
    def test_default_audit_is_write_free_and_never_launches(self) -> None:
        before = {path.relative_to(self.root) for path in self.root.rglob("*")}
        with mock.patch.object(
            coordinator, "launch_worker", side_effect=AssertionError("worker launched")
        ):
            result = self.invoke(self.args())
        after = {path.relative_to(self.root) for path in self.root.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual(
            result["status"], "offline_audit_no_workers_no_network_no_submission"
        )
        self.assertEqual(result["ready"], 2)
        self.assertEqual(result["resumableExactCheckpoints"], 0)
        self.assertEqual(result["sideEffects"]["metadataWrites"], 0)

    def test_exact_result_checkpoint_resumes_without_worker(self) -> None:
        self.write_certificate(self.routes[:1])
        self.write_result(self.routes[0])
        with mock.patch.object(
            coordinator, "launch_worker", side_effect=AssertionError("worker relaunched")
        ):
            result = self.invoke(self.args(execute=True, workers=0))
        self.assertEqual(result["successes"], 1)
        self.assertEqual(result["workersLaunchedThisInvocation"], 0)
        final = self.outbox / "future_tc7.txt"
        partial = self.outbox / ".future_tc7.partial.txt"
        self.assertTrue(final.is_file())
        self.assertTrue(partial.is_file())
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(partial.stat().st_mode), 0o600)
        mapping = json.loads(
            (self.data / "future_tc7_receipt_mapping_ready.json").read_text()
        )
        self.assertEqual(mapping["routeCount"], 1)
        self.assertEqual(mapping["distinctCandidateHashes"], 1)
        self.assertEqual(mapping["distinctTargetPairs"], 1)
        for path in self.data.glob("future_tc7*.json"):
            self.assertIsNone(coordinator.COEFFICIENT_RE.search(path.read_text()))

    def test_one_worker_partial_then_resume_preserves_order(self) -> None:
        launched: list[str] = []

        def fake_launch(_config, route):
            launched.append(route["routeId"])
            self.write_result(route)

        with mock.patch.object(coordinator, "launch_worker", side_effect=fake_launch):
            first = self.invoke(self.args(execute=True, workers=1))
        self.assertEqual(first["status"], "partial_checkpoint_ready_resume_with_execute")
        self.assertEqual(first["successes"], 1)
        self.assertEqual(first["pending"], 1)
        self.assertFalse((self.outbox / "future_tc7.txt").exists())
        self.assertTrue((self.outbox / ".future_tc7.partial.txt").exists())

        with mock.patch.object(coordinator, "launch_worker", side_effect=fake_launch):
            second = self.invoke(self.args(execute=True, workers=1))
        self.assertEqual(second["successes"], 2)
        self.assertEqual(second["workersLaunchedThisInvocation"], 1)
        self.assertEqual(launched, ["tc7_route_1", "tc7_route_2"])
        mapping = json.loads(
            (self.data / "future_tc7_receipt_mapping_ready.json").read_text()
        )
        self.assertEqual(
            [row["routeId"] for row in mapping["mappings"]],
            ["tc7_route_1", "tc7_route_2"],
        )
        final_lines = (self.outbox / "future_tc7.txt").read_text().splitlines()
        self.assertEqual(final_lines, [self.candidate_line(1), self.candidate_line(2)])

    def test_failed_worker_attempt_consumes_budget_and_stays_sequential(self) -> None:
        with mock.patch.object(
            coordinator,
            "launch_worker",
            side_effect=coordinator.GuardFailure("synthetic worker failure"),
        ) as launcher:
            result = self.invoke(self.args(execute=True, workers=1))
        self.assertEqual(launcher.call_count, 1)
        self.assertEqual(result["status"], "partial_checkpoint_ready_resume_with_execute")
        self.assertEqual(result["successes"], 0)
        self.assertEqual(result["pending"], 1)
        checkpoint = json.loads(
            (self.data / "future_tc7_checkpoint.json").read_text()
        )
        self.assertEqual(checkpoint["workersLaunchedThisInvocation"], 1)
        self.assertEqual(checkpoint["reports"][0]["status"], "skipped_fail_closed")
        self.assertEqual(
            checkpoint["reports"][1]["status"],
            "pending_worker_budget_resume_later",
        )

    def test_reserved_pair_blocks_audit_without_writes(self) -> None:
        reservation = self.data / "reserved.json"
        reservation.write_text(
            json.dumps(
                {
                    "coefficientMaterialIncluded": False,
                    "selected": [
                        {
                            "pair": "24T101/r24",
                            "coefficientSha256": "a" * 64,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        args = self.args() + ["--reserved", "data/reserved.json"]
        result = self.invoke(args)
        rows = {row["routeId"]: row for row in result["routes"]}
        self.assertEqual(rows["tc7_route_1"]["status"], "blocked_fail_closed")
        self.assertIn("reserved pair", rows["tc7_route_1"]["reason"])
        self.assertEqual(rows["tc7_route_2"]["status"], "ready_for_explicit_sequential_execution")

    def test_reserved_hash_blocks_an_exact_checkpoint(self) -> None:
        self.write_certificate(self.routes[:1])
        self.write_result(self.routes[0])
        line = self.candidate_line(1)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        reservation = self.data / "reserved_hash.json"
        reservation.write_text(
            json.dumps(
                {
                    "coefficientMaterialIncluded": False,
                    "reservedHashes": [digest],
                }
            ),
            encoding="utf-8",
        )
        result = self.invoke(
            self.args() + ["--reserved", "data/reserved_hash.json"]
        )
        self.assertEqual(result["routes"][0]["status"], "blocked_fail_closed")
        self.assertIn("reserved hash", result["routes"][0]["reason"])

    def test_existing_outbox_hash_blocks_an_exact_checkpoint(self) -> None:
        self.write_certificate(self.routes[:1])
        self.write_result(self.routes[0])
        existing = self.outbox / "preexisting.txt"
        existing.write_text(self.candidate_line(1) + "\n", encoding="ascii")
        result = self.invoke(self.args())
        self.assertEqual(result["routes"][0]["status"], "blocked_fail_closed")
        self.assertIn("outbox-covered", result["routes"][0]["reason"])

    def test_source_has_no_network_or_submission_path(self) -> None:
        source = Path(coordinator.__file__).read_text(encoding="utf-8")
        self.assertNotIn("sair_api", source)
        self.assertNotIn("requests", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("command_submit", source)
        self.assertIn("if args.execute", source)


class ExistingCertificateContractTests(unittest.TestCase):
    def test_tc4_and_tc5_tc6_certificates_match_generic_route_contract(self) -> None:
        root = Path(coordinator.__file__).resolve().parent
        for certificate_name, expected in (
            ("low_contention_tc4_routes_certificate.json", 22),
            ("low_contention_tc5_tc6_post_tc4_routes_certificate.json", 14),
        ):
            config = coordinator.Config(
                root=root,
                data=root / "data",
                outbox=root / "outbox",
                receipts=root / "receipts",
                database=root / "data/ledger.sqlite3",
                certificate=root / "data" / certificate_name,
                batch_name="contract_only",
                reservations=(),
            )
            _certificate, routes = coordinator.validate_certificate(config)
            self.assertEqual(len(routes), expected)


if __name__ == "__main__":
    unittest.main()

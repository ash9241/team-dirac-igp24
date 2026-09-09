import ast
import fcntl
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PREPARER_PATH = ROOT / "prepare_f5_untried_frontier.py"
WORKER_PATH = ROOT / "run_f5_untried_plan.sage.py"
DEST = ROOT / "data" / "f5_untried_frontier_20260722"
PLAN_PATH = DEST / "wave1_plan.json"
PREFLIGHT_PATH = DEST / "wave1_preflight.json"


def load_preparer():
    spec = importlib.util.spec_from_file_location("prepare_f5_untried_frontier", PREPARER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class PrepareF5UntriedFrontierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preparer = load_preparer()
        cls.plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
        cls.preflight = json.loads(PREFLIGHT_PATH.read_text(encoding="utf-8"))
        cls.worker_source = WORKER_PATH.read_text(encoding="utf-8")

    def test_canonical_reflection_exclusion(self):
        direct = "1,2,3,4,5"
        reflected = "1,-2,3,-4,5"
        unrelated = "1,2,3,4,6"
        self.assertEqual(
            self.preparer.canonical_hash(direct),
            self.preparer.canonical_hash(reflected),
        )
        self.assertNotEqual(
            self.preparer.canonical_hash(direct),
            self.preparer.canonical_hash(unrelated),
        )

    def test_claim_directory_jsonl_and_outbox_lineage_are_reserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            data = base / "data"
            outbox = base / "outbox"
            claims = data / "route_claims"
            claims.mkdir(parents=True)
            outbox.mkdir()
            (claims / "one.json").write_text(
                json.dumps({"targetLabel": "24T1", "targetR": 4}), encoding="utf-8"
            )
            (data / "staged.jsonl").write_text(
                json.dumps(
                    {
                        "candidate": {"coefficientSha256": "a" * 64},
                        "status": "hit_staged",
                        "target": {"label": "24T2", "r": 8},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (data / "directory.jsonl").mkdir()
            (outbox / "route_24T3_r12.txt").write_text("0," * 23 + "0,1\n", encoding="utf-8")
            destination = data / "own"
            destination.mkdir()
            with mock.patch.multiple(
                self.preparer,
                ROOT=base,
                DATA=data,
                OUTBOX=outbox,
                DEST=destination,
                INDEX=destination / "index.jsonl",
                CLAIMED_INDEX=destination / "claims.json",
                CLAIMS=destination / "claims",
            ):
                pairs, _ = self.preparer.claimed_pair_corpus()
            self.assertTrue({("24T1", 4), ("24T2", 8), ("24T3", 12)} <= pairs)

    def test_plan_and_launch_are_exactly_sha_pinned(self):
        plan_hash = sha256_path(PLAN_PATH)
        self.assertEqual(self.preflight["plan"]["sha256"], plan_hash)
        self.assertIn(f"--expected-plan-sha256 {plan_hash}", self.preflight["launchCommand"])
        self.assertIn("--expected-plan-sha256 <PLAN_SHA256>", self.plan["execution"]["commandTemplate"])
        self.assertEqual(
            self.plan["artifacts"]["worker"]["sha256"], sha256_path(WORKER_PATH)
        )

    def test_plan_is_coefficient_free_and_selection_is_fail_closed(self):
        plan_text = PLAN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("quotientLine", plan_text)
        self.assertNotIn("coefficientLine", plan_text)
        selected = self.plan["selectedSources"]
        self.assertEqual(len(selected), 50)
        self.assertEqual(len({row["canonicalQuotientSha256"] for row in selected}), 50)
        self.assertEqual(
            len({(row["source"]["label"], int(row["source"]["r"])) for row in selected}),
            50,
        )
        self.assertTrue(all(row["signatureAligned"] for row in selected))
        self.assertLessEqual(max(int(row["coefficientHeightBits"]) for row in selected), 256)
        self.assertTrue(all(self.preflight["checks"].values()))

    def test_selected_rows_are_exact_frontier_rows_and_pairs_unreserved(self):
        frontier_path = ROOT / self.plan["artifacts"]["frontierIndex"]["path"]
        frontier = {
            json.dumps(json.loads(line), separators=(",", ":"), sort_keys=True)
            for line in frontier_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        selected = {
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in self.plan["selectedSources"]
        }
        self.assertTrue(selected <= frontier)
        claim_index = json.loads(
            (ROOT / self.plan["artifacts"]["claimedPairIndex"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        reserved = {(row["label"], int(row["r"])) for row in claim_index["pairs"]}
        covered = {
            (pair["label"], int(pair["r"]))
            for row in self.plan["selectedSources"]
            for pair in row["possibleGoldPairs"]
        }
        self.assertFalse(covered & reserved)

    def test_prior_artifacts_are_exactly_current_and_checkpoint_is_separate(self):
        def artifacts(pattern):
            return [
                {
                    "path": str(path.resolve().relative_to(ROOT)),
                    "sha256": sha256_path(path),
                }
                for path in sorted((ROOT / "data").glob(pattern))
                if path.is_file()
            ]

        self.assertEqual(
            artifacts("agent_f5_full_ledger_safe_unique_orbit_*_plan.json"),
            self.plan["artifacts"]["priorPlans"],
        )
        self.assertEqual(
            artifacts("agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl"),
            self.plan["artifacts"]["priorResults"],
        )
        results = ROOT / self.plan["artifacts"]["results"]
        summary = ROOT / self.plan["artifacts"]["summary"]
        manifest = ROOT / self.plan["artifacts"]["manifest"]
        self.assertTrue(results.is_file())
        self.assertTrue(manifest.is_file())
        self.assertTrue(summary.is_file())
        self.assertEqual(
            sha256_path(results),
            "95fd21e01d30ad3ac520fc6eaf84f5e8223f062aaa561f7443e25b30302a8ec1",
        )
        self.assertEqual(
            sha256_path(summary),
            "9fab445f437ffe479128067dbfbef5c2dacff3e8ec3d8a093fed906956f19734",
        )
        self.assertEqual(
            sha256_path(manifest),
            "540631e7d10af4808340be65985fb46df2d43448b57b72a0c91ce57d6a958803",
        )

    def test_worker_has_broad_hash_scan_resume_and_race_guards(self):
        source = self.worker_source
        self.assertIn('DATA.rglob("*.jsonl")', source)
        self.assertIn("if not path.is_file()", source)
        self.assertIn("load_own_claims", source)
        self.assertIn("checkpoint is not an exact prefix", source)
        self.assertIn("checkpoint candidate fails exact recomputation", source)
        self.assertIn("orphan atomic claim", source)
        self.assertIn("os.link(temporary, path)", source)
        self.assertIn("orphan atomic claim changed during discriminant recovery", source)
        self.assertIn("Exact resume publication gate", source)
        self.assertIn("staged row changed immediately before manifest publication", source)
        self.assertIn("prior F5 plan/result boundary changed", source)
        self.assertGreaterEqual(
            source.count("SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?"), 4
        )
        self.assertGreaterEqual(source.count("live_target(connection"), 4)
        self.assertGreaterEqual(source.count("validate_mutable_boundaries()"), 4)

    def test_typed_arithmetic_miss_is_the_only_caught_derive_failure(self):
        source = self.worker_source
        self.assertIn("class NoUniqueDegreeTwelveFactor(ValueError)", source)
        self.assertIn('"resolved_no_unique_degree12_factor"', source)
        self.assertIn('"degreeTwelveSquarefreeFactorCount"', source)
        self.assertIn("checkpoint arithmetic miss fails exact recomputation", source)
        self.assertIn("unverified arithmetic miss reached resume publication", source)
        tree = ast.parse(source)
        caught = [
            node.type.id
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name)
        ]
        self.assertGreaterEqual(caught.count("NoUniqueDegreeTwelveFactor"), 3)
        # The sole generic ValueError handler belongs to integer parsing in
        # polynomial_hash; derive_candidate is caught only by the typed miss.
        self.assertEqual(caught.count("ValueError"), 1)

    def test_existing_checkpoint_is_preserved_and_resume_shape_is_valid(self):
        results = ROOT / self.plan["artifacts"]["results"]
        rows = [
            json.loads(line)
            for line in results.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        selected = self.plan["selectedSources"]
        self.assertEqual(len(rows), 50)
        self.assertEqual(
            [row["sourceCanonicalQuotientSha256"] for row in rows],
            [row["canonicalQuotientSha256"] for row in selected],
        )
        status_counts = {}
        for row in rows:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        self.assertEqual(
            status_counts,
            {
                "hit_staged": 3,
                "resolved_no_unique_degree12_factor": 1,
                "resolved_not_current_frozen_gold": 46,
            },
        )
        outside_planned = 0
        for row, selected_row in zip(rows, selected):
            if row["status"] == "resolved_no_unique_degree12_factor":
                self.assertNotIn("candidate", row)
                self.assertNotIn("target", row)
                self.assertEqual(
                    row["arithmeticOutcome"]["kind"],
                    "no_unique_degree12_factor",
                )
                continue
            self.assertEqual(int(row["candidate"]["r"]), int(row["target"]["r"]))
            planned = {
                (pair["label"], int(pair["r"]))
                for pair in selected_row["possibleGoldPairs"]
            }
            target = (row["target"]["label"], int(row["target"]["r"]))
            if target not in planned:
                outside_planned += 1
                self.assertEqual(row["status"], "resolved_not_current_frozen_gold")
        self.assertGreater(outside_planned, 0)
        claims_dir = ROOT / self.plan["artifacts"]["claimsDirectory"]
        self.assertEqual(len(list(claims_dir.glob("*.json"))), 3)
        manifest = ROOT / self.plan["artifacts"]["manifest"]
        self.assertEqual(len([line for line in manifest.read_text().splitlines() if line]), 3)

    def test_atomic_claim_is_complete_and_no_replace(self):
        tree = ast.parse(self.worker_source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "atomic_create"
        )
        namespace = {"Path": Path, "os": os, "tempfile": tempfile}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(WORKER_PATH), "exec"), namespace)
        atomic_create = namespace["atomic_create"]
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "claim.json"
            atomic_create(target, "complete\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "complete\n")
            with self.assertRaises(FileExistsError):
                atomic_create(target, "replacement\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "complete\n")
            self.assertEqual(list(Path(temporary).glob(".*.tmp")), [])

    def test_action_and_database_inputs_are_pinned(self):
        self.assertEqual(len(self.plan["artifacts"]["actionShards"]), 5)
        self.assertEqual(self.plan["frontier"]["dedupedActions"], 3771)
        self.assertIn("databaseSidecars", self.plan["artifacts"])
        gold = self.plan["artifacts"]["frozenGold"]
        self.assertEqual(
            gold["sha256"],
            "755d6f7f6cf6b38a52bbb0f3c3373ef36a957588644a5a90a71daed63cb02d80",
        )

    def test_shared_heavy_lock_is_free(self):
        lock_path = ROOT / "data" / ".low_contention_sequential.lock"
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()

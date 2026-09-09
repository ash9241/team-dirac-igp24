from __future__ import annotations

import hashlib
import itertools
import json
import unittest
from pathlib import Path

from f5_multiorbit_common import (
    canonical_quotient_line,
    canonical_quotient_sha256,
    dispatch_kind,
    filter_slot_assignments,
    label_assignments,
)


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "f5_multiorbit_frontier_20260722_wave1"
PLAN = DEST / "plan.json"
PREFLIGHT = DEST / "preflight.json"
FRONTIER = DEST / "frontier.jsonl"
BLOCKED = DEST / "blocked_multiblock_labels.json"
WORKER = ROOT / "run_f5_multiorbit_plan.sage.py"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class CommonDispatcherTests(unittest.TestCase):
    def test_canonical_quotient_identifies_reflection(self) -> None:
        direct = "3,-4,5,-6,1"
        reflected = "3,4,5,6,1"
        self.assertEqual(canonical_quotient_line(direct), canonical_quotient_line(reflected))
        self.assertEqual(
            canonical_quotient_sha256(direct), canonical_quotient_sha256(reflected)
        )
        expected = hashlib.sha256(
            canonical_quotient_line(direct).encode("utf-8")
        ).hexdigest()
        self.assertEqual(canonical_quotient_sha256(direct), expected)

    def test_joint_observation_can_leave_one_label_assignment(self) -> None:
        assignments = list(itertools.permutations(range(2)))
        observed = [(1, 1), (2,)]
        allowed = {((2,), (1, 1))}
        kept = filter_slot_assignments(assignments, observed, allowed)
        self.assertEqual(kept, [(1, 0)])
        self.assertEqual(label_assignments(kept, ["24T10", "24T20"]), {("24T20", "24T10")})

    def test_dispatch_kind_is_exact_label_multiset_property(self) -> None:
        same = [{"targetLabel": "24T1"}, {"targetLabel": "24T1"}]
        joint = [{"targetLabel": "24T1"}, {"targetLabel": "24T2"}]
        self.assertEqual(dispatch_kind(same), "same_target_label_multiset")
        self.assertEqual(dispatch_kind(joint), "joint_modular_profiles")


@unittest.skipUnless(PLAN.is_file(), "run the light-only frontier planner first")
class SealedFrontierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = PLAN.read_text(encoding="utf-8")
        cls.plan = json.loads(cls.plan_text)
        cls.preflight = read_json(PREFLIGHT)
        cls.frontier = read_jsonl(FRONTIER)
        cls.blocked = read_json(BLOCKED)

    def test_plan_is_coefficient_free_and_audit_gated(self) -> None:
        self.assertNotIn('"quotientLine"', self.plan_text)
        self.assertNotIn('"coefficientLine"', self.plan_text)
        self.assertFalse(self.plan["coefficientMaterialIncluded"])
        self.assertEqual(self.plan["status"], "awaiting_independent_audit")
        self.assertTrue(self.plan["execution"]["independentAuditRequired"])
        self.assertTrue(
            self.plan["execution"]["continuationAuditRequiredAfterPilot"]
        )
        self.assertFalse(self.plan["execution"]["submissionAuthorized"])
        worker_digest = hashlib.sha256(WORKER.read_bytes()).hexdigest()
        self.assertEqual(worker_digest, self.plan["artifacts"]["worker"]["sha256"])
        self.assertEqual(
            hashlib.sha256(PLAN.read_bytes()).hexdigest(),
            self.preflight["plan"]["sha256"],
        )

    def test_ledger_wal_is_hard_pinned_and_shm_is_informational(self) -> None:
        sidecars = self.plan["artifacts"]["databaseSidecars"]
        self.assertEqual(set(sidecars), {"wal", "shmInformationalAtSeal"})
        worker_source = WORKER.read_text(encoding="utf-8")
        self.assertIn("validate_database_sidecars", worker_source)
        self.assertIn("database_boundary", worker_source)
        self.assertIn("ledger/WAL changed during exact arithmetic", worker_source)

    def test_strict_system_gate_and_blocked_isolation(self) -> None:
        census = self.plan["structuralCensus"]
        self.assertEqual(census["rawSingleBlockSystemLabels"], 4374)
        self.assertEqual(census["blockedCoarseOnlyMultiBlockLabels"], 57)
        self.assertEqual(census["ambiguousMultiBlockLabels"], 932)
        self.assertEqual(self.blocked["count"], 57)
        self.assertEqual(len(self.blocked["labels"]), 57)
        self.assertFalse(self.blocked["executionEligibility"])
        self.assertTrue(all(row["systemCount"] > 1 for row in self.blocked["labels"]))
        self.assertEqual(len({row["label"] for row in self.blocked["labels"]}), 57)

    def test_every_saved_multi_action_cardinality_is_pinned(self) -> None:
        census = self.plan["structuralCensus"]
        self.assertEqual(census["savedActionRows"], 3771)
        self.assertEqual(census["safeSavedActionLabels"], 2576)
        self.assertEqual(census["safeUniqueActionLabels"], 1922)
        self.assertEqual(census["safeMultiActionLabels"], 654)
        self.assertEqual(census["safeMultiActions"], 1820)
        self.assertFalse(census["savedActionCorpusComplete"])

    def test_all_prior_exact_exclusions_are_sealed(self) -> None:
        exclusions = self.plan["priorExactExclusions"]
        self.assertEqual(exclusions["priorF5Artifacts"], 75)
        self.assertEqual(exclusions["priorF5Rows"], 428)
        self.assertEqual(exclusions["priorF5CanonicalHashes"], 391)
        self.assertEqual(exclusions["priorK2Rows"], 78)
        self.assertEqual(
            exclusions["priorK2CanonicalHashesIncludingInitialAndPost100"], 66
        )
        self.assertEqual(exclusions["allPriorCanonicalHashes"], 434)
        self.assertEqual(len(exclusions["excludedCanonicalSha256"]), 434)

    def test_full_ranked_frontier_counts(self) -> None:
        census = self.plan["frontierCensus"]
        self.assertEqual(census["frontierSources"], 39)
        self.assertEqual(census["frontierSourceLabels"], 13)
        self.assertEqual(census["frontierDistinctSourceSignatures"], 36)
        self.assertEqual(census["frontierSourceSignatureIncidences"], 53)
        self.assertEqual(census["frontierFactors"], 105)
        self.assertEqual(census["frontierGoldPairs"], 25)
        self.assertEqual(census["sameLabelSources"], 4)
        self.assertEqual(census["jointModularSources"], 35)
        self.assertEqual(self.frontier, self.plan["sources"])
        self.assertEqual(len(self.frontier), 39)
        hashes = [row["canonicalQuotientSha256"] for row in self.frontier]
        self.assertEqual(len(hashes), len(set(hashes)))
        self.assertEqual(sum(row["factorCount"] for row in self.frontier), 105)
        pairs = {
            (pair["label"], int(pair["r"]))
            for row in self.frontier
            for pair in row["possibleGoldPairs"]
        }
        self.assertEqual(len(pairs), 25)

    def test_canary_exercises_same_label_and_joint_dispatch(self) -> None:
        kinds = [row["dispatchKind"] for row in self.frontier[:8]]
        self.assertEqual(kinds[:4], ["same_target_label_multiset"] * 4)
        self.assertIn("joint_modular_profiles", kinds)
        self.assertEqual(set(kinds), {"same_target_label_multiset", "joint_modular_profiles"})

    def test_one_heavy_command_is_not_launchable_without_external_audit_hash(self) -> None:
        command = self.preflight["launchCommandAfterIndependentAudit"]
        self.assertIn("/usr/bin/caffeinate -i /usr/local/bin/sage -python", command)
        self.assertIn("--expected-plan-sha256", command)
        self.assertIn("--independent-audit", command)
        self.assertIn("--expected-audit-sha256 <AUDIT_SHA256>", command)
        self.assertIn("--maximum-sources 8", command)
        self.assertEqual(
            self.preflight["requiredIndependentAuditSchema"]["planSha256"],
            self.preflight["plan"]["sha256"],
        )
        continuation = self.preflight["requiredContinuationAuditSchema"]
        self.assertEqual(continuation["checkpointCompletedSources"], 8)
        self.assertEqual(
            continuation["schemaVersion"], "f5-multiorbit-continuation-audit-v1"
        )

    def test_worker_contains_fail_closed_exact_guards_and_no_api_client(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        for required in (
            "rebuild_exact_actions",
            "len(systems) != 1",
            "degree-12 factor count differs from exact action count",
            "UNRESOLVED_LABEL_ASSIGNMENT",
            "candidate.degree() != 24",
            "external claimed-pair boundary changed",
            "status='accepted' AND v.scoreable=1",
            "fcntl.LOCK_EX | fcntl.LOCK_NB",
            "independent-audit SHA256 differs from explicit launch pin",
            "--maximum-sources",
            "checkpointResultsSha256",
            "assert_manifest_unreceipted",
            "continuation beyond the sealed pilot requires",
            "validated_claim_pairs",
            "atomic claim set differs from exactly recomputed staged hits",
        ):
            self.assertIn(required, source)
        self.assertNotIn("import requests", source)
        self.assertNotIn("urllib.request", source)


if __name__ == "__main__":
    unittest.main()

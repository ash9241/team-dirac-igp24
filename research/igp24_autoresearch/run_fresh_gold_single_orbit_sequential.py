#!/usr/bin/env python3
"""Fail-closed sequential executor for the pinned current single-orbit tc0 frontier.

The default mode is a local audit.  Heavy arithmetic requires both
``--execute`` and ``--heavy-slot-confirmed``.  Execution is strictly
sequential and never performs network, staging, outbox, API, or submission
work.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import analyze_rank12_routes as shared
import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import fresh_t00134_pair_orbit_census as sole
import stage_single_exact_census as exact_census


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
FRONTIER = DATA / "fresh_current_gold_solo_pair_orbit_frontier_20260730.jsonl"
FRONTIER_SUMMARY = DATA / "fresh_current_gold_solo_pair_orbit_summary_20260730.json"
WORKER = ROOT / "pair_sum_one.sage.py"
CHECKPOINT_DIR = DATA / "fresh_gold_single_seq_checkpoints_20260730"
HITS = DATA / "fresh_gold_single_seq_hits_20260730.jsonl"
RUN_SUMMARY = DATA / "fresh_gold_single_seq_summary_20260730.json"
LOCK = DATA / ".fresh_gold_single_seq.lock"

FRONTIER_SHA256 = "5580e1dfbba021e15ead5412721172b8760a08a818cfac053f1d6753f8eccf1f"
FRONTIER_SUMMARY_SHA256 = (
    "e7364a0742bf6b621e0931082d1f5fc3fd8fe9ba77914803666be9b6ef4bf587"
)
WORKER_SHA256 = "f700021633d32e822ef4191760dad77d0e7230c3c29a19a0c63c0464c784c56e"
EXPECTED_FRONTIER_ROWS = 760
EXPECTED_SINGLE_GOLD_ROWS = 225
HISTORY_ALPHA = 2.0


class GuardFailure(RuntimeError):
    """A fail-closed invariant did not hold."""


def sha256_path(path: Path) -> str:
    return shared.sha256_file(path)


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--heavy-slot-confirmed",
        action="store_true",
        help="second explicit gate required with --execute",
    )
    parser.add_argument("--route-id", action="append", default=[])
    parser.add_argument("--max-routes", type=int, default=1)
    parser.add_argument("--max-wall-seconds", type=float, default=300.0)
    parser.add_argument("--max-hits", type=int, default=1)
    parser.add_argument("--worker-timeout", type=float, default=600.0)
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=2000,
        help="skip oversized source presentations whose pair resolvents are poor throughput",
    )
    parser.add_argument("--audit-limit", type=int, default=25)
    return parser.parse_args()


def validate_pins() -> dict:
    pins = {
        FRONTIER: FRONTIER_SHA256,
        FRONTIER_SUMMARY: FRONTIER_SUMMARY_SHA256,
        WORKER: WORKER_SHA256,
    }
    result = {}
    for path, expected in pins.items():
        actual = sha256_path(path)
        if actual != expected:
            raise GuardFailure(
                f"pinned artifact drift: {relative(path)}={actual}, expected={expected}"
            )
        result[relative(path)] = {"path": relative(path), "sha256": actual}
    return result


def route_id(row: dict) -> str:
    return (
        f"fgs_{int(row['priorityRank']):04d}_"
        f"{int(row['sourceT'])}_r{int(row['sourceR'])}_"
        f"{str(row['sourceCoefficientSha256'])[:12]}"
    )


def result_path(row: dict) -> Path:
    return DATA / f"{route_id(row)}_result.jsonl"


def checkpoint_path(row: dict) -> Path:
    return CHECKPOINT_DIR / f"{route_id(row)}.json"


def load_frontier() -> list[dict]:
    rows = shared.read_jsonl(FRONTIER)
    if (
        len(rows) != EXPECTED_FRONTIER_ROWS
        or [int(row["priorityRank"]) for row in rows]
        != list(range(1, EXPECTED_FRONTIER_ROWS + 1))
    ):
        raise GuardFailure("frontier count/rank sequence changed")
    selected = [
        row
        for row in rows
        if int(row["length24OrbitCount"]) == 1
        and int(row["reachableGoldPairCount"]) > 0
        and row.get("alreadyPairTested") is False
    ]
    if len(selected) != EXPECTED_SINGLE_GOLD_ROWS:
        raise GuardFailure(
            f"single-orbit gold frontier has {len(selected)} rows, "
            f"expected {EXPECTED_SINGLE_GOLD_ROWS}"
        )
    source_hashes = [str(row["sourceCoefficientSha256"]) for row in selected]
    if len(source_hashes) != len(set(source_hashes)):
        raise GuardFailure("single-orbit gold frontier repeats a source hash")
    return selected


def validate_action_routes(rows: list[dict]) -> dict[str, dict]:
    actions, provenance, _artifacts = pair_routes.load_sealed_actions()
    checked = {}
    for row in rows:
        label = str(row["sourceLabel"])
        action = actions.get(label)
        if action is None:
            raise GuardFailure(f"sealed action missing for {label}")
        targets = action.get("targets") or []
        if (
            int(action.get("length24OrbitCount", -1)) != 1
            or len(targets) != 1
            or int(targets[0].get("orbitSize", -1)) != 24
            or int(targets[0].get("kernelOrder", -1)) != 1
            or str(targets[0].get("targetLabel"))
            not in {str(pair["label"]) for pair in row["reachableGoldPairs"]}
            or row.get("actionArtifact") != provenance.get(label)
        ):
            raise GuardFailure(f"single faithful action/frontier mismatch for {label}")
        checked[route_id(row)] = action
    return checked


def historical_results(rows: list[dict]) -> tuple[dict[tuple[str, int, str], dict], dict]:
    current_gold: dict[tuple[str, int, str], set[tuple[str, int]]] = {}
    for row in rows:
        key = (str(row["sourceLabel"]), int(row["sourceR"]), str(
            (row.get("reachableGoldPairs") or [])[0]["label"]
        ))
        current_gold.setdefault(key, set()).update(
            (str(pair["label"]), int(pair["r"]))
            for pair in row.get("reachableGoldPairs") or []
        )

    seen = set()
    strata: dict[tuple[str, int, str], dict] = defaultdict(
        lambda: {"attempts": 0, "goldHits": 0}
    )
    target_attempts: Counter[str] = Counter()
    source_submission_attempts: Counter[str] = Counter()
    scanned = 0
    for path in sorted(DATA.rglob("*")):
        if (
            not path.is_file()
            or path.suffix not in (".json", ".jsonl")
            or not any(
                marker in path.name.lower()
                for marker in (
                    "pair",
                    "resolvent",
                    "frontier_candidates",
                    "live_gold_route",
                )
            )
        ):
            continue
        for result in sole.iter_file_dicts(path):
            if (
                result.get("status") != "certified"
                or result.get("sourceLabel") is None
                or result.get("sourceR") is None
                or result.get("targetLabel") is None
                or result.get("targetR") is None
                or result.get("sourceCoefficientSha256") is None
                or result.get("coefficientSha256") is None
            ):
                continue
            orbit = result.get("orbitCertificate") or {}
            actual = sorted(int(value) for value in orbit.get("actualDegrees") or [])
            expected = sorted(
                int(value) for value in orbit.get("expectedDegrees") or []
            )
            if (
                not actual
                or actual != expected
                or actual.count(24) != 1
                or any(int(value) != 1 for value in orbit.get("exponents") or [])
            ):
                continue
            identity = (
                str(result["sourceCoefficientSha256"]),
                str(result["coefficientSha256"]),
                int(result["targetR"]),
            )
            if identity in seen:
                continue
            seen.add(identity)
            scanned += 1
            key = (
                str(result["sourceLabel"]),
                int(result["sourceR"]),
                str(result["targetLabel"]),
            )
            strata[key]["attempts"] += 1
            actual_pair = (str(result["targetLabel"]), int(result["targetR"]))
            if actual_pair in current_gold.get(key, set()):
                strata[key]["goldHits"] += 1
            target_attempts[key[2]] += 1
            submission = str(result.get("sourceSubmissionId") or "")
            if submission:
                source_submission_attempts[submission] += 1
    return dict(strata), {
        "deduplicatedCertifiedSingleOrbitPackets": scanned,
        "targetAttempts": target_attempts,
        "sourceSubmissionAttempts": source_submission_attempts,
    }


def rank_routes(rows: list[dict]) -> tuple[list[dict], dict]:
    strata, history = historical_results(rows)
    ranked = []
    for row in rows:
        target_label = str((row["reachableGoldPairs"] or [])[0]["label"])
        key = (str(row["sourceLabel"]), int(row["sourceR"]), target_label)
        observed = strata.get(key, {"attempts": 0, "goldHits": 0})
        attempts = int(observed["attempts"])
        hits = int(observed["goldHits"])
        theory = float(row["estimatedAnyGoldHitProbability"])
        smoothed = (hits + HISTORY_ALPHA * theory) / (
            attempts + HISTORY_ALPHA
        )
        enriched = dict(row)
        enriched["executorRouteId"] = route_id(row)
        enriched["historyEvidence"] = {
            "stratum": {
                "sourceLabel": key[0],
                "sourceR": key[1],
                "targetLabel": key[2],
            },
            "attempts": attempts,
            "goldHitsAgainstCurrentFrontier": hits,
            "theoreticalGoldProbability": theory,
            "alpha": HISTORY_ALPHA,
            "bayesianSmoothedGoldProbability": smoothed,
            "novelStratum": attempts == 0,
            "targetHistoricalAttempts": int(
                history["targetAttempts"].get(target_label, 0)
            ),
            "sourceSubmissionHistoricalAttempts": int(
                history["sourceSubmissionAttempts"].get(
                    str(row["submissionId"]), 0
                )
            ),
            "acceptedProvenanceCount": int(row["acceptedProvenanceCount"]),
            "estimatedGoldPerSecondProxy": smoothed
            / (1.8935 + int(row["sourceCoefficientBytes"]) / 1000.0),
        }
        ranked.append(enriched)
    ranked.sort(
        key=lambda row: (
            int(row["historyEvidence"]["attempts"] != 0),
            -float(
                row["historyEvidence"]["estimatedGoldPerSecondProxy"]
            ),
            int(row["historyEvidence"]["targetHistoricalAttempts"]),
            int(row["historyEvidence"]["sourceSubmissionHistoricalAttempts"]),
            int(row["historyEvidence"]["acceptedProvenanceCount"]),
            int(row["sourceCoefficientBytes"]),
            int(row["priorityRank"]),
            str(row["sourceCoefficientSha256"]),
        )
    )
    for rank, row in enumerate(ranked, start=1):
        row["executorRank"] = rank
    return ranked, {
        "deduplicatedCertifiedSingleOrbitPackets": history[
            "deduplicatedCertifiedSingleOrbitPackets"
        ],
        "strata": len(strata),
        "ranking": (
            "unseen (sourceLabel,sourceR,targetLabel) strata first; then "
            "(historicalGoldHits + alpha*exactTheory)/(nHistory+alpha), alpha=2; "
            "divided by a fixed-overhead-plus-source-bytes runtime proxy; then "
            "target/source-receipt novelty, coefficient bytes, frontier rank"
        ),
    }


def connect_ro() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def local_boundary() -> dict:
    with connect_ro() as connection:
        snapshot = pair_routes.load_ledger_snapshot(connection)
        candidates = pair_routes.exact_candidate_and_receipt_snapshot(connection)
        supplemental, _artifacts = outbox_audit.supplemental_exact_pair_index()
        pair_index = outbox_audit.merged_pair_index(candidates, supplemental)
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(pair_index, connection)
        )
    gold = {
        pair
        for pair, state in snapshot["targets"].items()
        if int(state["teamCount"]) == 0 and state["discovered"] is False
    }
    eligible_gold = (
        gold
        - snapshot["baseline"]
        - snapshot["knownPairs"]
        - candidates["receiptPairs"]
        - outbox_pairs
    )
    return {
        "snapshot": snapshot,
        "receiptHashes": candidates["receiptHashes"],
        "receiptPairs": candidates["receiptPairs"],
        "knownCandidateHashes": candidates["knownCandidateHashes"],
        "outboxHashes": outbox_hashes,
        "outboxPairs": outbox_pairs,
        "eligibleGold": eligible_gold,
        "audit": {
            "eligibleGoldPairs": len(eligible_gold),
            "receiptHashes": len(candidates["receiptHashes"]),
            "receiptPairs": len(candidates["receiptPairs"]),
            "outboxHashes": len(outbox_hashes),
            "outboxPairs": len(outbox_pairs),
            "outboxArtifactIndexSha256": outbox_meta.get("artifactIndexSha256"),
        },
    }


def source_row(connection: sqlite3.Connection, row: dict) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT p.coefficient_hash,p.original_line,v.status,v.scoreable,
               v.label,v.t,v.r,v.disc_source,v.field_disc_abs
        FROM polynomials AS p JOIN verifications AS v
        USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (str(row["submissionId"]), int(row["polynomialIndex"])),
    ).fetchone()


def validate_source(
    row: dict, connection: sqlite3.Connection | None = None
) -> None:
    owned_connection = connection is None
    if connection is None:
        connection = connect_ro()
    try:
        current = source_row(connection, row)
    finally:
        if owned_connection:
            connection.close()
    if (
        current is None
        or str(current["coefficient_hash"])
        != str(row["sourceCoefficientSha256"])
        or str(current["status"]) != "accepted"
        or int(current["scoreable"] or 0) != 1
        or str(current["label"]) != str(row["sourceLabel"])
        or int(current["t"]) != int(row["sourceT"])
        or int(current["r"]) != int(row["sourceR"])
        or str(current["disc_source"]) != "exact_nfdisc"
        or str(current["field_disc_abs"]) != str(row["sourceFieldDiscAbs"])
        or len(str(current["original_line"]).encode("utf-8"))
        != int(row["sourceCoefficientBytes"])
    ):
        raise GuardFailure(
            f"accepted exact source changed: {row['executorRouteId']}"
        )


def tested_sources(rows: list[dict]) -> tuple[set[str], dict[str, list[str]]]:
    presentation = {
        (str(row["submissionId"]), int(row["polynomialIndex"])): str(
            row["sourceCoefficientSha256"]
        )
        for row in rows
    }
    return sole.tested_source_hashes(presentation)


def audit_route(
    row: dict,
    action: dict,
    boundary: dict,
    tested: set[str],
    tested_evidence: dict[str, list[str]],
    connection: sqlite3.Connection,
) -> dict:
    status = "ready"
    reasons = []
    try:
        validate_source(row, connection)
    except GuardFailure as exc:
        reasons.append(str(exc))
    digest = str(row["sourceCoefficientSha256"])
    output = result_path(row)
    temporary = output.with_suffix(output.suffix + ".tmp")
    checkpoint = checkpoint_path(row)
    if digest in tested:
        reasons.append("source_hash_already_pair_tested")
    current_gold = sorted(
        {
            (str(pair["label"]), int(pair["r"]))
            for pair in row["reachableGoldPairs"]
        }
        & boundary["eligibleGold"]
    )
    if not current_gold:
        reasons.append("no_reachable_pair_is_currently_eligible_gold")
    if temporary.exists():
        reasons.append("worker_temporary_output_exists")
    if checkpoint.exists():
        status = "checkpoint_present"
    elif output.exists():
        status = "recoverable_uncheckpointed_result"
    elif reasons:
        status = "blocked_fail_closed"
    return {
        "routeId": row["executorRouteId"],
        "executorRank": int(row["executorRank"]),
        "frontierRank": int(row["priorityRank"]),
        "source": (
            f"{row['sourceLabel']}/r{int(row['sourceR'])}"
        ),
        "targetLabel": str(action["targets"][0]["targetLabel"]),
        "currentReachableGoldPairs": [
            {"label": pair[0], "r": pair[1]} for pair in current_gold
        ],
        "status": status,
        "reasons": reasons,
        "testedEvidence": tested_evidence.get(digest, []),
        "output": relative(output),
        "checkpoint": relative(checkpoint),
        "historyEvidence": row["historyEvidence"],
    }


def command(row: dict, action: dict) -> list[str]:
    action_path = ROOT / str(row["actionArtifact"]["path"])
    output = result_path(row)
    return [
        "/usr/local/bin/sage",
        "-python",
        "pair_sum_one.sage.py",
        str(row["submissionId"]),
        str(int(row["polynomialIndex"])),
        "--orbit-map",
        relative(action_path),
        "--expected-source-hash",
        str(row["sourceCoefficientSha256"]),
        "--expected-target",
        str(action["targets"][0]["targetLabel"]),
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
        "--nfdisc",
        "--output-jsonl",
        relative(output),
    ]


def one_jsonl(path: Path) -> dict:
    rows = shared.read_jsonl(path)
    if len(rows) != 1:
        raise GuardFailure(f"expected one result row: {relative(path)}")
    return rows[0]


def validate_result(row: dict, action: dict) -> dict:
    path = result_path(row)
    result = one_jsonl(path)
    orbit = result.get("orbitCertificate") or {}
    actual = sorted(int(value) for value in orbit.get("actualDegrees") or [])
    expected = sorted(int(value) for value in orbit.get("expectedDegrees") or [])
    action_targets = [
        {
            "orbitIndex": int(target["orbitIndex"]),
            "orbitSize": int(target["orbitSize"]),
            "targetLabel": str(target["targetLabel"]),
            "targetT": int(target["targetT"]),
            "kernelOrder": int(target["kernelOrder"]),
        }
        for target in action["targets"]
    ]
    result_targets = [
        {
            "orbitIndex": int(target["orbitIndex"]),
            "orbitSize": int(target["orbitSize"]),
            "targetLabel": str(target["targetLabel"]),
            "targetT": int(target["targetT"]),
            "kernelOrder": int(target["kernelOrder"]),
        }
        for target in result.get("orbitTargets") or []
    ]
    line = exact_census.canonical_polynomial_line(result.get("coefficientLine"))
    if line is None:
        raise GuardFailure("worker candidate is not canonical primitive monic degree 24")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if (
        result.get("status") != "certified"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != str(row["submissionId"])
        or int(result.get("sourcePolynomialIndex", -1))
        != int(row["polynomialIndex"])
        or str(result.get("sourceCoefficientSha256"))
        != str(row["sourceCoefficientSha256"])
        or str(result.get("sourceLabel")) != str(row["sourceLabel"])
        or int(result.get("sourceR", -1)) != int(row["sourceR"])
        or str(result.get("targetLabel"))
        != str(action["targets"][0]["targetLabel"])
        or int(result.get("targetT", -1))
        != int(action["targets"][0]["targetT"])
        or result_targets != action_targets
        or actual != expected
        or actual != sorted(int(value) for value in action["orbitSizes"])
        or actual.count(24) != 1
        or any(int(value) != 1 for value in orbit.get("exponents") or [])
        or digest != str(result.get("coefficientSha256"))
        or int(result.get("fieldDiscriminantAbs", 0)) <= 0
        or int(result.get("polynomialDiscriminantAbs", 0)) <= 0
        or str(result.get("reduction")) != "best"
        or int((result.get("transform") or {}).get("c", 0))
        not in {1, 2, 3, 5, 7}
    ):
        raise GuardFailure(f"exact worker result validation failed: {route_id(row)}")
    return {
        "candidateSha256": digest,
        "candidateFieldDiscriminantAbs": str(result["fieldDiscriminantAbs"]),
        "candidatePolynomialDiscriminantAbs": str(
            result["polynomialDiscriminantAbs"]
        ),
        "targetPair": (
            str(result["targetLabel"]),
            int(result["targetR"]),
        ),
        "resultSha256": sha256_path(path),
        "workerElapsedSeconds": float(result["elapsedSeconds"]),
    }


def postflight(row: dict, candidate: dict, boundary: dict) -> dict:
    pair = candidate["targetPair"]
    digest = candidate["candidateSha256"]
    state = boundary["snapshot"]["targets"].get(pair)
    reasons = []
    if digest in boundary["knownCandidateHashes"]:
        reasons.append("candidate_hash_in_ledger")
    if digest in boundary["receiptHashes"]:
        reasons.append("candidate_hash_in_receipt")
    if digest in boundary["outboxHashes"]:
        reasons.append("candidate_hash_in_outbox")
    if pair in boundary["snapshot"]["baseline"]:
        reasons.append("target_pair_baseline")
    if pair in boundary["snapshot"]["knownPairs"]:
        reasons.append("target_pair_locally_known")
    if pair in boundary["receiptPairs"]:
        reasons.append("target_pair_in_receipt")
    if pair in boundary["outboxPairs"]:
        reasons.append("target_pair_in_outbox")
    is_gold = (
        state is not None
        and int(state["teamCount"]) == 0
        and state["discovered"] is False
    )
    if not is_gold:
        reasons.append("actual_target_pair_not_current_tc0_gold")
    status = (
        "certified_novel_gold_hit"
        if not reasons
        else "certified_exact_not_current_novel_gold"
    )
    return {
        "status": status,
        "goldHit": status == "certified_novel_gold_hit",
        "candidateNovel": not any(
            reason.startswith("candidate_hash_") for reason in reasons
        ),
        "targetCurrentGold": is_gold,
        "reasons": reasons,
        "targetState": state,
    }


def exclusive_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise GuardFailure(f"refusing to overwrite checkpoint: {relative(path)}")
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def checkpoints() -> list[dict]:
    if not CHECKPOINT_DIR.exists():
        return []
    rows = []
    route_ids = set()
    for path in sorted(CHECKPOINT_DIR.glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        route = str(row.get("routeId"))
        if (
            row.get("schemaVersion") != "fresh-gold-single-seq-route-checkpoint-v1"
            or route in route_ids
            or row.get("frontierSha256") != FRONTIER_SHA256
        ):
            raise GuardFailure(f"invalid/duplicate checkpoint: {relative(path)}")
        route_ids.add(route)
        rows.append(row)
    return rows


def refresh_derived(checkpoint_rows: list[dict], stop_reason: str | None) -> None:
    hits = [
        {
            "routeId": row["routeId"],
            "source": row["source"],
            "target": row["target"],
            "candidateSha256": row["candidate"]["coefficientSha256"],
            "fieldDiscriminantAbs": row["candidate"]["fieldDiscriminantAbs"],
            "result": row["result"],
            "checkpoint": row["checkpoint"],
        }
        for row in checkpoint_rows
        if row.get("status") == "certified_novel_gold_hit"
    ]
    shared.write_jsonl_atomic(
        HITS, hits, sort_key=lambda row: str(row["routeId"])
    )
    atomic_json(
        RUN_SUMMARY,
        {
            "schemaVersion": "fresh-gold-single-seq-summary-v1",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "status": "local_only_no_network_no_stage_no_submission",
            "frontier": {"path": relative(FRONTIER), "sha256": FRONTIER_SHA256},
            "routeCheckpoints": len(checkpoint_rows),
            "goldHits": len(hits),
            "stopReason": stop_reason,
            "outputs": {
                "checkpointDirectory": relative(CHECKPOINT_DIR),
                "goldHits": relative(HITS),
                "summary": relative(RUN_SUMMARY),
            },
            "sideEffects": {
                "networkCalls": 0,
                "stagingCalls": 0,
                "submissionCalls": 0,
            },
        },
    )


def heavy_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise GuardFailure("another fresh-gold sequential executor holds the lock") from exc
    return handle


def run_worker(row: dict, action: dict, timeout: float) -> bool:
    output = result_path(row)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if output.exists() or temporary.exists():
        raise GuardFailure("worker output/temporary path is not new")
    # This managed environment denies process-table inspection.  Mutual
    # exclusion therefore rests on this executor's nonblocking file lock plus
    # the explicit --heavy-slot-confirmed handoff.  Root must set that gate
    # only after every agent has confirmed that its current worker is clear.
    try:
        completed = subprocess.run(
            command(row, action),
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        if output.exists() or temporary.exists():
            raise GuardFailure(
                "timed-out worker left an output or temporary artifact"
            )
        return False
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
        raise GuardFailure(
            f"pair worker failed exit={completed.returncode}: {diagnostic[0][:300]}"
        )
    if not output.is_file() or temporary.exists():
        raise GuardFailure("pair worker did not atomically create its result")
    return True


def checkpoint_timeout(row: dict, timeout: float) -> dict:
    checkpoint = checkpoint_path(row)
    value = {
        "schemaVersion": "fresh-gold-single-seq-route-checkpoint-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "frontierSha256": FRONTIER_SHA256,
        "routeId": row["executorRouteId"],
        "executorRank": int(row["executorRank"]),
        "frontierRank": int(row["priorityRank"]),
        "status": "worker_timeout_no_result",
        "source": {
            "submissionId": str(row["submissionId"]),
            "polynomialIndex": int(row["polynomialIndex"]),
            "label": str(row["sourceLabel"]),
            "r": int(row["sourceR"]),
            "coefficientSha256": str(row["sourceCoefficientSha256"]),
        },
        "timeoutSeconds": float(timeout),
        "result": None,
        "candidate": None,
        "target": None,
        "historyEvidence": row["historyEvidence"],
        "checkpoint": relative(checkpoint),
        "sideEffects": {
            "sageWorkers": 1,
            "networkCalls": 0,
            "stagingCalls": 0,
            "submissionCalls": 0,
        },
    }
    exclusive_json(checkpoint, value)
    return value


def checkpoint_one(row: dict, candidate: dict, post: dict) -> dict:
    output = result_path(row)
    checkpoint = checkpoint_path(row)
    value = {
        "schemaVersion": "fresh-gold-single-seq-route-checkpoint-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "frontierSha256": FRONTIER_SHA256,
        "routeId": row["executorRouteId"],
        "executorRank": int(row["executorRank"]),
        "frontierRank": int(row["priorityRank"]),
        "status": post["status"],
        "source": {
            "submissionId": str(row["submissionId"]),
            "polynomialIndex": int(row["polynomialIndex"]),
            "label": str(row["sourceLabel"]),
            "r": int(row["sourceR"]),
            "coefficientSha256": str(row["sourceCoefficientSha256"]),
        },
        "target": {
            "label": candidate["targetPair"][0],
            "r": candidate["targetPair"][1],
            "goldHit": bool(post["goldHit"]),
            "localState": post["targetState"],
        },
        "candidate": {
            "coefficientSha256": candidate["candidateSha256"],
            "fieldDiscriminantAbs": candidate[
                "candidateFieldDiscriminantAbs"
            ],
            "polynomialDiscriminantAbs": candidate[
                "candidatePolynomialDiscriminantAbs"
            ],
            "coefficientMaterialIncluded": False,
        },
        "result": {
            "path": relative(output),
            "sha256": candidate["resultSha256"],
            "workerElapsedSeconds": candidate["workerElapsedSeconds"],
        },
        "postflight": post,
        "historyEvidence": row["historyEvidence"],
        "checkpoint": relative(checkpoint),
        "sideEffects": {
            "sageWorkers": 1,
            "networkCalls": 0,
            "stagingCalls": 0,
            "submissionCalls": 0,
        },
    }
    exclusive_json(checkpoint, value)
    return value


def select_requested(rows: list[dict], requested: list[str]) -> list[dict]:
    if not requested:
        return rows
    by_id = {row["executorRouteId"]: row for row in rows}
    if (
        len(requested) != len(set(requested))
        or any(route not in by_id for route in requested)
    ):
        raise GuardFailure("requested route id is duplicated or absent")
    return [by_id[route] for route in requested]


def main() -> int:
    args = arguments()
    if (
        args.max_routes < 1
        or args.max_wall_seconds <= 0
        or args.max_hits < 1
        or args.worker_timeout <= 0
        or args.max_source_bytes < 1
        or args.audit_limit < 1
    ):
        raise GuardFailure("route/wall/hit/timeout/audit limits must be positive")
    if args.execute != args.heavy_slot_confirmed:
        raise GuardFailure(
            "heavy execution requires both --execute and --heavy-slot-confirmed"
        )

    pins = validate_pins()
    frontier = load_frontier()
    actions = validate_action_routes(frontier)
    ranked, history_summary = rank_routes(frontier)
    ranked = select_requested(ranked, args.route_id)
    completed_rows = checkpoints()
    completed = {str(row["routeId"]) for row in completed_rows}
    boundary = local_boundary()
    tested, tested_evidence = tested_sources(ranked)
    with connect_ro() as audit_connection:
        audits = [
            audit_route(
                row,
                actions[row["executorRouteId"]],
                boundary,
                tested,
                tested_evidence,
                audit_connection,
            )
            for row in ranked
        ]

    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "audit_only_no_sage_gap_network_stage_or_submission",
                    "pins": pins,
                    "frontier": {
                        "allRows": EXPECTED_FRONTIER_ROWS,
                        "singleOrbitGoldRows": len(frontier),
                        "dynamicallyPairTestedSourceHashes": len(tested),
                    },
                    "historyModel": history_summary,
                    "boundary": boundary["audit"],
                    "checkpointRows": len(completed_rows),
                    "readyRoutes": sum(
                        row["status"] == "ready" for row in audits
                    ),
                    "blockedRoutes": sum(
                        row["status"] == "blocked_fail_closed" for row in audits
                    ),
                    "recoverableRoutes": sum(
                        row["status"] == "recoverable_uncheckpointed_result"
                        for row in audits
                    ),
                    "topRoutes": audits[: args.audit_limit],
                    "executeCliTemplate": (
                        "python3 run_fresh_gold_single_orbit_sequential.py "
                        "--execute --heavy-slot-confirmed --max-routes 100 "
                        "--max-wall-seconds 9000 --max-hits 3"
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    audit_by_id = {row["routeId"]: row for row in audits}
    start = time.monotonic()
    new_hits = 0
    attempted = 0
    stop_reason = "frontier_exhausted"
    lock_handle = heavy_lock()
    try:
        for row in ranked:
            route = row["executorRouteId"]
            if route in completed:
                continue
            audit = audit_by_id[route]
            output = result_path(row)
            if audit["status"] == "blocked_fail_closed":
                continue
            if int(row["sourceCoefficientBytes"]) > args.max_source_bytes:
                continue
            if attempted >= args.max_routes:
                stop_reason = "max_routes"
                break
            if new_hits >= args.max_hits:
                stop_reason = "max_hits"
                break
            remaining = args.max_wall_seconds - (time.monotonic() - start)
            if remaining <= 0:
                stop_reason = "max_wall_seconds"
                break

            validate_source(row)
            if not output.exists():
                possible = {
                    (str(pair["label"]), int(pair["r"]))
                    for pair in row["reachableGoldPairs"]
                }
                if (
                    str(row["sourceCoefficientSha256"]) in tested
                    or not (possible & boundary["eligibleGold"])
                ):
                    continue
                remaining = args.max_wall_seconds - (time.monotonic() - start)
                if remaining <= 0:
                    stop_reason = "max_wall_seconds"
                    break
                worker_completed = run_worker(
                    row,
                    actions[route],
                    min(args.worker_timeout, max(1.0, remaining)),
                )
                if not worker_completed:
                    checkpoint = checkpoint_timeout(
                        row,
                        min(args.worker_timeout, max(1.0, remaining)),
                    )
                    completed_rows.append(checkpoint)
                    completed.add(route)
                    tested.add(str(row["sourceCoefficientSha256"]))
                    attempted += 1
                    refresh_derived(completed_rows, None)
                    continue
            candidate = validate_result(row, actions[route])
            # This process owns the sole heavy slot and has no network,
            # staging, outbox, receipt, or submission path.  The complete
            # local boundary was audited immediately before the lock was
            # acquired; re-scanning roughly 2,000 artifacts twice per route
            # adds about 46 seconds without exposing new state during this
            # exclusive run.  Reserve each result below so later routes still
            # cannot duplicate a candidate or target pair.
            post = postflight(row, candidate, boundary)
            checkpoint = checkpoint_one(row, candidate, post)
            completed_rows.append(checkpoint)
            completed.add(route)
            tested.add(str(row["sourceCoefficientSha256"]))
            boundary["knownCandidateHashes"].add(candidate["candidateSha256"])
            boundary["snapshot"]["knownPairs"].add(candidate["targetPair"])
            boundary["eligibleGold"].discard(candidate["targetPair"])
            attempted += 1
            if post["goldHit"]:
                new_hits += 1
            refresh_derived(completed_rows, None)
    finally:
        lock_handle.close()

    refresh_derived(completed_rows, stop_reason)
    print(
        json.dumps(
            {
                "status": "completed_local_only_no_network_stage_or_submission",
                "attemptedSequentially": attempted,
                "newGoldHits": new_hits,
                "elapsedWallSeconds": round(time.monotonic() - start, 3),
                "stopReason": stop_reason,
                "checkpointDirectory": relative(CHECKPOINT_DIR),
                "goldHitList": relative(HITS),
                "summary": relative(RUN_SUMMARY),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        GuardFailure,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

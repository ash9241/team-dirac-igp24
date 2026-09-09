#!/usr/bin/env python3
"""Fail-closed light preflight/stager for conditional pair-factor tasks.

The preflight permits unrelated boundary growth but requires every sealed gold
option for the selected task to remain current, unknown, nonbaseline, and
absent from every receipt/outbox.  Staging accepts only an exact isolated row
or an exact resolved Frobenius assignment.  Exact non-gold outcomes produce a
coefficient-free miss certificate and no manifest.  Nothing is submitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper
import stage_frobenius_gold as frobenius
import stage_single_exact_census as exact


ROOT = audit.ROOT
DATA = audit.DATA
DB = audit.DB
REQUIRED_CHECKS = {
    "parentArtifactsPinned",
    "completedFactorOutputsExcluded",
    "safeOutcomePairAndHashExcluded",
    "allCurrentGoldTargetsFresh",
    "allSourcesAcceptedScoreableAndPinned",
    "batchSourceDeduplicated",
    "taskPathsUniqueAndAbsent",
    "completeOrbitTargetsPinned",
    "serialFailClosedCommandsPresent",
    "coefficientAndCredentialPayloadOmitted",
    "noWorkerNetworkSubmissionOrLedgerWrite",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--frobenius-certificate", type=Path)
    parser.add_argument("--preflight", action="store_true")
    return parser.parse_args()


def resolve(value: object, *, data_only: bool = False) -> Path:
    path = Path(str(value)).expanduser()
    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    root = DATA.resolve() if data_only else ROOT.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path escaped allowed root: {path}")
    return path


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"non-object JSONL row: {path}")
    return rows


def atomic_exclusive(path: Path, payload: bytes, mode: int = 0o600) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite staged artifact: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def pair(value: str) -> tuple[str, int]:
    label, raw_r = value.rsplit("/r", 1)
    return label, int(raw_r)


def record_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_task(plan_path: Path, task_id: str) -> tuple[dict, dict]:
    plan = read_json(plan_path)
    checks = plan.get("checks")
    if (
        plan.get("schemaVersion") != "conditional-gold-factor-top-batch-v1"
        or plan.get("status") != "ready_serial_fail_closed_no_worker_launched"
        or plan.get("coefficientMaterialIncluded") is not False
        or plan.get("credentialMaterialIncluded") is not False
        or plan.get("submissionAuthorized") is not False
        or not isinstance(checks, dict)
        or set(checks) != REQUIRED_CHECKS
        or any(value is not True for value in checks.values())
    ):
        raise ValueError("conditional batch plan is not intact")
    matches = [row for row in plan.get("tasks") or [] if row.get("taskId") == task_id]
    if len(matches) != 1:
        raise ValueError("task id is absent or nonunique")
    task = matches[0]
    artifacts = plan.get("artifacts") or {}
    for name in (
        "fullGoldAudit",
        "conditionalFactorRunbooks",
        "factorWorker",
        "frobeniusWorker",
        "stager",
        "safeOutcomeResult",
        "safeOutcomeStage",
    ):
        row = artifacts.get(name)
        if not isinstance(row, dict):
            raise ValueError(f"plan artifact is absent: {name}")
        path = resolve(row["path"])
        if helper.sha256_path(path) != row["sha256"]:
            raise ValueError(f"pinned plan artifact changed: {name}")
    action = resolve(task["actionArtifact"]["path"])
    if helper.sha256_path(action) != task["actionArtifact"]["sha256"]:
        raise ValueError("task action artifact changed")
    runbooks = read_json(resolve(artifacts["conditionalFactorRunbooks"]["path"]))
    source_rows = [
        row
        for row in runbooks.get("runbooks") or []
        if row.get("factorTaskId") == task_id
    ]
    if (
        len(source_rows) != 1
        or record_sha256(source_rows[0]) != task.get("originalFactorTaskRecordSha256")
    ):
        raise ValueError("task identity differs from pinned conditional runbook")
    full = read_json(resolve(artifacts["fullGoldAudit"]["path"]))
    full_routes = {
        row["routeId"]: row
        for row in (full.get("classifications") or {}).get("conditionalRoutes") or []
    }
    for route in task.get("selectedRoutes") or []:
        original = full_routes.get(route.get("routeId"))
        if (
            original is None
            or record_sha256(original) != route.get("fullAuditRouteRecordSha256")
        ):
            raise ValueError("selected route differs from pinned full audit")
    return plan, task


def current_exclusions(connection: sqlite3.Connection) -> tuple[dict, dict]:
    before = audit.volatile_boundary()
    corpus = audit.exact_and_lineage_corpus(connection)
    exclusions = audit.receipt_and_outbox_exclusions(connection, corpus)
    after = audit.volatile_boundary()
    if audit.compact_boundary(before) != audit.compact_boundary(after):
        raise ValueError("receipt/outbox boundary changed during task preflight")
    return exclusions, audit.compact_boundary(after)


def validate_source_and_targets(
    connection: sqlite3.Connection, plan: dict, task: dict
) -> tuple[dict, dict]:
    source = task["source"]
    row = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
        "FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
        "AND p.polynomial_index=?",
        (source["submissionId"], source["polynomialIndex"]),
    ).fetchone()
    if (
        row is None
        or str(row["coefficient_hash"]) != source["coefficientSha256"]
        or str(row["status"]) != "accepted"
        or int(row["scoreable"] or 0) != 1
        or audit.pair_text((str(row["label"]), int(row["r"]))) != task["sourcePair"]
    ):
        raise ValueError("conditional source anchor changed")
    exclusions, boundary = current_exclusions(connection)
    possible = {pair(value) for value in task["possibleTargetPairs"]}
    expected = {pair(value) for value in task["currentGoldTargetPairs"]}
    safe = plan.get("safeOutcomeExclusion") or {}
    if (
        not expected
        or not expected <= possible
        or pair(str(safe.get("pair"))) in possible
        or str(task["source"]["coefficientSha256"]) == safe.get("coefficientSha256")
    ):
        raise ValueError("task possible/gold sets or safe exclusion changed")
    for target_pair in expected:
        target = connection.execute(
            "SELECT team_count FROM targets WHERE label=? AND r=?", target_pair
        ).fetchone()
        if (
            target is None
            or int(target[0]) != 0
            or connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", target_pair
            ).fetchone()
            is not None
            or connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", target_pair
            ).fetchone()
            is not None
            or target_pair in exclusions["receiptPairs"]
            or target_pair in exclusions["outboxPairs"]
        ):
            raise ValueError(
                f"sealed current-gold option is no longer fresh: {audit.pair_text(target_pair)}"
            )
    expected_orbits = task.get("expectedOrbitTargets") or []
    if (
        len(expected_orbits) != int(task["length24OrbitCount"])
        or any(int(row.get("orbitSize", -1)) != 24 for row in expected_orbits)
    ):
        raise ValueError("task complete orbit targets are invalid")
    manifest = resolve(task["stageManifest"])
    certificate = resolve(task["stageCertificate"], data_only=True)
    for path in (manifest, certificate):
        if path.exists() or path.with_suffix(path.suffix + ".tmp").exists():
            raise FileExistsError(f"staging path already exists: {path}")
    return exclusions, boundary


def validate_source_fields(row: dict, task: dict) -> None:
    source_label, source_r = pair(task["sourcePair"])
    source = task["source"]
    if (
        str(row.get("sourceSubmissionId")) != source["submissionId"]
        or int(row.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
        or str(row.get("sourceCoefficientSha256")) != source["coefficientSha256"]
        or str(row.get("sourceLabel")) != source_label
        or int(row.get("sourceR", -1)) != source_r
        or int(row.get("workerExitCode", -1)) != 0
    ):
        raise ValueError("factor result source provenance differs from sealed task")


def candidate_rows(
    result_path: Path, certificate_path: Path | None, task: dict
) -> list[dict]:
    rows = read_jsonl(result_path)
    if certificate_path is None:
        if len(rows) != 1 or rows[0].get("status") != "certified":
            raise ValueError("single-orbit result is not one certified row")
        row = rows[0]
        validate_source_fields(row, task)
        validated = exact.validate_pair_sum(row, (), result_path, 1, "$")
        if validated is None:
            raise ValueError("single-orbit result failed exact pair-sum validation")
        expected = task["expectedOrbitTargets"]
        if (
            int(task["length24OrbitCount"]) != 1
            or len(expected) != 1
            or int((validated.get("proof") or {}).get("orbitIndex", -1))
            != int(expected[0]["orbitIndex"])
            or str(row.get("targetLabel")) != expected[0]["targetLabel"]
        ):
            raise ValueError("single-orbit result differs from complete action target")
        return [
            {
                "coefficientLine": row.get("coefficientLine"),
                "coefficientSha256": row.get("coefficientSha256"),
                "factorIndex": int(row.get("factorIndex", 0)),
                "polynomialDiscriminantAbs": row.get("polynomialDiscriminantAbs"),
                "sourceLabel": row.get("sourceLabel"),
                "sourceR": row.get("sourceR"),
                "sourceSubmissionId": row.get("sourceSubmissionId"),
                "sourcePolynomialIndex": row.get("sourcePolynomialIndex"),
                "targetLabel": row.get("targetLabel"),
                "targetR": row.get("targetR"),
            }
        ]
    if len(rows) != 1 or rows[0].get("status") != "certified_multi":
        raise ValueError("multi-orbit result is not one certified-multi row")
    row = rows[0]
    validate_source_fields(row, task)
    orbit_count = int(task["length24OrbitCount"])
    orbit_certificate = row.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit_certificate.get("actualDegrees") or []]
    expected_degrees = [int(value) for value in orbit_certificate.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit_certificate.get("exponents") or []]
    expected_targets = Counter(
        (
            int(value["orbitIndex"]),
            str(value["targetLabel"]),
            int(value["targetT"]),
            int(value["orbitSize"]),
        )
        for value in task["expectedOrbitTargets"]
    )
    actual_targets = Counter(
        (
            int(value["orbitIndex"]),
            str(value["targetLabel"]),
            int(value["targetT"]),
            int(value["orbitSize"]),
        )
        for value in row.get("orbitTargets") or []
    )
    candidates = row.get("candidates") or []
    if (
        int(row.get("length24OrbitCount", -1)) != orbit_count
        or actual != expected_degrees
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
        or actual.count(24) != orbit_count
        or len(candidates) != orbit_count
        or sorted(int(value.get("factorIndex", -1)) for value in candidates)
        != list(range(orbit_count))
        or actual_targets != expected_targets
    ):
        raise ValueError("multi-orbit factor certificate differs from sealed action")
    certificate = read_json(certificate_path)
    certificate_rows = certificate.get("rows") or []
    if len(certificate_rows) != 1:
        raise ValueError("Frobenius certificate must contain exactly one source row")
    joined = frobenius.join_resolved_assignments(
        certificate, rows, result_path, allow_unresolved=False
    )
    if len(joined) != orbit_count:
        raise ValueError("Frobenius assignment count differs from sealed orbit count")
    return joined


def main() -> int:
    args = arguments()
    plan_path = resolve(args.plan, data_only=True)
    plan, task = load_task(plan_path, args.task_id)
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        exclusions, exclusion_boundary = validate_source_and_targets(connection, plan, task)
        if args.preflight:
            print(
                json.dumps(
                    {
                        "status": "fresh_task_boundary_valid",
                        "taskId": args.task_id,
                        "currentGoldTargets": len(task["currentGoldTargetPairs"]),
                        "workerLaunched": False,
                        "networkCalls": 0,
                        "submissionCalls": 0,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.result is None:
            raise ValueError("--result is required unless --preflight is used")
        result_path = resolve(args.result, data_only=True)
        if str(result_path.relative_to(ROOT)) != task["factorOutput"]:
            raise ValueError("result path differs from sealed task output")
        certificate_path = (
            resolve(args.frobenius_certificate, data_only=True)
            if args.frobenius_certificate is not None
            else None
        )
        if bool(certificate_path) != bool(task.get("frobeniusCertificate")):
            raise ValueError("Frobenius certificate presence differs from task plan")
        if certificate_path is not None and str(certificate_path.relative_to(ROOT)) != task[
            "frobeniusCertificate"
        ]:
            raise ValueError("Frobenius certificate path differs from task plan")
        rows = candidate_rows(result_path, certificate_path, task)
        possible = {pair(value) for value in task["possibleTargetPairs"]}
        gold = {pair(value) for value in task["currentGoldTargetPairs"]}
        route_labels = set(map(str, task["routeTargetLabels"]))
        all_orbit_labels = {
            str(row["targetLabel"]) for row in task["expectedOrbitTargets"]
        }
        staged = []
        misses = []
        ignored = []
        known_hashes = set()
        for row in rows:
            realized = (str(row["targetLabel"]), int(row["targetR"]))
            if realized not in possible:
                if realized[0] not in all_orbit_labels:
                    raise ValueError("exact factor escaped complete action target labels")
                if realized[0] in route_labels:
                    raise ValueError("selected-route factor escaped sealed possible-pair set")
            line = exact.canonical_polynomial_line(row.get("coefficientLine"))
            if line is None:
                raise ValueError("factor is not a canonical degree-24 polynomial")
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            if digest != str(row.get("coefficientSha256")) or digest in known_hashes:
                raise ValueError("factor hash is invalid or duplicated")
            known_hashes.add(digest)
            item = {
                "line": line,
                "digest": digest,
                "pair": realized,
                "polynomialDiscriminantAbs": int(row["polynomialDiscriminantAbs"]),
                "factorIndex": int(row["factorIndex"]),
            }
            if realized[0] not in route_labels:
                ignored.append(item)
                continue
            if realized not in gold:
                misses.append(item)
                continue
            if (
                digest in exclusions["receiptHashes"]
                or digest in exclusions["outboxHashes"]
                or connection.execute(
                    "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
                ).fetchone()
                is not None
            ):
                raise ValueError("exact current-gold factor hash is already covered")
            staged.append(item)
    finally:
        connection.close()

    # One best polynomial per exact pair; no non-gold factor enters a manifest.
    best = {}
    for row in staged:
        key = row["pair"]
        rank = (row["polynomialDiscriminantAbs"], len(row["line"]), row["digest"])
        if key not in best or rank < best[key][0]:
            best[key] = (rank, row)
    selected = [best[key][1] for key in sorted(best, key=audit.pair_key)]
    if audit.compact_boundary(audit.volatile_boundary()) != exclusion_boundary:
        raise ValueError("receipt/outbox boundary changed before exclusive staging")
    manifest_path = resolve(task["stageManifest"], data_only=False)
    if not manifest_path.is_relative_to((ROOT / "outbox").resolve()):
        raise ValueError("stage manifest escaped outbox")
    stage_certificate = resolve(task["stageCertificate"], data_only=True)
    if manifest_path.exists() or stage_certificate.exists():
        raise FileExistsError("manifest or stage certificate appeared during staging")
    if selected:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_exclusive(
            manifest_path,
            "".join(row["line"] + "\n" for row in selected).encode("ascii"),
        )
    elif manifest_path.exists():
        raise ValueError("no-gold task unexpectedly has a manifest")

    certificate = {
        "schemaVersion": "conditional-gold-factor-fail-closed-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "exact_current_gold_staged_not_submitted"
            if selected
            else "exact_non_gold_miss_no_manifest"
        ),
        "taskId": args.task_id,
        "plan": helper.relative_artifact(plan_path),
        "result": helper.relative_artifact(result_path),
        "frobeniusCertificate": (
            helper.relative_artifact(certificate_path)
            if certificate_path is not None
            else None
        ),
        "selected": [
            {
                "coefficientSha256": row["digest"],
                "factorIndex": row["factorIndex"],
                "pair": audit.pair_text(row["pair"]),
            }
            for row in selected
        ],
        "exactMissPairs": sorted(
            {audit.pair_text(row["pair"]) for row in misses}
        ),
        "ignoredResolvedNonRoutePairs": sorted(
            {audit.pair_text(row["pair"]) for row in ignored}
        ),
        "manifest": (
            helper.relative_artifact(manifest_path) if selected else None
        ),
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered stage certificate")
    atomic_exclusive(stage_certificate, rendered.encode("utf-8"))
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "taskId": args.task_id,
                "stagedCurrentGoldPairs": len(selected),
                "exactMissPairs": len(certificate["exactMissPairs"]),
                "manifest": str(manifest_path.relative_to(ROOT)) if selected else None,
                "submissionCalls": 0,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed sequential executor for sealed low-contention tc1 routes.

The program never submits.  For each explicitly selected route it refreshes
recent queued receipts and the complete live target table, revalidates the
accepted source anchor and current tc1 target, runs one synchronous Sage
worker, invokes the sealed exact postflight validator, refreshes and guards a
second time, writes one no-overwrite manifest, and performs only an API dry
run.  Certificates and stdout are coefficient-free.

The default mode is a light, offline audit.  Heavy work requires --execute.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import stage_single_exact_census as exact_census
import validate_low_contention_pair_route as postflight_validator


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DB = DATA / "ledger.sqlite3"
DEFAULT_CERTIFICATE = DATA / "low_contention_pair_routes_certificate.json"
DEFAULT_PLAN = DATA / "low_contention_sequential_plan.json"
LOCK = DATA / ".low_contention_sequential.lock"

HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COEFFICIENT_PAYLOAD_RE = re.compile(
    r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])"
)
HEAVY_PROCESS_RE = re.compile(
    r"(?:^|[ /])(?:sage|gap)(?:$|[ ])", re.IGNORECASE
)


class GuardFailure(RuntimeError):
    """A fail-closed route invariant did not hold."""


def arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, default=DEFAULT_CERTIFICATE)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run guarded workers sequentially and stage/API-dry-run; never submit",
    )
    parser.add_argument(
        "--route-id",
        action="append",
        default=[],
        help="execute/audit only this planned route; repeat for an explicit sequence",
    )
    parser.add_argument(
        "--max-routes",
        type=int,
        default=1,
        help="maximum routes this invocation may execute (default: exactly one)",
    )
    parser.add_argument("--recent-limit", type=int, default=40)
    return parser.parse_args(list(argv) if argv is not None else None)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GuardFailure(f"expected one JSON object: {path}")
    return value


def one_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise GuardFailure(f"expected exactly one JSON object: {path}")
    return rows[0]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def artifact(path: Path) -> dict:
    return {"path": relative(path), "sha256": sha256_path(path)}


def route_map(certificate: dict) -> dict[str, dict]:
    rows = certificate.get("runbooks") or []
    result = {str(row.get("routeId")): row for row in rows}
    if len(result) != len(rows):
        raise GuardFailure("route certificate contains duplicate route ids")
    return result


def planned_paths(route: dict) -> dict[str, Path]:
    route_id = str(route["routeId"])
    result = (ROOT / str(route["output"])).resolve()
    return {
        "result": result,
        "resultTemporary": result.with_suffix(result.suffix + ".tmp"),
        "postflight": result.with_name(result.stem + "_postflight.json"),
        "manifest": OUTBOX / f"{route_id}.txt",
        "stageCertificate": DATA / f"{route_id}_stage_certificate.json",
    }


def validate_heavy_command(route: dict) -> None:
    command = route.get("heavyCommand")
    if not isinstance(command, list) or len(command) < 10:
        raise GuardFailure("heavy command is absent or malformed")
    if command[:3] != ["/usr/local/bin/sage", "-python", "pair_sum_one.sage.py"]:
        raise GuardFailure("heavy command is outside the sealed pair-sum worker")
    if any(not isinstance(value, str) or not value for value in command):
        raise GuardFailure("heavy command contains a non-string/empty argument")
    paths = planned_paths(route)
    try:
        output_index = command.index("--output-jsonl") + 1
        source_hash_index = command.index("--expected-source-hash") + 1
        target_index = command.index("--expected-target") + 1
    except (ValueError, IndexError) as exc:
        raise GuardFailure("heavy command lacks a required exact guard") from exc
    if (ROOT / command[output_index]).resolve() != paths["result"]:
        raise GuardFailure("heavy command output differs from the planned output")
    if command[source_hash_index] != str(route["source"]["coefficientSha256"]):
        raise GuardFailure("heavy command source hash differs from the sealed source")
    if command[target_index] != str(route["target"]["label"]):
        raise GuardFailure("heavy command target differs from the sealed target")


def validate_plan(plan_path: Path, certificate_path: Path) -> tuple[dict, dict, list[dict]]:
    plan_text = plan_path.read_text(encoding="utf-8")
    if COEFFICIENT_PAYLOAD_RE.search(plan_text):
        raise GuardFailure("coefficient payload found in the sequential plan")
    plan = json.loads(plan_text)
    certificate = read_json(certificate_path)
    if (
        plan.get("schemaVersion") != "low-contention-sequential-plan-v1"
        or plan.get("status") != "ready_guarded_no_submission"
        or plan.get("coefficientMaterialIncluded") is not False
        or plan.get("credentialMaterialIncluded") is not False
    ):
        raise GuardFailure("sequential plan is not sealed coefficient-free input")
    if (
        certificate.get("schemaVersion")
        != "low-contention-unordered-pair-route-audit-v1"
        or certificate.get("status") != "certified_light_only_runbooks_ready"
        or certificate.get("coefficientMaterialIncluded") is not False
    ):
        raise GuardFailure("route certificate is not sealed")
    pinned = plan.get("routeCertificate") or {}
    if (
        (ROOT / str(pinned.get("path"))).resolve() != certificate_path.resolve()
        or str(pinned.get("sha256")) != sha256_path(certificate_path)
    ):
        raise GuardFailure("route certificate path/hash changed")
    for pin in plan.get("programPins") or []:
        path = (ROOT / str(pin.get("path"))).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise GuardFailure("program pin escaped the project or is absent")
        if sha256_path(path) != str(pin.get("sha256")):
            raise GuardFailure(f"program pin changed: {relative(path)}")

    by_id = route_map(certificate)
    selected = []
    seen = set()
    for rank, item in enumerate(plan.get("routes") or [], start=1):
        route_id = str(item.get("routeId"))
        if route_id in seen or route_id not in by_id or int(item.get("rank", -1)) != rank:
            raise GuardFailure("planned route order is invalid/nonunique")
        seen.add(route_id)
        route = by_id[route_id]
        source, target = route["source"], route["target"]
        if (
            int(target.get("teamCountAtSeal", -1)) != 1
            or str(target.get("projectedMarginalScoreExact")) != "1/2"
            or str(item.get("targetPair")) != f"{target['label']}/r{target['r']}"
            or str(item.get("sourcePair")) != f"{source['label']}/r{source['r']}"
            or str(item.get("minimumDiscAbsAtSeal"))
            != str(target.get("minimumDiscAbsAtSeal"))
        ):
            raise GuardFailure(f"planned route metadata changed: {route_id}")
        expected_paths = {key: relative(value) for key, value in planned_paths(route).items() if key not in {"resultTemporary"}}
        if item.get("plannedArtifacts") != expected_paths:
            raise GuardFailure(f"planned artifact paths changed: {route_id}")
        validate_heavy_command(route)
        selected.append(route)
    if len(selected) != 10:
        raise GuardFailure("expected the ten unexecuted lc03-lc12 routes")
    discs = [int(route["target"]["minimumDiscAbsAtSeal"]) for route in selected]
    if discs != sorted(discs):
        raise GuardFailure("routes are not ranked by score then ascending discriminant")
    return plan, certificate, selected


def connect_ro() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def local_guard(
    connection: sqlite3.Connection,
    route: dict,
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    candidate_hash: str | None = None,
) -> dict:
    source = route["source"]
    target = route["target"]
    target_pair = (str(target["label"]), int(target["r"]))
    source_row = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
        "FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        (source["submissionId"], source["polynomialIndex"]),
    ).fetchone()
    if (
        source_row is None
        or str(source_row["coefficient_hash"]) != str(source["coefficientSha256"])
        or str(source_row["status"]) != "accepted"
        or int(source_row["scoreable"] or 0) != 1
        or str(source_row["label"]) != str(source["label"])
        or int(source_row["r"]) != int(source["r"])
    ):
        raise GuardFailure("source anchor is absent, changed, or not accepted-scoreable")
    if connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", target_pair
    ).fetchone() is not None:
        raise GuardFailure("target pair is baseline")
    if connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", target_pair
    ).fetchone() is not None:
        raise GuardFailure("target pair is already locally verified")
    target_row = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?", target_pair
    ).fetchone()
    if (
        target_row is None
        or int(target_row["team_count"]) != 1
        or int(target_row["discovered"] or 0) != 1
    ):
        raise GuardFailure("target is not currently a discovered tc1 pair")
    if target_pair in receipt_pairs:
        raise GuardFailure("target pair is covered by a local receipt")
    if candidate_hash is not None:
        if HASH_RE.fullmatch(candidate_hash) is None:
            raise GuardFailure("candidate hash is malformed")
        if candidate_hash in receipt_hashes:
            raise GuardFailure("candidate hash is covered by a local receipt")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone() is not None:
            raise GuardFailure("candidate hash already exists in the ledger")
    return {
        "sourceAcceptedScoreable": True,
        "targetPair": f"{target_pair[0]}/r{target_pair[1]}",
        "targetTeamCount": 1,
        "targetDiscovered": True,
        "targetMinimumDiscAbs": (
            str(target_row["minimum_disc_abs"])
            if target_row["minimum_disc_abs"] is not None else None
        ),
        "targetGeneratedAt": str(target_row["generated_at"]),
        "targetNotBaseline": True,
        "targetNotLocallyVerified": True,
        "targetNotReceiptCovered": True,
        "candidateNovel": candidate_hash is not None,
    }


def receipt_snapshot(connection: sqlite3.Connection) -> tuple[set[str], set[tuple[str, int]], dict]:
    return postflight_validator.receipt_snapshot(connection)


def run_json(command: list[str], *, timeout: int = 600) -> dict:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if completed.returncode != 0:
        tail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
        raise GuardFailure(f"command failed ({command[1]}): {tail[0][:300]}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise GuardFailure(f"command did not return JSON: {command[1]}") from exc
    if not isinstance(value, dict):
        raise GuardFailure(f"command returned non-object JSON: {command[1]}")
    return value


def refresh_remote(recent_limit: int) -> dict:
    recent = run_json(
        [sys.executable, "sair_api.py", "recent", "--limit", str(recent_limit)],
        timeout=240,
    )
    queued_ids = []
    for row in recent.get("items") or []:
        submission_id = str(row.get("submissionId") or "")
        if int(row.get("queued") or 0) > 0 and submission_id.startswith("sub_"):
            queued_ids.append(submission_id)
    sync_rows = []
    for submission_id in queued_ids:
        synced = run_json(
            [sys.executable, "sair_api.py", "sync", submission_id], timeout=300
        )
        sync_rows.append(
            {
                "submissionId": submission_id,
                "queued": int(synced.get("queued") or 0),
                "verified": int(synced.get("verified") or 0),
                "failed": int(synced.get("failed") or 0),
            }
        )
    targets = run_json([sys.executable, "sair_api.py", "targets"], timeout=600)
    return {
        "recentItems": len(recent.get("items") or []),
        "queuedSubmissionsSynced": sync_rows,
        "targetLabels": int(targets.get("labels") or 0),
        "targetPairs": int(targets.get("pairs") or 0),
    }


def heavy_processes() -> list[dict]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    result = []
    for raw in completed.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        first, _, command = raw.partition(" ")
        try:
            pid = int(first)
        except ValueError:
            continue
        if pid != os.getpid() and HEAVY_PROCESS_RE.search(command):
            result.append({"pid": pid, "program": command.split()[0]})
    return result


@contextmanager
def execution_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardFailure("another low-contention executor holds the lock") from exc
        yield


def artifact_state(route: dict) -> dict:
    paths = planned_paths(route)
    return {key: path.exists() for key, path in paths.items()}


def ensure_preworker_absence(route: dict) -> None:
    state = artifact_state(route)
    if state["resultTemporary"]:
        raise GuardFailure("stale/in-progress result temporary file exists")
    if not state["result"] and any(
        state[key] for key in ("postflight", "manifest", "stageCertificate")
    ):
        raise GuardFailure("downstream artifact exists without a worker result")
    if not state["postflight"] and any(
        state[key] for key in ("manifest", "stageCertificate")
    ):
        raise GuardFailure("staged artifact exists without exact postflight")
    if not state["manifest"] and state["stageCertificate"]:
        raise GuardFailure("stage certificate exists without a manifest")


def run_worker(route: dict) -> None:
    paths = planned_paths(route)
    if paths["result"].exists():
        return
    active = heavy_processes()
    if active:
        raise GuardFailure(f"Sage/GAP worker already active: {active}")
    completed = subprocess.run(
        list(route["heavyCommand"]),
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        tail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
        raise GuardFailure(f"heavy worker failed: {tail[0][:300]}")
    if not paths["result"].is_file() or paths["resultTemporary"].exists():
        raise GuardFailure("heavy worker did not atomically create its sealed result")


def ensure_postflight(certificate_path: Path, route: dict) -> None:
    paths = planned_paths(route)
    if paths["postflight"].exists():
        return
    command = [
        sys.executable,
        "validate_low_contention_pair_route.py",
        "--certificate",
        relative(certificate_path),
        "--route-id",
        str(route["routeId"]),
        "--result",
        relative(paths["result"]),
    ]
    result = run_json(command, timeout=600)
    if result.get("status") != "certified_exact_novel_live_not_staged_not_submitted":
        raise GuardFailure("exact postflight did not certify the route")


def validated_candidate(certificate_path: Path, route: dict) -> tuple[str, str, dict]:
    paths = planned_paths(route)
    result = one_jsonl(paths["result"])
    postflight = read_json(paths["postflight"])
    source, target = route["source"], route["target"]
    if (
        result.get("status") != "certified"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(result.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
        or str(result.get("sourceCoefficientSha256")) != str(source["coefficientSha256"])
        or str(result.get("targetLabel")) != str(target["label"])
        or int(result.get("targetR", -1)) != int(target["r"])
    ):
        raise GuardFailure("worker result differs from the exact sealed route")
    line = exact_census.canonical_polynomial_line(result.get("coefficientLine"))
    if line is None:
        raise GuardFailure("candidate is not canonical primitive monic degree 24")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    orbit = result.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected = [int(value) for value in orbit.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit.get("exponents") or []]
    if (
        digest != str(result.get("coefficientSha256"))
        or actual != expected
        or actual.count(24) != 1
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
    ):
        raise GuardFailure("candidate hash/orbit certificate is not exact squarefree")
    if (
        postflight.get("schemaVersion") != "low-contention-pair-route-postflight-v1"
        or postflight.get("status")
        != "certified_exact_novel_live_not_staged_not_submitted"
        or str(postflight.get("routeId")) != str(route["routeId"])
        or (postflight.get("candidate") or {}).get("irreducible") is not True
        or str((postflight.get("candidate") or {}).get("coefficientSha256")) != digest
        or str(((postflight.get("routeCertificate") or {}).get("sha256")))
        != sha256_path(certificate_path)
        or str(((postflight.get("result") or {}).get("sha256")))
        != sha256_path(paths["result"])
    ):
        raise GuardFailure("postflight does not pin exact target/hash/irreducibility")
    return line, digest, postflight


def exclusive_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise GuardFailure(f"refusing to overwrite: {relative(path)}") from exc


def exclusive_json(path: Path, value: dict) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if COEFFICIENT_PAYLOAD_RE.search(rendered):
        raise GuardFailure("coefficient payload would enter a stage certificate")
    exclusive_text(path, rendered)


def ensure_manifest(route: dict, line: str, digest: str) -> Path:
    manifest = planned_paths(route)["manifest"]
    expected = line + "\n"
    if manifest.exists():
        if manifest.read_text(encoding="ascii") != expected:
            raise GuardFailure("existing manifest differs from the exact candidate")
        return manifest
    exclusive_text(manifest, expected)
    if sha256_path(manifest) != hashlib.sha256(expected.encode("ascii")).hexdigest():
        raise GuardFailure("manifest write/hash verification failed")
    if hashlib.sha256(line.encode("ascii")).hexdigest() != digest:
        raise GuardFailure("manifest candidate hash changed")
    return manifest


def dry_run_manifest(manifest: Path) -> dict:
    result = run_json(
        [sys.executable, "sair_api.py", "submit", "--file", relative(manifest)],
        timeout=300,
    )
    if (
        result.get("commit") is not False
        or int(result.get("polynomials", -1)) != 1
        or int(result.get("knownLocalHashes", -1)) != 0
        or str(result.get("manifestHash")) != sha256_path(manifest)
    ):
        raise GuardFailure("API dry-run invariants failed")
    return {
        "commit": False,
        "polynomials": 1,
        "bytes": int(result["bytes"]),
        "knownLocalHashes": 0,
        "manifestHash": str(result["manifestHash"]),
    }


def execute_one(
    plan_path: Path,
    certificate_path: Path,
    route: dict,
    recent_limit: int,
) -> dict:
    ensure_preworker_absence(route)
    refresh_before = refresh_remote(recent_limit)
    with connect_ro() as connection:
        receipt_hashes, receipt_pairs, receipt_audit = receipt_snapshot(connection)
        guard_before = local_guard(
            connection, route, receipt_hashes, receipt_pairs, candidate_hash=None
        )

    run_worker(route)
    ensure_postflight(certificate_path, route)
    line, digest, postflight = validated_candidate(certificate_path, route)

    refresh_after = refresh_remote(recent_limit)
    with connect_ro() as connection:
        receipt_hashes, receipt_pairs, receipt_audit_after = receipt_snapshot(connection)
        guard_after = local_guard(
            connection, route, receipt_hashes, receipt_pairs, candidate_hash=digest
        )

    manifest = ensure_manifest(route, line, digest)
    dry_run = dry_run_manifest(manifest)
    stage_path = planned_paths(route)["stageCertificate"]
    stage = {
        "schemaVersion": "low-contention-sequential-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_tc1_staged_api_dry_run_not_submitted",
        "routeId": route["routeId"],
        "plan": artifact(plan_path),
        "routeCertificate": artifact(certificate_path),
        "postflight": artifact(planned_paths(route)["postflight"]),
        "manifest": {
            "path": relative(manifest),
            "sha256": sha256_path(manifest),
            "bytes": manifest.stat().st_size,
            "polynomials": 1,
        },
        "candidate": {
            "coefficientSha256": digest,
            "targetLabel": route["target"]["label"],
            "targetR": route["target"]["r"],
            "irreducible": True,
            "exactSquarefreeOrbitCertificate": True,
        },
        "guards": {
            "beforeWorker": guard_before,
            "afterWorker": guard_after,
            "receiptBefore": receipt_audit,
            "receiptAfter": receipt_audit_after,
        },
        "refresh": {"beforeWorker": refresh_before, "afterWorker": refresh_after},
        "apiDryRun": dry_run,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "sageWorkersLaunchedAtMost": 1,
            "gapWorkersLaunchedAtMost": 0,
            "submissionCalls": 0,
        },
    }
    if stage_path.exists():
        existing = read_json(stage_path)
        if (
            existing.get("status") != stage["status"]
            or (existing.get("manifest") or {}).get("sha256")
            != stage["manifest"]["sha256"]
            or (existing.get("candidate") or {}).get("coefficientSha256") != digest
        ):
            raise GuardFailure("existing stage certificate differs from current exact state")
    else:
        exclusive_json(stage_path, stage)
    return {
        "status": stage["status"],
        "routeId": route["routeId"],
        "targetPair": f"{route['target']['label']}/r{route['target']['r']}",
        "candidateSha256": digest,
        "manifest": relative(manifest),
        "stageCertificate": relative(stage_path),
        "submissionCalls": 0,
    }


def offline_audit(route: dict) -> dict:
    state = artifact_state(route)
    status = "ready_for_guarded_worker"
    error = None
    try:
        ensure_preworker_absence(route)
        with connect_ro() as connection:
            receipt_hashes, receipt_pairs, _audit = receipt_snapshot(connection)
            local_guard(connection, route, receipt_hashes, receipt_pairs)
    except (GuardFailure, ValueError, sqlite3.Error, OSError) as exc:
        status, error = "blocked_fail_closed", str(exc)
    return {
        "routeId": route["routeId"],
        "targetPair": f"{route['target']['label']}/r{route['target']['r']}",
        "status": status,
        "error": error,
        "artifacts": state,
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = arguments(argv)
    plan_path = args.plan.expanduser().resolve()
    certificate_path = args.certificate.expanduser().resolve()
    if (
        not plan_path.is_relative_to(DATA)
        or not certificate_path.is_relative_to(DATA)
        or args.max_routes < 1
    ):
        raise GuardFailure("plan/certificate escaped data or max-routes is invalid")
    _plan, _certificate, planned = validate_plan(plan_path, certificate_path)
    wanted = args.route_id
    if wanted:
        by_id = {route["routeId"]: route for route in planned}
        if len(set(wanted)) != len(wanted) or any(route_id not in by_id for route_id in wanted):
            raise GuardFailure("requested route sequence is invalid/duplicated/not planned")
        routes = [by_id[route_id] for route_id in wanted]
    else:
        routes = planned

    if not args.execute:
        print(json.dumps({
            "status": "offline_audit_no_workers_no_network_no_submission",
            "rankingPolicy": "score descending, then sealed target minimum discriminant ascending",
            "routes": [offline_audit(route) for route in routes],
        }, indent=2, sort_keys=True))
        return 0

    routes = routes[: args.max_routes]
    reports = []
    with execution_lock():
        for route in routes:
            reports.append(
                execute_one(plan_path, certificate_path, route, args.recent_limit)
            )
    print(json.dumps({
        "status": "staged_api_dry_run_only_no_submission",
        "executedSequentially": len(reports),
        "reports": reports,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

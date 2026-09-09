#!/usr/bin/env python3
"""Run one sequential F5 pass after reset exact closure has finished.

This controller deliberately waits behind the primary reset queue and the
adaptive index-24 closure controller.  It synchronizes any closure receipts,
builds an F5 plan from the then-current accepted ledger, uses both existing
GCP workers for deterministic arithmetic only, and commits no more manifests
than the daily submission quota still permits.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TOKEN_SOURCE = Path("/path/to/private-file")
REMOTE_ROOT = "/path/to/igp24"
HOSTS = (
    ("192.0.2.11", "COMPUTE_INSTANCE_ID", "head"),
    ("192.0.2.10", "COMPUTE_INSTANCE_ID", "tail"),
)
SSH_KEY = "/path/to/private-file"
KNOWN_HOSTS = "/path/to/private-file"


def token() -> str:
    tree = ast.parse(TOKEN_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if any(isinstance(target, ast.Name) and target.id == "TEAM_AUTH_TOKEN" for target in targets):
                return node.value.value
    raise RuntimeError("TEAM_AUTH_TOKEN missing")


def append_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(payload, sort_keys=True), flush=True)


def run(command: list[str], *, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    process = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True)
    print(json.dumps({
        "event": "command",
        "command": command[:4] + (["..."] if len(command) > 4 else []),
        "returncode": process.returncode,
        "stdoutExcerpt": process.stdout[-1200:],
        "stderrExcerpt": process.stderr[-1200:],
    }, sort_keys=True), flush=True)
    return process


def ssh_base(host: str, alias: str) -> list[str]:
    return [
        "ssh", "-i", SSH_KEY,
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", f"HostKeyAlias={alias}",
        "-o", "StrictHostKeyChecking=yes",
        f"YOUR_SSH_USER@{host}",
    ]


def scp_base(alias: str) -> list[str]:
    return [
        "scp", "-i", SSH_KEY,
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", f"HostKeyAlias={alias}",
        "-o", "StrictHostKeyChecking=yes",
    ]


def jsonl_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows


def successful_ids(path: Path, events: set[str]) -> list[str]:
    ids = []
    for row in jsonl_rows(path):
        value = row.get("submissionId")
        if row.get("event") in events and isinstance(value, str):
            ids.append(value)
    return list(dict.fromkeys(ids))


def wait_for_closure(path: Path, deadline: float, poll: float) -> list[str]:
    terminal = {"adaptive-controller-complete", "no-fresh-closure-jobs", "no-fresh-staged-rows"}
    while time.time() < deadline:
        rows = jsonl_rows(path)
        if any(row.get("event") in terminal for row in rows):
            return successful_ids(path, {"adaptive-commit-result"})
        print(json.dumps({"event": "waiting-for-exact-closure"}), flush=True)
        time.sleep(poll)
    raise TimeoutError("exact closure controller did not finish")


def sync_submissions(ids: list[str], environment: dict[str, str], deadline: float, poll: float) -> None:
    pending = set(ids)
    while pending and time.time() < deadline:
        again = set()
        for submission_id in sorted(pending):
            process = run([sys.executable, "sair_api.py", "sync", submission_id], environment=environment)
            if process.returncode:
                again.add(submission_id)
                continue
            try:
                payload = json.loads(process.stdout)
            except json.JSONDecodeError:
                again.add(submission_id)
                continue
            if int(payload.get("queued", payload.get("queuedCount", 0)) or 0) > 0:
                again.add(submission_id)
        pending = again
        if pending:
            time.sleep(poll)
    if pending:
        raise TimeoutError(f"{len(pending)} exact-closure submissions remain unverified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-log", type=Path, default=ROOT / "data/reset_submission_queue_20260814.jsonl")
    parser.add_argument("--closure-log", type=Path, default=ROOT / "data/postreset_exact_closure_controller_20260814.jsonl")
    parser.add_argument("--log", type=Path, default=ROOT / "data/postreset_f5_adaptive_controller_20260814.jsonl")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--timeout-seconds", type=float, default=10 * 3600)
    parser.add_argument("--workers-per-host", type=int, default=8)
    parser.add_argument("--max-sources", type=int, default=1600)
    parser.add_argument("--daily-submission-cap", type=int, default=200)
    args = parser.parse_args()
    deadline = time.time() + args.timeout_seconds
    environment = os.environ.copy()
    environment["SAIR_API_KEY"] = token()

    closure_ids = wait_for_closure(args.closure_log, deadline, args.poll_seconds)
    # The first closure plan intentionally starts after only 16 reset
    # submissions verify.  Before building the broader F5 pass, reconcile the
    # entire completed reset queue so all newly accepted source fields are in
    # the local ledger and cannot be accidentally resubmitted.
    reset_ids = successful_ids(args.queue_log, {"commit-result", "quota-race-retry-result"})
    sync_submissions(list(dict.fromkeys(reset_ids + closure_ids)), environment, deadline, args.poll_seconds)

    tag = "postreset_adaptive_20260814"
    plan = ROOT / f"data/current_f5_hybrid_value_{tag}.json"
    if plan.exists():
        raise FileExistsError(plan)
    built = run([
        sys.executable, "build_current_f5_hybrid_value_plan_20260813.py",
        "--tag", tag,
        "--max-team-count", "10",
        "--max-sources", str(args.max_sources),
        "--sources-per-label-signature", "16",
    ])
    if built.returncode:
        raise RuntimeError("post-reset F5 planning failed")
    payload = json.loads(plan.read_text(encoding="utf-8"))
    groups = int(payload["selectedGroups"])
    if groups == 0:
        append_log(args.log, {"event": "no-fresh-f5-groups"})
        return 0

    midpoint = (groups + 1) // 2
    ranges = ((1, midpoint), (midpoint + 1, groups))
    tags = []
    expected = []
    scripts = [ROOT / "run_current_f5_hybrid_value_group_20260813.sage.py", ROOT / "launch_current_f5_hybrid_range_20260814.sh"]
    for (host, alias, side), (start, stop) in zip(HOSTS, ranges):
        if stop < start:
            tags.append(None)
            expected.append(0)
            continue
        side_tag = f"{tag}_{side}"
        tags.append(side_tag)
        expected.append(stop - start + 1)
        if run(ssh_base(host, alias) + [f"mkdir -p {REMOTE_ROOT}/data"]).returncode:
            raise RuntimeError(f"failed to prepare F5 worker {side}")
        if run(scp_base(alias) + [str(path) for path in scripts] + [f"YOUR_SSH_USER@{host}:{REMOTE_ROOT}/"]).returncode:
            raise RuntimeError(f"failed to copy F5 scripts to {side}")
        if run(scp_base(alias) + [str(plan), f"YOUR_SSH_USER@{host}:{REMOTE_ROOT}/data/"]).returncode:
            raise RuntimeError(f"failed to copy F5 plan to {side}")
        remote_log = f"data/current_f5_hybrid_value_{side_tag}.log"
        command = (
            f"cd {REMOTE_ROOT} && nohup bash launch_current_f5_hybrid_range_20260814.sh "
            f"{plan.name} {side_tag} {start} {stop} {args.workers_per_host} 900 "
            f"> {remote_log} 2>&1 < /dev/null & echo $!"
        )
        launched = run(ssh_base(host, alias) + [command])
        if launched.returncode:
            raise RuntimeError(f"failed to launch F5 range on {side}")
        append_log(args.log, {"event": "launched-f5-range", "host": side, "start": start, "stop": stop, "pid": launched.stdout.strip()})

    finished = [need == 0 for need in expected]
    while not all(finished) and time.time() < deadline:
        for index, ((host, alias, side), side_tag, need) in enumerate(zip(HOSTS, tags, expected)):
            if finished[index] or side_tag is None:
                continue
            pattern = f"data/current_f5_hybrid_value_{side_tag}_group*_20260813.json"
            probe = run(ssh_base(host, alias) + [
                f"cd {REMOTE_ROOT}; count=$(find data -maxdepth 1 -name 'current_f5_hybrid_value_{side_tag}_group*_20260813.json' | wc -l); "
                f"running=$(pgrep -fc '[l]aunch_current_f5_hybrid_range_20260814.sh.*{side_tag}' || true); printf '%s %s\\n' \"$count\" \"$running\""
            ])
            if probe.returncode or len(probe.stdout.split()) < 2:
                continue
            count, running = map(int, probe.stdout.split()[-2:])
            append_log(args.log, {"event": "f5-range-progress", "host": side, "outputs": count, "planned": need, "running": running})
            if count >= need or running == 0:
                finished[index] = True
        if not all(finished):
            time.sleep(args.poll_seconds)
    if not all(finished):
        raise TimeoutError("F5 adaptive ranges exceeded timeout")

    certificates = []
    for (host, alias, side), side_tag, need in zip(HOSTS, tags, expected):
        if side_tag is None or need == 0:
            continue
        remote_archive = f"data/{side_tag}.tar.gz"
        packed = run(ssh_base(host, alias) + [
            f"cd {REMOTE_ROOT} && tar -czf {remote_archive} data/current_f5_hybrid_value_{side_tag}_group*_20260813.json"
        ])
        if packed.returncode:
            raise RuntimeError(f"failed to package F5 results on {side}")
        local_archive = ROOT / remote_archive
        if local_archive.exists():
            raise FileExistsError(local_archive)
        if run(scp_base(alias) + [f"YOUR_SSH_USER@{host}:{REMOTE_ROOT}/{remote_archive}", str(local_archive)]).returncode:
            raise RuntimeError(f"failed to retrieve F5 results from {side}")
        with tarfile.open(local_archive, "r:gz") as archive:
            members = archive.getmembers()
            if not members or any(not member.name.startswith(f"data/current_f5_hybrid_value_{side_tag}_group") for member in members):
                raise ValueError("unexpected F5 result archive member")
            archive.extractall(ROOT, filter="data")
        stage_manifest = ROOT / f"outbox/current_f5_hybrid_value_{side_tag}.txt"
        certificate = ROOT / f"data/current_f5_hybrid_value_{side_tag}_stage.json"
        staged = run([
            sys.executable, "stage_current_f5_archive_delta_20260813.py",
            "--tag-contains", side_tag,
            "--output", str(stage_manifest),
            "--certificate", str(certificate),
        ])
        if staged.returncode:
            raise RuntimeError(f"failed to stage F5 results from {side}")
        certificates.append(certificate)

    combined = ROOT / f"outbox/current_f5_hybrid_value_{tag}_combined.txt"
    combined_summary = ROOT / f"data/current_f5_hybrid_value_{tag}_combined.json"
    command = [sys.executable, "combine_f5_stage_certificates_20260813.py"]
    for certificate in certificates:
        command.extend(["--certificate", str(certificate)])
    command.extend(["--output", str(combined), "--summary", str(combined_summary)])
    if run(command).returncode:
        raise RuntimeError("failed to combine post-reset F5 stages")
    if not combined.stat().st_size:
        append_log(args.log, {"event": "no-fresh-f5-candidates", "groups": groups})
        return 0

    prefix = ROOT / f"outbox/current_f5_hybrid_value_{tag}_submit"
    split_summary = ROOT / f"data/current_f5_hybrid_value_{tag}_submit.json"
    if run([
        sys.executable, "split_submission_manifest_20260813.py",
        "--input", str(combined),
        "--output-prefix", str(prefix),
        "--summary", str(split_summary),
        "--max-lines", "1000", "--max-bytes", "950000",
    ]).returncode:
        raise RuntimeError("failed to split F5 adaptive manifest")

    remaining = max(0, args.daily_submission_cap - len(reset_ids) - len(closure_ids))
    manifests = [Path(path) for path in sorted(glob.glob(str(prefix) + "_*.txt"))][:remaining]
    committed = 0
    for ordinal, manifest in enumerate(manifests, 1):
        process = run([
            sys.executable, "sair_api.py", "submit", "--file", str(manifest),
            "--description", f"post-reset adaptive F5 {ordinal:02d}/{len(manifests):02d}", "--commit",
        ], environment=environment)
        event = {"event": "f5-adaptive-commit-result", "manifest": str(manifest), "returncode": process.returncode}
        if process.returncode:
            event["outputExcerpt"] = (process.stdout + process.stderr)[:1000]
            append_log(args.log, event)
            break
        response = json.loads(process.stdout)
        event["submissionId"] = response["submissionId"]
        event["polynomials"] = response["polynomials"]
        append_log(args.log, event)
        committed += 1
        time.sleep(1.25)
    append_log(args.log, {
        "event": "f5-adaptive-controller-complete",
        "groups": groups,
        "resetSubmissionIds": len(reset_ids),
        "closureSubmissionIds": len(closure_ids),
        "availableSlots": remaining,
        "stagedManifests": len(glob.glob(str(prefix) + "_*.txt")),
        "committed": committed,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

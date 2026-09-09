#!/usr/bin/env python3
"""Compute and submit a bounded exact closure wave after the reset pilot verifies."""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import subprocess
import sys
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
    process = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    print(
        json.dumps(
            {
                "event": "command",
                "command": command[:4] + (["..."] if len(command) > 4 else []),
                "returncode": process.returncode,
                "stdoutExcerpt": process.stdout[-1200:],
                "stderrExcerpt": process.stderr[-1200:],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return process


def ssh_base(host: str, alias: str) -> list[str]:
    return [
        "ssh",
        "-i", SSH_KEY,
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", f"HostKeyAlias={alias}",
        "-o", "StrictHostKeyChecking=yes",
        f"YOUR_SSH_USER@{host}",
    ]


def scp_base(alias: str) -> list[str]:
    return [
        "scp",
        "-i", SSH_KEY,
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", f"HostKeyAlias={alias}",
        "-o", "StrictHostKeyChecking=yes",
    ]


def wait_for(path: Path, deadline: float, poll: float) -> None:
    while time.time() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        print(json.dumps({"event": "waiting-for-plan", "path": str(path)}), flush=True)
        time.sleep(poll)
    raise TimeoutError(f"timed out waiting for {path}")


def queue_state(path: Path) -> tuple[list[str], bool]:
    ids: list[str] = []
    complete = False
    if not path.exists():
        return ids, complete
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("returncode") == 0 and isinstance(row.get("submissionId"), str):
            ids.append(row["submissionId"])
        complete = complete or row.get("event") == "queue-complete"
    return list(dict.fromkeys(ids)), complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-summary", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--queue-log", type=Path, default=ROOT / "data/reset_submission_queue_20260814.jsonl")
    parser.add_argument("--log", type=Path, default=ROOT / "data/postreset_exact_closure_controller_20260814.jsonl")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--timeout-seconds", type=float, default=6 * 3600)
    parser.add_argument("--workers-per-host", type=int, default=8)
    parser.add_argument("--daily-submission-cap", type=int, default=200)
    args = parser.parse_args()
    deadline = time.time() + args.timeout_seconds

    wait_for(args.plan, deadline, args.poll_seconds)
    wait_for(args.plan_summary, deadline, args.poll_seconds)
    rows = [json.loads(line) for line in args.plan.read_text().splitlines() if line.strip()]
    if not rows:
        append_log(args.log, {"event": "no-fresh-closure-jobs"})
        return 0
    if args.source_db.exists():
        raise FileExistsError(args.source_db)
    built = run(
        [
            sys.executable,
            "build_index24_plan_source_db_20260813.py",
            "--plan", str(args.plan),
            "--output", str(args.source_db),
        ]
    )
    if built.returncode:
        raise RuntimeError("failed to build closure source database")

    orbit_maps = sorted({ROOT / str(row["orbitMap"]) for row in rows})
    for path in orbit_maps:
        if not path.is_file():
            raise FileNotFoundError(path)
    data_files = [args.plan.resolve(), args.source_db.resolve(), *orbit_maps]
    scripts = [
        ROOT / "run_live_index24_pair_resolvent_gp_20260813.py",
        ROOT / "run_index24_pair_range_queue_20260813.py",
    ]
    midpoint = (len(rows) + 1) // 2
    ranges = ((0, midpoint, False), (midpoint, len(rows), True))
    remote_outputs: list[str] = []
    remote_logs: list[str] = []
    for (host, alias, tag), (start, stop, reverse) in zip(HOSTS, ranges):
        remote_output = f"data/postreset_first16_index24_pair_outputs_{tag}_20260814"
        remote_log = f"data/postreset_first16_index24_pair_{tag}_20260814.log"
        remote_outputs.append(remote_output)
        remote_logs.append(remote_log)
        if stop <= start:
            continue
        made = run(ssh_base(host, alias) + [f"mkdir -p {REMOTE_ROOT}/data"])
        if made.returncode:
            raise RuntimeError(f"failed to prepare {host}")
        copied_scripts = run(
            scp_base(alias)
            + [str(path) for path in scripts]
            + [f"YOUR_SSH_USER@{host}:{REMOTE_ROOT}/"]
        )
        copied_data = run(
            scp_base(alias)
            + [str(path) for path in data_files]
            + [f"YOUR_SSH_USER@{host}:{REMOTE_ROOT}/data/"]
        )
        if copied_scripts.returncode or copied_data.returncode:
            raise RuntimeError(f"failed to copy closure inputs to {host}")
        reverse_flag = " --reverse" if reverse else ""
        command = (
            f"cd {REMOTE_ROOT} && "
            f"nohup python3 run_index24_pair_range_queue_20260813.py "
            f"--plan data/{args.plan.name} --db data/{args.source_db.name} "
            f"--output-dir {remote_output} --start {start} --stop {stop} "
            f"--workers {args.workers_per_host}{reverse_flag} "
            f"> {remote_log} 2>&1 < /dev/null & echo $!"
        )
        launched = run(ssh_base(host, alias) + [command])
        if launched.returncode:
            raise RuntimeError(f"failed to launch closure range on {host}")
        append_log(
            args.log,
            {
                "event": "launched-range",
                "host": tag,
                "pid": launched.stdout.strip(),
                "start": start,
                "stop": stop,
            },
        )

    expected = [stop - start for start, stop, _reverse in ranges]
    finished = [count == 0 for count in expected]
    while not all(finished) and time.time() < deadline:
        for index, ((host, alias, tag), remote_output, need) in enumerate(
            zip(HOSTS, remote_outputs, expected)
        ):
            if finished[index]:
                continue
            probe = run(
                ssh_base(host, alias)
                + [
                    f"cd {REMOTE_ROOT}; "
                    f"count=$(find {remote_output} -maxdepth 1 -name 'gp_job_*.jsonl' 2>/dev/null | wc -l); "
                    f"running=$(pgrep -fc '[r]un_index24_pair_range_queue_20260813.py.*{remote_output}' || true); "
                    f"printf '%s %s\\n' \"$count\" \"$running\""
                ]
            )
            if probe.returncode:
                continue
            fields = probe.stdout.split()
            if len(fields) < 2:
                continue
            count, running = int(fields[-2]), int(fields[-1])
            append_log(
                args.log,
                {"event": "range-progress", "host": tag, "outputs": count, "required": need, "running": running},
            )
            if count >= need or running == 0:
                finished[index] = True
        if not all(finished):
            time.sleep(args.poll_seconds)
    if not all(finished):
        raise TimeoutError("closure ranges exceeded controller timeout")

    local_dirs: list[Path] = []
    for (host, alias, tag), remote_output, need in zip(HOSTS, remote_outputs, expected):
        if need == 0:
            continue
        local = ROOT / remote_output
        if local.exists():
            raise FileExistsError(local)
        copied = run(
            scp_base(alias)
            + ["-r", f"YOUR_SSH_USER@{host}:{REMOTE_ROOT}/{remote_output}", str(local.parent)]
        )
        if copied.returncode:
            raise RuntimeError(f"failed to retrieve closure outputs from {tag}")
        local_dirs.append(local)

    certificate = ROOT / "data/postreset_first16_index24_pair_value_20260814.json"
    prefix = ROOT / "outbox/postreset_first16_index24_pair_value_20260814"
    stage = [
        sys.executable,
        "stage_postarchive_index24_pair_wave_20260813.py",
        "--output-prefix", str(prefix),
        "--certificate", str(certificate),
        "--max-lines", "1000",
        "--max-bytes", "950000",
    ]
    for local in local_dirs:
        stage.extend(["--input", str(local / "gp_job_*.jsonl")])
    for prior in (
        "postarchive_index24_pair_complete_value_20260813.json",
        "postarchive_index24_pair_deep_head_value_20260813.json",
        "postarchive_index24_pair_deep_tail_value_20260813.json",
        "postarchive_index24_pair_deeper_head_value_20260813.json",
        "postarchive_index24_pair_deeper_tail_value_20260813.json",
        "postarchive_index24_pair_ultradeep_tail_value_20260813.json",
        "postarchive_index24_pair_ultradeep_head_value_20260813.json",
        "postarchive_index24_pair_extreme_tail_value_20260813.json",
    ):
        path = ROOT / "data" / prior
        if path.is_file():
            stage.extend(["--include-hash-certificate", str(path)])
    for prior in (
        "current_character_recovery_tc10_delta4_20260813.json",
        "current_c3_group_ring_hybrid_value_20260813.json",
        "current_f5_postreset_combined_pairdelta_20260813.json",
    ):
        path = ROOT / "data" / prior
        if path.is_file():
            stage.extend(["--exclude-pair-certificate", str(path)])
    staged = run(stage)
    if staged.returncode:
        if "no fresh" in (staged.stdout + staged.stderr).lower():
            append_log(args.log, {"event": "no-fresh-staged-rows"})
            return 0
        raise RuntimeError("failed to stage closure results")

    while time.time() < deadline:
        reset_ids, queue_complete = queue_state(args.queue_log)
        if queue_complete:
            break
        print(json.dumps({"event": "waiting-for-reset-queue-complete", "submissionIds": len(reset_ids)}), flush=True)
        time.sleep(args.poll_seconds)
    else:
        raise TimeoutError("reset queue did not complete")

    remaining = max(0, args.daily_submission_cap - len(reset_ids))
    manifests = [Path(path) for path in sorted(glob.glob(str(prefix) + "_*.txt"))]
    manifests = manifests[:remaining]
    environment = os.environ.copy()
    environment["SAIR_API_KEY"] = token()
    committed = 0
    for ordinal, manifest in enumerate(manifests):
        description = f"adaptive exact closure {ordinal + 1:02d}/{len(manifests):02d} {manifest.name}"
        process = run(
            [
                sys.executable,
                "sair_api.py",
                "submit",
                "--file", str(manifest),
                "--description", description,
                "--commit",
            ],
            environment=environment,
        )
        event = {
            "event": "adaptive-commit-result",
            "ordinal": ordinal,
            "manifest": str(manifest),
            "returncode": process.returncode,
        }
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
    append_log(
        args.log,
        {
            "event": "adaptive-controller-complete",
            "closureJobs": len(rows),
            "resetSubmissionIds": len(reset_ids),
            "availableSlots": remaining,
            "stagedManifests": len(glob.glob(str(prefix) + "_*.txt")),
            "committed": committed,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

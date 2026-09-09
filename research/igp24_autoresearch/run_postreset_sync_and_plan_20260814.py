#!/usr/bin/env python3
"""Sync the reset wave and build a fresh-label exact closure plan."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TOKEN_SOURCE = Path("/path/to/private-file")


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


def queue_ids(path: Path) -> list[str]:
    ids = []
    if not path.exists():
        return ids
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        submission_id = row.get("submissionId")
        if row.get("returncode") == 0 and isinstance(submission_id, str):
            ids.append(submission_id)
    return list(dict.fromkeys(ids))


def run(command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess:
    process = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True)
    print(json.dumps({
        "command": command,
        "returncode": process.returncode,
        "stdoutExcerpt": process.stdout[-2000:],
        "stderrExcerpt": process.stderr[-2000:],
    }), flush=True)
    return process


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue-log",
        type=Path,
        default=ROOT / "data" / "reset_submission_queue_20260814.jsonl",
    )
    parser.add_argument("--wait-for-submissions", type=int, default=16)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--timeout-seconds", type=float, default=4 * 3600)
    parser.add_argument("--output-plan", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output_plan.exists() or args.output_summary.exists():
        raise FileExistsError("post-reset plan output already exists")

    deadline = time.time() + args.timeout_seconds
    while time.time() < deadline:
        ids = queue_ids(args.queue_log)
        if len(ids) >= args.wait_for_submissions:
            break
        print(json.dumps({"event": "waiting-for-queue", "submissionIds": len(ids)}), flush=True)
        time.sleep(args.poll_seconds)
    else:
        raise TimeoutError("reset queue did not reach the requested submission count")

    environment = os.environ.copy()
    environment["SAIR_API_KEY"] = token()
    # Sync each known reset submission.  Revisit incomplete submissions until
    # every one in this initial adaptive window is fully classified.
    pending = set(ids[: args.wait_for_submissions])
    while pending and time.time() < deadline:
        next_pending = set()
        for submission_id in sorted(pending):
            process = run([sys.executable, "sair_api.py", "sync", submission_id], environment)
            if process.returncode != 0:
                next_pending.add(submission_id)
                continue
            try:
                row = json.loads(process.stdout)
            except json.JSONDecodeError:
                next_pending.add(submission_id)
                continue
            if int(row.get("queued", row.get("queuedCount", 0)) or 0) > 0:
                next_pending.add(submission_id)
        pending = next_pending
        if pending:
            time.sleep(args.poll_seconds)
    if pending:
        raise TimeoutError(f"{len(pending)} reset submissions remain unverified")

    command = [
        sys.executable,
        "build_live_index24_pair_resolvent_plan_20260813.py",
        "--max-team-count",
        "15",
        "--sources-per-signature",
        "1",
        "--output",
        str(args.output_plan),
        "--summary",
        str(args.output_summary),
    ]
    for prior in [
        "data/current_fullmap_index24_pair_plan_20260813.jsonl",
        "data/current_postarchive_index24_pair_novel_plan_20260813.jsonl",
        "data/current_postarchive_index24_pair_plan_20260813.jsonl",
        "data/current_postarchive_index24_pair_deep_plan_20260813.jsonl",
        "data/current_postarchive_index24_pair_deeper_plan_20260813.jsonl",
        "data/current_postarchive_index24_pair_ultradeep_plan_20260813.jsonl",
        "data/current_postarchive_index24_pair_extreme_pairmap_plan_20260813.jsonl",
    ]:
        path = ROOT / prior
        if path.is_file():
            command.extend(["--exclude-plan", str(path)])
    result = run(command, environment)
    if result.returncode != 0:
        raise RuntimeError("fresh-label closure planning failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

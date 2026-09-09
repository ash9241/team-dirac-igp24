#!/usr/bin/env python3
"""Commit the immutable 2026-08-14 reset queue with receipt-safe guards."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TOKEN_SOURCE = Path("/path/to/private-file")


def load_token() -> str:
    tree = ast.parse(TOKEN_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        if any(isinstance(target, ast.Name) and target.id == "TEAM_AUTH_TOKEN" for target in targets):
            token = node.value.value.strip()
            if token:
                return token
    raise RuntimeError("TEAM_AUTH_TOKEN not found")


def receipted_manifest_hashes() -> set[str]:
    hashes = set()
    for path in (ROOT / "receipts").glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            value = receipt.get("manifestHash")
            if isinstance(value, str) and len(value) == 64:
                hashes.add(value)
        except (OSError, json.JSONDecodeError):
            continue
    return hashes


def append_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def parse_utc(value: str) -> float:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--start-at must include a timezone")
    return parsed.timestamp()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--file", action="append", type=Path, default=[])
    parser.add_argument("--glob", action="append", default=[])
    parser.add_argument("--max-submissions", type=int, default=143)
    parser.add_argument("--delay", type=float, default=1.25)
    parser.add_argument(
        "--log",
        type=Path,
        default=ROOT / "data" / "reset_submission_queue_20260814.jsonl",
    )
    args = parser.parse_args()

    manifests = [path.resolve() for path in args.file]
    for pattern in args.glob:
        manifests.extend(sorted(ROOT.glob(pattern)))
    if not manifests:
        raise ValueError("queue is empty")
    if len(manifests) > args.max_submissions:
        raise ValueError(f"queue has {len(manifests)} files, above cap {args.max_submissions}")
    if len(manifests) != len(set(manifests)):
        raise ValueError("queue contains duplicate paths")
    for path in manifests:
        if not path.is_file():
            raise FileNotFoundError(path)

    start = parse_utc(args.start_at)
    while True:
        remaining = start - time.time()
        if remaining <= 0:
            break
        print(json.dumps({"event": "waiting", "secondsRemaining": round(remaining, 1)}), flush=True)
        time.sleep(min(30.0, remaining))

    token = load_token()
    environment = os.environ.copy()
    environment["SAIR_API_KEY"] = token
    committed = 0
    for ordinal, manifest in enumerate(manifests):
        rendered = manifest.read_bytes()
        manifest_hash = hashlib.sha256(rendered).hexdigest()
        if manifest_hash in receipted_manifest_hashes():
            event = {
                "event": "skip-receipted",
                "ordinal": ordinal,
                "manifest": str(manifest),
                "manifestHash": manifest_hash,
                "at": time.time(),
            }
            append_log(args.log, event)
            print(json.dumps(event), flush=True)
            continue
        description = f"reset priority {ordinal + 1:03d}/{len(manifests):03d} {manifest.name}"
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "sair_api.py"),
                "submit",
                "--file",
                str(manifest),
                "--description",
                description,
                "--commit",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
        )
        output = (process.stdout + process.stderr).strip()
        event = {
            "event": "commit-result",
            "ordinal": ordinal,
            "manifest": str(manifest),
            "manifestHash": manifest_hash,
            "returncode": process.returncode,
            "at": time.time(),
        }
        if process.returncode == 0:
            try:
                response = json.loads(process.stdout)
                event["submissionId"] = response["submissionId"]
                event["polynomials"] = response["polynomials"]
            except (json.JSONDecodeError, KeyError):
                event["outputExcerpt"] = output[:500]
                append_log(args.log, event)
                print(json.dumps(event), flush=True)
                raise RuntimeError("successful subprocess returned an ambiguous response")
            committed += 1
            append_log(args.log, event)
            print(json.dumps(event), flush=True)
            time.sleep(args.delay)
            continue

        # A 429 immediately around midnight can race the quota reset.  It is
        # safe to retry because SAIR did not accept the POST.  All other errors
        # are deliberately terminal because their commit state may be unclear.
        event["outputExcerpt"] = output[:1000]
        append_log(args.log, event)
        print(json.dumps(event), flush=True)
        if "SAIR HTTP 429" in output:
            print(json.dumps({"event": "quota-race-retry", "afterSeconds": 10}), flush=True)
            time.sleep(10)
            # Re-run this exact manifest once through a fresh child invocation
            # by replacing the current process in a bounded inner retry.
            retry = subprocess.run(
                [sys.executable, str(ROOT / "sair_api.py"), "submit", "--file", str(manifest),
                 "--description", description, "--commit"],
                cwd=ROOT, env=environment, text=True, capture_output=True,
            )
            retry_output = (retry.stdout + retry.stderr).strip()
            retry_event = event | {"event": "quota-race-retry-result", "returncode": retry.returncode, "at": time.time()}
            if retry.returncode == 0:
                response = json.loads(retry.stdout)
                retry_event["submissionId"] = response["submissionId"]
                retry_event["polynomials"] = response["polynomials"]
                append_log(args.log, retry_event)
                print(json.dumps(retry_event), flush=True)
                committed += 1
                time.sleep(args.delay)
                continue
            retry_event["outputExcerpt"] = retry_output[:1000]
            append_log(args.log, retry_event)
            print(json.dumps(retry_event), flush=True)
        raise RuntimeError(f"submission queue stopped at {manifest.name}")

    event = {"event": "queue-complete", "committed": committed, "files": len(manifests), "at": time.time()}
    append_log(args.log, event)
    print(json.dumps(event), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

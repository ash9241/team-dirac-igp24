#!/usr/bin/env python3
"""Submit the sealed endgame queue after the UTC daily-limit reset."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--not-before must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def append_event(path: Path, event: dict) -> None:
    payload = {"at": utc_now().isoformat(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )


def resolve(patterns: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        absolute = pattern if Path(pattern).is_absolute() else str(ROOT / pattern)
        paths.update(Path(value).resolve() for value in glob.glob(absolute))
    return sorted(path for path in paths if path.is_file() and path.stat().st_size > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--not-before", required=True)
    parser.add_argument("--priority", action="append", default=[])
    parser.add_argument("--bulk", action="append", default=[])
    parser.add_argument("--max-submissions", type=int, default=199)
    parser.add_argument("--inter-submit-seconds", type=float, default=0.25)
    parser.add_argument(
        "--journal",
        type=Path,
        default=ROOT / "data" / "endgame_reset_submitter_20260814.jsonl",
    )
    args = parser.parse_args()
    if "SAIR_API_KEY" not in os.environ:
        raise RuntimeError("SAIR_API_KEY is required in the process environment")
    if args.max_submissions < 1 or args.inter_submit_seconds < 0:
        raise ValueError("invalid submission bounds")

    not_before = parse_utc(args.not_before)
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    append_event(args.journal, {
        "event": "armed",
        "notBefore": not_before.isoformat(),
        "maxSubmissions": args.max_submissions,
        "pid": os.getpid(),
    })
    while utc_now() < not_before:
        time.sleep(min(30.0, max(0.1, (not_before - utc_now()).total_seconds())))

    # Refresh the immutable local boundary immediately before collecting the
    # queue.  A failure is fatal: no stale-boundary submissions are attempted.
    refreshed = run([sys.executable, "sair_api.py", "targets"])
    append_event(args.journal, {
        "event": "target_refresh",
        "exitCode": refreshed.returncode,
        "stdout": refreshed.stdout[-2000:],
        "stderr": refreshed.stderr[-2000:],
    })
    if refreshed.returncode != 0:
        return 2

    priority = resolve(args.priority)
    bulk = [path for path in resolve(args.bulk) if path not in set(priority)]
    queue = (priority + bulk)[: args.max_submissions]
    append_event(args.journal, {
        "event": "queue_frozen",
        "priorityCount": len(priority),
        "bulkCount": len(bulk),
        "queuedCount": len(queue),
        "queue": [str(path.relative_to(ROOT)) for path in queue],
    })
    if not queue:
        return 3

    committed = 0
    for position, manifest in enumerate(queue, 1):
        relative = str(manifest.relative_to(ROOT))
        data = manifest.read_bytes()
        lines = sum(bool(raw.split(b"#", 1)[0].strip()) for raw in data.splitlines())
        manifest_hash = hashlib.sha256(data).hexdigest()
        description = (
            f"IGP24 endgame reset queue {position:03d}/{len(queue):03d}: "
            f"{manifest.stem}, {lines} locally certified irreducible fields"
        )
        command = [
            sys.executable,
            "sair_api.py",
            "submit",
            "--file",
            relative,
            "--description",
            description,
            "--commit",
        ]
        result = run(command)
        event = {
            "event": "submit",
            "position": position,
            "manifest": relative,
            "manifestSha256": manifest_hash,
            "rows": lines,
            "exitCode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
        append_event(args.journal, event)
        if result.returncode != 0:
            append_event(args.journal, {
                "event": "stopped_on_error",
                "committed": committed,
                "failedManifest": relative,
            })
            return 4
        committed += 1
        if args.inter_submit_seconds:
            time.sleep(args.inter_submit_seconds)

    append_event(args.journal, {"event": "complete", "committed": committed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

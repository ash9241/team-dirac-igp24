#!/usr/bin/env sage -python
"""Profile explicit degree-24 transitive groups in one resumable Sage process.

The worker intentionally has no database, network, or submission dependencies.
It calls ``pair_signature_one.signature_profiles`` sequentially and atomically
checkpoints every certified label.  Re-running the same command skips certified
rows whose label and transitive-group index still match.
"""

from __future__ import annotations

import argparse
import fcntl
import gc
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
SINGLE_WORKER = ROOT / "pair_signature_one.sage.py"
HEAVY_WORKER_LOCK = ROOT / "data" / ".low_contention_sequential.lock"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")


class CheckpointError(ValueError):
    """Raised when an existing checkpoint cannot be safely resumed."""


def load_single_worker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_igp_pair_signature_one", SINGLE_WORKER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load signature worker: {SINGLE_WORKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_label(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise argparse.ArgumentTypeError(
            f"{label!r} is not an explicit degree-24 label such as 24T123"
        )
    return int(match.group(1))


def canonical_jsonl(rows: list[dict]) -> str:
    return "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )


def atomic_write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_jsonl(rows))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_checkpoint(path: Path, requested: dict[str, int]) -> dict[str, dict]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise CheckpointError(f"checkpoint is not a regular file: {path}")

    completed: dict[str, dict] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise CheckpointError(
                f"invalid JSON checkpoint row at {path}:{line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise CheckpointError(
                f"non-object checkpoint row at {path}:{line_number}"
            )
        label = str(row.get("sourceLabel", ""))
        if label not in requested:
            raise CheckpointError(
                f"checkpoint label {label!r} is outside the requested batch"
            )
        if label in completed:
            raise CheckpointError(f"duplicate checkpoint label: {label}")
        try:
            source_t = int(row.get("sourceT", -1))
        except (TypeError, ValueError) as exc:
            raise CheckpointError(
                f"checkpoint row has invalid sourceT: {label}"
            ) from exc
        if (
            row.get("status") != "certified"
            or source_t != requested[label]
            or not isinstance(row.get("profiles"), list)
            or not isinstance(row.get("length24OrbitCount"), int)
        ):
            raise CheckpointError(
                f"checkpoint row is not a matching certified profile: {label}"
            )
        completed[label] = row
    return completed


def ordered_rows(labels: list[str], completed: dict[str, dict]) -> list[dict]:
    return [completed[label] for label in labels if label in completed]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="dedicated JSONL checkpoint; no project cache is used implicitly",
    )
    parser.add_argument(
        "labels",
        nargs="+",
        metavar="24Tn",
        help="explicit degree-24 transitive-group labels",
    )
    args = parser.parse_args()

    seen: set[str] = set()
    duplicates: list[str] = []
    for label in args.labels:
        parse_label(label)
        if label in seen:
            duplicates.append(label)
        seen.add(label)
    if duplicates:
        parser.error(f"duplicate labels: {', '.join(sorted(set(duplicates)))}")
    return args


def run_batch(args: argparse.Namespace) -> dict:
    output = args.output.expanduser().resolve()
    labels = list(args.labels)
    requested = {label: parse_label(label) for label in labels}
    completed = read_checkpoint(output, requested)
    cached = len(completed)
    signature_worker = load_single_worker()
    started = time.monotonic()

    for position, label in enumerate(labels, start=1):
        if label in completed:
            continue
        label_started = time.monotonic()
        t = requested[label]
        try:
            profile = signature_worker.signature_profiles(label, t)
        except BaseException:
            # Every prior label is already durable.  Collect temporary GAP
            # objects before surfacing the original failure.
            gc.collect()
            libgap.collect()
            raise
        profile["status"] = "certified"
        completed[label] = profile
        atomic_write(output, ordered_rows(labels, completed))
        elapsed = time.monotonic() - label_started
        print(
            f"profiled {position}/{len(labels)} {label} in {elapsed:.3f}s",
            file=sys.stderr,
            flush=True,
        )

        # The returned checkpoint contains only Python primitives.  Explicitly
        # release each group's GAP action objects before constructing the next
        # group so a long batch stays within one group's working-set envelope.
        del profile
        gc.collect()
        libgap.collect()

    return {
        "status": "complete",
        "requestedLabels": len(labels),
        "cachedCertified": cached,
        "profiledThisRun": len(completed) - cached,
        "certified": len(completed),
        "elapsedSeconds": round(time.monotonic() - started, 6),
        "output": str(output),
        "networkCalls": 0,
        "submissionCalls": 0,
    }


def main() -> int:
    args = parse_args()
    HEAVY_WORKER_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with HEAVY_WORKER_LOCK.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("shared Sage/GAP heavy-worker lock is busy") from exc
        summary = run_batch(args)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CheckpointError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

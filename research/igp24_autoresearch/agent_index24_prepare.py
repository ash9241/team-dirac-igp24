#!/usr/bin/env python3
"""Freeze locally owned labels and the current 23,018-pair gold target set."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
OUTPUT = DATA / "agent_index24_group_input.jsonl"
META = DATA / "agent_index24_group_input_summary.json"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")


def atomic(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_extra_owned(values: list[str]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for value in values:
        try:
            label, raw_signatures = value.split(":", 1)
        except ValueError as exc:
            raise ValueError(
                f"invalid --extra-owned value {value!r}; expected 24Tn:r[,r...]"
            ) from exc
        if LABEL_RE.fullmatch(label) is None:
            raise ValueError(f"invalid degree-24 label in --extra-owned: {label!r}")
        signatures = [int(raw) for raw in raw_signatures.split(",") if raw]
        if not signatures or any(r < 0 or r > 24 or r % 2 for r in signatures):
            raise ValueError(f"invalid real-root signature list in --extra-owned: {value!r}")
        result[label].update(signatures)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=META)
    parser.add_argument(
        "--extra-owned",
        action="append",
        default=[],
        metavar="24Tn:r[,r...]",
        help="add an exactly certified in-flight source pair to the frozen input",
    )
    parser.add_argument(
        "--extra-owned-input",
        action="append",
        type=Path,
        default=[],
        help="carry forward owned source signatures from a prior frozen group input",
    )
    parser.add_argument(
        "--extra-owned-submission",
        action="append",
        default=[],
        help="treat every accepted pair in a fully adjudicated submission as owned",
    )
    parser.add_argument("--expected-gold-pairs", type=int)
    args = parser.parse_args()
    if args.output.resolve() == args.summary.resolve():
        parser.error("--output and --summary must be distinct")
    extra_owned = parse_extra_owned(args.extra_owned)
    for path in args.extra_owned_input:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            if not bool(row.get("isOwnedSource")):
                continue
            label = str(row.get("label"))
            if LABEL_RE.fullmatch(label) is None:
                raise ValueError(f"invalid owned label in {path}:{line_number}: {label!r}")
            signatures = {int(value) for value in row.get("sourceR") or []}
            if any(r < 0 or r > 24 or r % 2 for r in signatures):
                raise ValueError(f"invalid sourceR in {path}:{line_number}")
            extra_owned[label].update(signatures)

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    for submission_id in args.extra_owned_submission:
        submission = connection.execute(
            "SELECT queued_count,failed_count FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        if submission is None or int(submission[0]) or int(submission[1]):
            raise ValueError(f"extra-owned submission is not fully accepted: {submission_id}")
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE submission_id=? AND status='accepted'",
            (submission_id,),
        ):
            extra_owned[str(label)].add(int(r))
    owned = defaultdict(set)
    for label, r in connection.execute(
        "SELECT DISTINCT label,r FROM verifications WHERE status='accepted'"
    ):
        owned[str(label)].add(int(r))
    for label, signatures in extra_owned.items():
        owned[label].update(signatures)
    baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
    owned_pairs = {(label, r) for label, values in owned.items() for r in values}
    gold = defaultdict(set)
    generated = []
    target_rows = 0
    for label, r, team_count, generated_at in connection.execute(
        "SELECT label,r,team_count,generated_at FROM targets"
    ):
        target_rows += 1
        generated.append(str(generated_at))
        pair = (str(label), int(r))
        if int(team_count) == 0 and pair not in baseline and pair not in owned_pairs:
            gold[pair[0]].add(pair[1])
    connection.close()

    labels = sorted(set(owned) | set(gold), key=lambda label: int(label[3:]))
    rows = [
        {
            "goldR": sorted(gold.get(label, set())),
            "isGoldTarget": label in gold,
            "isOwnedSource": label in owned,
            "label": label,
            "sourceR": sorted(owned.get(label, set())),
            "t": int(label[3:]),
        }
        for label in labels
    ]
    atomic(
        args.output,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows),
    )
    summary = {
        "frozenGoldLabels": len(gold),
        "frozenGoldPairs": sum(len(values) for values in gold.values()),
        "frozenOwnedLabels": len(owned),
        "extraOwnedLabels": len(extra_owned),
        "extraOwnedPairs": sum(len(values) for values in extra_owned.values()),
        "extraOwnedInputs": [str(path.resolve()) for path in args.extra_owned_input],
        "extraOwnedSubmissions": args.extra_owned_submission,
        "targetGeneratedAtMax": max(generated),
        "targetGeneratedAtMin": min(generated),
        "targetRows": target_rows,
        "unionLabels": len(rows),
    }
    if (
        args.expected_gold_pairs is not None
        and summary["frozenGoldPairs"] != args.expected_gold_pairs
    ):
        raise RuntimeError(
            f"expected frozen {args.expected_gold_pairs:,} gold pairs, "
            f"got {summary['frozenGoldPairs']}"
        )
    atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

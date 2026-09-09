#!/usr/bin/env python3
"""Freeze the genuinely new post-2022 F6 unordered-pair source stratum.

The output is an input file for ``agent_index24_missing_pair_census.sage.py``.
Only scoreable source labels first used on or after 2026-07-22 and absent from
every saved unordered-pair action map are marked as owned sources.  All current
target labels are retained so the GAP census can intersect its exact actions
with the live tc0 signatures without reading the ledger itself.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
DEFAULT_DIRECTORY = DATA / "campaign_20260727_f627"
DEFAULT_INPUT = DEFAULT_DIRECTORY / "f6_post22_pair_revival_input.jsonl"
DEFAULT_PLAN = DEFAULT_DIRECTORY / "f6_post22_pair_revival_plan.json"
SOURCE_CUTOFF = "2026-07-22T00:00:00Z"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def known_pair_action_labels() -> tuple[set[str], list[str]]:
    patterns = (
        "data/agent_index24_pair_orbit_map.jsonl",
        "data/agent_index24_missing_pair_shard*.jsonl",
        "data/autopilot_pair_delta_20260722_v*/missing_pair_all.jsonl",
    )
    labels: set[str] = set()
    paths: list[str] = []
    for pattern in patterns:
        for raw_path in sorted(glob.glob(str(ROOT / pattern))):
            path = Path(raw_path)
            paths.append(str(path.relative_to(ROOT)))
            for row in read_jsonl(path):
                label = row.get("sourceLabel")
                if label is not None:
                    labels.add(str(label))
    return labels, paths


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def prepare(output: Path, plan_path: Path) -> dict:
    known, prior_paths = known_pair_action_labels()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        scoreable = list(
            connection.execute(
                """
                SELECT v.label,v.t,v.r,s.created_at
                FROM verifications v
                JOIN submissions s USING(submission_id)
                WHERE v.scoreable=1
                """
            )
        )
        targets = list(
            connection.execute(
                """
                SELECT label,t,r,team_count,discovered,generated_at
                FROM targets
                """
            )
        )
    finally:
        connection.close()

    source_r: dict[str, set[int]] = defaultdict(set)
    source_t: dict[str, int] = {}
    post_cutoff_labels: set[str] = set()
    for row in scoreable:
        label = str(row["label"])
        source_r[label].add(int(row["r"]))
        source_t[label] = int(row["t"])
        if str(row["created_at"]) >= SOURCE_CUTOFF:
            post_cutoff_labels.add(label)

    selected = sorted(
        post_cutoff_labels - known,
        key=lambda label: int(label.removeprefix("24T")),
    )
    gold_r: dict[str, set[int]] = defaultdict(set)
    target_t: dict[str, int] = {}
    generated_at = set()
    for row in targets:
        label = str(row["label"])
        target_t[label] = int(row["t"])
        generated_at.add(str(row["generated_at"]))
        pair = (label, int(row["r"]))
        if (
            int(row["team_count"]) == 0
            and int(row["discovered"]) == 0
            and pair not in baseline
            and pair not in owned_pairs
        ):
            gold_r[label].add(int(row["r"]))

    labels = sorted(
        set(target_t) | set(selected),
        key=lambda label: int(label.removeprefix("24T")),
    )
    rows = [
        {
            "goldR": sorted(gold_r.get(label, set())),
            "isOwnedSource": label in selected,
            "label": label,
            "sourceR": sorted(source_r.get(label, set())) if label in selected else [],
            "t": int(target_t[label] if label in target_t else source_t[label]),
        }
        for label in labels
    ]
    rendered = "".join(canonical_json(row) + "\n" for row in rows).encode()
    input_hash = sha256(rendered)
    plan = {
        "coefficientMaterialIncluded": False,
        "currentEligibleDefinition": (
            "team_count=0 AND discovered=0 AND nonbaseline AND unowned"
        ),
        "inputPath": str(output.relative_to(ROOT)),
        "inputRows": len(rows),
        "inputSha256": input_hash,
        "knownPairActionLabels": len(known),
        "networkCalls": 0,
        "postCutoffScoreableLabels": len(post_cutoff_labels),
        "priorMapPaths": prior_paths,
        "schemaVersion": "f6-post22-pair-revival-plan-v1",
        "selectedSourceCount": len(selected),
        "selectedSources": [
            {
                "label": label,
                "r": sorted(source_r[label]),
                "t": source_t[label],
            }
            for label in selected
        ],
        "sourceCutoff": SOURCE_CUTOFF,
        "submissionCalls": 0,
        "targetGeneratedAtMax": max(generated_at) if generated_at else None,
    }
    if not selected:
        raise ValueError("post-cutoff pair revival source set is empty")
    atomic_write(output, rendered)
    plan_payload = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    atomic_write(plan_path, plan_payload)
    return {
        "event": "f6_post22_pair_revival_prepared",
        "input": str(output.relative_to(ROOT)),
        "inputSha256": input_hash,
        "plan": str(plan_path.relative_to(ROOT)),
        "planSha256": sha256(plan_payload),
        "selectedSourceCount": len(selected),
        "submissionCalls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output.resolve(), args.plan.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

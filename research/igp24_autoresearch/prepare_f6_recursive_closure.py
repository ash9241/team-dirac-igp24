#!/usr/bin/env python3
"""Freeze the live target gate for the two locally exact recursive F6 sources."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CAMPAIGN = DATA / "campaign_20260727_f627"
INPUT = CAMPAIGN / "f6_recursive_10482_14293_input.jsonl"
PLAN = CAMPAIGN / "f6_recursive_10482_14293_plan.json"
SOURCES = {
    "24T10482": {
        "t": 10482,
        "r": 8,
        "coefficientSha256": "5ce6ddcc01ab2eb96f007ca64d577f86762ab6fc54767421c7897d59a48350c9",
        "batchLineOrdinal": 1,
        "evenPolynomial": True,
    },
    "24T14293": {
        "t": 14293,
        "r": 16,
        "coefficientSha256": "61599e26bf79c8b8f51d3379432c8a46b0e08b5ed9ee7e0e0d698df4bbed182c",
        "batchLineOrdinal": 2,
        "evenPolynomial": False,
    },
}


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def atomic_new(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        targets = list(
            connection.execute(
                "SELECT label,t,r,team_count,discovered,generated_at FROM targets"
            )
        )
    finally:
        connection.close()
    gold = defaultdict(set)
    target_t = {}
    generated = set()
    for row in targets:
        label = str(row["label"])
        target_t[label] = int(row["t"])
        generated.add(str(row["generated_at"]))
        pair = (label, int(row["r"]))
        if (
            int(row["team_count"]) == 0
            and int(row["discovered"]) == 0
            and pair not in baseline
            and pair not in owned
        ):
            gold[label].add(int(row["r"]))
    labels = sorted(
        set(target_t) | set(SOURCES),
        key=lambda label: int(label.removeprefix("24T")),
    )
    rows = [
        {
            "goldR": sorted(gold.get(label, set())),
            "isOwnedSource": label in SOURCES,
            "label": label,
            "sourceR": [int(SOURCES[label]["r"])] if label in SOURCES else [],
            "t": int(SOURCES[label]["t"] if label in SOURCES else target_t[label]),
        }
        for label in labels
    ]
    input_payload = "".join(canonical_json(row) + "\n" for row in rows).encode()
    plan = {
        "schemaVersion": "f6-recursive-10482-14293-plan-v1",
        "inputPath": str(INPUT.relative_to(ROOT)),
        "inputSha256": hashlib.sha256(input_payload).hexdigest(),
        "inputRows": len(rows),
        "currentEligibleDefinition": (
            "team_count=0 AND discovered=0 AND nonbaseline AND unowned"
        ),
        "targetGeneratedAtMax": max(generated) if generated else None,
        "sourceReceipt": (
            "data/campaign_20260727_f627/"
            "f6_post22_unique_wave_cumulative_certificate.json"
        ),
        "sources": SOURCES,
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    plan_payload = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    atomic_new(INPUT, input_payload)
    atomic_new(PLAN, plan_payload)
    print(
        canonical_json(
            {
                "input": str(INPUT.relative_to(ROOT)),
                "inputSha256": plan["inputSha256"],
                "plan": str(PLAN.relative_to(ROOT)),
                "planSha256": hashlib.sha256(plan_payload).hexdigest(),
                "sourceCount": 2,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freeze the live unordered-pair census input for exact F6 recursive sources."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CAMPAIGN = DATA / "campaign_20260727_f627"
DB = DATA / "ledger.sqlite3"
CERTIFICATE = CAMPAIGN / "f6_post22_unique_wave_cumulative_certificate.json"
OUTPUT = CAMPAIGN / "f6_recursive_16948_16949_input.jsonl"
PLAN = CAMPAIGN / "f6_recursive_16948_16949_input_plan.json"
SOURCE_LABELS = {"24T16948", "24T16949"}


def canonical(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    if OUTPUT.exists() or PLAN.exists():
        raise FileExistsError("refusing to overwrite recursive census input")
    certificate = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
    pins = {}
    for hit in certificate["hits"]:
        gate = hit["targetGate"]
        label = str(gate["label"])
        if label not in SOURCE_LABELS:
            continue
        line = str(hit["candidateCoefficientLine"])
        digest = sha(line)
        if digest != str(hit["candidateChecks"]["coefficientSha256"]):
            raise ValueError(f"candidate hash mismatch for {label}")
        pins[label] = {
            "coefficientLine": line,
            "coefficientSha256": digest,
            "r": int(gate["r"]),
            "t": int(label.removeprefix("24T")),
        }
    if set(pins) != SOURCE_LABELS:
        raise ValueError("recursive source pins are incomplete")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    baseline = {
        (str(row["label"]), int(row["r"]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(row["label"]), int(row["r"]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
        )
    }
    rows = []
    generated_at = set()
    for row in connection.execute(
        "SELECT label,t,r,team_count,discovered,generated_at FROM targets"
    ):
        label = str(row["label"])
        generated_at.add(str(row["generated_at"]))
        pair = (label, int(row["r"]))
        gold = (
            int(row["team_count"]) == 0
            and int(row["discovered"]) == 0
            and pair not in baseline
            and pair not in owned
        )
        if not rows or str(rows[-1]["label"]) != label:
            rows.append(
                {
                    "goldR": [],
                    "isOwnedSource": label in SOURCE_LABELS,
                    "label": label,
                    "sourceR": [pins[label]["r"]] if label in SOURCE_LABELS else [],
                    "t": int(row["t"]),
                }
            )
        if gold:
            rows[-1]["goldR"].append(int(row["r"]))
    connection.close()
    if len(rows) != 25000:
        raise ValueError(f"expected 25,000 target labels, got {len(rows)}")
    payload = "".join(canonical(row) + "\n" for row in rows)
    input_sha = sha(payload)
    plan = {
        "certificate": str(CERTIFICATE.relative_to(ROOT)),
        "coefficientMaterialIncludedInInput": False,
        "input": str(OUTPUT.relative_to(ROOT)),
        "inputSha256": input_sha,
        "networkCalls": 0,
        "schemaVersion": "f6-recursive-16948-16949-input-v1",
        "sources": [
            {
                "coefficientSha256": pins[label]["coefficientSha256"],
                "label": label,
                "r": pins[label]["r"],
                "t": pins[label]["t"],
            }
            for label in sorted(SOURCE_LABELS)
        ],
        "submissionCalls": 0,
        "targetGeneratedAt": sorted(generated_at),
    }
    atomic_write(OUTPUT, payload)
    atomic_write(PLAN, json.dumps(plan, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "input": str(OUTPUT.relative_to(ROOT)),
                "inputSha256": input_sha,
                "plan": str(PLAN.relative_to(ROOT)),
                "sourceCount": len(pins),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

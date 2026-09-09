#!/usr/bin/env python3
"""Independently validate and stage the four refreshed tc3 pair routes."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox" / "low_contention_hc3_refresh_20260812.txt"
CERTIFICATE = DATA / "low_contention_hc3_refresh_stage_20260812.json"
ROUTES = (
    ("low_contention_hc3_001_24T17550_r24_to_24T16867_r24_result.jsonl", "24T16867"),
    ("low_contention_hc3_002_24T15895_r24_to_24T15119_r24_result.jsonl", "24T15119"),
    ("low_contention_hc3_003_24T22795_r24_to_24T22806_r24_result.jsonl", "24T22806"),
    ("low_contention_hc3_004_24T20834_r24_to_24T20794_r24_result.jsonl", "24T20794"),
)


def main() -> int:
    if OUTBOX.exists() or CERTIFICATE.exists():
        raise FileExistsError("refusing to overwrite hc3 stage outputs")
    rows = []
    for filename, target in ROUTES:
        lines = [line for line in (DATA / filename).read_text(encoding="utf-8").splitlines() if line]
        if len(lines) != 1:
            raise ValueError(f"{filename} does not contain exactly one candidate")
        row = json.loads(lines[0])
        if row.get("status") != "certified" or row.get("targetLabel") != target:
            raise ValueError(f"route certificate mismatch in {filename}")
        if int(row.get("targetR", -1)) != 24:
            raise ValueError(f"route signature mismatch in {filename}")
        digest = hashlib.sha256(row["coefficientLine"].encode("ascii")).hexdigest()
        if digest != row.get("coefficientSha256"):
            raise ValueError(f"candidate hash mismatch in {filename}")
        rows.append(row)
    if len({row["coefficientSha256"] for row in rows}) != len(rows):
        raise ValueError("duplicate hc3 candidate hashes")

    commands = []
    for row in rows:
        commands.append(
            "p=Polrev([" + row["coefficientLine"] + "]);"
            "print(polisirreducible(p),\" \",poldegree(p),\" \",polsturm(p),\" \",abs(nfdisc(p)));"
        )
    process = subprocess.run(
        ["gp", "-q"], input="\n".join(commands) + "\n", text=True,
        capture_output=True, timeout=900, check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr[-2000:])
    tokens = process.stdout.split()
    if len(tokens) != 16:
        raise ValueError("GP validation returned the wrong token count")
    for index, row in enumerate(rows):
        irreducible, degree, roots, nfdisc = tokens[4 * index:4 * index + 4]
        if irreducible != "1" or degree != "24" or roots != "24":
            raise ValueError(f"GP validation failed for {row['coefficientSha256']}")
        row["independentFieldDiscriminantAbs"] = nfdisc
    OUTBOX.write_text("".join(row["coefficientLine"] + "\n" for row in rows), encoding="utf-8")
    CERTIFICATE.write_text(
        json.dumps({"candidates": rows, "networkCalls": 0, "submissionCalls": 0}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"certificate": str(CERTIFICATE), "manifest": str(OUTBOX), "polynomials": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

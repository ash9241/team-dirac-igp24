#!/usr/bin/env python3
"""Validate and stage candidates produced by one certified pair runbook batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    manifest = ROOT / "outbox" / f"{args.tag}.txt"
    stage = ROOT / "data" / f"{args.tag}_stage.json"
    if manifest.exists() or stage.exists():
        raise FileExistsError("refusing to overwrite pair stage outputs")
    certificate = json.loads(args.certificate.read_text(encoding="utf-8"))
    candidates = []
    for runbook in certificate.get("runbooks", []):
        result_path = ROOT / runbook["output"]
        rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines() if line]
        if len(rows) != 1:
            raise ValueError(f"expected one candidate in {result_path}")
        row = rows[0]
        target = runbook["target"]
        if (
            row.get("status") != "certified"
            or row.get("targetLabel") != target["label"]
            or int(row.get("targetR", -1)) != int(target["r"])
        ):
            raise ValueError(f"result/runbook mismatch in {result_path}")
        digest = hashlib.sha256(row["coefficientLine"].encode("ascii")).hexdigest()
        if digest != row.get("coefficientSha256"):
            raise ValueError(f"hash mismatch in {result_path}")
        candidates.append({**row, "routeId": runbook["routeId"]})
    if len({row["coefficientSha256"] for row in candidates}) != len(candidates):
        raise ValueError("duplicate candidate hashes")

    commands = [
        "p=Polrev([" + row["coefficientLine"] + "]);"
        "print(polisirreducible(p),\" \",poldegree(p),\" \",polsturm(p),\" \",abs(nfdisc(p)));"
        for row in candidates
    ]
    process = subprocess.run(
        ["gp", "-q"], input="\n".join(commands) + "\n", text=True,
        capture_output=True, timeout=1800, check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr[-4000:])
    tokens = process.stdout.split()
    if len(tokens) != 4 * len(candidates):
        raise ValueError("GP validation token-count mismatch")
    for index, row in enumerate(candidates):
        irreducible, degree, roots, nfdisc = tokens[4 * index:4 * index + 4]
        if (
            irreducible != "1" or degree != "24"
            or int(roots) != int(row["targetR"])
        ):
            raise ValueError(f"GP validation failed for {row['routeId']}")
        row["independentFieldDiscriminantAbs"] = nfdisc
    manifest.write_text("".join(row["coefficientLine"] + "\n" for row in candidates), encoding="utf-8")
    stage.write_text(
        json.dumps({"candidates": candidates, "networkCalls": 0, "submissionCalls": 0}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"manifest": str(manifest), "polynomials": len(candidates), "stage": str(stage)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

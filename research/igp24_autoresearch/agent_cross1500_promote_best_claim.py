#!/usr/bin/env python3
"""Promote a better exact Cross-1500 candidate into an existing pair claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
CLAIMS = ROOT / "data" / "agent_cross1500_stage1_pair_claims"


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--r", type=int, required=True)
    parser.add_argument("--global-index", type=int, required=True)
    parser.add_argument("--candidate-owner", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.output.read_text(encoding="utf-8"))
    expected_status = f"certified_{args.label}_r{args.r}"
    candidates = [
        row
        for row in payload.get("search", {}).get("candidateResults", [])
        if row.get("status") == expected_status
        and row.get("irreducible") is True
        and int(row.get("realRoots", -1)) == args.r
        and row.get("maximalSubgroupCertificate", {}).get("complete") is True
        and row.get("containmentProof", {}).get("sameDegree12Field") is True
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one exact candidate, found {len(candidates)}")
    candidate = candidates[0]
    line = str(candidate["candidateCoefficientLine"])
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(candidate["candidateSha256"]):
        raise ArithmeticError("candidate hash mismatch")
    candidate_disc = int(candidate["fieldDiscriminantAbs"])
    pair = (args.label, args.r)
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        target = connection.execute(
            "SELECT team_count FROM targets WHERE label=? AND r=?", pair
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
        ).fetchone()
        owned = int(
            connection.execute(
                "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
                pair,
            ).fetchone()[0]
        )
        hash_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        )
    if target != (0,) or baseline is not None or owned or hash_rows:
        raise ValueError(
            f"pair/candidate is no longer stageable: target={target}, baseline={baseline}, "
            f"owned={owned}, hashRows={hash_rows}"
        )

    claim_path = CLAIMS / f"{args.label}_r{args.r}.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    history = list(claim.get("selectionHistory", []))
    previous = {
        key: claim.get(key)
        for key in (
            "candidateSha256",
            "candidateFieldDiscAbs",
            "selectedCandidateOwner",
            "globalCommandIndex",
            "outputPath",
            "promotedAtUnix",
        )
        if claim.get(key) is not None
    }
    if "candidateFieldDiscAbs" not in previous:
        previous["candidateFieldDiscAbs"] = None
    history.append(previous)
    claim.update(
        {
            "candidateSha256": digest,
            "candidateFieldDiscAbs": str(candidate_disc),
            "selectedCandidateOwner": args.candidate_owner,
            "globalCommandIndex": args.global_index,
            "outputPath": str(args.output.resolve()),
            "promotedAtUnix": time.time(),
            "selectionHistory": history,
        }
    )
    write_atomic(
        claim_path,
        json.dumps(claim, separators=(",", ":"), sort_keys=True) + "\n",
    )
    write_atomic(args.manifest, line + "\n")
    manifest_sha = hashlib.sha256((line + "\n").encode("ascii")).hexdigest()
    print(
        json.dumps(
            {
                "label": args.label,
                "r": args.r,
                "candidateSha256": digest,
                "fieldDiscriminantAbs": str(candidate_disc),
                "claim": str(claim_path.relative_to(ROOT)),
                "manifest": str(args.manifest.relative_to(ROOT)),
                "manifestSha256": manifest_sha,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

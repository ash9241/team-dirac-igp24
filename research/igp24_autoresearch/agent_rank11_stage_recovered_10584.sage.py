#!/usr/bin/env sage -python
"""Audit, score, and stage the recovered exact 24T11164/r24 sibling."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PACKETS = DATA / "team1_swing_candidates.jsonl"
CERTIFICATE = DATA / "agent_rank11_pair_stage2_recovered_10584_frobenius.json"
EVIDENCE = DATA / "agent_fresh_rank11_current_sole_pairs.jsonl"
OUTPUT = DATA / "agent_rank11_pair_stage2_recovered_10584_hits.jsonl"
SUMMARY = DATA / "agent_rank11_pair_stage2_recovered_10584_summary.json"
MANIFEST = ROOT / "outbox" / "agent_rank11_pair_stage2_recovered_10584_live.txt"
SOURCE = ("sub_bc5ef08343ef4cd0a5d1d6432cc5054e", 0)
TARGET = ("24T11164", 24)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    packet = next(
        row
        for row in read_jsonl(PACKETS)
        if (str(row.get("sourceSubmissionId")), int(row.get("sourcePolynomialIndex")))
        == SOURCE
    )
    certificate = json.loads(CERTIFICATE.read_text())
    proof = certificate["rows"][0]
    if proof["status"] != "resolved":
        raise RuntimeError("recovered packet is not exactly resolved")
    assignments = {
        int(row["factorIndex"]): row for row in proof["assignments"]
    }
    holder = next(
        row
        for row in read_jsonl(EVIDENCE)
        if (str(row["label"]), int(row["r"])) == TARGET
    )
    if str(holder["teamId"]) != "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0":
        raise RuntimeError("rank-11 holder identity mismatch")

    connection = sqlite3.connect(f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True)
    target = connection.execute(
        "SELECT team_count,minimum_disc_abs,generated_at FROM targets WHERE label=? AND r=?",
        TARGET,
    ).fetchone()
    owned = connection.execute(
        "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
        TARGET,
    ).fetchone()[0]
    baseline = connection.execute(
        "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?", TARGET
    ).fetchone()[0]
    ledger_hashes = {
        str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")
    }
    connection.close()
    if target is None or int(target[0]) != 1 or owned or baseline:
        raise RuntimeError(
            f"target no longer stageable: target={target}, owned={owned}, baseline={baseline}"
        )

    ring = PolynomialRing(ZZ, "x")
    hits = []
    for candidate in packet["candidates"]:
        assignment = assignments[int(candidate["factorIndex"])]
        if (str(assignment["targetLabel"]), int(assignment["targetR"])) != TARGET:
            continue
        line = str(candidate["coefficientLine"])
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if digest != str(candidate["coefficientSha256"]):
            raise ArithmeticError("coefficient hash mismatch")
        if digest in ledger_hashes:
            raise RuntimeError("candidate is already in the local ledger")
        polynomial = ring([int(value) for value in line.split(",")])
        if polynomial.degree() != 24 or not polynomial.is_monic() or not polynomial.is_irreducible():
            raise ArithmeticError("candidate failed exact polynomial checks")
        field_disc = abs(int(pari(polynomial).nfdisc()))
        holder_disc = int(holder.get("minScoringDiscAbs") or holder.get("scoringDiscAbs"))
        ratio = min(1.0, math.log(holder_disc) / math.log(field_disc))
        hits.append(
            {
                **candidate,
                "sourceSubmissionId": SOURCE[0],
                "sourcePolynomialIndex": SOURCE[1],
                "sourceLabel": "24T10584",
                "sourceR": 24,
                "targetLabel": TARGET[0],
                "targetT": 11164,
                "targetR": TARGET[1],
                "fieldDiscriminantAbs": str(field_disc),
                "status": "certified_staged",
                "exactLabelProof": "joint_unramified_frobenius_cycle_profiles",
                "liveAudit": {
                    "teamCount": int(target[0]),
                    "minimumDiscAbs": str(target[1]),
                    "targetGeneratedAt": str(target[2]),
                    "ownedRows": int(owned),
                    "baselineRows": int(baseline),
                    "holderEvidence": holder,
                },
                "scoring": {
                    "holderFieldDiscAbs": str(holder_disc),
                    "candidateFieldDiscAbs": str(field_disc),
                    "discRatio": ratio,
                    "candidateScore": 0.5 * ratio,
                    "netRelativeSwing": 0.5 + 0.5 * ratio,
                },
            }
        )
    if len(hits) != 2:
        raise RuntimeError(f"expected two exact recovered factors, got {len(hits)}")
    hits.sort(key=lambda row: int(row["fieldDiscriminantAbs"]))
    atomic_text(
        OUTPUT,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in hits),
    )
    # One signature can score only once; stage the better-discriminant factor.
    atomic_text(MANIFEST, str(hits[0]["coefficientLine"]) + "\n")
    summary = {
        "exactFactors": len(hits),
        "distinctTargetPairs": 1,
        "stagedPolynomials": 1,
        "target": {"label": TARGET[0], "r": TARGET[1]},
        "chosenCoefficientSha256": hits[0]["coefficientSha256"],
        "chosenFieldDiscriminantAbs": hits[0]["fieldDiscriminantAbs"],
        "predictedScore": hits[0]["scoring"]["candidateScore"],
        "predictedNetRelativeSwing": hits[0]["scoring"]["netRelativeSwing"],
        "manifest": str(MANIFEST),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

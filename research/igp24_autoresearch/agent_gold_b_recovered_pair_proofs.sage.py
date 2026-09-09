#!/usr/bin/env sage -python
"""Machine-check the recovered 04:12 pair-resolvent provenance offline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

from sage.all import PolynomialRing, QQ, pari


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
PAIR_MAP = ROOT / "data" / "pair_orbit_map.jsonl"
WORKER = ROOT / "pair_sum_one.sage.py"
OUTPUT = ROOT / "data" / "agent_gold_b_recovered_pair_proofs.json"


CASES = [
    {
        "name": "mixed_index0_10873_r24",
        "sourceSubmissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "sourcePolynomialIndex": 433,
        "expectedSourceLabel": "24T10256",
        "expectedOrbitIndex": 3,
        "targetSubmissionId": "sub_796cd673c2f14658914946ab77277c54",
        "targetPolynomialIndex": 0,
        "expectedTargetLabel": "24T10873",
        "expectedTargetR": 24,
    },
    {
        "name": "mixed_index1_14779_r24_exact",
        "sourceSubmissionId": "sub_e74741c0b79449f38373d14baa31f7c9",
        "sourcePolynomialIndex": 20,
        "expectedSourceLabel": "24T15503",
        "expectedOrbitIndex": 1,
        "targetSubmissionId": "sub_796cd673c2f14658914946ab77277c54",
        "targetPolynomialIndex": 1,
        "expectedTargetLabel": "24T14779",
        "expectedTargetR": 24,
    },
    {
        "name": "mixed_index2_10873_r0",
        "sourceSubmissionId": "sub_b97d316041334d9fa21535acbae1482d",
        "sourcePolynomialIndex": 227,
        "expectedSourceLabel": "24T10256",
        "expectedOrbitIndex": 3,
        "targetSubmissionId": "sub_796cd673c2f14658914946ab77277c54",
        "targetPolynomialIndex": 2,
        "expectedTargetLabel": "24T10873",
        "expectedTargetR": 0,
    },
    {
        "name": "live_r20_route_test_lands_owned_r16",
        "sourceSubmissionId": "sub_796cd673c2f14658914946ab77277c54",
        "sourcePolynomialIndex": 32,
        "expectedSourceLabel": "24T14779",
        "expectedOrbitIndex": 6,
        "targetSubmissionId": None,
        "targetPolynomialIndex": None,
        "expectedTargetLabel": "24T15503",
        "expectedTargetR": 16,
    },
]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ledger_row(connection: sqlite3.Connection, submission_id: str, polynomial_index: int) -> dict:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.t,v.r,v.field_disc_abs,
               s.created_at,t.team_count
        FROM polynomials AS p
        JOIN verifications AS v USING(submission_id,polynomial_index)
        JOIN submissions AS s USING(submission_id)
        LEFT JOIN targets AS t ON t.label=v.label AND t.r=v.r
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"missing ledger row {submission_id}:{polynomial_index}")
    return dict(row)


def pair_map_row(label: str) -> dict:
    for line in PAIR_MAP.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["sourceLabel"] == label:
            return row
    raise ValueError(f"missing pair map for {label}")


def worker(case: dict) -> dict:
    command = [
        "sage",
        "-python",
        str(WORKER),
        case["sourceSubmissionId"],
        str(case["sourcePolynomialIndex"]),
        "--expected-target",
        case["expectedTargetLabel"],
        "--transforms",
        "1",
        "--reduce",
        "best",
        "--nfdisc",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"pair worker failed for {case['name']}: {completed.stderr[-2000:]}"
        )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    result["wallSeconds"] = round(time.monotonic() - started, 3)
    result["stderrTail"] = completed.stderr[-2000:]
    return result


def polynomial(line: str):
    ring = PolynomialRing(QQ, "x")
    return ring([int(value) for value in line.split(",")])


def main() -> int:
    proofs = []
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        for case in CASES:
            source = ledger_row(
                connection,
                case["sourceSubmissionId"],
                case["sourcePolynomialIndex"],
            )
            if source["label"] != case["expectedSourceLabel"]:
                raise RuntimeError(f"source-label mismatch for {case['name']}")
            orbit = pair_map_row(source["label"])
            matching = [
                row
                for row in orbit["targets"]
                if row["targetLabel"] == case["expectedTargetLabel"]
                and int(row["orbitIndex"]) == case["expectedOrbitIndex"]
            ]
            if len(matching) != 1 or len(orbit["targets"]) != 1:
                raise RuntimeError(f"pair action is not unique for {case['name']}")
            result = worker(case)
            if result["targetLabel"] != case["expectedTargetLabel"]:
                raise RuntimeError(f"worker target-label mismatch for {case['name']}")
            if int(result["targetR"]) != case["expectedTargetR"]:
                raise RuntimeError(f"worker target-signature mismatch for {case['name']}")
            proof = {
                "case": case,
                "sourceLedger": source,
                "uniqueOrbitCertificate": matching[0],
                "workerResult": result,
                "workerCoefficientHashRecomputed": sha256_text(result["coefficientLine"]),
            }
            if case["targetSubmissionId"] is not None:
                target = ledger_row(
                    connection,
                    case["targetSubmissionId"],
                    case["targetPolynomialIndex"],
                )
                generated = polynomial(result["coefficientLine"])
                submitted = polynomial(target["coefficients"])
                isomorphisms = pari(submitted).nfisisom(generated)
                proof["targetLedger"] = target
                proof["comparison"] = {
                    "exactCoefficientMatch": target["coefficients"] == result["coefficientLine"],
                    "exactCoefficientHashMatch": target["coefficient_hash"] == result["coefficientSha256"],
                    "fieldDiscriminantMatch": str(target["field_disc_abs"]) == str(result["fieldDiscriminantAbs"]),
                    "nfIsomorphic": bool(isomorphisms),
                    "nfIsomorphismCount": len(isomorphisms) if isomorphisms else 0,
                }
                if not proof["comparison"]["nfIsomorphic"]:
                    raise RuntimeError(f"submitted and rebuilt fields are not isomorphic for {case['name']}")
            else:
                owned = connection.execute(
                    "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
                    (result["targetLabel"], result["targetR"]),
                ).fetchone()[0]
                target = connection.execute(
                    "SELECT team_count,discovered,minimum_disc_abs FROM targets WHERE label=? AND r=?",
                    (result["targetLabel"], result["targetR"]),
                ).fetchone()
                proof["currentTargetState"] = {
                    "ownedScoreableRows": int(owned),
                    "teamCount": int(target[0]) if target else None,
                    "discovered": bool(target[1]) if target else False,
                    "minimumDiscAbs": target[2] if target else None,
                }
            proofs.append(proof)
    payload = {
        "networkCalls": 0,
        "submissionCalls": 0,
        "pairMap": {"path": str(PAIR_MAP.resolve()), "sha256": hashlib.sha256(PAIR_MAP.read_bytes()).hexdigest()},
        "proofs": proofs,
        "allHistoricalRowsProvedIsomorphic": all(
            proof.get("comparison", {}).get("nfIsomorphic", True) for proof in proofs
        ),
        "exactHistoricalCoefficientMatches": sum(
            proof.get("comparison", {}).get("exactCoefficientMatch", False) for proof in proofs
        ),
        "liveRouteTest": {
            "requestedGold": "24T15503/r20",
            "actual": f"{proofs[-1]['workerResult']['targetLabel']}/r{proofs[-1]['workerResult']['targetR']}",
            "valuable": False,
            "reason": "the exact source conjugacy class lands an already-owned signature",
        },
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT.resolve()),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "proofs": len(proofs),
                "status": "ok",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

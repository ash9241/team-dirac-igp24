#!/usr/bin/env sage -python
"""Run the 19 aligned square-ratio shifted-derivative cases coefficient-exactly."""

from __future__ import annotations

import glob
import hashlib
import json
import signal
import sqlite3
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SCAN = DATA / "shifted_derivative_square_ratio_scan_all_routes.json"
RESULTS = DATA / "shifted_derivative_19_arithmetic_results.jsonl"
SUMMARY = DATA / "shifted_derivative_19_arithmetic_summary.json"
CONTROL_MANIFEST = ROOT / "outbox" / "agent_f5_28_derivative_24T15337_r20.txt"
CONTROL_Q = (
    "14565989,-2114295053,11853787534,-18764977906,13941011872,"
    "-5699782331,1370959475,-199518023,17549862,-900974,24660,-293,1"
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def line(polynomial) -> str:
    return ",".join(str(value) for value in polynomial.list())


def timeout_handler(signum, frame):
    raise TimeoutError("case exceeded the coefficient-exact arithmetic gate")


def transform(quotient_line: str, a: int) -> dict:
    started = time.monotonic()
    ring_u = PolynomialRing(ZZ, "u")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    u = ring_u.gen()
    x = ring_x.gen()
    q = ring_u([ZZ(value) for value in quotient_line.split(",")])
    if q.degree() != 12 or not q.is_monic() or not q.is_irreducible():
        raise ValueError("bad quotient")
    square_ratio = q(a) * q(0)
    if a and (square_ratio <= 0 or not square_ratio.is_square()):
        raise ValueError("shift does not satisfy the exact square-ratio condition")

    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    radicand = (u - ZZ(a)) * q.derivative()
    radicand_y = sum(
        radicand[index] * y**index for index in range(radicand.degree() + 1)
    )
    h = ring_z(q_y.resultant(z - radicand_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    p = ring_x(h(x**2))
    if (
        h.degree() != 12
        or not h.is_monic()
        or not h.is_irreducible()
        or not p.is_irreducible()
    ):
        raise ValueError("resultant source failed exact irreducibility gates")

    factors = [
        (factor, int(exponent))
        for factor, exponent in h.symmetric_power(2, monic=True).factor()
    ]
    degree_twelve = [
        (index, factor)
        for index, (factor, exponent) in enumerate(factors)
        if factor.degree() == 12 and exponent == 1
    ]
    result = {
        "a": a,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "factorDegrees": [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ],
        "quotient": {
            "coefficientLine": quotient_line,
            "coefficientSha256": sha(quotient_line),
            "discriminantIsSquare": bool(q.discriminant().is_square()),
            "qAt0": str(q(0)),
            "qAtShift": str(q(a)),
            "squareRatioProduct": str(square_ratio),
        },
        "resultant": {
            "coefficientLine": line(h),
            "coefficientSha256": sha(line(h)),
            "rLift": int(p.number_of_real_roots()),
        },
        "status": (
            "unique_degree12_pair_factor"
            if len(degree_twelve) == 1
            else "pair_factor_pattern_miss"
        ),
    }
    if len(degree_twelve) == 1:
        factor_index, factor = degree_twelve[0]
        candidate = ring_x(factor(x**2))
        if not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("pair candidate failed exact gates")
        candidate_line = line(candidate)
        result["candidate"] = {
            "coefficientLine": candidate_line,
            "coefficientSha256": sha(candidate_line),
            "factorIndex": factor_index,
            "r": int(candidate.number_of_real_roots()),
        }
    return result


def load_cases() -> list[dict]:
    routes = []
    for name in sorted(
        glob.glob(
            str(DATA / "agent_f5_full_ledger_pair_product_routes_shard*of4.jsonl")
        )
    ):
        routes.extend(
            json.loads(line)
            for line in Path(name).read_text().splitlines()
            if line.strip()
        )
    aligned = {
        row["source"]["quotientPolynomialSha256"]
        for row in routes
        if int(row["source"]["r"]) == int(row["goldTarget"]["r"])
    }
    hits = json.loads(SCAN.read_text())["hits"]
    cases = []
    for hit in hits:
        digest = hit["source"]["coefficientSha256"]
        if digest not in aligned:
            continue
        exact_routes = [
            row
            for row in routes
            if row["source"]["quotientPolynomialSha256"] == digest
            and int(row["source"]["r"]) == int(row["goldTarget"]["r"])
        ]
        if not exact_routes:
            raise ValueError("aligned case lost its route")
        cases.append(
            {
                "a": int(hit["a"]),
                "quotientLine": hit["source"]["coefficientLine"],
                "routes": exact_routes,
            }
        )
    if len(cases) != 19 or len({row["quotientLine"] for row in cases}) != 16:
        raise ValueError("the reconstructed aligned square-ratio corpus changed")
    return cases


def main() -> int:
    if RESULTS.exists() or SUMMARY.exists():
        raise ValueError("refusing to overwrite shifted-derivative output")
    signal.signal(signal.SIGALRM, timeout_handler)

    control = transform(CONTROL_Q, 0)
    control_line = CONTROL_MANIFEST.read_text().strip()
    control_matches = (
        control.get("candidate", {}).get("coefficientLine") == control_line
    )
    if not control_matches:
        raise ValueError("a=0 derivative control no longer matches the audited manifest")

    connection = sqlite3.connect(f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True)
    current_tc0 = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT t.label,t.r FROM targets AS t
            WHERE t.team_count=0 AND t.discovered=0
              AND NOT EXISTS(
                SELECT 1 FROM baseline_pairs AS b
                WHERE b.label=t.label AND b.r=t.r
              )
              AND NOT EXISTS(
                SELECT 1 FROM verifications AS v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }
    known_hashes = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }

    results = []
    for position, case in enumerate(load_cases(), 1):
        signal.alarm(45)
        try:
            result = transform(case["quotientLine"], case["a"])
        except TimeoutError:
            result = {
                "a": case["a"],
                "quotient": {
                    "coefficientLine": case["quotientLine"],
                    "coefficientSha256": sha(case["quotientLine"]),
                },
                "status": "timeout_45s",
            }
        finally:
            signal.alarm(0)
        result["position"] = position
        result["savedAlignedRoutes"] = case["routes"]
        if "candidate" in result:
            candidate = result["candidate"]
            predicted = sorted(
                {
                    (str(route["action"]["targetLabel"]), int(candidate["r"]))
                    for route in case["routes"]
                }
            )
            candidate["knownCoefficient"] = (
                candidate["coefficientSha256"] in known_hashes
            )
            candidate["sameActionPredictedPairs"] = [
                {
                    "currentTc0": pair in current_tc0,
                    "label": pair[0],
                    "r": pair[1],
                }
                for pair in predicted
            ]
        results.append(result)
        RESULTS.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in results)
        )
        print(
            json.dumps(
                {
                    "a": case["a"],
                    "candidateR": result.get("candidate", {}).get("r"),
                    "event": "case",
                    "position": position,
                    "status": result["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    same_action_live = [
        row
        for row in results
        if any(
            pair["currentTc0"]
            for pair in row.get("candidate", {}).get(
                "sameActionPredictedPairs", []
            )
        )
    ]
    summary = {
        "caseCount": len(results),
        "control": {
            "a": 0,
            "candidateSha256": control["candidate"]["coefficientSha256"],
            "deduplicatedExistingManifest": str(CONTROL_MANIFEST.relative_to(ROOT)),
            "matchesAuditedManifest": control_matches,
        },
        "sameActionCurrentTc0Count": len(same_action_live),
        "sameActionCurrentTc0Positions": [row["position"] for row in same_action_live],
        "statusCounts": {
            status: sum(row["status"] == status for row in results)
            for status in sorted({row["status"] for row in results})
        },
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"event": "complete", **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

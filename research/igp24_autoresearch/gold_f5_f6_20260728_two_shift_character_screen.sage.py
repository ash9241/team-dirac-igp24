#!/usr/bin/env sage -python
"""Classify and screen the two-shift derivative-character hits."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
INPUT = DATA / "gold_f5_f6_20260728_two_shift_character_scan.json"
RESULTS = DATA / "gold_f5_f6_20260728_two_shift_character_screen.jsonl"
SUMMARY = DATA / "gold_f5_f6_20260728_two_shift_character_screen_summary.json"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_two_shift_character_screen.txt"
RESERVED = {
    ("24T10482", 8),
    ("24T11787", 12),
    ("24T14293", 16),
    ("24T15043", 4),
    ("24T15273", 20),
    ("24T15337", 20),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T24877", 14),
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE = load_module(
    "gold_single_derivative_screen",
    ROOT / "gold_f5_f6_20260728_all_character_derivative_screen.sage.py",
)


def arithmetic_transform(quotient_line: str, a: int, b: int) -> dict:
    started = time.monotonic()
    ring_u = PolynomialRing(ZZ, "u")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    u = ring_u.gen()
    x = ring_x.gen()
    q = ring_u([ZZ(value) for value in quotient_line.split(",")])
    if q.degree() != 12 or not q.is_monic() or not q.is_irreducible():
        raise ValueError("bad quotient")

    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    radicand = (u - ZZ(a)) * (u - ZZ(b)) * q.derivative()
    radicand %= q
    radicand_y = sum(
        radicand[index] * y**index
        for index in range(radicand.degree() + 1)
    )
    h = ring_z(q_y.resultant(z - radicand_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    source_polynomial = ring_x(h(x**2))
    if (
        h.degree() != 12
        or not h.is_monic()
        or not h.is_irreducible()
        or not source_polynomial.is_irreducible()
    ):
        return {
            "status": "source_irreducibility_miss",
            "elapsedSeconds": round(time.monotonic() - started, 3),
        }
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
        "b": b,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "factorDegrees": [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ],
        "sourcePolynomialLine": SINGLE.coefficient_line(source_polynomial),
        "sourcePolynomialR": int(source_polynomial.number_of_real_roots()),
        "sourcePolynomialSha256": SINGLE.sha_text(
            SINGLE.coefficient_line(source_polynomial)
        ),
        "transformedQuotientLine": SINGLE.coefficient_line(h),
        "transformedQuotientSha256": SINGLE.sha_text(
            SINGLE.coefficient_line(h)
        ),
        "transformedNorm": str(h[0]),
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
        line = SINGLE.coefficient_line(candidate)
        result["candidate"] = {
            "coefficientLine": line,
            "coefficientSha256": SINGLE.sha_text(line),
            "factorIndex": factor_index,
            "r": int(candidate.number_of_real_roots()),
        }
    return result


def main() -> int:
    for path in (RESULTS, SUMMARY, MANIFEST):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
    document = json.loads(INPUT.read_text(encoding="utf-8"))
    grouped = {}
    for hit in document["hits"]:
        source = hit["source"]
        key = (
            str(source["coefficientSha256"]),
            int(hit["a"]),
            int(hit["b"]),
        )
        saved = grouped.setdefault(
            key,
            {
                "a": int(hit["a"]),
                "b": int(hit["b"]),
                "cores": set(),
                "kinds": set(),
                "source": source,
            },
        )
        saved["cores"].add(int(hit["derivativeNormCoreRepresentative"]))
        saved["kinds"].add(str(hit["characterKind"]))

    connection = sqlite3.connect(
        f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True
    )
    try:
        tc0 = {
            (str(label), int(r))
            for label, r in connection.execute(
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
            str(value)
            for (value,) in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
    finally:
        connection.close()
    actions = SINGLE.load_actions()

    by_quotient = defaultdict(list)
    for key, case in grouped.items():
        by_quotient[key[0]].append((key, case))
    classification = {}
    ring = PolynomialRing(ZZ, "u")
    for quotient_hash, cases in sorted(by_quotient.items()):
        source = cases[0][1]["source"]
        quotient = ring(
            [ZZ(value) for value in source["coefficientLine"].split(",")]
        )
        cores = sorted(
            {
                core
                for _key, case in cases
                for core in case["cores"]
            }
        )
        alignment = SINGLE.CHARACTER.character_alignment(
            quotient, int(source["quotientT12"]), probe_cores=cores
        )
        classification[quotient_hash] = alignment

    results = []
    potential_pairs = set()
    started = time.monotonic()
    for ordinal, (key, case) in enumerate(sorted(grouped.items()), 1):
        source = case["source"]
        alignment = classification[key[0]]
        possible_labels = sorted(
            {
                label
                for core in case["cores"]
                for label in alignment["coreToPossibleLabels"].get(
                    str(core), []
                )
            }
        )
        exact_source_label = (
            possible_labels[0] if len(possible_labels) == 1 else None
        )
        arithmetic = arithmetic_transform(
            str(source["coefficientLine"]), int(case["a"]), int(case["b"])
        )
        predictions = []
        if exact_source_label is not None and "candidate" in arithmetic:
            candidate = arithmetic["candidate"]
            candidate_hash = str(candidate["coefficientSha256"])
            candidate_r = int(candidate["r"])
            for action in actions.get(exact_source_label, []):
                pair = (str(action["targetLabel"]), candidate_r)
                live = (
                    pair in tc0
                    and pair not in RESERVED
                    and candidate_hash not in known_hashes
                )
                predictions.append(
                    {
                        "action": action,
                        "currentTc0Unreserved": live,
                        "label": pair[0],
                        "r": pair[1],
                    }
                )
                if live:
                    potential_pairs.add(pair)
        row = {
            "ordinal": ordinal,
            "a": int(case["a"]),
            "b": int(case["b"]),
            "characterKinds": sorted(case["kinds"]),
            "coreRepresentatives": sorted(case["cores"]),
            "quotientSha256": key[0],
            "quotientT12": int(source["quotientT12"]),
            "possibleSourceLabels": possible_labels,
            "exactRankElevenSourceLabel": exact_source_label,
            "arithmetic": arithmetic,
            "predictions": predictions,
            "potentialHit": any(
                prediction["currentTc0Unreserved"]
                for prediction in predictions
            ),
            "source": source,
        }
        results.append(row)
        RESULTS.write_text(
            "".join(
                json.dumps(value, separators=(",", ":"), sort_keys=True)
                + "\n"
                for value in results
            ),
            encoding="utf-8",
        )
        if row["potentialHit"] or ordinal % 25 == 0:
            print(
                json.dumps(
                    {
                        "event": "case",
                        "ordinal": ordinal,
                        "total": len(grouped),
                        "exactSourceLabel": exact_source_label,
                        "candidateR": arithmetic.get("candidate", {}).get("r"),
                        "potentialHit": row["potentialHit"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    hit_rows = [row for row in results if row["potentialHit"]]
    lines = []
    seen_hashes = set()
    for row in hit_rows:
        candidate = row["arithmetic"]["candidate"]
        digest = str(candidate["coefficientSha256"])
        if digest not in seen_hashes:
            seen_hashes.add(digest)
            lines.append(str(candidate["coefficientLine"]))
    MANIFEST.write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8"
    )
    summary = {
        "schemaVersion": "gold-f5-f6-two-shift-character-screen-v1",
        "input": str(INPUT.relative_to(ROOT)),
        "uniqueCaseCount": len(grouped),
        "processedCaseCount": len(results),
        "exactSourceClassificationCount": sum(
            row["exactRankElevenSourceLabel"] is not None for row in results
        ),
        "statusCounts": dict(
            sorted(Counter(row["arithmetic"]["status"] for row in results).items())
        ),
        "potentialHitCount": len(hit_rows),
        "potentialPairs": [
            {"label": label, "r": r} for label, r in sorted(potential_pairs)
        ],
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": hashlib.sha256(RESULTS.read_bytes()).hexdigest(),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    SUMMARY.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

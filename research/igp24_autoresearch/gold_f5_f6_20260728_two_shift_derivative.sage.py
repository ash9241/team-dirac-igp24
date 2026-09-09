#!/usr/bin/env sage -python
"""Two-shift derivative pilot using completed character-alignment checkpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
INPUT = DATA / "gold_f5_f6_20260728_character_switch_derivative_v2.jsonl"
RESULTS = DATA / "gold_f5_f6_20260728_two_shift_derivative.jsonl"
SUMMARY = DATA / "gold_f5_f6_20260728_two_shift_derivative_summary.json"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_two_shift_derivative.txt"
BOUND = 30
RESERVED_PAIRS = {
    ("24T10482", 8),
    ("24T14293", 16),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T15337", 20),
    ("24T15043", 4),
    ("24T11787", 12),
    ("24T24877", 14),
    ("24T15273", 20),
}


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def line(polynomial) -> str:
    return ",".join(str(value) for value in polynomial.list())


def transform_two(quotient_line: str, first: int, second: int) -> dict:
    ring_u = PolynomialRing(ZZ, "u")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    u = ring_u.gen()
    x = ring_x.gen()
    q = ring_u([ZZ(value) for value in quotient_line.split(",")])
    radicand = (u - ZZ(first)) * (u - ZZ(second)) * q.derivative()
    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    radicand_y = sum(
        radicand[index] * y**index for index in range(radicand.degree() + 1)
    )
    h = ring_z(q_y.resultant(z - radicand_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    source = ring_x(h(x**2))
    if (
        h.degree() != 12
        or not h.is_monic()
        or not h.is_irreducible()
        or not source.is_irreducible()
    ):
        raise ValueError("transformed source is not irreducible")
    factors = [
        (factor, int(exponent))
        for factor, exponent in h.symmetric_power(2, monic=True).factor()
    ]
    selected = [
        (index, factor)
        for index, (factor, exponent) in enumerate(factors)
        if factor.degree() == 12 and exponent == 1
    ]
    result = {
        "firstShift": first,
        "secondShift": second,
        "resultantLine": line(h),
        "resultantSha256": sha(line(h)),
        "sourceR": int(source.number_of_real_roots()),
        "factorDegrees": [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ],
        "status": (
            "unique_degree12_pair_factor"
            if len(selected) == 1
            else "pair_factor_pattern_miss"
        ),
    }
    if len(selected) == 1:
        index, factor = selected[0]
        candidate = ring_x(factor(x**2))
        if candidate.is_monic() and candidate.is_irreducible():
            candidate_line = line(candidate)
            result["candidate"] = {
                "coefficientLine": candidate_line,
                "coefficientSha256": sha(candidate_line),
                "factorIndex": index,
                "r": int(candidate.number_of_real_roots()),
            }
    return result


def main() -> int:
    for path in (RESULTS, SUMMARY, MANIFEST):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
    checkpoints = [
        json.loads(raw)
        for raw in INPUT.read_text(encoding="utf-8").splitlines()
        if raw.strip() and json.loads(raw).get("alignmentStatus") == "exact"
    ]
    connection = sqlite3.connect(
        f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True
    )
    try:
        current_tc0 = {
            (str(label), int(r))
            for label, r in connection.execute(
                """
                SELECT t.label,t.r FROM targets t
                WHERE t.team_count=0 AND t.discovered=0
                  AND NOT EXISTS(
                    SELECT 1 FROM baseline_pairs b
                    WHERE b.label=t.label AND b.r=t.r
                  )
                  AND NOT EXISTS(
                    SELECT 1 FROM verifications v
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

    actions_by_qt = defaultdict(list)
    for path in sorted(
        DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
    ):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            action = json.loads(raw)
            if int(action["sourceBlockKernelOrder"]) != 2**11:
                continue
            if not any(
                label == str(action["targetLabel"]) for label, _r in current_tc0
            ):
                continue
            actions_by_qt[int(action["quotientT12"])].append(action)

    ring = PolynomialRing(ZZ, "u")
    results = []
    potential_hits = []
    started = time.monotonic()
    integers = list(range(-BOUND, BOUND + 1))
    for ordinal, checkpoint in enumerate(checkpoints, 1):
        q = ring(
            [ZZ(value) for value in checkpoint["quotientLine"].split(",")]
        )
        discriminant = ZZ(q.discriminant())
        values = {a: ZZ(q(a)) for a in integers}
        alignment = checkpoint["characterAlignment"]
        desired = []
        for action in actions_by_qt[int(checkpoint["quotientT12"])]:
            for core in alignment["labelToSquarefreeNormCores"].get(
                str(action["sourceLabel"]), []
            ):
                desired.append((action, ZZ(core)))
        arithmetic_hits = []
        seen = set()
        for action, core in desired:
            for first_index, first in enumerate(integers):
                if values[first] == 0:
                    continue
                for second in integers[first_index + 1 :]:
                    if values[second] == 0:
                        continue
                    ratio = (
                        QQ(values[first] * values[second] * discriminant)
                        / QQ(core)
                    )
                    if ratio <= 0 or not ratio.is_square():
                        continue
                    key = (
                        first,
                        second,
                        str(action["sourceLabel"]),
                        int(core),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        arithmetic = transform_two(
                            checkpoint["quotientLine"], first, second
                        )
                    except ValueError as error:
                        arithmetic_hits.append(
                            {
                                "firstShift": first,
                                "secondShift": second,
                                "sourceLabel": str(action["sourceLabel"]),
                                "core": int(core),
                                "status": "transform_rejected",
                                "reason": str(error),
                            }
                        )
                        continue
                    prediction = None
                    if "candidate" in arithmetic:
                        candidate = arithmetic["candidate"]
                        pair = (
                            str(action["targetLabel"]),
                            int(candidate["r"]),
                        )
                        potential = (
                            pair in current_tc0
                            and pair not in RESERVED_PAIRS
                            and str(candidate["coefficientSha256"])
                            not in known_hashes
                        )
                        prediction = {
                            "label": pair[0],
                            "r": pair[1],
                            "currentTc0Unreserved": potential,
                        }
                        if potential:
                            potential_hits.append(
                                {
                                    "action": action,
                                    "arithmetic": arithmetic,
                                    "characterCore": int(core),
                                    "quotient": {
                                        "coefficientLine": checkpoint[
                                            "quotientLine"
                                        ],
                                        "coefficientSha256": checkpoint[
                                            "quotientSha256"
                                        ],
                                        "quotientT12": checkpoint[
                                            "quotientT12"
                                        ],
                                    },
                                    "target": {
                                        "label": pair[0],
                                        "r": pair[1],
                                    },
                                }
                            )
                    arithmetic_hits.append(
                        {
                            "action": action,
                            "arithmetic": arithmetic,
                            "core": int(core),
                            "prediction": prediction,
                            "status": arithmetic["status"],
                        }
                    )
        result = {
            "ordinal": ordinal,
            "inputOrdinal": checkpoint["ordinal"],
            "quotientSha256": checkpoint["quotientSha256"],
            "quotientT12": checkpoint["quotientT12"],
            "desiredCharacterCount": len(desired),
            "arithmeticHits": arithmetic_hits,
        }
        results.append(result)
        RESULTS.write_text(
            "".join(canonical_json(value) + "\n" for value in results),
            encoding="utf-8",
        )
        print(
            canonical_json(
                {
                    "event": "quotient",
                    "ordinal": ordinal,
                    "total": len(checkpoints),
                    "arithmeticHits": len(arithmetic_hits),
                    "potentialHits": len(potential_hits),
                }
            ),
            flush=True,
        )

    distinct = {}
    for hit in potential_hits:
        key = (str(hit["target"]["label"]), int(hit["target"]["r"]))
        distinct.setdefault(key, hit)
    manifest_lines = []
    for hit in distinct.values():
        manifest_lines.append(hit["arithmetic"]["candidate"]["coefficientLine"])
    MANIFEST.write_text(
        "".join(value + "\n" for value in manifest_lines), encoding="utf-8"
    )
    summary = {
        "schemaVersion": "gold-f5-f6-20260728-two-shift-derivative-v1",
        "bound": BOUND,
        "inputCheckpointCount": len(checkpoints),
        "completed": len(results),
        "arithmeticHitCount": sum(len(row["arithmeticHits"]) for row in results),
        "potentialPairCount": len(distinct),
        "potentialPairs": [
            {"label": label, "r": r} for label, r in sorted(distinct)
        ],
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha_file(RESULTS),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha_file(MANIFEST),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    SUMMARY.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(canonical_json(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

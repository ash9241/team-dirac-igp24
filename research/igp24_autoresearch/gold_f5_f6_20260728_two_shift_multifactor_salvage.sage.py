#!/usr/bin/env sage -python
"""Salvage separating multifactor pair resolvents from the two-shift screen."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, libgap, primes_first_n


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
INPUT = DATA / "gold_f5_f6_20260728_two_shift_character_screen.jsonl"
OUTPUT = DATA / "gold_f5_f6_20260728_two_shift_multifactor_salvage.json"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_two_shift_multifactor_salvage.txt"
SOURCE_LABELS = {"24T19339", "24T20748"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCREEN = load_module(
    "gold_two_shift_screen",
    ROOT / "gold_f5_f6_20260728_two_shift_character_screen.sage.py",
)


def cycle_profiles(label: str) -> set[tuple[int, ...]]:
    group = libgap.TransitiveGroup(24, int(label[3:]))
    points = libgap.eval("[1..24]")
    return {
        tuple(
            sorted(
                int(value)
                for value in libgap.CycleLengths(
                    libgap.Representative(conjugacy_class), points
                )
            )
        )
        for conjugacy_class in libgap.ConjugacyClasses(group)
    }


def cycle_type(polynomial, prime: int):
    reduced = polynomial.change_ring(GF(prime))
    if not reduced.is_squarefree():
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def factors_for(row: dict):
    ring_u = PolynomialRing(ZZ, "u")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    u = ring_u.gen()
    x = ring_x.gen()
    q = ring_u(
        [ZZ(value) for value in row["source"]["coefficientLine"].split(",")]
    )
    a = ZZ(row["a"])
    b = ZZ(row["b"])
    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    radicand = ((u - a) * (u - b) * q.derivative()) % q
    radicand_y = sum(
        radicand[index] * y**index
        for index in range(radicand.degree() + 1)
    )
    h = ring_z(q_y.resultant(z - radicand_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    factorization = [
        (factor, int(exponent))
        for factor, exponent in h.symmetric_power(2, monic=True).factor()
    ]
    result = []
    for factor_index, (factor, exponent) in enumerate(factorization):
        if factor.degree() != 12 or exponent != 1:
            continue
        candidate = ring_x(factor(x**2))
        if not candidate.is_monic() or not candidate.is_irreducible():
            continue
        line = SCREEN.SINGLE.coefficient_line(candidate)
        result.append(
            {
                "candidate": candidate,
                "coefficientLine": line,
                "coefficientSha256": hashlib.sha256(line.encode()).hexdigest(),
                "factorIndex": factor_index,
                "r": int(candidate.number_of_real_roots()),
            }
        )
    return factorization, result


def main() -> int:
    for path in (OUTPUT, MANIFEST):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
    rows = [
        json.loads(raw)
        for raw in INPUT.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]
    rows = [
        row
        for row in rows
        if row.get("exactRankElevenSourceLabel") in SOURCE_LABELS
        and sum(
            item["degree"] == 12 and item["exponent"] == 1
            for item in row["arithmetic"]["factorDegrees"]
        )
        > 1
    ]
    actions = SCREEN.SINGLE.load_actions()
    target_labels = sorted(
        {
            str(action["targetLabel"])
            for source_label in SOURCE_LABELS
            for action in actions[source_label]
        }
    )
    profiles = {label: cycle_profiles(label) for label in target_labels}

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

    output_rows = []
    stage_lines = []
    staged_hashes = set()
    for row in rows:
        source_label = str(row["exactRankElevenSourceLabel"])
        possible_action_labels = sorted(
            {
                str(action["targetLabel"])
                for action in actions[source_label]
            }
        )
        factorization, candidates = factors_for(row)
        for candidate in candidates:
            possible = set(possible_action_labels)
            observations = []
            for prime in primes_first_n(256):
                prime = int(prime)
                profile = cycle_type(candidate["candidate"], prime)
                if profile is None:
                    continue
                observations.append(
                    {"prime": prime, "cycleType": list(profile)}
                )
                possible = {
                    label
                    for label in possible
                    if profile in profiles[label]
                }
                if len(possible) <= 1:
                    break
            exact_label = next(iter(possible)) if len(possible) == 1 else None
            pair = (
                (exact_label, int(candidate["r"]))
                if exact_label is not None
                else None
            )
            live = bool(
                pair is not None
                and pair in tc0
                and candidate["coefficientSha256"] not in known_hashes
            )
            saved = {
                key: value
                for key, value in candidate.items()
                if key != "candidate"
            }
            saved.update(
                {
                    "a": int(row["a"]),
                    "b": int(row["b"]),
                    "exactSourceLabelConditional": source_label,
                    "possibleActionLabels": sorted(possible),
                    "exactTargetLabelConditional": exact_label,
                    "profileObservations": observations,
                    "currentTc0Unreserved": live,
                    "sourceCoefficientSha256": row["quotientSha256"],
                }
            )
            output_rows.append(saved)
            if live and saved["coefficientSha256"] not in staged_hashes:
                stage_lines.append(saved["coefficientLine"])
                staged_hashes.add(saved["coefficientSha256"])

    MANIFEST.write_text(
        "".join(line + "\n" for line in stage_lines), encoding="utf-8"
    )
    payload = {
        "schemaVersion": "gold-f5-f6-two-shift-multifactor-salvage-v1",
        "inputCases": len(rows),
        "candidateFactors": len(output_rows),
        "conditionalHitCount": sum(
            row["currentTc0Unreserved"] for row in output_rows
        ),
        "conditionalPairs": sorted(
            {
                f"{row['exactTargetLabelConditional']}/r{row['r']}"
                for row in output_rows
                if row["currentTc0Unreserved"]
            }
        ),
        "rows": output_rows,
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "networkCalls": 0,
        "submissionCalls": 0,
        "warning": (
            "A conditional hit still requires a maximal-subgroup certificate "
            "for the reconstructed rank-11 source before staging as exact."
        ),
    }
    OUTPUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidateFactors": len(output_rows),
                "conditionalHitCount": payload["conditionalHitCount"],
                "conditionalPairs": payload["conditionalPairs"],
                "inputCases": len(rows),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

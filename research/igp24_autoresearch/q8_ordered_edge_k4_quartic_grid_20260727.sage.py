#!/usr/bin/env sage
"""Resumable ordered-edge S4/K4-star radical grid.

For an S4 quartic

    f(t) = t^4 + p*t^2 + q*t + s^2

and distinct roots alpha_i, alpha_j, the twelve radicands are

    a_ij = d*alpha_i*(c^2 - (alpha_i-alpha_j)^2).

The product of the six radicands incident with any fixed vertex is

    d^6 * alpha_i^2 * prod_k(alpha_k)
        * prod_{j != i}(c^2-(alpha_i-alpha_j)^2)^2,

which is a square because prod_k(alpha_k)=s^2.  Thus the Kummer kernel
has rank at most nine.  The quotient on ordered pairs is exactly 12T8.

Every irreducible candidate is classified against the exhaustive catalog
in q8_ordered_edge_k4_compatible_catalog_20260727.json by exact
unramified Frobenius cycle types.  The JSONL is append-only and resumable.
There is no network or submission path in this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, QQ, ZZ, prime_range


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
CATALOG = (
    ROOT / "data" / "q8_ordered_edge_k4_compatible_catalog_20260727.json"
)
RESULTS = ROOT / "data" / "q8_ordered_edge_k4_quartic_grid_20260727.jsonl"
SUMMARY = ROOT / "data" / "q8_ordered_edge_k4_quartic_grid_20260727_summary.json"
OUTBOX = ROOT / "outbox" / "q8_ordered_edge_k4_exact_gold_20260727.txt"
TARGETS = (10379, 10385)
TARGET_REAL_ROOTS = {
    10379: {12, 20},
    10385: {0, 4, 8, 12, 16, 20, 24},
}
DEFAULT_D = (-15, -14, -10, -7, -6, -5, -3, -2, -1, 1, 2, 3, 5, 6, 7, 10, 14, 15)


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def canonical_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def cycle_type(polynomial, prime: int) -> tuple[int, ...] | None:
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


def live_gate(label: str, real_roots: int, digest: str) -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    target = connection.execute(
        """
        SELECT team_count,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        (label, real_roots),
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (label, real_roots),
    ).fetchone()
    owned = connection.execute(
        """
        SELECT 1 FROM verifications
        WHERE label=? AND r=? AND scoreable=1
        """,
        (label, real_roots),
    ).fetchone()
    known = connection.execute(
        "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)
    ).fetchone()
    connection.close()
    return {
        "teamCount": None if target is None else int(target[0]),
        "discovered": None if target is None else int(target[1]),
        "generatedAt": None if target is None else target[2],
        "baseline": baseline is not None,
        "owned": owned is not None,
        "knownHash": known is not None,
        "currentTc0": bool(
            target is not None
            and int(target[0]) == 0
            and int(target[1]) == 0
            and baseline is None
            and owned is None
        ),
    }


def load_catalog() -> tuple[dict, dict]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    if (
        not payload.get("exhaustiveDegree24")
        or payload.get("quotientLabel") != "12T8"
        or int(payload.get("maximumKernelOrder", 0)) != 2**9
        or not payload.get("includesSplit24T10377")
    ):
        raise ValueError("ordered-edge compatible catalog failed integrity gates")
    metadata = {int(row["t"]): row for row in payload["groups"]}
    profiles = {
        target_t: {
            tuple(int(value) for value in profile)
            for profile in row["cycleProfiles"]
        }
        for target_t, row in metadata.items()
    }
    if not set(TARGETS) <= set(metadata):
        raise ValueError("requested targets absent from compatible catalog")
    return metadata, profiles


def load_completed() -> tuple[list[dict], set[tuple[int, int, int, int, int]]]:
    rows = []
    completed = set()
    if not RESULTS.exists():
        return rows, completed
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # A killed append can leave at most one incomplete final line.
            continue
        rows.append(row)
        completed.add(
            (
                int(row["p"]),
                int(row["q"]),
                int(row["s"]),
                int(row["c"]),
                int(row["d"]),
            )
        )
    return rows, completed


def append_row(row: dict) -> None:
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
        handle.write("\n")
        handle.flush()


def base_radicand_polynomial(p: int, q: int, s: int, c: int):
    """Return prod_{i != j}(z-alpha_i*(c^2-(alpha_i-alpha_j)^2))."""

    z_ring = PolynomialRing(QQ, "z")
    z = z_ring.gen()
    u_ring = PolynomialRing(z_ring, "u")
    u = u_ring.gen()
    v_ring = PolynomialRing(u_ring, "v")
    v = v_ring.gen()
    u_v = v_ring(u)
    z_v = v_ring(u_ring(z))

    quartic_u = u**4 + p * u**2 + q * u + s**2
    divided_difference = (
        v**3
        + v**2 * u_v
        + v * u_v**2
        + u_v**3
        + p * (v + u_v)
        + q
    )
    radicand = u_v * (c**2 - (u_v - v) ** 2)
    inner = u_ring(divided_difference.resultant(z_v - radicand))
    answer = z_ring(quartic_u.resultant(inner))
    if answer.degree() != 12 or not answer.is_monic():
        raise ArithmeticError("ordered-edge resultant lost degree or monicity")
    if any(QQ(value).denominator() != 1 for value in answer):
        raise ArithmeticError("ordered-edge resultant is unexpectedly nonintegral")
    return answer.change_ring(ZZ)


def scalar_twist(base, d: int):
    ring = base.parent()
    z = ring.gen()
    degree = int(base.degree())
    return ring(
        sum(
            base[index] * ZZ(d) ** (degree - index) * z**index
            for index in range(degree + 1)
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--p-min", type=int, default=-8)
    parser.add_argument("--p-max", type=int, default=8)
    parser.add_argument("--q-max", type=int, default=8)
    parser.add_argument("--s-max", type=int, default=3)
    parser.add_argument("--c-max", type=int, default=6)
    parser.add_argument("--prime-limit", type=int, default=3000)
    parser.add_argument("--max-quartics", type=int)
    parser.add_argument(
        "--d-values",
        default=",".join(str(value) for value in DEFAULT_D),
    )
    args = parser.parse_args()
    if (
        args.p_min > args.p_max
        or args.q_max < 1
        or args.s_max < 1
        or args.c_max < 0
        or args.prime_limit < 2
    ):
        parser.error("invalid nonempty parameter range")
    d_values = tuple(
        dict.fromkeys(
            int(value)
            for value in args.d_values.split(",")
            if value.strip() and int(value) != 0
        )
    )
    if not d_values:
        parser.error("provide at least one nonzero d")

    metadata, profiles = load_catalog()
    all_profiles = set(profiles)
    prime_values = [int(value) for value in prime_range(2, args.prime_limit + 1)]
    existing, completed = load_completed()
    polynomial_ring = PolynomialRing(ZZ, "x")
    x = polynomial_ring.gen()
    quartic_ring = PolynomialRing(QQ, "t")
    t = quartic_ring.gen()

    valid_quartics = 0
    new_rows = 0
    new_survivors = []
    stop = False
    for height in range(
        1, max(abs(args.p_min), abs(args.p_max), args.q_max, args.s_max) + 1
    ):
        p_values = [
            p
            for p in range(args.p_min, args.p_max + 1)
            if abs(p) <= height
        ]
        for s in range(1, min(args.s_max, height) + 1):
            for q in range(1, min(args.q_max, height) + 1):
                for p in p_values:
                    if max(abs(p), q, s) != height:
                        continue
                    quartic = t**4 + p * t**2 + q * t + s**2
                    if not quartic.is_irreducible():
                        continue
                    try:
                        quartic_label = int(
                            quartic.galois_group().transitive_number()
                        )
                    except Exception:
                        continue
                    if quartic_label != 5:
                        continue
                    valid_quartics += 1
                    if (
                        args.max_quartics is not None
                        and valid_quartics > args.max_quartics
                    ):
                        stop = True
                        break

                    for c in range(args.c_max + 1):
                        base = base_radicand_polynomial(p, q, s, c)
                        for d in d_values:
                            key = (p, q, s, c, d)
                            if key in completed:
                                continue
                            twisted = scalar_twist(base, d)
                            candidate = polynomial_ring(twisted(x**2))
                            line = canonical_line(candidate)
                            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                            real_roots = int(candidate.number_of_real_roots())
                            archimedean = tuple(
                                sorted(
                                    [1] * real_roots
                                    + [2] * ((24 - real_roots) // 2)
                                )
                            )
                            possible = {
                                target_t
                                for target_t in all_profiles
                                if archimedean in profiles[target_t]
                            }
                            target_possible = {
                                target_t
                                for target_t in TARGETS
                                if real_roots in TARGET_REAL_ROOTS[target_t]
                                and target_t in possible
                            }
                            observations = []
                            eliminations = []
                            for prime in prime_values:
                                if not target_possible:
                                    break
                                profile = cycle_type(candidate, prime)
                                if profile is None:
                                    continue
                                before = set(possible)
                                possible = {
                                    target_t
                                    for target_t in possible
                                    if profile in profiles[target_t]
                                }
                                target_possible &= possible
                                observations.append((prime, profile))
                                removed = before - possible
                                if removed:
                                    eliminations.append(
                                        {
                                            "prime": prime,
                                            "cycleType": list(profile),
                                            "removed": sorted(removed),
                                        }
                                    )
                                if len(possible) == 1:
                                    break

                            irreducible = (
                                bool(candidate.is_irreducible())
                                if target_possible
                                else None
                            )
                            unique = (
                                next(iter(possible))
                                if irreducible and len(possible) == 1
                                else None
                            )
                            gate = (
                                live_gate(f"24T{unique}", real_roots, digest)
                                if unique in TARGETS
                                else None
                            )
                            exact_gold = bool(
                                unique in TARGETS
                                and gate
                                and gate["currentTc0"]
                                and not gate["knownHash"]
                            )
                            row = {
                                "schemaVersion": "q8-ordered-edge-k4-grid-v1",
                                "p": p,
                                "q": q,
                                "s": s,
                                "c": c,
                                "d": d,
                                "quarticPolynomial": str(quartic),
                                "quarticLabel": "4T5",
                                "quarticDiscriminant": int(quartic.discriminant()),
                                "starSquareIdentity": (
                                    "prod_incident(a_ij)="
                                    "(d^3*s*alpha_i*"
                                    "prod_{j!=i}(c^2-(alpha_i-alpha_j)^2))^2"
                                ),
                                "quotientLabel": "12T8",
                                "catalogSize": len(metadata),
                                "candidateSha256": digest,
                                "candidateBytes": len(line.encode("ascii")),
                                "degree": int(candidate.degree()),
                                "monic": bool(candidate.is_monic()),
                                "primitive": (
                                    math.gcd(
                                        *[abs(int(value)) for value in candidate]
                                    )
                                    == 1
                                ),
                                "realRoots": real_roots,
                                "checkedGoodPrimes": len(observations),
                                "catalogSurvivors": sorted(possible),
                                "targetSurvivors": sorted(target_possible),
                                "irreducible": irreducible,
                                "exactLabel": (
                                    None if unique is None else f"24T{unique}"
                                ),
                                "liveGate": gate,
                                "candidateCoefficientLine": (
                                    line if target_possible else None
                                ),
                                "eliminations": (
                                    eliminations if target_possible else []
                                ),
                                "status": (
                                    "unique_exact_catalog_target_tc0"
                                    if exact_gold
                                    else (
                                        "target_catalog_survivor"
                                        if target_possible and irreducible
                                        else "excluded"
                                    )
                                ),
                                "networkCalls": 0,
                                "submissionCalls": 0,
                            }
                            append_row(row)
                            existing.append(row)
                            completed.add(key)
                            new_rows += 1
                            if row["status"] != "excluded":
                                new_survivors.append(row)
                                print(
                                    json.dumps(
                                        {
                                            "parameters": list(key),
                                            "hash": digest[:16],
                                            "r": real_roots,
                                            "survivors": sorted(possible),
                                            "status": row["status"],
                                        },
                                        sort_keys=True,
                                    ),
                                    flush=True,
                                )
                if stop:
                    break
            if stop:
                break
        if stop:
            break

    exact_rows_by_pair = {}
    for row in existing:
        if row.get("status") != "unique_exact_catalog_target_tc0":
            continue
        pair = (str(row["exactLabel"]), int(row["realRoots"]))
        exact_rows_by_pair.setdefault(pair, row)
    exact_rows = [
        exact_rows_by_pair[pair] for pair in sorted(exact_rows_by_pair)
    ]
    if exact_rows:
        atomic_text(
            OUTBOX,
            "\n".join(row["candidateCoefficientLine"] for row in exact_rows)
            + "\n",
        )

    summary = {
        "schemaVersion": "q8-ordered-edge-k4-grid-summary-v1",
        "catalogSize": len(metadata),
        "validQuarticsReached": valid_quartics,
        "rows": len(existing),
        "newRows": new_rows,
        "newTargetSurvivors": len(new_survivors),
        "exactTc0Pairs": [
            {
                "label": row["exactLabel"],
                "r": row["realRoots"],
                "sha256": row["candidateSha256"],
                "parameters": [
                    row["p"],
                    row["q"],
                    row["s"],
                    row["c"],
                    row["d"],
                ],
            }
            for row in exact_rows
        ],
        "results": str(RESULTS.relative_to(ROOT)),
        "outbox": (
            str(OUTBOX.relative_to(ROOT)) if exact_rows else None
        ),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

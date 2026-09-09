#!/usr/bin/env sage -python
"""Extract one exact degree-24 unordered-pair sibling field.

The source polynomial is read from the local submission ledger.  For a small
Tschirnhaus transform h(x) = x + c*x^2, we construct the pair-sum resolvent

    product_{i < j} (y - h(alpha_i) - h(alpha_j))

from Newton sums.  A result is accepted only when the irreducible factor-degree
multiset agrees exactly with the independently computed GAP orbit census.  For
a source action with one length-24 orbit, the unique degree-24 factor therefore
has the target action recorded in pair_orbit_map.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
ORBIT_MAP_PATH = ROOT / "data" / "pair_orbit_map.jsonl"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_source(submission_id: str, polynomial_index: int) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT p.coefficients, p.coefficient_hash,
                   v.label, v.t, v.r, v.status, v.scoreable
            FROM polynomials AS p
            JOIN verifications AS v
              USING (submission_id, polynomial_index)
            WHERE p.submission_id = ? AND p.polynomial_index = ?
            """,
            (submission_id, polynomial_index),
        ).fetchone()
    if row is None:
        raise ValueError("source polynomial is not present in the local verified ledger")
    return dict(row)


def load_orbit_row(source_label: str, orbit_map_path: Path = ORBIT_MAP_PATH) -> dict:
    for line in orbit_map_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["sourceLabel"] == source_label:
            return row
    raise ValueError(f"no exact pair-orbit map for {source_label}")


def root_power_sums(coefficients: list[int], maximum: int) -> list[int]:
    """Newton sums p[k] = sum_i alpha_i^k for a monic polynomial."""
    degree = len(coefficients) - 1
    if degree < 1 or coefficients[-1] != 1:
        raise ValueError("source polynomial must be monic")
    descending_tail = [0] + [coefficients[degree - i] for i in range(1, degree + 1)]
    powers = [0] * (maximum + 1)
    powers[0] = degree
    for k in range(1, maximum + 1):
        if k <= degree:
            value = sum(
                descending_tail[i] * powers[k - i]
                for i in range(1, k)
            )
            value += k * descending_tail[k]
        else:
            value = sum(
                descending_tail[i] * powers[k - i]
                for i in range(1, degree + 1)
            )
        powers[k] = -value
    return powers


def transformed_power_sums(root_powers: list[int], maximum: int, c: int) -> list[int]:
    """Power sums for h(alpha_i), where h(x)=x+c*x^2."""
    transformed = [0] * (maximum + 1)
    transformed[0] = root_powers[0]
    for k in range(1, maximum + 1):
        transformed[k] = sum(
            math.comb(k, a) * (c**a) * root_powers[k + a]
            for a in range(k + 1)
        )
    return transformed


def pair_sum_resolvent(ring, transformed_powers: list[int], degree: int):
    """Construct the monic resolvent from its power sums via Newton identities."""
    pair_powers = [0] * (degree + 1)
    pair_powers[0] = degree
    for m in range(1, degree + 1):
        numerator = sum(
            math.comb(m, a)
            * transformed_powers[a]
            * transformed_powers[m - a]
            for a in range(m + 1)
        ) - (2**m) * transformed_powers[m]
        if numerator % 2:
            raise ArithmeticError(f"nonintegral pair power sum at m={m}")
        pair_powers[m] = numerator // 2

    elementary = [0] * (degree + 1)
    elementary[0] = 1
    for k in range(1, degree + 1):
        numerator = sum(
            ((-1) ** (m - 1)) * elementary[k - m] * pair_powers[m]
            for m in range(1, k + 1)
        )
        if numerator % k:
            raise ArithmeticError(f"nonintegral elementary symmetric sum at k={k}")
        elementary[k] = numerator // k

    coefficients = [0] * (degree + 1)
    for k in range(degree + 1):
        coefficients[degree - k] = ((-1) ** k) * elementary[k]
    return ring(coefficients)


def factor_with_certificate(
    resolvent, expected_degrees: list[int], expected_degree_24_count: int
):
    if not resolvent.is_squarefree():
        return None, {"reason": "resolvent is not squarefree"}
    factorization = list(resolvent.factor())
    actual_degrees = sorted(int(factor.degree()) for factor, exponent in factorization for _ in range(int(exponent)))
    exponents = [int(exponent) for _, exponent in factorization]
    certificate = {
        "actualDegrees": actual_degrees,
        "expectedDegrees": sorted(expected_degrees),
        "exponents": exponents,
    }
    if any(exponent != 1 for exponent in exponents):
        certificate["reason"] = "nontrivial factor exponent"
        return None, certificate
    if actual_degrees != sorted(expected_degrees):
        certificate["reason"] = "factor degrees do not match GAP orbit sizes"
        return None, certificate
    degree_24 = [factor for factor, _ in factorization if factor.degree() == 24]
    if len(degree_24) != expected_degree_24_count:
        certificate["reason"] = (
            f"expected {expected_degree_24_count} degree-24 factors, "
            f"found {len(degree_24)}"
        )
        return None, certificate
    return degree_24, certificate


def reduce_polynomial(polynomial, mode: str):
    if mode == "none":
        return polynomial
    started = time.monotonic()
    if mode == "best":
        reduced = polynomial.parent()(pari(polynomial).polredbest())
    elif mode == "abs":
        reduced = polynomial.parent()(pari(polynomial).polredabs())
    else:
        raise ValueError(f"unknown reduction mode: {mode}")
    log(f"polynomial reduction ({mode}) took {time.monotonic() - started:.2f}s")
    return reduced


def coefficient_line(polynomial) -> str:
    coefficients = [int(value) for value in polynomial.list()]
    coefficients += [0] * (25 - len(coefficients))
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("candidate is not monic of degree 24")
    if coefficients[0] == 0:
        raise ValueError("candidate has zero constant coefficient")
    if math.gcd(*coefficients) != 1:
        raise ValueError("candidate coefficient gcd is not one")
    return ",".join(str(value) for value in coefficients)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_id")
    parser.add_argument("polynomial_index", type=int)
    parser.add_argument("--expected-target")
    parser.add_argument("--orbit-map", type=Path, default=ORBIT_MAP_PATH)
    parser.add_argument("--transforms", default="1,2,3,5,7")
    parser.add_argument("--reduce", choices=("none", "best", "abs"), default="best")
    parser.add_argument("--nfdisc", action="store_true")
    parser.add_argument("--all-degree-24", action="store_true")
    parser.add_argument(
        "--expected-source-hash",
        help="Require the verified ledger source to have this exact coefficient hash.",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        help=(
            "Write the certified result as one JSONL row and print only a "
            "coefficient-free summary. The output must be a new file under data/."
        ),
    )
    args = parser.parse_args()

    source = load_source(args.submission_id, args.polynomial_index)
    if source["status"] != "accepted":
        raise ValueError("ledger source is not accepted")
    if (
        args.expected_source_hash
        and str(source["coefficient_hash"]) != args.expected_source_hash
    ):
        raise ValueError("verified source coefficient hash mismatch")
    if args.output_jsonl is not None:
        output_path = args.output_jsonl.expanduser().resolve()
        data_root = (ROOT / "data").resolve()
        if output_path.parent != data_root:
            raise ValueError("--output-jsonl must be a direct child of data/")
        temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
        if output_path.exists() or temporary_path.exists():
            raise FileExistsError("refusing to overwrite pair-sum result output")
    source_label = str(source["label"])
    orbit_row = load_orbit_row(source_label, args.orbit_map)
    length_24 = orbit_row["targets"]
    if not args.all_degree_24 and len(length_24) != 1:
        raise ValueError(
            f"{source_label} has {len(length_24)} length-24 pair orbits; "
            "pass --all-degree-24 to extract the full certified factor set"
        )
    mapped_target_labels = {str(row["targetLabel"]) for row in length_24}
    target_label = (
        str(length_24[0]["targetLabel"]) if len(length_24) == 1 else None
    )
    if args.expected_target and args.expected_target not in mapped_target_labels:
        raise ValueError(
            f"target mismatch: GAP map says {sorted(mapped_target_labels)}, "
            f"expected {args.expected_target}"
        )

    coefficients = [int(value) for value in str(source["coefficients"]).split(",")]
    ring = PolynomialRing(ZZ, "x")
    source_polynomial = ring(coefficients)
    if source_polynomial.degree() != 24 or not source_polynomial.is_irreducible():
        raise ValueError("ledger source does not pass local degree/irreducibility checks")

    pair_degree = 24 * 23 // 2
    started = time.monotonic()
    root_powers = root_power_sums(coefficients, 2 * pair_degree)
    log(f"computed source Newton sums in {time.monotonic() - started:.2f}s")

    selected_factors = None
    certificate = None
    selected_transform = None
    attempts = []
    for transform in [int(value) for value in args.transforms.split(",") if value.strip()]:
        attempt_started = time.monotonic()
        transformed = transformed_power_sums(root_powers, pair_degree, transform)
        resolvent = pair_sum_resolvent(ring, transformed, pair_degree)
        resolvent_hash = hashlib.sha256(
            ",".join(str(int(value)) for value in resolvent.list()).encode("utf-8")
        ).hexdigest()
        log(
            f"c={transform}: built degree-{resolvent.degree()} resolvent "
            f"in {time.monotonic() - attempt_started:.2f}s"
        )
        factor_started = time.monotonic()
        selected_factors, certificate = factor_with_certificate(
            resolvent,
            [int(value) for value in orbit_row["orbitSizes"]],
            len(length_24),
        )
        attempt = {
            "transform": transform,
            "resolventSha256": resolvent_hash,
            "factorSeconds": round(time.monotonic() - factor_started, 3),
            "certificate": certificate,
        }
        attempts.append(attempt)
        log(
            f"c={transform}: factor/certificate took {attempt['factorSeconds']:.2f}s; "
            f"accepted={selected_factors is not None}"
        )
        if selected_factors is not None:
            selected_transform = transform
            break
    if selected_factors is None:
        print(json.dumps({"status": "no_separating_transform", "attempts": attempts}, sort_keys=True))
        return 2

    common = {
        "status": "certified",
        "workerExitCode": 0,
        "sourceSubmissionId": args.submission_id,
        "sourcePolynomialIndex": args.polynomial_index,
        "sourceCoefficientSha256": str(source["coefficient_hash"]),
        "sourceLabel": source_label,
        "sourceR": int(source["r"]),
        "transform": {"kind": "x+c*x^2", "c": selected_transform},
        "reduction": args.reduce,
        "orbitCertificate": certificate,
        "orbitTargets": length_24,
        "attempts": attempts,
    }
    candidate_rows = []
    for factor_index, selected in enumerate(selected_factors):
        candidate = reduce_polynomial(selected, args.reduce)
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("reduced candidate failed degree/monicity/irreducibility checks")
        line = coefficient_line(candidate)
        candidate_row = {
            "factorIndex": factor_index,
            "targetR": int(candidate.number_of_real_roots()),
            "coefficientLine": line,
            "coefficientBytes": len(line.encode("utf-8")),
            "coefficientSha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
            "polynomialDiscriminantAbs": str(abs(int(candidate.discriminant()))),
        }
        if args.nfdisc:
            disc_started = time.monotonic()
            candidate_row["fieldDiscriminantAbs"] = str(abs(int(pari(candidate).nfdisc())))
            candidate_row["nfdiscSeconds"] = round(time.monotonic() - disc_started, 3)
        candidate_rows.append(candidate_row)

    if args.all_degree_24:
        result = {
            **common,
            "status": "certified_multi",
            "candidates": candidate_rows,
            "elapsedSeconds": round(time.monotonic() - started, 3),
        }
    else:
        result = {
            **common,
            **candidate_rows[0],
            "targetLabel": target_label,
            "targetT": int(length_24[0]["targetT"]),
            "elapsedSeconds": round(time.monotonic() - started, 3),
        }
    rendered = json.dumps(result, separators=(",", ":"), sort_keys=True)
    if args.output_jsonl is not None:
        output_path = args.output_jsonl.expanduser().resolve()
        temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary_path.write_text(rendered + "\n", encoding="utf-8")
        temporary_path.replace(output_path)
        summary = {
            "elapsedSeconds": result["elapsedSeconds"],
            "output": str(output_path),
            "outputSha256": hashlib.sha256(
                (rendered + "\n").encode("utf-8")
            ).hexdigest(),
            "sourceCoefficientSha256": result["sourceCoefficientSha256"],
            "status": result["status"],
        }
        if args.all_degree_24:
            summary.update(
                {
                    "certifiedFactors": len(result["candidates"]),
                    "targetLabels": sorted(mapped_target_labels),
                    "targetRValues": sorted(
                        int(row["targetR"]) for row in result["candidates"]
                    ),
                }
            )
        else:
            summary.update(
                {
                    "coefficientSha256": result["coefficientSha256"],
                    "targetLabel": result["targetLabel"],
                    "targetR": result["targetR"],
                }
            )
        print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Find exact fully split unramified auxiliary primes for one owned base."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from sage.all import GF, ZZ, prime_range


ROOT = Path(__file__).resolve().parent


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_kernel_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--max-prime", type=int, default=200000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    helper = load_helper()
    source = helper.load_source(args.db, args.submission_id, args.polynomial_index)
    quotient = source["quotient"]
    discriminant = ZZ(quotient.discriminant())
    split_primes = []
    tested = 0
    for prime in prime_range(3, args.max_prime + 1):
        prime = int(prime)
        if discriminant % prime == 0:
            continue
        tested += 1
        factorization = quotient.change_ring(GF(prime)).factor()
        if (
            sum(int(factor.degree()) * int(exponent) for factor, exponent in factorization)
            == 12
            and len(factorization) == 12
            and all(int(factor.degree()) == 1 and int(exponent) == 1 for factor, exponent in factorization)
        ):
            split_primes.append(prime)
            if len(split_primes) >= args.count:
                break
    if len(split_primes) < args.count:
        raise ValueError(
            f"found only {len(split_primes)} split primes below {args.max_prime}"
        )
    result = {
        "fullySplitPrimes": split_primes,
        "maxPrime": args.max_prime,
        "networkCalls": 0,
        "primesTested": tested,
        "source": {key: value for key, value in source.items() if key != "quotient"},
        "submissionCalls": 0,
        "verification": "quotient mod p has twelve distinct linear factors",
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.resolve().write_text(rendered, encoding="utf-8")
        print(json.dumps({"output": str(args.output.resolve()), "primes": split_primes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

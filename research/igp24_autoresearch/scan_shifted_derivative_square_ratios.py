#!/usr/bin/env sage -python
"""Reconstruct square-ratio shifts for saved F5 quotient polynomials.

This is a read-only modular sieve over the exact quotient lines in the four
saved pair-product route shards.  A surviving integer ``a`` is certified by
the exact condition q(a)/q(0) in Q^{*2}, equivalently q(a)q(0) in Z^{*2}.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import time
from pathlib import Path

import gmpy2
import numpy as np


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "data" / "shifted_derivative_square_ratio_scan_all_routes.json"
PRIMES = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)


def exact_value(coefficients: tuple[int, ...], a: int) -> gmpy2.mpz:
    value = gmpy2.mpz(0)
    aa = gmpy2.mpz(a)
    for coefficient in reversed(coefficients):
        value = value * aa + coefficient
    return value


def load_sources() -> list[dict]:
    by_hash: dict[str, dict] = {}
    pattern = ROOT / "data" / "agent_f5_full_ledger_pair_product_routes_shard*of4.jsonl"
    for name in sorted(glob.glob(str(pattern))):
        path = Path(name)
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            source = row["source"]
            quotient_line = str(source["quotientLine"])
            digest = hashlib.sha256(quotient_line.encode()).hexdigest()
            if digest != str(source["quotientPolynomialSha256"]):
                raise ValueError(f"quotient hash mismatch in {path}:{line_number}")
            saved = by_hash.setdefault(
                digest,
                {
                    "coefficientLine": quotient_line,
                    "coefficientSha256": digest,
                    "rows": [],
                },
            )
            saved["rows"].append(
                {
                    "action": row["action"],
                    "source": source,
                }
            )
    return sorted(by_hash.values(), key=lambda row: row["coefficientSha256"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bound", type=int, default=200_000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.bound < 1:
        raise ValueError("bound must be positive")

    started = time.monotonic()
    sources = load_sources()
    if args.limit is not None:
        sources = sources[: args.limit]
    integers = np.arange(-args.bound, args.bound + 1, dtype=np.int64)
    residue_indexes = {prime: integers % prime for prime in PRIMES}
    hits = []
    sieve_survivor_count = 0

    for position, source in enumerate(sources, 1):
        coefficients = tuple(int(value) for value in source["coefficientLine"].split(","))
        if len(coefficients) != 13 or coefficients[-1] != 1 or coefficients[0] == 0:
            raise ValueError("bad saved quotient polynomial")
        mask = np.ones(integers.shape, dtype=np.bool_)
        mask[args.bound] = False
        for prime in PRIMES:
            residues = np.arange(prime, dtype=np.int64)
            values = np.zeros(prime, dtype=np.int64)
            for coefficient in reversed(coefficients):
                values = (values * residues + (coefficient % prime)) % prime
            products = values * (coefficients[0] % prime) % prime
            allowed = np.fromiter(
                (
                    product == 0
                    or pow(int(product), (prime - 1) // 2, prime) == 1
                    for product in products
                ),
                dtype=np.bool_,
                count=prime,
            )
            mask &= allowed[residue_indexes[prime]]
            if not mask.any():
                break
        candidates = integers[mask]
        sieve_survivor_count += int(candidates.size)
        q0 = gmpy2.mpz(coefficients[0])
        for a_value in candidates:
            a = int(a_value)
            qa = exact_value(coefficients, a)
            product = qa * q0
            if product > 0 and gmpy2.is_square(product):
                root = gmpy2.isqrt(product)
                hits.append(
                    {
                        "a": a,
                        "qAtA": str(qa),
                        "qAt0": str(q0),
                        "squareRootOfProduct": str(root),
                        "source": source,
                    }
                )
        if position % 100 == 0 or position == len(sources):
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "hits": len(hits),
                        "position": position,
                        "sources": len(sources),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    document = {
        "bound": args.bound,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactCondition": "q(a)*q(0) is a positive integer square; a != 0",
        "hits": hits,
        "primes": list(PRIMES),
        "sieveSurvivors": sieve_survivor_count,
        "sourceCount": len(sources),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "event": "complete",
                "hits": len(hits),
                "output": str(args.output),
                "sourceCount": len(sources),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

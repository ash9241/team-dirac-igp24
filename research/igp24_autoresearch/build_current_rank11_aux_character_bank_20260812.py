#!/usr/bin/env python3
"""Extend absent current rank-11 character tasks by one fully split prime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def factor_degrees(coefficients: list[int], prime: int) -> list[int]:
    # This module is run under Sage through the remote command below.
    from sage.all import GF, PolynomialRing

    ring = PolynomialRing(GF(prime), "y")
    polynomial = ring(coefficients)
    if not polynomial.is_squarefree():
        return []
    return sorted(
        int(factor.degree())
        for factor, exponent in polynomial.factor()
        for _ in range(int(exponent))
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--prime-count", type=int, default=3)
    parser.add_argument("--prime-max", type=int, default=20000)
    parser.add_argument("--max-candidates", type=int, default=128)
    args = parser.parse_args()

    import sqlite3
    from sage.all import prime_range

    source_rows = [json.loads(line) for line in args.bank.read_text().splitlines() if line.strip()]
    tasks = {}
    for row in source_rows:
        for attempt in row["attempts"]:
            tasks[int(attempt["globalCommandIndex"])] = (row, attempt)
    absent = set()
    for line in args.results.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("searchStatus") == "target_signature_absent_from_s_unit_space":
            absent.add(int(row["globalCommandIndex"]))

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    quotient_cache = {}
    split_cache = {}
    try:
        output_rows = []
        command_index = 0
        for index in sorted(absent):
            bank_row, attempt = tasks[index]
            identity = attempt["identity"]
            submission_id = str(identity[2])
            polynomial_index = int(identity[3])
            key = (submission_id, polynomial_index)
            if key not in quotient_cache:
                record = connection.execute(
                    "SELECT coefficients FROM polynomials WHERE submission_id=? AND polynomial_index=?",
                    key,
                ).fetchone()
                if record is None:
                    continue
                values = [int(value) for value in str(record[0]).split(",")]
                quotient_cache[key] = values[::2]
            quotient = quotient_cache[key]
            if key not in split_cache:
                ranked = []
                for prime in prime_range(3, args.prime_max + 1):
                    prime = int(prime)
                    degrees = factor_degrees(quotient, prime)
                    if len(degrees) >= 4:
                        ranked.append((-len(degrees), max(degrees), prime, degrees))
                split_cache[key] = sorted(ranked)
            forbidden = set()
            core = int(identity[4])
            value = core
            prime = 2
            while prime * prime <= value:
                if value % prime == 0:
                    forbidden.add(prime)
                    while value % prime == 0:
                        value //= prime
                prime = 3 if prime == 2 else prime + 2
            if value > 1:
                forbidden.add(value)
            split = [
                prime
                for _negative_count, _maximum_degree, prime, _degrees in split_cache[key]
                if prime not in forbidden
            ][: args.prime_count]
            attempts = []
            for auxiliary in split:
                digest = hashlib.sha256(
                    ("|".join(map(str, identity)) + f"|aux={auxiliary}").encode()
                ).hexdigest()
                output = (
                    ROOT / "data" / "current_rank11_character_aux_outputs_20260812"
                    / f"{identity[0]}_r{identity[1]}__{digest[:14]}.json"
                )
                command = list(attempt["command"])
                command_index_output = command.index("--output")
                command[command_index_output:command_index_output] = ["--aux-primes", str(auxiliary)]
                command[command.index("--max-candidates") + 1] = str(args.max_candidates)
                command[command.index("--output") + 1] = str(output.relative_to(ROOT))
                attempts.append(
                    {
                        "auxiliaryPrimes": [auxiliary],
                        "command": command,
                        "globalCommandIndex": command_index,
                        "identity": [*identity, auxiliary],
                        "output": str(output.resolve()),
                    }
                )
                command_index += 1
            if attempts:
                output_rows.append(
                    {
                        "attempts": attempts,
                        "logicalTaskId": bank_row["logicalTaskId"],
                        "status": "executable_current_rank11_one_aux",
                        "target": bank_row["target"],
                    }
                )
    finally:
        connection.close()

    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in output_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    summary = {
        "absentSourceCommands": len(absent),
        "commandCount": command_index,
        "livePairCount": len({row["logicalTaskId"] for row in output_rows}),
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "primeCountPerSource": args.prime_count,
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a final disjoint pilot from canonical fields omitted by prior banks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import agent_gold_c_build_character_census_pilot as shared


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def prime_divisors(value: int) -> set[int]:
    value = abs(int(value))
    result = set()
    candidate = 2
    while candidate * candidate <= value:
        if value % candidate == 0:
            result.add(candidate)
            while value % candidate == 0:
                value //= candidate
        candidate += 1 if candidate == 2 else 2
    if value > 1:
        result.add(value)
    return result


def primes_below(limit: int) -> list[int]:
    values = []
    for candidate in range(2, limit):
        if all(candidate % prime for prime in values if prime * prime <= candidate):
            values.append(candidate)
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--census", type=Path, default=shared.DEFAULT_CENSUS)
    parser.add_argument("--locked-bank", type=Path, default=shared.DEFAULT_LOCKED_BANK)
    parser.add_argument("--shallow-results", type=Path, default=shared.DEFAULT_SHALLOW_RESULTS)
    parser.add_argument("--exclude-bank", action="append", type=Path, default=[])
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    if args.bank.exists() or args.summary.exists():
        raise ValueError("refusing to overwrite a residual-field pilot")

    prior_locked = shared.locked_identities(args.locked_bank)
    prior_outputs, output_provenance = shared.output_identities()
    prior = prior_locked | prior_outputs
    used_fields = set()
    used_sources = set()
    used_pairs = set()
    for path in args.exclude_bank:
        for row in shared.load_jsonl(path):
            pair = (str(row["target"]["label"]), int(row["target"]["r"]))
            used_pairs.add(pair)
            for attempt in row.get("attempts", []):
                identity = attempt.get("identity")
                if identity:
                    normalized = (
                        str(identity[0]), int(identity[1]), str(identity[2]),
                        int(identity[3]), int(identity[4]),
                        tuple(int(value) for value in identity[5]),
                    )
                    prior.add(normalized)
                    used_sources.add((normalized[2], normalized[3]))
                if attempt.get("fieldCanonicalSha256"):
                    used_fields.add(str(attempt["fieldCanonicalSha256"]))

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        live_pairs = shared.shallow_absent_pairs(connection, args.shallow_results)
        snapshots = {
            pair: shared.current_snapshot(connection, pair) for pair in live_pairs
        }
    finally:
        connection.close()
    live_pairs.sort(key=lambda pair: (pair in used_pairs, pair))

    census_sha = shared.sha256_path(args.census)
    residual_fields = [
        row
        for row in shared.load_jsonl(args.census)
        if str(row["fieldCanonicalSha256"]) not in used_fields
    ]
    options = []
    for field in residual_fields:
        ramified = {
            int(value)
            for value in field.get("alignmentCertificate", {}).get(
                "ramifiedPrimes", []
            )
        }
        for representative in field["representatives"]:
            source = representative["source"]
            source_key = (
                str(source["submissionId"]), int(source["polynomialIndex"])
            )
            for pair in live_pairs:
                label, target_r = pair
                for core in field["targetNormCores"].get(label, []):
                    forbidden = ramified | prime_divisors(int(core))
                    available = [
                        prime
                        for prime in primes_below(80)
                        if prime >= 3 and prime not in forbidden
                    ]
                    schedules = [()] + [(prime,) for prime in available[:8]]
                    if len(available) >= 2:
                        schedules.append(tuple(available[:2]))
                    for auxiliary in schedules:
                        identity = (
                            label, target_r, source_key[0], source_key[1],
                            int(core), tuple(auxiliary),
                        )
                        if identity in prior:
                            continue
                        options.append(
                            {
                                "auxiliary": tuple(auxiliary),
                                "core": int(core),
                                "field": field,
                                "identity": identity,
                                "pair": pair,
                                "representative": representative,
                            }
                        )

    selected = []
    selected_identities = set()
    pair_use = Counter()
    field_use = Counter()
    source_use = Counter()

    def add(option):
        if option["identity"] in selected_identities:
            return
        selected.append(option)
        selected_identities.add(option["identity"])
        pair_use[option["pair"]] += 1
        field_use[option["field"]["fieldCanonicalSha256"]] += 1
        identity = option["identity"]
        source_use[(identity[2], identity[3])] += 1

    # Missing prior-bank pairs first.
    for pair in live_pairs:
        pool = [option for option in options if option["pair"] == pair]
        if not pool or len(selected) >= args.limit:
            continue
        add(
            min(
                pool,
                key=lambda option: (
                    field_use[option["field"]["fieldCanonicalSha256"]],
                    source_use[(option["identity"][2], option["identity"][3])],
                    len(option["auxiliary"]), option["auxiliary"],
                ),
            )
        )
    # Then touch every residual canonical field and representative.
    while len(selected) < args.limit:
        pool = [
            option for option in options
            if option["identity"] not in selected_identities
        ]
        if not pool:
            break
        add(
            min(
                pool,
                key=lambda option: (
                    field_use[option["field"]["fieldCanonicalSha256"]],
                    source_use[(option["identity"][2], option["identity"][3])],
                    pair_use[option["pair"]], len(option["auxiliary"]),
                    option["pair"], option["auxiliary"],
                ),
            )
        )

    rows_by_pair = {}
    for option in selected:
        field = option["field"]
        representative = option["representative"]
        source = representative["source"]
        label, target_r = option["pair"]
        auxiliary = option["auxiliary"]
        identity_text = json.dumps(option["identity"], separators=(",", ":"))
        command_id = hashlib.sha256(identity_text.encode()).hexdigest()
        output = args.outputs.resolve() / (
            f"{label}_r{target_r}__{field['fieldCanonicalSha256'][:12]}__"
            f"core{option['core']}__{command_id[:10]}.json"
        )
        command = [
            "sage", "-python", "character_kernel_gold_pilot.sage.py",
            "--db", str(args.db.resolve()),
            "--submission-id", str(source["submissionId"]),
            "--polynomial-index", str(source["polynomialIndex"]),
            "--target-label", label, "--target-r", str(target_r),
            "--norm-core", str(option["core"]),
            "--max-candidates", "64", "--witness-primes", "1000",
            "--seed", str(1 + int(command_id[:8], 16) % 2_000_000_000),
            "--output", str(output.relative_to(ROOT)),
        ]
        if auxiliary:
            command.extend(["--aux-primes", ",".join(map(str, auxiliary))])
        row = rows_by_pair.setdefault(
            option["pair"],
            {
                "attempts": [],
                "logicalTaskId": f"{label}_r{target_r}",
                "status": "residual_canonical_field_pilot",
                "target": {
                    "generatedAt": snapshots[option["pair"]]["generatedAt"],
                    "label": label,
                    "minimumDiscAbs": snapshots[option["pair"]]["minimumDiscAbs"],
                    "r": target_r,
                    "teamCount": snapshots[option["pair"]]["teamCount"],
                },
            },
        )
        row["attempts"].append(
            {
                "alignmentArtifact": str(args.census.relative_to(ROOT)),
                "alignmentArtifactSha256": census_sha,
                "auxiliaryPrimes": list(auxiliary),
                "command": command,
                "commandId": command_id,
                "fieldCanonicalSha256": field["fieldCanonicalSha256"],
                "identity": [*option["identity"][:5], list(auxiliary)],
                "output": str(output),
                "quotientPolynomialSha256": representative[
                    "quotientPolynomialSha256"
                ],
                "sourceCoefficientSha256": source["coefficientSha256"],
                "targetNormCore": option["core"],
                "targetNormCoreCertificate": {
                    "allUnambiguousCores": field["targetNormCores"][label],
                    "label": label,
                    "quotientT": field["quotientT"],
                },
            }
        )

    bank_rows = [rows_by_pair[pair] for pair in sorted(rows_by_pair)]
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in bank_rows
    )
    shared.write_atomic(args.bank, rendered)
    summary = {
        "bank": str(args.bank.resolve()),
        "bankSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "commands": len(selected),
        "distinctResidualFields": len(
            {option["field"]["fieldCanonicalSha256"] for option in selected}
        ),
        "distinctSources": len(
            {(option["identity"][2], option["identity"][3]) for option in selected}
        ),
        "existingOutputIdentities": len(prior_outputs),
        "networkCalls": 0,
        "residualCanonicalFields": len(residual_fields),
        "submissionCalls": 0,
    }
    shared.write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

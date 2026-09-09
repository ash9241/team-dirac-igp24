#!/usr/bin/env sage -python
"""Build a bounded new-auxiliary continuation after broad base exhaustion."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, prime_range

import agent_gold_c_build_character_census_pilot as shared


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def quotient_for(connection: sqlite3.Connection, source: dict):
    row = connection.execute(
        "SELECT coefficients FROM polynomials WHERE submission_id=? AND polynomial_index=?",
        (str(source["submissionId"]), int(source["polynomialIndex"])),
    ).fetchone()
    if row is None:
        raise ValueError("source polynomial missing from stable snapshot")
    values = [ZZ(value) for value in str(row[0]).split(",")]
    if len(values) != 25 or any(values[index] for index in range(1, 25, 2)):
        raise ValueError("source is not an even degree-24 polynomial")
    return PolynomialRing(ZZ, "y")(values[::2])


def split_rows(quotient, forbidden: set[int], count: int = 3) -> list[dict]:
    discriminant = ZZ(quotient.discriminant())
    rows = []
    for prime_value in prime_range(3, 2000):
        prime = int(prime_value)
        if prime in forbidden or discriminant % prime == 0:
            continue
        degrees = sorted(
            int(factor.degree())
            for factor, exponent in quotient.change_ring(GF(prime)).factor()
            for _ in range(int(exponent))
        )
        if len(degrees) < 4:
            continue
        rows.append(
            {
                "factorCount": len(degrees),
                "factorDegrees": degrees,
                "maximumFactorDegree": max(degrees),
                "prime": prime,
            }
        )
        if len(rows) == count:
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--alignment-results",
        type=Path,
        default=shared.DEFAULT_ALIGNMENT_RESULTS,
    )
    parser.add_argument("--locked-bank", type=Path, default=shared.DEFAULT_LOCKED_BANK)
    parser.add_argument("--shallow-results", type=Path, default=shared.DEFAULT_SHALLOW_RESULTS)
    parser.add_argument("--exclude-bank", action="append", type=Path, default=[])
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    if args.bank.exists() or args.summary.exists():
        raise ValueError("refusing to overwrite split-aux continuation")

    prior_locked = shared.locked_identities(args.locked_bank)
    prior_outputs, output_provenance = shared.output_identities()
    prior = prior_locked | prior_outputs
    prior_pairs = set()
    prior_pair_use = Counter()
    prior_source_use = Counter()
    prior_field_use = Counter()
    prior_label_core_use = Counter()
    prior_pair_core_use = Counter()
    prior_commands = 0
    for path in args.exclude_bank:
        for row in shared.load_jsonl(path):
            row_pair = (
                str(row["target"]["label"]), int(row["target"]["r"])
            )
            prior_pairs.add(row_pair)
            for attempt in row.get("attempts", []):
                identity = attempt.get("identity")
                if identity:
                    normalized = (
                        str(identity[0]), int(identity[1]), str(identity[2]),
                        int(identity[3]), int(identity[4]),
                        tuple(int(value) for value in identity[5]),
                    )
                    prior.add(normalized)
                    pair = (normalized[0], normalized[1])
                    source = (normalized[2], normalized[3])
                    label_core = (normalized[0], normalized[4])
                    prior_pair_use[pair] += 1
                    prior_source_use[source] += 1
                    prior_label_core_use[label_core] += 1
                    prior_pair_core_use[(pair, normalized[4])] += 1
                    prior_field_use[str(attempt["fieldCanonicalSha256"])] += 1
                    prior_commands += 1

    alignments = shared.load_new_alignments(args.alignment_results)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        live_pairs = shared.shallow_absent_pairs(connection, args.shallow_results)
        snapshots = {
            pair: shared.current_snapshot(connection, pair) for pair in live_pairs
        }
        options = []
        split_audit = {}
        for alignment in alignments:
            source = alignment["source"]
            source_key = (
                str(source["submissionId"]), int(source["polynomialIndex"])
            )
            quotient = quotient_for(connection, source)
            forbidden = {
                int(value)
                for value in alignment["alignment"].get("ramifiedPrimes", [])
            }
            rows = split_rows(quotient, forbidden)
            split_audit[f"{source_key[0]}:{source_key[1]}"] = rows
            for pair in live_pairs:
                label, target_r = pair
                for core in alignment["targetNormCores"].get(label, []):
                    for split in rows:
                        auxiliary = (int(split["prime"]),)
                        identity = (
                            label, target_r, source_key[0], source_key[1],
                            int(core), auxiliary,
                        )
                        if identity in prior:
                            continue
                        options.append(
                            {
                                "alignment": alignment,
                                "auxiliary": auxiliary,
                                "core": int(core),
                                "identity": identity,
                                "pair": pair,
                                "splitPrimeCertificate": split,
                            }
                        )
    finally:
        connection.close()

    live_pairs.sort(key=lambda pair: (prior_pair_use[pair], pair))
    selected = []
    selected_ids = set()
    pair_use = prior_pair_use.copy()
    source_use = prior_source_use.copy()
    field_use = prior_field_use.copy()
    label_core_use = prior_label_core_use.copy()
    pair_core_use = prior_pair_core_use.copy()

    def add(option):
        if option["identity"] in selected_ids:
            return
        selected.append(option)
        selected_ids.add(option["identity"])
        pair_use[option["pair"]] += 1
        identity = option["identity"]
        source_use[(identity[2], identity[3])] += 1
        field_use[option["alignment"]["fieldCanonicalSha256"]] += 1
        label_core_use[(identity[0], identity[4])] += 1
        pair_core_use[(option["pair"], identity[4])] += 1

    for pair in live_pairs:
        pool = [option for option in options if option["pair"] == pair]
        if not pool or len(selected) >= args.limit:
            continue
        add(
            min(
                pool,
                key=lambda option: (
                    source_use[(option["identity"][2], option["identity"][3])],
                    pair_core_use[(option["pair"], option["identity"][4])],
                    label_core_use[(option["identity"][0], option["identity"][4])],
                    field_use[option["alignment"]["fieldCanonicalSha256"]],
                    -option["splitPrimeCertificate"]["factorCount"],
                    option["splitPrimeCertificate"]["maximumFactorDegree"],
                    option["auxiliary"],
                ),
            )
        )
    while len(selected) < args.limit:
        pool = [option for option in options if option["identity"] not in selected_ids]
        if not pool:
            break
        add(
            min(
                pool,
                key=lambda option: (
                    pair_use[option["pair"]],
                    source_use[(option["identity"][2], option["identity"][3])],
                    pair_core_use[(option["pair"], option["identity"][4])],
                    label_core_use[(option["identity"][0], option["identity"][4])],
                    field_use[option["alignment"]["fieldCanonicalSha256"]],
                    -option["splitPrimeCertificate"]["factorCount"],
                    option["splitPrimeCertificate"]["maximumFactorDegree"],
                    option["pair"], option["auxiliary"],
                ),
            )
        )

    rows_by_pair = {}
    for option in selected:
        alignment = option["alignment"]
        source = alignment["source"]
        label, target_r = option["pair"]
        identity_text = json.dumps(option["identity"], separators=(",", ":"))
        command_id = hashlib.sha256(identity_text.encode()).hexdigest()
        output = args.outputs.resolve() / (
            f"{label}_r{target_r}__{source['coefficientSha256'][:12]}__"
            f"core{option['core']}__aux{option['auxiliary'][0]}__{command_id[:10]}.json"
        )
        command = [
            "sage", "-python", "character_kernel_gold_pilot.sage.py",
            "--db", str(args.db.resolve()),
            "--submission-id", str(source["submissionId"]),
            "--polynomial-index", str(source["polynomialIndex"]),
            "--target-label", label, "--target-r", str(target_r),
            "--norm-core", str(option["core"]),
            "--aux-primes", str(option["auxiliary"][0]),
            "--max-candidates", "64", "--witness-primes", "1000",
            "--seed", str(1 + int(command_id[:8], 16) % 2_000_000_000),
            "--output", str(output.relative_to(ROOT)),
        ]
        row = rows_by_pair.setdefault(
            option["pair"],
            {
                "attempts": [],
                "logicalTaskId": f"{label}_r{target_r}",
                "status": "new_split_auxiliary_continuation",
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
                "alignmentArtifact": alignment["alignmentArtifact"],
                "alignmentArtifactSha256": alignment["alignmentArtifactSha256"],
                "auxiliaryPrimes": list(option["auxiliary"]),
                "command": command,
                "commandId": command_id,
                "fieldCanonicalSha256": alignment["fieldCanonicalSha256"],
                "identity": [*option["identity"][:5], list(option["auxiliary"])],
                "output": str(output),
                "sourceCoefficientSha256": source["coefficientSha256"],
                "splitPrimeCertificate": option["splitPrimeCertificate"],
                "splitPrimeCoverage": (
                    "unramified auxiliary prime has at least four prime-ideal "
                    "factors, strictly enlarging the permitted S-unit support"
                ),
                "targetNormCore": option["core"],
                "targetNormCoreCertificate": {
                    "allUnambiguousCores": alignment["targetNormCores"][label],
                    "label": label,
                    "quotientT": alignment["quotientT"],
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
        "distinctFields": len({row["alignment"]["fieldCanonicalSha256"] for row in selected}),
        "distinctPairs": len({row["pair"] for row in selected}),
        "distinctSources": len({(row["identity"][2], row["identity"][3]) for row in selected}),
        "existingOutputIdentities": len(prior_outputs),
        "networkCalls": 0,
        "newDistinctLabelCores": len({
            (row["identity"][0], row["identity"][4]) for row in selected
            if prior_label_core_use[(row["identity"][0], row["identity"][4])] == 0
        }),
        "newDistinctPairCores": len({
            (row["pair"], row["identity"][4]) for row in selected
            if prior_pair_core_use[(row["pair"], row["identity"][4])] == 0
        }),
        "newDistinctSources": len({
            (row["identity"][2], row["identity"][3]) for row in selected
            if prior_source_use[(row["identity"][2], row["identity"][3])] == 0
        }),
        "priorExcludedCommands": prior_commands,
        "rawNewSplitAuxiliaryCandidates": len(options),
        "sourcesWithSplitCertificate": sum(bool(rows) for rows in split_audit.values()),
        "submissionCalls": 0,
    }
    shared.write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Build a bounded deep Cross-1500 split-prime pilot.

This is a stage-1 continuation, not a new construction family.  It audits the
completed shallow character-kernel outputs, retains only current unowned gold
pairs, proves the cheap local norm-parity obstruction before scheduling work,
and extends the same aligned degree-12 bases with deterministic pairs/triples
of strongly split auxiliary rational primes.

The output bank contains no network or submission operation.  Every command
invokes ``character_kernel_gold_pilot.sage.py``, which repeats alignment and
live-tier checks and stages nothing without a complete maximal-subgroup
certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, QQ, ZZ, prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_SHALLOW_RESULTS = DATA / "agent_gold_a_cross1500_results"
DEFAULT_BANK = DATA / "agent_deep_character_split_pilot_bank.jsonl"
DEFAULT_SUMMARY = DATA / "agent_deep_character_split_pilot_build_summary.json"
DEFAULT_OUTPUTS = DATA / "agent_deep_character_split_pilot_outputs"
DEFAULT_HTML = Path(
    "/path/to/private-file"
)
EXCLUDED_PAIR = ("24T18497", 0)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def pair_from_payload(payload: dict) -> tuple[str, int]:
    pair = str(payload["audit"]["liveTarget"]["pair"])
    label, r_text = pair.split("/r", 1)
    return label, int(r_text)


def current_gold(connection: sqlite3.Connection, pair: tuple[str, int]) -> dict:
    label, target_r = pair
    target = connection.execute(
        "SELECT team_count,minimum_disc_abs,generated_at FROM targets "
        "WHERE label=? AND r=?",
        pair,
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
    ).fetchone()
    owned = int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications "
            "WHERE label=? AND r=? AND scoreable=1",
            pair,
        ).fetchone()[0]
    )
    return {
        "baseline": baseline is not None,
        "generatedAt": str(target[2]) if target and target[2] else None,
        "locallyOwned": owned > 0,
        "minimumDiscAbs": str(target[1]) if target and target[1] else None,
        "ownedScoreableRows": owned,
        "pair": f"{label}/r{target_r}",
        "teamCount": int(target[0]) if target else None,
        "stageableGold": bool(
            target and int(target[0]) == 0 and baseline is None and owned == 0
        ),
    }


def quotient_for(
    connection: sqlite3.Connection,
    submission_id: str,
    polynomial_index: int,
):
    row = connection.execute(
        "SELECT coefficients,coefficient_hash FROM polynomials "
        "WHERE submission_id=? AND polynomial_index=?",
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError("source polynomial is absent from the ledger")
    coefficients = [ZZ(value) for value in str(row[0]).split(",")]
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or any(coefficients[index] for index in range(1, 25, 2))
    ):
        raise ValueError("source is not a monic even degree-24 polynomial")
    ring = PolynomialRing(ZZ, "y")
    quotient = ring(coefficients[::2])
    if not quotient.is_irreducible() or quotient.number_of_real_roots() != 12:
        raise ValueError("source quotient is not irreducible and totally real")
    return quotient, str(row[1])


def local_norm_audit(quotient, core_primes: tuple[int, ...]) -> dict:
    field = NumberField(quotient.change_ring(QQ), "a")
    rows = []
    feasible = True
    for prime in core_primes:
        residue_degrees = sorted(
            int(ideal.residue_class_degree()) for ideal in field.primes_above(prime)
        )
        odd_residue_degree_exists = any(value % 2 for value in residue_degrees)
        feasible &= odd_residue_degree_exists
        rows.append(
            {
                "oddResidueDegreeExists": odd_residue_degree_exists,
                "prime": int(prime),
                "residueDegrees": residue_degrees,
            }
        )
    return {
        "corePrimeRows": rows,
        "feasible": bool(feasible),
        "theorem": (
            "v_p(Norm(alpha)) is a Z-linear combination of residue degrees; "
            "an odd requested valuation is impossible if all are even"
        ),
    }


def split_prime_rows(
    quotient,
    forbidden: set[int],
    maximum_prime: int,
    count: int,
) -> list[dict]:
    discriminant = ZZ(quotient.discriminant())
    ranked = []
    for prime_value in prime_range(3, maximum_prime + 1):
        prime = int(prime_value)
        if prime in forbidden or discriminant % prime == 0:
            continue
        factor_degrees = sorted(
            int(factor.degree())
            for factor, exponent in quotient.change_ring(GF(prime)).factor()
            for _ in range(int(exponent))
        )
        ranked.append(
            (
                -len(factor_degrees),
                max(factor_degrees),
                prime,
                factor_degrees,
            )
        )
    selected = []
    for negative_factor_count, maximum_degree, prime, degrees in sorted(ranked):
        # At least four prime ideals makes this a genuine split-prime
        # expansion rather than another arbitrary tiny-prime schedule.
        if -negative_factor_count < 4:
            continue
        selected.append(
            {
                "factorCount": -negative_factor_count,
                "factorDegrees": degrees,
                "maximumFactorDegree": maximum_degree,
                "prime": prime,
            }
        )
        if len(selected) == count:
            break
    if len(selected) < count:
        raise ValueError(
            f"found only {len(selected)} sufficiently split primes below {maximum_prime}"
        )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--shallow-results", type=Path, default=DEFAULT_SHALLOW_RESULTS)
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--outputs", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--maximum-prime", type=int, default=5000)
    args = parser.parse_args()

    result_paths = sorted(args.shallow_results.glob("*.json"))
    if len(result_paths) != 618:
        raise ValueError(f"expected exactly 618 completed shallow outputs, found {len(result_paths)}")

    status_counts = Counter()
    payloads = []
    provenance_lines = []
    for path in result_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = str(payload["search"]["status"])
        status_counts[status] += 1
        payloads.append((path, payload))
        provenance_lines.append(
            f"{path.relative_to(ROOT)}\t{sha256_path(path)}\n"
        )
    if status_counts != Counter(
        {
            "completed": 48,
            "target_signature_absent_from_s_unit_space": 570,
        }
    ):
        raise ValueError(f"unexpected shallow status census: {status_counts}")

    absent_by_pair: dict[tuple[str, int], list[tuple[Path, dict]]] = defaultdict(list)
    all_pairs = set()
    tested_aux_by_identity: dict[tuple, set[tuple[int, ...]]] = defaultdict(set)
    for path, payload in payloads:
        pair = pair_from_payload(payload)
        all_pairs.add(pair)
        search = payload["search"]
        source = payload["audit"]["source"]
        identity = (
            pair[0],
            pair[1],
            str(source["submissionId"]),
            int(source["polynomialIndex"]),
            int(math.prod(int(value) for value in search["corePrimes"])),
        )
        tested_aux_by_identity[identity].add(
            tuple(int(value) for value in search["auxiliaryPrimes"])
        )
        if search["status"] == "target_signature_absent_from_s_unit_space":
            absent_by_pair[pair].append((path, payload))

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        snapshots = {pair: current_gold(connection, pair) for pair in sorted(all_pairs)}
        live_pairs = {
            pair
            for pair in absent_by_pair
            if pair != EXCLUDED_PAIR and snapshots[pair]["stageableGold"]
        }

        # One representative per exact (source field, target norm core).  A
        # representative may serve several requested signatures of one label.
        base_core_records = {}
        associations: dict[tuple, set[tuple[str, int]]] = defaultdict(set)
        for pair in sorted(live_pairs):
            for path, payload in absent_by_pair[pair]:
                source = payload["audit"]["source"]
                core_primes = tuple(
                    int(value) for value in payload["search"]["corePrimes"]
                )
                key = (
                    str(source["submissionId"]),
                    int(source["polynomialIndex"]),
                    core_primes,
                )
                base_core_records.setdefault(key, (path, payload))
                associations[key].add(pair)

        source_cache = {}
        local_audits = {}
        for key in sorted(base_core_records):
            submission_id, polynomial_index, core_primes = key
            source_key = (submission_id, polynomial_index)
            if source_key not in source_cache:
                source_cache[source_key] = quotient_for(
                    connection, submission_id, polynomial_index
                )
            quotient, _coefficient_hash = source_cache[source_key]
            local_audits[key] = local_norm_audit(quotient, core_primes)

        feasible_keys = [key for key in sorted(base_core_records) if local_audits[key]["feasible"]]
        blocked_keys = [key for key in sorted(base_core_records) if not local_audits[key]["feasible"]]
        feasible_pairs = set().union(*(associations[key] for key in feasible_keys))
        blocked_pairs = live_pairs - feasible_pairs

        rows_by_pair: dict[tuple[str, int], dict] = {}
        command_count = 0
        split_prime_audits = {}
        for key in feasible_keys:
            submission_id, polynomial_index, core_primes = key
            quotient, coefficient_hash = source_cache[(submission_id, polynomial_index)]
            prior_aux_primes = {
                prime
                for pair in associations[key]
                for auxiliary in tested_aux_by_identity[
                    (
                        pair[0],
                        pair[1],
                        submission_id,
                        polynomial_index,
                        int(math.prod(core_primes)),
                    )
                ]
                for prime in auxiliary
            }
            split_rows = split_prime_rows(
                quotient,
                set(core_primes) | prior_aux_primes,
                args.maximum_prime,
                3,
            )
            split_prime_audits[str(key)] = split_rows
            p1, p2, p3 = (int(row["prime"]) for row in split_rows)
            schedules = [(p1, p2), (p1, p2, p3)]

            for pair in sorted(associations[key]):
                label, target_r = pair
                row = rows_by_pair.setdefault(
                    pair,
                    {
                        "attempts": [],
                        "deepNormMatrixProvenance": {
                            "historicalBases": 155,
                            "historicalTasks": 2424,
                            "source": str(args.html),
                        },
                        "logicalTaskId": f"{label}_r{target_r}",
                        "status": "executable_deep_split_pilot",
                        "target": {
                            "generatedAt": snapshots[pair]["generatedAt"],
                            "label": label,
                            "r": target_r,
                            "teamCount": snapshots[pair]["teamCount"],
                        },
                    },
                )
                norm_core = int(math.prod(core_primes))
                for auxiliary in schedules:
                    identity = (
                        label,
                        target_r,
                        submission_id,
                        polynomial_index,
                        norm_core,
                        auxiliary,
                    )
                    identity_text = json.dumps(identity, separators=(",", ":"))
                    command_id = hashlib.sha256(identity_text.encode()).hexdigest()
                    suffix = "_".join(str(value) for value in auxiliary)
                    output = (
                        args.outputs
                        / f"{label}_r{target_r}__{coefficient_hash[:12]}__core{norm_core}__aux{suffix}__{command_id[:10]}.json"
                    )
                    seed = 1 + int(command_id[:8], 16) % 2_000_000_000
                    command = [
                        "sage",
                        "-python",
                        "character_kernel_gold_pilot.sage.py",
                        "--submission-id",
                        submission_id,
                        "--polynomial-index",
                        str(polynomial_index),
                        "--target-label",
                        label,
                        "--target-r",
                        str(target_r),
                        "--norm-core",
                        str(norm_core),
                        "--aux-primes",
                        ",".join(str(value) for value in auxiliary),
                        "--max-candidates",
                        "64",
                        "--witness-primes",
                        "1000",
                        "--seed",
                        str(seed),
                        "--output",
                        str(output.relative_to(ROOT)),
                    ]
                    row["attempts"].append(
                        {
                            "auxiliaryPrimes": list(auxiliary),
                            "command": command,
                            "commandId": command_id,
                            "identity": [
                                label,
                                target_r,
                                submission_id,
                                polynomial_index,
                                norm_core,
                                list(auxiliary),
                            ],
                            "localNormAudit": local_audits[key],
                            "output": str(output.resolve()),
                            "sourceCoefficientSha256": coefficient_hash,
                            "splitPrimeCertificate": split_rows,
                        }
                    )
                    command_count += 1
    finally:
        connection.close()

    bank_rows = [rows_by_pair[pair] for pair in sorted(rows_by_pair)]
    bank_text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in bank_rows
    )
    write_atomic(args.bank, bank_text)
    bank_hash = hashlib.sha256(bank_text.encode()).hexdigest()

    summary = {
        "bank": str(args.bank.resolve()),
        "bankSha256": bank_hash,
        "blockedBaseCoreAlignments": len(blocked_keys),
        "blockedLogicalPairs": len(blocked_pairs),
        "commands": command_count,
        "excludedOwnedPair": f"{EXCLUDED_PAIR[0]}/r{EXCLUDED_PAIR[1]}",
        "feasibleBaseCoreAlignments": len(feasible_keys),
        "feasibleLogicalPairs": len(feasible_pairs),
        "html": str(args.html.resolve()),
        "htmlSha256": sha256_path(args.html),
        "historicalDeepNormMatrix": {"bases": 155, "tasks": 2424},
        "liveGoldAbsentLogicalPairs": len(live_pairs),
        "localNormAudits": {str(key): local_audits[key] for key in sorted(local_audits)},
        "networkCalls": 0,
        "outputDirectory": str(args.outputs.resolve()),
        "resultProvenanceSha256": hashlib.sha256(
            "".join(provenance_lines).encode()
        ).hexdigest(),
        "rows": len(bank_rows),
        "shallowLogicalPairs": len(all_pairs),
        "shallowOutputs": len(result_paths),
        "shallowStatusCounts": dict(sorted(status_counts.items())),
        "splitPrimeAudits": split_prime_audits,
        "submissionCalls": 0,
        "workersRequired": 6,
    }
    write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

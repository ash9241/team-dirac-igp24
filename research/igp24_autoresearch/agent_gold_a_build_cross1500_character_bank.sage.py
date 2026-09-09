#!/usr/bin/env sage -python
"""Rebuild an executable Cross-1500 direct-character task bank.

The historical Cross-1000 engine grouped work by a totally real degree-12
base field, aligned every rational quadratic character exactly, and only then
searched sign/S-unit spaces.  This offline builder recreates that shape from
the current ledger and the exact GAP audit:

* one logical task per current nonbaseline, locally-unowned gold pair;
* several independently aligned local base fields per degree-12 quotient;
* a deterministic shallow/deep auxiliary-prime schedule per base;
* exact command arrays for ``character_kernel_gold_pilot.sage.py``;
* no network and no submission operations.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ, prime_range


ROOT = Path(__file__).resolve().parent
DEFAULT_AUDIT = ROOT / "data" / "agent_gold_b_character_all_r_gold_audit.json"
DEFAULT_ACTION_MAP = ROOT / "data" / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_OUTPUT = ROOT / "data" / "agent_gold_a_cross1500_character_bank.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "agent_gold_a_cross1500_character_bank_summary.json"


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_bank_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_targets(path: Path):
    audit = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in audit["rows"] if row["hasOwnedEvenQuotientT"]]
    targets = []
    for row in rows:
        for pair in row["goldPairs"]:
            targets.append(
                {
                    "generatedAt": pair["generatedAt"],
                    "label": row["label"],
                    "r": int(pair["r"]),
                    "t": int(row["t"]),
                    "teamCount": int(pair["teamCount"]),
                    "quotientT12": int(row["quotientT12"]),
                }
            )
    return audit, sorted(targets, key=lambda row: (row["t"], row["r"]))


def source_labels_by_quotient(path: Path, quotient_ts: set[int]):
    result: dict[int, dict[str, int]] = {value: {} for value in quotient_ts}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        label = str(row["sourceLabel"])
        for system in row["systems"]:
            quotient_t = int(system["blockActionT12"])
            if quotient_t not in result:
                continue
            existing = result[quotient_t].get(label)
            system_count = int(row["systemCount"])
            result[quotient_t][label] = (
                system_count if existing is None else min(existing, system_count)
            )
    return result


def even_coefficients(text: str):
    values = [ZZ(value) for value in text.split(",")]
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] <= 0
        or any(values[index] for index in range(1, 25, 2))
    ):
        return None
    return values


def candidate_sources(db: Path, labels_by_q):
    label_to_qs: dict[str, set[int]] = defaultdict(set)
    system_counts: dict[tuple[int, str], int] = {}
    for quotient_t, labels in labels_by_q.items():
        for label, count in labels.items():
            label_to_qs[label].add(quotient_t)
            system_counts[(quotient_t, label)] = count
    if not label_to_qs:
        return {}

    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        labels = sorted(label_to_qs)
        output: dict[int, list[dict]] = defaultdict(list)
        chunk_size = 500
        for offset in range(0, len(labels), chunk_size):
            chunk = labels[offset:offset + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            query = f"""
                SELECT v.label,v.r,v.field_disc_abs,v.submission_id,
                       v.polynomial_index,p.coefficients,p.coefficient_hash
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                WHERE v.status='accepted' AND v.scoreable=1
                  AND v.label IN ({placeholders})
            """
            for row in connection.execute(query, chunk):
                coefficients = even_coefficients(str(row[5]))
                if coefficients is None:
                    continue
                quotient_line = ",".join(str(value) for value in coefficients[::2])
                common = {
                    "coefficientBytes": len(str(row[5]).encode()),
                    "coefficientSha256": str(row[6]),
                    "fieldDiscAbs": str(row[2]) if row[2] else None,
                    "label": str(row[0]),
                    "polynomialIndex": int(row[4]),
                    "quotientCoefficients": quotient_line,
                    "r": int(row[1]),
                    "submissionId": str(row[3]),
                }
                for quotient_t in label_to_qs[common["label"]]:
                    output[quotient_t].append(
                        {
                            **common,
                            "blockSystemCount": system_counts[
                                (quotient_t, common["label"])
                            ],
                        }
                    )
    finally:
        connection.close()

    for quotient_t, rows in output.items():
        rows.sort(
            key=lambda row: (
                row["blockSystemCount"] != 1,
                row["coefficientBytes"],
                len(row["fieldDiscAbs"] or "9" * 100000),
                row["fieldDiscAbs"] or "9" * 100000,
                row["coefficientSha256"],
            )
        )
    return output


def align_bases(quotient_t: int, candidates, target_labels, maximum_bases: int,
                maximum_trials: int):
    ring = PolynomialRing(ZZ, "y")
    accepted = []
    seen_quotients = set()
    failures = []
    for candidate in candidates:
        quotient_line = candidate["quotientCoefficients"]
        quotient_hash = hashlib.sha256(quotient_line.encode()).hexdigest()
        if quotient_hash in seen_quotients:
            continue
        seen_quotients.add(quotient_hash)
        if len(seen_quotients) > maximum_trials:
            break
        quotient = ring([ZZ(value) for value in quotient_line.split(",")])
        try:
            if not quotient.is_irreducible() or quotient.number_of_real_roots() != 12:
                raise ValueError("quotient is not irreducible and totally real")
            core = int(ZZ(quotient[0]).squarefree_part())
            if core <= 0:
                raise ValueError("source norm core is not positive")
            alignment = HELPER.character_alignment(quotient, quotient_t)
            source_cores = alignment[
                "labelToUnambiguousSquarefreeNormCores"
            ].get(candidate["label"], [])
            if core not in source_cores:
                raise ValueError("alignment does not recover verified source")
            all_target_cores = {
                label: alignment["labelToUnambiguousSquarefreeNormCores"].get(
                    label, []
                )
                for label in sorted(target_labels)
            }
            target_cores = {
                label: cores for label, cores in all_target_cores.items() if cores
            }
            if not target_cores:
                raise ValueError("base aligns none of the live target labels")
            accepted.append(
                {
                    **{key: value for key, value in candidate.items()
                       if key != "quotientCoefficients"},
                    "alignment": {
                        "quotientOrder": alignment["quotientOrder"],
                        "quotientT": alignment["quotientT"],
                        "ramifiedPrimes": alignment["ramifiedPrimes"],
                        "sourceNormCore": core,
                        "targetNormCores": target_cores,
                        "unalignedTargetLabels": sorted(
                            set(target_labels) - set(target_cores)
                        ),
                    },
                    "quotientSha256": quotient_hash,
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "label": candidate["label"],
                    "polynomialIndex": candidate["polynomialIndex"],
                    "quotientSha256": quotient_hash,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "submissionId": candidate["submissionId"],
                }
            )
        covered = {
            label
            for base in accepted
            for label in base["alignment"]["targetNormCores"]
        }
        if len(accepted) >= maximum_bases and covered >= set(target_labels):
            break
    # Retain a compact set-cover of the labels that this quotient's arithmetic
    # bases can actually distinguish.  The scan may accept many partially
    # aligned bases before the last character becomes unambiguous.
    selected = []
    uncovered = set(target_labels)
    pool = list(accepted)
    while pool and len(selected) < maximum_bases:
        best = max(
            pool,
            key=lambda base: (
                len(
                    uncovered
                    & set(base["alignment"]["targetNormCores"])
                ),
                -base["coefficientBytes"],
            ),
        )
        gain = uncovered & set(best["alignment"]["targetNormCores"])
        if not gain and selected:
            break
        selected.append(best)
        uncovered -= gain
        pool.remove(best)
        if not uncovered and len(selected) >= min(2, maximum_bases):
            break
    return selected, failures


def auxiliary_schedules(base, target_core: int, count: int):
    forbidden = set(base["alignment"]["ramifiedPrimes"])
    forbidden.update(ZZ(target_core).prime_divisors())
    primes = [int(p) for p in prime_range(3, 200) if int(p) not in forbidden]
    schedules = [[]]
    schedules.extend([[prime] for prime in primes[: max(0, count - 2)]])
    if len(schedules) < count and len(primes) >= 2:
        schedules.append(primes[:2])
    return schedules[:count]


def command_for(task_id: str, target, base, target_core: int, aux, attempt: int):
    suffix = "none" if not aux else "_".join(str(value) for value in aux)
    output = (
        ROOT / "data" / "agent_gold_a_cross1500_results" /
        f"{task_id}__b{attempt:02d}__aux{suffix}.json"
    )
    command = [
        "sage", "-python", "character_kernel_gold_pilot.sage.py",
        "--submission-id", base["submissionId"],
        "--polynomial-index", str(base["polynomialIndex"]),
        "--target-label", target["label"],
        "--target-r", str(target["r"]),
        "--norm-core", str(target_core),
        "--max-candidates", "64",
        "--witness-primes", "1000",
        "--seed", str(1000003 + target["t"] * 29 + target["r"] * 101 + attempt),
        "--output", str(output.relative_to(ROOT)),
    ]
    if aux:
        command.extend(["--aux-primes", ",".join(str(value) for value in aux)])
    return {"auxiliaryPrimes": aux, "command": command, "output": str(output)}


def write_atomic(path: Path, text: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--bases-per-quotient", type=int, default=8)
    parser.add_argument("--trials-per-quotient", type=int, default=120)
    parser.add_argument("--aux-schedules-per-base", type=int, default=6)
    args = parser.parse_args()

    audit, targets = load_targets(args.audit)
    quotient_ts = {row["quotientT12"] for row in targets}
    labels_by_q = source_labels_by_quotient(args.action_map, quotient_ts)
    candidates = candidate_sources(args.db, labels_by_q)
    target_labels_by_q: dict[int, set[str]] = defaultdict(set)
    for target in targets:
        target_labels_by_q[target["quotientT12"]].add(target["label"])

    bases_by_q = {}
    failure_audit = {}
    for index, quotient_t in enumerate(sorted(quotient_ts), start=1):
        bases, failures = align_bases(
            quotient_t,
            candidates.get(quotient_t, []),
            target_labels_by_q[quotient_t],
            args.bases_per_quotient,
            args.trials_per_quotient,
        )
        bases_by_q[quotient_t] = bases
        failure_audit[quotient_t] = failures
        print(
            json.dumps(
                {
                    "alignedBases": len(bases),
                    "candidateBases": len(candidates.get(quotient_t, [])),
                    "completedQuotients": index,
                    "quotientT": quotient_t,
                    "totalQuotients": len(quotient_ts),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    tasks = []
    for target in targets:
        quotient_t = target["quotientT12"]
        bases = bases_by_q.get(quotient_t, [])
        task_id = f"{target['label']}_r{target['r']}"
        attempts = []
        base_rows = []
        for base_index, base in enumerate(bases):
            cores = base["alignment"]["targetNormCores"].get(target["label"])
            if not cores:
                continue
            target_core = min(int(value) for value in cores)
            base_rows.append(
                {
                    **base,
                    "selectedTargetNormCore": target_core,
                    "targetNormCores": cores,
                }
            )
            for aux_index, aux in enumerate(
                auxiliary_schedules(
                    base, target_core, args.aux_schedules_per_base
                )
            ):
                attempt = base_index * args.aux_schedules_per_base + aux_index
                attempts.append(
                    command_for(
                        task_id, target, base, target_core, aux, attempt
                    )
                )
        tasks.append(
            {
                "attempts": attempts,
                "bases": base_rows,
                "calibrationExactLabelPrecision": 391 / 396,
                "logicalTaskId": task_id,
                "projectedLivePoints": 391 / 396,
                "status": "executable" if attempts else "blocked_no_aligned_base",
                "target": target,
            }
        )

    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in tasks
    )
    write_atomic(args.output, rendered)
    executable = [row for row in tasks if row["status"] == "executable"]
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256(args.action_map),
        "alignedBaseCounts": {
            str(key): len(value) for key, value in sorted(bases_by_q.items())
        },
        "audit": str(args.audit.resolve()),
        "auditSha256": sha256(args.audit),
        "blockedTasks": len(tasks) - len(executable),
        "calibration": {
            "directCharacterExactLabels": 391,
            "directCharacterSubmitted": 396,
            "precision": 391 / 396,
            "source": "Team_Dirac_IGP24_From_77_to_13.html",
        },
        "commandAttempts": sum(len(row["attempts"]) for row in executable),
        "executableTasks": len(executable),
        "failureAudit": {str(key): value for key, value in failure_audit.items()},
        "historicalEngine": {
            "deepNormTasks": 2424,
            "discoveryBases": 216,
            "standardMatrixTasks": 2149,
        },
        "networkCalls": 0,
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "projectedLivePoints": sum(
            row["projectedLivePoints"] for row in executable
        ),
        "submissionCalls": 0,
        "tasks": len(tasks),
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    write_atomic(args.summary, summary_text)
    print(json.dumps({key: summary[key] for key in (
        "blockedTasks", "commandAttempts", "executableTasks",
        "projectedLivePoints", "tasks")}, sort_keys=True))
    return 0 if summary["blockedTasks"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a disjoint stage-1 extension to the SHA-locked Cross-1500 bank.

The original 630-command bank is immutable.  This builder consumes only its
``blocked_no_aligned_base`` rows, exact arithmetic alignment certificates
created after that freeze, and the current read-only ledger.  It emits one
compact base per newly aligned quotient and removes command identities already
present in either the locked bank or an existing result artifact.

No network, submission, or staging operation is performed here.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_LOCKED_BANK = DATA / "agent_gold_a_cross1500_character_bank.jsonl"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_OUTPUT = DATA / "agent_gold_b_cross1500_extension_bank.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_b_cross1500_extension_bank_summary.json"

# One exact, source-unambiguous base per newly executable quotient.  Q77 base2
# has the same exact target cores as base1 with fewer ramified primes and no
# prior search outputs, making it the compact disjoint choice.
ALIGNMENTS = {
    12: DATA / "agent_gold_b_character_alignment_q12_base1.json",
    77: DATA / "agent_gold_b_character_alignment_q77_base2.json",
    78: DATA / "agent_gold_b_character_alignment_q78_base1.json",
    81: DATA / "agent_gold_b_character_alignment_q81_base1.json",
    89: DATA / "agent_gold_b_character_alignment_q89_base1.json",
}

RECOVERED_CHARACTER_MANIFESTS = [
    DATA / "agent_gold_b_html_wave_manifests" / "nested_character_25.txt",
    DATA / "agent_gold_b_html_wave_manifests" / "t00035_atlas_280.txt",
    DATA / "agent_gold_b_html_wave_manifests" / "score700_bank_1000.txt",
    DATA / "agent_gold_b_html_wave_manifests" / "calibration_83.txt",
]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def primes_below(limit: int) -> list[int]:
    result = []
    for value in range(2, limit):
        if all(value % prime for prime in result if prime * prime <= value):
            result.append(value)
    return result


def prime_divisors(value: int) -> set[int]:
    result = set()
    remaining = abs(int(value))
    for prime in primes_below(math.isqrt(remaining) + 2):
        if prime * prime > remaining:
            break
        if remaining % prime == 0:
            result.add(prime)
            while remaining % prime == 0:
                remaining //= prime
    if remaining > 1:
        result.add(remaining)
    return result


def auxiliary_schedules(alignment: dict, target_core: int) -> list[list[int]]:
    forbidden = set(int(value) for value in alignment["ramifiedPrimes"])
    forbidden.update(prime_divisors(target_core))
    available = [
        prime for prime in primes_below(200) if prime >= 3 and prime not in forbidden
    ]
    schedules = [[]]
    schedules.extend([[prime] for prime in available[:4]])
    if len(available) >= 2:
        schedules.append(available[:2])
    return schedules[:6]


def parse_command_identity(command: list[str]) -> tuple | None:
    def option(name: str):
        try:
            return command[command.index(name) + 1]
        except (ValueError, IndexError):
            return None

    label = option("--target-label")
    target_r = option("--target-r")
    submission_id = option("--submission-id")
    polynomial_index = option("--polynomial-index")
    norm_core = option("--norm-core")
    if None in (label, target_r, submission_id, polynomial_index, norm_core):
        return None
    auxiliary = option("--aux-primes")
    return (
        str(label),
        int(target_r),
        str(submission_id),
        int(polynomial_index),
        int(norm_core),
        tuple(int(value) for value in auxiliary.split(",") if value)
        if auxiliary
        else (),
    )


def locked_identities(rows: list[dict]) -> set[tuple]:
    result = set()
    for row in rows:
        for attempt in row.get("attempts", []):
            identity = parse_command_identity([str(value) for value in attempt["command"]])
            if identity is not None:
                result.add(identity)
    return result


def result_identities() -> tuple[set[tuple], list[dict]]:
    identities = set()
    provenance = []
    for name in glob.glob(str(DATA / "**" / "*.json"), recursive=True):
        path = Path(name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        audit = payload.get("audit")
        search = payload.get("search")
        if not isinstance(audit, dict) or not isinstance(search, dict):
            continue
        source = audit.get("source")
        structure = audit.get("targetStructure")
        live = audit.get("liveTarget")
        if not all(isinstance(value, dict) for value in (source, structure, live)):
            continue
        pair = str(live.get("pair", ""))
        if "/r" not in pair:
            continue
        core_primes = search.get("corePrimes", [])
        auxiliary = search.get("auxiliaryPrimes", [])
        if not isinstance(core_primes, list) or not isinstance(auxiliary, list):
            continue
        try:
            identity = (
                str(structure["targetLabel"]),
                int(pair.rsplit("/r", 1)[1]),
                str(source["submissionId"]),
                int(source["polynomialIndex"]),
                int(math.prod(int(value) for value in core_primes)),
                tuple(int(value) for value in auxiliary),
            )
        except (KeyError, TypeError, ValueError):
            continue
        identities.add(identity)
        provenance.append(
            {
                "identity": [*identity[:5], list(identity[5])],
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
            }
        )
    return identities, provenance


def current_snapshot(connection: sqlite3.Connection, label: str, target_r: int) -> dict:
    target = connection.execute(
        "SELECT team_count,minimum_disc_abs,generated_at FROM targets "
        "WHERE label=? AND r=?",
        (label, target_r),
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1",
        (label, target_r),
    ).fetchone()
    owned = int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
            (label, target_r),
        ).fetchone()[0]
    )
    return {
        "baseline": baseline is not None,
        "generatedAt": str(target[2]) if target and target[2] else None,
        "locallyOwned": owned > 0,
        "minimumDiscAbs": str(target[1]) if target and target[1] else None,
        "ownedScoreableRows": owned,
        "teamCount": int(target[0]) if target else None,
    }


def page17_provenance(connection: sqlite3.Connection, locked_mtime: float) -> dict:
    rows = connection.execute(
        """
        SELECT s.submission_id,p.polynomial_index,p.coefficient_hash,
               v.label,v.r,s.created_at,s.synced_at
        FROM submissions AS s
        JOIN polynomials AS p USING(submission_id)
        LEFT JOIN verifications AS v USING(submission_id,polynomial_index)
        WHERE s.synced_at>? AND substr(s.created_at,1,10) BETWEEN '2026-07-07' AND '2026-07-09'
        ORDER BY s.submission_id,p.polynomial_index
        """,
        (locked_mtime,),
    ).fetchall()
    rendered = "".join(
        "\t".join("" if value is None else str(value) for value in row) + "\n"
        for row in rows
    )
    return {
        "logicalRows": len(rows),
        "logicalRowsSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "submissions": len({str(row[0]) for row in rows}),
        "syncedAfterLockedBankMtime": locked_mtime,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locked-bank", type=Path, default=DEFAULT_LOCKED_BANK)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--extra-alignment",
        action="append",
        type=Path,
        default=[],
        help="additional exact alignment artifact; may repeat a quotient",
    )
    args = parser.parse_args()

    locked_rows = read_jsonl(args.locked_bank)
    blocked_rows = [
        row for row in locked_rows if row.get("status") == "blocked_no_aligned_base"
    ]
    prior_locked = locked_identities(locked_rows)
    prior_results, result_provenance = result_identities()
    prior = prior_locked | prior_results

    alignment_records: dict[int, list[tuple[Path, dict]]] = {}
    alignment_paths = list(ALIGNMENTS.values()) + [
        path.resolve() for path in args.extra_alignment
    ]
    for path in alignment_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        alignment = payload["alignment"]
        source = payload["source"]
        quotient_t = int(alignment["quotientT"])
        source_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(
            source["label"], []
        )
        if int(source["squarefreeNormCore"]) not in [int(value) for value in source_cores]:
            raise ValueError(f"source is not unambiguously aligned in {path}")
        existing_sources = {
            (
                record[1]["source"]["submissionId"],
                int(record[1]["source"]["polynomialIndex"]),
            )
            for record in alignment_records.get(quotient_t, [])
        }
        source_key = (source["submissionId"], int(source["polynomialIndex"]))
        if source_key not in existing_sources:
            alignment_records.setdefault(quotient_t, []).append((path, payload))

    tasks = []
    excluded = []
    newly_executable = set()
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        for locked_row in blocked_rows:
            target = locked_row["target"]
            quotient_t = int(target["quotientT12"])
            records = alignment_records.get(quotient_t, [])
            if not records:
                continue
            attempts = []
            base_rows = []
            for alignment_path, payload in records:
                alignment = payload["alignment"]
                source = payload["source"]
                target_cores = [
                    int(value)
                    for value in alignment[
                        "labelToUnambiguousSquarefreeNormCores"
                    ].get(target["label"], [])
                ]
                if not target_cores:
                    continue
                newly_executable.add(locked_row["logicalTaskId"])
                selected_core = min(target_cores)
                base_rows.append(
                    {
                        "alignmentArtifact": str(alignment_path.relative_to(ROOT)),
                        "alignmentArtifactSha256": sha256_path(alignment_path),
                        "alignment": alignment,
                        "source": source,
                        "selectedTargetNormCore": selected_core,
                        "targetNormCores": target_cores,
                    }
                )
                for auxiliary in auxiliary_schedules(alignment, selected_core):
                    identity = (
                        str(target["label"]),
                        int(target["r"]),
                        str(source["submissionId"]),
                        int(source["polynomialIndex"]),
                        selected_core,
                        tuple(auxiliary),
                    )
                    identity_text = "|".join(str(value) for value in identity[:5])
                    identity_text += "|aux=" + ",".join(str(value) for value in auxiliary)
                    command_id = hashlib.sha256(identity_text.encode()).hexdigest()
                    if identity in prior:
                        excluded.append(
                            {
                                "commandId": command_id,
                                "identity": [*identity[:5], list(identity[5])],
                                "reason": (
                                    "already_in_locked_bank"
                                    if identity in prior_locked
                                    else "existing_result_identity"
                                ),
                            }
                        )
                        continue
                    suffix = "none" if not auxiliary else "_".join(map(str, auxiliary))
                    output = (
                        DATA
                        / "agent_gold_b_cross1500_extension_results"
                        / (
                            f"{target['label']}_r{target['r']}__q{quotient_t}__"
                            f"{source['coefficientSha256'][:12]}__core{selected_core}__"
                            f"aux{suffix}__{command_id[:10]}.json"
                        )
                    )
                    seed = 1 + int(command_id[:8], 16) % 2_000_000_000
                    command = [
                        "sage", "-python", "character_kernel_gold_pilot.sage.py",
                        "--submission-id", str(source["submissionId"]),
                        "--polynomial-index", str(source["polynomialIndex"]),
                        "--target-label", str(target["label"]),
                        "--target-r", str(target["r"]),
                        "--norm-core", str(selected_core),
                        "--max-candidates", "64",
                        "--witness-primes", "1000",
                        "--seed", str(seed),
                        "--output", str(output.relative_to(ROOT)),
                    ]
                    if auxiliary:
                        command.extend(["--aux-primes", ",".join(map(str, auxiliary))])
                    attempts.append(
                        {
                            "alignmentArtifact": str(alignment_path.relative_to(ROOT)),
                            "alignmentArtifactSha256": sha256_path(alignment_path),
                            "auxiliaryPrimes": auxiliary,
                            "command": command,
                            "commandId": command_id,
                            "identity": [*identity[:5], list(identity[5])],
                            "output": str(output),
                            "sourceCoefficientSha256": source["coefficientSha256"],
                            "targetNormCore": selected_core,
                            "targetNormCoreCertificate": {
                                "allUnambiguousCores": target_cores,
                                "label": target["label"],
                                "quotientT": quotient_t,
                            },
                        }
                    )
            if not attempts:
                continue
            tasks.append(
                {
                    "attempts": attempts,
                    "bases": base_rows,
                    "currentTargetSnapshot": current_snapshot(
                        connection, str(target["label"]), int(target["r"])
                    ),
                    "lockedBankStatus": locked_row["status"],
                    "lockedTarget": target,
                    "logicalTaskId": locked_row["logicalTaskId"],
                    "status": "executable",
                    "target": target,
                }
            )

        still_blocked = [
            row["logicalTaskId"]
            for row in blocked_rows
            if row["logicalTaskId"] not in newly_executable
        ]
        page17 = page17_provenance(connection, args.locked_bank.stat().st_mtime)
        ledger_counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("submissions", "polynomials", "verifications", "targets")
        }
    finally:
        connection.close()

    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in tasks
    )
    write_atomic(args.output, rendered)
    manifests = [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)}
        for path in RECOVERED_CHARACTER_MANIFESTS
    ]
    summary = {
        "alignmentArtifacts": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)}
            for path in alignment_paths
        ],
        "blockedLogicalTasksBefore": len(blocked_rows),
        "blockedLogicalTasksAfter": len(still_blocked),
        "commandAttempts": sum(len(row["attempts"]) for row in tasks),
        "excludedExistingCommandIdentities": len(excluded),
        "excludedExistingCommands": excluded,
        "existingResultArtifactsAudited": len(result_provenance),
        "ledgerLogicalCounts": ledger_counts,
        "lockedBank": str(args.locked_bank.resolve()),
        "lockedBankSha256": sha256_path(args.locked_bank),
        "networkCalls": 0,
        "newlyExecutableLogicalTaskIds": sorted(newly_executable),
        "newlyExecutableLogicalTasks": len(newly_executable),
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "page17BackfillProvenance": page17,
        "recoveredCharacterManifests": manifests,
        "stillBlockedLogicalTaskIds": still_blocked,
        "submissionCalls": 0,
    }
    write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "blockedLogicalTasksBefore",
                    "blockedLogicalTasksAfter",
                    "commandAttempts",
                    "excludedExistingCommandIdentities",
                    "newlyExecutableLogicalTasks",
                    "outputSha256",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

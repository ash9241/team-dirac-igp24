#!/usr/bin/env python3
"""Join live gold rows, exact alignments, and owned bases into executable tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def command_text(parts: list[str]) -> str:
    return " ".join(shlex.quote(value) for value in parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit", type=Path, default=ROOT / "data" / "agent_gold_b_character_all_r_gold_audit.json"
    )
    parser.add_argument(
        "--bases", type=Path, default=ROOT / "data" / "agent_gold_b_character_base_candidates.jsonl"
    )
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument(
        "--alignment-glob",
        default="data/agent_gold_b_character_alignment_q*_base*.json",
    )
    parser.add_argument(
        "--split-prime-glob",
        default="data/agent_gold_b_character_split_primes_q*_base*.json",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "agent_gold_b_character_wave_tasks.jsonl"
    )
    parser.add_argument(
        "--summary", type=Path, default=ROOT / "data" / "agent_gold_b_character_wave_tasks_summary.json"
    )
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    structure = {row["label"]: row for row in audit["rows"]}
    bases = [
        json.loads(line)
        for line in args.bases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    base_by_key = {
        (row["submissionId"], int(row["polynomialIndex"])): row for row in bases
    }

    connection = sqlite3.connect(f"file:{args.db}?immutable=1", uri=True)
    try:
        live_rows = connection.execute(
            """
            SELECT t.label,t.t,t.r,t.team_count,t.discovered,
                   t.minimum_disc_abs,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.team_count=0 AND b.label IS NULL AND owned.label IS NULL
            ORDER BY t.t,t.r
            """
        ).fetchall()
    finally:
        connection.close()
    live = {
        (str(row[0]), int(row[2])): {
            "discovered": int(row[4]),
            "generatedAt": str(row[6]) if row[6] else None,
            "minimumDiscAbs": str(row[5]) if row[5] else None,
            "teamCount": int(row[3]),
        }
        for row in live_rows
        if str(row[0]) in structure and int(row[2]) % 4 == 0
    }

    alignments = []
    for path in sorted(ROOT.glob(args.alignment_glob)):
        record = json.loads(path.read_text(encoding="utf-8"))
        source = record["source"]
        key = (source["submissionId"], int(source["polynomialIndex"]))
        if key not in base_by_key:
            continue
        alignments.append((path, record, base_by_key[key]))

    split_primes_by_source = {}
    for path in sorted(ROOT.glob(args.split_prime_glob)):
        record = json.loads(path.read_text(encoding="utf-8"))
        source = record["source"]
        key = (source["submissionId"], int(source["polynomialIndex"]))
        split_primes_by_source[key] = {
            "artifact": path,
            "primes": [int(value) for value in record["fullySplitPrimes"]],
        }

    tasks = []
    seen = set()
    for alignment_path, record, base in alignments:
        alignment = record["alignment"]
        quotient_t = int(alignment["quotientT"])
        cores_by_label = alignment["labelToUnambiguousSquarefreeNormCores"]
        for (label, target_r), snapshot in live.items():
            target_structure = structure[label]
            if int(target_structure["quotientT12"]) != quotient_t:
                continue
            source_key = (base["submissionId"], int(base["polynomialIndex"]))
            split_record = split_primes_by_source.get(source_key)
            auxiliary_variants = [()] + (
                [(prime,) for prime in split_record["primes"]]
                if split_record is not None
                else []
            )
            for norm_core in cores_by_label.get(label, []):
                for auxiliary_primes in auxiliary_variants:
                    identity = (
                        label,
                        target_r,
                        base["submissionId"],
                        int(base["polynomialIndex"]),
                        int(norm_core),
                    )
                    identity_with_auxiliary = identity + auxiliary_primes
                    if identity_with_auxiliary in seen:
                        continue
                    seen.add(identity_with_auxiliary)
                    identity_text = "|".join(map(str, identity))
                    if auxiliary_primes:
                        identity_text += "|aux=" + ",".join(map(str, auxiliary_primes))
                    digest = hashlib.sha256(identity_text.encode()).hexdigest()
                    task_id = f"character_{label}_r{target_r}_{digest[:12]}"
                    output_rel = f"data/agent_gold_b_wave_{task_id}.json"
                    seed = 1 + int(digest[:8], 16) % 2_000_000_000
                    command_parts = [
                        "/usr/local/bin/sage",
                        "-python",
                        "agent_gold_b_character_signature_search.sage.py",
                        "--submission-id",
                        base["submissionId"],
                        "--polynomial-index",
                        str(base["polynomialIndex"]),
                        "--target-label",
                        label,
                        "--target-r",
                        str(target_r),
                        "--norm-core",
                        str(norm_core),
                        "--max-candidates",
                        "64",
                        "--solutions-per-sign",
                        "2",
                        "--witness-primes",
                        "1000",
                        "--seed",
                        str(seed),
                    ]
                    if auxiliary_primes:
                        command_parts.extend(
                            ["--aux-primes", ",".join(map(str, auxiliary_primes))]
                        )
                    command_parts.extend(["--output", output_rel])
                    task = {
                        "alignmentArtifact": str(alignment_path.relative_to(ROOT)),
                        "alignmentArtifactSha256": file_sha256(alignment_path),
                        "auxiliaryPrimes": list(auxiliary_primes),
                        "baseRank": int(base["baseRank"]),
                        "command": command_text(command_parts),
                        "outputPath": output_rel,
                        "quotientT12": quotient_t,
                        "sourceCoefficientSha256": base["coefficientSha256"],
                        "sourceLabel": base["sourceLabel"],
                        "sourceNormCore": int(base["sourceNormCore"]),
                        "sourcePolynomialIndex": int(base["polynomialIndex"]),
                        "sourceR": int(base["sourceR"]),
                        "sourceSubmissionId": base["submissionId"],
                        "targetLabel": label,
                        "targetNormCore": int(norm_core),
                        "targetR": target_r,
                        "targetT": int(target_structure["t"]),
                        "taskId": task_id,
                        "liveTargetSnapshot": snapshot,
                        "priority": (
                            0
                            if target_r in (0, 24)
                            else 1
                            if target_r in (4, 20)
                            else 2
                            if target_r in (8, 16)
                            else 3
                        ),
                    }
                    if split_record is not None:
                        task.update(
                            {
                                "splitPrimeArtifact": str(
                                    split_record["artifact"].relative_to(ROOT)
                                ),
                                "splitPrimeArtifactSha256": file_sha256(
                                    split_record["artifact"]
                                ),
                                "splitPrimeVerification": (
                                    "each auxiliary p gives twelve distinct linear "
                                    "factors of the source quotient modulo p"
                                ),
                            }
                        )
                    tasks.append(task)

    tasks.sort(
        key=lambda row: (
            row["priority"],
            len(row["auxiliaryPrimes"]),
            row["baseRank"],
            row["quotientT12"],
            row["targetT"],
            row["targetR"],
            row["taskId"],
        )
    )
    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in tasks)
    args.output.resolve().write_text(rendered, encoding="utf-8")
    summary = {
        "alignmentArtifacts": len(alignments),
        "audit": str(args.audit.resolve()),
        "auditSha256": file_sha256(args.audit),
        "basePool": str(args.bases.resolve()),
        "basePoolSha256": file_sha256(args.bases),
        "livePairCount": len({(row["targetLabel"], row["targetR"]) for row in tasks}),
        "networkCalls": 0,
        "output": str(args.output.resolve()),
        "outputBytes": len(rendered.encode()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "quotientTs": sorted({row["quotientT12"] for row in tasks}),
        "submissionCalls": 0,
        "splitPrimeArtifacts": len(split_primes_by_source),
        "taskCount": len(tasks),
    }
    args.summary.resolve().write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

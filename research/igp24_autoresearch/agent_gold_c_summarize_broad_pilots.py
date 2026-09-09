#!/usr/bin/env python3
"""Freeze an aggregate audit of the broad-base character pilot waves."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


WAVES = [
    ("pilot1", "agent_gold_c_character_census_pilot_bank.jsonl", "agent_gold_c_character_census_pilot_results.jsonl", "agent_gold_c_character_census_pilot_run_summary.json"),
    ("wave1", "agent_gold_c_character_census_pilot2_wave1_bank.jsonl", "agent_gold_c_character_census_pilot2_wave1_results.jsonl", "agent_gold_c_character_census_pilot2_wave1_run_summary.json"),
    ("wave1_retry", "agent_gold_c_character_census_pilot2_wave1_sqlite_retry_bank.jsonl", "agent_gold_c_character_census_pilot2_wave1_sqlite_retry_results.jsonl", "agent_gold_c_character_census_pilot2_wave1_sqlite_retry_run_summary.json"),
    ("wave2", "agent_gold_c_character_census_pilot2_wave2_bank.jsonl", "agent_gold_c_character_census_pilot2_wave2_results.jsonl", "agent_gold_c_character_census_pilot2_wave2_run_summary.json"),
    ("wave3", "agent_gold_c_character_census_pilot2_wave3_stable_bank.jsonl", "agent_gold_c_character_census_pilot2_wave3_results.jsonl", "agent_gold_c_character_census_pilot2_wave3_run_summary.json"),
    ("wave4", "agent_gold_c_character_census_pilot2_wave4_bank.jsonl", "agent_gold_c_character_census_pilot2_wave4_results.jsonl", "agent_gold_c_character_census_pilot2_wave4_run_summary.json"),
    ("wave5", "agent_gold_c_character_census_pilot2_wave5_bank.jsonl", "agent_gold_c_character_census_pilot2_wave5_results.jsonl", "agent_gold_c_character_census_pilot2_wave5_run_summary.json"),
    ("residual_fields", "agent_gold_c_character_census_pilot2_residual_fields_bank.jsonl", "agent_gold_c_character_census_pilot2_residual_fields_results.jsonl", "agent_gold_c_character_census_pilot2_residual_fields_run_summary.json"),
]


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA / "agent_gold_c_character_broad_pilots_aggregate_summary.json",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite aggregate pilot summary")

    identities = {}
    field_ids = set()
    source_ids = set()
    wave_summaries = []
    launched = 0
    for name, bank_name, results_name, summary_name in WAVES:
        bank_path = DATA / bank_name
        results_path = DATA / results_name
        summary_path = DATA / summary_name
        if not all(path.exists() for path in (bank_path, results_path, summary_path)):
            raise ValueError(f"incomplete wave artifacts for {name}")
        flat = []
        for row in load_jsonl(bank_path):
            for attempt in row.get("attempts", []):
                flat.append(attempt)
                identity = attempt["identity"]
                key = (
                    str(identity[0]), int(identity[1]), str(identity[2]),
                    int(identity[3]), int(identity[4]),
                    tuple(int(value) for value in identity[5]),
                )
                identities.setdefault(key, {})["attempt"] = attempt
                if attempt.get("fieldCanonicalSha256"):
                    field_ids.add(str(attempt["fieldCanonicalSha256"]))
                source_ids.add((key[2], key[3]))
        results = load_jsonl(results_path)
        for result in results:
            attempt = flat[int(result["globalCommandIndex"])]
            identity = attempt["identity"]
            key = (
                str(identity[0]), int(identity[1]), str(identity[2]),
                int(identity[3]), int(identity[4]),
                tuple(int(value) for value in identity[5]),
            )
            # A stable-snapshot retry supersedes the transient infrastructure
            # failure for the same mathematical command identity.
            identities.setdefault(key, {})["result"] = result
            identities[key]["resolvedByWave"] = name
        run_summary = json.loads(summary_path.read_text())
        launched += int(run_summary["launchedThisRun"])
        wave_summaries.append(
            {
                "bank": str(bank_path.resolve()),
                "bankSha256": sha256_path(bank_path),
                "commands": len(flat),
                "exactCertified": int(run_summary["exactCertified"]),
                "failed": int(run_summary["failed"]),
                "name": name,
                "results": str(results_path.resolve()),
                "resultsSha256": sha256_path(results_path),
                "runSummary": str(summary_path.resolve()),
                "runSummarySha256": sha256_path(summary_path),
                "staged": int(run_summary["staged"]),
                "workers": int(run_summary["workers"]),
            }
        )

    unresolved = [key for key, row in identities.items() if "result" not in row]
    resolved_results = [row["result"] for row in identities.values() if "result" in row]
    summary = {
        "canonicalFieldsRepresented": len(field_ids),
        "exactCertified": sum(bool(row.get("exactCertified")) for row in resolved_results),
        "launchedWorkerCommandsIncludingRetries": launched,
        "networkCalls": 0,
        "resolvedOutputParseStatusCounts": dict(
            sorted(Counter(str(row.get("outputParseStatus")) for row in resolved_results).items())
        ),
        "resolvedSearchStatusCounts": dict(
            sorted(Counter(str(row.get("searchStatus")) for row in resolved_results).items())
        ),
        "resolvedWorkerFailures": sum(bool(row.get("workerFailure")) for row in resolved_results),
        "sourceRepresentationsUsed": len(source_ids),
        "staged": sum(bool(row.get("staged")) for row in resolved_results),
        "submissionCalls": 0,
        "uniqueCommandIdentities": len(identities),
        "unresolvedCommandIdentities": [
            [*key[:5], list(key[5])] for key in unresolved
        ],
        "waves": wave_summaries,
    }
    write_atomic(args.output, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if not unresolved and not summary["resolvedWorkerFailures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

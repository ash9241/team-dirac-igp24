#!/usr/bin/env sage -python
"""Build an exact direct-character bank for T00110 sole-held pairs.

The frozen public placement snapshot supplies holder identity only.  Reachability
is recomputed from the complete degree-24 block-action map and exact arithmetic
character alignments of locally verified even fields.  This builder is offline
and performs no submission or network calls.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_ACTION_MAP = ROOT / "data" / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_SNAPSHOT = Path(
    "/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl"
)
DEFAULT_OUTPUT = ROOT / "data" / "agent_rank10_character_bank.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "agent_rank10_character_bank_summary.json"


def load_cross_builder():
    path = ROOT / "agent_gold_a_build_cross1500_character_bank.sage.py"
    spec = importlib.util.spec_from_file_location("cross1500_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import builder from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CROSS = load_cross_builder()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def current_state(db: Path):
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        targets = {
            (str(label), int(r)): {
                "generatedAt": str(generated_at) if generated_at else None,
                "minimumDiscAbs": str(minimum_disc) if minimum_disc else None,
                "teamCount": int(team_count),
            }
            for label, r, team_count, minimum_disc, generated_at in connection.execute(
                "SELECT label,r,team_count,minimum_disc_abs,generated_at FROM targets"
            )
        }
    finally:
        connection.close()
    return owned, targets


def target_quotients(action_rows: list[dict], target_labels: set[str]):
    result: dict[str, set[int]] = defaultdict(set)
    for row in action_rows:
        label = str(row["sourceLabel"])
        if label not in target_labels:
            continue
        for system in row["systems"]:
            kernel_order = int(row["sourceOrder"]) // int(system["blockActionOrder"])
            if bool(system.get("flipInSource")) and kernel_order == 2**11:
                result[label].add(int(system["blockActionT12"]))
    return result


def source_labels_by_quotient(action_rows: list[dict], quotient_ts: set[int]):
    result: dict[int, dict[str, int]] = {value: {} for value in quotient_ts}
    for row in action_rows:
        label = str(row["sourceLabel"])
        for system in row["systems"]:
            quotient_t = int(system["blockActionT12"])
            kernel_order = int(row["sourceOrder"]) // int(system["blockActionOrder"])
            if (
                quotient_t not in result
                or not bool(system.get("flipInSource"))
                or kernel_order != 2**11
            ):
                continue
            old = result[quotient_t].get(label)
            count = int(row["systemCount"])
            result[quotient_t][label] = count if old is None else min(old, count)
    return result


def command_for(
    target: dict,
    base: dict,
    core: int,
    auxiliary: list[int],
    index: int,
    result_dir: Path,
):
    aux_text = "none" if not auxiliary else "_".join(str(value) for value in auxiliary)
    task_id = f"{target['label']}_r{target['r']}"
    output = result_dir / f"{task_id}__b{index:02d}__core{core}__aux{aux_text}.json"
    seed_text = "|".join(
        [
            task_id,
            str(base["submissionId"]),
            str(base["polynomialIndex"]),
            str(core),
            ",".join(str(value) for value in auxiliary),
        ]
    )
    seed = 1 + int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16) % 2_000_000_000
    command = [
        "sage",
        "-python",
        "character_kernel_gold_pilot.sage.py",
        "--submission-id",
        str(base["submissionId"]),
        "--polynomial-index",
        str(base["polynomialIndex"]),
        "--target-label",
        str(target["label"]),
        "--target-r",
        str(target["r"]),
        "--norm-core",
        str(core),
        "--max-candidates",
        "64",
        "--witness-primes",
        "1000",
        "--seed",
        str(seed),
        "--max-team-count",
        "1",
        "--output",
        str(output.relative_to(ROOT)),
    ]
    if auxiliary:
        command.extend(["--aux-primes", ",".join(str(value) for value in auxiliary)])
    return {
        "auxiliaryPrimes": auxiliary,
        "command": command,
        "output": str(output),
        "seed": seed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--bases-per-quotient", type=int, default=4)
    parser.add_argument("--trials-per-quotient", type=int, default=120)
    parser.add_argument("--attempts-per-pair", type=int, default=6)
    parser.add_argument("--expected-team-id", default="")
    parser.add_argument("--expected-team-number", default="IGP24-T00110")
    parser.add_argument(
        "--result-dir",
        type=Path,
        default=ROOT / "data" / "agent_rank10_character_results",
    )
    args = parser.parse_args()

    snapshot = load_jsonl(args.snapshot)
    if not snapshot or any(
        row.get("teamNumber") != args.expected_team_number
        or (
            args.expected_team_id
            and row.get("teamId") != args.expected_team_id
        )
        or int(row.get("kTeams", 0)) != 1
        for row in snapshot
    ):
        raise ValueError(
            "snapshot is not a pure pinned-team sole-held placement census"
        )
    action_rows = load_jsonl(args.action_map)
    owned, current = current_state(args.db)
    quotient_by_label = target_quotients(
        action_rows, {str(row["label"]) for row in snapshot}
    )

    targets = []
    for row in snapshot:
        pair = (str(row["label"]), int(row["r"]))
        state = current.get(pair)
        if pair in owned or pair[1] % 4 or state is None or state["teamCount"] not in (0, 1):
            continue
        for quotient_t in sorted(quotient_by_label.get(pair[0], ())):
            targets.append(
                {
                    "holderIncumbentDiscAbs": str(row["minScoringDiscAbs"]),
                    "label": pair[0],
                    "ledgerSnapshot": state,
                    "quotientT12": quotient_t,
                    "r": pair[1],
                    "t": int(row["t"]),
                }
            )
    target_labels_by_q: dict[int, set[str]] = defaultdict(set)
    for target in targets:
        target_labels_by_q[target["quotientT12"]].add(target["label"])
    quotient_ts = set(target_labels_by_q)
    source_labels = source_labels_by_quotient(action_rows, quotient_ts)
    candidates = CROSS.candidate_sources(args.db, source_labels)

    bases_by_q = {}
    failures_by_q = {}
    for completed, quotient_t in enumerate(sorted(quotient_ts), start=1):
        bases, failures = CROSS.align_bases(
            quotient_t,
            candidates.get(quotient_t, []),
            target_labels_by_q[quotient_t],
            args.bases_per_quotient,
            args.trials_per_quotient,
        )
        bases_by_q[quotient_t] = bases
        failures_by_q[quotient_t] = failures
        print(
            json.dumps(
                {
                    "alignedBases": len(bases),
                    "candidateBases": len(candidates.get(quotient_t, [])),
                    "completedQuotients": completed,
                    "quotientT": quotient_t,
                    "totalQuotients": len(quotient_ts),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    rows = []
    for target in targets:
        bases = [
            base
            for base in bases_by_q[target["quotientT12"]]
            if target["label"] in base["alignment"]["targetNormCores"]
        ]
        attempts = []
        for base in bases:
            cores = base["alignment"]["targetNormCores"][target["label"]]
            for core in cores:
                schedules = CROSS.auxiliary_schedules(
                    base, int(core), args.attempts_per_pair
                )
                for auxiliary in schedules:
                    attempts.append(
                        command_for(
                            target,
                            base,
                            int(core),
                            auxiliary,
                            len(attempts),
                            args.result_dir.resolve(),
                        )
                    )
                    if len(attempts) >= args.attempts_per_pair:
                        break
                if len(attempts) >= args.attempts_per_pair:
                    break
            if len(attempts) >= args.attempts_per_pair:
                break
        rows.append(
            {
                "attempts": attempts,
                "bases": bases,
                "logicalTaskId": f"{target['label']}_r{target['r']}",
                "status": "executable" if attempts else "blocked_no_aligned_base",
                "target": target,
            }
        )

    # Highest possible swing first, then extreme signatures, then Atlas order.
    rows.sort(
        key=lambda row: (
            row["status"] != "executable",
            -len(str(row["target"]["holderIncumbentDiscAbs"])),
            min(row["target"]["r"], 24 - row["target"]["r"]),
            row["target"]["t"],
            row["target"]["r"],
        )
    )
    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    CROSS.write_atomic(args.output, rendered)
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256_path(args.action_map),
        "alignedBaseCounts": {str(q): len(v) for q, v in sorted(bases_by_q.items())},
        "attempts": sum(len(row["attempts"]) for row in rows),
        "blockedPairs": sum(row["status"] != "executable" for row in rows),
        "candidateBaseCounts": {
            str(q): len(candidates.get(q, [])) for q in sorted(quotient_ts)
        },
        "executablePairs": sum(row["status"] == "executable" for row in rows),
        "failureCounts": {str(q): len(v) for q, v in sorted(failures_by_q.items())},
        "networkCalls": 0,
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "quotientTs": sorted(quotient_ts),
        "pinnedTeamId": args.expected_team_id,
        "pinnedTeamNumber": args.expected_team_number,
        "resultDir": str(args.result_dir.resolve()),
        "snapshot": str(args.snapshot.resolve()),
        "snapshotSha256": sha256_path(args.snapshot),
        "structuralPairs": len(rows),
        "submissionCalls": 0,
    }
    CROSS.write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

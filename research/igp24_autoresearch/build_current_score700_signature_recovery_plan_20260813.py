#!/usr/bin/env python3
"""Build a live-value plan from the certified score700 arithmetic families.

The historical archive contains exact character lifts, including proper
Kummer-submodule descents.  A single certified representative supplies a
degree-12 base field and Kummer squareclass.  Multiplying that representative
by unit squareclasses can realize other real signatures without changing the
known character overgroup.  This planner selects only families whose already
observed exact terminal groups have currently unowned low-holder signatures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DESCENT_RE = re.compile(r"exact_transitive_subgroup_descent:(?:24T)?(\d+)")
SUBGROUP_SUFFIX_RE = re.compile(r"_subgroup_\d+$")


def starting_target(row: dict) -> int:
    construction = str(row.get("construction_overgroup", ""))
    match = DESCENT_RE.search(construction)
    if match:
        return int(match.group(1))
    parameters = row.get("parameters") or {}
    return int(parameters.get("target_t", row["target_t"]))


def source_key(row: dict) -> tuple:
    parameters = row.get("parameters") or {}
    return (
        tuple(int(value) for value in row["base_coefficients"]),
        int(parameters.get("base_t", 0)),
        starting_target(row),
        int(row.get("expected_norm_squareclass", row.get("norm_squareclass", 1))),
        int(parameters.get("base_root_count", 12)),
        SUBGROUP_SUFFIX_RE.sub("", str(row.get("recipe_family", "score700_recovery"))),
    )


def height(row: dict) -> tuple[int, int]:
    numerators = []
    for value in row["h_coefficients"]:
        text = str(value)
        numerator, _, denominator = text.partition("/")
        numerators.extend((abs(int(numerator)), int(denominator or 1)))
    coefficient_height = max(
        abs(int(value)) for value in str(row["coefficients"]).split(",")
    )
    return max(numerators, default=0), coefficient_height


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT.parent / "routeA" / "data" / "score700_campaign.jsonl",
    )
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument(
        "--plan",
        type=Path,
        default=ROOT / "data" / "current_score700_signature_recovery_plan_20260813.jsonl",
    )
    parser.add_argument(
        "--targets",
        type=Path,
        default=ROOT / "data" / "current_score700_signature_recovery_targets_20260813.json",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "data" / "current_score700_signature_recovery_plan_20260813_summary.json",
    )
    parser.add_argument("--max-team-count", type=int, default=3)
    parser.add_argument("--sources-per-pair", type=int, default=1)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        live = {
            (int(str(label)[3:]), int(root)): {
                "label": str(label),
                "r": int(root),
                "teamCount": int(team_count),
                "minimumDiscAbs": str(minimum) if minimum is not None else None,
                "generatedAt": str(generated) if generated is not None else None,
            }
            for label, root, team_count, minimum, generated in connection.execute(
                "SELECT label,r,team_count,minimum_disc_abs,generated_at FROM targets "
                "WHERE team_count<=?",
                (args.max_team_count,),
            )
        }
        owned = {
            (int(str(label)[3:]), int(root))
            for label, root in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                # Pending discriminant scoring is still an accepted, already-used
                # pair.  Exclude it here so outage-time compute is reserved for
                # genuinely untouched targets instead of duplicating backlog work.
                "WHERE status='accepted'"
            )
        }
        baseline = {
            (int(str(label)[3:]), int(root))
            for label, root in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        server_pairs = {
            str(coefficient_hash): (int(str(label)[3:]), int(root))
            for coefficient_hash, label, root in connection.execute(
                "SELECT p.coefficient_hash,v.label,v.r "
                "FROM polynomials AS p JOIN verifications AS v "
                "USING(submission_id,polynomial_index) "
                "WHERE v.status='accepted' AND v.label LIKE '24T%'"
            )
        }
    finally:
        connection.close()

    representatives: dict[tuple, dict] = {}
    # GAP's local degree-24 T-number ordering is not the server ordering for
    # every proper subgroup.  Learn the exact per-family translation only
    # from previously accepted archive rows; ambiguous translations are
    # rejected rather than guessed.
    terminal_calibration_counts: dict[tuple, dict[int, dict[int, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    archive_rows = 0
    for line_number, line in enumerate(args.archive.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        archive_rows += 1
        if not row.get("submission_ready") or not row.get("exact_compatibility_proven"):
            continue
        key = source_key(row)
        candidate_hash = str(row.get("candidate_hash") or "")
        server_pair = server_pairs.get(candidate_hash)
        if server_pair is not None:
            terminal_calibration_counts[key][int(row["target_t"])][int(server_pair[0])] += 1
        current = representatives.get(key)
        if current is None or height(row) < height(current):
            representatives[key] = {**row, "archiveLine": line_number}

    opportunities: dict[tuple, list[dict]] = {}
    calibrations: dict[tuple, dict[int, int]] = {}
    pair_to_sources: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for key, row in representatives.items():
        calibration = {}
        for offline_terminal, observed in terminal_calibration_counts[key].items():
            if len(observed) == 1:
                calibration[int(offline_terminal)] = int(next(iter(observed)))
        if not calibration:
            continue
        calibrations[key] = calibration
        norm = int(key[3])
        parity = 2 if norm < 0 else 0
        maximum_root = 2 * int(key[4])
        expected = []
        for offline_terminal, server_terminal in sorted(calibration.items()):
            for root in range(parity, maximum_root + 1, 4):
                pair = server_terminal, root
                target = live.get(pair)
                if target is None or pair in owned or pair in baseline:
                    continue
                expected.append(
                    {
                        **target,
                        "offlineTerminalT": offline_terminal,
                        "t": server_terminal,
                    }
                )
                pair_to_sources[pair].append(key)
        if expected:
            opportunities[key] = expected

    # Greedily retain the strongest source for each expected pair, while a
    # selected source may cover several signatures in one unit enumeration.
    retained: set[tuple] = set()
    pair_uses: dict[tuple[int, int], int] = defaultdict(int)
    ranked_sources = sorted(
        opportunities,
        key=lambda key: (
            -sum(2.0 ** (-int(row["teamCount"])) for row in opportunities[key]),
            height(representatives[key]),
            key[1:],
        ),
    )
    for key in ranked_sources:
        useful = [
            row for row in opportunities[key]
            if pair_uses[(int(row["t"]), int(row["r"]))] < args.sources_per_pair
        ]
        if not useful:
            continue
        retained.add(key)
        for row in useful:
            pair_uses[(int(row["t"]), int(row["r"]))] += 1

    plan_rows = []
    covered_pairs: dict[tuple[int, int], int] = {}
    for key in retained:
        row = representatives[key]
        expected = [
            target for target in opportunities[key]
            if pair_uses[(int(target["t"]), int(target["r"]))] > 0
        ]
        roots = sorted({int(target["r"]) for target in expected})
        digest = hashlib.sha256(
            json.dumps(
                {
                    "base": row["base_coefficients"],
                    "seed": row["h_coefficients"],
                    "starting": key[2],
                    "roots": roots,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        for target in expected:
            pair = int(target["t"]), int(target["r"])
            covered_pairs[pair] = int(target["teamCount"])
        plan_rows.append(
            {
                "baseCoefficients": [int(value) for value in row["base_coefficients"]],
                "baseRootCount": int(key[4]),
                "baseT": int(key[1]),
                "expectedPairs": sorted(
                    expected,
                    key=lambda target: (int(target["teamCount"]), int(target["t"]), int(target["r"])),
                ),
                "jobId": digest[:20],
                "normSquareclass": int(key[3]),
                "requestedRoots": roots,
                "seedCoefficients": [str(value) for value in row["h_coefficients"]],
                "sourceArchiveLine": int(row["archiveLine"]),
                "sourceFamily": str(key[5]),
                "startingTargetT": int(key[2]),
                "terminalCalibration": {
                    str(offline): int(server)
                    for offline, server in sorted(calibrations[key].items())
                },
                "valueCeiling": sum(2.0 ** (-int(target["teamCount"])) for target in expected),
            }
        )
    plan_rows.sort(key=lambda row: (-float(row["valueCeiling"]), row["jobId"]))
    for index, row in enumerate(plan_rows):
        row["jobIndex"] = index

    args.plan.parent.mkdir(parents=True, exist_ok=True)
    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in plan_rows)
    args.plan.write_text(rendered, encoding="utf-8")
    target_payload = {
        f"{t}:{root}": target
        for (t, root), target in sorted(live.items())
        if (t, root) not in owned and (t, root) not in baseline
    }
    args.targets.write_text(json.dumps(target_payload, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "archiveRows": archive_rows,
        "distinctArithmeticSources": len(representatives),
        "serverMappedArchiveRows": sum(
            sum(sum(actual.values()) for actual in predicted.values())
            for predicted in terminal_calibration_counts.values()
        ),
        "eligibleArithmeticSources": len(opportunities),
        "jobCount": len(plan_rows),
        "coveredPairCount": len(covered_pairs),
        "coveredPairTeamCounts": {
            str(team_count): sum(1 for value in covered_pairs.values() if value == team_count)
            for team_count in range(args.max_team_count + 1)
        },
        "valueCeiling": sum(2.0 ** (-team_count) for team_count in covered_pairs.values()),
        "allLiveTargetCount": len(target_payload),
        "plan": str(args.plan.resolve()),
        "planSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "targets": str(args.targets.resolve()),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

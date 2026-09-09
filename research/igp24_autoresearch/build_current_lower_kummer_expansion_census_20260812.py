#!/usr/bin/env python3
"""Build current representative inputs for previously uncensused Kummer labels."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
PRIOR_ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
GOLD = DATA / "current_lower_kummer_expansion_gold_20260812.jsonl"
SHARDS = 8


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def render(path: Path, rows: list[dict]) -> str:
    text = "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows)
    path.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def main() -> int:
    shard_paths = [DATA / f"current_lower_kummer_expansion_input_shard{i}_of{SHARDS}_20260812.jsonl" for i in range(SHARDS)]
    for path in [GOLD, *shard_paths]:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
    prior_labels = {str(row["sourceLabel"]) for row in load(PRIOR_ACTIONS)}
    exact_profiles = {}
    action_by_label = {}
    for action in load(ACTION_MAP):
        label = str(action["sourceLabel"])
        source_order = int(action["sourceOrder"])
        profiles = {}
        for system in action["systems"]:
            block_order = int(system["blockActionOrder"])
            target_order = int(system["targetOrder"])
            if source_order % block_order or target_order % block_order:
                continue
            source_kernel = source_order // block_order
            target_kernel = target_order // block_order
            if not power_of_two(source_kernel) or not power_of_two(target_kernel):
                continue
            key = (
                int(system["blockActionT12"]),
                source_kernel,
                bool(system["flipInSource"]),
                str(system["targetLabel"]),
                target_kernel,
            )
            profiles[key] = system
        if len(profiles) != 1:
            continue
        key, _system = next(iter(profiles.items()))
        exact_profiles[label] = {
            "blockActionOrder": int(_system["blockActionOrder"]),
            "flipInSource": key[2],
            "quotientT12": key[0],
            "sourceKernelOrder": key[1],
            "sourceKummerRank": key[1].bit_length() - 1,
            "targetKernelOrder": key[4],
            "targetKummerRank": key[4].bit_length() - 1,
            "targetLabel": key[3],
        }
        action_by_label[label] = action

    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    gold_rows = [
        {
            "discovered": False,
            "label": str(row["label"]),
            "minimumDiscAbs": None,
            "r": int(row["r"]),
            "teamCount": 0,
        }
        for row in connection.execute(
            """
            SELECT t.label,t.r FROM targets t LEFT JOIN baseline_pairs b USING(label,r)
            WHERE t.team_count=0 AND t.discovered=0 AND b.label IS NULL
              AND NOT EXISTS(
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            ORDER BY t.t,t.r
            """
        )
    ]
    candidates = {}
    for row in connection.execute(
        """
        SELECT v.submission_id,v.polynomial_index,v.label,v.t,v.r,v.field_disc_abs,
               p.coefficients,p.coefficient_hash,length(p.coefficients) AS coefficient_bytes
        FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
        ORDER BY coefficient_bytes,p.coefficient_hash
        """
    ):
        label = str(row["label"])
        if label in prior_labels or label not in exact_profiles or label in candidates:
            continue
        coefficients = str(row["coefficients"]).split(",")
        if (
            len(coefficients) != 25
            or coefficients[-1] != "1"
            or any(int(coefficients[index]) for index in range(1, 24, 2))
        ):
            continue
        quotient_line = ",".join(coefficients[::2])
        candidates[label] = {
            "candidateBlockProfiles": [exact_profiles[label]],
            "candidateKernelOrders": [exact_profiles[label]["sourceKernelOrder"]],
            "candidateQuotientTs": [exact_profiles[label]["quotientT12"]],
            "exactStructuralProfile": exact_profiles[label],
            "quotientLine": quotient_line,
            "quotientPolynomialSha256": hashlib.sha256(quotient_line.encode()).hexdigest(),
            "source": {
                "coefficientSha256": str(row["coefficient_hash"]),
                "fieldDiscAbs": str(row["field_disc_abs"]) if row["field_disc_abs"] else None,
                "label": label,
                "polynomialIndex": int(row["polynomial_index"]),
                "r": int(row["r"]),
                "submissionId": str(row["submission_id"]),
                "t": int(row["t"]),
            },
            "status": "unique_exact_block_profile",
        }
    connection.close()
    ordered = [candidates[label] for label in sorted(candidates, key=lambda value: int(value[3:]))]
    shards = [[] for _ in range(SHARDS)]
    for index, row in enumerate(ordered):
        shards[index % SHARDS].append(row)
    hashes = {str(GOLD.relative_to(ROOT)): render(GOLD, gold_rows)}
    for path, rows in zip(shard_paths, shards):
        hashes[str(path.relative_to(ROOT))] = render(path, rows)
    summary = {
        "actionMapSha256": hashlib.sha256(ACTION_MAP.read_bytes()).hexdigest(),
        "currentGoldPairs": len(gold_rows),
        "newSourceLabels": len(ordered),
        "priorCensusSourceLabels": len(prior_labels),
        "shardCounts": [len(rows) for rows in shards],
        "sha256": hashes,
    }
    summary_path = DATA / "current_lower_kummer_expansion_input_summary_20260812.json"
    if summary_path.exists():
        raise FileExistsError(f"refusing to overwrite {summary_path}")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Cross-validate weighted Good-Turing silver scheduling parameters."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path

from build_empirical_value_silver_portfolio_20260813 import scan_candidates
from build_global_certified_novelty_portfolio_20260813 import height


ROOT = Path(__file__).resolve().parent


def fold_for(candidate_hash: str, folds: int) -> int:
    return int(candidate_hash[:16], 16) % folds


def rates(history, shrinkage: float):
    local_counts = defaultdict(Counter)
    family_counts = defaultdict(Counter)
    root_counts = defaultdict(Counter)
    pair_teams = {}
    for row, pair, teams in history:
        key = (row["source"], row["coarseFamily"], row["root"])
        local_counts[key][pair] += 1
        family_counts[(row["coarseFamily"], row["root"])][pair] += 1
        root_counts[row["root"]][pair] += 1
        pair_teams[pair] = teams

    def weighted_singleton_rate(counts: Counter):
        n = sum(counts.values())
        value = sum(math.ldexp(1.0, -pair_teams[pair]) for pair, count in counts.items() if count == 1)
        return value / max(1, n), n

    root_rate = {key: weighted_singleton_rate(counts) for key, counts in root_counts.items()}
    family_rate = {}
    for key, counts in family_counts.items():
        raw, n = weighted_singleton_rate(counts)
        parent = root_rate.get(key[1], (0.0, 0))[0]
        family_rate[key] = (raw * n + shrinkage * parent) / (n + shrinkage)
    local_rate = {}
    for key, counts in local_counts.items():
        raw, n = weighted_singleton_rate(counts)
        parent = family_rate.get((key[1], key[2]), 0.0)
        local_rate[key] = (raw * n + shrinkage * parent) / (n + shrinkage)
    default = sum(value[0] for value in root_rate.values()) / max(1, len(root_rate))
    return local_rate, default


def schedule(validation, local_rate, default: float, decay: float):
    queues = defaultdict(list)
    for row, pair, teams in validation:
        key = (row["source"], row["coarseFamily"], row["root"])
        queues[key].append((row, pair, teams))
    for queue in queues.values():
        queue.sort(key=lambda item: (height(item[0]["coefficients"]), item[0]["candidateHash"]))
    frontier = []
    for serial, (key, queue) in enumerate(sorted(queues.items())):
        score = local_rate.get(key, default)
        heapq.heappush(frontier, (-score, serial, 0, key, deque(queue), score))
    while frontier:
        _negative, serial, draw, key, queue, initial = heapq.heappop(frontier)
        yield queue.popleft()
        if queue:
            next_draw = draw + 1
            heapq.heappush(frontier, (-(initial * decay**next_draw), serial, next_draw, key, queue, initial))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--caps", default="1000,5000,10000,20000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    caps = [int(value) for value in args.caps.split(",")]
    candidates, _skips, _files = scan_candidates()
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    labeled = []
    hashes = list(candidates)
    for offset in range(0, len(hashes), 700):
        batch = hashes[offset : offset + 700]
        marks = ",".join("?" for _ in batch)
        for hit in connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r,v.status,t.team_count "
            "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
            "LEFT JOIN targets t ON t.label=v.label AND t.r=v.r "
            f"WHERE p.coefficient_hash IN ({marks})",
            batch,
        ):
            if hit["status"] != "accepted" or hit["label"] is None or hit["team_count"] is None:
                continue
            candidate_hash = str(hit["coefficient_hash"])
            labeled.append((
                candidates[candidate_hash],
                (str(hit["label"]), int(hit["r"])),
                int(hit["team_count"]),
            ))
    connection.close()

    shrinkages = (2.0, 5.0, 8.0, 15.0, 30.0)
    decays = (0.95, 0.97, 0.985, 0.995, 0.999)
    results = []
    for shrinkage in shrinkages:
        for decay in decays:
            aggregate = {cap: 0.0 for cap in caps}
            aggregate_pairs = {cap: 0 for cap in caps}
            fold_rows = []
            for fold in range(args.folds):
                train = [item for item in labeled if fold_for(item[0]["candidateHash"], args.folds) != fold]
                validation = [item for item in labeled if fold_for(item[0]["candidateHash"], args.folds) == fold]
                owned = {item[1] for item in train}
                local_rate, default = rates(train, shrinkage)
                value = 0.0
                new_pairs = set()
                snapshots = {}
                for ordinal, (_row, pair, teams) in enumerate(schedule(validation, local_rate, default, decay), 1):
                    if pair not in owned and pair not in new_pairs:
                        new_pairs.add(pair)
                        value += math.ldexp(1.0, -teams)
                    if ordinal in caps:
                        snapshots[ordinal] = {"value": value, "pairs": len(new_pairs)}
                    if ordinal >= max(caps):
                        break
                for cap in caps:
                    snapshot = snapshots.get(cap, {"value": value, "pairs": len(new_pairs)})
                    aggregate[cap] += snapshot["value"]
                    aggregate_pairs[cap] += snapshot["pairs"]
                fold_rows.append({"fold": fold, "train": len(train), "validation": len(validation), "snapshots": snapshots})
            results.append({
                "shrinkage": shrinkage,
                "decay": decay,
                "meanValue": {str(cap): aggregate[cap] / args.folds for cap in caps},
                "meanPairs": {str(cap): aggregate_pairs[cap] / args.folds for cap in caps},
                "folds": fold_rows,
            })
    results.sort(key=lambda item: tuple(-item["meanValue"][str(cap)] for cap in caps))
    payload = {
        "schemaVersion": "weighted-good-turing-silver-backtest-v1",
        "labeledRows": len(labeled),
        "foldCount": args.folds,
        "caps": caps,
        "ranking": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"labeledRows": len(labeled), "top": [{k: v for k, v in row.items() if k != "folds"} for row in results[:10]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reorder staged uncertain silver by smoothed weighted unseen-pair mass."""

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
from build_global_certified_novelty_portfolio_20260813 import canonical, certified, digest, height, root_count


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--shrinkage", type=float, default=8.0)
    parser.add_argument("--decay", type=float, default=0.985)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite Good-Turing silver schedule")

    candidates, _skips, _files = scan_candidates()
    staged = []
    staged_hashes = set()
    for path in args.input:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            candidate_hash = digest(",".join(str(int(item)) for item in line.split(",")))
            if candidate_hash in staged_hashes:
                raise ValueError(f"duplicate staged hash {candidate_hash}")
            staged_hashes.add(candidate_hash)
            row = candidates.get(candidate_hash)
            if row is None:
                # Global novelty can include exact-action rows that the
                # uncertain census deliberately filters.  Keep them in an
                # explicit fallback stratum; they will be target-audited by
                # the exact-action census separately.
                row = {
                    "candidateHash": candidate_hash,
                    "coefficients": line,
                    "family": "filtered-exact-or-global-candidate",
                    "coarseFamily": "filtered-exact-or-global-candidate",
                    "root": -1,
                    "source": "filtered-exact-or-global-candidate",
                    "predictedT": 0,
                }
            staged.append(row)

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    outcomes = {}
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
            if hit["status"] == "accepted" and hit["label"] is not None and hit["team_count"] is not None:
                outcomes[str(hit["coefficient_hash"])] = (
                    str(hit["label"]), int(hit["r"]), int(hit["team_count"])
                )
    connection.close()

    local_counts = defaultdict(Counter)
    family_counts = defaultdict(Counter)
    root_counts = defaultdict(Counter)
    pair_teams = {}
    for candidate_hash, outcome in outcomes.items():
        row = candidates[candidate_hash]
        pair = (outcome[0], outcome[1])
        pair_teams[pair] = outcome[2]
        local_counts[(row["source"], row["coarseFamily"], row["root"])][pair] += 1
        family_counts[(row["coarseFamily"], row["root"])][pair] += 1
        root_counts[row["root"]][pair] += 1

    def weighted_singleton_rate(counts: Counter) -> tuple[float, int, int]:
        n = sum(counts.values())
        singleton_value = 0.0
        singletons = 0
        for (label, root), count in counts.items():
            if count != 1:
                continue
            # Targets were already joined above; retrieve the minimum observed
            # contention for this pair from the outcome census.
            teams = pair_teams[(label, root)]
            singleton_value += math.ldexp(1.0, -teams)
            singletons += 1
        return singleton_value / max(1, n), n, singletons

    root_rates = {key: weighted_singleton_rate(value) for key, value in root_counts.items()}
    family_rates = {}
    for key, counts in family_counts.items():
        raw, n, singletons = weighted_singleton_rate(counts)
        parent, parent_n, _ = root_rates.get(key[1], (0.0, 0, 0))
        smoothed = (raw * n + args.shrinkage * parent) / (n + args.shrinkage)
        family_rates[key] = (smoothed, n, singletons, raw)

    local_rates = {}
    for key, counts in local_counts.items():
        raw, n, singletons = weighted_singleton_rate(counts)
        parent, _pn, _ps, _pr = family_rates.get((key[1], key[2]), (0.0, 0, 0, 0.0))
        smoothed = (raw * n + args.shrinkage * parent) / (n + args.shrinkage)
        local_rates[key] = (smoothed, n, singletons, raw)

    queues = defaultdict(list)
    for row in staged:
        key = (row["source"], row["coarseFamily"], row["root"])
        queues[key].append(row)
    for queue in queues.values():
        queue.sort(key=lambda row: (height(row["coefficients"]), row["candidateHash"]))

    frontier = []
    default_rate = sum(item[0] for item in root_rates.values()) / max(1, len(root_rates))
    for serial, (key, queue) in enumerate(sorted(queues.items())):
        score, observed, singletons, raw = local_rates.get(key, (default_rate, 0, 0, 0.0))
        heapq.heappush(frontier, (-score, serial, 0, key, deque(queue), observed, singletons, raw))

    selected = []
    selected_counts = Counter()
    top_scores = {}
    while frontier:
        negative, serial, draw, key, queue, observed, singletons, raw = heapq.heappop(frontier)
        row = queue.popleft()
        selected.append(row)
        selected_counts[key] += 1
        top_scores.setdefault(key, -negative)
        if queue:
            next_draw = draw + 1
            next_priority = top_scores[key] * (args.decay ** next_draw)
            heapq.heappush(
                frontier,
                (-next_priority, serial, next_draw, key, queue, observed, singletons, raw),
            )

    rendered = "".join(row["coefficients"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="ascii")
    top = []
    for key, score in sorted(top_scores.items(), key=lambda item: -item[1])[:100]:
        smoothed, observed, singletons, raw = local_rates.get(key, (default_rate, 0, 0, 0.0))
        top.append({
            "source": key[0],
            "family": key[1],
            "root": key[2],
            "weightedGoodTuringRate": score,
            "rawWeightedSingletonRate": raw,
            "observed": observed,
            "singletons": singletons,
            "eligible": len(queues[key]),
            "selected": selected_counts[key],
        })
    summary = {
        "schemaVersion": "weighted-good-turing-silver-order-v1",
        "method": "hierarchically smoothed weighted singleton mass with within-stratum decay",
        "inputs": [str(path.resolve()) for path in args.input],
        "rows": len(selected),
        "uniqueHashes": len({row["candidateHash"] for row in selected}),
        "labeledHistory": len(outcomes),
        "eligibleStrata": len(queues),
        "shrinkage": args.shrinkage,
        "decay": args.decay,
        "output": str(args.output.resolve()),
        "outputSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "topStrata": top,
        "checks": {
            "allInputRowsPreserved": len(selected) == len(staged),
            "allRowsUnique": len(selected) == len({row["candidateHash"] for row in selected}),
        },
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "topStrata"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build receipt-safe silver waves from empirically valuable archive strata.

The global novelty builder deliberately explores uniformly.  This builder uses
the server labels of already tested siblings to estimate how much *distinct
pair value* each source/construction/signature stratum has historically
produced, then spends a wave on the best posterior estimates while retaining
caps and round-robin diversity.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import re
import sqlite3
import heapq
from collections import Counter, defaultdict, deque
from pathlib import Path

from build_global_certified_novelty_portfolio_20260813 import (
    canonical,
    certified,
    digest,
    height,
    manifest_hashes,
    receipt_hashes,
    root_count,
)


ROOT = Path(__file__).resolve().parent


def coarse_family(value: str) -> str:
    """Remove per-candidate knobs while retaining construction architecture."""
    value = value.lower().strip()
    value = re.sub(r"_m-?\d+", "_m*", value)
    value = re.sub(r"_s-?\d+", "_s*", value)
    value = re.sub(r"_c-?\d+", "_c*", value)
    value = re.sub(r"_w-?\d+", "_w*", value)
    value = re.sub(r"(?:seed|mask|shift|variant)[_:=.-]*-?\d+", r"\g<0>".split("-")[0] + "*", value)
    return value


def scan_candidates() -> tuple[dict[str, dict], Counter, int]:
    files = sorted(
        set(glob.glob(str(ROOT / "data" / "**" / "*.jsonl"), recursive=True))
        | set(glob.glob(str(ROOT.parent / "routeA" / "data" / "**" / "*.jsonl"), recursive=True))
    )
    candidates: dict[str, dict] = {}
    skips = Counter()
    for raw_path in files:
        path = Path(raw_path)
        if not path.is_file():
            skips["jsonl_named_directory"] += 1
            continue
        lower = path.name.lower()
        if any(token in lower for token in ("lmfdb", "baseline", "degree24_exact")):
            skips["excluded_catalog_or_baseline_source"] += 1
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    skips["invalid_json"] += 1
                    continue
                if not isinstance(row, dict) or not certified(row):
                    continue
                # Exact-action rows are handled by the exact staging census.
                # A fresh defining polynomial for an already owned exact pair
                # has zero leaderboard value, and the current exact census is
                # exhausted after receipt/pair exclusions.  This portfolio is
                # intentionally reserved for genuinely label-uncertain fields.
                if (
                    row.get("exact_compatibility_proven") is True
                    or row.get("submission_ready") is True
                    or float(row.get("label_probability", 0.0) or 0.0) >= 1.0
                ):
                    skips["exact_action_routed_to_exact_census"] += 1
                    continue
                line = canonical(row)
                if line is None:
                    skips["not_monic_degree24"] += 1
                    continue
                candidate_hash = digest(line)
                claimed_hash = row.get("candidate_hash") or row.get("coefficientSha256")
                if claimed_hash and str(claimed_hash) != candidate_hash:
                    skips["claimed_hash_mismatch"] += 1
                    continue
                source = str(path.relative_to(ROOT.parent))
                family = str(
                    row.get("recipe_family")
                    or row.get("construction_overgroup")
                    or row.get("construction")
                    or path.stem
                )
                predicted_t = 0
                try:
                    predicted_t = int(row.get("target_t", 0) or 0)
                except (TypeError, ValueError):
                    pass
                normalized = {
                    "candidateHash": candidate_hash,
                    "coefficients": line,
                    "family": family,
                    "coarseFamily": coarse_family(family),
                    "root": root_count(row),
                    "source": source,
                    "predictedT": predicted_t,
                }
                incumbent = candidates.get(candidate_hash)
                if incumbent is None or (source, family) < (incumbent["source"], incumbent["family"]):
                    candidates[candidate_hash] = normalized
    return candidates, skips, len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--wave", type=int, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument(
        "--exclude-manifest",
        action="append",
        type=Path,
        default=[],
        help="also exclude hashes in an unsubmitted staged manifest (repeatable)",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--min-observed", type=int, default=3)
    parser.add_argument("--cap-per-stratum", type=int, default=20)
    parser.add_argument("--cap-per-source", type=int, default=100)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite empirical-value portfolio")

    candidates, skips, files_scanned = scan_candidates()
    excluded = receipt_hashes(args.receipts)
    staged_excluded = manifest_hashes(args.exclude_manifest)
    excluded.update(staged_excluded)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    outcomes: dict[str, tuple[str, int, int]] = {}
    hashes = list(candidates)
    for offset in range(0, len(hashes), 700):
        batch = hashes[offset : offset + 700]
        marks = ",".join("?" for _ in batch)
        for hit in connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r,t.team_count "
            "FROM polynomials p "
            "LEFT JOIN verifications v USING(submission_id,polynomial_index) "
            "LEFT JOIN targets t ON t.label=v.label AND t.r=v.r "
            f"WHERE p.coefficient_hash IN ({marks})",
            batch,
        ):
            candidate_hash = str(hit["coefficient_hash"])
            excluded.add(candidate_hash)
            if hit["label"] is not None and hit["team_count"] is not None:
                outcomes[candidate_hash] = (
                    str(hit["label"]), int(hit["r"]), int(hit["team_count"])
                )
    connection.close()

    # Use both file-level and construction-level evidence.  The former captures
    # tightly curated campaigns; the latter lets newer sibling files inherit
    # evidence from the same architecture.
    file_history: dict[tuple, list[tuple[str, int, int]]] = defaultdict(list)
    family_history: dict[tuple, list[tuple[str, int, int]]] = defaultdict(list)
    for candidate_hash, outcome in outcomes.items():
        row = candidates[candidate_hash]
        file_history[(row["source"], row["coarseFamily"], row["root"])].append(outcome)
        family_history[(row["coarseFamily"], row["root"])].append(outcome)

    eligible: dict[tuple, list[dict]] = defaultdict(list)
    for candidate_hash, row in candidates.items():
        if candidate_hash in excluded:
            continue
        eligible[(row["source"], row["coarseFamily"], row["root"])].append(row)

    global_values = [math.ldexp(1.0, -outcome[2]) for outcome in outcomes.values()]
    global_prior = sum(global_values) / max(1, len(global_values))

    def evidence_value(history: list[tuple[str, int, int]]) -> tuple[float, int, int, int, float]:
        # Historical score is counted once per distinct target pair.  This is
        # the relevant unit for the leaderboard, unlike polynomial acceptance.
        pair_best: dict[tuple[str, int], int] = {}
        for label, root, teams in history:
            pair_best[(label, root)] = teams
        distinct_value = sum(math.ldexp(1.0, -teams) for teams in pair_best.values())
        n = len(history)
        distinct = len(pair_best)
        scarce = sum(teams <= 3 for teams in pair_best.values())
        yield_per_field = distinct_value / max(1, n)
        return yield_per_field, n, distinct, scarce, distinct_value

    ranked = []
    for key, rows in eligible.items():
        source, family, root = key
        local = file_history.get(key, [])
        inherited = family_history.get((family, root), [])
        local_yield, local_n, local_distinct, local_scarce, local_value = evidence_value(local)
        family_yield, family_n, family_distinct, family_scarce, family_value = evidence_value(inherited)
        # Shrink sparse file evidence toward architecture evidence, then toward
        # the corpus prior.  A discovery multiplier favors strata whose tested
        # siblings reached many different labels rather than one repeated pair.
        inherited_mean = (family_value + 8.0 * global_prior) / (family_n + 8.0)
        posterior = (local_value + 5.0 * inherited_mean) / (local_n + 5.0)
        discovery = (local_distinct + 2.0) / (local_n + 3.0)
        scarce_bonus = 1.0 + min(1.0, local_scarce / max(1.0, local_distinct))
        confidence = min(1.0, (local_n + 0.25 * family_n) / max(1, args.min_observed))
        score = posterior * discovery * scarce_bonus * (0.35 + 0.65 * confidence)
        rows.sort(key=lambda row: (height(row["coefficients"]), row["candidateHash"]))
        ranked.append({
            "key": key,
            "score": score,
            "localObserved": local_n,
            "localDistinctPairs": local_distinct,
            "localScarcePairs": local_scarce,
            "localHistoricalValue": local_value,
            "familyObserved": family_n,
            "familyDistinctPairs": family_distinct,
            "familyScarcePairs": family_scarce,
            "eligible": len(rows),
            "queue": deque(rows),
        })
    ranked.sort(key=lambda item: (-item["score"], -item["localObserved"], item["key"]))

    # Best-first selection deliberately spends more than one row on a proven
    # chamber.  The gentle within-chamber decay prevents a single giant archive
    # from monopolizing the batch but is much more exploitative than uniform
    # round robin.  Receipts make successive waves intrinsically non-overlapping.
    frontier = []
    for serial, item in enumerate(ranked):
        if item["queue"]:
            heapq.heappush(frontier, (-item["score"], serial, 0, item))
    selected = []
    source_counts = Counter()
    stratum_counts = Counter()
    root_counts = Counter()
    deferred = 0
    while frontier and len(selected) < args.size:
        _negative_priority, serial, draw_index, item = heapq.heappop(frontier)
        source, family, root = item["key"]
        if source_counts[source] >= args.cap_per_source or stratum_counts[item["key"]] >= args.cap_per_stratum:
            deferred += len(item["queue"])
            continue
        row = item["queue"].popleft()
        selected.append(row | {"empiricalScore": item["score"]})
        source_counts[source] += 1
        stratum_counts[item["key"]] += 1
        root_counts[root] += 1
        if item["queue"] and stratum_counts[item["key"]] < args.cap_per_stratum:
            next_draw = draw_index + 1
            priority = item["score"] * (0.88 ** next_draw)
            heapq.heappush(frontier, (-priority, serial, next_draw, item))
    if len(selected) != args.size:
        raise ValueError(f"only {len(selected)} empirically ranked candidates fit the caps")

    manifest = "".join(row["coefficients"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    selected_keys = set(stratum_counts)
    top_strata = [
        {
            "source": item["key"][0],
            "family": item["key"][1],
            "root": item["key"][2],
            "score": item["score"],
            "localObserved": item["localObserved"],
            "localDistinctPairs": item["localDistinctPairs"],
            "localScarcePairs": item["localScarcePairs"],
            "localHistoricalValue": item["localHistoricalValue"],
            "familyObserved": item["familyObserved"],
            "eligible": item["eligible"],
            "selected": stratum_counts[item["key"]],
        }
        for item in ranked
        if item["key"] in selected_keys
    ][:100]
    summary = {
        "purpose": "empirical distinct-pair-value weighted silver exploration",
        "wave": args.wave,
        "filesScanned": files_scanned,
        "candidateUniverse": len(candidates),
        "historicallyLabeledCandidates": len(outcomes),
        "excludedKnownOrReceipted": len(set(candidates) & excluded),
        "excludedByStagedManifests": len(set(candidates) & staged_excluded),
        "eligibleStrata": len(eligible),
        "globalMarginalPrior": global_prior,
        "selectedRows": len(selected),
        "selectedStrata": len(stratum_counts),
        "selectedSources": len(source_counts),
        "deferredByCaps": deferred,
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "rootDistribution": dict(sorted(root_counts.items())),
        "sourceDistribution": dict(sorted(source_counts.items())),
        "topSelectedStrata": top_strata,
        "skipCounts": dict(sorted(skips.items())),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key not in {"sourceDistribution", "topSelectedStrata"}}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

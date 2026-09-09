#!/usr/bin/env python3
"""Prepare complete-preserving exact-isomorphism candidates for F6."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fingerprint-shards", type=Path, nargs="+", required=True)
    parser.add_argument("--exclusions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    rows = []
    for path in args.fingerprint_shards:
        rows.extend(read_jsonl(path))
    labels = [str(row["label"]) for row in rows]
    if len(labels) != len(set(labels)):
        raise RuntimeError("fingerprint shards contain duplicate labels")
    if any(row.get("status") != "certified" for row in rows):
        raise RuntimeError("fingerprint census is not fully certified")

    exclusions = {
        str(row["sourceLabel"]): set(str(value) for value in row["excludedTargetLabels"])
        for row in read_jsonl(args.exclusions)
    }
    sources = sorted((row for row in rows if row["isOwnedSource"]), key=lambda row: int(row["t"]))
    targets_by_order = defaultdict(list)
    for row in rows:
        if row["isGoldTarget"]:
            targets_by_order[str(row["order"])].append(row)

    raw_order_pairs = 0
    fingerprint_rejected_pairs = 0
    excluded_identity_pairs = 0
    unavailable_profile_pairs = 0
    candidates = []
    order_candidate_counts = Counter()
    for source in sources:
        targets = targets_by_order[str(source["order"])]
        raw_order_pairs += len(targets)
        excluded = exclusions.get(str(source["label"]), set())
        for target in targets:
            if str(target["label"]) in excluded:
                excluded_identity_pairs += 1
                continue
            source_profile = source.get("conjugacyProfileSha256")
            target_profile = target.get("conjugacyProfileSha256")
            if source_profile is not None and target_profile is not None:
                if source["fingerprintSha256"] != target["fingerprintSha256"]:
                    fingerprint_rejected_pairs += 1
                    continue
            else:
                unavailable_profile_pairs += 1
            candidates.append(
                {
                    "fingerprintSha256": source["fingerprintSha256"],
                    "goldR": sorted(int(value) for value in target["goldR"]),
                    "order": str(source["order"]),
                    "sourceLabel": str(source["label"]),
                    "sourceR": sorted(int(value) for value in source["sourceR"]),
                    "sourceT": int(source["t"]),
                    "targetLabel": str(target["label"]),
                    "targetT": int(target["t"]),
                }
            )
            order_candidate_counts[str(source["order"])] += 1

    candidates.sort(key=lambda row: (int(row["sourceT"]), int(row["targetT"])))
    write_jsonl(args.output, candidates)
    summary = {
        "candidatePairsForExactIsomorphism": len(candidates),
        "excludedIdentityPairs": excluded_identity_pairs,
        "fingerprintRejectedPairs": fingerprint_rejected_pairs,
        "fingerprintShardArtifacts": [
            {"path": str(path.resolve()), "sha256": sha256(path)}
            for path in args.fingerprint_shards
        ],
        "largestCandidateOrders": [
            {"candidatePairs": count, "order": order}
            for order, count in order_candidate_counts.most_common(20)
        ],
        "ownedSources": len(sources),
        "rawSameOrderPairs": raw_order_pairs,
        "targetLabels": sum(len(values) for values in targets_by_order.values()),
        "unavailableConjugacyProfileCandidatePairs": unavailable_profile_pairs,
    }
    temporary = args.summary.with_suffix(args.summary.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

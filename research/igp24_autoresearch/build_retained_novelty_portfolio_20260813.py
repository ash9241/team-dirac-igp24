#!/usr/bin/env python3
"""Build a deterministic, receipt-aware exploration portfolio from retained lifts.

These rows are exact irreducible degree-24 fields but their proper subgroup labels
have not been certified locally.  Selection therefore optimizes construction and
source diversity, not a claimed target label.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ROUTE_DATA = ROOT.parent / "routeA" / "data"


def canonical(value: str) -> str:
    coefficients = [int(part) for part in value.split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("not a monic degree-24 polynomial")
    return ",".join(map(str, coefficients))


def receipt_hashes(receipts: Path) -> set[str]:
    result = set()
    for path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_file():
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]:
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    result.add(hashlib.sha256(canonical(line).encode("ascii")).hexdigest())
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return result


def source_paths() -> list[Path]:
    patterns = (
        "cross1500_galoisdb_*subfield*.jsonl",
        "cross1500_12t61_subfield*.jsonl",
        "cross1500_top4_subfield*.jsonl",
    )
    return sorted({Path(path) for pattern in patterns for path in glob.glob(str(ROUTE_DATA / pattern))})


def mechanism(row: dict) -> str:
    family = str(row.get("recipe_family", "unknown"))
    if family.startswith("subfield-product") or family.startswith("subfield_product"):
        return "product"
    if "unit" in family:
        return "unit"
    if "kummer" in family:
        return "kummer"
    return family.split(":", 1)[0]


def height_key(line: str) -> tuple[int, int]:
    values = [abs(int(part)) for part in line.split(",")]
    return max(values).bit_length(), sum(value.bit_length() for value in values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--wave", type=int, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite novelty portfolio")

    rows: dict[str, dict] = {}
    appearances = Counter()
    paths = source_paths()
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if not (
                    row.get("exploration_ready") is True
                    and row.get("local_irreducible") is True
                    and float(row.get("valid_probability", 0.0)) == 1.0
                ):
                    continue
                line = canonical(str(row["coefficients"]))
                digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                if digest != str(row["candidate_hash"]):
                    raise ValueError(f"hash mismatch in {path}")
                appearances[digest] += 1
                incumbent = rows.get(digest)
                if incumbent is None or str(row.get("recipe_family", "")) < str(incumbent.get("recipe_family", "")):
                    rows[digest] = row | {"coefficients": line, "source_path": path.name}

    excluded = receipt_hashes(args.receipts)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known: dict[str, tuple[str, int]] = {}
    hashes = list(rows)
    for offset in range(0, len(hashes), 800):
        batch = hashes[offset : offset + 800]
        marks = ",".join("?" for _ in batch)
        for hit in connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r FROM polynomials p "
            "LEFT JOIN verifications v USING(submission_id,polynomial_index) "
            f"WHERE p.coefficient_hash IN ({marks})",
            batch,
        ):
            excluded.add(str(hit["coefficient_hash"]))
            if hit["label"] is not None:
                known[str(hit["coefficient_hash"])] = (str(hit["label"]), int(hit["r"]))
    connection.close()

    # Measure historical pair entropy for each coarse construction stratum.
    historical = defaultdict(Counter)
    for digest, pair in known.items():
        row = rows[digest]
        key = (int(row.get("base_t", -1)), mechanism(row), int(row.get("target_r", -1)))
        historical[key][pair] += 1

    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for digest, row in rows.items():
        if digest in excluded:
            continue
        params = row.get("parameters") or {}
        fingerprint = (
            int(row.get("base_t", -1)),
            mechanism(row),
            int(row.get("target_r", -1)),
            int(params.get("subfield_degree", 0) or 0),
            int(params.get("subfield_index", 0) or 0),
            str(row.get("source_field_label", "")),
        )
        buckets[fingerprint].append(row | {"candidate_hash": digest})

    # Start every fingerprint with its smallest-height representative.  Rotate
    # base groups and mechanisms so a large catalog cannot monopolize a wave.
    queues = defaultdict(deque)
    for fingerprint, options in buckets.items():
        options.sort(key=lambda row: (height_key(row["coefficients"]), row["candidate_hash"]))
        base_t, mech, root, *_ = fingerprint
        queues[(base_t, mech, root)].append(options[0])

    def stratum_rank(item: tuple[tuple, deque]) -> tuple:
        (base_t, mech, root), queue = item
        counts = historical[(base_t, mech, root)]
        observed = sum(counts.values())
        distinct = len(counts)
        singletons = sum(value == 1 for value in counts.values())
        discovery = (distinct + singletons + 1) / (observed + 2)
        return (-discovery, -len(queue), base_t, mech, root)

    for queue in queues.values():
        ordered = sorted(queue, key=lambda row: (str(row.get("recipe_family", "")), row["candidate_hash"]))
        queue.clear()
        queue.extend(ordered)
    active = deque(key for key, _ in sorted(queues.items(), key=stratum_rank))
    selected = []
    base_counts = Counter()
    mechanism_counts = Counter()
    root_counts = Counter()
    # Wave number changes the starting rotation while remaining reproducible.
    if active:
        active.rotate(-((args.wave - 1) * 11 % len(active)))
    while active and len(selected) < args.size:
        key = active.popleft()
        queue = queues[key]
        if not queue:
            continue
        row = queue.popleft()
        selected.append(row)
        base_counts[int(row.get("base_t", -1))] += 1
        mechanism_counts[mechanism(row)] += 1
        root_counts[int(row.get("target_r", -1))] += 1
        if queue:
            active.append(key)

    if len(selected) != args.size:
        raise ValueError(f"only {len(selected)} eligible diverse rows remain")
    manifest = "".join(row["coefficients"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "purpose": "valid irreducible exploration; no local terminal-label claim",
        "wave": args.wave,
        "sourceFiles": len(paths),
        "retainedUniqueCandidates": len(rows),
        "excludedKnownOrReceipted": len(set(rows) & excluded),
        "eligibleFingerprints": len(buckets),
        "selectedRows": len(selected),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "baseDistribution": dict(sorted(base_counts.items())),
        "mechanismDistribution": dict(sorted(mechanism_counts.items())),
        "rootDistribution": dict(sorted(root_counts.items())),
        "selected": [
            {
                "candidateHash": row["candidate_hash"],
                "baseT": row.get("base_t"),
                "mechanism": mechanism(row),
                "localRootCount": row.get("local_root_count"),
                "recipeFamily": row.get("recipe_family"),
                "sourceField": row.get("source_field_label"),
                "sourcePath": row.get("source_path"),
            }
            for row in selected
        ],
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "selected"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

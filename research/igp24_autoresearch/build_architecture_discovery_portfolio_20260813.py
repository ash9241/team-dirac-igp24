#!/usr/bin/env python3
"""Build disjoint, family-balanced portfolios from unclassified field archives."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT.parent / "routeA" / "data"


def canonical(value) -> str:
    if isinstance(value, list):
        coefficients = [int(item) for item in value]
    else:
        coefficients = [int(item) for item in str(value).split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("not a monic degree-24 polynomial")
    return ",".join(map(str, coefficients))


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def receipt_hashes(path: Path) -> set[str]:
    result = set()
    for receipt_path in path.glob("sub_*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_file() or digest(manifest.read_text(encoding="utf-8")) == "":
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]:
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    result.add(digest(canonical(line)))
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return result


def family(row: dict, source: str) -> str:
    return str(
        row.get("recipe_family")
        or row.get("construction_overgroup")
        or row.get("family")
        or source.removesuffix(".jsonl")
    )


def candidate_files() -> list[Path]:
    patterns = (
        "cross2000_empirical_*.jsonl",
        "cross2000_tower_*.jsonl",
        "cross1500_primitive_masked_tower_*.jsonl",
        "cross1500_masked_tower_*.jsonl",
        "cross1500_quartic_root_lift_*.jsonl",
        "cross1500_q12t61_focus_*.jsonl",
        "cross1500_s3_cubic_*.jsonl",
        "gq96_*.jsonl",
        "tower_*248.jsonl",
        "candidates_fiber_*.jsonl",
    )
    return sorted({Path(item) for pattern in patterns for item in glob.glob(str(ARCHIVE / pattern))})


def height(line: str) -> tuple[int, int, str]:
    values = [abs(int(item)) for item in line.split(",")]
    return max(values).bit_length(), sum(item.bit_length() for item in values), line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--wave", type=int, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--include-family",
        action="append",
        default=[],
        help="if supplied, retain only these exact normalized family names",
    )
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite architecture portfolio")

    candidates: dict[str, dict] = {}
    sources_by_hash = defaultdict(set)
    paths = candidate_files()
    skips = Counter()
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    skips["invalid_json"] += 1
                    continue
                if not isinstance(row, dict) or row.get("local_irreducible") is not True:
                    skips["not_locally_irreducible"] += 1
                    continue
                # Terminal-exact rows were separately audited against the live
                # target table.  This portfolio is strictly the unclassified lane.
                if row.get("submission_ready") is True and row.get("exact_compatibility_proven") is True:
                    skips["terminal_exact_lane"] += 1
                    continue
                target_t = row.get("target_t")
                if target_t not in (None, 0, "0"):
                    skips["claimed_nonzero_target"] += 1
                    continue
                try:
                    line = canonical(row["coefficients"])
                except (KeyError, TypeError, ValueError):
                    skips["invalid_coefficients"] += 1
                    continue
                candidate_hash = digest(line)
                if row.get("candidate_hash") and str(row["candidate_hash"]) != candidate_hash:
                    raise ValueError(f"candidate hash mismatch in {path}")
                fam = family(row, path.name)
                if args.include_family and fam not in set(args.include_family):
                    skips["outside_included_families"] += 1
                    continue
                if any(token in fam.lower() for token in ("d4", "lmfdb", "baseline")):
                    skips["excluded_collapsed_or_baseline_family"] += 1
                    continue
                normalized = row | {
                    "candidate_hash": candidate_hash,
                    "coefficients": line,
                    "family": fam,
                    "source_file": path.name,
                }
                sources_by_hash[candidate_hash].add(path.name)
                incumbent = candidates.get(candidate_hash)
                if incumbent is None or (path.name, fam) < (incumbent["source_file"], incumbent["family"]):
                    candidates[candidate_hash] = normalized

    excluded = receipt_hashes(args.receipts)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known = {}
    hashes = list(candidates)
    for offset in range(0, len(hashes), 800):
        batch = hashes[offset : offset + 800]
        marks = ",".join("?" for _ in batch)
        for hit in connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r FROM polynomials p "
            "LEFT JOIN verifications v USING(submission_id,polynomial_index) "
            f"WHERE p.coefficient_hash IN ({marks})",
            batch,
        ):
            candidate_hash = str(hit["coefficient_hash"])
            excluded.add(candidate_hash)
            if hit["label"] is not None:
                known[candidate_hash] = (str(hit["label"]), int(hit["r"]))
    connection.close()

    family_history = defaultdict(lambda: [0, set()])
    for candidate_hash, pair in known.items():
        fam = candidates[candidate_hash]["family"]
        family_history[fam][0] += 1
        family_history[fam][1].add(pair)

    fine = defaultdict(list)
    for candidate_hash, row in candidates.items():
        if candidate_hash in excluded:
            continue
        params = row.get("parameters") or {}
        base = tuple(params.get("base_coefficients") or ())
        fingerprint = (
            row["family"],
            int(row.get("local_root_count", row.get("target_r", -1))),
            str(params.get("form", "")),
            base,
            int(params.get("e", 0) or 0),
            row["source_file"],
        )
        fine[fingerprint].append(row)

    family_queues = defaultdict(list)
    for fingerprint, options in fine.items():
        options.sort(key=lambda item: (height(item["coefficients"]), item["candidate_hash"]))
        family_queues[fingerprint[0]].append(options[0])
    for fam, queue in family_queues.items():
        queue.sort(
            key=lambda item: (
                int(item.get("local_root_count", item.get("target_r", -1))),
                item["source_file"],
                height(item["coefficients"]),
                item["candidate_hash"],
            )
        )
        family_queues[fam] = deque(queue)

    def family_rank(fam: str) -> tuple:
        observed, pairs = family_history[fam]
        if observed == 0:
            novelty_prior = 0.22
        else:
            novelty_prior = (len(pairs) + 2) / (observed + 20)
        return (-novelty_prior, -len(family_queues[fam]), fam)

    # Round-robin over signature first, then family.  The non-real archives are
    # much larger than the real-signature archives; a family-only scheduler
    # would consequently overfill r=0 even when other chambers are available.
    strata = defaultdict(deque)
    for fam, queue in family_queues.items():
        while queue:
            row = queue.popleft()
            root = int(row.get("local_root_count", row.get("target_r", -1)))
            strata[(root, fam)].append(row)
    family_order = {fam: rank for rank, fam in enumerate(sorted(family_queues, key=family_rank))}
    roots = sorted({root for root, _fam in strata})
    if roots:
        roots = roots[(args.wave - 1) % len(roots) :] + roots[: (args.wave - 1) % len(roots)]
    active = deque(
        (root, fam)
        for root in roots
        for _same_root, fam in sorted(
            (key for key in strata if key[0] == root),
            key=lambda key: (family_order[key[1]], key[1]),
        )
    )
    selected = []
    family_counts = Counter()
    source_counts = Counter()
    root_counts = Counter()
    while active and len(selected) < args.size:
        root, fam = active.popleft()
        queue = strata[(root, fam)]
        if not queue:
            continue
        row = queue.popleft()
        selected.append(row)
        family_counts[fam] += 1
        source_counts[row["source_file"]] += 1
        root_counts[int(row.get("local_root_count", row.get("target_r", -1)))] += 1
        if queue:
            active.append((root, fam))
    if len(selected) != args.size:
        raise ValueError(f"only {len(selected)} eligible family-balanced rows remain")

    manifest = "".join(row["coefficients"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "purpose": "unclassified valid-field architecture discovery",
        "includedFamilies": sorted(set(args.include_family)),
        "wave": args.wave,
        "sourceFilesScanned": len(paths),
        "candidateUniverse": len(candidates),
        "excludedKnownOrReceipted": len(set(candidates) & excluded),
        "eligibleFineFingerprints": len(fine),
        "selectedRows": len(selected),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "familyDistribution": dict(sorted(family_counts.items())),
        "rootDistribution": dict(sorted(root_counts.items())),
        "sourceDistribution": dict(sorted(source_counts.items())),
        "skipCounts": dict(sorted(skips.items())),
        "selected": [
            {
                "candidateHash": row["candidate_hash"],
                "family": row["family"],
                "localRootCount": row.get("local_root_count", row.get("target_r")),
                "sourceFile": row["source_file"],
            }
            for row in selected
        ],
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "selected"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

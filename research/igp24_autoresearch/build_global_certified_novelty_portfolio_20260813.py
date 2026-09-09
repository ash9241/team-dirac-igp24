#!/usr/bin/env python3
"""Build receipt-safe exploration waves from every locally certified field bank."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(row: dict) -> str | None:
    for key in ("coefficients", "coefficientLine", "coefficient_line", "polynomial"):
        if key not in row:
            continue
        value = row[key]
        try:
            coefficients = [int(item) for item in (value if isinstance(value, list) else str(value).split(","))]
        except (TypeError, ValueError):
            continue
        if len(coefficients) == 25 and coefficients[-1] == 1:
            return ",".join(map(str, coefficients))
    return None


def certified(row: dict) -> bool:
    return bool(
        row.get("local_irreducible") is True
        or row.get("irreducible") is True
        or row.get("directPariIrreducible") is True
        or (
            row.get("primitiveMonicDegree24") is True
            and row.get("irreducibilityProof")
        )
    )


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def receipt_hashes(receipts: Path) -> set[str]:
    result = set()
    for path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if (
                not manifest.is_file()
                or hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]
            ):
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    result.add(digest(",".join(str(int(item)) for item in line.split(","))))
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            continue
    return result


def manifest_hashes(paths: list[Path]) -> set[str]:
    """Return canonical hashes from staged manifests that have no receipt yet."""
    result = set()
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                result.add(digest(",".join(str(int(item)) for item in line.split(","))))
    return result


def root_count(row: dict) -> int:
    for key in ("local_root_count", "target_r", "r", "realRootCount", "root_count"):
        try:
            value = int(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= value <= 24 and value % 2 == 0:
            return value
    return -1


def height(line: str) -> tuple[int, int, str]:
    values = [abs(int(item)) for item in line.split(",")]
    return max(values).bit_length(), sum(item.bit_length() for item in values), line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument(
        "--max-per-fine-bucket",
        type=int,
        default=1,
        help="height-ranked representatives retained per source/family/root bucket",
    )
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
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite global certified portfolio")

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
                normalized = {
                    "candidateHash": candidate_hash,
                    "coefficients": line,
                    "family": family,
                    "root": root_count(row),
                    "source": source,
                }
                incumbent = candidates.get(candidate_hash)
                if incumbent is None or (source, family) < (incumbent["source"], incumbent["family"]):
                    candidates[candidate_hash] = normalized

    excluded = receipt_hashes(args.receipts)
    staged_excluded = manifest_hashes(args.exclude_manifest)
    excluded.update(staged_excluded)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    hashes = list(candidates)
    for offset in range(0, len(hashes), 800):
        batch = hashes[offset : offset + 800]
        marks = ",".join("?" for _ in batch)
        excluded.update(
            str(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT coefficient_hash FROM polynomials WHERE coefficient_hash IN ({marks})",
                batch,
            )
        )
    connection.close()

    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for candidate_hash, row in candidates.items():
        if candidate_hash in excluded:
            continue
        # One height-minimized representative per source/family/signature
        # chamber prevents giant archives from dominating a wave.
        buckets[(row["source"], row["family"], row["root"])].append(row)
    source_root_queues = defaultdict(list)
    for (source, family, root), rows in buckets.items():
        rows.sort(key=lambda row: (height(row["coefficients"]), row["candidateHash"]))
        source_root_queues[(source, root)].extend(rows[: args.max_per_fine_bucket])
    for queue in source_root_queues.values():
        queue.sort(key=lambda row: (row["family"], height(row["coefficients"]), row["candidateHash"]))

    strata = {key: deque(rows) for key, rows in source_root_queues.items()}
    roots = sorted({root for _source, root in strata})
    if roots:
        shift = (args.wave - 1) % len(roots)
        roots = roots[shift:] + roots[:shift]
    keys_by_root = {
        root: deque(sorted((item for item in strata if item[1] == root), key=lambda item: item[0]))
        for root in roots
    }
    active_roots = deque(root for root in roots if keys_by_root[root])
    if active_roots:
        active_roots.rotate(-((args.wave - 1) % len(active_roots)))
    selected = []
    source_counts = Counter()
    family_counts = Counter()
    root_counts = Counter()
    while active_roots and len(selected) < args.size:
        root = active_roots.popleft()
        root_keys = keys_by_root[root]
        key = root_keys.popleft()
        queue = strata[key]
        if not queue:
            continue
        row = queue.popleft()
        selected.append(row)
        source_counts[row["source"]] += 1
        family_counts[row["family"]] += 1
        root_counts[row["root"]] += 1
        if queue:
            root_keys.append(key)
        if root_keys:
            active_roots.append(root)
    if len(selected) != args.size:
        raise ValueError(f"only {len(selected)} globally balanced candidates remain")

    manifest = "".join(row["coefficients"] + "\n" for row in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest, encoding="utf-8")
    summary = {
        "purpose": "global locally certified degree-24 novelty exploration",
        "wave": args.wave,
        "filesScanned": len(files),
        "candidateUniverse": len(candidates),
        "excludedKnownOrReceipted": len(set(candidates) & excluded),
        "excludedByStagedManifests": len(set(candidates) & staged_excluded),
        "eligibleFineBuckets": len(buckets),
        "selectedRows": len(selected),
        "selectedSources": len(source_counts),
        "manifest": str(args.output.resolve().relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "rootDistribution": dict(sorted(root_counts.items())),
        "sourceDistribution": dict(sorted(source_counts.items())),
        "familyDistribution": dict(family_counts.most_common()),
        "skipCounts": dict(sorted(skips.items())),
    }
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key not in {"sourceDistribution", "familyDistribution"}}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

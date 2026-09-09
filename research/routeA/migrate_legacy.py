#!/usr/bin/env python3
"""Best-effort, repeatable migration of legacy routeA artifacts into SQLite."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from routeA.ledger import DEFAULT_DB, Ledger, candidate_hash, payload_hash


HERE = Path(__file__).resolve().parent


def migrate(ledger: Ledger, route_dir: Path = HERE) -> dict[str, int]:
    stats: Counter[str] = Counter()
    legacy_hashes: dict[str, list[str]] = defaultdict(list)
    sid_map = _read_jsonl(route_dir / "sid_map.jsonl")
    for sid_record in sid_map:
        batch_name = sid_record.get("batch")
        sid = sid_record.get("sid")
        if not batch_name or not sid:
            continue
        batch_path = route_dir / batch_name
        manifest_path = route_dir / f"{batch_name}.manifest"
        if not batch_path.exists() or not manifest_path.exists():
            stats["missing_batches"] += 1
            continue
        lines = [line.strip() for line in batch_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        manifests = _read_jsonl(manifest_path)
        if len(lines) != len(manifests):
            stats["manifest_length_mismatch"] += 1
            continue
        keys = []
        for line, manifest in zip(lines, manifests):
            family = str(manifest.get("fam") or manifest.get("cls") or manifest.get("arm") or "legacy_routeA")
            recipe_id = ledger.upsert_recipe(
                {
                    "family": family,
                    "parameters": manifest,
                    "recipe_id": f"legacy:{manifest.get('h')}" if manifest.get("h") else None,
                }
            )
            key = ledger.upsert_candidate(
                {
                    "coefficients": line,
                    "recipe_id": recipe_id,
                    "local_irreducible": True,
                    "local_root_count": manifest.get("r"),
                    "source_host": "legacy",
                }
            )
            keys.append(key)
            if manifest.get("h"):
                legacy_hashes[str(manifest["h"])].append(key)
            stats["candidates"] += 1
        batch_uuid = f"legacy:{sid}"
        ledger.persist_batch(batch_uuid, keys, payload_hash(lines))
        ledger.mark_submitted(batch_uuid, str(sid))
        stats["submissions"] += 1

    unique_legacy = {
        old: keys[0] for old, keys in legacy_hashes.items() if len(set(keys)) == 1
    }
    for record in _read_jsonl(route_dir / "knowledge.jsonl"):
        dial = record.get("dial") or {}
        old_hash = dial.get("h") or record.get("h")
        key = unique_legacy.get(str(old_hash)) if old_hash else None
        if not key or not record.get("sid"):
            stats["unresolved_knowledge"] += 1
            continue
        ledger.record_verification(
            key,
            str(record["sid"]),
            {
                "status": "accepted",
                "t": int(record["t"]),
                "r": int(record["r"]),
                "legacy": True,
            },
        )
        stats["verifications"] += 1
    return dict(stats)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--route-dir", default=str(HERE))
    args = parser.parse_args()
    with Ledger(args.db) as ledger:
        stats = migrate(ledger, Path(args.route_dir))
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

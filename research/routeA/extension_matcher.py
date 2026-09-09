#!/usr/bin/env python3
"""Build target queues only from causal GAP/extension compatibility records."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


MATCHER_VERSION = "extension-v1"
ACCEPTED_EVIDENCE = {"gap-exact", "extension-fingerprint"}


def build_queue(
    open_pairs: Iterable[tuple[int, int]],
    capabilities: Iterable[Mapping[str, Any]],
    *,
    max_recipes_per_target: int = 10,
) -> dict[str, dict[str, Any]]:
    open_by_target: dict[int, set[int]] = defaultdict(set)
    for t, r in open_pairs:
        open_by_target[int(t)].add(int(r))
    recipes_by_target: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in capabilities:
        evidence = raw.get("compatibility")
        if evidence not in ACCEPTED_EVIDENCE:
            continue
        recipe = dict(raw.get("recipe") or raw.get("dial") or {})
        recipe_id = str(raw.get("recipe_id") or recipe.get("recipe_id") or "")
        if not recipe_id:
            continue
        for target in raw.get("compatible_targets", []):
            t = int(target)
            if t not in open_by_target:
                continue
            recipes_by_target[t].append(
                {
                    "recipe_id": recipe_id,
                    "family": raw.get("family") or recipe.get("fam") or "unknown",
                    "compatibility": evidence,
                    "structural_fingerprint": raw.get("structural_fingerprint"),
                    "dial": recipe,
                }
            )
    queue = {}
    for t, recipes in recipes_by_target.items():
        unique = {}
        for recipe in recipes:
            unique.setdefault(recipe["recipe_id"], recipe)
        chosen = list(unique.values())[:max_recipes_per_target]
        strongest = "gap-exact" if any(r["compatibility"] == "gap-exact" for r in chosen) else "extension-fingerprint"
        queue[str(t)] = {
            "matcher_version": MATCHER_VERSION,
            "compatibility": strongest,
            "open": sorted(open_by_target[t]),
            "recipes": chosen,
            "dials": [recipe["dial"] for recipe in chosen],
        }
    return queue


def load_open_pairs(data_dir: Path) -> set[tuple[int, int]]:
    pairs = set()
    for name in ("unclaimed_nonbaseline.json", "raid_pairs.json"):
        path = data_dir / name
        if path.exists():
            pairs.update((int(t), int(r)) for t, r in json.loads(path.read_text(encoding="utf-8")))
    return pairs


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capabilities", help="JSONL recipe capability catalog")
    parser.add_argument("--data-dir", default=str(here.parent / "daemon" / "data"))
    parser.add_argument("--output", default=str(here / "targeted_queue.json"))
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.capabilities).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    queue = build_queue(load_open_pairs(Path(args.data_dir)), rows)
    Path(args.output).write_text(json.dumps(queue, sort_keys=True) + "\n", encoding="utf-8")
    print(f"queue: {len(queue)} causally compatible labels")


if __name__ == "__main__":
    main()

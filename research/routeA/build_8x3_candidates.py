#!/usr/bin/env python3
"""Build a candidate shard from component libraries and an exact GAP map."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from routeA.constructions.compositum_8x3 import disjoint_ramification, generate_compositum
from routeA.constructions.fiber_product_8x3 import generate_fiber_compositum, shares_sign_resolvent
from routeA.gap_product_map import load_components


def load_product_map(path: str | Path) -> dict[tuple[int, int], int]:
    mapping = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            mapping[(int(row["g8"]), int(row["g3"]))] = int(row["target_t"])
    return mapping


def load_open_pairs(data_dir: str | Path) -> set[tuple[int, int]]:
    data_dir = Path(data_dir)
    pairs = set()
    for name in ("unclaimed_nonbaseline.json", "raid_pairs.json"):
        path = data_dir / name
        if path.exists():
            pairs.update((int(t), int(r)) for t, r in json.loads(path.read_text(encoding="utf-8")))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("octics")
    parser.add_argument("cubics")
    parser.add_argument("product_map")
    parser.add_argument("output")
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "daemon" / "data"))
    parser.add_argument("--c", default="-3,-2,-1,1,2,3")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--mode", choices=("direct", "fiber-c2"), default="direct")
    args = parser.parse_args()
    octics = load_components(args.octics)
    cubics = load_components(args.cubics)
    product_map = load_product_map(args.product_map)
    open_pairs = load_open_pairs(args.data_dir)
    parameters = [int(value) for value in args.c.split(",") if int(value) != 0]
    written = 0
    seen = set()
    with Path(args.output).open("w", encoding="utf-8") as output:
        for octic in octics:
            for cubic in cubics:
                target_t = product_map.get((octic.transitive_id, cubic.transitive_id))
                target_r = octic.root_count * cubic.root_count
                if target_t is None or (target_t, target_r) not in open_pairs:
                    continue
                if args.mode == "direct" and not disjoint_ramification(octic, cubic):
                    continue
                if args.mode == "fiber-c2" and not shares_sign_resolvent(octic, cubic):
                    continue
                for primitive_parameter in parameters:
                    try:
                        generator = generate_compositum if args.mode == "direct" else generate_fiber_compositum
                        candidate = generator(
                            octic, cubic, primitive_parameter=primitive_parameter, target_t=target_t
                        )
                    except (RuntimeError, ValueError):
                        continue
                    if candidate.candidate_hash in seen:
                        continue
                    seen.add(candidate.candidate_hash)
                    output.write(json.dumps(candidate.to_json(), sort_keys=True) + "\n")
                    written += 1
                    if written >= args.limit:
                        print(f"wrote {written} candidates")
                        return
    print(f"wrote {written} candidates")


if __name__ == "__main__":
    main()

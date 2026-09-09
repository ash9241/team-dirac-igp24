#!/usr/bin/env sage -python
"""Exact degree-24 orbit census for the 3-subset action.

This is an offline, sharded raid census.  It reads locally verified source
labels and the durable Low-hanging-fruit solo snapshot, then asks GAP for every
orbit of each source group on the 2,024 unordered triples.  A length-24 orbit
is an exact alternative degree-24 permutation representation.  For target
hits, conjugacy-class representatives of order one or two give the exact
source/target real-root signature map.

No network or submission operation is performed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_PAIR_MAP = ROOT / "data" / "pair_orbit_map.jsonl"
DEFAULT_TARGETS = ROOT / "data" / "low_hanging_fruit_unique_placements.jsonl"


def fixed_points(permutation, degree: int = 24) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def load_targets(path: Path):
    pairs: dict[str, set[int]] = defaultdict(set)
    target_t: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if int(row.get("kTeams", 1)) != 1:
            continue
        label = str(row["label"])
        pairs[label].add(int(row["r"]))
        target_t[label] = int(row.get("t", label[3:]))
    orders = {
        int(libgap.Size(libgap.TransitiveGroup(24, t)))
        for t in sorted(target_t.values())
    }
    return dict(pairs), orders


def load_sources(db: Path, pair_map: Path, target_orders: set[int]):
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        signatures: dict[str, set[int]] = defaultdict(set)
        for label, r in connection.execute(
            """
            SELECT DISTINCT label,r
            FROM verifications
            WHERE status='accepted' AND scoreable=1
              AND label IS NOT NULL AND r IS NOT NULL
            """
        ):
            signatures[str(label)].add(int(r))
    finally:
        connection.close()

    rows = []
    for line in pair_map.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        label = str(row["sourceLabel"])
        if label not in signatures:
            continue
        if int(row["sourceOrder"]) not in target_orders:
            continue
        rows.append(
            {
                "sourceLabel": label,
                "sourceOrder": int(row["sourceOrder"]),
                "sourceR": sorted(signatures[label]),
                "sourceT": int(row["sourceT"]),
            }
        )
    return sorted(rows, key=lambda row: row["sourceT"])


def target_signature_routes(group, orbit_rows, source_rs, target_pairs):
    interesting = [
        row for row in orbit_rows if row["targetLabel"] in target_pairs
    ]
    if not interesting:
        return []
    routes = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        source_r = fixed_points(representative)
        if source_r not in source_rs:
            continue
        for row in interesting:
            image_element = libgap.Image(row["homomorphism"], representative)
            target_r = fixed_points(image_element)
            if target_r not in target_pairs[row["targetLabel"]]:
                continue
            routes.append(
                {
                    "classIndex": class_index,
                    "classSize": int(libgap.Size(conjugacy_class)),
                    "orbitIndex": row["orbitIndex"],
                    "order": order,
                    "sourceR": source_r,
                    "targetLabel": row["targetLabel"],
                    "targetR": target_r,
                }
            )
    return routes


def census_one(source, triples, target_pairs):
    started = time.monotonic()
    group = libgap.TransitiveGroup(24, source["sourceT"])
    orbits = list(libgap.Orbits(group, triples, libgap.OnSets))
    orbit_sizes = [int(libgap.Length(orbit)) for orbit in orbits]
    length_24 = []
    for orbit_index, orbit in enumerate(orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(homomorphism)
        target_t = int(libgap.TransitiveIdentification(image))
        length_24.append(
            {
                "homomorphism": homomorphism,
                "imageOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(homomorphism))),
                "orbitIndex": orbit_index,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
            }
        )
    signature_routes = target_signature_routes(
        group, length_24, set(source["sourceR"]), target_pairs
    )
    serializable = [
        {key: value for key, value in row.items() if key != "homomorphism"}
        for row in length_24
    ]
    return {
        **source,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "length24OrbitCount": len(length_24),
        "nonselfLength24Count": sum(
            row["targetT"] != source["sourceT"] for row in serializable
        ),
        "orbitSizes": orbit_sizes,
        "signatureRoutes": signature_routes,
        "soloTargetOrbitCount": sum(
            row["targetLabel"] in target_pairs for row in serializable
        ),
        "status": "certified",
        "targets": serializable,
    }


def write_atomic(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--pair-map", type=Path, default=DEFAULT_PAIR_MAP)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index must be in [0, shard-count)")

    target_pairs, target_orders = load_targets(args.targets)
    all_sources = load_sources(args.db, args.pair_map, target_orders)
    sources = [
        row for index, row in enumerate(all_sources)
        if index % args.shard_count == args.shard_index
    ]
    triples = libgap.Combinations(libgap.eval("[1..24]"), 3)
    rows = []
    for index, source in enumerate(sources, start=1):
        try:
            row = census_one(source, triples, target_pairs)
        except Exception as exc:
            row = {
                **source,
                "error": f"{type(exc).__name__}: {exc}",
                "status": "error",
            }
        rows.append(row)
        if args.checkpoint_every and index % args.checkpoint_every == 0:
            write_atomic(args.output, rows)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "routes": sum(len(x.get("signatureRoutes", [])) for x in rows),
                        "shard": args.shard_index,
                        "total": len(sources),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    write_atomic(args.output, rows)
    summary = {
        "certified": sum(row["status"] == "certified" for row in rows),
        "errors": sum(row["status"] != "certified" for row in rows),
        "output": str(args.output.resolve()),
        "signatureRoutes": sum(len(row.get("signatureRoutes", [])) for row in rows),
        "soloTargetOrbits": sum(row.get("soloTargetOrbitCount", 0) for row in rows),
        "sourceUniverse": len(all_sources),
        "sources": len(sources),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

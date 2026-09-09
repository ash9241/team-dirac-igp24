#!/usr/bin/env sage -python
"""Compute exact unordered-pair action maps for verified degree-24 sources."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ledger.sqlite3"
OUTPUT_PATH = ROOT / "data" / "pair_orbit_map.jsonl"


def source_labels() -> list[tuple[str, int]]:
    with sqlite3.connect(DB_PATH) as conn:
        return [
            (str(label), int(t))
            for label, t in conn.execute(
                "SELECT DISTINCT label, t FROM verifications "
                "WHERE status='accepted' OR status='ok' "
                "ORDER BY t"
            )
        ]


def orbit_map(label: str, t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
    pairs = libgap.Combinations(libgap.eval("[1..24]"), 2)
    orbits = list(libgap.Orbits(group, pairs, libgap.OnSets))
    orbit_sizes = [int(libgap.Length(orbit)) for orbit in orbits]
    targets: list[dict] = []
    for orbit_index, orbit in enumerate(orbits):
        orbit_size = int(libgap.Length(orbit))
        if orbit_size != 24:
            continue
        homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(homomorphism)
        target_t = int(libgap.TransitiveIdentification(image))
        targets.append(
            {
                "orbitIndex": orbit_index,
                "orbitSize": orbit_size,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
                "imageOrder": int(libgap.Size(image)),
                "kernelOrder": int(libgap.Size(libgap.Kernel(homomorphism))),
            }
        )
    target_counts = Counter(row["targetLabel"] for row in targets)
    return {
        "sourceLabel": label,
        "sourceT": t,
        "sourceOrder": int(libgap.Size(group)),
        "orbitSizes": orbit_sizes,
        "length24OrbitCount": len(targets),
        "targetCounts": dict(sorted(target_counts.items())),
        "targets": targets,
    }


def main() -> None:
    rows = []
    failures = []
    labels = source_labels()
    for position, (label, t) in enumerate(labels, start=1):
        try:
            rows.append(orbit_map(label, t))
        except Exception as exc:
            failures.append({"sourceLabel": label, "sourceT": t, "error": str(exc)})
            print(f"ERROR {label}: {exc}", file=sys.stderr, flush=True)
        if position % 25 == 0 or position == len(labels):
            print(f"mapped {position}/{len(labels)} source groups", file=sys.stderr, flush=True)
    temporary = OUTPUT_PATH.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(OUTPUT_PATH)
    for row in rows:
        print(
            f"{row['sourceLabel']}: orbits={row['orbitSizes']} "
            f"length24={row['length24OrbitCount']} targets={row['targetCounts']}"
        )
    print(f"wrote {len(rows)} source maps to {OUTPUT_PATH}")
    if failures:
        failure_path = ROOT / "data" / "pair_orbit_map_failures.json"
        failure_path.write_text(json.dumps(failures, indent=2) + "\n", encoding="utf-8")
        print(f"{len(failures)} mappings failed; details in {failure_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

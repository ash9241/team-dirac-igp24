#!/usr/bin/env sage
"""Check every normable multi-field K4 candidate against all nine Q66 targets."""

import importlib.util
import json
from pathlib import Path

from sage.all import PolynomialRing, ZZ, prime_range


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "data" / "q66_k4_multifield_orientation_pilot.jsonl"
OUTPUT = ROOT / "data" / "q66_k4_multifield_nine_target_check.json"
TARGETS = [15051, 15091, 10381, 15082, 17170, 12926, 12936, 15398, 15519]

spec = importlib.util.spec_from_file_location(
    "q66_shared", ROOT / "q66_k4_radical_search_20260727.sage.py"
)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def main():
    profiles = {target: shared.group_profiles(target) for target in TARGETS}
    ring = PolynomialRing(ZZ, "x")
    candidates = {}
    for line in INPUT.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        coefficient_line = row.get("candidateCoefficientLine")
        if coefficient_line:
            candidates[row["candidateSha256"]] = {
                "candidateSha256": row["candidateSha256"],
                "candidateCoefficientLine": coefficient_line,
                "qSha256": row["qSha256"],
                "realRoots": row["realRoots"],
            }
    results = []
    for record in candidates.values():
        polynomial = ring([ZZ(value) for value in record["candidateCoefficientLine"].split(",")])
        possible = set(TARGETS)
        observations = []
        for prime in prime_range(2, 10000):
            profile = shared.cycle_type(polynomial, int(prime))
            if profile is None:
                continue
            observations.append((int(prime), profile))
            possible = {
                target for target in possible if profile in profiles[target]
            }
            if not possible:
                break
        results.append(
            {
                **record,
                "survivors": [f"24T{target}" for target in sorted(possible)],
                "checkedGoodPrimes": len(observations),
                "decisiveExclusion": (
                    None
                    if possible
                    else {
                        "prime": observations[-1][0],
                        "cycleType": list(observations[-1][1]),
                    }
                ),
            }
        )
    OUTPUT.write_text(
        json.dumps(
            {
                "targets": [f"24T{target}" for target in TARGETS],
                "candidateCount": len(results),
                "survivorCount": sum(bool(row["survivors"]) for row in results),
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(OUTPUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

#!/usr/bin/env sage
"""Test the two K4 edge orientations across every recovered 12T66 field."""

import glob
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari, prime_range


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "q66_k4_multifield_orientation_pilot.jsonl"

spec = importlib.util.spec_from_file_location(
    "q66_shared", ROOT / "q66_k4_radical_search_20260727.sage.py"
)
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def recovered():
    values = {}
    for path_string in glob.glob(
        str(ROOT / "data" / "agent_f5_full_ledger_pair_product_routes_shard*of4.jsonl")
    ):
        path = Path(path_string)
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if int(row.get("action", {}).get("quotientT12", -1)) != 66:
                continue
            source = row["source"]
            values[source["quotientPolynomialSha256"]] = {
                "qLine": source["quotientLine"],
                "qSha256": source["quotientPolynomialSha256"],
                "sourceLabel": source["label"],
                "submissionId": source["submissionId"],
                "polynomialIndex": source["polynomialIndex"],
            }
    return [values[key] for key in sorted(values)]


def main():
    request_profiles = {
        target: shared.group_profiles(target) for target in shared.REQUESTED
    }
    ring = PolynomialRing(QQ, "y")
    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()
    rows = []
    for source in recovered():
        q = ring([ZZ(value) for value in source["qLine"].split(",")])
        field = NumberField(q, "a")
        edge_field, embedding, _ = field.subfields(6)[0]
        relative = field.relativize(embedding(edge_field.gen()), "b")
        base = relative.base_field()
        norm_data = base.pari_rnfnorm_data(relative)
        closure, _ = edge_field.galois_closure(names="w", map=True)
        quartics = sorted(
            {str(entry[0].polynomial()): entry[0].polynomial()
             for entry in closure.subfields(4)}.values(),
            key=str,
        )
        seen_candidates = set()
        for quartic_index, quartic in enumerate(quartics):
            resolvent = quartic.symmetric_power(2, monic=True)
            roots = resolvent.change_ring(base).roots()
            for orientation, (edge_value, multiplicity) in enumerate(roots):
                answer = norm_data.rnfisnorm(pari(edge_value))
                obstruction_one = str(answer[1]) == "1"
                row = {
                    **source,
                    "quarticIndex": quartic_index,
                    "quartic": str(quartic),
                    "edgeResolvent": str(resolvent),
                    "orientation": orientation,
                    "relativeNormObstructionIsOne": obstruction_one,
                    "relativeNormObstruction": str(answer[1]),
                    "networkCalls": 0,
                    "submissionCalls": 0,
                }
                if obstruction_one:
                    relative_value = relative(answer[0])
                    absolute = relative.structure()[0](relative_value)
                    _, scale, minimal = shared.integral_square_scale(absolute)
                    candidate = candidate_ring(minimal(x**2))
                    line = shared.canonical_line(candidate)
                    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
                    row.update(
                        {
                            "squareScale": scale,
                            "candidateCoefficientLine": line,
                            "candidateSha256": digest,
                            "candidateBytes": len(line.encode("ascii")),
                            "irreducible": bool(candidate.is_irreducible()),
                            "realRoots": int(candidate.number_of_real_roots()),
                        }
                    )
                    if digest in seen_candidates:
                        row["status"] = "duplicate_candidate_orientation"
                    else:
                        seen_candidates.add(digest)
                        possible = set(shared.REQUESTED)
                        observations = []
                        for prime in prime_range(2, 5000):
                            profile = shared.cycle_type(candidate, int(prime))
                            if profile is None:
                                continue
                            observations.append((int(prime), profile))
                            possible = {
                                target for target in possible
                                if profile in request_profiles[target]
                            }
                            if not possible:
                                break
                        row["checkedGoodPrimes"] = len(observations)
                        row["requestedSurvivors"] = [
                            f"24T{target}" for target in sorted(possible)
                        ]
                        row["decisiveExclusion"] = (
                            None if possible else {
                                "prime": observations[-1][0],
                                "cycleType": list(observations[-1][1]),
                            }
                        )
                        row["status"] = (
                            "requested_survivor"
                            if possible else "requested_targets_excluded"
                        )
                else:
                    row["status"] = "desired_edge_orientation_not_relative_norm"
                rows.append(row)
                shared.atomic_text(
                    OUTPUT,
                    "".join(
                        json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n"
                        for item in rows
                    ),
                )
                print(
                    json.dumps(
                        {
                            "q": source["qSha256"][:12],
                            "quartic": quartic_index,
                            "orientation": orientation,
                            "norm": obstruction_one,
                            "status": row["status"],
                            "r": row.get("realRoots"),
                            "survivors": row.get("requestedSurvivors", []),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "normableOrientations": sum(
                    row["relativeNormObstructionIsOne"] for row in rows
                ),
                "requestedSurvivors": sum(
                    row["status"] == "requested_survivor" for row in rows
                ),
                "output": str(OUTPUT),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

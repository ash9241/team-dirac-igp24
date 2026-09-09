#!/usr/bin/env sage -python
"""Seal the fail-closed q191 singleton-core census audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

from sage.all import prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOCAL = DATA / "broad_structural_character_gate_q191_singleton_local_20260731.json"
OUTPUT = DATA / "q191_singleton_exact_closure_20260731.json"
HELPER_PATH = (
    ROOT / "broad_structural_character_gate_q191_frobenius_squareclass_20260731.sage.py"
)
EXACT = {
    "4604d7ca5d2d77e5695d132ecdebae5729f4194dd7c04c7c43ab9af6cae8c792":
        DATA / "broad_structural_character_gate_q191_exact_4604d7ca_squareclass_20260731.json",
    "8d299dcc7d762bdc3afc7e27d7368738e497bc8144d0f9f8d0572ce1e362874f":
        DATA / "broad_structural_character_gate_q191_exact_8d299dcc_squareclass_20260731.json",
    "df57b2f83655d8e8ac8cc138ac5e796ab4bf84e3b4c4616ddf3cd6e619df5639":
        DATA / "broad_structural_character_gate_q191_exact_df57b2f8_squareclass_20260731.json",
}


def load_helper():
    spec = importlib.util.spec_from_file_location("q191_closure_helper", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def possible_block_quotients(helper, checked_sources: list[dict]) -> set[int]:
    structures = helper.structure_rows()
    possible_sets = []
    for source in checked_sources:
        systems = [
            system
            for system in structures[source["label"]].get("blockSystems", [])
            if system.get("shape") == "12x2"
        ]
        possible_sets.append(
            {int(str(system["quotientActionLabel"])[3:]) for system in systems}
        )
    return set.intersection(*possible_sets)


def cycle_classification(helper, quotient, possible: set[int]) -> tuple[set[int], list[dict]]:
    profiles = {
        value: helper.group_cycle_profiles(value) for value in sorted(possible)
    }
    remaining = set(possible)
    elimination = []
    for prime in prime_range(2, 5001):
        prime = int(prime)
        observed = helper.cycle_type(quotient, prime)
        if observed is None:
            continue
        reduced = {
            value for value in remaining if observed in profiles[value]
        }
        if len(reduced) < len(remaining):
            elimination.append(
                {
                    "afterLabels": [f"12T{value}" for value in sorted(reduced)],
                    "cycleType": list(observed),
                    "prime": prime,
                }
            )
            remaining = reduced
        if len(remaining) <= 1:
            break
    return remaining, elimination


def main() -> int:
    helper = load_helper()
    local = read_json(LOCAL)
    exact_payloads = {digest: read_json(path) for digest, path in EXACT.items()}
    audits = []
    q191_obstructed_routes = 0
    non_q191_routes = 0

    for field in local["fields"]:
        passers = [
            pair for pair in field["localPairGates"] if pair.get("localPass")
        ]
        if not passers:
            continue
        field_hash = str(field["fieldCanonicalSha256"])
        identity = helper.verified_even_quotient(field)
        possible = possible_block_quotients(
            helper, identity["checkedSources"]
        )
        remaining, elimination = cycle_classification(
            helper, identity["quotient"], possible
        )
        direct_label = None
        if 191 in remaining and len(remaining) > 1:
            direct_label = int(
                identity["quotient"].galois_group(
                    algorithm="gap"
                ).transitive_number()
            )
            remaining = {direct_label}
        if 191 in remaining and len(remaining) != 1:
            raise ArithmeticError(
                f"q191 closure failed to decide target membership for {field_hash}: "
                f"{sorted(remaining)}"
            )
        exact_t = next(iter(remaining)) if len(remaining) == 1 else None
        is_q191 = remaining == {191}
        route_cores = sorted(int(pair["core"]) for pair in passers)
        exact_sign = []
        if is_q191:
            if field_hash not in exact_payloads:
                raise ArithmeticError(
                    f"missing exact Selmer artifact for {field_hash}"
                )
            exact_field = exact_payloads[field_hash]["fields"][0]
            exact_pairs = (exact_field["exactSelmerSignGate"]["pairs"])
            if sorted(int(pair["core"]) for pair in exact_pairs) != route_cores:
                raise ArithmeticError("local/exact singleton-core mismatch")
            for pair in exact_pairs:
                if (
                    pair.get("status") != "exact_selmer_sign_obstruction"
                    or int(pair.get("exactSelmerSolvableSignMasks", -1)) != 0
                ):
                    raise ArithmeticError("unexpected q191 exact route status")
                exact_sign.append(
                    {
                        "core": int(pair["core"]),
                        "exactSelmerSolvableSignMasks": 0,
                        "status": "exact_selmer_sign_obstruction",
                    }
                )
            q191_obstructed_routes += len(route_cores)
        else:
            non_q191_routes += len(route_cores)

        audits.append(
            {
                "cycleElimination": elimination,
                "directGaloisLabel": (
                    None if direct_label is None else f"12T{direct_label}"
                ),
                "exactQuotientLabel": (
                    None if exact_t is None else f"12T{exact_t}"
                ),
                "remainingBlockQuotientLabels": [
                    f"12T{value}" for value in sorted(remaining)
                ],
                "exactSelmerSign": exact_sign,
                "fieldCanonicalSha256": field_hash,
                "possibleBlockQuotientsBeforeCycleExclusion": [
                    f"12T{value}" for value in sorted(possible)
                ],
                "routeCores": route_cores,
                "routeCount": len(route_cores),
                "sourceLabels": sorted(
                    {row["label"] for row in identity["checkedSources"]}
                ),
            }
        )

    total_routes = sum(row["routeCount"] for row in audits)
    if (
        total_routes != 10
        or q191_obstructed_routes != 3
        or non_q191_routes != 7
    ):
        raise ArithmeticError(
            "q191 closure census changed: "
            f"{total_routes=}, {q191_obstructed_routes=}, {non_q191_routes=}"
        )
    payload = {
        "audit": {
            "networkCalls": 0,
            "stageCalls": 0,
            "submissionCalls": 0,
        },
        "exactArtifacts": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
            }
            for path in EXACT.values()
        ],
        "fields": audits,
        "localArtifact": {
            "path": str(LOCAL.relative_to(ROOT)),
            "sha256": sha256_path(LOCAL),
        },
        "schemaVersion": "q191-singleton-exact-closure-v1",
        "summary": {
            "exactQ191SelmerSignObstructedRoutes": q191_obstructed_routes,
            "localSingletonCoreRoutes": total_routes,
            "nonQ191StructuralCensusRoutes": non_q191_routes,
            "stageableRoutes": 0,
            "status": "closed_without_packet",
        },
    }
    atomic_write(OUTPUT, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    print(f"wrote {OUTPUT}")
    print(f"sha256 {sha256_path(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

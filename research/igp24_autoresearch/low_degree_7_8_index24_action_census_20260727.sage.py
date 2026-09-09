#!/usr/bin/env sage -python
"""Action-first index-24 census from certified degree-7/8 source fields.

This lane is deliberately independent of radical/Kummer constructions.  It
uses the exact natural Galois groups of locally certified low-degree fields,
enumerates core-free subgroup conjugacy classes of index 24, identifies every
degree-24 coset action, computes the complex-conjugation signature map, and
only then intersects the result with the current tc0 ledger.

For each subgroup it also certifies a concrete low-degree relative invariant:
an H-orbit (Reynolds/trace) sum of root monomials whose stabilizer in G is
exactly H.  No resolvent polynomial is constructed by this census.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
DEGREE8_BANK = DATA / "agent_non12_all_degree8_subfields.jsonl"
DEGREE8_BANK_SUMMARY = DATA / "agent_non12_all_degree8_subfields_summary.json"
F6_ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"
F6_FRONTIER = DATA / "agent_index24_structural_frontier.jsonl"
F6A_ACTIONS = DATA / "index24_f6a_kummer_action_census.json"
SOURCE_OUTPUT = DATA / "low_degree_7_8_index24_source_inventory_20260727.jsonl"
ACTION_OUTPUT = DATA / "low_degree_7_8_index24_actions_20260727.jsonl"
ROUTE_OUTPUT = DATA / "low_degree_7_8_index24_routes_20260727.jsonl"
SUMMARY_OUTPUT = DATA / "low_degree_7_8_index24_summary_20260727.json"
RING = PolynomialRing(ZZ, "x")


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
    temporary.replace(path)


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def fixed_points(permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def subgroup_identity(group_label: str, subgroup) -> str:
    payload = {
        "groupLabel": group_label,
        "smallGenerators": sorted(
            str(value) for value in libgap.SmallGeneratingSet(subgroup)
        ),
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def permutation_tuple(permutation, degree: int) -> tuple[int, ...]:
    return tuple(
        int(libgap.OnPoints(point, permutation)) - 1
        for point in range(1, degree + 1)
    )


def apply_mask(mask: int, permutation: tuple[int, ...]) -> int:
    image = 0
    for point, target in enumerate(permutation):
        if mask & (1 << point):
            image |= 1 << target
    return image


def apply_exponents(
    exponents: tuple[int, ...], permutation: tuple[int, ...]
) -> tuple[int, ...]:
    image = [0] * len(exponents)
    for point, target in enumerate(permutation):
        image[target] = exponents[point]
    return tuple(image)


def orbit_of_term_set(seed, generators, apply_term) -> set:
    orbit = {seed}
    queue = [seed]
    while queue:
        current = queue.pop()
        for generator in generators:
            image = frozenset(
                apply_term(term, generator) for term in current
            )
            if image not in orbit:
                orbit.add(image)
                queue.append(image)
                if len(orbit) > 24:
                    raise ArithmeticError(
                        "H-invariant term set unexpectedly has orbit above index 24"
                    )
    return orbit


def invariant_for_subgroup(
    degree: int,
    group_order: int,
    group_generators: list[tuple[int, ...]],
    subgroup_elements: list[tuple[int, ...]],
) -> dict:
    subgroup_order = len(subgroup_elements)

    # First seek a squarefree orbit-monomial trace.  The stabilizer of the
    # H-orbit of the seed subset is exactly H iff its G orbit has index 24.
    seen_subset_orbits = set()
    subset_candidates = []
    for subset_size in range(1, degree // 2 + 1):
        for subset in itertools.combinations(range(degree), subset_size):
            mask = sum(1 << point for point in subset)
            orbit = frozenset(apply_mask(mask, value) for value in subgroup_elements)
            if orbit in seen_subset_orbits:
                continue
            seen_subset_orbits.add(orbit)
            family_orbit = orbit_of_term_set(
                orbit, group_generators, apply_mask
            )
            stabilizer_order = group_order // len(family_orbit)
            if stabilizer_order == subgroup_order:
                subset_candidates.append(
                    (
                        len(orbit),
                        subset_size,
                        mask,
                        {
                            "baseExponents": [
                                int(bool(mask & (1 << point)))
                                for point in range(degree)
                            ],
                            "kind": "squarefree_subset_orbit_reynolds_sum",
                            "maxExponent": 1,
                            "orbitExponents": [
                                [
                                    int(bool(term & (1 << point)))
                                    for point in range(degree)
                                ]
                                for term in sorted(orbit)
                            ],
                            "stabilizerOrder": stabilizer_order,
                            "termCount": len(orbit),
                            "totalDegree": subset_size,
                        },
                    )
                )
    if subset_candidates:
        subset_candidates.sort(key=lambda row: row[:3])
        return subset_candidates[0][3]

    # Next seek a low-exponent orbit sum.  Search in increasing total degree.
    ternary = []
    for exponents in itertools.product(range(3), repeat=degree):
        if min(exponents) != 0 or max(exponents) != 2:
            continue
        ternary.append(exponents)
    ternary.sort(
        key=lambda exponents: (
            sum(exponents),
            sum(value != 0 for value in exponents),
            exponents,
        )
    )
    seen_exponent_orbits = set()
    for exponents in ternary:
        orbit = frozenset(
            apply_exponents(exponents, value) for value in subgroup_elements
        )
        if orbit in seen_exponent_orbits:
            continue
        seen_exponent_orbits.add(orbit)
        family_orbit = orbit_of_term_set(
            orbit, group_generators, apply_exponents
        )
        stabilizer_order = group_order // len(family_orbit)
        if stabilizer_order == subgroup_order:
            return {
                "baseExponents": list(exponents),
                "kind": "ternary_monomial_orbit_reynolds_sum",
                "maxExponent": 2,
                "orbitExponents": [list(term) for term in sorted(orbit)],
                "stabilizerOrder": stabilizer_order,
                "termCount": len(orbit),
                "totalDegree": sum(exponents),
            }

    # A monomial with pairwise distinct exponents has trivial stabilizer.
    # Consequently the set of its H-images has setwise stabilizer exactly H.
    # This gives an unconditional, explicit fixed-field invariant.
    exponents = tuple(range(degree))
    orbit = frozenset(
        apply_exponents(exponents, value) for value in subgroup_elements
    )
    family_orbit = orbit_of_term_set(
        orbit, group_generators, apply_exponents
    )
    stabilizer_order = group_order // len(family_orbit)
    if stabilizer_order != subgroup_order:
        raise ArithmeticError("universal distinct-exponent invariant failed")
    return {
        "baseExponents": list(exponents),
        "kind": "distinct_exponent_orbit_reynolds_sum",
        "maxExponent": degree - 1,
        "orbitExponents": [list(term) for term in sorted(orbit)],
        "stabilizerOrder": stabilizer_order,
        "termCount": len(orbit),
        "totalDegree": sum(exponents),
    }


def source_inventory() -> list[dict]:
    raw = read_jsonl(DEGREE8_BANK)
    by_hash = defaultdict(list)
    for row in raw:
        if (
            row.get("status") != "certified_recoverable_subfield"
            or int(row.get("subfieldDegree", 0)) != 8
        ):
            raise ValueError("degree-8 bank contains an uncertified/nondegree-8 row")
        line = str(row["coefficientLine"])
        if hashlib.sha256(line.encode("ascii")).hexdigest() != str(
            row["coefficientSha256"]
        ):
            raise ValueError("degree-8 source coefficient hash changed")
        coefficients = [int(value) for value in line.split(",")]
        if len(coefficients) != 9 or coefficients[-1] != 1:
            raise ValueError("degree-8 source is not monic of exact degree")
        if int(row["galoisGroup"]["degree"]) != 8:
            raise ValueError("degree-8 source group certificate changed")
        by_hash[str(row["coefficientSha256"])].append(row)
    inventory = []
    for digest, rows in sorted(by_hash.items()):
        coefficient_lines = {str(row["coefficientLine"]) for row in rows}
        group_labels = {str(row["galoisGroup"]["label"]) for row in rows}
        real_roots = {int(row["realRoots"]) for row in rows}
        if (
            len(coefficient_lines) != 1
            or len(group_labels) != 1
            or len(real_roots) != 1
        ):
            raise ArithmeticError("duplicate source hash has inconsistent certificate")
        best = min(
            rows,
            key=lambda row: (
                int(row["coefficientBytes"]),
                int(row["sourceLabel"][3:]),
                str(row["sourceSubmissionId"]),
                int(row["sourcePolynomialIndex"]),
            ),
        )
        polynomial = RING([int(value) for value in best["coefficientLine"].split(",")])
        if not polynomial.is_irreducible():
            raise ArithmeticError("certified degree-8 source became reducible")
        inventory.append(
            {
                "certificateInput": str(DEGREE8_BANK.relative_to(ROOT)),
                "coefficientBytes": int(best["coefficientBytes"]),
                "coefficientLine": str(best["coefficientLine"]),
                "coefficientSha256": digest,
                "degree": 8,
                "fieldDiscriminantAbs": str(best["fieldDiscriminantAbs"]),
                "galoisGroupLabel": str(best["galoisGroup"]["label"]),
                "galoisGroupOrder": int(best["galoisGroup"]["order"]),
                "provenance": [
                    {
                        "sourceCoefficientSha256": str(
                            row["sourceCoefficientSha256"]
                        ),
                        "sourceLabel": str(row["sourceLabel"]),
                        "sourcePolynomialIndex": int(row["sourcePolynomialIndex"]),
                        "sourceSubmissionId": str(row["sourceSubmissionId"]),
                        "subfieldOrdinal": int(row["subfieldOrdinal"]),
                    }
                    for row in sorted(
                        rows,
                        key=lambda row: (
                            int(row["sourceLabel"][3:]),
                            str(row["sourceSubmissionId"]),
                            int(row["sourcePolynomialIndex"]),
                            int(row["subfieldOrdinal"]),
                        ),
                    )
                ],
                "provenanceCount": len(rows),
                "realRoots": int(best["realRoots"]),
                "status": "certified_degree8_source",
            }
        )
    return inventory


def existing_f6_inventory() -> tuple[set[str], set[tuple[str, int]], dict]:
    action_labels = set()
    isomorphism_rows = read_jsonl(F6_ISOMORPHISM)
    for row in isomorphism_rows:
        if row.get("status") != "certified_isomorphic":
            continue
        for subgroup in row.get("subgroupClasses", []):
            action_labels.add(str(subgroup["actualTargetLabel"]))
    f6a = json.loads(F6A_ACTIONS.read_text())
    f6a_labels = {
        str(row["targetLabel"])
        for row in f6a["actionRows"]
        if row.get("transitive") and row.get("targetLabel")
    }
    action_labels.update(f6a_labels)
    frontier_pairs = set()
    frontier_rows = read_jsonl(F6_FRONTIER)
    for row in frontier_rows:
        for target_r in row.get("mappedTargetR", []):
            frontier_pairs.add((str(row["targetLabel"]), int(target_r)))
    return action_labels, frontier_pairs, {
        "certifiedIsomorphismRows": sum(
            row.get("status") == "certified_isomorphic"
            for row in isomorphism_rows
        ),
        "f6aTransitiveActionRows": sum(
            bool(row.get("transitive") and row.get("targetLabel"))
            for row in f6a["actionRows"]
        ),
        "frontierRows": len(frontier_rows),
    }


def current_tc0() -> set[tuple[str, int]]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        return {
            (str(label), int(r))
            for label, r in connection.execute(
                """
                SELECT t.label,t.r FROM targets AS t
                WHERE t.team_count=0 AND t.discovered=0
                  AND NOT EXISTS(
                    SELECT 1 FROM baseline_pairs AS b
                    WHERE b.label=t.label AND b.r=t.r
                  )
                  AND NOT EXISTS(
                    SELECT 1 FROM verifications AS v
                    WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
                  )
                """
            )
        }
    finally:
        connection.close()


def action_census(
    sources_by_group: dict[str, list[dict]],
    existing_f6_labels: set[str],
) -> list[dict]:
    output = []
    group_ranges = ((7, 7), (8, 50))
    for degree, group_count in group_ranges:
        for transitive_t in range(1, group_count + 1):
            group_label = f"{degree}T{transitive_t}"
            group = libgap.TransitiveGroup(degree, transitive_t)
            group_order = int(libgap.Size(group))
            if group_order % 24:
                continue
            generators = [
                permutation_tuple(value, degree)
                for value in libgap.GeneratorsOfGroup(group)
            ]
            relevant_classes = []
            for class_index, conjugacy_class in enumerate(
                libgap.ConjugacyClasses(group)
            ):
                representative = libgap.Representative(conjugacy_class)
                order = int(libgap.Order(representative))
                if order not in (1, 2):
                    continue
                relevant_classes.append(
                    {
                        "classIndex": class_index,
                        "classSize": int(libgap.Size(conjugacy_class)),
                        "order": order,
                        "representative": representative,
                        "sourceR": fixed_points(representative, degree),
                    }
                )
            subgroup_classes = list(libgap.ConjugacyClassesSubgroups(group))
            ordinal = 0
            for subgroup_class_index, subgroup_class in enumerate(subgroup_classes):
                subgroup = libgap.Representative(subgroup_class)
                if int(libgap.Index(group, subgroup)) != 24:
                    continue
                core_order = int(libgap.Size(libgap.Core(group, subgroup)))
                if core_order != 1:
                    continue
                cosets = libgap.RightCosets(group, subgroup)
                homomorphism = libgap.ActionHomomorphism(
                    group, cosets, libgap.OnRight
                )
                image = libgap.Image(homomorphism)
                if int(libgap.Size(libgap.Kernel(homomorphism))) != 1:
                    raise ArithmeticError("core-free coset action is not faithful")
                target_t = int(libgap.TransitiveIdentification(image))
                target_label = f"24T{target_t}"
                signature_map = defaultdict(set)
                profiles = []
                for class_row in relevant_classes:
                    target_r = fixed_points(
                        libgap.Image(
                            homomorphism, class_row["representative"]
                        ),
                        24,
                    )
                    signature_map[class_row["sourceR"]].add(target_r)
                    profiles.append(
                        {
                            "classIndex": class_row["classIndex"],
                            "classSize": class_row["classSize"],
                            "order": class_row["order"],
                            "sourceR": class_row["sourceR"],
                            "targetR": target_r,
                        }
                    )
                subgroup_elements = [
                    permutation_tuple(value, degree)
                    for value in libgap.Elements(subgroup)
                ]
                invariant = invariant_for_subgroup(
                    degree, group_order, generators, subgroup_elements
                )
                invariant["formula"] = (
                    "sum over the recorded H-orbit exponents e of "
                    "product_i(root_i^e_i)"
                )
                invariant["gOrbitLength"] = (
                    group_order // int(invariant["stabilizerOrder"])
                )
                if int(invariant["gOrbitLength"]) != 24:
                    raise ArithmeticError("relative invariant orbit is not 24")
                identity = subgroup_identity(group_label, subgroup)
                output.append(
                    {
                        "actionOrdinalWithinSourceGroup": ordinal,
                        "availableCertifiedSourceCount": len(
                            sources_by_group.get(group_label, [])
                        ),
                        "availableCertifiedSourceHashes": sorted(
                            row["coefficientSha256"]
                            for row in sources_by_group.get(group_label, [])
                        ),
                        "coreOrder": core_order,
                        "degree": degree,
                        "existingF6ActionLabel": (
                            target_label in existing_f6_labels
                        ),
                        "groupLabel": group_label,
                        "groupOrder": group_order,
                        "index": 24,
                        "relativeInvariant": invariant,
                        "signatureMap": {
                            str(source_r): sorted(values)
                            for source_r, values in sorted(signature_map.items())
                        },
                        "signatureProfiles": profiles,
                        "sourceT": transitive_t,
                        "subgroupClassIndex": subgroup_class_index,
                        "subgroupClassIdentitySha256": identity,
                        "subgroupOrder": int(libgap.Size(subgroup)),
                        "targetLabel": target_label,
                        "targetOrder": int(libgap.Size(image)),
                        "targetT": target_t,
                    }
                )
                ordinal += 1
            print(
                canonical_json(
                    {
                        "actionRowsSoFar": len(output),
                        "group": group_label,
                        "index24Actions": ordinal,
                        "phase": "action-census",
                    }
                ),
                flush=True,
            )
    return output


def main() -> int:
    started = time.monotonic()
    for path in (SOURCE_OUTPUT, ACTION_OUTPUT, ROUTE_OUTPUT, SUMMARY_OUTPUT):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    sources = source_inventory()
    sources_by_group = defaultdict(list)
    for source in sources:
        sources_by_group[str(source["galoisGroupLabel"])].append(source)
    existing_f6_labels, existing_f6_pairs, f6_counts = existing_f6_inventory()
    live = current_tc0()
    actions = action_census(sources_by_group, existing_f6_labels)

    routes = []
    for action in actions:
        available_sources = sources_by_group.get(str(action["groupLabel"]), [])
        for source in available_sources:
            possible_rs = [
                int(value)
                for value in action["signatureMap"].get(
                    str(int(source["realRoots"])), []
                )
            ]
            possible_pairs = [
                (str(action["targetLabel"]), target_r)
                for target_r in possible_rs
            ]
            hits = sorted(set(possible_pairs).intersection(live))
            if not hits:
                continue
            invariant = action["relativeInvariant"]
            invariant_tier = {
                "squarefree_subset_orbit_reynolds_sum": 0,
                "ternary_monomial_orbit_reynolds_sum": 1,
                "distinct_exponent_orbit_reynolds_sum": 2,
            }[str(invariant["kind"])]
            routes.append(
                {
                    "actionOrdinalWithinSourceGroup": int(
                        action["actionOrdinalWithinSourceGroup"]
                    ),
                    "allPossibleSignaturesCurrentTc0": (
                        set(possible_pairs) <= live
                    ),
                    "coefficientBytes": int(source["coefficientBytes"]),
                    "coefficientLine": str(source["coefficientLine"]),
                    "coefficientSha256": str(source["coefficientSha256"]),
                    "degree": int(source["degree"]),
                    "deterministicTargetSignature": len(possible_rs) == 1,
                    "existingF6ActionLabel": bool(
                        action["existingF6ActionLabel"]
                    ),
                    "galoisGroupLabel": str(source["galoisGroupLabel"]),
                    "invariantTier": invariant_tier,
                    "novelCurrentTc0PairsVsF6Frontier": [
                        {"label": label, "r": r}
                        for label, r in hits
                        if (label, r) not in existing_f6_pairs
                    ],
                    "possibleTargetRs": possible_rs,
                    "provenance": source["provenance"],
                    "realRoots": int(source["realRoots"]),
                    "relativeInvariant": invariant,
                    "sourceFieldDiscriminantAbs": str(
                        source["fieldDiscriminantAbs"]
                    ),
                    "subgroupClassIdentitySha256": str(
                        action["subgroupClassIdentitySha256"]
                    ),
                    "targetLabel": str(action["targetLabel"]),
                    "targetT": int(action["targetT"]),
                    "tc0Hits": [
                        {"label": label, "r": r} for label, r in hits
                    ],
                }
            )
    routes.sort(
        key=lambda row: (
            -int(row["allPossibleSignaturesCurrentTc0"]),
            -int(row["deterministicTargetSignature"]),
            row["invariantTier"],
            int(row["relativeInvariant"]["termCount"]),
            int(row["relativeInvariant"]["totalDegree"]),
            row["coefficientBytes"],
            -len(row["novelCurrentTc0PairsVsF6Frontier"]),
            row["targetT"],
            row["coefficientSha256"],
        )
    )
    for rank, route in enumerate(routes, start=1):
        route["priorityRank"] = rank

    structural_actions = len(actions)
    available_actions = [
        action
        for action in actions
        if int(action["availableCertifiedSourceCount"]) > 0
    ]
    live_pairs = {
        (hit["label"], int(hit["r"]))
        for route in routes
        for hit in route["tc0Hits"]
    }
    novel_pairs = {
        (hit["label"], int(hit["r"]))
        for route in routes
        for hit in route["novelCurrentTc0PairsVsF6Frontier"]
    }
    summary = {
        "actionRows": structural_actions,
        "availableActionRows": len(available_actions),
        "availableDegree8GroupsWithIndex24Actions": sorted(
            {
                action["groupLabel"]
                for action in available_actions
                if int(action["degree"]) == 8
            },
            key=lambda label: int(label.split("T")[1]),
        ),
        "certifiedDegree7SourceCount": 0,
        "certifiedDegree8SourceCount": len(sources),
        "currentTc0Definition": (
            "targets.team_count=0 AND discovered=0 AND nonbaseline AND "
            "locally unowned by any scoreable verification"
        ),
        "currentTc0PairCount": len(live),
        "degree7StructuralActionRows": sum(
            int(action["degree"]) == 7 for action in actions
        ),
        "degree8StructuralActionRows": sum(
            int(action["degree"]) == 8 for action in actions
        ),
        "distinctAvailableSourceGroups": len(sources_by_group),
        "distinctLiveTc0Pairs": len(live_pairs),
        "distinctNovelLiveTc0PairsVsF6Frontier": len(novel_pairs),
        "existingF6Inventory": f6_counts,
        "heavyArithmeticCalls": 0,
        "inputSha256": {
            str(path.relative_to(ROOT)): sha256_path(path)
            for path in (
                DB,
                DEGREE8_BANK,
                DEGREE8_BANK_SUMMARY,
                F6_ISOMORPHISM,
                F6_FRONTIER,
                F6A_ACTIONS,
            )
        },
        "networkCalls": 0,
        "pilotAuthorized": bool(routes),
        "priorityPilot": routes[0] if routes else None,
        "routeCount": len(routes),
        "routeRowsAllPossibleSignaturesCurrentTc0": sum(
            row["allPossibleSignaturesCurrentTc0"] for row in routes
        ),
        "routeRowsDeterministicSignature": sum(
            row["deterministicTargetSignature"] for row in routes
        ),
        "routeRowsNovelActionLabelVsF6": sum(
            not row["existingF6ActionLabel"] for row in routes
        ),
        "runtimeSeconds": time.monotonic() - started,
        "sourceInventory": str(SOURCE_OUTPUT.relative_to(ROOT)),
        "status": (
            "action_intersection_has_executable_routes"
            if routes
            else "complete_no_current_tc0_intersection"
        ),
        "submissionCalls": 0,
    }
    write_jsonl_atomic(SOURCE_OUTPUT, sources)
    write_jsonl_atomic(ACTION_OUTPUT, actions)
    write_jsonl_atomic(ROUTE_OUTPUT, routes)
    write_json_atomic(SUMMARY_OUTPUT, summary)
    print(canonical_json(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

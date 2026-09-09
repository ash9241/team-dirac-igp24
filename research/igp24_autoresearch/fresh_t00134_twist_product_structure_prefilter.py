#!/usr/bin/env python3
"""Light exact Cartesian-product action prefilter for the recovered bank.

No Sage/GAP process is launched.  The exact degree-24 structural census
already records complementary block actions.  Two transverse complementary
systems with faithful kernels prove that the standard group is the direct
product of the two recorded lower-degree actions:

* the two block kernels are normal;
* transverse partitions make their intersection trivial;
* their orders multiply to the full group order; and
* normal subgroups with trivial intersection commute.

That certificate identifies cached 3x8/4x6 product labels and lets us
reintersect the recovered subfield bank with the fully excluded current
tc0/tc1 and T00134 top-10,000 opportunity sets without heavy computation.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import fresh_t00134_twist_route_audit as base


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BANK = DATA / "agent_non12_recoverable_subfields.jsonl"
PILOT = DATA / "agent_non12_simple_compositum_pilot.json"
STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
CURRENT_TARGETS = (
    DATA / "fresh_t00134_twist_current_tc01_heavy_targets_20260730.json"
)
TOP10000_TARGETS = DATA / "fresh_t00134_twist_heavy_targets_20260730.json"
AUDIT = DATA / "fresh_t00134_twist_product_structure_prefilter_20260730.json"
ROUTES = (
    DATA / "fresh_t00134_twist_product_structure_routes_20260730.jsonl"
)


def pair_key(row: dict) -> tuple[str, str]:
    return (
        str(row["first"]["galoisGroup"]["label"]),
        str(row["second"]["galoisGroup"]["label"]),
    )


def structure_product_proofs(
    rows: list[dict], group_specs: dict[tuple[str, str], dict]
) -> tuple[dict[tuple[str, str], list[dict]], dict]:
    proofs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    candidate_structures = 0
    transverse_pairs = 0
    for structure in rows:
        if not structure.get("isSolvable"):
            continue
        systems = structure.get("blockSystems") or []
        forward = []
        reverse = []
        for system in systems:
            shape = str(system["shape"])
            if shape in ("3x8", "4x6"):
                key = (
                    str(system["quotientActionLabel"]),
                    str(system["fiberActionLabel"]),
                )
                if key in group_specs:
                    forward.append((key, system))
            elif shape in ("8x3", "6x4"):
                key = (
                    str(system["fiberActionLabel"]),
                    str(system["quotientActionLabel"]),
                )
                if key in group_specs:
                    reverse.append((key, system))
        if not forward or not reverse:
            continue
        candidate_structures += 1
        for key, left in forward:
            spec = group_specs[key]
            if (
                int(structure["groupOrder"]) != int(spec["productOrder"])
                or int(left["quotientActionOrder"]) != int(spec["leftOrder"])
                or int(left["fiberActionOrder"]) != int(spec["rightOrder"])
                or int(left["blockKernelOrder"]) != int(spec["rightOrder"])
            ):
                continue
            for reverse_key, right in reverse:
                if reverse_key != key:
                    continue
                if (
                    int(right["quotientActionOrder"])
                    != int(spec["rightOrder"])
                    or int(right["fiberActionOrder"])
                    != int(spec["leftOrder"])
                    or int(right["blockKernelOrder"])
                    != int(spec["leftOrder"])
                ):
                    continue
                intersection = sorted(
                    set(map(int, left["seedBlock"]))
                    & set(map(int, right["seedBlock"]))
                )
                if len(intersection) != 1:
                    continue
                transverse_pairs += 1
                proofs[key].append(
                    {
                        "targetLabel": str(structure["label"]),
                        "targetT": int(structure["t"]),
                        "targetOrder": int(structure["groupOrder"]),
                        "forwardSystem": {
                            "shape": str(left["shape"]),
                            "quotientActionLabel": str(
                                left["quotientActionLabel"]
                            ),
                            "fiberActionLabel": str(
                                left["fiberActionLabel"]
                            ),
                            "blockKernelOrder": int(
                                left["blockKernelOrder"]
                            ),
                            "systemSha256": str(left["systemSha256"]),
                        },
                        "reverseSystem": {
                            "shape": str(right["shape"]),
                            "quotientActionLabel": str(
                                right["quotientActionLabel"]
                            ),
                            "fiberActionLabel": str(
                                right["fiberActionLabel"]
                            ),
                            "blockKernelOrder": int(
                                right["blockKernelOrder"]
                            ),
                            "systemSha256": str(right["systemSha256"]),
                        },
                        "transverseSeedIntersection": intersection,
                        "proof": (
                            "complementary faithful normal block kernels have "
                            "trivial intersection by transverse invariant "
                            "partitions; their orders multiply to |G|, hence "
                            "G is their direct product in the Cartesian action"
                        ),
                    }
                )
    # Multiple block-system proofs for one target label are harmless, but two
    # target labels for one natural product action would contradict the exact
    # transitive-library identification and must not be silently accepted.
    ambiguous = {}
    normalized = {}
    for key, values in proofs.items():
        labels = {str(row["targetLabel"]) for row in values}
        if len(labels) != 1:
            ambiguous["|".join(key)] = sorted(labels)
            continue
        normalized[key] = values
    return normalized, {
        "structuresScanned": len(rows),
        "candidateStructuresWithBothDirections": candidate_structures,
        "transverseComplementarySystemPairs": transverse_pairs,
        "ambiguousGroupPairsRejected": ambiguous,
    }


def main() -> int:
    started = time.monotonic()
    if AUDIT.exists() or ROUTES.exists():
        raise FileExistsError("refusing to overwrite product prefilter artifacts")

    bank_rows = base.read_jsonl(BANK)
    unique = {}
    for row in bank_rows:
        unique.setdefault(str(row["coefficientSha256"]), row)
    by_degree = {
        degree: [
            row
            for row in unique.values()
            if int(row["subfieldDegree"]) == degree
        ]
        for degree in (3, 4, 6, 8)
    }
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    pilot_factor_hashes = {
        str(row[side]["coefficientSha256"])
        for row in pilot.get("pilot") or []
        for side in ("first", "second")
    }

    group_specs = {}
    combinations = []
    rejected = Counter()
    for left_degree, right_degree in ((3, 8), (4, 6)):
        for left, right in itertools.product(
            by_degree[left_degree], by_degree[right_degree]
        ):
            if str(left["sourceLabel"]) == str(right["sourceLabel"]):
                rejected["sameAcceptedSourceLabel"] += 1
                continue
            if math.gcd(
                int(left["fieldDiscriminantAbs"]),
                int(right["fieldDiscriminantAbs"]),
            ) != 1:
                rejected["nonCoprimeFactorFieldDiscriminants"] += 1
                continue
            key = (
                str(left["galoisGroup"]["label"]),
                str(right["galoisGroup"]["label"]),
            )
            spec = {
                "leftDegree": left_degree,
                "leftOrder": int(left["galoisGroup"]["order"]),
                "rightDegree": right_degree,
                "rightOrder": int(right["galoisGroup"]["order"]),
                "productOrder": int(left["galoisGroup"]["order"])
                * int(right["galoisGroup"]["order"]),
            }
            old = group_specs.setdefault(key, spec)
            if old != spec:
                raise ValueError(f"inconsistent lower-group specification {key}")
            combinations.append(
                {
                    "shape": f"{left_degree}x{right_degree}",
                    "groupPair": key,
                    "targetR": int(left["realRoots"])
                    * int(right["realRoots"]),
                    "firstCoefficientSha256": str(
                        left["coefficientSha256"]
                    ),
                    "secondCoefficientSha256": str(
                        right["coefficientSha256"]
                    ),
                    "firstSourceLabel": str(left["sourceLabel"]),
                    "secondSourceLabel": str(right["sourceLabel"]),
                    "freshFactorPresentations": (
                        str(left["coefficientSha256"])
                        not in pilot_factor_hashes
                        and str(right["coefficientSha256"])
                        not in pilot_factor_hashes
                    ),
                }
            )
    if (
        len(combinations) != 2542
        or len(group_specs) != 132
        or len(unique) != 397
    ):
        raise ValueError("recovered compositum bank boundary changed")

    structures = base.read_jsonl(STRUCTURES)
    structural_map, structure_meta = structure_product_proofs(
        structures, group_specs
    )

    # Certified pilot fields independently pin their exact natural product
    # labels and supplement low baseline labels absent from the old structural
    # census boundary.
    pilot_map = defaultdict(list)
    for row in pilot.get("pilot") or []:
        pilot_map[pair_key(row)].append(
            {
                "targetLabel": str(row["targetLabel"]),
                "targetT": int(str(row["targetLabel"])[3:]),
                "targetOrder": int(row["targetGroup"]["order"]),
                "pilotIndex": int(row["pilotIndex"]),
                "proof": (
                    "certified coprime-discriminant simple compositum with "
                    "exact Cartesian-product action"
                ),
            }
        )

    action_map = {}
    action_proofs = {}
    conflicts = {}
    for key in group_specs:
        proofs = list(structural_map.get(key, [])) + list(
            pilot_map.get(key, [])
        )
        labels = {str(row["targetLabel"]) for row in proofs}
        if len(labels) > 1:
            conflicts["|".join(key)] = sorted(labels)
            continue
        if labels:
            action_map[key] = next(iter(labels))
            action_proofs[key] = proofs
    if conflicts:
        raise ValueError(f"product-action cache conflicts: {conflicts}")

    current = json.loads(CURRENT_TARGETS.read_text(encoding="utf-8"))
    top10000 = json.loads(TOP10000_TARGETS.read_text(encoding="utf-8"))
    boundaries = {
        "currentTc01": {
            (str(row["label"]), int(row["r"])): row
            for row in current["eligiblePairs"]
        },
        "top10000": {
            (str(row["label"]), int(row["r"])): row
            for row in top10000["top10000EligiblePairs"]
        },
    }

    route_rows = []
    boundary_summaries = {}
    fresh_combinations = [
        row for row in combinations if row["freshFactorPresentations"]
    ]
    for name, targets in boundaries.items():
        by_pair = defaultdict(list)
        raw_combo_hits = 0
        fresh_combo_hits = 0
        for combination in combinations:
            label = action_map.get(tuple(combination["groupPair"]))
            if label is None:
                continue
            pair = (label, int(combination["targetR"]))
            if pair not in targets:
                continue
            raw_combo_hits += 1
            if not combination["freshFactorPresentations"]:
                continue
            fresh_combo_hits += 1
            by_pair[pair].append(combination)
        for pair, hits in sorted(
            by_pair.items(),
            key=lambda item: (
                int(item[0][0][3:]),
                int(item[0][1]),
            ),
        ):
            target = targets[pair]
            group_pairs = sorted(
                {tuple(row["groupPair"]) for row in hits}
            )
            proof_rows = [
                proof
                for group_pair in group_pairs
                for proof in action_proofs[group_pair]
            ]
            best = sorted(
                hits,
                key=lambda row: (
                    row["shape"],
                    row["firstCoefficientSha256"],
                    row["secondCoefficientSha256"],
                ),
            )[:3]
            if name == "currentTc01":
                team_count = int(target["teamCount"])
            else:
                team_count = int(target["kTeams"])
            route_rows.append(
                {
                    "boundary": name,
                    "routeFamily": (
                        "coprime_recovered_subfield_simple_compositum"
                    ),
                    "targetLabel": pair[0],
                    "targetR": pair[1],
                    "teamCount": team_count,
                    "projectedMarginalBase": 2.0 ** (-team_count),
                    "exactFreshFactorCombinationCount": len(hits),
                    "distinctGroupPairs": [
                        list(value) for value in group_pairs
                    ],
                    "representativeFactorPresentations": [
                        {
                            key: value
                            for key, value in row.items()
                            if key
                            not in (
                                "freshFactorPresentations",
                                "targetR",
                                "groupPair",
                            )
                        }
                        for row in best
                    ],
                    "actionProofs": proof_rows,
                    "exactProof": {
                        "factorFields": (
                            "exact recovered lower-degree transitive labels "
                            "and exact factor field discriminants"
                        ),
                        "linearDisjointness": (
                            "coprime field discriminants imply disjoint finite "
                            "ramification of Galois closures; their Galois "
                            "intersection is everywhere unramified and hence Q"
                        ),
                        "degreeAndLabel": (
                            "degree 24 and cached exact transverse "
                            "Cartesian-product action"
                        ),
                        "signature": "r(compositum)=r(first)*r(second)",
                    },
                    "status": (
                        "exact_structural_route_arithmetic_candidate_not_built"
                    ),
                }
            )
        boundary_summaries[name] = {
            "eligiblePairs": len(targets),
            "rawCombinationHitsIncludingPilotTestedFactors": raw_combo_hits,
            "freshFactorCombinationHits": fresh_combo_hits,
            "distinctExactTargetPairs": len(by_pair),
            "hitRatePerFreshBankCombination": (
                fresh_combo_hits / len(fresh_combinations)
                if fresh_combinations
                else 0.0
            ),
            "teamCountDistributionByPair": dict(
                sorted(
                    Counter(
                        int(
                            targets[pair][
                                "teamCount"
                                if name == "currentTc01"
                                else "kTeams"
                            ]
                        )
                        for pair in by_pair
                    ).items()
                )
            ),
        }

    route_rows.sort(
        key=lambda row: (
            row["boundary"] != "currentTc01",
            int(row["teamCount"]),
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
        )
    )
    unresolved = sorted(set(group_specs) - set(action_map))
    audit = {
        "schemaVersion": (
            "fresh-t00134-light-exact-product-structure-prefilter-v1"
        ),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "exact_structural_product_routes"
            if route_rows
            else "zero_mapped_exact_structural_product_routes"
        ),
        "bank": {
            "rawRows": len(bank_rows),
            "distinctFactorHashes": len(unique),
            "factorCounts": {
                str(degree): len(rows)
                for degree, rows in by_degree.items()
            },
            "linearlyDisjointSourceDiverseCombinations": len(combinations),
            "freshCombinationsAfterCertifiedPilotFactorExclusion": len(
                fresh_combinations
            ),
            "certifiedPilotFactorHashesExcluded": len(pilot_factor_hashes),
            "distinctGroupPairs": len(group_specs),
            "rejections": dict(sorted(rejected.items())),
        },
        "actionCache": {
            **structure_meta,
            "groupPairsMappedExactly": len(action_map),
            "groupPairsMappedByStructuralCensus": len(structural_map),
            "groupPairsMappedByCertifiedPilot": len(pilot_map),
            "unresolvedGroupPairs": [list(value) for value in unresolved],
            "unresolvedGroupPairCount": len(unresolved),
            "mappingCoverage": len(action_map) / len(group_specs),
            "exactMapping": [
                {
                    "factorGroupPair": list(key),
                    "targetLabel": action_map[key],
                    "proofCount": len(action_proofs[key]),
                }
                for key in sorted(action_map)
            ],
        },
        "boundaries": boundary_summaries,
        "routeRows": len(route_rows),
        "inputSha256": {
            str(path.relative_to(ROOT)): base.sha256_path(path)
            for path in (
                BANK,
                PILOT,
                STRUCTURES,
                CURRENT_TARGETS,
                TOP10000_TARGETS,
            )
        },
        "runtimeSeconds": time.monotonic() - started,
        "coefficientMaterialIncluded": False,
        "candidateArithmeticCalls": 0,
        "sageCalls": 0,
        "gapCalls": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    base.exclusive_text(
        ROUTES,
        "".join(base.canonical_json(row) + "\n" for row in route_rows),
    )
    base.exclusive_text(
        AUDIT, json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "audit": str(AUDIT.relative_to(ROOT)),
                "auditSha256": base.sha256_path(AUDIT),
                "routes": str(ROUTES.relative_to(ROOT)),
                "routesSha256": base.sha256_path(ROUTES),
                "actionMapCoverage": (
                    f"{len(action_map)}/{len(group_specs)}"
                ),
                "unresolvedGroupPairs": len(unresolved),
                "currentTc01": boundary_summaries["currentTc01"],
                "top10000": boundary_summaries["top10000"],
                "runtimeSeconds": audit["runtimeSeconds"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

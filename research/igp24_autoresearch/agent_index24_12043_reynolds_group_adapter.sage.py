#!/usr/bin/env sage -python
"""Deferred exact group adapter for a 24T12043 subgroup Reynolds invariant.

``--preflight-only`` is Sage/GAP-free.  ``--certify-group-only`` is the future
isolated heavy step: it reconstructs the four sealed subgroups, certifies their
24T12067 coset actions, and searches a bounded squarefree-monomial family for a
smaller Reynolds seed.  It never reads source coefficients, specializes roots,
stages a polynomial, accesses the network, or submits.

The group certificate deliberately keeps ``executableInvariant`` null.  A
later arithmetic worker must still prove an exact root-action alignment and
noncollision of the 24 specialized conjugates.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path

import agent_index24_12043_reynolds_preflight as light


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "agent_index24_12043_reynolds_group_certificate.json"


def subgroup_identity(libgap, subgroup) -> str:
    generators = sorted(str(value) for value in libgap.SmallGeneratingSet(subgroup))
    payload = light.canonical_json(
        {
            "sourceLabel": light.SOURCE["label"],
            "subgroupGenerators": generators,
            "targetLabel": light.TARGET["label"],
        }
    )
    return light.sha256_bytes(payload.encode())


def automorphism_subgroup_orbit(libgap, group, subgroup):
    automorphisms = libgap.AutomorphismGroup(group)
    generators = list(libgap.GeneratorsOfGroup(automorphisms))
    representatives = [subgroup]
    cursor = 0
    while cursor < len(representatives):
        current = representatives[cursor]
        cursor += 1
        for automorphism in generators:
            image = libgap.Image(automorphism, current)
            if not any(
                bool(libgap.IsConjugate(group, image, known))
                for known in representatives
            ):
                representatives.append(image)
                if len(representatives) > 32:
                    raise ArithmeticError("unexpectedly large automorphism orbit")
    return representatives, int(libgap.Size(automorphisms)), len(generators)


def permutation_tuple(libgap, permutation, degree: int = 24) -> tuple[int, ...]:
    image = tuple(
        int(libgap.OnPoints(point, permutation)) - 1
        for point in range(1, degree + 1)
    )
    if sorted(image) != list(range(degree)):
        raise ArithmeticError("GAP element did not induce a degree-24 permutation")
    return image


def permutation_set(libgap, group) -> set[tuple[int, ...]]:
    return {permutation_tuple(libgap, element) for element in libgap.Elements(group)}


def image_subset(
    subset: frozenset[int], permutation: tuple[int, ...]
) -> frozenset[int]:
    return frozenset(permutation[index] for index in subset)


def squarefree_seed_search(
    group_permutations: set[tuple[int, ...]],
    subgroups: list[tuple[str, set[tuple[int, ...]]]],
    maximum_degree: int,
) -> dict | None:
    """Return the first seed in a complete degree/lexicographic bounded search."""
    if not 0 <= maximum_degree <= 12:
        raise ValueError("squarefree search degree must lie in [0,12]")
    ordered_subgroups = sorted(subgroups, key=lambda row: row[0])
    for degree in range(1, maximum_degree + 1):
        for values in itertools.combinations(range(24), degree):
            subset = frozenset(values)
            stabilizer = {
                permutation
                for permutation in group_permutations
                if image_subset(subset, permutation) == subset
            }
            for identity, subgroup in ordered_subgroups:
                if stabilizer.issubset(subgroup):
                    seed = tuple(int(index in subset) for index in range(24))
                    formal = light.exact_reynolds_support_certificate(
                        seed, group_permutations, subgroup
                    )
                    if formal["supportStabilizerOrder"] != light.EXPECTED_SUBGROUP_ORDER:
                        raise ArithmeticError("selected squarefree support is not H-relative")
                    return {
                        "completeSearchThroughDegree": degree - 1,
                        "kind": "squarefree_monomial_reynolds_orbit_sum",
                        "seedExponentVectorSha256": light.sha256_bytes(
                            light.canonical_json(seed).encode()
                        ),
                        "seedSubsetZeroBased": sorted(subset),
                        "selectedAtDegree": degree,
                        "selectedSubgroupClassIdentitySha256": identity,
                        **formal,
                    }
    return None


def universal_fallback(
    group_permutations: set[tuple[int, ...]],
    subgroup_identity_and_elements: tuple[str, set[tuple[int, ...]]],
    complete_squarefree_degree: int,
) -> dict:
    identity, subgroup = subgroup_identity_and_elements
    seed = light.universal_seed_exponents(24)
    formal = light.exact_reynolds_support_certificate(
        seed, group_permutations, subgroup
    )
    if (
        formal["seedStabilizerOrder"] != 1
        or formal["supportCardinality"] != light.EXPECTED_SUBGROUP_ORDER
        or formal["supportStabilizerOrder"] != light.EXPECTED_SUBGROUP_ORDER
    ):
        raise ArithmeticError("universal asymmetric Reynolds fallback failed")
    return {
        "completeSquarefreeSearchThroughDegree": complete_squarefree_degree,
        "kind": "universal_asymmetric_monomial_reynolds_orbit_sum",
        "seedExponentVectorSha256": light.sha256_bytes(
            light.canonical_json(seed).encode()
        ),
        "selectedSubgroupClassIdentitySha256": identity,
        "totalDegree": sum(seed),
        **formal,
    }


def validate_output_path(path: Path) -> None:
    if path.resolve().parent != DATA.resolve():
        raise ValueError("group certificate output escaped the data directory")
    collisions = [candidate for candidate in (path, Path(str(path) + ".tmp")) if candidate.exists()]
    if collisions:
        raise FileExistsError(
            "refusing to overwrite group certificate: " + ", ".join(map(str, collisions))
        )


def publish_no_overwrite(path: Path, payload: bytes) -> str:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    else:
        temporary.unlink()
    return hashlib.sha256(payload).hexdigest()


def certify_group(output: Path, maximum_squarefree_degree: int) -> dict:
    validate_output_path(output)
    input_hashes, _census, sealed, _route = light.validate_sealed_inputs()
    source_receipt = light.source_receipt_state()
    target_state = light.target_state()
    if not target_state["liveNonbaseline"]:
        raise ValueError("24T12067/r24 is no longer live, unowned, and nonbaseline")

    # Deferred intentionally: python3 --preflight-only must not initialize Sage/GAP.
    from sage.all import libgap

    source_group = libgap.TransitiveGroup(24, light.SOURCE["t"])
    target_group = libgap.TransitiveGroup(24, light.TARGET["t"])
    if (
        int(libgap.Size(source_group)) != light.EXPECTED_SOURCE_ORDER
        or int(libgap.TransitiveIdentification(source_group)) != light.SOURCE["t"]
        or int(libgap.Size(target_group)) != light.EXPECTED_SOURCE_ORDER
        or int(libgap.TransitiveIdentification(target_group)) != light.TARGET["t"]
    ):
        raise ArithmeticError("standard source/target group identity changed")
    stable_isomorphism = libgap.eval(
        'function(G,H) return IsomorphismGroups(G,H:forcetest:="old"); end'
    )
    isomorphism = stable_isomorphism(source_group, target_group)
    if isomorphism == libgap.fail:
        raise ArithmeticError("sealed source/target groups are no longer isomorphic")
    initial = libgap.PreImage(isomorphism, libgap.Stabilizer(target_group, 1))
    representatives, automorphism_order, automorphism_generator_count = (
        automorphism_subgroup_orbit(libgap, source_group, initial)
    )
    if (
        automorphism_order != light.EXPECTED_AUTOMORPHISM_ORDER
        or len(representatives) != len(light.EXPECTED_SUBGROUP_IDENTITIES)
    ):
        raise ArithmeticError("reconstructed subgroup automorphism orbit changed")

    group_permutations = permutation_set(libgap, source_group)
    if len(group_permutations) != light.EXPECTED_SOURCE_ORDER:
        raise ArithmeticError("standard source action is not faithful")
    subgroup_rows = []
    subgroup_elements = []
    for subgroup in representatives:
        identity = subgroup_identity(libgap, subgroup)
        cosets = libgap.RightCosets(source_group, subgroup)
        action = libgap.ActionHomomorphism(source_group, cosets, libgap.OnRight)
        image = libgap.Image(action)
        row = {
            "cosetActionT": int(libgap.TransitiveIdentification(image)),
            "coreOrder": int(libgap.Size(libgap.Core(source_group, subgroup))),
            "index": int(libgap.Index(source_group, subgroup)),
            "kernelOrder": int(libgap.Size(libgap.Kernel(action))),
            "subgroupClassIdentitySha256": identity,
            "subgroupOrder": int(libgap.Size(subgroup)),
        }
        if (
            row["cosetActionT"] != light.TARGET["t"]
            or row["coreOrder"] != 1
            or row["index"] != light.EXPECTED_INDEX
            or row["kernelOrder"] != 1
            or row["subgroupOrder"] != light.EXPECTED_SUBGROUP_ORDER
        ):
            raise ArithmeticError("one reconstructed coset action failed exact checks")
        elements = permutation_set(libgap, subgroup)
        if len(elements) != light.EXPECTED_SUBGROUP_ORDER:
            raise ArithmeticError("reconstructed subgroup permutation order changed")
        subgroup_rows.append(row)
        subgroup_elements.append((identity, elements))

    observed_identities = {
        row["subgroupClassIdentitySha256"] for row in subgroup_rows
    }
    sealed_identities = {
        row["subgroupClassIdentitySha256"] for row in sealed["subgroupClasses"]
    }
    if observed_identities != light.EXPECTED_SUBGROUP_IDENTITIES or observed_identities != sealed_identities:
        raise ArithmeticError("reconstructed subgroup identities do not match the seal")

    construction = squarefree_seed_search(
        group_permutations, subgroup_elements, maximum_squarefree_degree
    )
    if construction is None:
        construction = universal_fallback(
            group_permutations,
            min(subgroup_elements, key=lambda row: row[0]),
            maximum_squarefree_degree,
        )
    if (
        construction["cosetConjugateCount"] != light.EXPECTED_INDEX
        or not construction["formalConjugatesDistinct"]
    ):
        raise ArithmeticError("formal Reynolds construction did not separate 24 cosets")

    payload = {
        "coefficientMaterialIncluded": False,
        "executableInvariant": None,
        "family": "INDEX24_SUBGROUP_REYNOLDS_ORBIT_INVARIANT",
        "formalInvariant": construction,
        "groupCertificate": {
            "automorphismGeneratorCount": automorphism_generator_count,
            "automorphismGroupOrder": automorphism_order,
            "sourceOrder": len(group_permutations),
            "sourceT": light.SOURCE["t"],
            "subgroups": sorted(
                subgroup_rows,
                key=lambda row: row["subgroupClassIdentitySha256"],
            ),
        },
        "heavyArithmeticCalls": 1,
        "inputSha256": input_hashes,
        "networkCalls": 0,
        "remainingExecutionGates": [
            "exactRootActionAlignment",
            "specializedConjugatesDistinct",
            "exactIntegralResolvent",
            "resolventIs24T12067_r24",
        ],
        "schemaVersion": "index24-12043-reynolds-group-certificate-v1",
        "source": source_receipt,
        "status": "formal_group_invariant_certified_arithmetic_specialization_blocked",
        "submissionCalls": 0,
        "target": {**light.TARGET, "stateAtPublish": target_state},
    }
    rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    artifact_hash = publish_no_overwrite(output, rendered)
    return {
        "artifact": str(output.relative_to(ROOT)),
        "artifactSha256": artifact_hash,
        "coefficientMaterialIncluded": False,
        "event": "index24_12043_reynolds_group_certificate_complete",
        "executableInvariant": None,
        "heavyArithmeticCalls": 1,
        "networkCalls": 0,
        "status": payload["status"],
        "submissionCalls": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--certify-group-only", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--maximum-squarefree-degree", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preflight_only:
        event = {
            **light.preflight(),
            "event": "index24_12043_reynolds_group_adapter_preflight_ok",
            "maximumSquarefreeDegree": args.maximum_squarefree_degree,
            "plannedOutput": str(args.output.relative_to(ROOT)),
        }
    else:
        event = certify_group(args.output, args.maximum_squarefree_degree)
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

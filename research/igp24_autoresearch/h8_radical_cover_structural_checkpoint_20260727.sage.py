#!/usr/bin/env sage
"""Exact structural checkpoint for degree-eight radical covers.

Two independent proper-subgroup families inside S3 wr H are censused for
every transitive degree-eight group H=8T1,...,8T50:

1. V : (H x <-1>) for every nonzero H-invariant V <= F_3^8;
2. the kernels of the local-discriminant sign and of its product with the
   block-permutation sign.

Every resulting transitive degree-24 action is identified exactly in GAP.
Its complex-conjugation signatures are derived from the involutions of H and
the fiber action, then intersected with the pinned local tc0 frontier.  Exact
locally recovered degree-eight presentations are attached only when their
signature matches the required quotient involution.

There is no arithmetic candidate generation, network access, or submission
path in this checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import GF, VectorSpace, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
SOURCES = DATA / "agent_non12_all_degree8_subfields.jsonl"
ROWS = DATA / "h8_radical_cover_structural_checkpoint_20260727.jsonl"
SUMMARY = DATA / "h8_radical_cover_structural_checkpoint_20260727.json"
FIELD = GF(3)
AMBIENT = VectorSpace(FIELD, 8)
POINTS8 = libgap.eval("[1..8]")


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def canonical(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def subspace_key(space) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(int(value) for value in row)
        for row in space.basis_matrix().rows()
    )


def permute_vector(vector, permutation):
    output = [FIELD.zero()] * 8
    for index in range(1, 9):
        image = int(libgap.OnPoints(index, permutation))
        output[image - 1] = vector[index - 1]
    return AMBIENT(output)


def cyclic_span(vector, generators):
    space = AMBIENT.subspace([vector])
    while True:
        basis = list(space.basis())
        expanded = AMBIENT.subspace(
            basis
            + [
                permute_vector(value, generator)
                for value in basis
                for generator in generators
            ]
        )
        if expanded.dimension() == space.dimension():
            return space
        space = expanded


def invariant_submodules(group):
    gap_module = libgap.PermutationGModule(group, libgap.GF(3))
    bases = list(libgap.eval("MTX.BasesSubmodules")(gap_module))
    modules = {}
    for basis in bases:
        space = AMBIENT.subspace(
            [[int(value) for value in row] for row in basis]
        )
        modules[subspace_key(space)] = space
    generators = list(libgap.GeneratorsOfGroup(group))
    for space in modules.values():
        for generator in generators:
            if any(
                permute_vector(value, generator) not in space
                for value in space.basis()
            ):
                raise ArithmeticError("submodule closure verification failed")
    return modules


def perm(images):
    return libgap.PermList([int(value) for value in images])


def translation(vector):
    images = []
    for block in range(8):
        shift = int(vector[block])
        for fiber in range(3):
            images.append(3 * block + ((fiber + shift) % 3) + 1)
    return perm(images)


def local_cycle(block):
    images = list(range(1, 25))
    offset = 3 * block
    images[offset : offset + 3] = [offset + 2, offset + 3, offset + 1]
    return perm(images)


def local_swap(block):
    images = list(range(1, 25))
    offset = 3 * block
    images[offset : offset + 3] = [offset + 2, offset + 1, offset + 3]
    return perm(images)


def lift_outer(generator):
    images = []
    for block in range(1, 9):
        image_block = int(libgap.OnPoints(block, generator))
        images.extend(
            (image_block - 1) * 3 + fiber for fiber in range(1, 4)
        )
    return perm(images)


def diagonal_inversion():
    images = []
    for block in range(8):
        images.extend([3 * block + 1, 3 * block + 3, 3 * block + 2])
    return perm(images)


def module_action(outer, module):
    group = libgap.Group(
        [translation(vector) for vector in module.basis()]
        + [
            lift_outer(generator)
            for generator in libgap.GeneratorsOfGroup(outer)
        ]
        + [diagonal_inversion()]
    )
    expected = (
        (3 ** int(module.dimension())) * int(libgap.Size(outer)) * 2
    )
    if int(libgap.Size(group)) != expected:
        raise ArithmeticError("ternary-module action order mismatch")
    if not bool(libgap.IsTransitive(group, libgap.eval("[1..24]"))):
        raise ArithmeticError("nonzero ternary module gave intransitive action")
    return group


def character_actions(outer):
    order = int(libgap.Size(outer))
    outer_generators = list(libgap.GeneratorsOfGroup(outer))
    lifted = [lift_outer(generator) for generator in outer_generators]
    cycles = [local_cycle(block) for block in range(8)]
    swaps = [local_swap(block) for block in range(8)]
    local_even = cycles + [
        swaps[block] * swaps[7] for block in range(7)
    ]
    local_kernel = libgap.Group(local_even + lifted)
    expected = (6**8) * order // 2
    if int(libgap.Size(local_kernel)) != expected:
        raise ArithmeticError("local-sign kernel order mismatch")

    outer_even = libgap.Intersection(outer, libgap.AlternatingGroup(8))
    sign_nontrivial = int(libgap.Size(outer_even)) * 2 == order
    actions = [("local_sign_product_kernel", local_kernel)]
    if sign_nontrivial:
        lifted_even = [
            lift_outer(generator)
            for generator in libgap.GeneratorsOfGroup(outer_even)
        ]
        odd = next(
            generator
            for generator in outer_generators
            if int(libgap.SignPerm(generator)) == -1
        )
        product_kernel = libgap.Group(
            local_even + lifted_even + [swaps[7] * lift_outer(odd)]
        )
        if int(libgap.Size(product_kernel)) != expected:
            raise ArithmeticError("product-sign kernel order mismatch")
        actions.append(("local_times_source_sign_kernel", product_kernel))
    else:
        actions.append(("local_times_source_sign_kernel", local_kernel))
    return sign_nontrivial, actions


def outer_real_signatures(outer) -> list[int]:
    values = {
        8 - int(libgap.NrMovedPoints(libgap.Representative(cls)))
        for cls in libgap.ConjugacyClasses(outer)
        if int(libgap.Order(libgap.Representative(cls))) in (1, 2)
    }
    return sorted(values)


def module_target_signatures(source_values) -> list[int]:
    values = set()
    for source_r in source_values:
        if source_r == 8:
            values.update((8, 24))
        else:
            values.update((source_r, 3 * source_r))
    return sorted(values)


def character_target_signatures(family, source_values) -> dict[int, list[int]]:
    result = {}
    for source_r in source_values:
        values = []
        for k in range(source_r + 1):
            local_parity = (source_r - k) % 2
            outer_parity = ((8 - source_r) // 2) % 2
            if family == "local_sign_product_kernel":
                allowed = local_parity == 0
            elif family == "local_times_source_sign_kernel":
                allowed = (local_parity + outer_parity) % 2 == 0
            else:
                raise ValueError(family)
            if allowed:
                values.append(source_r + 2 * k)
        result[source_r] = values
    return result


def live_frontier(connection):
    baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
    owned = set(
        connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
        )
    )
    targets = {}
    frontier = defaultdict(set)
    for label, r, team_count, discovered, generated_at in connection.execute(
        "SELECT label,r,team_count,discovered,generated_at FROM targets"
    ):
        key = (str(label), int(r))
        gate = {
            "teamCount": int(team_count),
            "discovered": int(discovered),
            "generatedAt": generated_at,
            "baseline": key in baseline,
            "owned": key in owned,
        }
        targets[key] = gate
        if (
            gate["teamCount"] == 0
            and gate["discovered"] == 0
            and not gate["baseline"]
            and not gate["owned"]
        ):
            frontier[key[0]].add(key[1])
    return targets, frontier


def main() -> int:
    source_rows = [
        json.loads(line)
        for line in SOURCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    local = defaultdict(list)
    for row in source_rows:
        source_t = int(str(row["galoisGroup"]["label"]).split("T")[1])
        local[(source_t, int(row["realRoots"]))].append(row)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    targets, frontier = live_frontier(connection)
    connection.close()

    rows = []
    structural_hits = []
    local_hits = []
    for source_t in range(1, 51):
        outer = libgap.TransitiveGroup(8, source_t)
        source_signatures = outer_real_signatures(outer)
        modules = invariant_submodules(outer)
        module_rows = []
        for key, module in sorted(
            modules.items(), key=lambda item: (item[1].dimension(), item[0])
        ):
            if int(module.dimension()) == 0:
                continue
            action = module_action(outer, module)
            target_t = int(libgap.TransitiveIdentification(action))
            target_label = f"24T{target_t}"
            signatures = module_target_signatures(source_signatures)
            hits = sorted(frontier.get(target_label, set()) & set(signatures))
            route_rows = []
            for source_r in source_signatures:
                derived = (
                    [8, 24]
                    if source_r == 8
                    else sorted({source_r, 3 * source_r})
                )
                for target_r in sorted(set(derived) & set(hits)):
                    route = {
                        "family": "ternary_invariant_module",
                        "sourceLabel": f"8T{source_t}",
                        "sourceR": source_r,
                        "targetLabel": target_label,
                        "targetR": target_r,
                        "kernelDimension": int(module.dimension()),
                        "localPresentationCount": len(
                            local.get((source_t, source_r), [])
                        ),
                        "gate": targets[(target_label, target_r)],
                    }
                    structural_hits.append(route)
                    route_rows.append(route)
                    if route["localPresentationCount"]:
                        local_hits.append(route)
            module_rows.append(
                {
                    "basis": [list(row) for row in key],
                    "kernelDimension": int(module.dimension()),
                    "order": int(libgap.Size(action)),
                    "targetLabel": target_label,
                    "targetT": target_t,
                    "attainableSignatures": signatures,
                    "liveRoutes": route_rows,
                }
            )

        sign_nontrivial, actions = character_actions(outer)
        character_rows = []
        seen_character = set()
        for family, action in actions:
            target_t = int(libgap.TransitiveIdentification(action))
            target_label = f"24T{target_t}"
            signature_map = character_target_signatures(
                family, source_signatures
            )
            key = (target_t, tuple(
                (r, tuple(values)) for r, values in sorted(signature_map.items())
            ))
            equivalent = key in seen_character
            seen_character.add(key)
            hits = sorted(
                frontier.get(target_label, set())
                & {
                    value
                    for values in signature_map.values()
                    for value in values
                }
            )
            route_rows = []
            for source_r, values in sorted(signature_map.items()):
                for target_r in sorted(set(values) & set(hits)):
                    route = {
                        "family": family,
                        "sourceLabel": f"8T{source_t}",
                        "sourceR": source_r,
                        "targetLabel": target_label,
                        "targetR": target_r,
                        "localPresentationCount": len(
                            local.get((source_t, source_r), [])
                        ),
                        "gate": targets[(target_label, target_r)],
                    }
                    structural_hits.append(route)
                    route_rows.append(route)
                    if route["localPresentationCount"]:
                        local_hits.append(route)
            character_rows.append(
                {
                    "family": family,
                    "equivalentToPrevious": equivalent,
                    "order": int(libgap.Size(action)),
                    "targetLabel": target_label,
                    "targetT": target_t,
                    "attainableSignaturesBySourceR": {
                        str(r): values for r, values in sorted(signature_map.items())
                    },
                    "liveRoutes": route_rows,
                }
            )

        row = {
            "schemaVersion": "h8-radical-cover-source-v1",
            "sourceLabel": f"8T{source_t}",
            "sourceT": source_t,
            "sourceOrder": int(libgap.Size(outer)),
            "sourcePermutationSignNontrivial": sign_nontrivial,
            "possibleSourceSignatures": source_signatures,
            "locallyRepresentedSourceSignatures": sorted(
                r for (t, r) in local if t == source_t
            ),
            "invariantModuleCountIncludingZero": len(modules),
            "nonzeroInvariantModules": module_rows,
            "characterKernels": character_rows,
        }
        rows.append(row)
        atomic_text(
            ROWS,
            "".join(canonical(item) + "\n" for item in rows),
        )
        print(
            json.dumps(
                {
                    "sourceLabel": row["sourceLabel"],
                    "modules": len(module_rows),
                    "structuralHits": sum(
                        len(item["liveRoutes"]) for item in module_rows
                    )
                    + sum(
                        len(item["liveRoutes"]) for item in character_rows
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    pairs = sorted(
        {(row["targetLabel"], row["targetR"]) for row in structural_hits},
        key=lambda pair: (int(pair[0][3:]), pair[1]),
    )
    local_pairs = sorted(
        {(row["targetLabel"], row["targetR"]) for row in local_hits},
        key=lambda pair: (int(pair[0][3:]), pair[1]),
    )
    summary = {
        "schemaVersion": "h8-radical-cover-structural-checkpoint-v1",
        "degree8Groups": 50,
        "representedLocalDegree8Groups": len({t for t, _r in local}),
        "representedLocalSignatureClasses": len(local),
        "nonzeroInvariantModules": sum(
            len(row["nonzeroInvariantModules"]) for row in rows
        ),
        "distinctTernaryTargetLabels": len(
            {
                module["targetLabel"]
                for row in rows
                for module in row["nonzeroInvariantModules"]
            }
        ),
        "characterKernelActions": sum(
            len(row["characterKernels"]) for row in rows
        ),
        "structuralTc0RouteCount": len(structural_hits),
        "structuralTc0PairCount": len(pairs),
        "structuralTc0Pairs": [
            {"label": label, "r": r} for label, r in pairs
        ],
        "localActionableRouteCount": len(local_hits),
        "localActionablePairCount": len(local_pairs),
        "localActionablePairs": [
            {"label": label, "r": r} for label, r in local_pairs
        ],
        "structuralRoutes": structural_hits,
        "localActionableRoutes": local_hits,
        "completenessProof": {
            "modules": (
                "GAP MeatAxe MTX.BasesSubmodules enumerated the complete "
                "submodule lattice of the exact F3 permutation module; every "
                "returned basis was independently checked for H-invariance"
            ),
            "moduleSignatures": (
                "for an outer involution with r fixed blocks, the two "
                "cyclotomic lifts fix r or 3r points; identity gives 8 or 24"
            ),
            "characterSignatures": (
                "with k identity fibers over r fixed blocks, r+2k points "
                "are fixed; local parity is r-k and outer parity is (8-r)/2"
            ),
        },
        "rows": str(ROWS.relative_to(ROOT)),
        "rowsSha256": hashlib.sha256(ROWS.read_bytes()).hexdigest(),
        "targetSnapshotGeneratedAtMax": max(
            gate["generatedAt"] for gate in targets.values()
        ),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

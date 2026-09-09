#!/usr/bin/env sage -python
"""Census all tailored H-invariant ternary kernels in degree 24."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import GF, VectorSpace, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCES = DATA / "agent_non12_all_degree8_subfields.jsonl"
DB = DATA / "ledger.sqlite3"
ROWS = DATA / "c3_invariant_submodule_lift_census_20260727.jsonl"
OUTPUT = DATA / "c3_invariant_submodule_lift_census_20260727.json"
FIELD = GF(3)
AMBIENT = VectorSpace(FIELD, 8)


def canonical(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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
        images = [
            permute_vector(value, generator)
            for value in basis
            for generator in generators
        ]
        expanded = AMBIENT.subspace(basis + images)
        if expanded.dimension() == space.dimension():
            return space
        space = expanded


def invariant_submodules(group):
    generators = list(libgap.GeneratorsOfGroup(group))
    cyclic = {}
    for coordinates in AMBIENT:
        if not coordinates:
            continue
        first = next(value for value in coordinates if value)
        if first != 1:
            continue
        module = cyclic_span(coordinates, generators)
        cyclic[subspace_key(module)] = module

    zero = AMBIENT.subspace([])
    modules = {subspace_key(zero): zero, **cyclic}
    frontier = list(cyclic.values())
    cursor = 0
    sum_additions = 0
    while cursor < len(frontier):
        left = frontier[cursor]
        cursor += 1
        for right in list(modules.values()):
            combined = left + right
            key = subspace_key(combined)
            if key not in modules:
                modules[key] = combined
                frontier.append(combined)
                sum_additions += 1

    for module in modules.values():
        for generator in generators:
            for basis_value in module.basis():
                if permute_vector(basis_value, generator) not in module:
                    raise ValueError("enumerated subspace is not H-invariant")
    return {
        "cyclic": cyclic,
        "modules": modules,
        "sumAdditions": sum_additions,
    }


def perm_from_images(images):
    return libgap.PermList(libgap(images))


def lifted_group(source_group, module):
    generators = []
    for vector in module.basis():
        images = []
        for block in range(8):
            shift = int(vector[block])
            for fiber in range(3):
                images.append(3 * block + ((fiber + shift) % 3) + 1)
        generators.append(perm_from_images(images))

    for source_generator in libgap.GeneratorsOfGroup(source_group):
        images = []
        for block in range(1, 9):
            image_block = int(libgap.OnPoints(block, source_generator))
            for fiber in range(3):
                images.append(3 * (image_block - 1) + fiber + 1)
        generators.append(perm_from_images(images))

    images = []
    for block in range(8):
        images.extend([3 * block + 1, 3 * block + 3, 3 * block + 2])
    generators.append(perm_from_images(images))
    return libgap.Group(generators)


def live_frontier(connection):
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
        )
    }
    frontier = defaultdict(set)
    targets = {}
    for label, r, team_count, discovered, generated_at in connection.execute(
        "SELECT label,r,team_count,discovered,generated_at FROM targets"
    ):
        pair = (str(label), int(r))
        targets[pair] = {
            "teamCount": int(team_count),
            "discovered": int(discovered),
            "generatedAt": str(generated_at),
            "baseline": pair in baseline,
            "owned": pair in owned,
        }
        if (
            int(team_count) == 0
            and int(discovered) == 0
            and pair not in baseline
            and pair not in owned
        ):
            frontier[str(label)].add(int(r))
    return frontier, targets


def attainable_target_signatures(source_r: int) -> list[int]:
    # In V semidirect (H x <-1>), a quotient involution h with source_r
    # fixed blocks has two canonical lifts.  (0,h,+1) fixes all three fiber
    # points above each fixed block, while (0,h,-1) fixes exactly one.
    # For h=1, these are the identity and diagonal inversion.
    if source_r == 8:
        return [8, 24]
    return sorted({source_r, 3 * source_r})


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    source_rows = [
        json.loads(line)
        for line in SOURCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    signatures = defaultdict(set)
    presentations = defaultdict(set)
    for row in source_rows:
        label = str(row["galoisGroup"]["label"])
        source_t = int(label.removeprefix("8T"))
        signatures[source_t].add(int(row["realRoots"]))
        presentations[(source_t, int(row["realRoots"]))].add(
            str(row["coefficientSha256"])
        )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    frontier, targets = live_frontier(connection)
    connection.close()

    completed_rows = []
    if ROWS.exists():
        completed_rows = [
            json.loads(line)
            for line in ROWS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    completed = {int(row["sourceT"]) for row in completed_rows}
    rows = list(completed_rows)

    for source_t in sorted(signatures):
        if source_t in completed:
            continue
        source_group = libgap.TransitiveGroup(8, source_t)
        module_census = invariant_submodules(source_group)
        module_rows = []
        routes = []
        for key, module in sorted(
            module_census["modules"].items(),
            key=lambda item: (item[1].dimension(), item[0]),
        ):
            dimension = int(module.dimension())
            if dimension == 0:
                continue
            target_group = lifted_group(source_group, module)
            if not bool(libgap.IsTransitive(target_group)):
                raise ValueError("nonzero invariant kernel did not act transitively")
            expected_order = (
                (3**dimension) * int(libgap.Size(source_group)) * 2
            )
            actual_order = int(libgap.Size(target_group))
            if actual_order != expected_order:
                raise ValueError("tailored lift order mismatch")
            target_t = int(libgap.TransitiveIdentification(target_group))
            target_label = f"24T{target_t}"
            attainable = {}
            module_routes = []
            for source_r in sorted(signatures[source_t]):
                target_r_values = attainable_target_signatures(source_r)
                attainable[str(source_r)] = target_r_values
                for target_r in target_r_values:
                    if target_r not in frontier.get(target_label, set()):
                        continue
                    route = {
                        "availableSourcePresentations": len(
                            presentations[(source_t, source_r)]
                        ),
                        "kernelCodimension": 8 - dimension,
                        "kernelDimension": dimension,
                        "sourceLabel": f"8T{source_t}",
                        "sourceR": source_r,
                        "sourceT": source_t,
                        "targetLabel": target_label,
                        "targetR": target_r,
                        "targetT": target_t,
                    }
                    routes.append(route)
                    module_routes.append(route)
            module_rows.append(
                {
                    "attainableTargetRBySourceR": attainable,
                    "basis": [list(row) for row in key],
                    "kernelCodimension": 8 - dimension,
                    "kernelDimension": dimension,
                    "liveRoutes": module_routes,
                    "previousGenericFamily": dimension in (7, 8),
                    "targetLabel": target_label,
                    "targetOrder": actual_order,
                    "targetT": target_t,
                }
            )
        row = {
            "schemaVersion": "c3-invariant-submodule-source-v1",
            "completenessProof": (
                "Every H-submodule is the sum of the cyclic H-spans of its "
                "nonzero vectors. The catalog contains the cyclic H-span of "
                "every projective vector and is closed under subspace sums."
            ),
            "cyclicModuleCount": len(module_census["cyclic"]),
            "invariantModuleCountIncludingZero": len(
                module_census["modules"]
            ),
            "modules": module_rows,
            "nonzeroInvariantModuleCount": len(module_rows),
            "routes": routes,
            "sourceLabel": f"8T{source_t}",
            "sourceOrder": int(libgap.Size(source_group)),
            "sourceR": sorted(signatures[source_t]),
            "sourceT": source_t,
            "sumClosureAdditions": module_census["sumAdditions"],
        }
        rows.append(row)
        atomic_write(
            ROWS,
            "".join(canonical(value) + "\n" for value in rows).encode(),
        )
        print(
            json.dumps(
                {
                    "event": "checkpoint",
                    "liveRoutes": len(routes),
                    "modules": len(module_rows),
                    "sourceLabel": f"8T{source_t}",
                },
                sort_keys=True,
            ),
            flush=True,
        )

    all_routes = [
        route for row in rows for route in row.get("routes", [])
    ]
    ranked = sorted(
        all_routes,
        key=lambda route: (
            route["kernelCodimension"],
            -route["availableSourcePresentations"],
            route["targetT"],
            route["targetR"],
            route["sourceT"],
            route["sourceR"],
        ),
    )
    summary = {
        "schemaVersion": "c3-invariant-submodule-lift-census-v1",
        "mechanism": (
            "all nonzero H-invariant V <= F3^8 in "
            "V semidirect (H x diagonal inversion)"
        ),
        "sourceGroups": len(rows),
        "sourceSignatureClasses": sum(len(row["sourceR"]) for row in rows),
        "nonzeroInvariantModules": sum(
            row["nonzeroInvariantModuleCount"] for row in rows
        ),
        "distinctTargetLabels": len(
            {
                module["targetLabel"]
                for row in rows
                for module in row["modules"]
            }
        ),
        "currentTc0RouteCount": len(ranked),
        "currentTc0Routes": ranked,
        "rows": str(ROWS.relative_to(ROOT)),
        "rowsSha256": sha_bytes(ROWS.read_bytes()),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    atomic_write(OUTPUT, payload)
    print(
        json.dumps(
            {
                "currentTc0RouteCount": len(ranked),
                "distinctTargetLabels": summary["distinctTargetLabels"],
                "nonzeroInvariantModules": summary[
                    "nonzeroInvariantModules"
                ],
                "output": str(OUTPUT.relative_to(ROOT)),
                "outputSha256": sha_bytes(payload),
                "sourceGroups": len(rows),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

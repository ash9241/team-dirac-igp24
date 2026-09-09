#!/usr/bin/env sage -python
"""Exact action census for higher conjugate-subset Kummer products.

For a certified even source q(x^2), write beta_0,...,beta_11 for the roots
of q and retain its exact signed two-block action.  If an H-orbit O of
k-subsets has length twelve, the elements

    h_S = product(beta_i for i in S),  S in O,

are the conjugates of a degree-12 element.  Their squareclasses are obtained
from the source Kummer module by the displayed H-equivariant GF(2) incidence
map.  The induced signed action on the roots +/-sqrt(h_S) is therefore exact.

This census covers any requested k from 3 through 11.  Complement sizes
k=7,...,11 retain the exact action but add the rational norm squareclass,
so they can produce genuinely different quadratic twists and signatures.
It performs no arithmetic resolvent work, network calls, or submissions.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

from sage.all import GF, Matrix, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_HISTORICAL = DATA / "agent_gold_c_lower_kummer_historical_census.jsonl"
DEFAULT_GOLD = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
DEFAULT_ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_c_lower_kummer_subset_product_summary.json"


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def profile_key(system: dict, source_order: int) -> tuple:
    block_order = int(system["blockActionOrder"])
    return (
        int(system["blockActionT12"]),
        source_order // block_order,
        bool(system["flipInSource"]),
        str(system["targetLabel"]),
        int(system["targetOrder"]) // block_order,
    )


def block_action_data(
    blocks: list[tuple[int, int]], permutation
) -> tuple[list[int], list[int]]:
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def subset_orbits(
    block_generators: list[list[int]], subset_size: int
) -> list[list[tuple[int, ...]]]:
    remaining = set(itertools.combinations(range(12), subset_size))
    orbits = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            subset = queue.popleft()
            for permutation in block_generators:
                image = tuple(sorted(permutation[index] for index in subset))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        orbits.append(sorted(orbit))
    return orbits


def induced_permutation(
    orbit: list[tuple[int, ...]],
    block_permutation: list[int],
    sign_vector: list[int],
):
    position = {subset: index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        image_subset = tuple(sorted(block_permutation[index] for index in subset))
        target = position[image_subset]
        sign = sum(sign_vector[index] for index in subset) % 2
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def orbit_permutation(
    orbit: list[tuple[int, ...]], block_permutation: list[int]
):
    position = {subset: index for index, subset in enumerate(orbit)}
    return libgap.PermList(
        [
            position[
                tuple(sorted(block_permutation[index] for index in subset))
            ]
            + 1
            for subset in orbit
        ]
    )


def actions_for_system(source_t: int, system: dict, subset_sizes: tuple[int, ...]) -> list[dict]:
    source_group = libgap.TransitiveGroup(24, source_t)
    generators = list(libgap.GeneratorsOfGroup(source_group))
    blocks = [tuple(sorted(int(value) for value in block)) for block in system["blocks"]]
    generator_data = [block_action_data(blocks, generator) for generator in generators]
    block_generators = [row[0] for row in generator_data]
    source_quotient_order = int(system["blockActionOrder"])
    rows = []

    for subset_size in subset_sizes:
        for orbit in subset_orbits(block_generators, subset_size):
            if len(orbit) != 12:
                continue
            induced_generators = [
                induced_permutation(orbit, block_permutation, sign_vector)
                for block_permutation, sign_vector in generator_data
            ]
            target_group = libgap.Group(induced_generators)
            if not bool(libgap.IsTransitive(target_group, libgap.eval("[1..24]"))):
                continue
            target_order = int(libgap.Size(target_group))
            induced_quotient_group = libgap.Group(
                [
                    orbit_permutation(orbit, block_permutation)
                    for block_permutation, _sign_vector in generator_data
                ]
            )
            induced_quotient_order = int(libgap.Size(induced_quotient_group))
            if target_order % induced_quotient_order:
                raise ValueError("induced subset-product action has incompatible induced quotient order")
            kernel_order = target_order // induced_quotient_order
            if kernel_order <= 0 or kernel_order & (kernel_order - 1):
                raise ValueError("induced subset-product block kernel is not a two-power")
            incidence = Matrix(
                GF(2),
                12,
                12,
                lambda row, column: int(column in orbit[row]),
            )
            source_signature_to_targets = defaultdict(set)
            for conjugacy_class in libgap.ConjugacyClasses(source_group):
                representative = libgap.Representative(conjugacy_class)
                if int(libgap.Order(representative)) not in (1, 2):
                    continue
                source_r = 24 - int(libgap.NrMovedPoints(representative))
                block_permutation, sign_vector = block_action_data(blocks, representative)
                induced = induced_permutation(orbit, block_permutation, sign_vector)
                target_r = 24 - int(libgap.NrMovedPoints(induced))
                source_signature_to_targets[source_r].add(target_r)
            target_t = int(libgap.TransitiveIdentification(target_group))
            rows.append(
                {
                    "incidenceMatrixRank": int(incidence.rank()),
                    "incidenceRows": [
                        [int(value) for value in incidence.row(index)]
                        for index in range(12)
                    ],
                    "mechanismCertificate": (
                        "h_S=product(beta_i for i in S); the twelve subsets form "
                        "one quotient-group orbit and the displayed GF(2) incidence "
                        "matrix is the exact H-equivariant map on conjugate squareclasses"
                    ),
                    "inducedQuotientOrder": induced_quotient_order,
                    "inducedQuotientT12": int(
                        libgap.TransitiveIdentification(induced_quotient_group)
                    ),
                    "quotientT12": int(system["blockActionT12"]),
                    "sourceQuotientOrder": source_quotient_order,
                    "sourceSignatureToPossibleTargetSignatures": {
                        str(source_r): sorted(targets)
                        for source_r, targets in sorted(source_signature_to_targets.items())
                    },
                    "subsetOrbit": [list(subset) for subset in orbit],
                    "subsetSize": subset_size,
                    "targetKernelOrder": kernel_order,
                    "targetKummerRank": kernel_order.bit_length() - 1,
                    "targetLabel": f"24T{target_t}",
                    "targetOrder": target_order,
                    "targetT": target_t,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--historical", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--subset-sizes", default="3,4,5,6")
    args = parser.parse_args()
    subset_sizes = tuple(sorted({int(value) for value in args.subset_sizes.split(",")}))
    if not subset_sizes or any(value < 3 or value > 11 for value in subset_sizes):
        raise ValueError("subset sizes must be a nonempty subset of 3,...,11")
    for path in (args.actions, args.routes, args.summary):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")

    action_map = {row["sourceLabel"]: row for row in load_jsonl(args.action_map)}
    historical = load_jsonl(args.historical)
    gold_rows = load_jsonl(args.gold)
    gold = {(row["label"], int(row["r"])): row for row in gold_rows}
    sources_by_label = defaultdict(list)
    exact_profile_by_label = {}
    for row in historical:
        profile = row.get("exactStructuralProfile")
        if profile is None:
            continue
        label = row["source"]["label"]
        sources_by_label[label].append(row)
        exact_profile_by_label[label] = profile

    action_rows = []
    failures = []
    for label in sorted(sources_by_label, key=lambda value: int(value[3:])):
        action = action_map[label]
        expected = exact_profile_by_label[label]
        matching_systems = [
            system
            for system in action["systems"]
            if profile_key(system, int(action["sourceOrder"]))
            == (
                int(expected["quotientT12"]),
                int(expected["sourceKernelOrder"]),
                bool(expected["flipInSource"]),
                str(expected["targetLabel"]),
                int(expected["targetKernelOrder"]),
            )
        ]
        seen = set()
        for system_index, system in enumerate(matching_systems):
            try:
                subset_actions = actions_for_system(
                    int(action["sourceT"]), system, subset_sizes
                )
            except Exception as exc:
                failures.append(
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "sourceLabel": label,
                        "systemIndex": system_index,
                    }
                )
                continue
            for product_action in subset_actions:
                identity = (
                    label,
                    product_action["subsetSize"],
                    product_action["targetLabel"],
                    product_action["targetKernelOrder"],
                    tuple(tuple(subset) for subset in product_action["subsetOrbit"]),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                action_rows.append(
                    {
                        **product_action,
                        "sourceBlockKernelOrder": int(expected["sourceKernelOrder"]),
                        "sourceBlockSystem": system["blocks"],
                        "sourceKummerRank": int(expected["sourceKummerRank"]),
                        "sourceLabel": label,
                        "sourceT": int(action["sourceT"]),
                    }
                )
        print(
            json.dumps(
                {
                    "actions": len(action_rows),
                    "event": "source_label_complete",
                    "failures": len(failures),
                    "sourceLabel": label,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    route_rows = []
    for action in action_rows:
        for historical_row in sources_by_label[action["sourceLabel"]]:
            source_r = int(historical_row["source"]["r"])
            target_signatures = action[
                "sourceSignatureToPossibleTargetSignatures"
            ].get(str(source_r), [])
            for target_r in target_signatures:
                pair = (action["targetLabel"], int(target_r))
                if pair not in gold:
                    continue
                route_rows.append(
                    {
                        "action": action,
                        "goldTarget": gold[pair],
                        "source": historical_row["source"],
                        "sourceQuotientLine": historical_row["quotientLine"],
                        "sourceQuotientPolynomialSha256": historical_row[
                            "quotientPolynomialSha256"
                        ],
                        "targetSignatureIsUniqueFromSourceSignature": len(
                            target_signatures
                        )
                        == 1,
                    }
                )

    actions_sha = write_jsonl(args.actions, action_rows)
    routes_sha = write_jsonl(args.routes, route_rows)
    distinct_pairs = {
        (row["goldTarget"]["label"], int(row["goldTarget"]["r"]))
        for row in route_rows
    }
    summary = {
        "actionMapSha256": sha256_path(args.action_map),
        "actions": str(args.actions.resolve()),
        "actionsSha256": actions_sha,
        "distinctHistoricalSourceLabels": len(sources_by_label),
        "failures": failures,
        "frozenGoldSha256": sha256_path(args.gold),
        "incidenceRankHistogram": dict(
            sorted(Counter(row["incidenceMatrixRank"] for row in action_rows).items())
        ),
        "liveDistinctLabels": len({label for label, _r in distinct_pairs}),
        "liveDistinctPairs": len(distinct_pairs),
        "liveKernelHistogram": dict(
            sorted(Counter(row["action"]["targetKernelOrder"] for row in route_rows).items())
        ),
        "liveRouteRows": len(route_rows),
        "networkCalls": 0,
        "routes": str(args.routes.resolve()),
        "routesSha256": routes_sha,
        "submissionCalls": 0,
        "subsetActionHistogram": dict(
            sorted(Counter(row["subsetSize"] for row in action_rows).items())
        ),
        "subsetLiveRouteHistogram": dict(
            sorted(Counter(row["action"]["subsetSize"] for row in route_rows).items())
        ),
        "subsetSizes": list(subset_sizes),
        "totalSubsetProductActions": len(action_rows),
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    temporary = args.summary.with_suffix(args.summary.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

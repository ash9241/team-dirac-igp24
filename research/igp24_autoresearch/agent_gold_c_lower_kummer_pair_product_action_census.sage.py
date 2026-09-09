#!/usr/bin/env sage -python
"""Exact action census for prescribed-rank conjugate pair products.

For an even source q(x^2), fix its exact two-block action and let beta_i be
the twelve roots of q.  For every H-orbit of unordered pairs of size twelve,
the products h_S=prod_{i in S} beta_i are conjugates of a degree-12 element.
On squareclasses this is the exact H-equivariant binary incidence map A.
Applying A to the source Kummer module gives a generally different kernel
rank and extension-group label.  This is a new submodule construction, not an
S-unit/auxiliary-prime search.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict, deque
from pathlib import Path

from sage.all import GF, Matrix, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_HISTORICAL = DATA / "agent_gold_c_lower_kummer_historical_census.jsonl"
DEFAULT_GOLD = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
DEFAULT_ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_c_lower_kummer_pair_product_summary.json"


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


def block_action_data(blocks: list[tuple[int, int]], permutation) -> tuple[list[int], list[int]]:
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


def pair_orbits(block_generators: list[list[int]]) -> list[list[tuple[int, int]]]:
    remaining = {(first, second) for first in range(12) for second in range(first + 1, 12)}
    orbits = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            pair = queue.popleft()
            for permutation in block_generators:
                image = tuple(sorted((permutation[pair[0]], permutation[pair[1]])))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        orbits.append(sorted(orbit))
    return orbits


def induced_permutation(
    orbit: list[tuple[int, int]], block_permutation: list[int], sign_vector: list[int]
):
    position = {pair: index for index, pair in enumerate(orbit)}
    images = []
    for pair in orbit:
        image_pair = tuple(
            sorted((block_permutation[pair[0]], block_permutation[pair[1]]))
        )
        target = position[image_pair]
        sign = sign_vector[pair[0]] ^ sign_vector[pair[1]]
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def action_for_system(source_t: int, system: dict) -> list[dict]:
    source_group = libgap.TransitiveGroup(24, source_t)
    generators = list(libgap.GeneratorsOfGroup(source_group))
    blocks = [tuple(sorted(int(value) for value in block)) for block in system["blocks"]]
    generator_data = [block_action_data(blocks, generator) for generator in generators]
    block_generators = [row[0] for row in generator_data]
    rows = []
    for orbit in pair_orbits(block_generators):
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
        quotient_order = int(system["blockActionOrder"])
        if target_order % quotient_order:
            raise ValueError("induced pair-product action has incompatible quotient order")
        kernel_order = target_order // quotient_order
        if kernel_order <= 0 or kernel_order & (kernel_order - 1):
            raise ValueError("induced pair-product block kernel is not a two-power")
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
        rows.append(
            {
                "incidenceMatrixRank": int(incidence.rank()),
                "incidenceRows": [
                    [int(value) for value in incidence.row(index)] for index in range(12)
                ],
                "mechanismCertificate": (
                    "h_S=product(beta_i for i in S); the twelve subsets form "
                    "one quotient-group orbit and the displayed GF(2) incidence "
                    "matrix is the exact map on conjugate squareclasses"
                ),
                "pairOrbit": [list(pair) for pair in orbit],
                "quotientT12": int(system["blockActionT12"]),
                "sourceSignatureToPossibleTargetSignatures": {
                    str(source_r): sorted(targets)
                    for source_r, targets in sorted(source_signature_to_targets.items())
                },
                "targetKernelOrder": kernel_order,
                "targetKummerRank": kernel_order.bit_length() - 1,
                "targetLabel": f"24T{int(libgap.TransitiveIdentification(target_group))}",
                "targetOrder": target_order,
                "targetT": int(libgap.TransitiveIdentification(target_group)),
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
    args = parser.parse_args()
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
        for system in matching_systems:
            for product_action in action_for_system(int(action["sourceT"]), system):
                identity = (
                    label,
                    product_action["targetLabel"],
                    product_action["targetKernelOrder"],
                    tuple(tuple(pair) for pair in product_action["pairOrbit"]),
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
                    "sourceLabel": label,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    route_rows = []
    for action in action_rows:
        if action["targetKernelOrder"] not in {4, 8, 16, 32, 64, 128, 256, 512, 1024}:
            continue
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
        "frozenGoldSha256": sha256_path(args.gold),
        "incidenceRankHistogram": dict(
            sorted(Counter(row["incidenceMatrixRank"] for row in action_rows).items())
        ),
        "liveDistinctLabels": len({label for label, _r in distinct_pairs}),
        "liveDistinctPairs": len(distinct_pairs),
        "liveKernelHistogram": dict(
            sorted(
                Counter(
                    row["action"]["targetKernelOrder"]
                    for row in route_rows
                ).items()
            )
        ),
        "liveQuotientTs": sorted(
            {row["action"]["quotientT12"] for row in route_rows}
        ),
        "liveRouteRows": len(route_rows),
        "networkCalls": 0,
        "pairProductActions": len(action_rows),
        "routes": str(args.routes.resolve()),
        "routesSha256": routes_sha,
        "submissionCalls": 0,
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    temporary = args.summary.with_suffix(args.summary.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

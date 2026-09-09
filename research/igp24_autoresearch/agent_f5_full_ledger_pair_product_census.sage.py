#!/usr/bin/env sage -python
"""Reopen F5 on the complete verified-even ledger source corpus.

The earlier pair-product waves used only the 846 even rows in one historical
submission.  This census keeps the same mathematical construction but replaces
that exhausted source bank by every locally verified scoreable even polynomial.

This stage is structural and offline.  It enumerates source labels having one
literal two-block system (not merely one repeated order/profile tuple),
computes all size-12 unordered-pair incidence actions, and intersects their
possible real signatures with the frozen SAIR undiscovered-pair snapshot.  It
does not submit anything and it does not claim a hit before the corresponding
arithmetic pair resolvent is factored and assigned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

from sage.all import GF, Matrix, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_GOLD = DATA / "live_undiscovered_signatures.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_even(coefficients: str) -> bool:
    values = coefficients.split(",")
    return (
        len(values) == 25
        and values[-1] == "1"
        and all(int(values[index]) == 0 for index in range(1, 25, 2))
    )


def quotient_line(coefficients: str) -> str:
    return ",".join(coefficients.split(",")[::2])


def profile_key(system: dict, source_order: int) -> tuple:
    block_order = int(system["blockActionOrder"])
    target_order = int(system["targetOrder"])
    if source_order % block_order or target_order % block_order:
        return ()
    return (
        int(system["blockActionT12"]),
        source_order // block_order,
        bool(system["flipInSource"]),
        str(system["targetLabel"]),
        target_order // block_order,
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


def pair_orbits(block_generators: list[list[int]]) -> list[list[tuple[int, int]]]:
    remaining = {
        (first, second)
        for first in range(12)
        for second in range(first + 1, 12)
    }
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
    orbit: list[tuple[int, int]],
    block_permutation: list[int],
    sign_vector: list[int],
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


def actions_for_system(source_t: int, system: dict) -> list[dict]:
    source_group = libgap.TransitiveGroup(24, source_t)
    generators = list(libgap.GeneratorsOfGroup(source_group))
    blocks = [
        tuple(sorted(int(value) for value in block)) for block in system["blocks"]
    ]
    generator_data = [block_action_data(blocks, generator) for generator in generators]
    block_generators = [row[0] for row in generator_data]
    signature_map = defaultdict(set)
    involutions = []
    for conjugacy_class in libgap.ConjugacyClasses(source_group):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) in (1, 2):
            involutions.append(representative)
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
            raise ValueError("induced pair action has incompatible quotient order")
        kernel_order = target_order // quotient_order
        if kernel_order <= 0 or kernel_order & (kernel_order - 1):
            raise ValueError("induced pair-action kernel is not a two-power")
        signature_map.clear()
        for representative in involutions:
            source_r = 24 - int(libgap.NrMovedPoints(representative))
            block_permutation, sign_vector = block_action_data(blocks, representative)
            induced = induced_permutation(orbit, block_permutation, sign_vector)
            target_r = 24 - int(libgap.NrMovedPoints(induced))
            signature_map[source_r].add(target_r)
        incidence = Matrix(
            GF(2),
            12,
            12,
            lambda row, column: int(column in orbit[row]),
        )
        target_t = int(libgap.TransitiveIdentification(target_group))
        rows.append(
            {
                "incidenceMatrixRank": int(incidence.rank()),
                "incidenceRows": [
                    [int(value) for value in incidence.row(index)]
                    for index in range(12)
                ],
                "mechanism": "full-ledger H-equivariant unordered-pair product",
                "pairOrbit": [list(pair) for pair in orbit],
                "quotientT12": int(system["blockActionT12"]),
                "sourceBlockKernelOrder": (
                    int(libgap.Size(source_group)) // quotient_order
                ),
                "sourceBlockSystem": [list(block) for block in blocks],
                "sourceLabel": f"24T{source_t}",
                "sourceSignatureToPossibleTargetSignatures": {
                    str(source_r): sorted(targets)
                    for source_r, targets in sorted(signature_map.items())
                },
                "sourceT": source_t,
                "targetKernelOrder": kernel_order,
                "targetLabel": f"24T{target_t}",
                "targetOrder": target_order,
                "targetT": target_t,
            }
        )
    return rows


def source_inventory(
    db: Path,
    eligible_labels: set[str],
    maximum_sources_per_label_signature: int,
) -> tuple[dict[str, dict[int, list[dict]]], dict]:
    best = defaultdict(dict)
    scanned = 0
    even_rows = 0
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            """
            SELECT v.label,v.t,v.r,p.coefficients,p.coefficient_hash,
                   p.submission_id,p.polynomial_index,v.field_disc_abs
            FROM polynomials AS p JOIN verifications AS v
            USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
            """
        )
        for row in cursor:
            scanned += 1
            label = str(row[0])
            if label not in eligible_labels:
                continue
            coefficients = str(row[3])
            if not is_even(coefficients):
                continue
            even_rows += 1
            q_line = quotient_line(coefficients)
            q_hash = hashlib.sha256(q_line.encode()).hexdigest()
            key = (label, int(row[2]))
            candidate = {
                "coefficientBytes": len(coefficients.encode()),
                "coefficientSha256": str(row[4]),
                "fieldDiscAbs": str(row[7]) if row[7] else None,
                "label": label,
                "polynomialIndex": int(row[6]),
                "quotientLine": q_line,
                "quotientPolynomialSha256": q_hash,
                "r": int(row[2]),
                "submissionId": str(row[5]),
                "t": int(row[1]),
            }
            previous = best[key].get(q_hash)
            if previous is None or (
                candidate["coefficientBytes"], candidate["coefficientSha256"]
            ) < (previous["coefficientBytes"], previous["coefficientSha256"]):
                best[key][q_hash] = candidate
    finally:
        connection.close()
    inventory = defaultdict(dict)
    distinct_quotients = 0
    for (label, signature), candidates in best.items():
        ordered = sorted(
            candidates.values(),
            key=lambda row: (row["coefficientBytes"], row["coefficientSha256"]),
        )
        distinct_quotients += len(ordered)
        inventory[label][signature] = ordered[:maximum_sources_per_label_signature]
    return inventory, {
        "acceptedScoreableRowsScanned": scanned,
        "eligibleEvenRows": even_rows,
        "eligibleDistinctLabelSignatures": len(best),
        "eligibleDistinctQuotientPolynomials": distinct_quotients,
        "retainedSourceRepresentatives": sum(
            len(rows) for by_signature in inventory.values() for rows in by_signature.values()
        ),
    }


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--maximum-sources-per-label-signature", type=int, default=3)
    args = parser.parse_args()
    if args.shard_count <= 0 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard")

    suffix = f"shard{args.shard_index}of{args.shard_count}"
    actions_path = DATA / f"agent_f5_full_ledger_pair_product_actions_{suffix}.jsonl"
    routes_path = DATA / f"agent_f5_full_ledger_pair_product_routes_{suffix}.jsonl"
    errors_path = DATA / f"agent_f5_full_ledger_pair_product_errors_{suffix}.jsonl"
    summary_path = DATA / f"agent_f5_full_ledger_pair_product_summary_{suffix}.json"

    action_map = load_jsonl(args.action_map)
    exact_rows = []
    for row in action_map:
        source_order = int(row["sourceOrder"])
        profiles = {
            profile_key(system, source_order)
            for system in row["systems"]
            if profile_key(system, source_order)
        }
        # Equal quotient/order/profile tuples do not prove that unordered-pair
        # actions attached to distinct block systems are equivalent.  Those
        # labels require a separate all-systems/Frobenius assignment audit and
        # are deliberately excluded from this safe first census.
        if len(profiles) == 1 and len(row["systems"]) == 1:
            exact_rows.append(row)
    exact_rows.sort(key=lambda row: int(row["sourceT"]))
    sharded = [
        row
        for position, row in enumerate(exact_rows)
        if position % args.shard_count == args.shard_index
    ]
    if args.limit:
        sharded = sharded[: args.limit]

    completed = set()
    for path in (actions_path, errors_path):
        if not path.exists():
            continue
        for row in load_jsonl(path):
            if "sourceLabel" in row:
                completed.add(str(row["sourceLabel"]))

    eligible_labels = {str(row["sourceLabel"]) for row in sharded}
    inventory, inventory_summary = source_inventory(
        args.db, eligible_labels, args.maximum_sources_per_label_signature
    )
    gold_rows = load_jsonl(args.gold)
    gold = {(str(row["label"]), int(row["r"])): row for row in gold_rows}
    routes_existing = load_jsonl(routes_path) if routes_path.exists() else []
    existing_route_keys = {
        (
            row["action"]["sourceLabel"],
            row["action"]["targetLabel"],
            int(row["goldTarget"]["r"]),
            row["source"]["quotientPolynomialSha256"],
            tuple(tuple(pair) for pair in row["action"]["pairOrbit"]),
        )
        for row in routes_existing
    }

    started = time.monotonic()
    new_actions = 0
    new_routes = 0
    errors = 0
    for position, row in enumerate(sharded, 1):
        label = str(row["sourceLabel"])
        if label in completed:
            continue
        try:
            system = row["systems"][0]
            actions = actions_for_system(int(row["sourceT"]), system)
            for action in actions:
                append_jsonl(actions_path, action)
                new_actions += 1
                for source_r, sources in inventory.get(label, {}).items():
                    target_signatures = action[
                        "sourceSignatureToPossibleTargetSignatures"
                    ].get(str(source_r), [])
                    for target_r in target_signatures:
                        gold_target = gold.get((action["targetLabel"], int(target_r)))
                        if gold_target is None:
                            continue
                        for source in sources:
                            key = (
                                action["sourceLabel"],
                                action["targetLabel"],
                                int(target_r),
                                source["quotientPolynomialSha256"],
                                tuple(tuple(pair) for pair in action["pairOrbit"]),
                            )
                            if key in existing_route_keys:
                                continue
                            append_jsonl(
                                routes_path,
                                {
                                    "action": action,
                                    "goldTarget": gold_target,
                                    "source": source,
                                    "status": "structural_route_requires_arithmetic_resolvent",
                                },
                            )
                            existing_route_keys.add(key)
                            new_routes += 1
        except Exception as exc:
            append_jsonl(
                errors_path,
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "sourceLabel": label,
                    "sourceT": int(row["sourceT"]),
                },
            )
            errors += 1
        completed.add(label)
        if position % 25 == 0:
            print(
                json.dumps(
                    {
                        "actions": new_actions,
                        "elapsedSeconds": round(time.monotonic() - started, 3),
                        "errors": errors,
                        "event": "full_ledger_pair_product_progress",
                        "position": position,
                        "routes": new_routes,
                        "shardLabels": len(sharded),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    actions = load_jsonl(actions_path) if actions_path.exists() else []
    routes = load_jsonl(routes_path) if routes_path.exists() else []
    error_rows = load_jsonl(errors_path) if errors_path.exists() else []
    distinct_pairs = {
        (row["action"]["targetLabel"], int(row["goldTarget"]["r"]))
        for row in routes
    }
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256_path(args.action_map),
        "actions": str(actions_path.resolve()),
        "actionsCount": len(actions),
        "actionsSha256": sha256_path(actions_path) if actions_path.exists() else None,
        "completedSourceLabels": len(completed & eligible_labels),
        "elapsedSecondsThisRun": round(time.monotonic() - started, 3),
        "errors": len(error_rows),
        "errorsArtifact": str(errors_path.resolve()),
        "exactProfileLabelsAllShards": len(exact_rows),
        "frozenGold": str(args.gold.resolve()),
        "frozenGoldSha256": sha256_path(args.gold),
        "inventory": inventory_summary,
        "mechanism": "F5 pair products reopened on complete verified-even ledger corpus",
        "networkCalls": 0,
        "routes": str(routes_path.resolve()),
        "routesCount": len(routes),
        "routesDistinctFrozenLivePairs": len(distinct_pairs),
        "routesDistinctFrozenLiveLabels": len({pair[0] for pair in distinct_pairs}),
        "routesSha256": sha256_path(routes_path) if routes_path.exists() else None,
        "shardCount": args.shard_count,
        "shardIndex": args.shard_index,
        "shardSourceLabels": len(sharded),
        "submissionCalls": 0,
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    temporary = summary_path.with_suffix(summary_path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(summary_path)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

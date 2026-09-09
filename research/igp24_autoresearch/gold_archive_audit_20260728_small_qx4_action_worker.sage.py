#!/usr/bin/env sage -python
"""Exact core-free index-24 action census for one selected q(x^4) group.

This is a read-only audit.  It enumerates every GAP conjugacy class of
core-free index-24 subgroups, transports the accepted source real-root
signatures through the corresponding coset actions, and intersects the
profiles with the immutable local target snapshot.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
ALLOWED_SOURCE_T = {1978, 3826, 3980, 4384, 8515, 9095}

ARTIFACT_FAMILIES = {
    "natural_unordered_pair": [
        "data/agent_index24_pair_orbit_map.jsonl",
        "data/agent_index24_missing_pair_shard*.jsonl",
        "data/agent_gold_b_combined_pair_orbit_map.jsonl",
        "data/agent_page19_pair_orbit_combined.jsonl",
        "data/pair_orbit_map.jsonl",
    ],
    "natural_ordered_pair": [
        "data/agent_index24_ordered_pair_shard*.jsonl",
    ],
    "natural_triple": [
        "data/agent_gold_a_triple_orbit_shard*.jsonl",
    ],
    "kummer_pair_product": [
        "data/agent_f5_full_ledger_pair_product_actions_shard*.jsonl",
        "data/agent_gold_c_lower_kummer_pair_product_actions.jsonl",
    ],
    "kummer_subset_product": [
        "data/agent_gold_c_lower_kummer_subset_product_actions.jsonl",
        "data/agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl",
    ],
}


def fixed_points(permutation) -> int:
    return 24 - int(libgap.NrMovedPoints(permutation))


def subgroup_identity(source_label: str, subgroup) -> str:
    generators = sorted(str(value) for value in libgap.SmallGeneratingSet(subgroup))
    payload = json.dumps(
        {"sourceLabel": source_label, "subgroupGenerators": generators},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def load_local_state(source_label: str) -> tuple[list[int], dict, set[tuple[str, int]]]:
    connection = sqlite3.connect(
        f"file:{DB.resolve()}?mode=ro&immutable=1", uri=True
    )
    try:
        source_r = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT r
                FROM verifications
                WHERE label=? AND lower(coalesce(status,''))='accepted'
                ORDER BY r
                """,
                (source_label,),
            )
        ]
        targets = {
            (str(row[0]), int(row[1])): {
                "teamCount": int(row[2]),
                "discovered": bool(row[3]),
                "generatedAt": row[4],
                "minimumDiscAbs": row[5],
            }
            for row in connection.execute(
                """
                SELECT label,r,team_count,discovered,generated_at,minimum_disc_abs
                FROM targets
                """
            )
        }
        baseline = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
    finally:
        connection.close()
    if not source_r:
        raise RuntimeError(f"{source_label} has no accepted local source signature")
    return source_r, targets, baseline


def row_source_label(row: dict) -> str | None:
    if row.get("sourceLabel"):
        return str(row["sourceLabel"])
    action = row.get("action")
    if isinstance(action, dict) and action.get("sourceLabel"):
        return str(action["sourceLabel"])
    return None


def row_target_labels(row: dict) -> set[str]:
    labels = set()
    if row.get("targetLabel"):
        labels.add(str(row["targetLabel"]))
    action = row.get("action")
    if isinstance(action, dict) and action.get("targetLabel"):
        labels.add(str(action["targetLabel"]))
    for target in row.get("targets", []):
        if isinstance(target, dict) and target.get("targetLabel"):
            labels.add(str(target["targetLabel"]))
    return labels


def existing_artifact_routes(source_label: str) -> tuple[dict[str, list[str]], dict]:
    routes = {}
    provenance = {}
    for family, patterns in ARTIFACT_FAMILIES.items():
        labels = set()
        paths = set()
        rows = 0
        for pattern in patterns:
            for name in glob.glob(str(ROOT / pattern)):
                path = Path(name)
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        if source_label not in line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row_source_label(row) != source_label:
                            continue
                        rows += 1
                        paths.add(str(path.relative_to(ROOT)))
                        labels.update(row_target_labels(row))
        routes[family] = sorted(labels, key=lambda value: int(value[3:]))
        provenance[family] = {
            "matchingRows": rows,
            "paths": sorted(paths),
        }
    return routes, provenance


def natural_orbit_subgroups(group) -> list[dict]:
    """Return explicit subgroup classes from existing natural action families."""
    families = []

    point_stabilizer = libgap.Stabilizer(group, 1)
    families.append(
        {
            "family": "natural_point",
            "orbitIndex": 0,
            "stabilizer": point_stabilizer,
            "targetLabel": (
                f"24T{int(libgap.TransitiveIdentification(group))}"
            ),
        }
    )

    orbit_specs = [
        (
            "natural_unordered_pair",
            libgap.Combinations(libgap.eval("[1..24]"), 2),
            libgap.OnSets,
        ),
        (
            "natural_ordered_pair",
            libgap.eval(
                "Filtered(Cartesian([1..24],[1..24]),x->x[1]<>x[2])"
            ),
            libgap.OnTuples,
        ),
        (
            "natural_triple",
            libgap.Combinations(libgap.eval("[1..24]"), 3),
            libgap.OnSets,
        ),
    ]
    for family, domain, operation in orbit_specs:
        for orbit_index, orbit in enumerate(libgap.Orbits(group, domain, operation)):
            if int(libgap.Length(orbit)) != 24:
                continue
            action = libgap.ActionHomomorphism(group, orbit, operation)
            if int(libgap.Size(libgap.Kernel(action))) != 1:
                continue
            image = libgap.Image(action)
            representative = libgap.Representative(orbit)
            stabilizer = libgap.Stabilizer(group, representative, operation)
            families.append(
                {
                    "family": family,
                    "orbitIndex": orbit_index,
                    "stabilizer": stabilizer,
                    "targetLabel": (
                        f"24T{int(libgap.TransitiveIdentification(image))}"
                    ),
                }
            )
    return families


def census(source_t: int) -> dict:
    started = time.monotonic()
    source_label = f"24T{source_t}"
    source_r, targets, baseline = load_local_state(source_label)
    artifact_routes, artifact_provenance = existing_artifact_routes(source_label)

    group = libgap.TransitiveGroup(24, source_t)
    group_order = int(libgap.Size(group))
    conjugacy_classes = list(libgap.ConjugacyClasses(group))
    relevant_classes = []
    for class_index, conjugacy_class in enumerate(conjugacy_classes):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        natural_r = fixed_points(representative)
        if natural_r not in source_r:
            continue
        relevant_classes.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "naturalSourceR": natural_r,
                "order": order,
                "representative": representative,
            }
        )

    natural_coverage = natural_orbit_subgroups(group)
    subgroup_classes = list(libgap.ConjugacyClassesSubgroups(group))
    actions = []
    for subgroup_class_index, subgroup_class in enumerate(subgroup_classes):
        subgroup = libgap.Representative(subgroup_class)
        if int(libgap.Index(group, subgroup)) != 24:
            continue
        core_order = int(libgap.Size(libgap.Core(group, subgroup)))
        if core_order != 1:
            continue
        cosets = libgap.RightCosets(group, subgroup)
        action = libgap.ActionHomomorphism(group, cosets, libgap.OnRight)
        if int(libgap.Size(libgap.Kernel(action))) != 1:
            raise ArithmeticError("core-free coset action is unexpectedly unfaithful")
        image = libgap.Image(action)
        target_t = int(libgap.TransitiveIdentification(image))
        target_label = f"24T{target_t}"

        exact_natural_coverage = []
        for row in natural_coverage:
            if bool(libgap.IsConjugate(group, subgroup, row["stabilizer"])):
                exact_natural_coverage.append(
                    {
                        "family": row["family"],
                        "orbitIndex": row["orbitIndex"],
                        "targetLabel": row["targetLabel"],
                    }
                )

        profiles = []
        tc0_profiles = []
        for class_row in relevant_classes:
            target_r = fixed_points(
                libgap.Image(action, class_row["representative"])
            )
            pair = (target_label, target_r)
            target = targets.get(pair)
            current_tc0 = bool(
                target
                and int(target["teamCount"]) == 0
                and not bool(target["discovered"])
            )
            profile = {
                "classIndex": class_row["classIndex"],
                "classSize": class_row["classSize"],
                "currentTc0": current_tc0,
                "inBaseline": pair in baseline,
                "order": class_row["order"],
                "sourceR": class_row["naturalSourceR"],
                "target": target,
                "targetR": target_r,
            }
            profiles.append(profile)
            if current_tc0:
                tc0_profiles.append(profile)

        artifact_label_matches = sorted(
            family
            for family, labels in artifact_routes.items()
            if target_label in labels
        )
        actions.append(
            {
                "artifactTargetLabelMatches": artifact_label_matches,
                "coreOrder": core_order,
                "exactNaturalCoverage": exact_natural_coverage,
                "profiles": profiles,
                "structuralTc0Profiles": tc0_profiles,
                "subgroupClassIdentitySha256": subgroup_identity(
                    source_label, subgroup
                ),
                "subgroupClassIndex": subgroup_class_index,
                "subgroupOrder": int(libgap.Size(subgroup)),
                "targetLabel": target_label,
                "targetOrder": int(libgap.Size(image)),
                "targetT": target_t,
            }
        )

    action_targets = sorted(
        {row["targetLabel"] for row in actions},
        key=lambda value: int(value[3:]),
    )
    tc0_routes = [
        {
            "artifactTargetLabelMatches": action["artifactTargetLabelMatches"],
            "classIndex": profile["classIndex"],
            "classSize": profile["classSize"],
            "exactNaturalCoverage": action["exactNaturalCoverage"],
            "inBaseline": profile["inBaseline"],
            "sourceR": profile["sourceR"],
            "subgroupClassIdentitySha256": action[
                "subgroupClassIdentitySha256"
            ],
            "subgroupClassIndex": action["subgroupClassIndex"],
            "target": profile["target"],
            "targetLabel": action["targetLabel"],
            "targetR": profile["targetR"],
        }
        for action in actions
        for profile in action["structuralTc0Profiles"]
    ]
    return {
        "actionTargetLabels": action_targets,
        "actions": actions,
        "acceptedSourceR": source_r,
        "artifactProvenance": artifact_provenance,
        "artifactRoutesByFamily": artifact_routes,
        "conjugacyClassCount": len(conjugacy_classes),
        "coreFreeIndex24ActionCount": len(actions),
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "groupOrder": group_order,
        "networkCalls": 0,
        "schemaVersion": "gold-archive-audit-20260728-small-qx4-action-v1",
        "sourceLabel": source_label,
        "sourceT": source_t,
        "status": "certified",
        "structuralTc0ProfileCount": len(tc0_routes),
        "structuralTc0Routes": tc0_routes,
        "subgroupConjugacyClassCount": len(subgroup_classes),
        "submissionCalls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--t", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.t not in ALLOWED_SOURCE_T:
        parser.error("--t must be one of the six selected source groups")
    payload = census(args.t)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "actions": payload["coreFreeIndex24ActionCount"],
                "elapsedSeconds": payload["elapsedSeconds"],
                "output": str(args.output),
                "sourceLabel": payload["sourceLabel"],
                "tc0Profiles": payload["structuralTc0ProfileCount"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

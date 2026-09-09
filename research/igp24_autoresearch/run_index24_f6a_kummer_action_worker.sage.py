#!/usr/bin/env sage -python
"""Checkpointed action-only worker for the fresh F6-A signed-subset census.

``--preflight-only`` is ordinary Python and does not initialize Sage/GAP.
``--census-only`` is the single heavy mode.  It enumerates exact two-point
block systems and signed quotient-subset actions, checkpointing after every
source/k task.  It never reads polynomial coefficients itself, constructs a
resolvent, accesses the network, stages a candidate, or submits.

Polynomial arithmetic is authorized in the output only after a faithful
degree-24 action has exact TransitiveIdentification equal to a sealed target
and its exact source-signature profile is one of the safe sealed profiles.
The downstream contract closes root alignment by enumerating every exact
degree-12 factor rather than assuming a numbering of roots.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import signal
import time
from collections import defaultdict, deque
from pathlib import Path

import prepare_index24_f6a_kummer_action_plan as light


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_CHECKPOINT = DATA / "index24_f6a_kummer_action_checkpoint.json"
DEFAULT_OUTPUT = DATA / "index24_f6a_kummer_action_census.json"


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def rendered_plan(plan: dict) -> bytes:
    return (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()


def stable_plan_sha256(plan: dict) -> str:
    """Hash sealed scope while allowing a fresh target snapshot to refresh."""
    stable = json.loads(json.dumps(plan))
    for route in stable.get("structuralRoutes", []):
        for target in route.get("targets", []):
            target.pop("state", None)
    for task in stable.get("tasks", []):
        for route in task.get("targetActions", []):
            for target in route.get("targets", []):
                target.pop("state", None)
    return sha256_bytes(rendered_plan(stable))


def subset_orbits(
    block_generators: list[list[int]], subset_size: int, max_universe: int
) -> list[list[tuple[int, ...]]]:
    universe_size = math.comb(12, subset_size)
    if universe_size > max_universe:
        raise ResourceWarning(
            f"subset universe {universe_size} exceeds hard run gate {max_universe}"
        )
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


def binary_rank(rows: list[list[int]]) -> int:
    values = []
    width = len(rows[0]) if rows else 0
    for row in rows:
        if len(row) != width or any(value not in (0, 1) for value in row):
            raise ValueError("incidence matrix is not binary and rectangular")
        encoded = sum(int(value) << index for index, value in enumerate(row))
        values.append(encoded)
    rank = 0
    for column in range(width - 1, -1, -1):
        pivot = next((index for index in range(rank, len(values)) if values[index] & (1 << column)), None)
        if pivot is None:
            continue
        values[rank], values[pivot] = values[pivot], values[rank]
        for index in range(len(values)):
            if index != rank and values[index] & (1 << column):
                values[index] ^= values[rank]
        rank += 1
    return rank


def block_action_data(libgap, blocks: list[tuple[int, int]], permutation) -> tuple[list[int], list[int]]:
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
        if image not in point_to_block:
            raise ArithmeticError("source generator does not preserve the block system")
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def induced_permutation(libgap, orbit, block_permutation, sign_vector):
    position = {subset: index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        image_subset = tuple(sorted(block_permutation[index] for index in subset))
        target = position[image_subset]
        sign = sum(sign_vector[index] for index in subset) % 2
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def orbit_permutation(libgap, orbit, block_permutation):
    position = {subset: index for index, subset in enumerate(orbit)}
    return libgap.PermList(
        [
            position[tuple(sorted(block_permutation[index] for index in subset))] + 1
            for subset in orbit
        ]
    )


def fixed_points(libgap, permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def point_stabilizer_presentation_sha(libgap, source_label: str, target_label: str, subgroup) -> str:
    # This is an audit fingerprint of the actual derived subgroup, not a claim
    # that presentation hashes are invariant under conjugacy/automorphisms.
    generators = sorted(str(value) for value in libgap.SmallGeneratingSet(subgroup))
    return sha256_bytes(
        canonical_json(
            {
                "sourceLabel": source_label,
                "subgroupGenerators": generators,
                "targetLabel": target_label,
            }
        ).encode()
    )


def validate_paths(checkpoint: Path, output: Path) -> tuple[Path, Path, Path]:
    checkpoint = checkpoint.resolve()
    output = output.resolve()
    if checkpoint.parent != DATA.resolve() or output.parent != DATA.resolve():
        raise ValueError("checkpoint/output escaped the data directory")
    lock = Path(str(checkpoint) + ".lock")
    if output.exists() or Path(str(output) + ".tmp").exists():
        raise FileExistsError(f"refusing to overwrite final census: {output}")
    return checkpoint, output, lock


def atomic_checkpoint(path: Path, value: dict) -> None:
    light.assert_coefficient_free(value)
    temporary = Path(str(path) + ".tmp")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


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
    return sha256_bytes(payload)


def acquire_lock(lock: Path) -> int:
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(descriptor, f"pid={os.getpid()}\n".encode())
    os.fsync(descriptor)
    return descriptor


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("action-only worker reached its wall-time gate")


def configure_alarm(seconds: int) -> None:
    def expired(_signum, _frame):
        raise TimeoutError("action-only worker reached its process wall-time gate")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)


def enumerate_block_systems(libgap, group, generators, max_systems: int, deadline: float) -> list[dict]:
    centralizer = libgap.Centralizer(libgap.SymmetricGroup(24), group)
    systems = {}
    for flip in libgap.Elements(centralizer):
        check_deadline(deadline)
        if int(libgap.Order(flip)) != 2 or int(libgap.NrMovedPoints(flip)) != 24:
            continue
        key = tuple(
            (point, int(libgap.OnPoints(point, flip)))
            for point in range(1, 25)
            if point < int(libgap.OnPoints(point, flip))
        )
        if len(key) != 12:
            raise ArithmeticError("fixed-point-free involution did not give twelve blocks")
        if key in systems:
            continue
        blocks = [tuple(pair) for pair in key]
        generator_data = [block_action_data(libgap, blocks, generator) for generator in generators]
        block_generators = [row[0] for row in generator_data]
        quotient_group = libgap.Group(
            [libgap.PermList([value + 1 for value in row]) for row in block_generators]
        )
        systems[key] = {
            "blockActionOrder": int(libgap.Size(quotient_group)),
            "blockActionT12": int(libgap.TransitiveIdentification(quotient_group)),
            "blocks": blocks,
            "blockGenerators": block_generators,
            "generatorData": generator_data,
            "systemSha256": sha256_bytes(canonical_json(blocks).encode()),
        }
        if len(systems) > max_systems:
            raise ResourceWarning(
                f"source has more than the hard run gate of {max_systems} block systems"
            )
    if not systems:
        raise ArithmeticError("accepted even source action has no exact two-point block system")
    return [systems[key] for key in sorted(systems)]


def build_source_context(libgap, source: dict, max_systems: int, deadline: float) -> dict:
    check_deadline(deadline)
    group = libgap.TransitiveGroup(24, int(source["t"]))
    if (
        int(libgap.TransitiveIdentification(group)) != int(source["t"])
        or int(libgap.Size(group)) != int(source["order"])
    ):
        raise ArithmeticError(f"standard source action changed for {source['label']}")
    generators = list(libgap.GeneratorsOfGroup(group))
    relevant_classes = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) not in (1, 2):
            continue
        relevant_classes.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "representative": representative,
                "sourceR": fixed_points(libgap, representative, 24),
            }
        )
    systems = enumerate_block_systems(libgap, group, generators, max_systems, deadline)
    return {
        "generators": generators,
        "group": group,
        "relevantClasses": relevant_classes,
        "source": source,
        "systems": systems,
    }


def task_action_rows(libgap, context: dict, task: dict, args, deadline: float) -> tuple[list[dict], list[dict], dict]:
    source = context["source"]
    subset_size = int(task["subsetSize"])
    rows = []
    promotions = []
    orbit_histogram = {}
    target_by_t = {int(row["targetT"]): row for row in task["targetActions"]}
    for system_index, system in enumerate(context["systems"]):
        check_deadline(deadline)
        orbits = subset_orbits(system["blockGenerators"], subset_size, args.max_subset_universe)
        orbit_histogram[system["systemSha256"]] = sorted(len(orbit) for orbit in orbits)
        for orbit_index, orbit in enumerate(orbits):
            check_deadline(deadline)
            if len(orbit) != 12:
                continue
            induced_generators = [
                induced_permutation(libgap, orbit, block_permutation, sign_vector)
                for block_permutation, sign_vector in system["generatorData"]
            ]
            target_group = libgap.Group(induced_generators)
            transitive = bool(libgap.IsTransitive(target_group, libgap.eval("[1..24]")))
            target_order = int(libgap.Size(target_group))
            induced_quotient = libgap.Group(
                [
                    orbit_permutation(libgap, orbit, block_permutation)
                    for block_permutation, _sign_vector in system["generatorData"]
                ]
            )
            incidence_rows = [
                [int(column in subset) for column in range(12)] for subset in orbit
            ]
            signature_map = defaultdict(set)
            profiles = []
            for class_row in context["relevantClasses"]:
                block_permutation, sign_vector = block_action_data(
                    libgap, system["blocks"], class_row["representative"]
                )
                induced = induced_permutation(libgap, orbit, block_permutation, sign_vector)
                target_r = fixed_points(libgap, induced, 24)
                signature_map[class_row["sourceR"]].add(target_r)
                profiles.append(
                    {
                        "classIndex": class_row["classIndex"],
                        "classSize": class_row["classSize"],
                        "sourceR": class_row["sourceR"],
                        "targetR": target_r,
                    }
                )
            row = {
                "blockActionOrder": system["blockActionOrder"],
                "blockActionT12": system["blockActionT12"],
                "blockSystemIndex": system_index,
                "blockSystemSha256": system["systemSha256"],
                "faithful": False,
                "incidenceMatrixRank": binary_rank(incidence_rows),
                "incidenceRows": incidence_rows,
                "inducedQuotientOrder": int(libgap.Size(induced_quotient)),
                "inducedQuotientT12": int(libgap.TransitiveIdentification(induced_quotient)),
                "orbitIndexWithinSubsetSize": orbit_index,
                "profiles": profiles,
                "sourceLabel": source["label"],
                "sourceSignatureToPossibleTargetSignatures": {
                    str(source_r): sorted(values) for source_r, values in sorted(signature_map.items())
                },
                "sourceT": source["t"],
                "subsetOrbit": [list(subset) for subset in orbit],
                "subsetSize": subset_size,
                "targetActionEquality": False,
                "targetOrder": target_order,
                "transitive": transitive,
            }
            if transitive:
                target_t = int(libgap.TransitiveIdentification(target_group))
                row.update({"targetLabel": f"24T{target_t}", "targetT": target_t})
                route = target_by_t.get(target_t)
                if route is not None and target_order == int(source["order"]):
                    homomorphism = libgap.GroupHomomorphismByImages(
                        context["group"], target_group, context["generators"], induced_generators
                    )
                    if homomorphism == libgap.fail:
                        raise ArithmeticError("induced images do not define a source homomorphism")
                    kernel_order = int(libgap.Size(libgap.Kernel(homomorphism)))
                    point_stabilizer = libgap.PreImage(
                        homomorphism, libgap.Stabilizer(target_group, 1)
                    )
                    index = int(libgap.Index(context["group"], point_stabilizer))
                    core_order = int(libgap.Size(libgap.Core(context["group"], point_stabilizer)))
                    mapped = row["sourceSignatureToPossibleTargetSignatures"].get(
                        str(source["r"]), []
                    )
                    allowed_profiles = route["allowedSourceSignatureProfiles"]
                    exact = (
                        kernel_order == 1
                        and index == 24
                        and core_order == 1
                        and mapped in allowed_profiles
                    )
                    row.update(
                        {
                            "coreOrder": core_order,
                            "faithful": kernel_order == 1,
                            "kernelOrder": kernel_order,
                            "pointStabilizerIndex": index,
                            "pointStabilizerOrder": int(libgap.Size(point_stabilizer)),
                            "pointStabilizerPresentationSha256": point_stabilizer_presentation_sha(
                                libgap, source["label"], route["targetLabel"], point_stabilizer
                            ),
                            "safeSourceSignatureProfile": exact,
                            "targetActionEquality": exact,
                        }
                    )
                    if exact:
                        live_rs = sorted(set(mapped).intersection(route["safeTargetRs"]))
                        promotion_key = {
                            "blockSystemSha256": system["systemSha256"],
                            "sourceLabel": source["label"],
                            "subsetOrbit": row["subsetOrbit"],
                            "subsetSize": subset_size,
                            "targetLabel": route["targetLabel"],
                            "targetRs": live_rs,
                        }
                        promotions.append(
                            {
                                "arithmeticAuthorized": True,
                                "candidateConstruction": (
                                    "factor all degree-12 components of q.symmetric_power(k), then "
                                    "test each factor(x^2) through every exact final gate"
                                ),
                                "factorEnumerationDegree": int(task["factorEnumerationDegree"]),
                                "factorSelectionUsesRootNumbering": False,
                                "inducedActionDegree": 24,
                                "invariantFormula": "h_S=product(beta_i for i in S); roots +/-sqrt(h_S)",
                                "promotionId": sha256_bytes(canonical_json(promotion_key).encode()),
                                "rootActionAlignment": "exhaustive exact degree-12 factor enumeration",
                                "sourceLabel": source["label"],
                                "sourceR": source["r"],
                                "subsetOrbit": row["subsetOrbit"],
                                "subsetSize": subset_size,
                                "targetLabel": route["targetLabel"],
                                "targetRs": live_rs,
                                "targetT": target_t,
                            }
                        )
            rows.append(row)
            if len(rows) > args.max_action_rows:
                raise ResourceWarning("single task exceeded the action-row gate")
            if len(promotions) > args.max_promotions:
                raise ResourceWarning("single task exceeded the promotion gate")
    promotions = sorted({row["promotionId"]: row for row in promotions}.values(), key=lambda row: row["promotionId"])
    return rows, promotions, {
        "blockSystemCount": len(context["systems"]),
        "length12ActionRows": len(rows),
        "orbitSizeHistogramByBlockSystem": orbit_histogram,
        "promotionCount": len(promotions),
    }


def new_checkpoint(plan: dict, plan_sha: str, args) -> dict:
    return {
        "actionOnly": True,
        "actionRows": [],
        "coefficientMaterialIncluded": False,
        "completedTaskIds": [],
        "failures": [],
        "family": plan["family"],
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "planSha256": plan_sha,
        "promotions": [],
        "resourceGates": {
            "maxActionRows": args.max_action_rows,
            "maxBlockSystems": args.max_block_systems,
            "maxPromotions": args.max_promotions,
            "maxSubsetUniverse": args.max_subset_universe,
            "maxWallSeconds": args.max_wall_seconds,
        },
        "schemaVersion": "index24-f6a-kummer-action-checkpoint-v1",
        "status": "running",
        "submissionCalls": 0,
        "taskSummaries": [],
    }


def load_checkpoint(path: Path, plan: dict, plan_sha: str, args) -> dict:
    if not path.exists():
        return new_checkpoint(plan, plan_sha, args)
    checkpoint = json.loads(path.read_text())
    if (
        checkpoint.get("schemaVersion") != "index24-f6a-kummer-action-checkpoint-v1"
        or checkpoint.get("planSha256") != plan_sha
        or checkpoint.get("coefficientMaterialIncluded") is not False
        or checkpoint.get("heavyArithmeticCalls") != 0
        or checkpoint.get("networkCalls") != 0
        or checkpoint.get("submissionCalls") != 0
        or checkpoint.get("resourceGates") != new_checkpoint(plan, plan_sha, args)["resourceGates"]
    ):
        raise ValueError("checkpoint provenance or resource gates changed")
    if checkpoint.get("status") == "failed" and not args.retry_failed:
        raise ValueError("checkpoint is failed; inspect it and pass --retry-failed explicitly")
    if checkpoint.get("status") not in {"running", "failed"}:
        raise ValueError("checkpoint is not resumable")
    light.assert_coefficient_free(checkpoint)
    return checkpoint


def validate_cli_gates(args, plan: dict) -> None:
    hard = plan["hardResourceGates"]
    values = {
        "max_action_rows": (args.max_action_rows, hard["maxActionRows"]),
        "max_block_systems": (args.max_block_systems, hard["maxBlockSystemsPerSource"]),
        "max_promotions": (args.max_promotions, hard["maxPromotions"]),
        "max_subset_universe": (args.max_subset_universe, hard["maxSubsetUniverse"]),
        "max_wall_seconds": (args.max_wall_seconds, hard["maxActionWallSeconds"]),
    }
    for name, (value, maximum) in values.items():
        if value <= 0 or value > maximum:
            raise ValueError(f"{name} must lie in 1..{maximum}")
    largest_task = max(int(task["factorEnumerationDegree"]) for task in plan["tasks"])
    if args.max_subset_universe < largest_task:
        raise ValueError("max-subset-universe is smaller than a sealed fresh task")


def preflight(args) -> dict:
    plan = light.build_plan()
    validate_cli_gates(args, plan)
    plan_sha = stable_plan_sha256(plan)
    return {
        "actionOnly": True,
        "coefficientMaterialIncluded": False,
        "event": "index24_f6a_kummer_action_worker_preflight_ok",
        "excludedTasks": len(plan["exclusions"]),
        "freshTasks": len(plan["tasks"]),
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "planSha256": plan_sha,
        "plannedCheckpoint": str(args.checkpoint.relative_to(ROOT)),
        "plannedOutput": str(args.output.relative_to(ROOT)),
        "resourceGates": {
            "maxActionRows": args.max_action_rows,
            "maxBlockSystems": args.max_block_systems,
            "maxPromotions": args.max_promotions,
            "maxSubsetUniverse": args.max_subset_universe,
            "maxWallSeconds": args.max_wall_seconds,
        },
        "submissionCalls": 0,
    }


def run_census(args) -> dict:
    checkpoint_path, output_path, lock_path = validate_paths(args.checkpoint, args.output)
    lock_descriptor = acquire_lock(lock_path)
    try:
        plan = light.build_plan()
        validate_cli_gates(args, plan)
        plan_sha = stable_plan_sha256(plan)
        checkpoint = load_checkpoint(checkpoint_path, plan, plan_sha, args)
        checkpoint["status"] = "running"
        checkpoint["failures"] = []
        atomic_checkpoint(checkpoint_path, checkpoint)

        configure_alarm(args.max_wall_seconds)
        deadline = time.monotonic() + args.max_wall_seconds
        # Deferred by design: preflight and unit tests do not initialize Sage/GAP.
        from sage.all import libgap

        sources = {row["label"]: row for row in plan["sources"]}
        completed = set(checkpoint["completedTaskIds"])
        context = None
        context_label = None
        for task in plan["tasks"]:
            if task["taskId"] in completed:
                continue
            check_deadline(deadline)
            try:
                if context_label != task["sourceLabel"]:
                    context = build_source_context(
                        libgap,
                        sources[task["sourceLabel"]],
                        args.max_block_systems,
                        deadline,
                    )
                    context_label = task["sourceLabel"]
                rows, promotions, summary = task_action_rows(
                    libgap, context, task, args, deadline
                )
                if len(checkpoint["actionRows"]) + len(rows) > args.max_action_rows:
                    raise ResourceWarning("cumulative action rows exceed the run gate")
                existing_promotions = {row["promotionId"]: row for row in checkpoint["promotions"]}
                existing_promotions.update({row["promotionId"]: row for row in promotions})
                if len(existing_promotions) > args.max_promotions:
                    raise ResourceWarning("cumulative promotions exceed the run gate")
                checkpoint["actionRows"].extend(rows)
                checkpoint["promotions"] = sorted(
                    existing_promotions.values(), key=lambda row: row["promotionId"]
                )
                checkpoint["completedTaskIds"].append(task["taskId"])
                checkpoint["taskSummaries"].append({"taskId": task["taskId"], **summary})
                completed.add(task["taskId"])
                atomic_checkpoint(checkpoint_path, checkpoint)
                print(
                    json.dumps(
                        {
                            "actionRows": len(checkpoint["actionRows"]),
                            "completedTasks": len(completed),
                            "event": "index24_f6a_kummer_action_checkpoint",
                            "freshTasks": len(plan["tasks"]),
                            "promotions": len(checkpoint["promotions"]),
                            "taskId": task["taskId"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                checkpoint["failures"] = [
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "taskId": task["taskId"],
                    }
                ]
                checkpoint["status"] = "failed"
                atomic_checkpoint(checkpoint_path, checkpoint)
                raise

        if len(completed) != len(plan["tasks"]):
            raise ArithmeticError("worker exited without completing every fresh task")
        # Rebuild the light plan immediately before publishing to recheck every
        # mutable target and source receipt without introducing coefficients.
        final_plan = light.build_plan()
        if stable_plan_sha256(final_plan) != plan_sha:
            raise ValueError("sealed plan or mutable target state changed during the action census")
        status = (
            "exact_action_hits_authorize_isolated_arithmetic"
            if checkpoint["promotions"]
            else "certified_no_fresh_signed_subset_action"
        )
        payload = {
            "actionOnly": True,
            "actionRows": checkpoint["actionRows"],
            "coefficientMaterialIncluded": False,
            "completedTaskIds": checkpoint["completedTaskIds"],
            "dispatcherContractAfterExactActionHit": plan[
                "dispatcherContractAfterExactActionHit"
            ],
            "exactActionPromotionCount": len(checkpoint["promotions"]),
            "family": plan["family"],
            "hardResourceGates": checkpoint["resourceGates"],
            "heavyArithmeticCalls": 0,
            "networkCalls": 0,
            "planSha256": plan_sha,
            "promotions": checkpoint["promotions"],
            "schemaVersion": "index24-f6a-kummer-action-census-v1",
            "status": status,
            "submissionCalls": 0,
            "taskSummaries": checkpoint["taskSummaries"],
        }
        light.assert_coefficient_free(payload)
        rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        artifact_sha = publish_no_overwrite(output_path, rendered)
        checkpoint["finalArtifact"] = str(output_path.relative_to(ROOT))
        checkpoint["finalArtifactSha256"] = artifact_sha
        checkpoint["status"] = "complete"
        atomic_checkpoint(checkpoint_path, checkpoint)
        return {
            "actionOnly": True,
            "artifact": str(output_path.relative_to(ROOT)),
            "artifactSha256": artifact_sha,
            "coefficientMaterialIncluded": False,
            "event": "index24_f6a_kummer_action_census_complete",
            "exactActionPromotionCount": len(checkpoint["promotions"]),
            "heavyArithmeticCalls": 0,
            "networkCalls": 0,
            "status": status,
            "submissionCalls": 0,
        }
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        os.close(lock_descriptor)
        lock_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--census-only", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-wall-seconds", type=int, default=1800)
    parser.add_argument("--max-block-systems", type=int, default=64)
    parser.add_argument("--max-subset-universe", type=int, default=924)
    parser.add_argument("--max-action-rows", type=int, default=4096)
    parser.add_argument("--max-promotions", type=int, default=64)
    parser.add_argument("--retry-failed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preflight_only:
        event = preflight(args)
    else:
        event = run_census(args)
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

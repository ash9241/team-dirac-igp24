#!/usr/bin/env python3
"""Seal the light-only post-backfill gold route handoff.

The full-ledger re-intersection has already performed the current receipt,
outbox, target, and source-lineage exclusions.  This script validates the
completed exact profile checkpoint, stages guaranteed-safe factor runbooks,
and groups conditional routes by the one pair-resolvent factorization they
share.  It never launches a polynomial worker or submits anything.
"""

from __future__ import annotations

import json
import shlex
import tempfile
from collections import defaultdict
from pathlib import Path

import audit_low_contention_pair_routes as pair_audit
import prepare_v11_pair_delta as helper
import run_gold_profile_backfill_one as backfill


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BATCH = DATA / "gold_profile_backfill_20260722_batch1"
PLAN = BATCH / "provenance_plan.json"
PROFILE_OUTPUT = BATCH / "missing_profile_rows.jsonl"
GOLD_CERTIFICATE = DATA / "full_ledger_gold_reintersection_certificate.json"
SAFE_RUNBOOKS = BATCH / "safe_factor_runbooks.json"
CONDITIONAL_RUNBOOKS = BATCH / "conditional_factor_runbooks.json"
CERTIFICATE = BATCH / "postbackfill_frontier_certificate.json"


class SealFailure(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SealFailure(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise SealFailure(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def artifact(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": helper.sha256_path(path),
    }


def atomic_json(path: Path, value: dict) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise SealFailure(f"coefficient payload entered metadata: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(rendered)
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_pair(text: str) -> tuple[str, int]:
    label, raw_r = str(text).rsplit("/r", 1)
    return label, int(raw_r)


def validate_profiles(plan: dict, rows: list[dict]) -> dict:
    validated = backfill.validate_worker_rows(rows, plan)
    selected = {
        parse_pair(value) for value in plan.get("selectedSignatures") or []
    }
    flattened = {
        (str(row["sourceLabel"]), int(source_r))
        for row in rows
        for source_r in row.get("sourceR") or []
    }
    profiled = set()
    profile_rows = 0
    incomplete_target_maps = 0
    duplicate_classes = 0
    for row in rows:
        label = str(row["sourceLabel"])
        expected_orbits = {
            int(target["orbitIndex"]) for target in row.get("targets") or []
        }
        seen_classes: dict[int, set[int]] = defaultdict(set)
        for profile in row.get("profiles") or []:
            source_r = int(profile["sourceR"])
            if (label, source_r) not in selected:
                continue
            profile_rows += 1
            profiled.add((label, source_r))
            class_index = int(profile["classIndex"])
            if class_index in seen_classes[source_r]:
                duplicate_classes += 1
            seen_classes[source_r].add(class_index)
            actual_orbits = {
                int(target["orbitIndex"])
                for target in pair_audit.profile_targets(profile)
            }
            if actual_orbits != expected_orbits:
                incomplete_target_maps += 1
    errors = sum(
        1
        for row in rows
        if row.get("status") != "certified"
        or row.get("error") is not None
        or row.get("errors")
        or not helper.certificate_is_exact(row)
    )
    if (
        len(validated) != 29
        or len(rows) != 29
        or len(selected) != 32
        or flattened != selected
        or profiled != selected
        or errors
        or incomplete_target_maps
        or duplicate_classes
    ):
        raise SealFailure("completed profile checkpoint failed exact coverage audit")
    return {
        "selectedProfiles": len(selected),
        "selectedLabels": len(validated),
        "certifiedOutputRows": len(rows),
        "compatibleClassProfileRows": profile_rows,
        "coveredSelectedProfiles": len(profiled),
        "missingSelectedProfiles": len(selected - profiled),
        "unexpectedSelectedProfiles": len(profiled - selected),
        "duplicateCompatibleClasses": duplicate_classes,
        "incompleteTargetMaps": incomplete_target_maps,
        "workerErrors": errors,
        "allRowsExactCertified": True,
    }


def factor_key(route: dict) -> tuple:
    anchor = route["sourceAnchor"]
    action = route["actionArtifact"]
    return (
        str(route["sourcePair"]),
        str(anchor["submissionId"]),
        int(anchor["polynomialIndex"]),
        str(anchor["coefficientSha256"]),
        str(action["path"]),
    )


def grouped_runbooks(routes: list[dict], prefix: str, status: str) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for route in routes:
        groups[factor_key(route)].append(route)
    ordered = sorted(
        groups.items(),
        key=lambda item: min(int(route["rank"]) for route in item[1]),
    )
    runbooks = []
    for index, (_key, group) in enumerate(ordered, start=1):
        group = sorted(group, key=lambda route: int(route["orbitIndex"]))
        first = group[0]
        anchor = first["sourceAnchor"]
        action = first["actionArtifact"]
        orbit_counts = {int(route["length24OrbitCount"]) for route in group}
        if len(orbit_counts) != 1:
            raise SealFailure("one factor task has inconsistent orbit counts")
        orbit_count = next(iter(orbit_counts))
        orbit_indexes = [int(route["orbitIndex"]) for route in group]
        if len(orbit_indexes) != len(set(orbit_indexes)):
            raise SealFailure("duplicate route orbit in factor task")
        output = DATA / f"gold_{prefix}_factor_{index:04d}_result.jsonl"
        temporary = output.with_suffix(output.suffix + ".tmp")
        if output.exists() or temporary.exists():
            raise SealFailure(f"planned factor output already exists: {output}")
        command = [
            "/usr/local/bin/sage",
            "-python",
            "pair_sum_one.sage.py",
            str(anchor["submissionId"]),
            str(anchor["polynomialIndex"]),
            "--orbit-map",
            str(action["path"]),
            "--expected-source-hash",
            str(anchor["coefficientSha256"]),
        ]
        if orbit_count == 1:
            labels = {
                parse_pair(pair)[0]
                for route in group
                for pair in route["possibleTargetPairs"]
            }
            if len(labels) != 1:
                raise SealFailure("single-orbit task has multiple target labels")
            command.extend(["--expected-target", next(iter(labels))])
        else:
            command.append("--all-degree-24")
        command.extend(["--output-jsonl", str(output.relative_to(ROOT))])
        guarded = (
            f"test ! -e {shlex.quote(str(output.relative_to(ROOT)))} && "
            + " ".join(shlex.quote(value) for value in command)
        )
        runbooks.append({
            "factorTaskId": f"{prefix}_factor_{index:04d}",
            "status": status,
            "sourcePair": first["sourcePair"],
            "sourceAnchor": anchor,
            "actionArtifact": action,
            "length24OrbitCount": orbit_count,
            "routes": [
                {
                    "routeId": route["routeId"],
                    "certaintyClass": route["certaintyClass"],
                    "orbitIndex": int(route["orbitIndex"]),
                    "compatibleSuccessMass": route["compatibleSuccessMass"],
                    "conditionalSuccessFractionExact": route[
                        "conditionalSuccessFractionExact"
                    ],
                    "possibleTargetPairs": route["possibleTargetPairs"],
                    "currentGoldTargetPairs": route["currentGoldTargetPairs"],
                    "profileArtifacts": route["profileArtifacts"],
                }
                for route in group
            ],
            "output": str(output.relative_to(ROOT)),
            "heavyCommand": command,
            "guardedShellCommand": guarded,
            "guards": {
                "outputAbsentAtSeal": True,
                "sourceHashPinned": True,
                "currentTargetReceiptOutboxAndOwnershipMustBeRechecked": True,
                "exactResultPairMustBelongToRouteOptionSet": True,
                "submissionAuthorized": False,
            },
        })
    return runbooks


def main() -> int:
    for path in (SAFE_RUNBOOKS, CONDITIONAL_RUNBOOKS, CERTIFICATE):
        if path.exists():
            raise SealFailure(f"refusing to overwrite sealed artifact: {path}")

    plan = read_json(PLAN)
    if (
        plan.get("schemaVersion") != "gold-profile-backfill-one-worker-plan-v1"
        or plan.get("status") != "ready_waiting_for_root_heavy_clearance"
        or plan.get("coefficientMaterialIncluded") is not False
        or plan.get("credentialMaterialIncluded") is not False
        or len(plan.get("selectedSignatures") or []) != 32
    ):
        raise SealFailure("backfill plan metadata changed")
    # The plan intentionally pins the *pre*-backfill gold certificate, which
    # the requested re-intersection has now replaced.  All other execution
    # inputs remain pinned and are revalidated here.
    plan_artifacts = plan["artifacts"]
    for key in ("ranking", "input", "emptyPriorInput", "worker", "coordinator"):
        backfill.pinned_artifact(plan_artifacts[key])
    for item in plan_artifacts["actionMaps"]:
        backfill.pinned_artifact(item)
    profile_rows = read_jsonl(PROFILE_OUTPUT)
    profile_validation = validate_profiles(plan, profile_rows)

    gold = read_json(GOLD_CERTIFICATE)
    if (
        gold.get("status") != "certified_fresh_gold_candidates_found"
        or not all((gold.get("checks") or {}).values())
        or gold.get("coefficientMaterialIncluded") is not False
        or gold.get("credentialMaterialIncluded") is not False
    ):
        raise SealFailure("full-ledger gold re-intersection is not fully certified")
    profile_artifacts = (gold.get("sealedPairCorpus") or {}).get(
        "profileArtifacts"
    ) or []
    if artifact(PROFILE_OUTPUT) not in profile_artifacts:
        raise SealFailure("full-ledger re-intersection did not include backfill output")

    classes = gold["classifications"]
    deterministic = list(classes["deterministicSingleOrbitRunbooks"])
    all_safe = list(classes["allCompatibleSafeRoutes"])
    conditional = list(classes["conditionalRoutes"])
    safe = [*deterministic, *all_safe]
    for route in deterministic:
        if (
            route.get("certaintyClass") != "deterministic_single_orbit"
            or len(route.get("possibleTargetPairs") or []) != 1
            or route.get("possibleTargetPairs") != route.get("currentGoldTargetPairs")
        ):
            raise SealFailure("invalid deterministic safe route")
    for route in all_safe:
        if (
            route.get("certaintyClass") != "all_compatible_safe"
            or route.get("possibleTargetPairs") != route.get("currentGoldTargetPairs")
            or route.get("conditionalSuccessFractionExact") != "1"
        ):
            raise SealFailure("invalid all-compatible safe route")
    for route in conditional:
        if (
            route.get("certaintyClass") != "conditional"
            or not set(route.get("currentGoldTargetPairs") or [])
            < set(route.get("possibleTargetPairs") or [])
        ):
            raise SealFailure("invalid conditional route classification")

    route_ids = [route["routeId"] for route in [*safe, *conditional]]
    route_keys = [
        (
            route["sourcePair"],
            int(route["orbitIndex"]),
            tuple(route["possibleTargetPairs"]),
        )
        for route in [*safe, *conditional]
    ]
    if len(route_ids) != len(set(route_ids)) or len(route_keys) != len(set(route_keys)):
        raise SealFailure("re-intersection route set is not deduplicated")

    safe_runbooks = grouped_runbooks(
        safe,
        "safe",
        "ready_waiting_for_explicit_heavy_clearance_after_boundary_recheck",
    )
    conditional_runbooks = grouped_runbooks(
        conditional,
        "conditional",
        "held_conditional_factor_only_no_worker_authorized",
    )
    backfill_path = str(PROFILE_OUTPUT.relative_to(ROOT))
    newly_unlocked_safe = [
        route for route in safe if backfill_path in route.get("profileArtifacts", [])
    ]
    newly_unlocked_conditional = [
        route
        for route in conditional
        if backfill_path in route.get("profileArtifacts", [])
    ]

    safe_value = {
        "schemaVersion": "gold-postbackfill-safe-factor-runbooks-v1",
        "status": "safe_routes_staged_offline_waiting_for_explicit_heavy_clearance",
        "parentGoldCertificate": artifact(GOLD_CERTIFICATE),
        "runbooks": safe_runbooks,
        "routeCount": len(safe),
        "factorTaskCount": len(safe_runbooks),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    conditional_value = {
        "schemaVersion": "gold-postbackfill-conditional-factor-runbooks-v1",
        "status": "conditional_routes_isolated_and_factor_deduplicated_no_worker_authorized",
        "parentGoldCertificate": artifact(GOLD_CERTIFICATE),
        "runbooks": conditional_runbooks,
        "routeCount": len(conditional),
        "factorTaskCount": len(conditional_runbooks),
        "distinctCurrentGoldTargets": len(classes["conditionalTargetIndex"]),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    atomic_json(SAFE_RUNBOOKS, safe_value)
    atomic_json(CONDITIONAL_RUNBOOKS, conditional_value)

    certificate = {
        "schemaVersion": "gold-profile-backfill-postintersection-frontier-v1",
        "status": "certified_safe_staged_conditional_deduplicated_no_pair_factor_run",
        "parents": {
            "backfillPlan": artifact(PLAN),
            "profileOutput": artifact(PROFILE_OUTPUT),
            "goldReintersection": artifact(GOLD_CERTIFICATE),
        },
        "profileValidation": profile_validation,
        "frontier": {
            "deterministicSingleOrbitRoutes": len(deterministic),
            "allCompatibleSafeRoutes": len(all_safe),
            "safeFactorTasks": len(safe_runbooks),
            "conditionalRoutes": len(conditional),
            "conditionalFactorTasksAfterSourceDeduplication": len(
                conditional_runbooks
            ),
            "conditionalDistinctCurrentGoldTargets": len(
                classes["conditionalTargetIndex"]
            ),
            "newlyUnlockedSafeRoutesUsingBackfill": len(newly_unlocked_safe),
            "newlyUnlockedConditionalRoutesUsingBackfill": len(
                newly_unlocked_conditional
            ),
            "newlyUnlockedConditionalSourcePairs": len(
                {route["sourcePair"] for route in newly_unlocked_conditional}
            ),
        },
        "artifacts": {
            "safeRunbooks": artifact(SAFE_RUNBOOKS),
            "conditionalRunbooks": artifact(CONDITIONAL_RUNBOOKS),
        },
        "checks": {
            "allSelectedProfileOutputsExactAndCovered": True,
            "fullGoldReintersectionChecksTrue": True,
            "backfillProfileArtifactIncluded": True,
            "safeRoutesHaveOnlyCurrentGoldOutcomes": True,
            "conditionalRoutesHaveMixedOutcomeSets": True,
            "routeIdsAndRouteKeysDeduplicated": True,
            "factorTasksDeduplicatedBySourceAnchorAndAction": True,
            "plannedOutputsAbsent": True,
            "noPairFactorWorkerLaunched": True,
            "coefficientAndCredentialPayloadOmitted": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    atomic_json(CERTIFICATE, certificate)
    print(json.dumps({
        "status": certificate["status"],
        "profileValidation": profile_validation,
        "frontier": certificate["frontier"],
        "safeRunbooks": artifact(SAFE_RUNBOOKS),
        "conditionalRunbooks": artifact(CONDITIONAL_RUNBOOKS),
        "certificate": artifact(CERTIFICATE),
        "networkCalls": 0,
        "submissionCalls": 0,
        "heavyWorkersLaunched": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SealFailure, backfill.GuardFailure, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

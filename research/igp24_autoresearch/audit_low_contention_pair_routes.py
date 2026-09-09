#!/usr/bin/env python3
"""Seal a coefficient-free tc1/tc2 unordered-pair route audit.

This is an offline, light-only audit.  It joins the sealed unordered-pair
action chain through v14 to the current accepted-scoreable signature boundary,
supplements it with exact cached complex-conjugation profiles, and looks for
currently unowned/nonbaseline target pairs with team count one or two.

No polynomial arithmetic is performed.  Cached exact SINGLE-output
construction lineage is used only to close already-tested source anchors and
to prove that no already-constructed novel low-contention output was missed.
The emitted certificate contains hashes and pair labels, never coefficients.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import prepare_v11_pair_delta as helper
import stage_single_exact_census as exact_census


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
V14 = DATA / "autopilot_pair_delta_20260722_v14"
PLAN = V14 / "provenance_plan.json"
BOUNDARY = V14 / "group_input.jsonl"
TERMINAL_ACTION_MAP = V14 / "missing_pair_all.jsonl"
CERTIFICATE = DATA / "low_contention_pair_routes_certificate.json"
SUMMARY = DATA / "low_contention_pair_routes_summary.json"

PROFILE_PATHS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
    DATA / "agent_pair_sibling_shard2_profiles.jsonl",
    DATA / "agent_rank10_pair_stage2_profiles.jsonl",
    DATA / "agent_rank11_pair_stage2_profiles.jsonl",
    DATA / "agent_gold_b_backfill_profiles.jsonl",
    DATA / "agent_gold_a_missing_profiles.jsonl",
    DATA / "gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl",
)

RUNBOOK_LIMIT = 12
PAIR_RE = re.compile(r"24T([1-9][0-9]*)\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
COEFFICIENT_LINE_RE = re.compile(
    r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])"
)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected one JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    result = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{line_number}")
        result.append(value)
    return result


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def pair_sort_key(pair: tuple[str, int]) -> tuple[int, int]:
    match = PAIR_RE.fullmatch(pair[0])
    if match is None:
        raise ValueError(f"invalid label: {pair[0]!r}")
    return int(match.group(1)), int(pair[1])


def canonical_digest(rows: list[object]) -> str:
    text = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def action_core(row: dict) -> tuple:
    return (
        int(row["sourceT"]),
        int(row["length24OrbitCount"]),
        tuple(int(value) for value in row.get("orbitSizes") or []),
        tuple(
            sorted(
                (
                    int(target["orbitIndex"]),
                    str(target["targetLabel"]),
                    int(target["targetT"]),
                    int(target.get("orbitSize", 24)),
                    int(target.get("imageOrder", -1)),
                    int(target.get("kernelOrder", -1)),
                )
                for target in row.get("targets") or []
            )
        ),
    )


def load_sealed_actions() -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    plan = read_json(PLAN)
    prior = ((plan.get("artifacts") or {}).get("priorMaps") or [])
    if len(prior) != 17:
        raise ValueError(f"expected 17 pinned prior maps through v13, found {len(prior)}")

    artifacts = []
    paths = []
    for item in prior:
        path = ROOT / str(item["path"])
        digest = helper.sha256_path(path)
        if digest != str(item["sha256"]):
            raise ValueError(f"sealed action-map hash mismatch: {path}")
        artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": digest})
        paths.append(path)

    if paths[-1] != DATA / "autopilot_pair_delta_20260722_v13/missing_pair_all.jsonl":
        raise ValueError("sealed prior chain does not terminate at v13")
    terminal_digest = helper.sha256_path(TERMINAL_ACTION_MAP)
    artifacts.append(
        {
            "path": str(TERMINAL_ACTION_MAP.relative_to(ROOT)),
            "sha256": terminal_digest,
        }
    )
    paths.append(TERMINAL_ACTION_MAP)

    actions: dict[str, dict] = {}
    provenance: dict[str, dict] = {}
    for path, artifact in zip(paths, artifacts):
        for row in read_jsonl(path):
            label = str(row["sourceLabel"])
            if path == TERMINAL_ACTION_MAP and (
                row.get("status") != "certified"
                or not helper.certificate_is_exact(row)
            ):
                raise ValueError(f"uncertified terminal v14 action row: {label}")
            if label in actions and action_core(actions[label]) != action_core(row):
                raise ValueError(f"conflicting sealed actions for {label}")
            actions[label] = row
            provenance[label] = artifact

    if len(actions) != 6_411:
        raise ValueError(f"unexpected sealed action-label count: {len(actions)}")
    return actions, provenance, artifacts


def profile_targets(profile: dict) -> list[dict]:
    value = profile.get("targets", profile.get("orbitSignatures"))
    if not isinstance(value, list):
        raise ValueError("profile has no target-signature list")
    return value


def load_profiles(
    actions: dict[str, dict], action_artifacts: list[dict]
) -> tuple[dict[str, dict[tuple[int, int], dict]], dict[tuple[str, int], set[str]], list[dict]]:
    # The sealed maps themselves contain exact signature slices for every
    # post-map delta.  The supplemental caches fill exact profiles for older
    # source labels.  Overlapping orbit claims must agree exactly.
    action_paths = [ROOT / artifact["path"] for artifact in action_artifacts]
    paths = [*action_paths, *PROFILE_PATHS]
    artifacts = []
    seen_paths = set()
    profiles: dict[str, dict[tuple[int, int], dict]] = defaultdict(dict)
    provenance: dict[tuple[str, int], set[str]] = defaultdict(set)

    for path in paths:
        path = path.resolve()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if not path.is_file():
            raise ValueError(f"missing exact profile artifact: {path}")
        artifact = {
            "path": str(path.relative_to(ROOT)),
            "sha256": helper.sha256_path(path),
        }
        artifacts.append(artifact)
        for row in read_jsonl(path):
            source_profiles = row.get("profiles") or []
            if not source_profiles:
                continue
            if row.get("status") not in (None, "certified"):
                continue
            label = str(row["sourceLabel"])
            action = actions.get(label)
            if action is None:
                continue
            action_targets = {
                int(target["orbitIndex"]): str(target["targetLabel"])
                for target in action.get("targets") or []
            }
            for profile in source_profiles:
                source_r = int(profile["sourceR"])
                class_index = int(profile["classIndex"])
                class_size = int(profile["classSize"])
                target_map = {
                    int(target["orbitIndex"]): (
                        str(target["targetLabel"]), int(target["targetR"])
                    )
                    for target in profile_targets(profile)
                    if int(target["orbitIndex"]) in action_targets
                }
                for orbit_index, pair in target_map.items():
                    if pair[0] != action_targets[orbit_index]:
                        raise ValueError(
                            f"profile/action mismatch for {label} orbit {orbit_index}"
                        )
                key = (source_r, class_index)
                old = profiles[label].get(key)
                if old is not None:
                    if int(old["classSize"]) != class_size:
                        raise ValueError(f"class-size conflict for {label} {key}")
                    merged = dict(old["targets"])
                    for orbit_index, pair in target_map.items():
                        if orbit_index in merged and merged[orbit_index] != pair:
                            raise ValueError(
                                f"profile target conflict for {label} {key} orbit {orbit_index}"
                            )
                        merged[orbit_index] = pair
                    old["targets"] = merged
                else:
                    profiles[label][key] = {
                        "classIndex": class_index,
                        "classSize": class_size,
                        "sourceR": source_r,
                        "targets": target_map,
                    }
                provenance[(label, source_r)].add(artifact["path"])
    return profiles, provenance, artifacts


def load_ledger_snapshot(connection: sqlite3.Connection) -> dict:
    connection.row_factory = sqlite3.Row
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    known_pairs = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE label IS NOT NULL AND r IS NOT NULL"
        )
    }
    targets = {
        (str(row["label"]), int(row["r"])): {
            "teamCount": int(row["team_count"]),
            "discovered": bool(row["discovered"]),
            "minimumDiscAbs": (
                str(row["minimum_disc_abs"])
                if row["minimum_disc_abs"] is not None else None
            ),
            "generatedAt": str(row["generated_at"]),
        }
        for row in connection.execute(
            "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at "
            "FROM targets"
        )
    }
    if len(targets) != 165_836 or len({label for label, _r in targets}) != 25_000:
        raise ValueError("target cache is incomplete")

    boundary_pairs = helper.source_pairs(read_jsonl(BOUNDARY))
    if len(boundary_pairs) != 21_927 or not boundary_pairs <= owned:
        raise ValueError(
            "current accepted-scoreable signatures regressed behind the sealed v14 boundary"
        )
    return {
        "baseline": baseline,
        "owned": owned,
        "knownPairs": known_pairs,
        "targets": targets,
        "boundaryPairs": boundary_pairs,
        "postV14Pairs": owned - boundary_pairs,
    }


def exact_candidate_and_receipt_snapshot(
    connection: sqlite3.Connection,
) -> dict:
    candidates, corpus = exact_census.scan_candidates(DATA)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for candidate in candidates:
        pair_index[str(candidate["coefficientSha256"])].add(
            (str(candidate["targetLabel"]), int(candidate["targetR"]))
        )
    ambiguous_hashes = {
        digest for digest, pairs in pair_index.items() if len(pairs) != 1
    }
    receipt_hashes, receipt_pairs, receipt_audit = exact_census.receipt_exclusions(
        RECEIPTS, DATA, connection, pair_index
    )
    known_candidate_hashes = exact_census.query_known_hashes(
        connection, set(pair_index)
    )

    validated = []
    invalid_sources = 0
    for candidate in candidates:
        if exact_census.validate_source_pins(connection, candidate):
            validated.append(candidate)
        else:
            invalid_sources += 1
    return {
        "candidates": validated,
        "corpus": corpus,
        "candidatePairIndex": pair_index,
        "ambiguousHashes": ambiguous_hashes,
        "knownCandidateHashes": known_candidate_hashes,
        "receiptHashes": receipt_hashes,
        "receiptPairs": receipt_pairs,
        "receiptAudit": receipt_audit,
        "invalidSources": invalid_sources,
    }


def enumerate_routes(
    snapshot: dict,
    actions: dict[str, dict],
    action_provenance: dict[str, dict],
    profiles: dict[str, dict[tuple[int, int], dict]],
    profile_provenance: dict[tuple[str, int], set[str]],
    receipt_pairs: set[tuple[str, int]],
) -> tuple[list[dict], dict]:
    baseline = snapshot["baseline"]
    owned = snapshot["owned"]
    known_pairs = snapshot["knownPairs"]
    targets = snapshot["targets"]
    eligible = {
        pair
        for pair, state in targets.items()
        if state["teamCount"] in (1, 2)
        and pair not in baseline
        and pair not in owned
        and pair not in known_pairs
        and pair not in receipt_pairs
    }
    eligible_without_receipts = {
        pair
        for pair, state in targets.items()
        if state["teamCount"] in (1, 2)
        and pair not in baseline
        and pair not in owned
        and pair not in known_pairs
    }

    resolved_source_pairs = set()
    identity_source_pairs = set()
    profile_source_pairs = set()
    action_source_pairs = set()
    routes = []

    for source_pair in sorted(snapshot["owned"], key=pair_sort_key):
        source_label, source_r = source_pair
        action = actions.get(source_label)
        if action is None:
            raise ValueError(f"sealed action chain lacks accepted label {source_label}")
        if int(action.get("length24OrbitCount", 0)) < 1:
            continue
        action_source_pairs.add(source_pair)
        action_targets = {
            int(target["orbitIndex"]): str(target["targetLabel"])
            for target in action.get("targets") or []
        }

        if source_r == 24:
            compatible = [
                {
                    "classIndex": -1,
                    "classSize": 1,
                    "sourceR": 24,
                    "targets": {
                        orbit_index: (label, 24)
                        for orbit_index, label in action_targets.items()
                    },
                }
            ]
            proof_kind = "identity_complex_conjugation"
            identity_source_pairs.add(source_pair)
        else:
            compatible = [
                row
                for (r, _class_index), row in profiles.get(source_label, {}).items()
                if int(r) == source_r
            ]
            proof_kind = "exact_cached_conjugacy_profiles"
            if compatible:
                profile_source_pairs.add(source_pair)
        if not compatible:
            continue
        if any(set(row["targets"]) != set(action_targets) for row in compatible):
            raise ValueError(
                f"incomplete compatible profile for {source_label}/r{source_r}"
            )
        resolved_source_pairs.add(source_pair)

        for orbit_index, target_label in sorted(action_targets.items()):
            outcome_mass: Counter = Counter()
            for row in compatible:
                pair = tuple(row["targets"][orbit_index])
                if pair[0] != target_label:
                    raise ValueError("profile target label differs from sealed action")
                outcome_mass[pair] += int(row["classSize"])
            outcomes = set(outcome_mass)
            low_outcomes = outcomes & eligible
            receipt_collisions = outcomes & receipt_pairs
            if not low_outcomes:
                continue
            team_counts = {pair: targets[pair]["teamCount"] for pair in low_outcomes}
            score_values = [Fraction(1, 2 ** count) for count in team_counts.values()]
            routes.append(
                {
                    "sourcePair": source_pair,
                    "sourceLabel": source_label,
                    "sourceR": source_r,
                    "orbitIndex": orbit_index,
                    "orbitCount": len(action_targets),
                    "targetLabel": target_label,
                    "outcomes": outcomes,
                    "lowOutcomes": low_outcomes,
                    "outcomeMass": outcome_mass,
                    "compatibleClassCount": len(compatible),
                    "compatibleClassMass": sum(outcome_mass.values()),
                    "deterministic": len(outcomes) == 1,
                    "guaranteedLowContention": outcomes <= eligible,
                    "exactScoreLower": min(score_values),
                    "exactScoreUpper": max(score_values),
                    "proofKind": proof_kind,
                    "actionArtifact": action_provenance[source_label],
                    "profileArtifacts": sorted(
                        profile_provenance.get(source_pair, set())
                    ),
                    "receiptCollisions": receipt_collisions,
                }
            )

    coverage = {
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "acceptedLabels": len({label for label, _r in snapshot["owned"]}),
        "actionMappedPairsWithLength24Orbit": len(action_source_pairs),
        "signatureResolvedPairs": len(resolved_source_pairs),
        "signatureResolvedByIdentity": len(identity_source_pairs),
        "signatureResolvedByExactProfiles": len(profile_source_pairs),
        "signatureUnresolvedPairsNotPromoted": len(action_source_pairs - resolved_source_pairs),
        "eligibleTc1Tc2PairsBeforeReceiptExclusion": len(eligible_without_receipts),
        "eligibleTc1Tc2PairsAfterAllPairExclusions": len(eligible),
        "receiptPairCollisionsAtTargetLayer": len(
            eligible_without_receipts & receipt_pairs
        ),
    }
    return routes, coverage


def cached_lineage(
    routes: list[dict], candidate_snapshot: dict, snapshot: dict
) -> dict:
    route_index = {}
    for route in routes:
        if int(route["orbitCount"]) == 1:
            key = (
                route["sourceLabel"],
                int(route["sourceR"]),
                route["targetLabel"],
            )
            route_index[key] = route

    ready = []
    excluded_known = []
    closure_facts = set()
    closure_groups: dict[tuple[str, int, str], dict] = {}
    tested_source_keys = set()
    tested_source_hashes = set()
    receipt_hashes = candidate_snapshot["receiptHashes"]
    receipt_pairs = candidate_snapshot["receiptPairs"]
    known_hashes = candidate_snapshot["knownCandidateHashes"]
    ambiguous = candidate_snapshot["ambiguousHashes"]

    for candidate in candidate_snapshot["candidates"]:
        if (candidate.get("proof") or {}).get("schema") != "pair_sum_single_v1":
            continue
        pins = candidate.get("sourcePins") or []
        if len(pins) != 1:
            continue
        pin = pins[0]
        source_key = (str(pin["submissionId"]), int(pin["polynomialIndex"]))
        source_hash = str(pin["coefficientSha256"])
        source_pair = (str(pin["label"]), int(pin["r"]))
        output_pair = (
            str(candidate["targetLabel"]), int(candidate["targetR"])
        )
        output_hash = str(candidate["coefficientSha256"])
        tested_source_keys.add(source_key)
        tested_source_hashes.add(source_hash)

        state = snapshot["targets"].get(output_pair)
        locally_eligible = (
            state is not None
            and state["teamCount"] in (1, 2)
            and output_pair not in snapshot["baseline"]
            and output_pair not in snapshot["owned"]
            and output_pair not in snapshot["knownPairs"]
            and output_pair not in receipt_pairs
        )
        if locally_eligible:
            fact = {
                "source": source_key,
                "sourcePair": source_pair,
                "targetPair": output_pair,
                "outputHash": output_hash,
                "artifact": str((candidate.get("proof") or {}).get("artifact")),
            }
            if (
                output_hash not in known_hashes
                and output_hash not in receipt_hashes
                and output_hash not in ambiguous
            ):
                ready.append(fact)
            else:
                excluded_known.append(fact)

        route = route_index.get((source_pair[0], source_pair[1], output_pair[0]))
        if route is None:
            continue
        if output_pair not in route["outcomes"]:
            raise ValueError(
                f"cached exact outcome escapes profile possibilities: {source_pair} -> {output_pair}"
            )
        if output_pair in route["lowOutcomes"]:
            continue
        fact_key = (source_key, source_hash, output_pair, output_hash)
        if fact_key in closure_facts:
            continue
        closure_facts.add(fact_key)
        group_key = (source_pair[0], source_pair[1], output_pair[0])
        group = closure_groups.setdefault(
            group_key,
            {
                "sourcePair": pair_text(source_pair),
                "targetLabel": output_pair[0],
                "currentLowOutcomes": sorted(
                    (pair_text(pair) for pair in route["lowOutcomes"]),
                    key=str,
                ),
                "closedAnchorCount": 0,
                "actualMissPairs": Counter(),
                "artifacts": set(),
            },
        )
        group["closedAnchorCount"] += 1
        group["actualMissPairs"][pair_text(output_pair)] += 1
        group["artifacts"].add(str((candidate.get("proof") or {}).get("artifact")))

    groups = []
    for key in sorted(closure_groups, key=lambda value: (int(value[0][3:]), value[1], value[2])):
        group = closure_groups[key]
        groups.append(
            {
                **{k: v for k, v in group.items() if k not in ("actualMissPairs", "artifacts")},
                "actualMissPairs": dict(sorted(group["actualMissPairs"].items())),
                "artifacts": sorted(group["artifacts"]),
            }
        )
    return {
        "testedSourceKeys": tested_source_keys,
        "testedSourceHashes": tested_source_hashes,
        "readyNovelLowCandidates": ready,
        "knownOrReceiptLowCandidates": excluded_known,
        "closedAnchorCount": len(closure_facts),
        "closedRouteSignatureCount": len(groups),
        "closureGroups": groups,
    }


def select_anchor(
    connection: sqlite3.Connection,
    source_pair: tuple[str, int],
    tested_keys: set[tuple[str, int]],
    tested_hashes: set[str],
) -> dict | None:
    rows = connection.execute(
        "SELECT v.submission_id,v.polynomial_index,p.coefficient_hash,"
        "length(p.coefficients) AS coefficient_bytes "
        "FROM verifications v JOIN polynomials p "
        "USING(submission_id,polynomial_index) "
        "WHERE v.status='accepted' AND v.scoreable=1 AND v.label=? AND v.r=? "
        "ORDER BY coefficient_bytes,v.submission_id,v.polynomial_index",
        source_pair,
    )
    for row in rows:
        key = (str(row["submission_id"]), int(row["polynomial_index"]))
        digest = str(row["coefficient_hash"])
        if key in tested_keys or digest in tested_hashes:
            continue
        if HASH_RE.fullmatch(digest) is None:
            raise ValueError("invalid ledger source hash")
        return {
            "submissionId": key[0],
            "polynomialIndex": key[1],
            "coefficientSha256": digest,
            "coefficientBytes": int(row["coefficient_bytes"]),
        }
    return None


def route_rank(route: dict) -> tuple:
    target_pair = next(iter(route["lowOutcomes"]))
    return (
        0 if snapshot_target_count(route) == 1 else 1,
        0 if route["deterministic"] else 1,
        0 if int(route["orbitCount"]) == 1 else 1,
        int((route.get("anchor") or {}).get("coefficientBytes", 10**12)),
        *pair_sort_key(target_pair),
        *pair_sort_key(route["sourcePair"]),
        int(route["orbitIndex"]),
    )


def snapshot_target_count(route: dict) -> int:
    values = set(int(value) for value in route["targetTeamCounts"].values())
    return min(values)


def public_route(route: dict) -> dict:
    return {
        "sourcePair": pair_text(route["sourcePair"]),
        "orbitIndex": int(route["orbitIndex"]),
        "orbitCount": int(route["orbitCount"]),
        "deterministic": bool(route["deterministic"]),
        "guaranteedLowContention": bool(route["guaranteedLowContention"]),
        "proofKind": route["proofKind"],
        "compatibleClassCount": int(route["compatibleClassCount"]),
        "compatibleClassMass": int(route["compatibleClassMass"]),
        "possibleTargetPairs": [
            pair_text(pair) for pair in sorted(route["outcomes"], key=pair_sort_key)
        ],
        "lowContentionTargetPairs": [
            pair_text(pair) for pair in sorted(route["lowOutcomes"], key=pair_sort_key)
        ],
        "targetTeamCounts": dict(sorted(route["targetTeamCounts"].items())),
        "exactMarginalScoreLower": fraction_text(route["exactScoreLower"]),
        "exactMarginalScoreUpper": fraction_text(route["exactScoreUpper"]),
        "constructionCostTier": (
            "one_single_orbit_pair_resolvent"
            if int(route["orbitCount"]) == 1
            else "one_multi_orbit_pair_resolvent_plus_assignment"
        ),
        "sourceCoefficientBytes": (
            int(route["anchor"]["coefficientBytes"])
            if route.get("anchor") is not None else None
        ),
    }


def make_runbooks(
    routes: list[dict], snapshot: dict, limit: int
) -> list[dict]:
    # Heavy handoff is intentionally restricted to deterministic single-orbit
    # routes.  Each worker then emits exactly one factor with an exact pair.
    candidates = [
        route
        for route in routes
        if route["deterministic"]
        and int(route["orbitCount"]) == 1
        and route.get("anchor") is not None
    ]
    best_by_target: dict[tuple[str, int], dict] = {}
    for route in sorted(candidates, key=route_rank):
        target_pair = next(iter(route["lowOutcomes"]))
        best_by_target.setdefault(target_pair, route)
    selected = sorted(best_by_target.values(), key=route_rank)[:limit]

    runbooks = []
    for index, route in enumerate(selected, start=1):
        source_pair = route["sourcePair"]
        target_pair = next(iter(route["lowOutcomes"]))
        anchor = route["anchor"]
        route_id = (
            f"lc{index:02d}_{source_pair[0]}_r{source_pair[1]}_to_"
            f"{target_pair[0]}_r{target_pair[1]}"
        )
        output = DATA / f"low_contention_{route_id}_result.jsonl"
        if output.exists() or output.with_suffix(output.suffix + ".tmp").exists():
            raise ValueError(f"isolated runbook output already exists: {output}")
        action_path = ROOT / route["actionArtifact"]["path"]
        command = [
            "/usr/local/bin/sage", "-python", "pair_sum_one.sage.py",
            anchor["submissionId"], str(anchor["polynomialIndex"]),
            "--orbit-map", str(action_path.relative_to(ROOT)),
            "--expected-source-hash", anchor["coefficientSha256"],
            "--expected-target", target_pair[0],
            "--output-jsonl", str(output.relative_to(ROOT)),
        ]
        shell_command = (
            f"test ! -e {shlex.quote(str(output.relative_to(ROOT)))} && "
            + " ".join(shlex.quote(value) for value in command)
        )
        state = snapshot["targets"][target_pair]
        runbooks.append(
            {
                "routeId": route_id,
                "status": "ready_waiting_for_heavy_slot",
                "source": {
                    "submissionId": anchor["submissionId"],
                    "polynomialIndex": anchor["polynomialIndex"],
                    "label": source_pair[0],
                    "r": source_pair[1],
                    "coefficientSha256": anchor["coefficientSha256"],
                    "coefficientBytes": anchor["coefficientBytes"],
                    "ledgerStatus": "accepted_scoreable",
                    "notPreviouslyPairConstructed": True,
                },
                "target": {
                    "label": target_pair[0],
                    "r": target_pair[1],
                    "teamCountAtSeal": state["teamCount"],
                    "discoveredAtSeal": state["discovered"],
                    "minimumDiscAbsAtSeal": state["minimumDiscAbs"],
                    "projectedMarginalScoreExact": fraction_text(
                        Fraction(1, 2 ** state["teamCount"])
                    ),
                    "projectionQualifier": "upper_bound_before_discriminant_penalty",
                },
                "exactAction": {
                    "orbitIndex": route["orbitIndex"],
                    "length24OrbitCount": route["orbitCount"],
                    "deterministicAcrossCompatibleClasses": True,
                    "compatibleClassCount": route["compatibleClassCount"],
                    "proofKind": route["proofKind"],
                    "actionArtifact": route["actionArtifact"],
                    "profileArtifacts": route["profileArtifacts"],
                },
                "guards": {
                    "outputAbsentAtSeal": True,
                    "targetNonbaselineAtSeal": target_pair not in snapshot["baseline"],
                    "targetUnownedAtSeal": target_pair not in snapshot["owned"],
                    "targetNotKnownVerificationPairAtSeal": target_pair not in snapshot["knownPairs"],
                    "targetNotReceiptPairAtSeal": True,
                    "sourceHashPinned": True,
                    "postRunMustRecheckTargetAndOutputHash": True,
                    "submissionAuthorized": False,
                },
                "output": str(output.relative_to(ROOT)),
                "heavyCommand": command,
                "guardedShellCommand": shell_command,
                "postflightCommand": [
                    "python3", "validate_low_contention_pair_route.py",
                    "--certificate", str(CERTIFICATE.relative_to(ROOT)),
                    "--route-id", route_id,
                    "--result", str(output.relative_to(ROOT)),
                ],
            }
        )
    return runbooks


def main() -> int:
    if CERTIFICATE.exists() or SUMMARY.exists():
        raise FileExistsError("refusing to overwrite sealed low-contention audit")

    actions, action_provenance, action_artifacts = load_sealed_actions()
    profiles, profile_provenance, profile_artifacts = load_profiles(
        actions, action_artifacts
    )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = load_ledger_snapshot(connection)
        candidate_snapshot = exact_candidate_and_receipt_snapshot(connection)
        routes, coverage = enumerate_routes(
            snapshot,
            actions,
            action_provenance,
            profiles,
            profile_provenance,
            candidate_snapshot["receiptPairs"],
        )
        lineage = cached_lineage(routes, candidate_snapshot, snapshot)

        for route in routes:
            route["targetTeamCounts"] = {
                pair_text(pair): snapshot["targets"][pair]["teamCount"]
                for pair in route["lowOutcomes"]
            }
            route["anchor"] = select_anchor(
                connection,
                route["sourcePair"],
                lineage["testedSourceKeys"],
                lineage["testedSourceHashes"],
            )
    finally:
        connection.close()

    if lineage["readyNovelLowCandidates"]:
        raise ValueError(
            "cached novel exact low-contention candidates exist; stage them before heavy runbooks"
        )

    deterministic = [route for route in routes if route["deterministic"]]
    conditional = [route for route in routes if not route["deterministic"]]
    ranked_distinct = {}
    for route in sorted(routes, key=route_rank):
        for pair in sorted(route["lowOutcomes"], key=pair_sort_key):
            ranked_distinct.setdefault(pair, route)
    ranked_rows = [
        public_route(route)
        for _pair, route in list(ranked_distinct.items())[:100]
    ]
    runbooks = make_runbooks(routes, snapshot, RUNBOOK_LIMIT)

    target_rows = [
        [label, r, state["teamCount"], state["discovered"], state["minimumDiscAbs"], state["generatedAt"]]
        for (label, r), state in sorted(snapshot["targets"].items(), key=lambda item: pair_sort_key(item[0]))
    ]
    accepted_rows = [
        [label, r] for label, r in sorted(snapshot["owned"], key=pair_sort_key)
    ]
    receipt_audit = candidate_snapshot["receiptAudit"]
    score_total = sum(
        Fraction(1, 2 ** int(row["target"]["teamCountAtSeal"]))
        for row in runbooks
    )
    team_distribution = Counter(
        int(row["target"]["teamCountAtSeal"]) for row in runbooks
    )

    certificate = {
        "schemaVersion": "low-contention-unordered-pair-route-audit-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_light_only_runbooks_ready",
        "method": (
            "sealed unordered-pair actions through v14 + exact cached "
            "complex-conjugation profiles + current accepted-scoreable "
            "signature boundary + exact cached SINGLE construction lineage"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedPairSetSha256": canonical_digest(accepted_rows),
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": canonical_digest(target_rows),
            "targetGeneratedAtMin": min(state["generatedAt"] for state in snapshot["targets"].values()),
            "targetGeneratedAtMax": max(state["generatedAt"] for state in snapshot["targets"].values()),
            "v14Boundary": helper.relative_artifact(BOUNDARY),
        },
        "artifacts": {
            "sealedActionMaps": action_artifacts,
            "profileArtifacts": profile_artifacts,
            "candidateCorpus": candidate_snapshot["corpus"],
            "database": {"path": str(DB.relative_to(ROOT))},
            "receiptsDirectory": str(RECEIPTS.relative_to(ROOT)),
            "auditProgram": helper.relative_artifact(Path(__file__).resolve()),
        },
        "coverage": coverage,
        "receiptExclusion": {
            "receiptCount": receipt_audit["receiptCount"],
            "receiptPolynomialHashes": receipt_audit["receiptPolynomialHashes"],
            "receiptTargetPairs": receipt_audit["receiptTargetPairs"],
            "queuedPossiblePairMapRows": receipt_audit["queuedPossiblePairMapRows"],
        },
        "knownExclusion": {
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "baselinePairs": len(snapshot["baseline"]),
            "knownCandidateHashesInLedger": len(candidate_snapshot["knownCandidateHashes"]),
            "ambiguousExactCandidateHashes": len(candidate_snapshot["ambiguousHashes"]),
            "invalidCandidateSourcePins": candidate_snapshot["invalidSources"],
        },
        "routeCensus": {
            "structuralRoutes": len(routes),
            "deterministicRoutes": len(deterministic),
            "conditionalRoutes": len(conditional),
            "guaranteedLowContentionRoutes": sum(
                bool(route["guaranteedLowContention"]) for route in routes
            ),
            "distinctLowContentionTargets": len(
                set().union(*(route["lowOutcomes"] for route in routes))
                if routes else set()
            ),
            "deterministicSingleOrbitRoutesWithFreshAnchor": sum(
                route["deterministic"]
                and int(route["orbitCount"]) == 1
                and route.get("anchor") is not None
                for route in routes
            ),
            "teamCountDistributionByRouteOutcome": dict(
                sorted(
                    Counter(
                        snapshot["targets"][pair]["teamCount"]
                        for route in routes for pair in route["lowOutcomes"]
                    ).items()
                )
            ),
        },
        "cachedExactLineage": {
            "validatedSingleOutputCandidates": sum(
                (candidate.get("proof") or {}).get("schema") == "pair_sum_single_v1"
                for candidate in candidate_snapshot["candidates"]
            ),
            "readyNovelLowContentionCandidates": 0,
            "knownOrReceiptLowContentionCandidates": len(
                lineage["knownOrReceiptLowCandidates"]
            ),
            "closedTestedSourceAnchorsAsExactSignatureMisses": lineage["closedAnchorCount"],
            "routeSignaturesWithCachedMissClosures": lineage["closedRouteSignatureCount"],
            "closureGroups": lineage["closureGroups"],
        },
        "ranking": {
            "order": [
                "teamCount 1 before 2",
                "deterministic before conditional",
                "single orbit before multi orbit",
                "shorter accepted source coefficient encoding",
                "target/source pair order",
            ],
            "scoreFormula": "2^(-current_cached_team_count), upper bound before discriminant penalty",
            "topDistinctRoutes": ranked_rows,
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": dict(sorted(team_distribution.items())),
            "marginalScoreExact": fraction_text(score_total),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "checks": {
            "v14BoundaryIsSubsetOfCurrentAcceptedScoreablePairSet": True,
            "allAcceptedLabelsPresentInSealedActionChain": True,
            "allPromotedRoutesUseExactSignatureProfilesOrIdentity": True,
            "allTargetPairsNonbaselineUnownedUnknownAndNonreceipt": True,
            "allCachedCandidateAndReceiptHashesExcluded": True,
            "allRunbooksSingleOrbitDeterministicAndOutputAbsent": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "polynomialArithmeticRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload detected in certificate")
    helper.atomic_text(CERTIFICATE, rendered)

    summary = {
        "schemaVersion": "low-contention-unordered-pair-route-summary-v1",
        "status": certificate["status"],
        "certificate": helper.relative_artifact(CERTIFICATE),
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "signatureResolvedPairs": coverage["signatureResolvedPairs"],
        "structuralRoutes": len(routes),
        "deterministicRoutes": len(deterministic),
        "conditionalRoutes": len(conditional),
        "distinctLowContentionTargets": certificate["routeCensus"]["distinctLowContentionTargets"],
        "cachedExactMissAnchorClosures": lineage["closedAnchorCount"],
        "cachedNovelReadyCandidates": 0,
        "runbooksReady": len(runbooks),
        "runbookTeamCountDistribution": dict(sorted(team_distribution.items())),
        "runbookMarginalScoreExact": fraction_text(score_total),
        "runbookProjectionQualifier": "upper_bound_before_discriminant_penalty",
        "heavyWorkerLaunched": False,
        "submissionCalls": 0,
        "coefficientMaterialIncluded": False,
    }
    helper.atomic_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

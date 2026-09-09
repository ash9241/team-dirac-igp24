#!/usr/bin/env python3
"""Build an exact, local-only census of routes into rank-12 solo pairs.

The action map proves the target transitive-group label on every length-24
pair orbit.  Complex-conjugation profiles then prove which source and target
real-root signatures can occur together.  This script intersects those two
exact GAP computations with Team Dirac's locally verified source fields and
the saved public snapshot of Low hanging fruit's unique placements.

No network requests or submissions are made.  Missing signature profiles are
computed by the crash-isolated ``pair_signature_one.sage.py`` worker and
cached as reproducible evidence for subsequent runs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "ledger.sqlite3"
ORBIT_MAP_PATH = DATA / "pair_orbit_map.jsonl"
SIGNATURE_MAP_PATH = DATA / "pair_signature_map.jsonl"
RANK12_SNAPSHOT_PATH = DATA / "low_hanging_fruit_unique_placements.jsonl"
PROFILE_CACHE_PATH = DATA / "rank12_route_signature_profiles.jsonl"
OUTPUT_PATH = DATA / "rank12_exact_action_candidates.json"
SIGNATURE_WORKER = ROOT / "pair_signature_one.sage.py"
SINGLE_RESULTS_PATH = DATA / "pair_sum_candidates.jsonl"
FROBENIUS_RESULTS_PATH = DATA / "frobenius_assignments.jsonl"

RANK12_TEAM_ID = "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl_atomic(path: Path, rows: Iterable[dict], sort_key) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=sort_key):
            handle.write(
                json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            )
    temporary.replace(path)


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_rows(conn: sqlite3.Connection) -> dict[tuple[str, int], list[dict]]:
    rows: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    query = """
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               p.coefficient_hash,p.original_line,v.field_disc_abs
        FROM verifications AS v
        JOIN polynomials AS p
          ON p.submission_id=v.submission_id
         AND p.polynomial_index=v.polynomial_index
        WHERE v.scoreable=1 AND v.label IS NOT NULL AND v.r IS NOT NULL
        ORDER BY v.label,v.r,length(p.original_line),
                 v.submission_id,v.polynomial_index
    """
    for (
        label,
        t,
        r,
        submission_id,
        polynomial_index,
        coefficient_hash,
        original_line,
        field_disc_abs,
    ) in conn.execute(query):
        key = (str(label), int(r))
        digest = str(coefficient_hash)
        rows[key].setdefault(
            digest,
            {
                "coefficientBytes": len(str(original_line).encode("utf-8")),
                "coefficientSha256": digest,
                "fieldDiscAbs": str(field_disc_abs) if field_disc_abs else None,
                "polynomialIndex": int(polynomial_index),
                "sourceT": int(t),
                "submissionId": str(submission_id),
            },
        )
    return {
        key: sorted(
            values.values(),
            key=lambda item: (
                item["coefficientBytes"],
                item["submissionId"],
                item["polynomialIndex"],
            ),
        )
        for key, values in rows.items()
    }


def verified_profile(profile: dict, orbit_row: dict) -> dict:
    """Cross-check a profile against the independently saved action map."""
    if profile.get("status") not in (None, "certified"):
        raise ValueError(
            f"{profile.get('sourceLabel')} profile status={profile.get('status')}"
        )
    if str(profile["sourceLabel"]) != str(orbit_row["sourceLabel"]):
        raise ValueError("signature/action source-label mismatch")
    if int(profile["sourceT"]) != int(orbit_row["sourceT"]):
        raise ValueError("signature/action source-T mismatch")
    if int(profile["length24OrbitCount"]) != int(
        orbit_row["length24OrbitCount"]
    ):
        raise ValueError("signature/action length-24 count mismatch")

    expected = {
        (int(target["orbitIndex"]), str(target["targetLabel"]))
        for target in orbit_row["targets"]
    }
    for class_profile in profile["profiles"]:
        actual = {
            (int(target["orbitIndex"]), str(target["targetLabel"]))
            for target in class_profile["orbitSignatures"]
        }
        if actual != expected:
            raise ValueError(
                f"{profile['sourceLabel']} class {class_profile['classIndex']} "
                "does not match the action map"
            )
    checked = dict(profile)
    checked["status"] = "certified"
    checked["actionMapCrossCheck"] = "exact_match"
    return checked


def compute_profile(source: tuple[str, int], timeout: int) -> dict:
    label, t = source
    try:
        completed = subprocess.run(
            ["sage", "-python", str(SIGNATURE_WORKER), label, str(t)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "timeout",
        }
    if completed.returncode != 0:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "error",
            "error": completed.stderr[-2000:],
        }
    try:
        row = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            "sourceLabel": label,
            "sourceT": t,
            "status": "invalid_output",
            "error": str(exc),
        }
    row["status"] = "certified"
    return row


def exact_tested_sources(
    conn: sqlite3.Connection,
) -> tuple[dict[tuple[str, int], set[str]], dict[tuple[str, int], list[dict]]]:
    """Return tested source hashes and any exact factors grouped by source pair."""
    row_hash = {
        (str(submission_id), int(polynomial_index)): str(coefficient_hash)
        for submission_id, polynomial_index, coefficient_hash in conn.execute(
            "SELECT submission_id,polynomial_index,coefficient_hash FROM polynomials"
        )
    }
    tested: dict[tuple[str, int], set[str]] = defaultdict(set)
    outcomes: dict[tuple[str, int], list[dict]] = defaultdict(list)

    for row in read_jsonl(SINGLE_RESULTS_PATH):
        if row.get("status") != "certified":
            continue
        source_key = (str(row["sourceLabel"]), int(row["sourceR"]))
        source_row = (
            str(row["sourceSubmissionId"]),
            int(row["sourcePolynomialIndex"]),
        )
        digest = row_hash.get(source_row)
        if digest:
            tested[source_key].add(digest)
        outcomes[source_key].append(
            {
                "coefficientLine": str(row["coefficientLine"]),
                "coefficientSha256": str(row["coefficientSha256"]),
                "factorIndex": int(row["factorIndex"]),
                "sourceCoefficientSha256": digest,
                "sourcePolynomialIndex": source_row[1],
                "sourceSubmissionId": source_row[0],
                "targetLabel": str(row["targetLabel"]),
                "targetR": int(row["targetR"]),
                "proofKind": "exact_pair_orbit_factorization",
            }
        )

    for row in read_jsonl(FROBENIUS_RESULTS_PATH):
        source_key = (str(row["sourceLabel"]), int(row["sourceR"]))
        source_row = (
            str(row["sourceSubmissionId"]),
            int(row["sourcePolynomialIndex"]),
        )
        digest = row_hash.get(source_row)
        if digest:
            tested[source_key].add(digest)
        for factor in row.get("factors") or []:
            exact_label = factor.get("exactTargetLabel")
            if exact_label is None:
                continue
            outcomes[source_key].append(
                {
                    "coefficientLine": str(factor["coefficientLine"]),
                    "coefficientSha256": str(factor["coefficientSha256"]),
                    "factorIndex": int(factor["factorIndex"]),
                    "sourceCoefficientSha256": digest,
                    "sourcePolynomialIndex": source_row[1],
                    "sourceSubmissionId": source_row[0],
                    "targetLabel": str(exact_label),
                    "targetR": int(factor["targetR"]),
                    "proofKind": "exact_pair_factorization_plus_frobenius_assignment",
                }
            )
    return tested, outcomes


def priority_key(candidate: dict) -> tuple:
    return (
        int(candidate["priorityTier"]),
        not bool(candidate["signatureCoverage"]["guaranteedForEveryClass"]),
        -float(candidate["signatureCoverage"]["classSizeFraction"]),
        -int(candidate["ownedSources"]["untestedDistinctPolynomialCount"]),
        int(candidate["target"]["t"]),
        int(candidate["target"]["r"]),
        int(candidate["source"]["t"]),
        int(candidate["source"]["r"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--profile-cache", type=Path, default=PROFILE_CACHE_PATH)
    args = parser.parse_args()

    orbit_rows = read_jsonl(ORBIT_MAP_PATH)
    orbit_by_label = {str(row["sourceLabel"]): row for row in orbit_rows}
    if len(orbit_by_label) != len(orbit_rows):
        raise ValueError("duplicate source label in pair orbit map")

    rank_rows = read_jsonl(RANK12_SNAPSHOT_PATH)
    if not rank_rows:
        raise ValueError("rank-12 placement snapshot is empty")
    if any(str(row["teamId"]) != RANK12_TEAM_ID for row in rank_rows):
        raise ValueError("placement snapshot contains another team")
    if any(int(row["kTeams"]) != 1 for row in rank_rows):
        raise ValueError("placement snapshot contains a non-unique pair")
    rank_pairs = {
        (str(row["label"]), int(row["r"])): row for row in rank_rows
    }
    rank_labels = {label for label, _r in rank_pairs}

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        owned = source_rows(connection)
        tested_hashes, exact_outcomes = exact_tested_sources(connection)
    finally:
        connection.close()
    owned_labels = {label for label, _r in owned}

    relevant_orbits = {
        label: row
        for label, row in orbit_by_label.items()
        if label in owned_labels
        and any(str(target["targetLabel"]) in rank_labels for target in row["targets"])
    }

    profiles_by_label: dict[str, dict] = {}
    profile_sources: dict[str, str] = {}
    for source_name, path in (
        ("pair_signature_map", SIGNATURE_MAP_PATH),
        ("rank12_profile_cache", args.profile_cache),
    ):
        for row in read_jsonl(path):
            label = str(row.get("sourceLabel"))
            if label in relevant_orbits and row.get("status") == "certified":
                profiles_by_label[label] = row
                profile_sources[label] = source_name

    pending = [
        (label, int(row["sourceT"]))
        for label, row in relevant_orbits.items()
        if label not in profiles_by_label
    ]
    failures = []
    if pending:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.workers)
        ) as executor:
            futures = {
                executor.submit(compute_profile, source, args.timeout): source
                for source in pending
            }
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                label = str(row["sourceLabel"])
                if row.get("status") == "certified":
                    profiles_by_label[label] = row
                    profile_sources[label] = "computed_for_rank12_census"
                else:
                    failures.append(row)

    certified_profiles = {}
    for label, row in profiles_by_label.items():
        try:
            certified_profiles[label] = verified_profile(row, relevant_orbits[label])
        except ValueError as exc:
            failures.append(
                {
                    "sourceLabel": label,
                    "status": "cross_check_error",
                    "error": str(exc),
                }
            )
    write_jsonl_atomic(
        args.profile_cache,
        certified_profiles.values(),
        sort_key=lambda row: int(row["sourceT"]),
    )

    candidates = []
    for source_label, orbit_row in relevant_orbits.items():
        profile = certified_profiles.get(source_label)
        if profile is None:
            continue
        source_t = int(orbit_row["sourceT"])
        length24_count = int(orbit_row["length24OrbitCount"])
        target_slots = list(orbit_row["targets"])
        all_faithful = all(int(slot["kernelOrder"]) == 1 for slot in target_slots)

        for (owned_label, source_r), polynomials in owned.items():
            if owned_label != source_label:
                continue
            signature_classes = [
                class_profile
                for class_profile in profile["profiles"]
                if int(class_profile["sourceR"]) == source_r
            ]
            if not signature_classes:
                continue
            total_class_size = sum(int(item["classSize"]) for item in signature_classes)

            route_classes: dict[tuple[str, int], dict[int, list[int]]] = defaultdict(
                lambda: defaultdict(list)
            )
            route_class_sizes: dict[tuple[str, int], dict[int, int]] = defaultdict(dict)
            for class_profile in signature_classes:
                class_index = int(class_profile["classIndex"])
                class_size = int(class_profile["classSize"])
                for outcome in class_profile["orbitSignatures"]:
                    pair = (str(outcome["targetLabel"]), int(outcome["targetR"]))
                    if pair not in rank_pairs:
                        continue
                    route_classes[pair][int(outcome["orbitIndex"])].append(class_index)
                    route_class_sizes[pair][class_index] = class_size

            owned_hashes = {item["coefficientSha256"] for item in polynomials}
            tested_for_source = tested_hashes.get((source_label, source_r), set())
            untested_hashes = owned_hashes - tested_for_source
            source_outcomes = exact_outcomes.get((source_label, source_r), [])

            for target_pair, by_orbit in route_classes.items():
                target_label, target_r = target_pair
                target = rank_pairs[target_pair]
                compatible_indexes = sorted(
                    {index for indexes in by_orbit.values() for index in indexes}
                )
                compatible_size = sum(
                    route_class_sizes[target_pair][index]
                    for index in compatible_indexes
                )
                exact_hits = [
                    outcome
                    for outcome in source_outcomes
                    if (outcome["targetLabel"], int(outcome["targetR"]))
                    == target_pair
                ]
                every_class = all(
                    any(
                        (str(outcome["targetLabel"]), int(outcome["targetR"]))
                        == target_pair
                        for outcome in class_profile["orbitSignatures"]
                    )
                    for class_profile in signature_classes
                )

                if exact_hits:
                    tier = 0
                    tier_name = "exact_factor_ready"
                elif untested_hashes and length24_count == 1:
                    tier = 1
                    tier_name = "single_orbit_untested_owned_field"
                elif untested_hashes and all_faithful:
                    tier = 2
                    tier_name = "multi_orbit_untested_owned_field"
                else:
                    tier = 3
                    tier_name = "structural_only_all_owned_fields_tested_or_unresolvable"

                orbit_evidence = []
                for orbit_index, class_indexes in sorted(by_orbit.items()):
                    slot = next(
                        item
                        for item in target_slots
                        if int(item["orbitIndex"]) == orbit_index
                    )
                    orbit_evidence.append(
                        {
                            "compatibleClassIndexes": sorted(class_indexes),
                            "imageOrder": int(slot["imageOrder"]),
                            "kernelOrder": int(slot["kernelOrder"]),
                            "orbitIndex": orbit_index,
                            "orbitSize": int(slot["orbitSize"]),
                            "targetLabel": str(slot["targetLabel"]),
                        }
                    )

                tested_misses = [
                    {
                        key: outcome[key]
                        for key in (
                            "sourceCoefficientSha256",
                            "sourceSubmissionId",
                            "sourcePolynomialIndex",
                            "targetLabel",
                            "targetR",
                            "proofKind",
                        )
                    }
                    for outcome in source_outcomes
                    if (outcome["targetLabel"], int(outcome["targetR"]))
                    != target_pair
                ]
                examples = sorted(
                    polynomials,
                    key=lambda item: (
                        item["coefficientSha256"] not in untested_hashes,
                        item["coefficientBytes"],
                        item["submissionId"],
                        item["polynomialIndex"],
                    ),
                )[:5]

                candidates.append(
                    {
                        "exactCertifiedTargetFactors": exact_hits,
                        "orbitEvidence": orbit_evidence,
                        "ownedSources": {
                            "allOwnedDistinctPolynomialCount": len(owned_hashes),
                            "examplesUntestedFirst": examples,
                            "testedDistinctPolynomialCount": len(
                                owned_hashes & tested_for_source
                            ),
                            "testedExactOutcomeMisses": tested_misses,
                            "untestedDistinctPolynomialCount": len(untested_hashes),
                        },
                        "priorityTier": tier,
                        "priorityTierName": tier_name,
                        "resolution": {
                            "allLength24ActionsFaithful": all_faithful,
                            "length24OrbitCount": length24_count,
                            "method": (
                                "fixed_single_length24_orbit"
                                if length24_count == 1
                                else "exact_marginal_then_joint_frobenius_matching"
                            ),
                            "structurallyResolvable": (
                                length24_count == 1 or all_faithful
                            ),
                            "targetLabelMultiplicity": sum(
                                str(slot["targetLabel"]) == target_label
                                for slot in target_slots
                            ),
                        },
                        "signatureCoverage": {
                            "allSourceSignatureClassIndexes": sorted(
                                int(item["classIndex"])
                                for item in signature_classes
                            ),
                            "classCountDenominator": len(signature_classes),
                            "classCountNumerator": len(compatible_indexes),
                            "classSizeDenominator": total_class_size,
                            "classSizeFraction": compatible_size / total_class_size,
                            "classSizeNumerator": compatible_size,
                            "compatibleClassIndexes": compatible_indexes,
                            "guaranteedForEveryClass": every_class,
                        },
                        "source": {
                            "label": source_label,
                            "r": source_r,
                            "t": source_t,
                        },
                        "target": {
                            "discSource": target.get("discSource"),
                            "fetchedAt": target.get("fetchedAt"),
                            "kTeams": int(target["kTeams"]),
                            "label": target_label,
                            "pointsBeforeRaid": float(target["points"]),
                            "r": target_r,
                            "scoringDiscAbs": target.get("scoringDiscAbs"),
                            "t": int(target["t"]),
                            "teamId": str(target["teamId"]),
                            "teamName": str(target["teamName"]),
                        },
                    }
                )

    candidates.sort(key=priority_key)
    for rank, candidate in enumerate(candidates, start=1):
        candidate["priorityRank"] = rank

    tier_counts = defaultdict(int)
    route_type_counts = defaultdict(int)
    target_pair_set = set()
    for candidate in candidates:
        tier_counts[candidate["priorityTierName"]] += 1
        route_type_counts[
            "single"
            if candidate["resolution"]["length24OrbitCount"] == 1
            else "multi"
        ] += 1
        target_pair_set.add((candidate["target"]["label"], candidate["target"]["r"]))

    result = {
        "candidates": candidates,
        "failures": sorted(failures, key=lambda row: str(row.get("sourceLabel"))),
        "method": {
            "candidateMeaning": (
                "A candidate is an exact GAP-proven pair-action route from an "
                "owned (source label, real-root signature) to a saved rank-12 "
                "solo-held pair. Class-conditional routes still require testing "
                "an owned source field whose complex-conjugation class realizes "
                "the compatible profile."
            ),
            "multiOrbitResolution": (
                "Faithful multi-orbit packets are resolvable by the existing "
                "exact marginal and joint Frobenius finite-matching certifier "
                "after the pair-sum resolvent is constructed."
            ),
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "provenance": {
            "ledger": str(DB_PATH),
            "orbitMap": {
                "path": str(ORBIT_MAP_PATH),
                "sha256": sha256_file(ORBIT_MAP_PATH),
            },
            "rank12Snapshot": {
                "fetchedAtValues": sorted(
                    {str(row.get("fetchedAt")) for row in rank_rows}
                ),
                "path": str(RANK12_SNAPSHOT_PATH),
                "sha256": sha256_file(RANK12_SNAPSHOT_PATH),
                "teamId": RANK12_TEAM_ID,
            },
            "signatureMap": {
                "path": str(SIGNATURE_MAP_PATH),
                "sha256": sha256_file(SIGNATURE_MAP_PATH),
            },
            "supplementalProfileCache": str(args.profile_cache),
        },
        "summary": {
            "candidateRoutes": len(candidates),
            "distinctRank12TargetPairsReachable": len(target_pair_set),
            "exactFactorReady": sum(
                bool(candidate["exactCertifiedTargetFactors"])
                for candidate in candidates
            ),
            "failedProfiles": len(failures),
            "ownedMappedSourceLabelsTouchingRank12": len(relevant_orbits),
            "profiledSourceLabels": len(certified_profiles),
            "rank12SoloPairsInSnapshot": len(rank_pairs),
            "routeTypeCounts": dict(sorted(route_type_counts.items())),
            "tierCounts": dict(sorted(tier_counts.items())),
        },
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

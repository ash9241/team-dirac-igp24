#!/usr/bin/env python3
"""Offline exact pair-orbit census against verified T00134 crawl 5.

The census joins four independently auditable facts:

* accepted, scoreable source presentations in the local ledger;
* the saved exact GAP unordered-pair action map;
* exact complex-conjugation profiles for every degree-24 pair action; and
* the completed 180-row T00134 sole-holder crawl with ``crawl_id = 5``.

It emits one route per *distinct untested source polynomial*.  Duplicate
accepted provenance rows for the same coefficient hash remain recorded in
``acceptedProvenance`` but are never proposed as redundant arithmetic work.
The script is local-only: it has no network or submission code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

import analyze_rank12_routes as shared


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ORBIT_MAP = DATA / "pair_orbit_map.jsonl"
SNAPSHOT = DATA / "rank10_t00134_authenticated_nested_20260730_unique_placements.jsonl"
CHECKPOINT = DATA / "rank10_t00134_20260730_unique_checkpoint.json"
PROFILE_INPUTS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
    DATA / "agent_pair_sibling_shard2_profiles.jsonl",
    DATA / "agent_rank10_pair_stage2_profiles.jsonl",
)
PROFILE_CACHE = DATA / "fresh_t00134_pair_orbit_profiles.jsonl"
FRONTIER = DATA / "fresh_t00134_pair_orbit_frontier.jsonl"
TESTED_ROUTES = DATA / "fresh_t00134_pair_orbit_tested_routes.jsonl"
SUMMARY = DATA / "fresh_t00134_pair_orbit_summary.json"

TEAM_ID = "teamv2_32d618912a0e471fb418e886de946622"
TEAM_NUMBER = "IGP24-T00134"
CRAWL_ID = 5

TESTED_STATUSES = {
    "certified",
    "certified_multi",
    "certified_staged",
}


def read_jsonl(path: Path) -> list[dict]:
    return shared.read_jsonl(path)


def sha256(path: Path) -> str:
    return shared.sha256_file(path)


def validated_snapshot() -> dict[tuple[str, int], dict]:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    expected = {
        "complete": True,
        "crawlId": CRAWL_ID,
        "scope": "unique",
        "teamId": TEAM_ID,
        "teamNumber": TEAM_NUMBER,
    }
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(
                f"crawl checkpoint {key}={checkpoint.get(key)!r}, expected {value!r}"
            )

    rows = read_jsonl(SNAPSHOT)
    checkpoint_rows = checkpoint.get("rows")
    if (
        len(rows) != 180
        or not isinstance(checkpoint_rows, list)
        or len(checkpoint_rows) != len(rows)
    ):
        raise ValueError("crawl-5 snapshot is not the expected complete 180 rows")
    for row in rows:
        if (
            str(row.get("teamId")) != TEAM_ID
            or str(row.get("teamNumber")) != TEAM_NUMBER
            or int(row.get("kTeams", 0)) != 1
        ):
            raise ValueError("snapshot identity or sole-holder invariant failed")
    pairs = {(str(row["label"]), int(row["r"])): row for row in rows}
    if len(pairs) != len(rows):
        raise ValueError("duplicate pair in crawl-5 snapshot")

    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        crawl = connection.execute(
            """
            SELECT team_id,team_number,scope,placement_rows,unique_rows,
                   jsonl_path,complete
            FROM public_team_placement_crawls
            WHERE crawl_id=?
            """,
            (CRAWL_ID,),
        ).fetchone()
        if crawl is None:
            raise ValueError("crawl 5 is absent from the audit ledger")
        if (
            str(crawl[0]) != TEAM_ID
            or str(crawl[1]) != TEAM_NUMBER
            or str(crawl[2]) != "unique"
            or int(crawl[3]) != len(rows)
            or int(crawl[4]) != len(rows)
            or Path(str(crawl[5])).resolve() != SNAPSHOT.resolve()
            or int(crawl[6]) != 1
        ):
            raise ValueError("crawl-5 ledger row does not match the frozen snapshot")
        persisted = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT label,r FROM public_team_placements WHERE crawl_id=?",
                (CRAWL_ID,),
            )
        }
    if persisted != set(pairs):
        raise ValueError("crawl-5 JSONL/SQLite pair sets disagree")
    return pairs


def accepted_presentations(
    connection: sqlite3.Connection,
) -> dict[tuple[str, int], list[dict]]:
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    rows = connection.execute(
        """
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               v.field_disc_abs,v.disc_source,p.coefficient_hash,p.original_line
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
          AND v.label IS NOT NULL AND v.r IS NOT NULL
        ORDER BY v.label,v.r,length(p.original_line),
                 v.submission_id,v.polynomial_index
        """
    )
    for row in rows:
        grouped[(str(row[0]), int(row[2]))].append(
            {
                "sourceLabel": str(row[0]),
                "sourceT": int(row[1]),
                "sourceR": int(row[2]),
                "submissionId": str(row[3]),
                "polynomialIndex": int(row[4]),
                "sourceFieldDiscAbs": str(row[5]) if row[5] else None,
                "sourceDiscProof": str(row[6]) if row[6] else None,
                "sourceCoefficientSha256": str(row[7]),
                "sourceCoefficientBytes": len(str(row[8]).encode("utf-8")),
            }
        )
    return grouped


def iter_dicts(value) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def iter_file_dicts(path: Path) -> Iterator[dict]:
    try:
        if path.suffix == ".jsonl":
            for row in read_jsonl(path):
                yield from iter_dicts(row)
        else:
            yield from iter_dicts(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return


def tested_source_hashes(
    presentation_hash: dict[tuple[str, int], str],
) -> tuple[set[str], dict[str, list[str]]]:
    """Find actual certified pair arithmetic, excluding mere plans/frontiers."""

    tested: set[str] = set()
    evidence: dict[str, list[str]] = defaultdict(list)
    for path in sorted(DATA.rglob("*")):
        if (
            not path.is_file()
            or path.suffix not in (".json", ".jsonl")
            or not any(
                marker in path.name.lower()
                for marker in ("pair", "resolvent", "live_gold_route")
            )
            or path in (FRONTIER, PROFILE_CACHE)
        ):
            continue
        for row in iter_file_dicts(path):
            if str(row.get("status")) not in TESTED_STATUSES:
                continue
            submission = row.get("sourceSubmissionId")
            index = row.get("sourcePolynomialIndex")
            if submission is None or index is None:
                continue
            # A frontier/task row is not arithmetic evidence.  Every supported
            # exact pair worker result contains one of these result payloads.
            if not any(
                key in row
                for key in (
                    "coefficientLine",
                    "candidates",
                    "orbitCertificate",
                    "attempts",
                )
            ):
                continue
            digest = presentation_hash.get((str(submission), int(index)))
            if digest is None:
                continue
            tested.add(digest)
            relative = str(path.relative_to(ROOT))
            if relative not in evidence[digest]:
                evidence[digest].append(relative)
    return tested, evidence


def identity_profile(orbit: dict) -> dict:
    return {
        "sourceLabel": str(orbit["sourceLabel"]),
        "sourceT": int(orbit["sourceT"]),
        "length24OrbitCount": int(orbit["length24OrbitCount"]),
        "status": "certified",
        "actionMapCrossCheck": "exact_match",
        "profiles": [
            {
                "classIndex": -1,
                "classSize": 1,
                "order": 1,
                "sourceR": 24,
                "orbitSignatures": [
                    {
                        "orbitIndex": int(target["orbitIndex"]),
                        "targetLabel": str(target["targetLabel"]),
                        "targetR": 24,
                    }
                    for target in orbit["targets"]
                ],
            }
        ],
    }


def load_profiles(
    relevant: dict[str, dict], compute_missing: bool, timeout: int
) -> tuple[dict[str, dict], dict[str, str], list[dict]]:
    profiles: dict[str, dict] = {}
    sources: dict[str, str] = {}
    for path in (*PROFILE_INPUTS, PROFILE_CACHE):
        for row in read_jsonl(path):
            label = str(row.get("sourceLabel"))
            if label in relevant and row.get("status") in (None, "certified"):
                profiles[label] = row
                sources[label] = str(path.relative_to(ROOT))

    failures: list[dict] = []
    missing = [
        (label, int(orbit["sourceT"]))
        for label, orbit in sorted(relevant.items())
        if label not in profiles
    ]
    if compute_missing:
        # Deliberately sequential: this repository permits one heavy GAP/Sage
        # worker at a time.
        for source in missing:
            row = shared.compute_profile(source, timeout)
            if row.get("status") == "certified":
                profiles[str(row["sourceLabel"])] = row
                sources[str(row["sourceLabel"])] = "computed_fresh_t00134"
            else:
                failures.append(row)

    checked: dict[str, dict] = {}
    for label, profile in profiles.items():
        try:
            checked[label] = shared.verified_profile(profile, relevant[label])
        except ValueError as exc:
            failures.append(
                {
                    "sourceLabel": label,
                    "status": "action_map_cross_check_error",
                    "error": str(exc),
                }
            )
    shared.write_jsonl_atomic(
        PROFILE_CACHE,
        checked.values(),
        sort_key=lambda row: int(row["sourceT"]),
    )
    return checked, sources, failures


def canonical_presentations(rows: Iterable[dict]) -> list[dict]:
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_hash[str(row["sourceCoefficientSha256"])].append(row)
    result = []
    for digest, provenance in sorted(by_hash.items()):
        canonical = min(
            provenance,
            key=lambda row: (
                int(row["sourceCoefficientBytes"]),
                str(row["submissionId"]),
                int(row["polynomialIndex"]),
            ),
        )
        result.append(
            {
                **canonical,
                "sourceCoefficientSha256": digest,
                "acceptedProvenance": [
                    {
                        "submissionId": str(row["submissionId"]),
                        "polynomialIndex": int(row["polynomialIndex"]),
                    }
                    for row in provenance
                ],
                "acceptedProvenanceCount": len(provenance),
            }
        )
    return result


def build_routes(
    accepted: dict[tuple[str, int], list[dict]],
    relevant: dict[str, dict],
    profiles: dict[str, dict],
    target_pairs: dict[tuple[str, int], dict],
    tested_hashes: set[str],
    tested_evidence: dict[str, list[str]],
) -> tuple[list[dict], list[dict]]:
    routes: list[dict] = []
    tested_routes: list[dict] = []
    for (source_label, source_r), rows in accepted.items():
        orbit = relevant.get(source_label)
        if orbit is None:
            continue
        profile = profiles.get(source_label)
        profile_kind = "cached_conjugacy_profile"
        if source_r == 24:
            profile = identity_profile(orbit)
            profile_kind = "identity_complex_conjugation"
        if profile is None:
            continue
        classes = [
            row
            for row in profile.get("profiles", [])
            if int(row["sourceR"]) == source_r
        ]
        if not classes:
            continue

        class_pair_sets: list[set[tuple[str, int]]] = []
        orbit_evidence: dict[tuple[str, int], dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for class_row in classes:
            class_pairs: set[tuple[str, int]] = set()
            for signature in class_row.get("orbitSignatures", []):
                pair = (str(signature["targetLabel"]), int(signature["targetR"]))
                if pair not in target_pairs:
                    continue
                class_pairs.add(pair)
                orbit_evidence[pair][int(signature["orbitIndex"])].append(
                    int(class_row["classIndex"])
                )
            class_pair_sets.append(class_pairs)
        union_pairs = set().union(*class_pair_sets) if class_pair_sets else set()
        if not union_pairs:
            continue
        guaranteed = (
            set.intersection(*class_pair_sets) if class_pair_sets else set()
        )
        class_weight = sum(int(row["classSize"]) for row in classes)
        expected_hits = (
            sum(
                int(class_row["classSize"]) * len(class_pairs)
                for class_row, class_pairs in zip(classes, class_pair_sets)
            )
            / class_weight
        )
        any_hit_probability = (
            sum(
                int(class_row["classSize"])
                for class_row, class_pairs in zip(classes, class_pair_sets)
                if class_pairs
            )
            / class_weight
        )

        for source in canonical_presentations(rows):
            digest = str(source["sourceCoefficientSha256"])
            route = {
                **source,
                "length24OrbitCount": int(orbit["length24OrbitCount"]),
                "allLength24ActionsFaithful": all(
                    int(target["kernelOrder"]) == 1 for target in orbit["targets"]
                ),
                "targetLabelMultiplicity": dict(orbit.get("targetCounts") or {}),
                "hasRepeatedTargetLabel": any(
                    int(count) > 1
                    for count in (orbit.get("targetCounts") or {}).values()
                ),
                "compatibleClassIndexes": sorted(
                    int(row["classIndex"]) for row in classes
                ),
                "compatibleClassWeight": class_weight,
                "signatureProfileKind": profile_kind,
                "reachableTargetPairs": [
                    {"label": label, "r": r} for label, r in sorted(union_pairs)
                ],
                "guaranteedTargetPairs": [
                    {"label": label, "r": r} for label, r in sorted(guaranteed)
                ],
                "reachableTargetPairCount": len(union_pairs),
                "guaranteedTargetPairCount": len(guaranteed),
                "expectedT00134FactorsByClassWeight": expected_hits,
                "estimatedAnyT00134HitProbability": any_hit_probability,
                "orbitEvidence": {
                    f"{label}/r{r}": {
                        str(orbit_index): sorted(class_indexes)
                        for orbit_index, class_indexes in sorted(by_orbit.items())
                    }
                    for (label, r), by_orbit in sorted(orbit_evidence.items())
                },
                "alreadyPairTested": digest in tested_hashes,
                "testedEvidence": tested_evidence.get(digest, []),
            }
            if digest in tested_hashes:
                tested_routes.append(route)
            else:
                routes.append(route)

    sort_key = lambda row: (
        -int(row["guaranteedTargetPairCount"]),
        -float(row["expectedT00134FactorsByClassWeight"]),
        -float(row["estimatedAnyT00134HitProbability"]),
        int(row["length24OrbitCount"]) == 1,
        int(row["sourceCoefficientBytes"]),
        int(row["sourceT"]),
        int(row["sourceR"]),
        str(row["sourceCoefficientSha256"]),
    )
    routes.sort(key=sort_key)
    tested_routes.sort(key=sort_key)
    for rank, route in enumerate(routes, start=1):
        route["priorityRank"] = rank
    return routes, tested_routes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compute-missing-profiles",
        action="store_true",
        help="compute missing exact profiles sequentially with Sage/GAP",
    )
    parser.add_argument("--profile-timeout", type=int, default=180)
    args = parser.parse_args()
    if args.profile_timeout < 1:
        parser.error("--profile-timeout must be positive")

    target_pairs = validated_snapshot()
    target_labels = {label for label, _r in target_pairs}
    orbit_rows = read_jsonl(ORBIT_MAP)
    orbit_by_label = {str(row["sourceLabel"]): row for row in orbit_rows}
    if len(orbit_by_label) != len(orbit_rows):
        raise ValueError("duplicate source label in exact pair-orbit map")

    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        accepted = accepted_presentations(connection)
        presentation_hash = {
            (str(row["submissionId"]), int(row["polynomialIndex"])): str(
                row["sourceCoefficientSha256"]
            )
            for rows in accepted.values()
            for row in rows
        }
    owned_labels = {label for label, _r in accepted}
    relevant = {
        label: row
        for label, row in orbit_by_label.items()
        if label in owned_labels
        and any(
            str(target["targetLabel"]) in target_labels
            for target in row.get("targets", [])
        )
    }

    profiles, profile_sources, failures = load_profiles(
        relevant, args.compute_missing_profiles, args.profile_timeout
    )
    tested_hashes, tested_evidence = tested_source_hashes(presentation_hash)
    routes, tested_routes = build_routes(
        accepted,
        relevant,
        profiles,
        target_pairs,
        tested_hashes,
        tested_evidence,
    )
    shared.write_jsonl_atomic(
        FRONTIER,
        routes,
        sort_key=lambda row: int(row["priorityRank"]),
    )
    shared.write_jsonl_atomic(
        TESTED_ROUTES,
        tested_routes,
        sort_key=lambda row: (
            int(row["sourceT"]),
            int(row["sourceR"]),
            str(row["sourceCoefficientSha256"]),
        ),
    )

    profile_missing = sorted(set(relevant) - set(profiles))
    summary = {
        "schemaVersion": "fresh-t00134-pair-orbit-census-v1",
        "method": {
            "action": "exact GAP unordered-pair orbit map",
            "signature": "exact order-1/2 conjugacy profiles in every length-24 action",
            "multiOrbitPolicy": "all mapped degree-24 orbits retained; faithfulness recorded",
            "presentationPolicy": "accepted scoreable rows deduplicated only by exact coefficient hash",
            "testedPolicy": "certified arithmetic result required; plans/frontiers excluded",
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "provenance": {
            "crawlId": CRAWL_ID,
            "snapshot": {"path": str(SNAPSHOT), "sha256": sha256(SNAPSHOT)},
            "checkpoint": {"path": str(CHECKPOINT), "sha256": sha256(CHECKPOINT)},
            "ledger": str(DB),
            "orbitMap": {"path": str(ORBIT_MAP), "sha256": sha256(ORBIT_MAP)},
            "profileCache": {
                "path": str(PROFILE_CACHE),
                "sha256": sha256(PROFILE_CACHE),
            },
        },
        "profileSources": profile_sources,
        "profileFailures": failures,
        "profileMissing": profile_missing,
        "summary": {
            "t00134SolePairs": len(target_pairs),
            "t00134SoleLabels": len(target_labels),
            "mappedAcceptedSourceLabelsTouchingT00134": len(relevant),
            "mappedMultiOrbitSourceLabelsTouchingT00134": sum(
                int(row["length24OrbitCount"]) > 1 for row in relevant.values()
            ),
            "mappedRepeatedTargetSourceLabelsTouchingT00134": sum(
                any(int(count) > 1 for count in (row.get("targetCounts") or {}).values())
                for row in relevant.values()
            ),
            "profilesCertified": len(profiles),
            "profilesMissing": len(profile_missing),
            "untestedDistinctSourceRoutes": len(routes),
            "alreadyTestedDistinctSourceRoutes": len(tested_routes),
            "distinctReachableT00134Pairs": len(
                {
                    (str(pair["label"]), int(pair["r"]))
                    for route in routes
                    for pair in route["reachableTargetPairs"]
                }
            ),
            "guaranteedRoutes": sum(
                int(route["guaranteedTargetPairCount"]) > 0 for route in routes
            ),
            "multiOrbitUntestedRoutes": sum(
                int(route["length24OrbitCount"]) > 1 for route in routes
            ),
        },
        "topRoutes": [
            {
                key: route[key]
                for key in (
                    "priorityRank",
                    "sourceLabel",
                    "sourceR",
                    "submissionId",
                    "polynomialIndex",
                    "sourceCoefficientSha256",
                    "length24OrbitCount",
                    "guaranteedTargetPairs",
                    "reachableTargetPairs",
                    "expectedT00134FactorsByClassWeight",
                    "estimatedAnyT00134HitProbability",
                )
            }
            for route in routes[:25]
        ],
        "outputs": {
            "frontier": str(FRONTIER),
            "testedRoutes": str(TESTED_ROUTES),
        },
    }
    shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary["summary"], indent=2, sort_keys=True))
    if profile_missing and not args.compute_missing_profiles:
        print(
            f"{len(profile_missing)} exact profiles remain; rerun with "
            "--compute-missing-profiles"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Exact stage-2 pair-orbit census and bounded T00110 raid pilot.

This lane deliberately includes faithful multi-orbit source actions, including
repeated target labels.  The GAP action map and exact complex-conjugation
profiles provide the structural intersection.  The pilot then constructs the
entire degree-24 sibling packet with a separating Tschirnhaus pair-sum
resolvent; ``frobenius_discriminate.sage.py`` assigns every factor label by
exact marginal and joint source-action cycle profiles.

The script is local-only.  It neither refreshes public state nor submits.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import analyze_rank12_routes as shared


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ORBIT_MAP = DATA / "pair_orbit_map.jsonl"
SIGNATURE_INPUTS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
    DATA / "agent_pair_sibling_shard2_profiles.jsonl",
)
PROFILE_CACHE = DATA / "agent_rank10_pair_stage2_profiles.jsonl"
SNAPSHOT = Path("/private/tmp/rank10_raid/rank10_current_unique_placements.jsonl")
FRONTIER = DATA / "agent_rank10_pair_stage2_frontier.json"
PILOT_TASKS = DATA / "agent_rank10_pair_stage2_pilot_tasks.jsonl"
PILOT_RESULTS = DATA / "agent_rank10_pair_stage2_pilot_results.jsonl"
PILOT_CERTIFICATE = DATA / "agent_rank10_pair_stage2_pilot_frobenius_certificate.json"
PILOT_HITS = DATA / "agent_rank10_pair_stage2_pilot_hits.jsonl"
SUMMARY = DATA / "agent_rank10_pair_stage2_summary.json"
MANIFEST = ROOT / "outbox" / "agent_rank10_pair_stage2_live.txt"
PAIR_WORKER = ROOT / "pair_sum_one.sage.py"
FROBENIUS_WORKER = ROOT / "frobenius_discriminate.sage.py"

TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"
TEAM_NUMBER = "IGP24-T00110"


def read_jsonl(path: Path) -> list[dict]:
    return shared.read_jsonl(path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    shared.write_jsonl_atomic(
        path,
        rows,
        sort_key=lambda row: (
            int(row.get("pilotRank", 0)),
            int(row.get("sourceT", str(row.get("sourceLabel", "24T0"))[3:])),
            int(row.get("sourceR", -1)),
        ),
    )


def sha256(path: Path) -> str:
    return shared.sha256_file(path)


def canonical_hash(line: str) -> str:
    coefficients = [int(value.strip()) for value in line.split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("not a monic degree-24 coefficient line")
    canonical = ",".join(str(value) for value in coefficients)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def validated_snapshot() -> dict[tuple[str, int], dict]:
    rows = read_jsonl(SNAPSHOT)
    if not rows:
        raise ValueError("fresh T00110 snapshot is empty")
    if any(str(row.get("teamId")) != TEAM_ID for row in rows):
        raise ValueError("snapshot contains another team")
    if any(str(row.get("teamNumber")) != TEAM_NUMBER for row in rows):
        raise ValueError("snapshot contains another team number")
    if any(int(row.get("kTeams", 0)) != 1 for row in rows):
        raise ValueError("snapshot contains a non-sole pair")
    pairs = {(str(row["label"]), int(row["r"])): row for row in rows}
    if len(pairs) != len(rows):
        raise ValueError("duplicate pair in T00110 snapshot")
    return pairs


def load_profiles(relevant: dict[str, dict], workers: int, timeout: int):
    profiles: dict[str, dict] = {}
    sources: dict[str, str] = {}
    for path in (*SIGNATURE_INPUTS, PROFILE_CACHE):
        for row in read_jsonl(path):
            label = str(row.get("sourceLabel"))
            if label in relevant and row.get("status") in (None, "certified"):
                profiles[label] = row
                sources[label] = str(path.relative_to(ROOT))

    pending = [
        (label, int(orbit["sourceT"]))
        for label, orbit in relevant.items()
        if label not in profiles
    ]
    failures: list[dict] = []
    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(shared.compute_profile, source, timeout): source
                for source in pending
            }
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                label = str(row["sourceLabel"])
                if row.get("status") == "certified":
                    profiles[label] = row
                    sources[label] = "computed_stage2"
                else:
                    failures.append(row)

    certified: dict[str, dict] = {}
    for label, profile in profiles.items():
        try:
            certified[label] = shared.verified_profile(profile, relevant[label])
        except ValueError as exc:
            failures.append(
                {"sourceLabel": label, "status": "cross_check_error", "error": str(exc)}
            )
    shared.write_jsonl_atomic(
        PROFILE_CACHE,
        certified.values(),
        sort_key=lambda row: int(row["sourceT"]),
    )
    return certified, sources, pending, failures


def tested_source_keys() -> set[tuple[str, int]]:
    """Conservatively skip source presentations already sent through pair workers."""
    keys: set[tuple[str, int]] = set()
    for path in DATA.glob("*pair*.jsonl"):
        if path in (PILOT_TASKS, PILOT_HITS):
            continue
        for row in read_jsonl(path):
            # Frontier/task rows also carry source provenance.  Count only an
            # actual arithmetic result as tested, never a merely queued row.
            if row.get("status") not in (
                "certified",
                "certified_multi",
                "certified_staged",
            ):
                continue
            submission = row.get("sourceSubmissionId", row.get("submissionId"))
            index = row.get("sourcePolynomialIndex", row.get("polynomialIndex"))
            if submission is not None and index is not None:
                keys.add((str(submission), int(index)))
    return keys


def route_census(
    owned: dict[tuple[str, int], list[dict]],
    relevant: dict[str, dict],
    profiles: dict[str, dict],
    target_pairs: dict[tuple[str, int], dict],
) -> list[dict]:
    tested = tested_source_keys()
    routes: list[dict] = []
    for (source_label, source_r), polynomials in owned.items():
        orbit = relevant.get(source_label)
        profile = profiles.get(source_label)
        if orbit is None or profile is None:
            continue
        classes = [
            row
            for row in profile.get("profiles", [])
            if int(row["sourceR"]) == source_r
        ]
        if not classes:
            continue
        class_pair_sets = []
        class_sizes = []
        orbit_evidence: dict[tuple[str, int], dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for class_row in classes:
            pairs = set()
            class_index = int(class_row["classIndex"])
            class_sizes.append(int(class_row["classSize"]))
            for signature in class_row.get("orbitSignatures", []):
                pair = (str(signature["targetLabel"]), int(signature["targetR"]))
                if pair in target_pairs:
                    pairs.add(pair)
                    orbit_evidence[pair][int(signature["orbitIndex"])].append(class_index)
            class_pair_sets.append(pairs)
        union_pairs = set().union(*class_pair_sets) if class_pair_sets else set()
        if not union_pairs:
            continue
        guaranteed_pairs = set.intersection(*class_pair_sets) if class_pair_sets else set()

        untested = [
            row
            for row in polynomials
            if (str(row["submissionId"]), int(row["polynomialIndex"])) not in tested
        ]
        examples = untested or polynomials
        source = examples[0]
        all_faithful = all(int(row["kernelOrder"]) == 1 for row in orbit["targets"])
        if int(orbit["length24OrbitCount"]) > 1 and not all_faithful:
            continue

        weighted_hits = sum(
            int(class_row["classSize"])
            * len(class_pairs)
            for class_row, class_pairs in zip(classes, class_pair_sets)
        )
        total_class_size = sum(class_sizes)
        expected_hits = weighted_hits / total_class_size
        routes.append(
            {
                "sourceLabel": source_label,
                "sourceT": int(orbit["sourceT"]),
                "sourceR": source_r,
                "submissionId": str(source["submissionId"]),
                "polynomialIndex": int(source["polynomialIndex"]),
                "sourceCoefficientSha256": str(source["coefficientSha256"]),
                "sourceCoefficientBytes": int(source["coefficientBytes"]),
                "sourceFieldDiscAbs": source.get("fieldDiscAbs"),
                "sourcePresentationWasUntested": bool(untested),
                "ownedPresentationCount": len(polynomials),
                "length24OrbitCount": int(orbit["length24OrbitCount"]),
                "allLength24ActionsFaithful": all_faithful,
                "targetLabelMultiplicity": dict(orbit.get("targetCounts") or {}),
                "hasRepeatedTargetLabel": any(
                    int(count) > 1 for count in (orbit.get("targetCounts") or {}).values()
                ),
                "compatibleClassIndexes": sorted(int(row["classIndex"]) for row in classes),
                "compatibleClassSize": total_class_size,
                "reachableTargetPairs": [
                    {"label": label, "r": r} for label, r in sorted(union_pairs)
                ],
                "guaranteedTargetPairs": [
                    {"label": label, "r": r} for label, r in sorted(guaranteed_pairs)
                ],
                "reachableTargetPairCount": len(union_pairs),
                "guaranteedTargetPairCount": len(guaranteed_pairs),
                "expectedT00110FactorsByClassSize": expected_hits,
                "orbitEvidence": {
                    f"{label}/r{r}": {
                        str(orbit_index): sorted(class_indexes)
                        for orbit_index, class_indexes in sorted(by_orbit.items())
                    }
                    for (label, r), by_orbit in sorted(orbit_evidence.items())
                },
            }
        )
    routes.sort(
        key=lambda row: (
            int(row["length24OrbitCount"]) == 1,
            -int(row["guaranteedTargetPairCount"]),
            -float(row["expectedT00110FactorsByClassSize"]),
            not bool(row["hasRepeatedTargetLabel"]),
            not bool(row["sourcePresentationWasUntested"]),
            int(row["sourceT"]),
            int(row["sourceR"]),
        )
    )
    for rank, row in enumerate(routes, start=1):
        row["priorityRank"] = rank
    return routes


def choose_pilot(routes: list[dict], limit: int) -> list[dict]:
    # Cover every compatible faithful multi-orbit route first.  If fewer than
    # ``limit`` exist, fill the remaining worker slots with genuinely untested
    # single-orbit controls from the same exact census.
    ordered = [row for row in routes if int(row["length24OrbitCount"]) > 1]
    ordered.extend(
        sorted(
            (row for row in routes if int(row["length24OrbitCount"]) == 1),
            key=lambda row: (
                not bool(row["sourcePresentationWasUntested"]),
                -float(row["expectedT00110FactorsByClassSize"]),
                int(row["sourceT"]),
                int(row["sourceR"]),
            ),
        )
    )
    chosen = []
    seen_sources = set()
    for route in ordered:
        key = (str(route["submissionId"]), int(route["polynomialIndex"]))
        if key in seen_sources:
            continue
        seen_sources.add(key)
        chosen.append({**route, "pilotRank": len(chosen) + 1})
        if len(chosen) == limit:
            break
    return chosen


def run_packet(task: dict, timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(PAIR_WORKER),
        str(task["submissionId"]),
        str(task["polynomialIndex"]),
        "--all-degree-24",
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
        "--nfdisc",
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {**task, "status": "timeout", "workerWallSeconds": timeout}
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return {
            **task,
            "status": "invalid_output",
            "error": str(exc),
            "stderrTail": completed.stderr[-2000:],
            "workerExitCode": completed.returncode,
            "workerWallSeconds": round(time.monotonic() - started, 3),
        }
    return {
        **task,
        **result,
        "pilotRank": int(task["pilotRank"]),
        "workerExitCode": completed.returncode,
        "workerWallSeconds": round(time.monotonic() - started, 3),
    }


def run_frobenius(timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(FROBENIUS_WORKER),
        "--input",
        str(PILOT_RESULTS),
        "--prime-bound",
        "5000",
        "--require-resolved",
        "--output",
        str(PILOT_CERTIFICATE),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if not PILOT_CERTIFICATE.exists():
        raise RuntimeError(
            f"Frobenius certificate missing: exit={completed.returncode}; "
            f"stderr={completed.stderr[-2000:]}"
        )
    certificate = json.loads(PILOT_CERTIFICATE.read_text(encoding="utf-8"))
    certificate["workerExitCode"] = completed.returncode
    certificate["stderrTail"] = completed.stderr[-2000:]
    return certificate


def current_local_state(pair: tuple[str, int]) -> dict:
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        target = connection.execute(
            "SELECT team_count,minimum_disc_abs FROM targets WHERE label=? AND r=?", pair
        ).fetchone()
        owned = connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
            pair,
        ).fetchone()[0]
        baseline = connection.execute(
            "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?", pair
        ).fetchone()[0]
    return {
        "teamCount": int(target[0]) if target else None,
        "minimumDiscAbs": str(target[1]) if target and target[1] else None,
        "ownedRows": int(owned),
        "baselineRows": int(baseline),
    }


def annotate_hits(
    results: list[dict], certificate: dict, target_pairs: dict[tuple[str, int], dict]
) -> list[dict]:
    by_source = {
        (
            str(row["sourceSubmissionId"]),
            int(row["sourcePolynomialIndex"]),
        ): row
        for row in results
    }
    hits = []
    seen_pairs = set()
    for proof in certificate.get("rows", []):
        if proof.get("status") != "resolved":
            continue
        key = (
            str(proof["sourceSubmissionId"]),
            int(proof["sourcePolynomialIndex"]),
        )
        packet = by_source[key]
        factors = {int(row["factorIndex"]): row for row in packet["candidates"]}
        for assignment in proof.get("assignments", []):
            pair = (str(assignment["targetLabel"]), int(assignment["targetR"]))
            factor = factors[int(assignment["factorIndex"])]
            local = current_local_state(pair)
            rank10 = target_pairs.get(pair)
            kind = None
            if local["teamCount"] == 0 and not local["ownedRows"] and not local["baselineRows"]:
                kind = "live_gold"
            elif (
                local["teamCount"] == 1
                and rank10 is not None
                and not local["ownedRows"]
                and not local["baselineRows"]
            ):
                kind = "rank10_solo_raid"
            if kind is None or pair in seen_pairs:
                continue
            line = str(factor["coefficientLine"])
            digest = canonical_hash(line)
            if digest != str(factor["coefficientSha256"]):
                raise ArithmeticError("factor coefficient hash mismatch")
            candidate_disc = int(factor["fieldDiscriminantAbs"])
            if kind == "live_gold":
                score = 1.0
                swing = 1.0
                holder_disc = None
                ratio = 1.0
            else:
                holder_disc = int(
                    rank10.get("minScoringDiscAbs") or rank10.get("scoringDiscAbs")
                )
                ratio = min(1.0, math.log(holder_disc) / math.log(candidate_disc))
                score = 0.5 * ratio
                swing = 0.5 + score
            hits.append(
                {
                    **factor,
                    "targetLabel": pair[0],
                    "targetT": int(pair[0][3:]),
                    "targetR": pair[1],
                    "sourceLabel": str(packet["sourceLabel"]),
                    "sourceR": int(packet["sourceR"]),
                    "sourceSubmissionId": key[0],
                    "sourcePolynomialIndex": key[1],
                    "strategicKind": kind,
                    "localTargetAudit": local,
                    "rank10SnapshotEvidence": rank10,
                    "scoring": {
                        "candidateFieldDiscAbs": str(candidate_disc),
                        "holderFieldDiscAbs": str(holder_disc) if holder_disc else None,
                        "discRatio": ratio,
                        "candidateScore": score,
                        "netRelativeSwing": swing,
                        "formula": (
                            "gold=1"
                            if kind == "live_gold"
                            else "ratio=min(1,log(Dholder)/log(Dcandidate)); score=.5*ratio; swing=.5+score"
                        ),
                    },
                    "exactLabelProof": "joint_unramified_frobenius_cycle_profiles",
                }
            )
            seen_pairs.add(pair)
    return hits


def write_manifest(hits: list[dict]) -> None:
    temporary = MANIFEST.with_suffix(".txt.tmp")
    temporary.write_text(
        "".join(str(row["coefficientLine"]) + "\n" for row in hits), encoding="utf-8"
    )
    temporary.replace(MANIFEST)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--profile-timeout", type=int, default=180)
    parser.add_argument("--packet-timeout", type=int, default=600)
    parser.add_argument("--frobenius-timeout", type=int, default=600)
    parser.add_argument("--pilot", type=int, default=6)
    parser.add_argument("--census-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.pilot < 1:
        parser.error("workers and pilot must be positive")

    started = time.time()
    target_pairs = validated_snapshot()
    rank_labels = {label for label, _r in target_pairs}
    orbit_rows = read_jsonl(ORBIT_MAP)
    orbit_by_label = {str(row["sourceLabel"]): row for row in orbit_rows}
    if len(orbit_by_label) != len(orbit_rows):
        raise ValueError("duplicate source label in pair action map")

    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        owned = shared.source_rows(connection)
    owned_labels = {label for label, _r in owned}
    relevant = {
        label: row
        for label, row in orbit_by_label.items()
        if label in owned_labels
        and any(str(target["targetLabel"]) in rank_labels for target in row["targets"])
    }

    profiles, profile_sources, pending_profiles, failures = load_profiles(
        relevant, args.workers, args.profile_timeout
    )
    routes = route_census(owned, relevant, profiles, target_pairs)
    frontier = {
        "candidates": routes,
        "method": {
            "action": "exact GAP unordered-pair orbit map",
            "signature": "exact order-1/2 conjugacy profiles in every length-24 action",
            "multiOrbitResolution": "separating pair-sum resolvent plus exact marginal/joint Frobenius matching",
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "provenance": {
            "ledger": str(DB),
            "orbitMap": {"path": str(ORBIT_MAP), "sha256": sha256(ORBIT_MAP)},
            "snapshot": {"path": str(SNAPSHOT), "sha256": sha256(SNAPSHOT)},
            "profileCache": {"path": str(PROFILE_CACHE), "sha256": sha256(PROFILE_CACHE)},
        },
        "summary": {
            "t00110SolePairs": len(target_pairs),
            "mappedOwnedSourceLabelsTouchingT00110": len(relevant),
            "multiOrbitSourceLabels": sum(
                int(row["length24OrbitCount"]) > 1 for row in relevant.values()
            ),
            "singleOrbitSourceLabels": sum(
                int(row["length24OrbitCount"]) == 1 for row in relevant.values()
            ),
            "repeatedTargetLabelSourceLabels": sum(
                any(int(count) > 1 for count in (row.get("targetCounts") or {}).values())
                for row in relevant.values()
            ),
            "profilesInitiallyMissing": len(pending_profiles),
            "profilesCertified": len(profiles),
            "profileFailures": len(failures),
            "compatibleOwnedSourceSignatureRoutes": len(routes),
            "distinctReachableT00110Pairs": len(
                {
                    (row["label"], int(row["r"]))
                    for route in routes
                    for row in route["reachableTargetPairs"]
                }
            ),
        },
        "profileSources": profile_sources,
        "profileFailures": failures,
    }
    shared.write_json_atomic(FRONTIER, frontier)
    if args.census_only:
        print(json.dumps(frontier["summary"], indent=2, sort_keys=True))
        return 0 if not failures else 2

    pilot = choose_pilot(routes, args.pilot)
    if len(pilot) != args.pilot:
        raise RuntimeError(f"only {len(pilot)} multi-orbit pilot routes available")
    write_jsonl(PILOT_TASKS, pilot)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_packet, task, args.packet_timeout): task for task in pilot
        }
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            results.append(row)
            write_jsonl(PILOT_RESULTS, results)
            print(
                json.dumps(
                    {
                        "event": "packet",
                        "pilotRank": row.get("pilotRank"),
                        "sourceLabel": row.get("sourceLabel"),
                        "sourceR": row.get("sourceR"),
                        "status": row.get("status"),
                        "factors": len(row.get("candidates") or []),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    results.sort(key=lambda row: int(row["pilotRank"]))
    write_jsonl(PILOT_RESULTS, results)

    certified = [row for row in results if row.get("status") == "certified_multi"]
    if certified:
        # Keep the certifier input pure: malformed/timeout rows are retained in
        # the summary but cannot enter an exact certificate.
        write_jsonl(PILOT_RESULTS, certified)
        certificate = run_frobenius(args.frobenius_timeout)
        write_jsonl(PILOT_RESULTS, results)
        hits = annotate_hits(certified, certificate, target_pairs)
    else:
        certificate = {"summary": {"rows": 0, "resolved": 0, "unresolved": 0, "contradiction": 0}, "rows": []}
        hits = []
    write_jsonl(PILOT_HITS, hits)
    write_manifest(hits)

    summary = {
        **frontier["summary"],
        "frontierPath": str(FRONTIER),
        "frontierSha256": sha256(FRONTIER),
        "pilotRequested": args.pilot,
        "pilotLaunched": len(pilot),
        "pilotCompleted": len(results),
        "pilotCertifiedPackets": len(certified),
        "pilotResolvedPackets": int(certificate.get("summary", {}).get("resolved", 0)),
        "pilotHits": len(hits),
        "pilotRank10Raids": sum(row["strategicKind"] == "rank10_solo_raid" for row in hits),
        "pilotLiveGolds": sum(row["strategicKind"] == "live_gold" for row in hits),
        "projectedCandidateScore": sum(row["scoring"]["candidateScore"] for row in hits),
        "projectedNetRelativeSwing": sum(row["scoring"]["netRelativeSwing"] for row in hits),
        "elapsedSeconds": round(time.time() - started, 3),
        "pilotTasks": str(PILOT_TASKS),
        "pilotResults": str(PILOT_RESULTS),
        "pilotCertificate": str(PILOT_CERTIFICATE),
        "pilotHitsPath": str(PILOT_HITS),
        "manifest": str(MANIFEST),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    shared.write_json_atomic(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failures and len(certified) == len(pilot) else 2


if __name__ == "__main__":
    raise SystemExit(main())

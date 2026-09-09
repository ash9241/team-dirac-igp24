#!/usr/bin/env python3
"""Verify and exploit only the frozen historical page-20 delta.

This lane has no submission API and writes only page-20-prefixed artifacts.
It extends the exact unordered-pair action/profile census and stages a
coefficient only after exact arithmetic certification plus a final live-state
audit against gold or monotone rank-10/rank-11 sole-holder evidence.
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
from collections import Counter
from pathlib import Path

import agent_page19_delta_pipeline as shared


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PRE = DATA / "agent_page20_pre_ingest_checkpoint.json"
PAGE_ITEMS = DATA / "agent_page20_history_page_items.jsonl"
INGEST_SUMMARY = DATA / "agent_page20_history_ingest_summary.json"
PAGE19_COMBINED = DATA / "agent_page19_pair_orbit_combined.jsonl"
COMBINED_ORBITS = DATA / "agent_page20_pair_orbit_combined.jsonl"
DELTA_ORBITS = DATA / "agent_page20_pair_orbit_delta.jsonl"
DELTA_PROFILES = DATA / "agent_page20_pair_signature_delta.jsonl"
FROZEN_SUBMISSIONS = DATA / "agent_page20_history_delta_submissions.jsonl"
FROZEN_POLYNOMIALS = DATA / "agent_page20_history_delta_scoreable.jsonl"
ROUTES = DATA / "agent_page20_pair_priority_routes.jsonl"
RESULTS = DATA / "agent_page20_pair_pilot_results.jsonl"
HITS = DATA / "agent_page20_pair_pilot_hits.jsonl"
MANIFEST = ROOT / "outbox" / "agent_page20_pair_pilot_live_hits.txt"
SUMMARY = DATA / "agent_page20_delta_pipeline_summary.json"
PAIR_WORKER = ROOT / "agent_page20_pair_sum_one.sage.py"

RANK10 = Path("/private/tmp/rank10_raid/rank10_current_unique_placements.jsonl")
RANK11 = DATA / "low_hanging_fruit_unique_placements.jsonl"
HOLDER_SPECS = (
    (
        "rank10_solo_raid",
        RANK10,
        "teamv2_07f0f7f581c34fd1a0dd913dad90dcec",
        "IGP24-T00110",
    ),
    (
        "rank11_solo_raid",
        RANK11,
        "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0",
        "IGP24-T00013",
    ),
)


def canonical_hash(coefficients: str) -> str:
    values = [int(value) for value in coefficients.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("ledger coefficient is not monic degree 24")
    canonical = ",".join(str(value) for value in values)
    if canonical != coefficients:
        raise ValueError("ledger coefficients are not canonical")
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def freeze_and_verify_delta() -> tuple[list[dict], list[dict], dict]:
    pre = json.loads(PRE.read_text(encoding="utf-8"))
    items = shared.read_jsonl(PAGE_ITEMS)
    if len(items) != 100:
        raise ValueError(f"frozen page 20 has {len(items)} items, expected 100")
    ids = [str(row["submissionId"]) for row in items]
    if len(set(ids)) != 100:
        raise ValueError("frozen page 20 has duplicate submission IDs")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in ids)
        submissions = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT submission_id,created_at,updated_at,description,
                       queued_count,verified_count,failed_count,synced_at
                FROM submissions WHERE submission_id IN ({placeholders})
                ORDER BY created_at DESC,submission_id
                """,
                ids,
            )
        ]
        if {str(row["submission_id"]) for row in submissions} != set(ids):
            raise ValueError("not all frozen page-20 IDs are present in the ledger")
        if any(
            str(row["created_at"]) >= str(pre["oldestCreatedAtExclusive"])
            for row in submissions
        ):
            raise ValueError("page-20 submission overlaps the page-19 boundary")
        if any(
            float(row["synced_at"]) <= float(pre["maxSyncedAtExclusive"])
            for row in submissions
        ):
            raise ValueError("page-20 submission predates the frozen sync boundary")

        polynomial_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM polynomials WHERE submission_id IN ({placeholders})",
                ids,
            ).fetchone()[0]
        )
        verification_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM verifications WHERE submission_id IN ({placeholders})",
                ids,
            ).fetchone()[0]
        )
        failure_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM failures WHERE submission_id IN ({placeholders})",
                ids,
            ).fetchone()[0]
        )
        all_coefficients = connection.execute(
            f"""
            SELECT coefficients,coefficient_hash FROM polynomials
            WHERE submission_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        coefficient_hash_mismatches = sum(
            canonical_hash(str(row[0])) != str(row[1]) for row in all_coefficients
        )
        if coefficient_hash_mismatches:
            raise ValueError("page-20 ledger has coefficient hash mismatches")

        polynomials = []
        query = f"""
            SELECT v.submission_id,v.polynomial_index,v.label,v.t,v.r,
                   v.field_disc_abs,p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.submission_id IN ({placeholders})
              AND v.status='accepted' AND v.scoreable=1
            ORDER BY v.submission_id,v.polynomial_index
        """
        for row in connection.execute(query, ids):
            coefficients = str(row[7])
            polynomials.append(
                {
                    "coefficientBytes": len(coefficients.encode("ascii")),
                    "coefficientSha256": str(row[6]),
                    "fieldDiscAbs": str(row[5]) if row[5] else None,
                    "label": str(row[2]),
                    "polynomialIndex": int(row[1]),
                    "r": int(row[4]),
                    "submissionId": str(row[0]),
                    "t": int(row[3]),
                }
            )
        direct_priority_rows = 0
        for row in polynomials:
            state = shared.current_pair_state(
                connection, (str(row["label"]), int(row["r"]))
            )
            if (
                not state["baseline"]
                and not state["locallyOwnedRows"]
                and state["teamCount"] in (0, 1)
            ):
                direct_priority_rows += 1
    finally:
        connection.close()

    list_counts = {str(row["submissionId"]): int(row["verifiedCountFromList"]) for row in items}
    list_mismatches = [
        str(row["submission_id"])
        for row in submissions
        if int(row["verified_count"]) != list_counts[str(row["submission_id"])]
    ]
    if list_mismatches:
        raise ValueError(f"list/detail verified-count mismatch: {list_mismatches}")
    if polynomial_count != verification_count + failure_count:
        raise ValueError(
            "downloaded polynomial count is not verification plus failure count"
        )

    shared.write_jsonl(FROZEN_SUBMISSIONS, submissions)
    shared.write_jsonl(FROZEN_POLYNOMIALS, polynomials)
    audit = {
        "coefficientHashMismatches": coefficient_hash_mismatches,
        "directSubmitReadyImportedRows": direct_priority_rows,
        "failedRows": failure_count,
        "frozenIdsEqualLedgerIds": True,
        "listDetailVerifiedCountMismatches": len(list_mismatches),
        "polynomialRows": polynomial_count,
        "scoreableAcceptedRows": len(polynomials),
        "submissionRows": len(submissions),
        "verifiedRows": verification_count,
    }
    return submissions, polynomials, audit


def holder_evidence() -> dict[tuple[str, int], dict]:
    evidence = {}
    for kind, path, team_id, team_number in HOLDER_SPECS:
        for row in shared.read_jsonl(path):
            if (
                str(row.get("teamId")) != team_id
                or str(row.get("teamNumber")) != team_number
                or int(row.get("kTeams", 0)) != 1
            ):
                continue
            evidence[(str(row["label"]), int(row["r"]))] = {
                **row,
                "holderKind": kind,
                "holderTeamId": team_id,
                "holderTeamNumber": team_number,
            }
    return evidence


def priority_snapshot() -> tuple[set[tuple[str, int]], dict[tuple[str, int], dict], dict]:
    evidence = holder_evidence()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
        owned = set(
            connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        )
        targets = {
            (str(label), int(r)): (int(team_count), generated_at)
            for label, r, team_count, generated_at in connection.execute(
                "SELECT label,r,team_count,generated_at FROM targets"
            )
        }
    finally:
        connection.close()
    gold = {
        pair
        for pair, (team_count, _generated) in targets.items()
        if team_count == 0 and pair not in baseline and pair not in owned
    }
    sole = {
        pair: row
        for pair, row in evidence.items()
        if targets.get(pair, (None, None))[0] == 1
        and pair not in baseline
        and pair not in owned
    }
    counts = Counter(row["holderKind"] for row in sole.values())
    metadata = {
        "goldPairs": len(gold),
        "rank10SolePairs": counts.get("rank10_solo_raid", 0),
        "rank11SolePairs": counts.get("rank11_solo_raid", 0),
        "targetGeneratedAtMin": min(value[1] for value in targets.values()),
        "targetGeneratedAtMax": max(value[1] for value in targets.values()),
        "targetRows": len(targets),
    }
    return gold, sole, metadata


def exact_profiles(
    polynomials: list[dict], orbits: dict[str, dict], workers: int, timeout: int
):
    existing = {}
    for path in (
        DATA / "pair_signature_map.jsonl",
        DATA / "rank12_route_signature_profiles.jsonl",
        DATA / "agent_pair_sibling_shard2_profiles.jsonl",
        DATA / "agent_page19_pair_signature_delta.jsonl",
    ):
        for row in shared.read_jsonl(path):
            if row.get("status") not in (None, "certified"):
                continue
            existing[str(row["sourceLabel"])] = row
    eligible = {}
    for row in polynomials:
        orbit = orbits.get(str(row["label"]))
        if orbit is None or len(orbit.get("targets") or []) != 1:
            continue
        if str(orbit["targets"][0]["targetLabel"]) == str(row["label"]):
            continue
        eligible[str(row["label"])] = int(row["t"])
    missing = sorted(
        [(label, t) for label, t in eligible.items() if label not in existing],
        key=lambda item: item[1],
    )
    delta = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                shared.run_json,
                ["sage", "-python", str(shared.PROFILE_WORKER), label, str(t)],
                timeout,
            ): (label, t)
            for label, t in missing
        }
        for future in concurrent.futures.as_completed(futures):
            row, failure = future.result()
            label, t = futures[future]
            if row is not None and int(row.get("returnCode", 1)) == 0:
                row.pop("stderrTail", None)
                row.pop("returnCode", None)
                row.pop("wallSeconds", None)
                row["status"] = "certified"
                existing[label] = row
                delta.append(row)
            else:
                failures.append(
                    failure
                    or {
                        "sourceLabel": label,
                        "sourceT": t,
                        "status": "worker_error",
                        "payload": row,
                    }
                )
    shared.write_jsonl(DELTA_PROFILES, sorted(delta, key=lambda row: int(row["sourceT"])))
    return existing, missing, delta, failures


def build_routes(
    polynomials: list[dict],
    orbits: dict[str, dict],
    profiles: dict[str, dict],
    gold: set[tuple[str, int]],
    sole: dict[tuple[str, int], dict],
    limit: int,
):
    candidates = []
    skip = Counter()
    for row in polynomials:
        orbit = orbits.get(str(row["label"]))
        if orbit is None or len(orbit.get("targets") or []) != 1:
            skip["not_unique_length24_orbit"] += 1
            continue
        if str(orbit["targets"][0]["targetLabel"]) == str(row["label"]):
            skip["self_action"] += 1
            continue
        profile = profiles.get(str(row["label"]))
        if profile is None:
            skip["missing_exact_signature_profile"] += 1
            continue
        pair = shared.forced_pair(row, profile)
        if pair is None:
            skip["target_pair_not_forced_by_source_signature"] += 1
            continue
        if pair in gold:
            kind = "live_gold"
            tier = 0
        elif pair in sole:
            kind = str(sole[pair]["holderKind"])
            tier = 1 if kind == "rank10_solo_raid" else 2
        else:
            skip["not_current_gold_or_monotone_sole"] += 1
            continue
        candidates.append(
            {
                **row,
                "exactCompatibleClassCount": sum(
                    int(item["sourceR"]) == int(row["r"])
                    for item in profile.get("profiles", [])
                ),
                "forcedExactPair": True,
                "priorityKind": kind,
                "priorityTier": tier,
                "targetLabel": pair[0],
                "targetR": pair[1],
            }
        )
    candidates.sort(
        key=lambda row: (
            int(row["priorityTier"]),
            int(row["coefficientBytes"]),
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
            row["coefficientSha256"],
        )
    )
    selected = []
    deferred = []
    seen = set()
    for row in candidates:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        if pair in seen:
            deferred.append(row)
        else:
            seen.add(pair)
            selected.append(row)
    selected.extend(deferred)
    selected = selected[:limit]
    for rank, row in enumerate(selected, start=1):
        row["pilotRank"] = rank
    shared.write_jsonl(ROUTES, selected)
    return selected, dict(sorted(skip.items())), len(candidates)


def multi_orbit_audit(
    polynomials: list[dict],
    orbits: dict[str, dict],
    gold: set[tuple[str, int]],
    sole: dict[tuple[str, int], dict],
) -> dict:
    source_labels = sorted(
        {
            str(row["label"])
            for row in polynomials
            if len((orbits.get(str(row["label"])) or {}).get("targets") or []) > 1
        },
        key=lambda label: int(label[3:]),
    )
    target_labels = {
        str(target["targetLabel"])
        for label in source_labels
        for target in orbits[label]["targets"]
    }
    priority = []
    for pair in sorted(
        gold | set(sole), key=lambda item: (int(item[0][3:]), int(item[1]))
    ):
        if pair[0] not in target_labels:
            continue
        priority.append(
            {
                "label": pair[0],
                "r": pair[1],
                "priorityKind": "live_gold" if pair in gold else sole[pair]["holderKind"],
            }
        )
    return {
        "excludedSafelyWithoutExactFactorAssignment": not priority,
        "priorityPairsByTargetLabel": priority,
        "sourceLabelCount": len(source_labels),
        "sourceLabels": source_labels,
        "targetLabelCount": len(target_labels),
    }


def run_route(route: dict, timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(PAIR_WORKER),
        str(route["submissionId"]),
        str(route["polynomialIndex"]),
        "--expected-target",
        str(route["targetLabel"]),
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
        "--nfdisc",
    ]
    row, failure = shared.run_json(command, timeout)
    if failure is not None:
        return {
            **route,
            **failure,
            "forcedTargetLabel": route["targetLabel"],
            "forcedTargetR": int(route["targetR"]),
            "workerCommand": command,
        }
    return {
        **route,
        **row,
        "forcedTargetLabel": route["targetLabel"],
        "forcedTargetR": int(route["targetR"]),
        "workerCommand": command,
    }


def final_audit(result: dict, evidence: dict[tuple[str, int], dict]) -> dict:
    if result.get("status") != "certified" or int(result.get("returnCode", 1)) != 0:
        return {"stageable": False, "reason": "worker_not_certified"}
    line = str(result["coefficientLine"])
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result["coefficientSha256"]):
        return {"stageable": False, "reason": "coefficient_hash_mismatch"}
    pair = (str(result["targetLabel"]), int(result["targetR"]))
    if pair != (str(result["forcedTargetLabel"]), int(result["forcedTargetR"])):
        return {"stageable": False, "reason": "forced_pair_mismatch"}
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        state = shared.current_pair_state(connection, pair)
        hash_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        )
    finally:
        connection.close()
    base = {
        **state,
        "candidateCoefficientSha256": digest,
        "ledgerCoefficientRows": hash_rows,
        "stageable": False,
    }
    if hash_rows or state["baseline"] or state["locallyOwnedRows"]:
        return {**base, "reason": "duplicate_baseline_or_owned"}
    if state["teamCount"] == 0:
        return {
            **base,
            "priorityKind": "live_gold",
            "projectedCandidateScore": 1.0,
            "projectedNetRelativeSwing": 1.0,
            "stageable": True,
        }
    holder = evidence.get(pair)
    if state["teamCount"] == 1 and holder and result.get("fieldDiscriminantAbs"):
        holder_disc = int(holder.get("minScoringDiscAbs") or holder["scoringDiscAbs"])
        candidate_disc = int(result["fieldDiscriminantAbs"])
        ratio = min(1.0, math.log(holder_disc) / math.log(candidate_disc))
        return {
            **base,
            "candidateFieldDiscAbs": str(candidate_disc),
            "discRatio": ratio,
            "holderScoringDiscAbs": str(holder_disc),
            "holderTeamId": holder["holderTeamId"],
            "holderTeamNumber": holder["holderTeamNumber"],
            "priorityKind": holder["holderKind"],
            "projectedCandidateScore": 0.5 * ratio,
            "projectedNetRelativeSwing": 0.5 + 0.5 * ratio,
            "stageable": True,
        }
    return {**base, "reason": "no_longer_exact_priority_pair"}


def run_pilot(routes: list[dict], workers: int, timeout: int):
    evidence = holder_evidence()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_route, route, timeout): route for route in routes}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            row["postCertificationAudit"] = final_audit(row, evidence)
            results.append(row)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "event": "page20_pair_pilot",
                        "stageable": row["postCertificationAudit"].get("stageable", False),
                        "status": row.get("status"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    results.sort(key=lambda row: int(row["pilotRank"]))
    hits = []
    manifest = []
    seen_hashes = set()
    seen_pairs = set()
    for row in results:
        if not row["postCertificationAudit"].get("stageable"):
            continue
        digest = str(row["coefficientSha256"])
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        if digest in seen_hashes or pair in seen_pairs:
            continue
        seen_hashes.add(digest)
        seen_pairs.add(pair)
        hits.append(row)
        manifest.append(str(row["coefficientLine"]))
    shared.write_jsonl(RESULTS, results)
    shared.write_jsonl(HITS, hits)
    shared.write_lines(MANIFEST, manifest)
    return results, hits, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--map-timeout", type=int, default=90)
    parser.add_argument("--profile-timeout", type=int, default=180)
    parser.add_argument("--pilot-timeout", type=int, default=360)
    parser.add_argument("--max-pilots", type=int, default=24)
    args = parser.parse_args()
    if args.workers != 6:
        raise ValueError("page-20 exact lane must use exactly six workers")
    if not 1 <= args.max_pilots <= 48:
        raise ValueError("max pilots must be in 1..48")

    started = time.monotonic()
    submissions, polynomials, delta_audit = freeze_and_verify_delta()
    shared.COMBINED_ORBITS = COMBINED_ORBITS
    shared.DELTA_ORBITS = DELTA_ORBITS
    shared.BACKFILL_ORBITS = PAGE19_COMBINED
    orbits, map_requested, map_delta, map_failures = shared.exact_orbit_maps(
        polynomials, args.workers, args.map_timeout
    )
    profiles, profile_requested, profile_delta, profile_failures = exact_profiles(
        polynomials, orbits, args.workers, args.profile_timeout
    )
    gold, sole, priority_metadata = priority_snapshot()
    routes, route_skips, eligible_routes = build_routes(
        polynomials, orbits, profiles, gold, sole, args.max_pilots
    )
    multi = multi_orbit_audit(polynomials, orbits, gold, sole)
    if routes:
        results, hits, manifest = run_pilot(routes, args.workers, args.pilot_timeout)
    else:
        results, hits, manifest = [], [], []
        shared.write_jsonl(RESULTS, [])
        shared.write_jsonl(HITS, [])
        shared.write_lines(MANIFEST, [])

    paths = {
        "combinedOrbitMap": COMBINED_ORBITS,
        "deltaOrbitMap": DELTA_ORBITS,
        "deltaProfiles": DELTA_PROFILES,
        "frozenPolynomials": FROZEN_POLYNOMIALS,
        "frozenSubmissions": FROZEN_SUBMISSIONS,
        "hits": HITS,
        "historyIngestSummary": INGEST_SUMMARY,
        "manifest": MANIFEST,
        "preIngestCheckpoint": PRE,
        "results": RESULTS,
        "routes": ROUTES,
    }
    hit_kinds = Counter(
        row["postCertificationAudit"].get("priorityKind") for row in hits
    )
    summary = {
        "boundedHistoryPage": 20,
        "deltaIntegrityAudit": delta_audit,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "eligibleExactPriorityRoutes": eligible_routes,
        "frozenScoreablePolynomials": len(polynomials),
        "frozenSubmissions": len(submissions),
        "hitKinds": dict(sorted((str(k), v) for k, v in hit_kinds.items())),
        "mapFailures": map_failures,
        "multiOrbitPriorityAudit": multi,
        "newOrbitMapsCertified": len(map_delta),
        "newOrbitMapsRequested": len(map_requested),
        "newProfilesCertified": len(profile_delta),
        "newProfilesRequested": len(profile_requested),
        "networkCalls": 0,
        "paths": {
            key: {"path": str(path.resolve()), "sha256": shared.sha256_path(path)}
            for key, path in paths.items()
        },
        "pilotCertified": sum(row.get("status") == "certified" for row in results),
        "pilotHits": len(hits),
        "pilotRoutes": len(routes),
        "pilotWorkers": args.workers,
        "prioritySnapshot": priority_metadata,
        "profileFailures": profile_failures,
        "routeSkipCounts": route_skips,
        "stagedManifestRows": len(manifest),
        "submissionCalls": 0,
    }
    shared.write_json(SUMMARY, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0 if not map_failures and not profile_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

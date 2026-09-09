#!/usr/bin/env python3
"""Freeze and exploit only the newly imported historical page-19 delta.

For scoreable rows belonging to submissions imported after the frozen page-18
cutoff, this program computes any missing exact unordered-pair action maps,
computes exact complex-conjugation profiles, selects only forced current gold
or T00110 sole-holder routes, and runs a bounded six-worker resolvent pilot.
Only post-certified current hits are written to the agent-local outbox.

It also records new even rows whose verified action label is an exact full
2^11 direct-character action for one of the active Gold-C quotient types.
Those rows are flags for the independent exact alignment census, not claimed
alignment certificates.  There are no network or submission operations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
GLOBAL_ORBITS = DATA / "pair_orbit_map.jsonl"
BACKFILL_ORBITS = DATA / "agent_gold_b_combined_pair_orbit_map.jsonl"
ORBIT_WORKER = ROOT / "pair_orbit_one.sage.py"
PROFILE_WORKER = ROOT / "pair_signature_one.sage.py"
PAIR_WORKER = ROOT / "agent_page19_pair_sum_one.sage.py"
COMBINED_ORBITS = DATA / "agent_page19_pair_orbit_combined.jsonl"
DELTA_ORBITS = DATA / "agent_page19_pair_orbit_delta.jsonl"
DELTA_PROFILES = DATA / "agent_page19_pair_signature_delta.jsonl"
FROZEN_SUBMISSIONS = DATA / "agent_page19_history_delta_submissions.jsonl"
FROZEN_POLYNOMIALS = DATA / "agent_page19_history_delta_scoreable.jsonl"
DIRECT_FLAGS = DATA / "agent_page19_direct_character_flags.jsonl"
ROUTES = DATA / "agent_page19_pair_priority_routes.jsonl"
RESULTS = DATA / "agent_page19_pair_pilot_results.jsonl"
HITS = DATA / "agent_page19_pair_pilot_hits.jsonl"
MANIFEST = ROOT / "outbox" / "agent_page19_pair_pilot_live_hits.txt"
SUMMARY = DATA / "agent_page19_delta_pipeline_summary.json"
RANK10 = Path("/private/tmp/rank10_raid/low_hanging_fruit_unique_placements.jsonl")
RANK10_TEAM_ID = "teamv2_07f0f7f581c34fd1a0dd913dad90dcec"
RANK10_TEAM_NUMBER = "IGP24-T00110"

# Frozen immediately before page 19 was requested.  The created-at guard
# excludes any new live submissions synced concurrently by another lane.
PAGE18_MAX_SYNCED_AT = 1784633969.6922
PAGE18_MIN_CREATED_AT = "2026-07-07T17:40:20Z"

DIRECT_LABELS_BY_Q = {
    24: {"24T16709", "24T16710", "24T16711", "24T16712"},
    28: {"24T16720", "24T16721", "24T16722", "24T16723", "24T16724", "24T16725", "24T16726", "24T16727"},
    36: {"24T18007", "24T18008", "24T18009", "24T18010"},
    38: {"24T18016", "24T18017", "24T18018", "24T18019"},
    41: {"24T18026", "24T18027", "24T18028"},
    60: {"24T18493", "24T18494"},
    80: {"24T19334", "24T19335", "24T19336", "24T19337"},
    88: {"24T19668", "24T19669", "24T19670"},
    95: {"24T19685", "24T19686", "24T19687", "24T19688"},
    97: {"24T19692", "24T19693", "24T19694"},
    100: {"24T19699", "24T19700", "24T19701"},
    101: {"24T19702", "24T19703", "24T19704", "24T19705"},
    106: {"24T19715", "24T19716", "24T19717"},
    108: {"24T19720", "24T19721", "24T19722", "24T19723"},
    109: {"24T19724", "24T19725", "24T19726", "24T19727"},
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(path)


def write_json(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(f"{line}\n" for line in lines), encoding="ascii")
    temporary.replace(path)


def even_quotient_hash(coefficients: str) -> str | None:
    try:
        values = [int(value) for value in coefficients.split(",")]
    except ValueError:
        return None
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] <= 0
        or any(values[index] for index in range(1, 25, 2))
    ):
        return None
    line = ",".join(str(value) for value in values[::2])
    return hashlib.sha256(line.encode()).hexdigest()


def freeze_delta(db: Path) -> tuple[list[dict], list[dict]]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        submissions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT submission_id,created_at,updated_at,description,
                       queued_count,verified_count,failed_count,synced_at
                FROM submissions
                WHERE synced_at>? AND created_at<?
                ORDER BY created_at DESC,submission_id
                """,
                (PAGE18_MAX_SYNCED_AT, PAGE18_MIN_CREATED_AT),
            )
        ]
        ids = [str(row["submission_id"]) for row in submissions]
        polynomials = []
        for offset in range(0, len(ids), 400):
            chunk = ids[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            query = f"""
                SELECT v.submission_id,v.polynomial_index,v.label,v.t,v.r,
                       v.field_disc_abs,p.coefficient_hash,p.coefficients
                FROM verifications AS v
                JOIN polynomials AS p USING(submission_id,polynomial_index)
                WHERE v.submission_id IN ({placeholders})
                  AND v.status='accepted' AND v.scoreable=1
                ORDER BY v.submission_id,v.polynomial_index
            """
            for row in connection.execute(query, chunk):
                coefficients = str(row[7])
                polynomials.append(
                    {
                        "coefficientBytes": len(coefficients.encode()),
                        "coefficientSha256": str(row[6]),
                        "fieldDiscAbs": str(row[5]) if row[5] else None,
                        "label": str(row[2]),
                        "polynomialIndex": int(row[1]),
                        "quotientPolynomialSha256": even_quotient_hash(coefficients),
                        "r": int(row[4]),
                        "submissionId": str(row[0]),
                        "t": int(row[3]),
                    }
                )
    finally:
        connection.close()
    write_jsonl(FROZEN_SUBMISSIONS, submissions)
    write_jsonl(FROZEN_POLYNOMIALS, polynomials)
    return submissions, polynomials


def run_json(command: list[str], timeout: int) -> tuple[dict | None, dict | None]:
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
        return None, {
            "command": command,
            "status": "timeout",
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return None, {
            "command": command,
            "returnCode": completed.returncode,
            "status": "no_output",
            "stderrTail": completed.stderr[-1200:],
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    try:
        row = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return None, {
            "command": command,
            "error": str(exc),
            "returnCode": completed.returncode,
            "status": "invalid_output",
            "stderrTail": completed.stderr[-1200:],
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    row["returnCode"] = completed.returncode
    row["stderrTail"] = completed.stderr[-1200:]
    row["wallSeconds"] = round(time.monotonic() - started, 3)
    return row, None


def exact_orbit_maps(polynomials: list[dict], workers: int, timeout: int):
    by_label = {(row["label"], int(row["t"])) for row in polynomials}
    union = {}
    for path in (GLOBAL_ORBITS, BACKFILL_ORBITS):
        for row in read_jsonl(path):
            union[str(row["sourceLabel"])] = row
    missing = sorted(
        [(label, t) for label, t in by_label if label not in union],
        key=lambda item: item[1],
    )
    delta = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_json,
                ["sage", "-python", str(ORBIT_WORKER), label, str(t)],
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
                union[label] = row
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
    ordered = sorted(union.values(), key=lambda row: int(row["sourceT"]))
    write_jsonl(COMBINED_ORBITS, ordered)
    write_jsonl(DELTA_ORBITS, sorted(delta, key=lambda row: int(row["sourceT"])))
    return union, missing, delta, failures


def exact_profiles(polynomials: list[dict], orbits: dict[str, dict], workers: int, timeout: int):
    existing = {}
    for path in (
        DATA / "pair_signature_map.jsonl",
        DATA / "rank12_route_signature_profiles.jsonl",
        DATA / "agent_pair_sibling_shard2_profiles.jsonl",
    ):
        for row in read_jsonl(path):
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
                run_json,
                ["sage", "-python", str(PROFILE_WORKER), label, str(t)],
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
    write_jsonl(DELTA_PROFILES, sorted(delta, key=lambda row: int(row["sourceT"])))
    return existing, missing, delta, failures


def rank10_evidence() -> dict[tuple[str, int], dict]:
    result = {}
    for row in read_jsonl(RANK10):
        if str(row.get("teamId")) == RANK10_TEAM_ID and int(row.get("kTeams", 0)) == 1:
            result[(str(row["label"]), int(row["r"]))] = row
    return result


def current_pair_state(connection: sqlite3.Connection, pair: tuple[str, int]) -> dict:
    target = connection.execute(
        "SELECT team_count,minimum_disc_abs,generated_at FROM targets WHERE label=? AND r=?",
        pair,
    ).fetchone()
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
    ).fetchone()
    owned = connection.execute(
        "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
        pair,
    ).fetchone()
    return {
        "baseline": baseline is not None,
        "generatedAt": str(target[2]) if target and target[2] else None,
        "locallyOwnedRows": int(owned[0]),
        "minimumDiscAbs": str(target[1]) if target and target[1] else None,
        "teamCount": int(target[0]) if target else None,
    }


def forced_pair(row: dict, profile: dict) -> tuple[str, int] | None:
    compatible = [
        item for item in profile.get("profiles", []) if int(item["sourceR"]) == int(row["r"])
    ]
    if not compatible:
        return None
    pairs = set()
    for item in compatible:
        signatures = list(item.get("orbitSignatures") or [])
        if len(signatures) != 1:
            return None
        signature = signatures[0]
        pairs.add((str(signature["targetLabel"]), int(signature["targetR"])))
    return next(iter(pairs)) if len(pairs) == 1 else None


def build_routes(polynomials: list[dict], orbits: dict[str, dict], profiles: dict[str, dict], limit: int):
    evidence = rank10_evidence()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
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
            pair = forced_pair(row, profile)
            if pair is None:
                skip["target_pair_not_forced_by_source_signature"] += 1
                continue
            state = current_pair_state(connection, pair)
            if state["baseline"] or state["locallyOwnedRows"]:
                skip["baseline_or_locally_owned_target"] += 1
                continue
            if state["teamCount"] == 0:
                kind = "live_gold"
                tier = 0
            elif state["teamCount"] == 1 and pair in evidence:
                kind = "rank10_solo_raid"
                tier = 1
            else:
                skip["not_current_gold_or_t00110_solo"] += 1
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
                    "targetSnapshot": state,
                }
            )
    finally:
        connection.close()
    candidates.sort(
        key=lambda row: (
            int(row["priorityTier"]),
            int(row["coefficientBytes"]),
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
            row["coefficientSha256"],
        )
    )
    # First maximize distinct exact target pairs; use alternates only if the
    # bounded pilot still has room.
    selected = []
    deferred = []
    seen_pairs = set()
    for row in candidates:
        pair = (row["targetLabel"], int(row["targetR"]))
        if pair in seen_pairs:
            deferred.append(row)
        else:
            seen_pairs.add(pair)
            selected.append(row)
    selected.extend(deferred)
    selected = selected[:limit]
    for index, row in enumerate(selected, start=1):
        row["pilotRank"] = index
    write_jsonl(ROUTES, selected)
    return selected, dict(sorted(skip.items())), len(candidates)


def multi_orbit_priority_audit(polynomials: list[dict], orbits: dict[str, dict]) -> dict:
    """Prove whether any excluded multi-orbit label could reach a priority pair."""
    evidence = rank10_evidence()
    source_labels = sorted(
        {
            str(row["label"])
            for row in polynomials
            if len((orbits.get(str(row["label"])) or {}).get("targets") or []) > 1
        },
        key=lambda label: int(label[3:]),
    )
    target_labels = sorted(
        {
            str(target["targetLabel"])
            for label in source_labels
            for target in orbits[label]["targets"]
        },
        key=lambda label: int(label[3:]),
    )
    priority_pairs = []
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        for label in target_labels:
            for target_r in range(0, 25, 2):
                pair = (label, target_r)
                state = current_pair_state(connection, pair)
                if state["baseline"] or state["locallyOwnedRows"]:
                    continue
                if state["teamCount"] == 0 or (
                    state["teamCount"] == 1 and pair in evidence
                ):
                    priority_pairs.append(
                        {
                            "label": label,
                            "r": target_r,
                            "teamCount": state["teamCount"],
                            "priorityKind": (
                                "live_gold"
                                if state["teamCount"] == 0
                                else "rank10_solo_raid"
                            ),
                        }
                    )
    finally:
        connection.close()
    return {
        "sourceLabels": source_labels,
        "sourceLabelCount": len(source_labels),
        "targetLabels": target_labels,
        "targetLabelCount": len(target_labels),
        "currentGoldOrT00110Pairs": priority_pairs,
        "excludedSafelyWithoutFactorAssignment": not priority_pairs,
    }


def run_pair_route(route: dict, timeout: int) -> dict:
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
    row, failure = run_json(command, timeout)
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


def post_certification_audit(result: dict, evidence: dict[tuple[str, int], dict]) -> dict:
    if result.get("status") != "certified" or int(result.get("returnCode", 1)) != 0:
        return {"stageable": False, "reason": "worker_not_certified"}
    line = str(result["coefficientLine"])
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result["coefficientSha256"]):
        return {"stageable": False, "reason": "coefficient_hash_mismatch"}
    pair = (str(result["targetLabel"]), int(result["targetR"]))
    forced = (str(result["forcedTargetLabel"]), int(result["forcedTargetR"]))
    if pair != forced:
        return {"stageable": False, "reason": "forced_pair_mismatch"}
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        state = current_pair_state(connection, pair)
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
        "pair": f"{pair[0]}/r{pair[1]}",
        "stageable": False,
        "unseenCoefficientHash": hash_rows == 0,
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
    if state["teamCount"] == 1 and holder is not None and result.get("fieldDiscriminantAbs"):
        holder_disc = int(holder.get("minScoringDiscAbs") or holder["scoringDiscAbs"])
        candidate_disc = int(result["fieldDiscriminantAbs"])
        ratio = min(1.0, math.log(holder_disc) / math.log(candidate_disc))
        return {
            **base,
            "candidateFieldDiscAbs": str(candidate_disc),
            "discRatio": ratio,
            "holderScoringDiscAbs": str(holder_disc),
            "priorityKind": "rank10_solo_raid",
            "projectedCandidateScore": 0.5 * ratio,
            "projectedNetRelativeSwing": 0.5 + 0.5 * ratio,
            "rank10HolderTeamId": RANK10_TEAM_ID,
            "rank10HolderTeamNumber": RANK10_TEAM_NUMBER,
            "stageable": True,
        }
    return {**base, "reason": "no_longer_current_gold_or_t00110_solo"}


def run_pilot(routes: list[dict], workers: int, timeout: int):
    evidence = rank10_evidence()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_pair_route, route, timeout): route for route in routes}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            row["postCertificationAudit"] = post_certification_audit(row, evidence)
            results.append(row)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "event": "pair_pilot",
                        "pilotRank": row["pilotRank"],
                        "stageable": row["postCertificationAudit"].get("stageable", False),
                        "status": row.get("status"),
                        "target": f"{row.get('targetLabel')}/r{row.get('targetR')}",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    results.sort(key=lambda row: int(row["pilotRank"]))
    staged_hashes = set()
    staged_pairs = set()
    hits = []
    manifest = []
    for row in results:
        audit = row["postCertificationAudit"]
        if not audit.get("stageable"):
            continue
        digest = str(row["coefficientSha256"])
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        if digest in staged_hashes or pair in staged_pairs:
            audit["stageable"] = False
            audit["reason"] = "duplicate_hit_within_delta"
            continue
        staged_hashes.add(digest)
        staged_pairs.add(pair)
        manifest.append(str(row["coefficientLine"]))
        hits.append(row)
    write_jsonl(RESULTS, results)
    write_jsonl(HITS, hits)
    write_lines(MANIFEST, manifest)
    return results, hits, manifest


def direct_flags(polynomials: list[dict]) -> list[dict]:
    reverse: dict[str, list[int]] = defaultdict(list)
    for quotient_t, labels in DIRECT_LABELS_BY_Q.items():
        for label in labels:
            reverse[label].append(quotient_t)
    flags = []
    for row in polynomials:
        quotient_hash = row.get("quotientPolynomialSha256")
        if quotient_hash is None:
            continue
        for quotient_t in reverse.get(str(row["label"]), []):
            flags.append(
                {
                    **row,
                    "flagStatus": "exact_direct_action_label_candidate",
                    "quotientT": quotient_t,
                    "requiresArithmeticAlignment": True,
                }
            )
    flags.sort(
        key=lambda row: (
            int(row["quotientT"]),
            int(row["label"][3:]),
            row["submissionId"],
            int(row["polynomialIndex"]),
        )
    )
    write_jsonl(DIRECT_FLAGS, flags)
    return flags


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--map-timeout", type=int, default=90)
    parser.add_argument("--profile-timeout", type=int, default=180)
    parser.add_argument("--pilot-timeout", type=int, default=360)
    parser.add_argument("--max-pilots", type=int, default=24)
    args = parser.parse_args()
    if args.workers != 6:
        raise ValueError("page-19 exact pilot must use exactly six workers")
    if args.max_pilots < 1 or args.max_pilots > 48:
        raise ValueError("--max-pilots must be between 1 and 48")

    started = time.monotonic()
    submissions, polynomials = freeze_delta(args.db)
    if not submissions:
        raise ValueError("page-19 delta is empty")
    flags = direct_flags(polynomials)
    orbits, map_requested, map_delta, map_failures = exact_orbit_maps(
        polynomials, args.workers, args.map_timeout
    )
    profiles, profile_requested, profile_delta, profile_failures = exact_profiles(
        polynomials, orbits, args.workers, args.profile_timeout
    )
    routes, route_skips, eligible_routes = build_routes(
        polynomials, orbits, profiles, args.max_pilots
    )
    multi_priority = multi_orbit_priority_audit(polynomials, orbits)
    if routes:
        results, hits, manifest = run_pilot(
            routes, args.workers, args.pilot_timeout
        )
    else:
        results, hits, manifest = [], [], []
        write_jsonl(RESULTS, [])
        write_jsonl(HITS, [])
        write_lines(MANIFEST, [])

    paths = {
        "combinedOrbitMap": COMBINED_ORBITS,
        "deltaOrbitMap": DELTA_ORBITS,
        "deltaProfiles": DELTA_PROFILES,
        "directCharacterFlags": DIRECT_FLAGS,
        "frozenPolynomials": FROZEN_POLYNOMIALS,
        "frozenSubmissions": FROZEN_SUBMISSIONS,
        "hits": HITS,
        "manifest": MANIFEST,
        "results": RESULTS,
        "routes": ROUTES,
    }
    summary = {
        "deltaCreatedBeforeExclusive": PAGE18_MIN_CREATED_AT,
        "deltaSyncedAfterExclusive": PAGE18_MAX_SYNCED_AT,
        "directCharacterFlagCount": len(flags),
        "directCharacterFlagsByQuotient": dict(
            sorted(Counter(str(row["quotientT"]) for row in flags).items())
        ),
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "eligibleExactPriorityRoutes": eligible_routes,
        "frozenScoreablePolynomials": len(polynomials),
        "frozenSubmissions": len(submissions),
        "goldHits": sum(
            row["postCertificationAudit"].get("priorityKind") == "live_gold"
            for row in hits
        ),
        "mapFailures": map_failures,
        "multiOrbitPriorityAudit": multi_priority,
        "newOrbitMapsCertified": len(map_delta),
        "newOrbitMapsRequested": len(map_requested),
        "newProfilesCertified": len(profile_delta),
        "newProfilesRequested": len(profile_requested),
        "networkCalls": 0,
        "paths": {
            key: {"path": str(path.resolve()), "sha256": sha256_path(path)}
            for key, path in paths.items()
        },
        "pilotCertified": sum(row.get("status") == "certified" for row in results),
        "pilotHits": len(hits),
        "pilotRoutes": len(routes),
        "pilotWorkers": args.workers,
        "profileFailures": profile_failures,
        "rank10RaidHits": sum(
            row["postCertificationAudit"].get("priorityKind") == "rank10_solo_raid"
            for row in hits
        ),
        "routeSkipCounts": route_skips,
        "stagedManifestRows": len(manifest),
        "submissionCalls": 0,
    }
    write_json(SUMMARY, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0 if not map_failures and not profile_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

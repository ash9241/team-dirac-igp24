#!/usr/bin/env python3
"""Exact live-gold pipeline for certified degree-24 3-subset actions.

No pair-action artifact is read by this program.  Its structural evidence is
limited to the four triple-orbit shards plus independently recomputed GAP
signature profiles and arithmetic triple-resolvent certificates.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import glob
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CERTIFICATE_SHARDS = tuple(
    DATA / f"agent_gold_a_triple_orbit_shard{index}.jsonl" for index in range(4)
)
PROFILE_WORKER = ROOT / "agent_gold_a_triple_profile_census.sage.py"
ARITHMETIC_WORKER = ROOT / "agent_gold_a_triple_sum_one.sage.py"
CANDIDATES = DATA / "agent_gold_a_triple_current_candidates.jsonl"
PROFILE_SHARDS = tuple(
    DATA / f"agent_gold_a_triple_current_profile_shard{index}.jsonl"
    for index in range(6)
)
PROFILES = DATA / "agent_gold_a_triple_current_profiles.jsonl"
ROUTES = DATA / "agent_gold_a_triple_live_routes.jsonl"
RESULTS = DATA / "agent_gold_a_triple_live_results.jsonl"
HITS = DATA / "agent_gold_a_triple_live_hits.jsonl"
MANIFEST = ROOT / "outbox" / "agent_gold_a_triple_first_live_hit.txt"
SUMMARY = DATA / "agent_gold_a_triple_live_summary.json"


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def canonical_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


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


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_jsonl(path: Path, rows: list[dict]) -> None:
    atomic_text(
        path,
        "".join(canonical_json(row) + "\n" for row in rows),
    )


def write_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def current_gold(max_team_count: int = 0) -> tuple[set[tuple[str, int]], dict]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
        owned = set(
            connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE status='accepted'"
            )
        )
        target_rows = list(
            connection.execute(
                "SELECT label,r,team_count,generated_at FROM targets"
            )
        )
    finally:
        connection.close()
    gold = {
        (str(label), int(r))
        for label, r, count, _generated in target_rows
        if int(count) <= max_team_count
        and (str(label), int(r)) not in baseline
        and (str(label), int(r)) not in owned
    }
    metadata = {
        "goldPairs": len(gold),
        "maximumTeamCount": max_team_count,
        "targetGeneratedAtMax": max(str(row[3]) for row in target_rows),
        "targetGeneratedAtMin": min(str(row[3]) for row in target_rows),
        "targetRows": len(target_rows),
    }
    return gold, metadata


def freeze_candidates(gold: set[tuple[str, int]]):
    gold_by_label: dict[str, set[int]] = defaultdict(set)
    for label, r in gold:
        gold_by_label[label].add(r)
    rows = []
    seen_labels = set()
    shard_audit = []
    for shard in CERTIFICATE_SHARDS:
        certificate_rows = read_jsonl(shard)
        shard_hash = sha256_path(shard)
        shard_audit.append(
            {
                "path": str(shard.resolve()),
                "rows": len(certificate_rows),
                "sha256": shard_hash,
            }
        )
        for certificate in certificate_rows:
            if certificate.get("status") != "certified":
                raise ValueError(f"uncertified triple row in {shard}")
            label = str(certificate["sourceLabel"])
            if label in seen_labels:
                raise ValueError(f"duplicate triple certificate label {label}")
            seen_labels.add(label)
            targets = list(certificate.get("targets") or [])
            if len(targets) != int(certificate["length24OrbitCount"]):
                raise ValueError("triple certificate target count mismatch")
            relevant = {
                str(target["targetLabel"]): sorted(
                    gold_by_label.get(str(target["targetLabel"]), set())
                )
                for target in targets
                if gold_by_label.get(str(target["targetLabel"]))
            }
            if not targets or not relevant:
                continue
            row_hash = canonical_sha256(certificate)
            rows.append(
                {
                    **certificate,
                    "certificateRowSha256": row_hash,
                    "certificateSourcePath": str(shard.resolve()),
                    "certificateSourceSha256": shard_hash,
                    "currentGoldRsByTargetLabel": relevant,
                }
            )
    rows.sort(key=lambda row: int(row["sourceT"]))
    write_jsonl(CANDIDATES, rows)
    return rows, shard_audit, len(seen_labels)


def run_profile_shard(index: int, timeout: int) -> dict:
    command = [
        "sage",
        "-python",
        str(PROFILE_WORKER),
        "--input",
        str(CANDIDATES),
        "--output",
        str(PROFILE_SHARDS[index]),
        "--shard-index",
        str(index),
        "--shard-count",
        str(len(PROFILE_SHARDS)),
        "--checkpoint-every",
        "5",
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
        return {
            "command": command,
            "shard": index,
            "status": "timeout",
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    return {
        "command": command,
        "returnCode": completed.returncode,
        "shard": index,
        "status": "complete" if completed.returncode == 0 else "error",
        "stderrTail": completed.stderr[-1200:],
        "stdoutTail": completed.stdout[-1200:],
        "wallSeconds": round(time.monotonic() - started, 3),
    }


def compute_profiles(candidates: list[dict], timeout: int):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(PROFILE_SHARDS)) as pool:
        audits = list(pool.map(lambda index: run_profile_shard(index, timeout), range(len(PROFILE_SHARDS))))
    if any(row["status"] != "complete" for row in audits):
        raise RuntimeError(f"triple profile shard failure: {audits}")
    profiles = []
    for path in PROFILE_SHARDS:
        profiles.extend(read_jsonl(path))
    if any(row.get("status") != "certified" for row in profiles):
        raise RuntimeError("triple profile census contains uncertified rows")
    expected = {str(row["certificateRowSha256"]) for row in candidates}
    actual = {str(row["certificateRowSha256"]) for row in profiles}
    if actual != expected or len(profiles) != len(candidates):
        raise RuntimeError("triple profile census does not cover the frozen certificates")
    profiles.sort(key=lambda row: int(row["sourceT"]))
    write_jsonl(PROFILES, profiles)
    return profiles, audits


def normalized_command(command: list[str]) -> list[str]:
    normalized = []
    for value in command:
        path = Path(str(value))
        if path.name in ("sage", ARITHMETIC_WORKER.name):
            normalized.append(path.name)
        else:
            normalized.append(str(value))
    return normalized


def command_identity(command: list[str]) -> str:
    return canonical_sha256(normalized_command(command))


def nested_values(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_values(child)


def prior_command_identities() -> set[str]:
    identities = set()
    for name in glob.glob(str(DATA / "agent_gold_a_triple*.json*")):
        path = Path(name)
        if path in (CANDIDATES, PROFILES, ROUTES, SUMMARY):
            continue
        values = read_jsonl(path) if path.suffix == ".jsonl" else [json.loads(path.read_text())]
        for value in values:
            for row in nested_values(value):
                identity = row.get("commandIdentitySha256")
                if identity:
                    identities.add(str(identity))
                command = row.get("workerCommand")
                if (
                    isinstance(command, list)
                    and any(Path(str(part)).name == ARITHMETIC_WORKER.name for part in command)
                ):
                    identities.add(command_identity([str(part) for part in command]))
    return identities


def source_rows(label: str, r: int) -> list[dict]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT v.submission_id,v.polynomial_index,p.coefficient_hash,
                   length(p.original_line) coefficient_bytes
            FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.label=? AND v.r=?
            ORDER BY length(p.original_line),v.submission_id,v.polynomial_index
            """,
            (label, r),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def worker_command(source: dict) -> list[str]:
    return [
        "sage",
        "-python",
        str(ARITHMETIC_WORKER),
        str(source["submission_id"]),
        str(source["polynomial_index"]),
        "--transforms",
        "0,1",
        "--reduce",
        "best",
        "--nfdisc",
    ]


def build_routes(
    profiles: list[dict], gold: set[tuple[str, int]], pilot_limit: int
):
    prior = prior_command_identities()
    candidates = []
    exact_support = 0
    skips = Counter()
    for profile in profiles:
        label = str(profile["sourceLabel"])
        target_labels = {str(row["targetLabel"]) for row in profile["targets"]}
        if target_labels != {label}:
            skips["multiple_or_nonself_target_labels"] += 1
            continue
        for source_r in profile["sourceR"]:
            compatible = [
                row
                for row in profile["profiles"]
                if int(row["sourceR"]) == int(source_r)
            ]
            if not compatible:
                skips["no_compatible_class"] += 1
                continue
            per_class_gold = []
            for class_row in compatible:
                per_class_gold.append(
                    {
                        (str(signature["targetLabel"]), int(signature["targetR"]))
                        for signature in class_row["orbitSignatures"]
                        if (str(signature["targetLabel"]), int(signature["targetR"])) in gold
                    }
                )
            if not any(per_class_gold):
                skips["no_current_gold_in_any_compatible_class"] += 1
                continue
            exact_support += 1
            if not all(per_class_gold):
                skips["current_gold_not_supported_by_every_compatible_class"] += 1
                continue
            forced_pairs = set.intersection(*per_class_gold)
            source_options = source_rows(label, int(source_r))
            chosen = None
            for source in source_options:
                command = worker_command(source)
                identity = command_identity(command)
                if identity in prior:
                    continue
                chosen = (source, command, identity)
                break
            if chosen is None:
                skips["every_source_command_previously_used"] += 1
                continue
            source, command, identity = chosen
            candidates.append(
                {
                    "actionKind": "unordered_3_subset",
                    "commandIdentitySha256": identity,
                    "compatibleClassCount": len(compatible),
                    "compatibleClassIndexes": [int(row["classIndex"]) for row in compatible],
                    "exactProfileSha256": profile["exactProfileSha256"],
                    "forcedGoldPairsAcrossCompatibleClasses": [
                        [pair[0], pair[1]]
                        for pair in sorted(forced_pairs, key=lambda pair: (int(pair[0][3:]), pair[1]))
                    ],
                    "guaranteedSomeLiveGoldEveryCompatibleClass": True,
                    "length24OrbitCount": int(profile["length24OrbitCount"]),
                    "sourceCoefficientBytes": int(source["coefficient_bytes"]),
                    "sourceCoefficientSha256": str(source["coefficient_hash"]),
                    "sourceLabel": label,
                    "sourcePolynomialIndex": int(source["polynomial_index"]),
                    "sourceR": int(source_r),
                    "sourceSubmissionId": str(source["submission_id"]),
                    "sourceT": int(profile["sourceT"]),
                    "workerCommand": command,
                }
            )
    candidates.sort(
        key=lambda row: (
            not bool(row["forcedGoldPairsAcrossCompatibleClasses"]),
            int(row["length24OrbitCount"]),
            int(row["sourceCoefficientBytes"]),
            int(row["sourceT"]),
            int(row["sourceR"]),
        )
    )
    # First pass enforces both source-label and submission diversity.  A second
    # pass permits repeated submissions but never repeated source labels.
    selected = []
    used_labels = set()
    used_submissions = set()
    for row in candidates:
        if row["sourceLabel"] in used_labels or row["sourceSubmissionId"] in used_submissions:
            continue
        selected.append(row)
        used_labels.add(row["sourceLabel"])
        used_submissions.add(row["sourceSubmissionId"])
        if len(selected) == pilot_limit:
            break
    if len(selected) < pilot_limit:
        for row in candidates:
            if row["sourceLabel"] in used_labels:
                continue
            selected.append(row)
            used_labels.add(row["sourceLabel"])
            if len(selected) == pilot_limit:
                break
    for rank, row in enumerate(selected, start=1):
        row["pilotRank"] = rank
    write_jsonl(ROUTES, selected)
    return (
        selected,
        len(candidates),
        dict(sorted(skips.items())),
        len(prior),
        exact_support,
    )


def run_one(route: dict, timeout: int) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            route["workerCommand"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            **route,
            "status": "timeout",
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {
            **route,
            "returnCode": completed.returncode,
            "status": "no_output",
            "stderrTail": completed.stderr[-1600:],
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {
            **route,
            "error": str(exc),
            "returnCode": completed.returncode,
            "status": "invalid_output",
            "stderrTail": completed.stderr[-1600:],
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    return {
        **route,
        **result,
        "exactProfileSha256": route["exactProfileSha256"],
        "returnCode": completed.returncode,
        "stderrTail": completed.stderr[-1600:],
        "wallSeconds": round(time.monotonic() - started, 3),
        "workerExactProfileSha256": result.get("exactProfileSha256"),
    }


def candidate_audit(result: dict, max_team_count: int) -> list[dict]:
    if result.get("status") != "certified_multi" or int(result.get("returnCode", 1)) != 0:
        return []
    if str(result.get("workerExactProfileSha256")) != str(result.get("exactProfileSha256")):
        return []
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    audited = []
    try:
        for candidate in result.get("candidates", []):
            line = str(candidate["coefficientLine"])
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            pair = (str(candidate["targetLabel"]), int(candidate["targetR"]))
            target = connection.execute(
                "SELECT team_count,generated_at FROM targets WHERE label=? AND r=?", pair
            ).fetchone()
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()
            owned = int(
                connection.execute(
                    "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND status='accepted'",
                    pair,
                ).fetchone()[0]
            )
            hash_rows = int(
                connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
                ).fetchone()[0]
            )
            stageable = bool(
                digest == str(candidate["coefficientSha256"])
                and target is not None
                and int(target[0]) <= max_team_count
                and baseline is None
                and owned == 0
                and hash_rows == 0
            )
            audited.append(
                {
                    **candidate,
                    "audit": {
                        "baseline": baseline is not None,
                        "coefficientSha256Verified": digest == str(candidate["coefficientSha256"]),
                        "generatedAt": str(target[1]) if target else None,
                        "ledgerCoefficientRows": hash_rows,
                        "locallyOwnedRows": owned,
                        "stageable": stageable,
                        "teamCount": int(target[0]) if target else None,
                    },
                }
            )
    finally:
        connection.close()
    return audited


def run_pilot(routes: list[dict], workers: int, timeout: int, max_team_count: int):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, route, timeout): route for route in routes}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            row = future.result()
            row["auditedCandidates"] = candidate_audit(row, max_team_count)
            results.append(row)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "event": "triple_pilot",
                        "liveHits": sum(
                            candidate["audit"]["stageable"]
                            for candidate in row["auditedCandidates"]
                        ),
                        "pilotRank": row["pilotRank"],
                        "status": row.get("status"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    results.sort(key=lambda row: int(row["pilotRank"]))
    hits = []
    for row in results:
        for candidate in row["auditedCandidates"]:
            if candidate["audit"]["stageable"]:
                hits.append(
                    {
                        "actionKind": "unordered_3_subset",
                        "commandIdentitySha256": row["commandIdentitySha256"],
                        "exactProfileSha256": row["exactProfileSha256"],
                        "pilotRank": row["pilotRank"],
                        "sourceLabel": row["sourceLabel"],
                        "sourcePolynomialIndex": row["sourcePolynomialIndex"],
                        "sourceR": row["sourceR"],
                        "sourceSubmissionId": row["sourceSubmissionId"],
                        **candidate,
                    }
                )
    hits.sort(key=lambda row: (int(row["pilotRank"]), int(row["factorIndex"])))
    write_jsonl(RESULTS, results)
    write_jsonl(HITS, hits)
    first = hits[0] if hits else None
    atomic_text(MANIFEST, (str(first["coefficientLine"]) + "\n") if first else "")
    return results, hits, first


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-timeout", type=int, default=900)
    parser.add_argument("--pilot-timeout", type=int, default=900)
    parser.add_argument("--pilot-limit", type=int, default=4)
    parser.add_argument("--pilot-workers", type=int, default=4)
    parser.add_argument("--max-team-count", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.pilot_limit <= 12 or not 1 <= args.pilot_workers <= 6:
        raise ValueError("pilot bounds are invalid")

    started = time.monotonic()
    gold, target_metadata = current_gold(args.max_team_count)
    candidates, shard_audit, certificate_universe = freeze_candidates(gold)
    profiles, profile_audit = compute_profiles(candidates, args.profile_timeout)
    # Refresh only from the already-refreshed local target table after the GAP census.
    gold_final, target_metadata_final = current_gold(args.max_team_count)
    routes, eligible, skips, prior_count, exact_support = build_routes(
        profiles, gold_final, args.pilot_limit
    )
    if routes:
        results, hits, first = run_pilot(
            routes, min(args.pilot_workers, len(routes)), args.pilot_timeout,
            args.max_team_count,
        )
    else:
        results, hits, first = [], [], None
        write_jsonl(RESULTS, [])
        write_jsonl(HITS, [])
        atomic_text(MANIFEST, "")

    paths = {
        "candidates": CANDIDATES,
        "hits": HITS,
        "manifest": MANIFEST,
        "profiles": PROFILES,
        "results": RESULTS,
        "routes": ROUTES,
    }
    for index, path in enumerate(PROFILE_SHARDS):
        paths[f"profileShard{index}"] = path
    summary = {
        "actionKind": "unordered_3_subset",
        "certificateShardAudit": shard_audit,
        "certificateUniverseRows": certificate_universe,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "eligibleExactGuaranteedRoutes": eligible,
        "exactCompatibleClassSupportRoutes": exact_support,
        "firstLiveHit": (
            {
                "coefficientSha256": first["coefficientSha256"],
                "pair": [first["targetLabel"], first["targetR"]],
                "pilotRank": first["pilotRank"],
                "sourceLabel": first["sourceLabel"],
            }
            if first
            else None
        ),
        "frozenCandidateCertificateRows": len(candidates),
        "liveHitCount": len(hits),
        "maximumTeamCount": args.max_team_count,
        "pairActionArtifactsRead": 0,
        "paths": {
            key: {"path": str(path.resolve()), "sha256": sha256_path(path)}
            for key, path in paths.items()
        },
        "pilotCertified": sum(row.get("status") == "certified_multi" for row in results),
        "pilotCommandCount": len(routes),
        "pilotLimit": args.pilot_limit,
        "pilotSourceLabels": [row["sourceLabel"] for row in routes],
        "pilotSourceSubmissions": [row["sourceSubmissionId"] for row in routes],
        "priorCommandIdentitiesExcluded": prior_count,
        "profileCensusAudit": profile_audit,
        "profileRowsCertified": len(profiles),
        "routeSkipCounts": skips,
        "stagedManifestRows": 1 if first else 0,
        "targetSnapshotAtCandidateFreeze": target_metadata,
        "targetSnapshotAtRouteFreeze": target_metadata_final,
    }
    write_json(SUMMARY, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

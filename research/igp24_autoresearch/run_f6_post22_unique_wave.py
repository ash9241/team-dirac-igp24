#!/usr/bin/env python3
"""Run the complete post-2026-07-22 F6 unique unordered-pair wave.

The GAP census is treated as an immutable action certificate.  One smallest
accepted source receipt is selected for every unique-orbit route.  Routes are
ordered coverage-first (one route per target before sibling routes), and every
completed route is appended and fsynced before the next worker starts.

Only candidates whose *realized* (label, r) pair still passes the live tc0
gate are retained with coefficients in the hit checkpoint.  Non-hits retain a
coefficient-free audit record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CAMPAIGN = DATA / "campaign_20260727_f627"
CENSUS = CAMPAIGN / "f6_post22_pair_revival_census.jsonl"
PLAN = CAMPAIGN / "f6_post22_unique_wave_plan.json"
RESULTS = CAMPAIGN / "f6_post22_unique_wave_results.jsonl"
HITS = CAMPAIGN / "f6_post22_unique_wave_exact_hits.jsonl"
SUMMARY = CAMPAIGN / "f6_post22_unique_wave_summary.json"
WORKER = ROOT / "pair_sum_one.sage.py"
IMPORT_GLOB = "f6_post22_*_unique.jsonl"
CURRENT_CENSUS_GLOB = "f6_current_action_shard*_of8_20260812.jsonl"


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_checkpoint(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (canonical_json(row) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def route_key(source_label: str, source_r: int, orbit_index: int, target_label: str) -> str:
    return f"{source_label}|r{source_r}|oi{orbit_index}|{target_label}"


def smallest_source(connection: sqlite3.Connection, label: str, source_r: int) -> dict:
    rows = connection.execute(
        """
        SELECT p.submission_id,p.polynomial_index,p.coefficient_hash,
               length(p.original_line) AS coefficient_bytes
        FROM polynomials p
        JOIN verifications v USING(submission_id,polynomial_index)
        WHERE v.label=? AND v.r=? AND v.scoreable=1 AND v.status='accepted'
        ORDER BY coefficient_bytes,p.coefficient_hash,p.submission_id,p.polynomial_index
        """,
        (label, source_r),
    ).fetchall()
    if not rows:
        raise ValueError(f"no accepted scoreable source for {label}/r{source_r}")
    # Duplicate receipts with the same polynomial are deliberately collapsed.
    return dict(rows[0])


def route_profile(census_row: dict, route: dict) -> dict:
    source_r = int(route["sourceR"])
    orbit_index = int(route["orbitIndex"])
    gold = {int(value) for value in route["goldR"]}
    compatible = []
    for profile in census_row.get("profiles") or []:
        if int(profile["sourceR"]) != source_r:
            continue
        for target in profile.get("targets") or []:
            if int(target["orbitIndex"]) == orbit_index:
                compatible.append(
                    {
                        "classIndex": int(profile["classIndex"]),
                        "classSize": int(profile["classSize"]),
                        "targetR": int(target["targetR"]),
                    }
                )
    total_weight = sum(item["classSize"] for item in compatible)
    gold_weight = sum(
        item["classSize"] for item in compatible if item["targetR"] in gold
    )
    return {
        "compatibleClasses": compatible,
        "goldClassCount": sum(item["targetR"] in gold for item in compatible),
        "compatibleClassCount": len(compatible),
        "goldClassWeight": gold_weight,
        "compatibleClassWeight": total_weight,
    }


def priority(route: dict) -> tuple:
    total_weight = int(route["compatibleClassWeight"])
    total_classes = int(route["compatibleClassCount"])
    weighted_fraction = (
        int(route["goldClassWeight"]) / total_weight if total_weight else 0.0
    )
    class_fraction = (
        int(route["goldClassCount"]) / total_classes if total_classes else 0.0
    )
    return (
        not bool(route["allCompatibleClassesGold"]),
        route["deterministicTargetR"] is None,
        -weighted_fraction,
        -class_fraction,
        int(route["sourceCoefficientBytes"]),
        int(route["targetT"]),
        str(route["targetLabel"]),
        int(route["sourceT"]),
        str(route["sourceLabel"]),
        int(route["sourceR"]),
    )


def build_plan(current: bool = False) -> dict:
    census_paths = sorted(DATA.glob(CURRENT_CENSUS_GLOB)) if current else [CENSUS]
    if not census_paths:
        raise ValueError("no census files found")
    census_rows = [
        (path, row)
        for path in census_paths
        for row in read_jsonl(path)
    ]
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    routes = []
    all_route_targets = set()
    try:
        for census_path, census_row in census_rows:
            for raw_route in census_row.get("routes") or []:
                all_route_targets.add(str(raw_route["targetLabel"]))
                if int(census_row["length24OrbitCount"]) != 1:
                    continue
                source_label = str(census_row["sourceLabel"])
                source_r = int(raw_route["sourceR"])
                source = smallest_source(connection, source_label, source_r)
                profile = route_profile(census_row, raw_route)
                route = {
                    **raw_route,
                    **profile,
                    "routeKey": route_key(
                        source_label,
                        source_r,
                        int(raw_route["orbitIndex"]),
                        str(raw_route["targetLabel"]),
                    ),
                    "sourceLabel": source_label,
                    "sourceT": int(census_row["sourceT"]),
                    "sourceR": source_r,
                    "sourceSubmissionId": str(source["submission_id"]),
                    "sourcePolynomialIndex": int(source["polynomial_index"]),
                    "sourceCoefficientSha256": str(source["coefficient_hash"]),
                    "sourceCoefficientBytes": int(source["coefficient_bytes"]),
                    "orbitSizes": [int(value) for value in census_row["orbitSizes"]],
                    "orbitCertificateSha256": str(
                        census_row["exactCertificateSha256"]
                    ),
                    "censusPath": str(census_path.relative_to(ROOT)),
                }
                routes.append(route)
    finally:
        connection.close()

    keys = [item["routeKey"] for item in routes]
    if not routes or len(routes) != len(set(keys)):
        raise ValueError(
            f"unique-route census changed: routes={len(routes)}, keys={len(set(keys))}"
        )

    ordered_by_quality = sorted(routes, key=priority)
    first_coverage = []
    remainder = []
    seen_targets = set()
    for route in ordered_by_quality:
        target = str(route["targetLabel"])
        if target not in seen_targets:
            first_coverage.append(route)
            seen_targets.add(target)
        else:
            remainder.append(route)
    ordered = first_coverage + remainder
    unique_targets = {str(item["targetLabel"]) for item in routes}
    multi_only = sorted(
        all_route_targets - unique_targets,
        key=lambda label: int(label.removeprefix("24T")),
    )
    plan = {
        "schemaVersion": "f6-post22-unique-wave-plan-v1",
        "censusPaths": [str(path.relative_to(ROOT)) for path in census_paths],
        "censusSha256": {
            str(path.relative_to(ROOT)): sha256_file(path) for path in census_paths
        },
        "routeCount": len(ordered),
        "coverageFirstCount": len(first_coverage),
        "uniqueOrbitTargetCount": len(unique_targets),
        "allRouteTargetCount": len(all_route_targets),
        "multiOrbitOnlyTargets": multi_only,
        "deduplication": (
            "one smallest accepted receipt per "
            "(sourceLabel,sourceR,orbitIndex,targetLabel); duplicate source hashes collapsed"
        ),
        "transforms": [1, 2, 3],
        "reduction": "best",
        "nfdisc": True,
        "checkpointEvery": 1,
        "submissionCalls": 0,
        "networkCalls": 0,
        "routes": ordered,
    }
    rendered = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if PLAN.exists():
        existing = PLAN.read_bytes()
        if existing != rendered:
            raise ValueError("existing wave plan differs from current deterministic plan")
    else:
        atomic_write(PLAN, rendered)
    return plan


def live_gate(connection: sqlite3.Connection, label: str, r: int) -> dict:
    target = connection.execute(
        """
        SELECT team_count,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        (label, r),
    ).fetchone()
    baseline = int(
        connection.execute(
            "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
            (label, r),
        ).fetchone()[0]
    )
    owned = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM verifications
            WHERE label=? AND r=? AND scoreable=1
            """,
            (label, r),
        ).fetchone()[0]
    )
    current = bool(
        target is not None
        and int(target["team_count"]) == 0
        and int(target["discovered"]) == 0
        and baseline == 0
        and owned == 0
    )
    return {
        "teamCount": int(target["team_count"]) if target is not None else None,
        "discovered": int(target["discovered"]) if target is not None else None,
        "generatedAt": str(target["generated_at"]) if target is not None else None,
        "baselineMatches": baseline,
        "ownedScoreableMatches": owned,
        "currentTc0": current,
    }


def candidate_ledger_duplicates(
    connection: sqlite3.Connection, coefficient_hash: str, coefficient_line: str
) -> dict:
    return {
        "coefficientHashMatches": int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (coefficient_hash,),
            ).fetchone()[0]
        ),
        "coefficientLineMatches": int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficients=?",
                (coefficient_line,),
            ).fetchone()[0]
        ),
    }


def candidate_summary(result: dict) -> dict:
    return {
        "status": str(result["status"]),
        "targetLabel": str(result["targetLabel"]),
        "targetT": int(result["targetT"]),
        "targetR": int(result["targetR"]),
        "coefficientBytes": int(result["coefficientBytes"]),
        "coefficientSha256": str(result["coefficientSha256"]),
        "fieldDiscriminantAbs": str(result["fieldDiscriminantAbs"]),
        "polynomialDiscriminantAbs": str(result["polynomialDiscriminantAbs"]),
        "transform": result["transform"],
        "reduction": str(result["reduction"]),
        "orbitCertificate": result["orbitCertificate"],
        "attempts": result["attempts"],
        "elapsedSeconds": float(result["elapsedSeconds"]),
    }


def certify_result(route: dict, result: dict, provenance: dict) -> tuple[dict, dict | None]:
    if result.get("status") != "certified":
        raise ValueError("worker did not return a certified unique-orbit result")
    expected_fields = {
        "sourceLabel": route["sourceLabel"],
        "sourceR": route["sourceR"],
        "sourceSubmissionId": route["sourceSubmissionId"],
        "sourcePolynomialIndex": route["sourcePolynomialIndex"],
        "sourceCoefficientSha256": route["sourceCoefficientSha256"],
        "targetLabel": route["targetLabel"],
    }
    for field, expected in expected_fields.items():
        if result.get(field) != expected:
            raise ValueError(
                f"worker result mismatch for {field}: "
                f"{result.get(field)!r} != {expected!r}"
            )
    line = str(result["coefficientLine"])
    digest = sha256_bytes(line.encode("ascii"))
    if digest != str(result["coefficientSha256"]):
        raise ValueError("worker candidate coefficient hash mismatch")
    coefficients = [int(value) for value in line.split(",")]
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
    ):
        raise ValueError("worker candidate failed primitive monic degree-24 shape")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        target_label = str(result["targetLabel"])
        target_r = int(result["targetR"])
        gate = live_gate(connection, target_label, target_r)
        duplicates = candidate_ledger_duplicates(connection, digest, line)
    finally:
        connection.close()
    planned_gold = target_r in {int(value) for value in route["goldR"]}
    exact_hit = planned_gold and bool(gate["currentTc0"])
    common = {
        "schemaVersion": "f6-post22-unique-wave-result-v1",
        "routeKey": route["routeKey"],
        "routeOrdinal": int(route["routeOrdinal"]),
        "sourceLabel": route["sourceLabel"],
        "sourceR": int(route["sourceR"]),
        "sourceSubmissionId": route["sourceSubmissionId"],
        "sourcePolynomialIndex": int(route["sourcePolynomialIndex"]),
        "sourceCoefficientSha256": route["sourceCoefficientSha256"],
        "targetLabel": route["targetLabel"],
        "goldR": [int(value) for value in route["goldR"]],
        "realizedPair": {"label": target_label, "r": target_r},
        "plannedGold": planned_gold,
        "liveGate": gate,
        "ledgerDuplicateExclusions": duplicates,
        "provenance": provenance,
        "candidate": candidate_summary(result),
        "status": "exact_current_tc0" if exact_hit else "exact_non_tc0",
    }
    hit = None
    if exact_hit:
        hit = {
            **common,
            "schemaVersion": "f6-post22-unique-wave-hit-v1",
            "candidate": result,
        }
    return common, hit


def parse_worker_stdout(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def run_worker(route: dict, timeout: int) -> tuple[dict | None, dict]:
    command = [
        "sage",
        "-python",
        str(WORKER),
        str(route["sourceSubmissionId"]),
        str(route["sourcePolynomialIndex"]),
        "--expected-target",
        str(route["targetLabel"]),
        "--expected-source-hash",
        str(route["sourceCoefficientSha256"]),
        "--orbit-map",
        str(ROOT / route["censusPath"]),
        "--transforms",
        "1,2,3",
        "--reduce",
        "best",
        "--nfdisc",
    ]
    environment = dict(os.environ)
    environment["SAGE_NUM_THREADS"] = "1"
    environment["OPENBLAS_NUM_THREADS"] = "1"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return None, {
            "kind": "worker",
            "status": "timeout",
            "timeoutSeconds": timeout,
            "wallSeconds": round(time.monotonic() - started, 3),
            "stderrTail": (error.stderr or "")[-1000:]
            if isinstance(error.stderr, str)
            else None,
        }
    result = parse_worker_stdout(completed.stdout)
    provenance = {
        "kind": "worker",
        "returnCode": int(completed.returncode),
        "wallSeconds": round(time.monotonic() - started, 3),
        "stderrSha256": sha256_bytes(completed.stderr.encode("utf-8")),
        "stderrTail": completed.stderr[-1000:],
    }
    if completed.returncode != 0:
        provenance["status"] = "worker_error"
        return result, provenance
    provenance["status"] = "completed"
    return result, provenance


def existing_imports(routes_by_key: dict[str, dict]) -> dict[str, tuple[dict, dict]]:
    imported = {}
    for path in sorted(DATA.glob(IMPORT_GLOB)):
        rows = read_jsonl(path)
        if len(rows) != 1 or rows[0].get("status") != "certified":
            continue
        result = rows[0]
        key = route_key(
            str(result["sourceLabel"]),
            int(result["sourceR"]),
            int(result["orbitTargets"][0]["orbitIndex"]),
            str(result["targetLabel"]),
        )
        if key not in routes_by_key or key in imported:
            continue
        imported[key] = (
            result,
            {
                "kind": "prewave_import",
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_file(path),
                "status": "completed",
            },
        )
    return imported


def write_summary(plan: dict) -> dict:
    results = read_jsonl(RESULTS)
    hits = read_jsonl(HITS)
    statuses = sorted({str(row["status"]) for row in results})
    summary = {
        "schemaVersion": "f6-post22-unique-wave-summary-v1",
        "routeCount": int(plan["routeCount"]),
        "coverageFirstCount": int(plan["coverageFirstCount"]),
        "uniqueOrbitTargetCount": int(plan["uniqueOrbitTargetCount"]),
        "allRouteTargetCount": int(plan["allRouteTargetCount"]),
        "completed": len({str(row["routeKey"]) for row in results}),
        "exactCurrentTc0": len(hits),
        "exactCurrentTc0Pairs": sorted(
            {
                (str(row["realizedPair"]["label"]), int(row["realizedPair"]["r"]))
                for row in hits
            }
        ),
        "statusCounts": {
            status: sum(str(row["status"]) == status for row in results)
            for status in statuses
        },
        "planPath": str(PLAN.relative_to(ROOT)),
        "resultsPath": str(RESULTS.relative_to(ROOT)),
        "hitsPath": str(HITS.relative_to(ROOT)),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    rendered = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = SUMMARY.with_suffix(SUMMARY.suffix + f".{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, SUMMARY)
    return summary


def main() -> int:
    global PLAN, RESULTS, HITS, SUMMARY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--current-20260812",
        action="store_true",
        help="Use the eight refreshed 2026-08-12 census shards and isolated outputs.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()

    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("require 0 <= --shard-index < --shard-count")

    if args.current_20260812:
        suffix = (
            ""
            if args.shard_count == 1
            else f"_shard{args.shard_index}_of{args.shard_count}"
        )
        PLAN = DATA / "f6_current_unique_wave_plan_20260812.json"
        RESULTS = DATA / f"f6_current_unique_wave_results{suffix}_20260812.jsonl"
        HITS = DATA / f"f6_current_unique_wave_exact_hits{suffix}_20260812.jsonl"
        SUMMARY = DATA / f"f6_current_unique_wave_summary{suffix}_20260812.json"

    plan = build_plan(current=args.current_20260812)
    for ordinal, route in enumerate(plan["routes"], 1):
        route["routeOrdinal"] = ordinal
    if args.prepare_only:
        print(
            canonical_json(
                {
                    "event": "prepared",
                    "plan": str(PLAN.relative_to(ROOT)),
                    "routeCount": plan["routeCount"],
                    "uniqueOrbitTargetCount": plan["uniqueOrbitTargetCount"],
                }
            ),
            flush=True,
        )
        return 0

    completed_keys = {str(row["routeKey"]) for row in read_jsonl(RESULTS)}
    hit_hashes = {
        str(row["candidate"]["coefficientSha256"]) for row in read_jsonl(HITS)
    }
    routes_by_key = {str(route["routeKey"]): route for route in plan["routes"]}
    imports = existing_imports(routes_by_key)
    pending = [
        route
        for route in plan["routes"]
        if route["routeKey"] not in completed_keys
        and (int(route["routeOrdinal"]) - 1) % args.shard_count == args.shard_index
    ]
    if args.limit is not None:
        pending = pending[: args.limit]

    for route in pending:
        key = str(route["routeKey"])
        if key in imports:
            result, provenance = imports[key]
        else:
            result, provenance = run_worker(route, args.timeout)
        if (
            not isinstance(result, dict)
            or provenance.get("status") != "completed"
            or result.get("status") != "certified"
        ):
            row = {
                "schemaVersion": "f6-post22-unique-wave-result-v1",
                "routeKey": key,
                "routeOrdinal": int(route["routeOrdinal"]),
                "sourceLabel": route["sourceLabel"],
                "sourceR": int(route["sourceR"]),
                "targetLabel": route["targetLabel"],
                "status": str(provenance.get("status", "worker_error")),
                "provenance": provenance,
            }
            append_checkpoint(RESULTS, row)
            print(
                canonical_json(
                    {
                        "event": "checkpoint",
                        "routeOrdinal": route["routeOrdinal"],
                        "routeCount": plan["routeCount"],
                        "routeKey": key,
                        "status": row["status"],
                    }
                ),
                flush=True,
            )
            continue
        row, hit = certify_result(route, result, provenance)
        append_checkpoint(RESULTS, row)
        if hit is not None:
            candidate_hash = str(hit["candidate"]["coefficientSha256"])
            if candidate_hash not in hit_hashes:
                append_checkpoint(HITS, hit)
                hit_hashes.add(candidate_hash)
        print(
            canonical_json(
                {
                    "event": "checkpoint",
                    "routeOrdinal": route["routeOrdinal"],
                    "routeCount": plan["routeCount"],
                    "routeKey": key,
                    "status": row["status"],
                    "realizedPair": row["realizedPair"],
                }
            ),
            flush=True,
        )

    summary = write_summary(plan)
    print(canonical_json(summary), flush=True)
    expected_completed = sum(
        (int(route["routeOrdinal"]) - 1) % args.shard_count == args.shard_index
        for route in plan["routes"]
    )
    return 0 if int(summary["completed"]) == expected_completed else 2


if __name__ == "__main__":
    raise SystemExit(main())

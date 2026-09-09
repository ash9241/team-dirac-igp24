#!/usr/bin/env python3
"""Run the exhaustive coefficient-distinct alternate-source F6 pilot."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CAMPAIGN = DATA / "campaign_20260727_f627"
CENSUS = CAMPAIGN / "f6_post22_pair_revival_census.jsonl"
UNIQUE_PLAN = CAMPAIGN / "f6_post22_unique_wave_plan.json"
PLAN = CAMPAIGN / "f6_post22_alternate_source_pilot_plan.json"
RESULTS = CAMPAIGN / "f6_post22_alternate_source_pilot_results.jsonl"
HITS = CAMPAIGN / "f6_post22_alternate_source_pilot_exact_hits.jsonl"
SUMMARY = CAMPAIGN / "f6_post22_alternate_source_pilot_summary.json"
WORKER = ROOT / "pair_sum_one.sage.py"


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_new(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_replace(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_checkpoint(path: Path, row: dict) -> None:
    with path.open("ab") as handle:
        handle.write((canonical_json(row) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())


def build_plan() -> dict:
    census = read_jsonl(CENSUS)
    unique_plan = json.loads(UNIQUE_PLAN.read_text(encoding="utf-8"))
    executed_hashes = {
        str(row["sourceCoefficientSha256"]) for row in unique_plan["routes"]
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        current_gold = defaultdict(set)
        generated_at = set()
        for row in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        ):
            label = str(row["label"])
            r = int(row["r"])
            generated_at.add(str(row["generated_at"]))
            if (
                int(row["team_count"]) == 0
                and int(row["discovered"]) == 0
                and (label, r) not in baseline
                and (label, r) not in owned
            ):
                current_gold[label].add(r)
        tasks = []
        for census_row in census:
            if int(census_row["length24OrbitCount"]) != 1:
                continue
            target = census_row["targets"][0]
            orbit_index = int(target["orbitIndex"])
            target_label = str(target["targetLabel"])
            for source_r in sorted(
                {int(row["sourceR"]) for row in census_row["profiles"]}
            ):
                profiles = []
                for profile in census_row["profiles"]:
                    if int(profile["sourceR"]) != source_r:
                        continue
                    mapped = next(
                        (
                            item
                            for item in profile["targets"]
                            if int(item["orbitIndex"]) == orbit_index
                        ),
                        None,
                    )
                    if mapped is not None:
                        profiles.append(
                            {
                                "classIndex": int(profile["classIndex"]),
                                "classSize": int(profile["classSize"]),
                                "targetR": int(mapped["targetR"]),
                            }
                        )
                compatible = [
                    row
                    for row in profiles
                    if row["targetR"] in current_gold.get(target_label, set())
                ]
                if not compatible:
                    continue
                sources = connection.execute(
                    """
                    SELECT p.submission_id,p.polynomial_index,p.coefficient_hash,
                           length(p.original_line) AS coefficient_bytes
                    FROM polynomials p
                    JOIN verifications v USING(submission_id,polynomial_index)
                    WHERE v.label=? AND v.r=? AND v.status='accepted' AND v.scoreable=1
                    ORDER BY coefficient_bytes,p.coefficient_hash,
                             p.submission_id,p.polynomial_index
                    """,
                    (census_row["sourceLabel"], source_r),
                ).fetchall()
                seen = set()
                for source in sources:
                    digest = str(source["coefficient_hash"])
                    if digest in seen or digest in executed_hashes:
                        continue
                    seen.add(digest)
                    tasks.append(
                        {
                            "routeKey": (
                                f"{census_row['sourceLabel']}|r{source_r}|"
                                f"oi{orbit_index}|{target_label}"
                            ),
                            "sourceLabel": str(census_row["sourceLabel"]),
                            "sourceR": source_r,
                            "sourceSubmissionId": str(source["submission_id"]),
                            "sourcePolynomialIndex": int(
                                source["polynomial_index"]
                            ),
                            "sourceCoefficientSha256": digest,
                            "sourceCoefficientBytes": int(
                                source["coefficient_bytes"]
                            ),
                            "targetLabel": target_label,
                            "targetT": int(target["targetT"]),
                            "orbitIndex": orbit_index,
                            "currentGoldR": sorted(
                                current_gold.get(target_label, set())
                            ),
                            "compatibleClasses": compatible,
                            "compatibleClassWeight": sum(
                                row["classSize"] for row in compatible
                            ),
                            "totalClassWeight": sum(
                                row["classSize"] for row in profiles
                            ),
                        }
                    )
    finally:
        connection.close()

    if len(tasks) != 9:
        raise ValueError(f"alternate-source ceiling changed: {len(tasks)} != 9")
    if len({task["sourceCoefficientSha256"] for task in tasks}) != len(tasks):
        raise ValueError("alternate-source pool contains duplicate coefficients")
    if any(task["sourceCoefficientSha256"] in executed_hashes for task in tasks):
        raise ValueError("alternate-source pool overlaps the completed 53")

    def quality(task):
        return (
            -task["compatibleClassWeight"] / task["totalClassWeight"],
            task["sourceCoefficientBytes"],
            task["sourceLabel"],
            task["sourceR"],
            task["sourceCoefficientSha256"],
        )

    ordered_quality = sorted(tasks, key=quality)
    ordered = []
    used_labels = set()
    for task in ordered_quality:
        if task["sourceLabel"] not in used_labels:
            ordered.append(task)
            used_labels.add(task["sourceLabel"])
    used_routes = {task["routeKey"] for task in ordered}
    for task in ordered_quality:
        if task["routeKey"] not in used_routes:
            ordered.append(task)
            used_routes.add(task["routeKey"])
    ordered.extend(task for task in ordered_quality if task not in ordered)
    for ordinal, task in enumerate(ordered, 1):
        task["ordinal"] = ordinal

    plan = {
        "schemaVersion": "f6-post22-alternate-source-pilot-plan-v1",
        "requestedPilotSize": 50,
        "exhaustiveAvailableSize": len(ordered),
        "ceilingReason": (
            "only nine coefficient-distinct accepted source fields remain on "
            "the exact 53 unique-orbit label/r routes after excluding every "
            "completed source coefficient hash"
        ),
        "distinctSourceLabels": len({task["sourceLabel"] for task in ordered}),
        "distinctRouteKeys": len({task["routeKey"] for task in ordered}),
        "distinctTargetLabels": len({task["targetLabel"] for task in ordered}),
        "executedSourceHashesExcluded": len(executed_hashes),
        "recursiveShardLabelsExcluded": ["24T16948", "24T16949"],
        "ordering": (
            "distinct source labels, then distinct route keys, then weighted "
            "compatible-class hit fraction and source coefficient bytes"
        ),
        "currentEligibleDefinition": (
            "team_count=0 AND discovered=0 AND nonbaseline AND unowned"
        ),
        "targetGeneratedAtMax": max(generated_at) if generated_at else None,
        "censusPath": str(CENSUS.relative_to(ROOT)),
        "censusSha256": sha256_file(CENSUS),
        "completedPlanPath": str(UNIQUE_PLAN.relative_to(ROOT)),
        "completedPlanSha256": sha256_file(UNIQUE_PLAN),
        "transforms": [1, 2, 3],
        "reduction": "best",
        "nfdisc": True,
        "timeoutSeconds": 90,
        "checkpointEvery": 1,
        "scaleThreshold": 0.05,
        "tasks": ordered,
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    if PLAN.exists():
        if PLAN.read_bytes() != payload:
            raise ValueError("existing alternate-source plan differs")
    else:
        atomic_new(PLAN, payload)
    return plan


def parse_result(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def run_worker(task: dict, timeout: int) -> tuple[dict | None, dict]:
    command = [
        "sage",
        "-python",
        str(WORKER),
        task["sourceSubmissionId"],
        str(task["sourcePolynomialIndex"]),
        "--expected-target",
        task["targetLabel"],
        "--expected-source-hash",
        task["sourceCoefficientSha256"],
        "--orbit-map",
        str(CENSUS),
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
    except subprocess.TimeoutExpired:
        return None, {
            "status": "timeout",
            "timeoutSeconds": timeout,
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    result = parse_result(completed.stdout)
    return result, {
        "status": "completed" if completed.returncode == 0 else "worker_error",
        "returnCode": int(completed.returncode),
        "wallSeconds": round(time.monotonic() - started, 3),
        "stderrSha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "stderrTail": completed.stderr[-1000:],
    }


def live_gate(label: str, r: int, digest: str, line: str) -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        target = connection.execute(
            "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
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
                "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
                (label, r),
            ).fetchone()[0]
        )
        hash_matches = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (digest,),
            ).fetchone()[0]
        )
        line_matches = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficients=?", (line,)
            ).fetchone()[0]
        )
    finally:
        connection.close()
    current = bool(
        target is not None
        and int(target["team_count"]) == 0
        and int(target["discovered"]) == 0
        and baseline == 0
        and owned == 0
        and hash_matches == 0
        and line_matches == 0
    )
    return {
        "teamCount": int(target["team_count"]) if target else None,
        "discovered": int(target["discovered"]) if target else None,
        "generatedAt": str(target["generated_at"]) if target else None,
        "baselineMatches": baseline,
        "ownedScoreableMatches": owned,
        "ledgerCoefficientHashMatches": hash_matches,
        "ledgerCoefficientLineMatches": line_matches,
        "currentTc0AndNew": current,
    }


def main() -> int:
    plan = build_plan()
    if not HITS.exists():
        atomic_new(HITS, b"")
    completed = {str(row["sourceCoefficientSha256"]) for row in read_jsonl(RESULTS)}
    hit_hashes = {
        str(row["candidate"]["coefficientSha256"]) for row in read_jsonl(HITS)
    }
    for task in plan["tasks"]:
        if task["sourceCoefficientSha256"] in completed:
            continue
        result, provenance = run_worker(task, int(plan["timeoutSeconds"]))
        if (
            not isinstance(result, dict)
            or result.get("status") != "certified"
            or provenance["status"] != "completed"
        ):
            row = {
                **task,
                "status": provenance["status"],
                "provenance": provenance,
            }
            append_checkpoint(RESULTS, row)
            print(
                canonical_json(
                    {
                        "event": "checkpoint",
                        "ordinal": task["ordinal"],
                        "total": plan["exhaustiveAvailableSize"],
                        "status": row["status"],
                    }
                ),
                flush=True,
            )
            continue
        if (
            result["sourceCoefficientSha256"]
            != task["sourceCoefficientSha256"]
            or result["targetLabel"] != task["targetLabel"]
        ):
            raise ValueError("worker receipt differs from sealed task")
        line = str(result["coefficientLine"])
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if digest != str(result["coefficientSha256"]):
            raise ValueError("candidate coefficient hash mismatch")
        coefficients = [int(value) for value in line.split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or coefficients[0] == 0
            or math.gcd(*coefficients) != 1
        ):
            raise ValueError("candidate line failed primitive degree-24 checks")
        target_r = int(result["targetR"])
        gate = live_gate(result["targetLabel"], target_r, digest, line)
        exact_hit = (
            target_r in {int(value) for value in task["currentGoldR"]}
            and gate["currentTc0AndNew"]
        )
        row = {
            **task,
            "status": "exact_current_tc0" if exact_hit else "exact_non_tc0",
            "realizedPair": {
                "label": str(result["targetLabel"]),
                "r": target_r,
            },
            "liveGate": gate,
            "candidate": {
                "coefficientSha256": digest,
                "coefficientBytes": int(result["coefficientBytes"]),
                "fieldDiscriminantAbs": str(result["fieldDiscriminantAbs"]),
                "polynomialDiscriminantAbs": str(
                    result["polynomialDiscriminantAbs"]
                ),
                "orbitCertificate": result["orbitCertificate"],
                "attempts": result["attempts"],
                "elapsedSeconds": float(result["elapsedSeconds"]),
            },
            "provenance": provenance,
        }
        append_checkpoint(RESULTS, row)
        if exact_hit and digest not in hit_hashes:
            append_checkpoint(
                HITS,
                {
                    **row,
                    "candidate": result,
                },
            )
            hit_hashes.add(digest)
        print(
            canonical_json(
                {
                    "event": "checkpoint",
                    "ordinal": task["ordinal"],
                    "total": plan["exhaustiveAvailableSize"],
                    "status": row["status"],
                    "realizedPair": row["realizedPair"],
                }
            ),
            flush=True,
        )

    rows = read_jsonl(RESULTS)
    hits = read_jsonl(HITS)
    completed_count = len({str(row["sourceCoefficientSha256"]) for row in rows})
    hit_rate = len(hits) / completed_count if completed_count else 0.0
    summary = {
        "schemaVersion": "f6-post22-alternate-source-pilot-summary-v1",
        "requestedPilotSize": int(plan["requestedPilotSize"]),
        "exhaustiveAvailableSize": int(plan["exhaustiveAvailableSize"]),
        "completed": completed_count,
        "exactCurrentTc0": len(hits),
        "exactCurrentTc0Pairs": sorted(
            {
                (str(row["realizedPair"]["label"]), int(row["realizedPair"]["r"]))
                for row in hits
            }
        ),
        "hitRate": hit_rate,
        "scaleThreshold": float(plan["scaleThreshold"]),
        "thresholdMet": hit_rate >= float(plan["scaleThreshold"]),
        "remainingCoefficientDistinctSources": 0,
        "scaleDecision": (
            "threshold_met_but_source_pool_exhausted"
            if hit_rate >= float(plan["scaleThreshold"])
            else "below_threshold_and_source_pool_exhausted"
        ),
        "statusCounts": {
            status: sum(row["status"] == status for row in rows)
            for status in sorted({str(row["status"]) for row in rows})
        },
        "planPath": str(PLAN.relative_to(ROOT)),
        "planSha256": sha256_file(PLAN),
        "resultsPath": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha256_file(RESULTS),
        "hitsPath": str(HITS.relative_to(ROOT)),
        "hitsSha256": sha256_file(HITS),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    atomic_replace(
        SUMMARY, (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    )
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

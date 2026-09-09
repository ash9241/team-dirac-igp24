#!/usr/bin/env python3
"""Claim and execute one deterministic final30 F6 unique-orbit shard."""

from __future__ import annotations

import concurrent.futures
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
SHARED = DATA / "gpt56ultra_rank11_f6_final30_20260726"
LOCK = SHARED / "claims.lock"
CLAIMS = SHARED / "claims.jsonl"
PLAN = SHARED / "independent_new_family_plan.json"
RESULTS = SHARED / "independent_new_family_results.jsonl"
HITS = SHARED / "independent_new_family_exact_hits.jsonl"
LINES = SHARED / "independent_new_family_exact_lines.txt"
SUMMARY = SHARED / "independent_new_family_summary.json"
WORKER = ROOT / "pair_sum_one.sage.py"
MAXIMUM = 16
TIMEOUT = 45


def read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def atomic_jsonl(path: Path, rows):
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    os.replace(temporary, path)


def catalog_paths():
    rows = []
    pattern = re.compile(r"_v(\d+)$")
    for directory in DATA.glob("autopilot_pair_delta_20260722_v*"):
        match = pattern.search(directory.name)
        if not match:
            continue
        version = int(match.group(1))
        path = directory / "missing_pair_all.jsonl"
        if 3 <= version <= 21 and path.exists():
            rows.append((version, path))
    return [path for _version, path in sorted(rows)]


def catalog():
    exact = {}
    for path in catalog_paths():
        for row in read_jsonl(path):
            if row.get("status") != "certified" or int(row.get("length24OrbitCount", 0)) != 1:
                continue
            targets = row.get("targets") or []
            if len(targets) != 1:
                continue
            key = str(row["sourceLabel"])
            exact[key] = {"catalogPath": str(path.resolve()), "row": row}
    return exact


def claimed_state():
    source_keys, target_pairs = set(), set()
    for path in SHARED.rglob("*.json*"):
        if path.name.endswith(".tmp") or path == PLAN:
            continue
        try:
            objects = read_jsonl(path) if path.suffix == ".jsonl" else [json.loads(path.read_text())]
        except Exception:
            continue
        stack = list(objects)
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, dict):
                sid = value.get("sourceSubmissionId")
                pidx = value.get("sourcePolynomialIndex")
                label = value.get("targetLabel", value.get("label"))
                signature = value.get("targetR", value.get("r"))
                if sid is not None and pidx is not None:
                    source_keys.add((str(sid), int(pidx)))
                if label is not None and signature is not None:
                    target_pairs.add((str(label), int(signature)))
                stack.extend(value.values())
    return source_keys, target_pairs


def ownership(connection, label, r):
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE label=? AND r=?",
            (label, r),
        ).fetchone()[0]
    )


def build_candidates(exact):
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    candidates = []
    for source_label, item in exact.items():
        row = item["row"]
        target = row["targets"][0]
        orbit_index = int(target["orbitIndex"])
        target_label = str(target["targetLabel"])
        target_t = int(target["targetT"])
        possible = {}
        for profile in row.get("profiles") or []:
            source_r = int(profile["sourceR"])
            for mapped in profile.get("targets") or []:
                if int(mapped["orbitIndex"]) == orbit_index:
                    possible.setdefault(source_r, set()).add(int(mapped["targetR"]))
        for source_r, target_rs in possible.items():
            owned_sources = connection.execute(
                """
                SELECT p.submission_id,p.polynomial_index,p.coefficient_hash
                FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
                WHERE v.label=? AND v.r=? AND v.scoreable=1
                ORDER BY p.submission_id,p.polynomial_index
                """,
                (source_label, source_r),
            ).fetchall()
            current = []
            for target_r in sorted(target_rs):
                snapshot = connection.execute(
                    "SELECT team_count,generated_at FROM targets WHERE label=? AND r=?",
                    (target_label, target_r),
                ).fetchone()
                if snapshot is None or int(snapshot[0]) not in (0, 1):
                    continue
                if ownership(connection, target_label, target_r):
                    continue
                current.append(
                    {
                        "targetLabel": target_label,
                        "targetT": target_t,
                        "targetR": target_r,
                        "teamCount": int(snapshot[0]),
                        "generatedAt": snapshot[1],
                    }
                )
            if not current:
                continue
            for source in owned_sources:
                candidates.append(
                    {
                        "catalogPath": item["catalogPath"],
                        "orbitIndex": orbit_index,
                        "possibleCurrentPairs": current,
                        "sourceCoefficientSha256": str(source["coefficient_hash"]),
                        "sourceLabel": source_label,
                        "sourceR": source_r,
                        "sourceSubmissionId": str(source["submission_id"]),
                        "sourcePolynomialIndex": int(source["polynomial_index"]),
                        "targetLabel": target_label,
                        "targetT": target_t,
                    }
                )
    connection.close()
    candidates.sort(
        key=lambda row: (
            min(pair["teamCount"] for pair in row["possibleCurrentPairs"]),
            min(pair["targetR"] for pair in row["possibleCurrentPairs"]),
            row["targetT"],
            row["sourceLabel"],
            row["sourceR"],
            row["sourceCoefficientSha256"],
        )
    )
    return candidates


def claim(candidates):
    SHARED.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source_claims, target_claims = claimed_state()
        existing = read_jsonl(CLAIMS)
        selected, used_targets = [], set(target_claims)
        for candidate in candidates:
            source_key = (
                candidate["sourceSubmissionId"],
                candidate["sourcePolynomialIndex"],
            )
            if source_key in source_claims:
                continue
            available = [
                pair
                for pair in candidate["possibleCurrentPairs"]
                if (pair["targetLabel"], pair["targetR"]) not in used_targets
            ]
            if not available:
                continue
            nominated = sorted(
                available,
                key=lambda pair: (pair["teamCount"], pair["targetR"], pair["targetLabel"]),
            )[0]
            claimed = {
                **candidate,
                "claimOwner": "independent_new_family",
                "claimedAt": time.time(),
                "nominatedPair": nominated,
            }
            selected.append(claimed)
            source_claims.add(source_key)
            used_targets.add((nominated["targetLabel"], nominated["targetR"]))
            if len(selected) >= MAXIMUM:
                break
        atomic_jsonl(CLAIMS, existing + selected)
        fcntl.flock(lock, fcntl.LOCK_UN)
    PLAN.write_text(json.dumps({"selected": selected}, indent=2, sort_keys=True) + "\n")
    return selected


def run_job(job):
    command = [
        "sage",
        "-python",
        str(WORKER),
        job["sourceSubmissionId"],
        str(job["sourcePolynomialIndex"]),
        "--expected-target",
        job["targetLabel"],
        "--orbit-map",
        job["catalogPath"],
        "--transforms",
        "1,2,3",
        "--reduce",
        "best",
        "--nfdisc",
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return {**job, "status": "timeout", "wallSeconds": round(time.monotonic() - started, 3)}
    result = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            result = json.loads(line)
            break
        except json.JSONDecodeError:
            pass
    if completed.returncode or not isinstance(result, dict) or result.get("status") != "certified":
        return {
            **job,
            "status": "worker_error",
            "returncode": completed.returncode,
            "stderrTail": completed.stderr[-1000:],
            "result": result,
            "wallSeconds": round(time.monotonic() - started, 3),
        }
    realized = (str(result["targetLabel"]), int(result["targetR"]))
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    snapshot = connection.execute(
        "SELECT team_count,generated_at FROM targets WHERE label=? AND r=?", realized
    ).fetchone()
    owned = ownership(connection, *realized)
    connection.close()
    fresh = snapshot is not None and int(snapshot[0]) in (0, 1) and not owned
    allowed = {
        (pair["targetLabel"], int(pair["targetR"]))
        for pair in job["possibleCurrentPairs"]
    }
    exact_hit = realized in allowed and fresh
    return {
        **job,
        "candidate": result,
        "candidateSha256": hashlib.sha256(result["coefficientLine"].encode()).hexdigest(),
        "freshGate": {
            "teamCount": int(snapshot[0]) if snapshot else None,
            "generatedAt": snapshot[1] if snapshot else None,
            "owned": owned,
        },
        "realizedPair": {"label": realized[0], "r": realized[1]},
        "status": "exact_hit" if exact_hit else "exact_noncurrent",
        "wallSeconds": round(time.monotonic() - started, 3),
    }


def main():
    for path in (RESULTS, HITS, LINES):
        path.write_text("", encoding="ascii")
    candidates = build_candidates(catalog())
    selected = claim(candidates)
    print(
        json.dumps(
            {
                "event": "claimed",
                "catalogCandidates": len(candidates),
                "claimed": len(selected),
                "remaining": max(0, len(candidates) - len(selected)),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    completed, hits = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run_job, job) for job in selected]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            completed.append(row)
            atomic_jsonl(RESULTS, sorted(completed, key=lambda x: (x["targetT"], x["sourceLabel"])))
            if row["status"] == "exact_hit":
                hits.append(row)
                atomic_jsonl(HITS, hits)
                with LINES.open("a", encoding="ascii") as handle:
                    handle.write(row["candidate"]["coefficientLine"] + "\n")
                print(
                    json.dumps(
                        {
                            "event": "exact_hit",
                            "target": row["realizedPair"]["label"],
                            "r": row["realizedPair"]["r"],
                            "source": row["sourceLabel"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            else:
                print(
                    json.dumps(
                        {
                            "event": "completed",
                            "source": row["sourceLabel"],
                            "status": row["status"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    summary = {
        "catalogCandidates": len(candidates),
        "claimed": len(selected),
        "completed": len(completed),
        "exactHits": len(hits),
        "statusCounts": {
            status: sum(row["status"] == status for row in completed)
            for status in sorted({row["status"] for row in completed})
        },
        "submissionCalls": 0,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if hits else 2


if __name__ == "__main__":
    raise SystemExit(main())

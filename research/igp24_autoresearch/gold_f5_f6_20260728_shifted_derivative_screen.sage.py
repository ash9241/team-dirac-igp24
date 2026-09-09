#!/usr/bin/env sage -python
"""Screen every unrun rank-11 shifted-derivative F5 case.

This is an isolated, read-only continuation of the July-27 derivative-radicand
pilot.  It deliberately excludes the 19 cases already executed there and does
not write to the ledger or make network/submission calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SCAN = DATA / "shifted_derivative_square_ratio_scan_all_routes.json"
PRIOR = DATA / "shifted_derivative_19_arithmetic_results.jsonl"
RESULTS = DATA / "gold_f5_f6_20260728_shifted_derivative_screen.jsonl"
SUMMARY = DATA / "gold_f5_f6_20260728_shifted_derivative_screen_summary.json"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_shifted_derivative_screen.txt"
STAGED_PAIRS = {
    ("24T10482", 8),
    ("24T14293", 16),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T15337", 20),
    ("24T15043", 4),
    ("24T11787", 12),
    ("24T24877", 14),
}

_SOURCE_SPEC = importlib.util.spec_from_file_location(
    "run_shifted_derivative_19",
    ROOT / "run_shifted_derivative_19.sage.py",
)
if _SOURCE_SPEC is None or _SOURCE_SPEC.loader is None:
    raise ImportError("cannot load the preserved shifted-derivative worker")
_SOURCE_MODULE = importlib.util.module_from_spec(_SOURCE_SPEC)
_SOURCE_SPEC.loader.exec_module(_SOURCE_MODULE)
transform = _SOURCE_MODULE.transform


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_cases() -> list[dict]:
    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    already_run = {
        (str(row["quotient"]["coefficientSha256"]), int(row["a"]))
        for row in load_jsonl(PRIOR)
    }
    cases = {}
    for hit in scan["hits"]:
        quotient = hit["source"]
        key = (str(quotient["coefficientSha256"]), int(hit["a"]))
        if key in already_run:
            continue
        rank_eleven_rows = [
            row
            for row in quotient["rows"]
            if int(row["action"]["sourceBlockKernelOrder"]) == 2**11
        ]
        if not rank_eleven_rows:
            continue
        saved = cases.setdefault(
            key,
            {
                "a": int(hit["a"]),
                "quotientLine": str(quotient["coefficientLine"]),
                "quotientSha256": key[0],
                "routes": [],
            },
        )
        saved["routes"].extend(rank_eleven_rows)
    ordered = sorted(
        cases.values(),
        key=lambda row: (row["quotientSha256"], row["a"]),
    )
    if len(ordered) != 46 or sum(len(row["routes"]) for row in ordered) != 46:
        raise ValueError("unrun rank-11 derivative frontier changed")
    return ordered


def main() -> int:
    for path in (RESULTS, SUMMARY, MANIFEST):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    db = DATA / "ledger.sqlite3"
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        current_tc0 = {
            (str(label), int(r))
            for label, r in connection.execute(
                """
                SELECT t.label,t.r
                FROM targets AS t
                WHERE t.team_count=0 AND t.discovered=0
                  AND NOT EXISTS(
                    SELECT 1 FROM baseline_pairs AS b
                    WHERE b.label=t.label AND b.r=t.r
                  )
                  AND NOT EXISTS(
                    SELECT 1 FROM verifications AS v
                    WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
                  )
                """
            )
        }
        known_hashes = {
            str(value)
            for (value,) in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
    finally:
        connection.close()

    rows = []
    potential_pairs = set()
    started = time.monotonic()
    for ordinal, case in enumerate(load_cases(), 1):
        result = transform(case["quotientLine"], case["a"])
        predicted = []
        if "candidate" in result:
            candidate = result["candidate"]
            pair_r = int(candidate["r"])
            candidate["knownLedgerCoefficient"] = (
                str(candidate["coefficientSha256"]) in known_hashes
            )
            for route in case["routes"]:
                pair = (str(route["action"]["targetLabel"]), pair_r)
                gate = (
                    pair in current_tc0
                    and pair not in STAGED_PAIRS
                    and not candidate["knownLedgerCoefficient"]
                )
                predicted.append(
                    {
                        "label": pair[0],
                        "r": pair[1],
                        "currentTc0Unstaged": gate,
                    }
                )
                if gate:
                    potential_pairs.add(pair)
        row = {
            "ordinal": ordinal,
            "a": case["a"],
            "quotientSha256": case["quotientSha256"],
            "routes": case["routes"],
            "arithmetic": result,
            "sameActionPredictions": predicted,
            "potentialSameActionHit": any(
                item["currentTc0Unstaged"] for item in predicted
            ),
        }
        rows.append(row)
        RESULTS.write_text(
            "".join(canonical_json(item) + "\n" for item in rows),
            encoding="utf-8",
        )
        print(
            canonical_json(
                {
                    "event": "case",
                    "ordinal": ordinal,
                    "total": 46,
                    "status": result["status"],
                    "candidateR": result.get("candidate", {}).get("r"),
                    "potentialSameActionHit": row["potentialSameActionHit"],
                }
            ),
            flush=True,
        )

    hit_rows = [row for row in rows if row["potentialSameActionHit"]]
    manifest_lines = []
    seen_hashes = set()
    for row in hit_rows:
        candidate = row["arithmetic"]["candidate"]
        digest = str(candidate["coefficientSha256"])
        if digest not in seen_hashes:
            manifest_lines.append(str(candidate["coefficientLine"]))
            seen_hashes.add(digest)
    MANIFEST.write_text(
        "".join(line + "\n" for line in manifest_lines),
        encoding="utf-8",
    )
    status_counts = Counter(row["arithmetic"]["status"] for row in rows)
    summary = {
        "schemaVersion": "gold-f5-f6-20260728-shifted-derivative-screen-v1",
        "caseCount": len(rows),
        "rankElevenSourceGate": True,
        "priorCasesExcluded": 19,
        "potentialSameActionHitCount": len(hit_rows),
        "potentialSameActionPairs": [
            {"label": label, "r": r} for label, r in sorted(potential_pairs)
        ],
        "statusCounts": dict(sorted(status_counts.items())),
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha256_file(RESULTS),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha256_file(MANIFEST),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    SUMMARY.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(canonical_json(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

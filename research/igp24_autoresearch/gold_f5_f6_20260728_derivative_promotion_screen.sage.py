#!/usr/bin/env sage -python
"""Reclassify all saved shifted-derivative cases in their full rank-11 lifts.

The prior July-27 screen compared candidates only with their saved source
actions.  A derivative radicand can raise a lower sign kernel to the full
character kernel.  This isolated screen recovers that character action from
the exact norm core, then applies the corresponding saved F5 pair action.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SCAN = DATA / "shifted_derivative_square_ratio_scan_all_routes.json"
RESULTS = DATA / "gold_f5_f6_20260728_derivative_promotion_screen.jsonl"
SUMMARY = DATA / "gold_f5_f6_20260728_derivative_promotion_screen_summary.json"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_derivative_promotion_screen.txt"
RESERVED_PAIRS = {
    ("24T10482", 8),
    ("24T14293", 16),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T15337", 20),
    ("24T15043", 4),
    ("24T11787", 12),
    ("24T24877", 14),
    ("24T15273", 20),
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DERIVATIVE = load_module(
    "gold_f5_f6_derivative_worker",
    ROOT / "run_shifted_derivative_19.sage.py",
)
CHARACTER = load_module(
    "gold_f5_f6_character_worker",
    ROOT / "character_kernel_gold_pilot.sage.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_actions() -> dict[str, list[dict]]:
    actions = defaultdict(list)
    for path in sorted(
        DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
    ):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if int(row["sourceBlockKernelOrder"]) == 2**11:
                    actions[str(row["sourceLabel"])].append(row)
    return actions


def load_cases() -> list[dict]:
    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    cases = {}
    for hit in scan["hits"]:
        source = hit["source"]
        key = (str(source["coefficientSha256"]), int(hit["a"]))
        saved = cases.setdefault(
            key,
            {
                "a": key[1],
                "quotientLine": str(source["coefficientLine"]),
                "quotientSha256": key[0],
                "savedRows": [],
            },
        )
        saved["savedRows"].extend(source["rows"])
    ordered = sorted(
        cases.values(), key=lambda row: (row["quotientSha256"], row["a"])
    )
    if len(ordered) != 112:
        raise ValueError(f"square-ratio case count changed: {len(ordered)} != 112")
    return ordered


def main() -> int:
    for path in (SUMMARY, MANIFEST):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    connection = sqlite3.connect(
        f"file:{(DATA / 'ledger.sqlite3').resolve()}?mode=ro", uri=True
    )
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

    action_map = load_actions()
    ring_z = PolynomialRing(ZZ, "z")
    classifications = {}
    results = (
        [
            json.loads(raw)
            for raw in RESULTS.read_text(encoding="utf-8").splitlines()
            if raw.strip()
        ]
        if RESULTS.exists()
        else []
    )
    potential_pairs = {
        (str(prediction["label"]), int(prediction["r"]))
        for row in results
        for prediction in row.get("promotedPredictions", [])
        if prediction.get("currentTc0Unreserved")
    }
    started = time.monotonic()
    for ordinal, case in enumerate(load_cases(), 1):
        if ordinal <= len(results):
            continue
        quotient_ts = {
            int(row["action"]["quotientT12"]) for row in case["savedRows"]
        }
        if len(quotient_ts) != 1:
            raise ValueError("one quotient polynomial has inconsistent T12 actions")
        quotient_t = next(iter(quotient_ts))
        try:
            arithmetic = DERIVATIVE.transform(case["quotientLine"], case["a"])
        except ValueError as error:
            row = {
                "ordinal": ordinal,
                "a": case["a"],
                "quotientSha256": case["quotientSha256"],
                "quotientT12": quotient_t,
                "savedRows": case["savedRows"],
                "arithmetic": {
                    "status": "transform_rejected",
                    "reason": str(error),
                },
                "transformedNormCore": None,
                "characterClassification": None,
                "exactFullRankElevenSourceLabel": None,
                "promotedActionCount": 0,
                "promotedPredictions": [],
                "potentialPromotedHit": False,
            }
            results.append(row)
            RESULTS.write_text(
                "".join(canonical_json(value) + "\n" for value in results),
                encoding="utf-8",
            )
            print(
                canonical_json(
                    {
                        "event": "case",
                        "ordinal": ordinal,
                        "total": 112,
                        "status": "transform_rejected",
                        "potentialPromotedHit": False,
                    }
                ),
                flush=True,
            )
            continue
        h = ring_z(
            [
                ZZ(value)
                for value in arithmetic["resultant"]["coefficientLine"].split(",")
            ]
        )
        transformed_core = int(ZZ(h[0]).squarefree_part())
        classification_key = (case["quotientSha256"], quotient_t, transformed_core)
        if classification_key not in classifications:
            alignment = CHARACTER.character_alignment(
                h, quotient_t, probe_cores=[transformed_core]
            )
            possible_labels = alignment["coreToPossibleLabels"].get(
                str(transformed_core), []
            )
            classifications[classification_key] = {
                "alignment": alignment,
                "possibleLabels": possible_labels,
            }
        classification = classifications[classification_key]
        exact_source_label = (
            str(classification["possibleLabels"][0])
            if len(classification["possibleLabels"]) == 1
            else None
        )
        promoted_actions = (
            action_map.get(exact_source_label, [])
            if exact_source_label is not None
            else []
        )
        predictions = []
        if "candidate" in arithmetic:
            candidate = arithmetic["candidate"]
            candidate_r = int(candidate["r"])
            known = str(candidate["coefficientSha256"]) in known_hashes
            for action in promoted_actions:
                pair = (str(action["targetLabel"]), candidate_r)
                live = (
                    pair in current_tc0
                    and pair not in RESERVED_PAIRS
                    and not known
                )
                predictions.append(
                    {
                        "action": action,
                        "currentTc0Unreserved": live,
                        "label": pair[0],
                        "r": pair[1],
                    }
                )
                if live:
                    potential_pairs.add(pair)
        row = {
            "ordinal": ordinal,
            "a": case["a"],
            "quotientSha256": case["quotientSha256"],
            "quotientT12": quotient_t,
            "savedRows": case["savedRows"],
            "arithmetic": arithmetic,
            "transformedNormCore": transformed_core,
            "characterClassification": classification,
            "exactFullRankElevenSourceLabel": exact_source_label,
            "promotedActionCount": len(promoted_actions),
            "promotedPredictions": predictions,
            "potentialPromotedHit": any(
                item["currentTc0Unreserved"] for item in predictions
            ),
        }
        results.append(row)
        RESULTS.write_text(
            "".join(canonical_json(value) + "\n" for value in results),
            encoding="utf-8",
        )
        print(
            canonical_json(
                {
                    "event": "case",
                    "ordinal": ordinal,
                    "total": 112,
                    "exactFullRankElevenSourceLabel": exact_source_label,
                    "candidateR": arithmetic.get("candidate", {}).get("r"),
                    "potentialPromotedHit": row["potentialPromotedHit"],
                }
            ),
            flush=True,
        )

    hit_rows = [row for row in results if row["potentialPromotedHit"]]
    manifest_lines = []
    seen_hashes = set()
    for row in hit_rows:
        candidate = row["arithmetic"]["candidate"]
        digest = str(candidate["coefficientSha256"])
        if digest not in seen_hashes:
            manifest_lines.append(str(candidate["coefficientLine"]))
            seen_hashes.add(digest)
    MANIFEST.write_text(
        "".join(line + "\n" for line in manifest_lines), encoding="utf-8"
    )
    labels = Counter(
        str(row["exactFullRankElevenSourceLabel"])
        for row in results
        if row["exactFullRankElevenSourceLabel"] is not None
    )
    distinct_classifications = {
        (
            str(row["quotientSha256"]),
            int(row["quotientT12"]),
            int(row["transformedNormCore"]),
        )
        for row in results
        if row.get("characterClassification") is not None
    }
    summary = {
        "schemaVersion": "gold-f5-f6-20260728-derivative-promotion-screen-v1",
        "caseCount": len(results),
        "characterClassificationCount": len(distinct_classifications),
        "uniquelyClassifiedCases": sum(
            row["exactFullRankElevenSourceLabel"] is not None for row in results
        ),
        "sourceLabelCounts": dict(sorted(labels.items())),
        "potentialPromotedHitCount": len(hit_rows),
        "potentialPromotedPairs": [
            {"label": label, "r": r} for label, r in sorted(potential_pairs)
        ],
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha_file(RESULTS),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha_file(MANIFEST),
        "reservedPairs": [
            {"label": label, "r": r} for label, r in sorted(RESERVED_PAIRS)
        ],
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

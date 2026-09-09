#!/usr/bin/env sage -python
"""Bounded character-switch derivative pilot on high-value F5 quotients."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import signal
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESULTS = DATA / "gold_f5_f6_20260728_character_switch_derivative_v2.jsonl"
SUMMARY = DATA / "gold_f5_f6_20260728_character_switch_derivative_v2_summary.json"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_character_switch_derivative_v2.txt"
BOUND = 30
MAXIMUM_QUOTIENTS = 100
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
    "gold_f5_f6_switch_derivative_worker",
    ROOT / "run_shifted_derivative_19.sage.py",
)
ALIGNMENT = load_module(
    "gold_f5_f6_switch_alignment_worker",
    ROOT / "agent_gold_c_character_census_worker.sage.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timeout_handler(signum, frame):
    raise TimeoutError("character alignment exceeded 20 seconds")


def main() -> int:
    for path in (RESULTS, SUMMARY, MANIFEST):
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

    actions_by_label = {}
    actions_by_quotient = defaultdict(list)
    for path in sorted(
        DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
    ):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            action = json.loads(raw)
            if int(action["sourceBlockKernelOrder"]) != 2**11:
                continue
            target_label = str(action["targetLabel"])
            if not any(label == target_label for label, _r in current_tc0):
                continue
            source_label = str(action["sourceLabel"])
            actions_by_label[source_label] = action
            actions_by_quotient[int(action["quotientT12"])].append(action)

    quotients = {}
    for path in sorted(
        DATA.glob("agent_f5_full_ledger_pair_product_routes_shard*of4.jsonl")
    ):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            route = json.loads(raw)
            source_label = str(route["action"]["sourceLabel"])
            if source_label not in actions_by_label:
                continue
            source = route["source"]
            digest = str(source["quotientPolynomialSha256"])
            action = actions_by_label[source_label]
            eligible_signatures = sorted(
                r
                for label, r in current_tc0
                if label == str(action["targetLabel"])
            )
            saved = quotients.setdefault(
                digest,
                {
                    "coefficientBits": max(
                        abs(int(value))
                        for value in str(source["quotientLine"]).split(",")
                    ).bit_length(),
                    "quotientLine": str(source["quotientLine"]),
                    "quotientSha256": digest,
                    "quotientT12": int(action["quotientT12"]),
                    "sourceLabels": set(),
                    "reachablePairs": set(),
                },
            )
            saved["sourceLabels"].add(source_label)
            saved["reachablePairs"].update(
                (str(action["targetLabel"]), r) for r in eligible_signatures
            )

    ranked = sorted(
        quotients.values(),
        key=lambda row: (
            -len(row["reachablePairs"]),
            row["coefficientBits"],
            row["quotientSha256"],
        ),
    )[:MAXIMUM_QUOTIENTS]
    for row in ranked:
        row["sourceLabels"] = sorted(row["sourceLabels"])
        row["reachablePairs"] = [
            {"label": label, "r": r}
            for label, r in sorted(row["reachablePairs"])
        ]

    ring = PolynomialRing(ZZ, "u")
    signal.signal(signal.SIGALRM, timeout_handler)
    results = []
    hits = []
    started = time.monotonic()
    for ordinal, row in enumerate(ranked, 1):
        q = ring([ZZ(value) for value in row["quotientLine"].split(",")])
        signal.alarm(20)
        try:
            alignment = ALIGNMENT.exact_linear_character_alignment(
                q, int(row["quotientT12"])
            )
            alignment_status = "exact"
        except (TimeoutError, ValueError) as error:
            alignment = None
            alignment_status = (
                "timeout_20s"
                if isinstance(error, TimeoutError)
                else f"alignment_rejected:{error}"
            )
        finally:
            signal.alarm(0)
        if alignment is None:
            result = {
                **row,
                "ordinal": ordinal,
                "alignmentStatus": alignment_status,
                "shiftHits": [],
            }
            results.append(result)
            RESULTS.write_text(
                "".join(canonical_json(value) + "\n" for value in results),
                encoding="utf-8",
            )
            continue

        desired = []
        for action in actions_by_quotient[int(row["quotientT12"])]:
            source_label = str(action["sourceLabel"])
            for core in alignment["labelToSquarefreeNormCores"].get(
                source_label, []
            ):
                desired.append(
                    {
                        "action": action,
                        "core": int(core),
                        "sourceLabel": source_label,
                    }
                )
        discriminant = ZZ(q.discriminant())
        shift_hits = []
        seen = set()
        for desired_row in desired:
            core = ZZ(desired_row["core"])
            for a in range(-BOUND, BOUND + 1):
                if a == 0:
                    continue
                ratio = QQ(q(a) * discriminant) / QQ(core)
                if ratio <= 0 or not ratio.is_square():
                    continue
                key = (a, desired_row["sourceLabel"], int(core))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    arithmetic = DERIVATIVE.transform(row["quotientLine"], a)
                except ValueError as error:
                    shift_hits.append(
                        {
                            "a": a,
                            "core": int(core),
                            "sourceLabel": desired_row["sourceLabel"],
                            "status": "transform_rejected",
                            "reason": str(error),
                        }
                    )
                    continue
                predictions = []
                if "candidate" in arithmetic:
                    candidate = arithmetic["candidate"]
                    pair = (
                        str(desired_row["action"]["targetLabel"]),
                        int(candidate["r"]),
                    )
                    potential = (
                        pair in current_tc0
                        and pair not in RESERVED_PAIRS
                        and str(candidate["coefficientSha256"]) not in known_hashes
                    )
                    predictions.append(
                        {
                            "label": pair[0],
                            "r": pair[1],
                            "currentTc0Unreserved": potential,
                        }
                    )
                    if potential:
                        hits.append(
                            {
                                "a": a,
                                "arithmetic": arithmetic,
                                "characterAction": desired_row["action"],
                                "characterCore": int(core),
                                "quotient": row,
                                "target": {"label": pair[0], "r": pair[1]},
                            }
                        )
                shift_hits.append(
                    {
                        "a": a,
                        "arithmetic": arithmetic,
                        "core": int(core),
                        "predictions": predictions,
                        "sourceLabel": desired_row["sourceLabel"],
                        "status": arithmetic["status"],
                    }
                )
        result = {
            **row,
            "ordinal": ordinal,
            "alignmentStatus": alignment_status,
            "characterAlignment": alignment,
            "desiredCharacterCount": len(desired),
            "shiftHits": shift_hits,
        }
        results.append(result)
        RESULTS.write_text(
            "".join(canonical_json(value) + "\n" for value in results),
            encoding="utf-8",
        )
        print(
            canonical_json(
                {
                    "event": "quotient",
                    "ordinal": ordinal,
                    "total": len(ranked),
                    "desiredCharacters": len(desired),
                    "shiftHits": len(shift_hits),
                    "potentialHits": len(hits),
                }
            ),
            flush=True,
        )

    distinct_hits = {}
    for hit in hits:
        pair = (str(hit["target"]["label"]), int(hit["target"]["r"]))
        distinct_hits.setdefault(pair, hit)
    manifest_lines = []
    seen_hashes = set()
    for hit in distinct_hits.values():
        candidate = hit["arithmetic"]["candidate"]
        digest = str(candidate["coefficientSha256"])
        if digest not in seen_hashes:
            manifest_lines.append(str(candidate["coefficientLine"]))
            seen_hashes.add(digest)
    MANIFEST.write_text(
        "".join(line + "\n" for line in manifest_lines), encoding="utf-8"
    )
    summary = {
        "schemaVersion": "gold-f5-f6-20260728-character-switch-derivative-v1",
        "bound": BOUND,
        "availableQuotients": len(quotients),
        "selectedQuotients": len(ranked),
        "completedQuotients": len(results),
        "alignmentTimeouts": sum(
            row["alignmentStatus"] != "exact" for row in results
        ),
        "arithmeticShiftHits": sum(len(row["shiftHits"]) for row in results),
        "potentialPairCount": len(distinct_hits),
        "potentialPairs": [
            {"label": label, "r": r} for label, r in sorted(distinct_hits)
        ],
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha_file(RESULTS),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha_file(MANIFEST),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    SUMMARY.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(canonical_json(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

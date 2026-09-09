#!/usr/bin/env python3
"""Build the frozen cumulative audit after lower-Kummer waves 1--8."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
INITIAL_HIT = DATA / "agent_gold_c_lower_kummer_pair_product_hit_9187_r16.json"
OUTPUT = DATA / "agent_gold_c_lower_kummer_pair_product_wave8_cumulative_audit.json"
INITIAL_SOURCE_SHA256 = "9cf7636b762bbd74f3eb35b755f1af6a79be30ff0d289012a548b3ad4760c64f"
INITIAL_PAIR = ("24T9187", 16)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pair_row(pair: tuple[str, int]) -> dict:
    return {"label": pair[0], "r": pair[1]}


def main() -> int:
    if OUTPUT.exists():
        raise ValueError(f"refusing to overwrite {OUTPUT}")
    actions = load_jsonl(ACTIONS)
    routes = load_jsonl(ROUTES)
    action_count = Counter(row["sourceLabel"] for row in actions)
    plans = [DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_plan.json" for wave in range(1, 9)]
    result_paths = [DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_results.jsonl" for wave in range(1, 9)]
    summaries = [DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_summary.json" for wave in range(1, 9)]
    for path in [ACTIONS, ROUTES, INITIAL_HIT, *plans, *result_paths, *summaries]:
        if not path.is_file():
            raise ValueError(f"missing audit input {path}")

    processed_hashes = {INITIAL_SOURCE_SHA256}
    plan_rows = []
    for path in plans:
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan_rows.extend(plan["routes"])
        processed_hashes.update(row["source"]["coefficientSha256"] for row in plan["routes"])
    results = [row for path in result_paths for row in load_jsonl(path)]
    if len(plan_rows) != 64 or len(results) != 64:
        raise ValueError("expected exactly eight complete waves of eight routes")

    staged_pairs = {INITIAL_PAIR}
    staged_pairs.update(
        (row["target"]["label"], int(row["target"]["r"]))
        for row in results
        if row["status"] == "hit_staged"
    )
    if len(staged_pairs) != 12:
        raise ValueError("staged hit pairs are not distinct")

    all_pairs = {
        (row["action"]["targetLabel"], int(row["goldTarget"]["r"])) for row in routes
    }
    source_hashes_by_pair = defaultdict(set)
    source_label_by_hash = {}
    source_details_by_hash = {}
    for row in routes:
        source_hash = row["source"]["coefficientSha256"]
        pair = (row["action"]["targetLabel"], int(row["goldTarget"]["r"]))
        source_hashes_by_pair[pair].add(source_hash)
        source_label_by_hash[source_hash] = row["source"]["label"]
        source_details_by_hash[source_hash] = {
            "coefficientSha256": source_hash,
            "label": row["source"]["label"],
            "polynomialIndex": int(row["source"]["polynomialIndex"]),
            "quotientT12": int(row["action"]["quotientT12"]),
            "sourceR": int(row["source"]["r"]),
            "submissionId": row["source"]["submissionId"],
        }

    pair_status_rows = []
    remaining_hashes = set()
    for pair in sorted(all_pairs, key=lambda value: (int(value[0][3:]), value[1])):
        sources = source_hashes_by_pair[pair]
        tested = sources & processed_hashes
        remaining = sources - processed_hashes
        if pair in staged_pairs:
            status = "staged"
        elif not tested:
            status = "untested"
        elif not remaining:
            status = "exhausted_no_hit"
        else:
            status = "attempted_with_remaining"
        if status != "staged":
            remaining_hashes.update(remaining)
        pair_status_rows.append(
            {
                **pair_row(pair),
                "remainingSourcePolynomials": len(remaining),
                "status": status,
                "testedSourcePolynomials": len(tested),
                "totalSourcePolynomials": len(sources),
            }
        )

    remaining_by_label = defaultdict(lambda: {"pairs": set(), "sourceHashes": set()})
    for row in pair_status_rows:
        if row["status"] == "staged":
            continue
        pair = (row["label"], row["r"])
        for source_hash in source_hashes_by_pair[pair] - processed_hashes:
            source_label = source_label_by_hash[source_hash]
            remaining_by_label[source_label]["pairs"].add(pair)
            remaining_by_label[source_label]["sourceHashes"].add(source_hash)
    remaining_sources = []
    for label in sorted(remaining_by_label, key=lambda value: int(value[3:])):
        entry = remaining_by_label[label]
        hashes = sorted(entry["sourceHashes"])
        remaining_sources.append(
            {
                "actionOrbitCount": int(action_count[label]),
                "label": label,
                "livePairs": [pair_row(pair) for pair in sorted(entry["pairs"])],
                "orbitAssignmentRequired": int(action_count[label]) > 1,
                "sourcePolynomials": [source_details_by_hash[value] for value in hashes],
            }
        )

    status_histogram = Counter(row["status"] for row in results)
    pair_status_histogram = Counter(row["status"] for row in pair_status_rows)
    wave_yields = [int(json.loads(path.read_text(encoding="utf-8"))["exactHits"]) for path in summaries]
    initial_hash = json.loads(INITIAL_HIT.read_text(encoding="utf-8"))["candidate"]["coefficientSha256"]
    excluded_known = [
        row
        for row in results
        if (row["target"]["label"], int(row["target"]["r"])) == INITIAL_PAIR
        and row["candidate"]["coefficientSha256"] == initial_hash
    ]

    input_paths = [ACTIONS, ROUTES, INITIAL_HIT, *plans, *result_paths, *summaries]
    audit = {
        "arithmetic": {
            "formalWaveResolvedRoutes": len(results),
            "initialCertifiedHitOutsideWaves": 1,
            "statusHistogram": dict(sorted(status_histogram.items())),
            "waveExactHitYields": wave_yields,
            "waveExactHits": sum(wave_yields),
        },
        "duplicateGate": {
            "claimedPairRouteResolutions": int(status_histogram["resolved_pair_claimed"]),
            "excludedInitialPairWithInitialKnownHash": len(excluded_known),
            "knownCoefficientRouteResolutions": int(status_histogram["resolved_known_coefficient"]),
        },
        "inputSha256": {
            str(path.relative_to(ROOT)): sha256_path(path) for path in input_paths
        },
        "mechanism": "exact-conjugate-pair-product-unique-orbit-v1",
        "networkCalls": 0,
        "pairCoverage": {
            "allStructuralLivePairs": len(all_pairs),
            "pairStatusHistogram": dict(sorted(pair_status_histogram.items())),
            "rows": pair_status_rows,
            "stagedDistinctPairs": len(staged_pairs),
            "stagedPairs": [pair_row(pair) for pair in sorted(staged_pairs)],
        },
        "remainingSources": {
            "distinctLabels": len(remaining_by_label),
            "multiOrbitLabels": sum(row["orbitAssignmentRequired"] for row in remaining_sources),
            "multiOrbitSourcePolynomials": sum(
                len(row["sourcePolynomials"]) for row in remaining_sources if row["orbitAssignmentRequired"]
            ),
            "rows": remaining_sources,
            "sourcePolynomials": len(remaining_hashes),
            "uniqueOrbitLabels": sum(not row["orbitAssignmentRequired"] for row in remaining_sources),
            "uniqueOrbitSourcePolynomials": sum(
                len(row["sourcePolynomials"]) for row in remaining_sources if not row["orbitAssignmentRequired"]
            ),
        },
        "submissionCalls": 0,
        "yieldGate": {
            "continuePairProduct": True,
            "higherSubsetNow": False,
            "lastFourWaveYield": sum(wave_yields[-4:]) / 32,
            "nextStep": (
                "Run one more source-diverse pair-product wave after adding exact "
                "multi-orbit factor assignment; four structural pairs have never been "
                "tested and five remaining source polynomials require that assignment."
            ),
            "overallWaveYield": sum(wave_yields) / len(results),
            "pivotCondition": (
                "Move to the higher-subset mechanism only if the next complete wave "
                "adds no distinct hit after the multi-orbit routes are exactly resolved."
            ),
            "reason": (
                "Waves 1-8 yielded 11 distinct hits, including five in the last four "
                "waves; only the latest wave was dry, so the pair-product mechanism "
                "has not met a stall gate."
            ),
        },
    }
    rendered = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "stagedDistinctPairs": len(staged_pairs),
                "waveResolvedRoutes": len(results),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

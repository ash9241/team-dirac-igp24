#!/usr/bin/env python3
"""Freeze the residual ceiling after the final lower-Kummer pair-product pass."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
WAVE_AUDIT = DATA / "agent_gold_c_lower_kummer_pair_product_wave8_cumulative_audit.json"
FINAL_PLAN = DATA / "agent_gold_c_lower_kummer_pair_product_final_plan.json"
FINAL_RESULTS = DATA / "agent_gold_c_lower_kummer_pair_product_final_results.jsonl"
FINAL_SUMMARY = DATA / "agent_gold_c_lower_kummer_pair_product_final_summary.json"
OUTPUT = DATA / "agent_gold_c_lower_kummer_pair_product_final_ceiling_audit.json"
INITIAL_SOURCE_SHA256 = "9cf7636b762bbd74f3eb35b755f1af6a79be30ff0d289012a548b3ad4760c64f"


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
    wave_plans = [DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_plan.json" for wave in range(1, 9)]
    inputs = [ROUTES, WAVE_AUDIT, FINAL_PLAN, FINAL_RESULTS, FINAL_SUMMARY, *wave_plans]
    for path in inputs:
        if not path.is_file():
            raise ValueError(f"missing ceiling input {path}")

    routes = load_jsonl(ROUTES)
    wave_audit = json.loads(WAVE_AUDIT.read_text(encoding="utf-8"))
    final_plan = json.loads(FINAL_PLAN.read_text(encoding="utf-8"))
    final_results = load_jsonl(FINAL_RESULTS)
    final_summary = json.loads(FINAL_SUMMARY.read_text(encoding="utf-8"))
    if len(final_plan["sources"]) != 14 or len(final_results) != 14:
        raise ValueError("final pass did not resolve all fourteen residual sources")
    if int(final_summary["candidatePolynomials"]) != 22 or int(final_summary["exactHits"]) != 2:
        raise ValueError("unexpected final candidate/hit count")

    processed_hashes = {INITIAL_SOURCE_SHA256}
    for path in wave_plans:
        plan = json.loads(path.read_text(encoding="utf-8"))
        processed_hashes.update(row["source"]["coefficientSha256"] for row in plan["routes"])
    processed_hashes.update(
        row["source"]["coefficientSha256"] for row in final_plan["sources"]
    )

    staged_pairs = {
        (row["label"], int(row["r"]))
        for row in wave_audit["pairCoverage"]["stagedPairs"]
    }
    staged_pairs.update(
        (row["target"]["label"], int(row["target"]["r"]))
        for row in final_summary["staged"]
    )
    source_hashes_by_pair = defaultdict(set)
    all_source_hashes = set()
    for row in routes:
        pair = (row["action"]["targetLabel"], int(row["goldTarget"]["r"]))
        source_hash = row["source"]["coefficientSha256"]
        source_hashes_by_pair[pair].add(source_hash)
        all_source_hashes.add(source_hash)

    pair_rows = []
    residual_unhit_sources = set()
    for pair in sorted(source_hashes_by_pair, key=lambda value: (int(value[0][3:]), value[1])):
        remaining = source_hashes_by_pair[pair] - processed_hashes
        status = "staged" if pair in staged_pairs else "exhausted_no_hit"
        if status != "staged":
            residual_unhit_sources.update(remaining)
        pair_rows.append(
            {
                **pair_row(pair),
                "remainingSourcePolynomials": len(remaining),
                "status": status,
                "testedSourcePolynomials": len(source_hashes_by_pair[pair] & processed_hashes),
                "totalSourcePolynomials": len(source_hashes_by_pair[pair]),
            }
        )
    if residual_unhit_sources:
        raise ValueError("an unstaged structural pair still has an untested source")
    if len(staged_pairs) != 14:
        raise ValueError("expected fourteen distinct staged structural pairs")

    previously_untested = {
        ("24T3463", 16),
        ("24T14142", 8),
        ("24T14142", 16),
        ("24T17365", 24),
    }
    outcomes = []
    status_by_pair = {(row["label"], row["r"]): row["status"] for row in pair_rows}
    for pair in sorted(previously_untested, key=lambda value: (int(value[0][3:]), value[1])):
        outcomes.append({**pair_row(pair), "finalStatus": status_by_pair[pair]})

    unprocessed_overall = all_source_hashes - processed_hashes
    audit = {
        "finalPass": {
            "candidatePolynomials": int(final_summary["candidatePolynomials"]),
            "exactHits": int(final_summary["exactHits"]),
            "resolvedSourcePolynomials": int(final_summary["resolvedSourcePolynomials"]),
            "statusHistogram": final_summary["statusHistogram"],
        },
        "inputSha256": {str(path.relative_to(ROOT)): sha256_path(path) for path in inputs},
        "multiOrbitAssignment": {
            "distinctTargetLabelSourcePolynomialsResolvedByJointModularProfiles": 3,
            "sameTargetLabelSourcePolynomialsResolvedByExactActionMultiset": 2,
            "unresolvedAssignments": 0,
        },
        "networkCalls": 0,
        "previouslyUntestedPairOutcomes": outcomes,
        "residualCeiling": {
            "allStructuralLivePairs": len(source_hashes_by_pair),
            "exhaustedNoHitPairs": sum(row["status"] == "exhausted_no_hit" for row in pair_rows),
            "pairRows": pair_rows,
            "remainingExecutableSourcesForUnhitPairs": 0,
            "stagedDistinctPairs": len(staged_pairs),
            "stagedPairs": [pair_row(pair) for pair in sorted(staged_pairs)],
            "unprocessedSourcePolynomialsOverall": len(unprocessed_overall),
            "unprocessedSourcesOnlyServeAlreadyStagedPairs": True,
        },
        "submissionCalls": 0,
        "yieldDecision": {
            "blockBecauseFinalWaveZero": False,
            "finalWaveAddedDistinctHits": 2,
            "historicalPairProductCeilingReached": True,
            "nextAction": (
                "Pause at the exact historical k=2 ceiling. There are no residual "
                "executable sources for an unhit pair; any continuation must use a "
                "different mechanism such as a higher-subset construction."
            ),
        },
    }
    rendered = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "exhaustedNoHitPairs": audit["residualCeiling"]["exhaustedNoHitPairs"],
                "output": str(OUTPUT),
                "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "stagedDistinctPairs": len(staged_pairs),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

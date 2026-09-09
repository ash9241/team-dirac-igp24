#!/usr/bin/env python3
"""Census lower-rank Kummer routes from the frozen score700 wave.

This first pass is purely structural: it joins exact accepted even q(x^2)
rows to the exhaustive two-block action map and the frozen 23,018 gold list.
Rows with a unique block-action profile are already exact; ambiguous rows are
left for polynomial-specific Frobenius quotient resolution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_GOLD = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_OUTPUT = DATA / "agent_gold_c_lower_kummer_historical_census.jsonl"
DEFAULT_ROUTES = DATA / "agent_gold_c_lower_kummer_live_routes.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_c_lower_kummer_census_summary.json"
SCORE700_SUBMISSION = "sub_43f61dfb464e4f41829f5c417a1a3df5"


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_even(coefficients: str) -> bool:
    values = coefficients.split(",")
    return (
        len(values) == 25
        and values[-1] == "1"
        and all(int(values[index]) == 0 for index in range(1, 25, 2))
    )


def power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--submission-id", default=SCORE700_SUBMISSION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    for path in (args.output, args.routes, args.summary):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")

    action_rows = load_jsonl(args.action_map)
    action_by_label = {str(row["sourceLabel"]): row for row in action_rows}
    gold_rows = load_jsonl(args.gold)
    gold_by_pair = {
        (str(row["label"]), int(row["r"])): row for row in gold_rows
    }

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        ledger_rows = list(
            connection.execute(
                """
                SELECT p.polynomial_index,p.coefficients,p.coefficient_hash,
                       v.label,v.t,v.r,v.field_disc_abs,v.status,v.scoreable
                FROM polynomials AS p JOIN verifications AS v
                USING(submission_id,polynomial_index)
                WHERE p.submission_id=? ORDER BY p.polynomial_index
                """,
                (args.submission_id,),
            )
        )
    finally:
        connection.close()

    census = []
    route_rows = []
    for row in ledger_rows:
        if str(row[7]) != "accepted" or int(row[8]) != 1 or not is_even(str(row[1])):
            continue
        label = str(row[3])
        action = action_by_label.get(label)
        profiles = {}
        if action is not None:
            for system in action["systems"]:
                block_order = int(system["blockActionOrder"])
                source_order = int(action["sourceOrder"])
                target_order = int(system["targetOrder"])
                if source_order % block_order or target_order % block_order:
                    continue
                source_kernel = source_order // block_order
                target_kernel = target_order // block_order
                if not power_of_two(source_kernel) or not power_of_two(target_kernel):
                    continue
                key = (
                    int(system["blockActionT12"]),
                    source_kernel,
                    bool(system["flipInSource"]),
                    str(system["targetLabel"]),
                    target_kernel,
                )
                profiles[key] = {
                    "blockActionOrder": block_order,
                    "flipInSource": key[2],
                    "quotientT12": key[0],
                    "sourceKernelOrder": source_kernel,
                    "sourceKummerRank": source_kernel.bit_length() - 1,
                    "targetKernelOrder": target_kernel,
                    "targetKummerRank": target_kernel.bit_length() - 1,
                    "targetLabel": key[3],
                }
        candidate_profiles = [profiles[key] for key in sorted(profiles)]
        exact_profile = candidate_profiles[0] if len(candidate_profiles) == 1 else None
        source = {
            "coefficientSha256": str(row[2]),
            "fieldDiscAbs": str(row[6]) if row[6] else None,
            "label": label,
            "polynomialIndex": int(row[0]),
            "r": int(row[5]),
            "submissionId": args.submission_id,
            "t": int(row[4]),
        }
        quotient_line = ",".join(str(row[1]).split(",")[::2])
        item = {
            "candidateBlockProfiles": candidate_profiles,
            "candidateKernelOrders": sorted(
                {profile["sourceKernelOrder"] for profile in candidate_profiles}
            ),
            "candidateQuotientTs": sorted(
                {profile["quotientT12"] for profile in candidate_profiles}
            ),
            "exactStructuralProfile": exact_profile,
            "quotientLine": quotient_line,
            "quotientPolynomialSha256": hashlib.sha256(quotient_line.encode()).hexdigest(),
            "source": source,
            "status": (
                "unique_exact_block_profile"
                if exact_profile is not None
                else "needs_polynomial_specific_block_resolution"
            ),
        }
        census.append(item)
        for profile in candidate_profiles:
            pair = (profile["targetLabel"], int(row[5]))
            gold = gold_by_pair.get(pair)
            if (
                profile["flipInSource"]
                or profile["targetKernelOrder"] not in {4, 8, 16, 32, 64, 128, 256, 512, 1024}
                or gold is None
            ):
                continue
            route_rows.append(
                {
                    "exactFromStructureAlone": exact_profile == profile,
                    "goldTarget": gold,
                    "mechanism": (
                        "multiply the quotient generator by a fresh positive "
                        "rational prime; its unramified quadratic squareclass "
                        "adjoins the missing global block flip and raises the "
                        "conjugate squareclass-span rank by exactly one"
                    ),
                    "profile": profile,
                    "source": source,
                }
            )

    census_sha = write_jsonl(args.output, census)
    route_sha = write_jsonl(args.routes, route_rows)
    exact_routes = [row for row in route_rows if row["exactFromStructureAlone"]]
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256_path(args.action_map),
        "candidateLiveRoutes": len(route_rows),
        "census": str(args.output.resolve()),
        "censusSha256": census_sha,
        "exactStructuralLiveRoutes": len(exact_routes),
        "exactStructuralRouteKernelHistogram": dict(
            sorted(Counter(row["profile"]["targetKernelOrder"] for row in exact_routes).items())
        ),
        "exactStructuralRouteQuotientTs": sorted(
            {row["profile"]["quotientT12"] for row in exact_routes}
        ),
        "frozenGold": str(args.gold.resolve()),
        "frozenGoldPairs": len(gold_rows),
        "frozenGoldSha256": sha256_path(args.gold),
        "historicalEvenRows": len(census),
        "historicalExactStructuralProfiles": sum(
            row["exactStructuralProfile"] is not None for row in census
        ),
        "historicalKernelCandidateHistogram": dict(
            sorted(
                Counter(
                    kernel
                    for row in census
                    for kernel in row["candidateKernelOrders"]
                ).items()
            )
        ),
        "networkCalls": 0,
        "routes": str(args.routes.resolve()),
        "routesSha256": route_sha,
        "score700SubmissionId": args.submission_id,
        "submissionCalls": 0,
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    temporary = args.summary.with_suffix(args.summary.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build an evidence-backed, submission-free live IGP24 hill-climb plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DEFAULT_DB = ROOT / "data/ledger.sqlite3"
DEFAULT_CENSUS = PROJECT / "cloud/output/full_block_census.jsonl"
DEFAULT_ACTIONS = ROOT / "data/agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_BOARD = ROOT / "data/live_leaderboard_snapshot_20260806.json"
DEFAULT_RANK11 = ROOT / "data/current_rank11_alex_unique_20260806.jsonl"
DEFAULT_RANK10 = ROOT / "data/current_rank10_t00171_unique_20260806.jsonl"
DEFAULT_RANK12 = ROOT / "data/current_rank12_t00087_unique_20260806.jsonl"
DEFAULT_RANK14 = ROOT / "data/current_rank14_t00134_unique_20260806.jsonl"
DEFAULT_Q214 = ROOT / "data/current_q214_separating_resolvent_frontier_20260806.summary.json"
DEFAULT_JSON = ROOT / "data/live_hillclimb_plan_20260806.json"
DEFAULT_MD = ROOT / "LIVE_HILLCLIMB_PLAN_20260806.md"
PROVEN_SQUARECLASS_Q = {28, 80, 81, 153, 155, 158, 185, 195, 260}


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def opponent_summary(rows: list[dict], census: dict[int, dict]) -> dict:
    q6 = Counter()
    shapes = Counter()
    orders = Counter()
    solvable = Counter()
    labels = set()
    for row in rows:
        group = census[int(row["t"])]
        labels.add(str(row["label"]))
        shapes[tuple(group.get("block_sizes") or [])] += 1
        orders[str(group.get("order"))] += 1
        solvable[str(bool(group.get("solvable"))).lower()] += 1
        for quotient in group.get("block_quotients") or []:
            if int(quotient["quotient_degree"]) == 6:
                q6[int(quotient["quotient_t"])] += 1
    return {
        "uniquePairs": len(rows),
        "uniqueGroups": len(labels),
        "solvableDistribution": dict(solvable),
        "topBlockShapes": [
            {"blockSizes": list(shape), "pairs": count}
            for shape, count in shapes.most_common(12)
        ],
        "topDegree6Quotients": [
            {"quotient": f"6T{q}", "pairs": count}
            for q, count in q6.most_common(16)
        ],
        "topGroupOrders": [
            {"order": order, "pairs": count}
            for order, count in orders.most_common(12)
        ],
    }


def even_source_capacity(connection: sqlite3.Connection, labels: set[str]) -> dict:
    if not labels:
        return {"acceptedRows": 0, "evenPresentations": 0, "uniqueQuotientPresentations": 0}
    accepted = 0
    even = 0
    quotients = set()
    labels_list = sorted(labels)
    for start in range(0, len(labels_list), 800):
        chunk = labels_list[start:start + 800]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT p.coefficients FROM verifications v "
            "JOIN polynomials p USING(submission_id,polynomial_index) "
            f"WHERE v.status='accepted' AND v.label IN ({placeholders})"
        )
        for (line,) in connection.execute(query, chunk):
            accepted += 1
            try:
                values = [int(value) for value in str(line).split(",")]
            except ValueError:
                continue
            if len(values) != 25 or any(values[index] != 0 for index in range(1, 24, 2)):
                continue
            even += 1
            quotient = ",".join(str(values[index]) for index in range(0, 25, 2))
            quotients.add(hashlib.sha256(quotient.encode("ascii")).hexdigest())
    return {
        "acceptedRows": accepted,
        "evenPresentations": even,
        "uniqueQuotientPresentations": len(quotients),
        "note": "presentation count is an upper bound, not a canonical field-isomorphism count",
    }


def build_plan(args: argparse.Namespace) -> dict:
    board = json.loads(args.board.read_text(encoding="utf-8"))
    rank11_rows = read_jsonl(args.rank11)
    rank10_rows = read_jsonl(args.rank10)
    rank12_rows = read_jsonl(args.rank12)
    rank14_rows = read_jsonl(args.rank14)
    census_rows = read_jsonl(args.census)
    census = {int(row["t"]): row for row in census_rows}
    action_rows = read_jsonl(args.actions)
    q_to_labels: dict[int, set[str]] = defaultdict(set)
    label_to_q: dict[str, set[int]] = defaultdict(set)
    for row in action_rows:
        label = str(row["sourceLabel"])
        for system in row.get("systems") or []:
            q = int(system["blockActionT12"])
            q_to_labels[q].add(label)
            label_to_q[label].add(q)

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        target_generated_at = sorted({str(row[0]) for row in connection.execute(
            "SELECT DISTINCT generated_at FROM targets"
        )})
        target_counts = {
            int(k): int(v) for k, v in connection.execute(
                "SELECT team_count,COUNT(*) FROM targets GROUP BY team_count"
            )
        }
        owned_pairs = {
            (str(label), int(r)) for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE status='accepted' AND scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
            )
        }
        owned_groups = {label for label, _r in owned_pairs}
        gold_siblings = []
        for row in connection.execute(
            "SELECT label,t,r,team_count FROM targets WHERE team_count=0 AND discovered=0"
        ):
            label, t, r, team_count = str(row[0]), int(row[1]), int(row[2]), int(row[3])
            if label in owned_groups and (label, r) not in owned_pairs:
                gold_siblings.append({"label": label, "t": t, "r": r, "teamCount": team_count})
        q_gold = Counter()
        q_groups: dict[int, set[str]] = defaultdict(set)
        unmapped_gold_siblings = 0
        for row in gold_siblings:
            qs = label_to_q.get(row["label"], set())
            if not qs:
                unmapped_gold_siblings += 1
            for q in qs:
                q_gold[q] += 1
                q_groups[q].add(row["label"])
        raid_segments = {
            "rank10": rank10_rows,
            "rank11": rank11_rows,
            "rank12": rank12_rows,
            "rank14": rank14_rows,
        }
        raid_q_counts: dict[int, Counter] = defaultdict(Counter)
        raid_intersections = {}
        for segment, rows in raid_segments.items():
            owned_group_rows = 0
            mapped_rows = 0
            for row in rows:
                label, r_value = str(row["label"]), int(row["r"])
                if label not in owned_groups or (label, r_value) in owned_pairs:
                    continue
                owned_group_rows += 1
                qs = label_to_q.get(label, set())
                if qs:
                    mapped_rows += 1
                for q in qs:
                    raid_q_counts[q][segment] += 1
            raid_intersections[segment] = {
                "pairsOnGroupsDiracAlreadyReached": owned_group_rows,
                "mappedToExact12x2Action": mapped_rows,
            }
        selected_q = {q for q, _count in q_gold.most_common(16)} | {
            q for q in PROVEN_SQUARECLASS_Q if q_gold[q] or sum(raid_q_counts[q].values())
        } | {
            q for q, counts in raid_q_counts.items() if sum(counts.values()) >= 2
        }
        q_lanes = []
        for q in selected_q:
            capacity = even_source_capacity(connection, q_to_labels[q])
            q_lanes.append({
                "quotient": f"12T{q}",
                "q": q,
                "currentGoldSignatureSiblings": q_gold[q],
                "currentGoldTargetGroups": len(q_groups[q]),
                "currentSoloRaidTargets": {
                    segment: int(raid_q_counts[q][segment])
                    for segment in ("rank10", "rank11", "rank12", "rank14")
                },
                "targetConditionedRelativeSwingCeiling": q_gold[q] + sum(raid_q_counts[q].values()),
                "historicallyProvenExactSquareclassLane": q in PROVEN_SQUARECLASS_Q,
                "sourceCapacity": capacity,
                "priorityClass": (
                    "proven-scale" if q in PROVEN_SQUARECLASS_Q and q_gold[q] >= 5
                    else "calibration-scale" if q_gold[q] >= 15
                    else "reserve"
                ),
            })
        q_lanes.sort(key=lambda row: (
            row["priorityClass"] != "proven-scale",
            -row["targetConditionedRelativeSwingCeiling"],
            -row["currentGoldSignatureSiblings"],
            -row["sourceCapacity"]["uniqueQuotientPresentations"],
        ))
        owned_rs: dict[str, list[int]] = defaultdict(list)
        for label, r_value in sorted(owned_pairs):
            owned_rs[label].append(r_value)
        defensive_pairs = []
        capacity_cache: dict[int, dict] = {}
        for row in rank14_rows:
            label, r_value = str(row["label"]), int(row["r"])
            if label not in owned_groups or (label, r_value) in owned_pairs:
                continue
            qs = sorted(label_to_q.get(label, set()))
            q_details = []
            for q in qs:
                if q not in capacity_cache:
                    capacity_cache[q] = even_source_capacity(connection, q_to_labels[q])
                q_details.append({
                    "quotient": f"12T{q}",
                    "historicallyProvenExactSquareclassLane": q in PROVEN_SQUARECLASS_Q,
                    "sourceCapacity": capacity_cache[q],
                })
            defensive_pairs.append({
                "label": label,
                "r": r_value,
                "opponent": "IGP24-T00134",
                "currentTeamCount": 1,
                "diracAlreadyOwnedSignatures": sorted(owned_rs[label]),
                "exact12x2Lanes": q_details,
                "submissionReady": False,
            })
        defensive_pairs.sort(key=lambda row: (
            not any(q["historicallyProvenExactSquareclassLane"] for q in row["exact12x2Lanes"]),
            not bool(row["exact12x2Lanes"]),
            -len(row["diracAlreadyOwnedSignatures"]),
            row["label"], row["r"],
        ))
    finally:
        connection.close()

    mine = board["mine"]
    top = {int(row["rank"]): row for row in board["top"]}
    score = float(mine["score"])
    gaps = {
        f"rank{rank}": {
            "opponentScore": float(top[rank]["score"]),
            "scoreGap": max(0.0, float(top[rank]["score"]) - score),
            "minimumWholeGolds": max(0, math.floor(float(top[rank]["score"]) - score) + 1),
            "minimumDirectSoloRaids": max(0, math.floor(float(top[rank]["score"]) - score) + 1),
        }
        for rank in (12, 11, 10)
    }
    rank14_cushion = score - float(top[14]["score"])
    q214_summary = json.loads(args.q214.read_text(encoding="utf-8"))
    return {
        "schemaVersion": "live-igp24-hillclimb-plan-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "safety": {
            "submissionAuthorized": False,
            "cloudLaunchAuthorized": False,
            "stopLatchesMustRemain": True,
            "reason": "planning and exact local research only; one authoritative submitter is not yet established",
        },
        "liveBoundary": {
            "leaderboardRetrievedAt": board.get("retrievedAt"),
            "targetGeneratedAt": target_generated_at,
            "rank": int(mine["rank"]),
            "score": score,
            "scoreablePairs": int(mine["scoreablePairs"]),
            "rank14Cushion": rank14_cushion,
            "gaps": gaps,
            "competitionClosesAt": "2026-08-15T12:00:00Z",
            "targetTeamCountDistribution": {str(k): v for k, v in sorted(target_counts.items())},
        },
        "opponentRaidSurface": {
            "rank11Alex": {
                **opponent_summary(rank11_rows, census),
                **raid_intersections["rank11"],
            },
            "rank10T00171": {
                **opponent_summary(rank10_rows, census),
                **raid_intersections["rank10"],
            },
            "rank12T00087": {
                **opponent_summary(rank12_rows, census),
                **raid_intersections["rank12"],
            },
            "rank14Defense": {
                "opponentSummary": opponent_summary(rank14_rows, census),
                "pairsOnGroupsDiracAlreadyReached": len(defensive_pairs),
                "mappedToExact12x2Action": sum(bool(row["exact12x2Lanes"]) for row in defensive_pairs),
                "priorityPairs": defensive_pairs,
                "finding": "one exact raid creates roughly a one-point relative cushion; no polynomial is currently submission-ready",
            },
            "economics": {
                "goldTc0": "approximately +1 to Dirac before later dilution",
                "directSoloRaid": "+0.5 to Dirac and -0.5 to the raided holder; relative swing 1",
            },
            "finding": "every crawled unique pair is an architecture_gap in the existing product-route atlas",
        },
        "goldSiblingSurface": {
            "currentGoldPairsOnAlreadyReachedGroups": len(gold_siblings),
            "currentGoldGroups": len({row["label"] for row in gold_siblings}),
            "mappedToExact12x2Action": len(gold_siblings) - unmapped_gold_siblings,
            "unmappedByCurrent12x2Action": unmapped_gold_siblings,
            "rankedQuotientLanes": q_lanes,
        },
        "immediateExactResearch": {
            "q109OneMaximalFrontier": {
                "pair": "24T19727/r24",
                "candidateSha256": "8c7900cc068fedc4204a41a7dbad8f4eb0417a9e74d8d7800ab8b3474689e5ef",
                "currentTeamCount": 0,
                "submissionReady": False,
                "verifiedSibling": "the same exact family produced server-accepted 24T19727/r20",
                "existingEvidence": "exact 12T109 quotient, singleton character/core, one sign solution, and 4/5 maximal exclusions after 9,988 primes",
                "remainingAmbiguity": "24T17014 (order 98,304, 12x2 block-kernel rank 9) inside target 24T19727 (order 393,216, rank 11)",
                "pilot": "prove selected conjugate squareclass rank 11 or compute the index-4 relative coset resolvent",
                "promotionGate": "independent exact exclusion of 24T17014 plus refreshed current-target/history gates",
            },
            "q214SeparatingResolvent": {
                "sealedCandidates": int(q214_summary["sealedQ214Candidates"]),
                "currentTc0CompatibleBeforeHistoryGate": int(q214_summary["tc0CompatibleBeforeSubmissionHistoryGate"]),
                "unsubmittedFailClosedFrontier": int(q214_summary["frontierCandidates"]),
                "submissionReady": 0,
                "currentCompatiblePairs": dict(q214_summary["tc0CompatibilityDistribution"]),
                "pilot": "classify the five smallest candidates with a group-only separating relative resolvent",
                "promotionGate": q214_summary["nextExperiment"]["promotionGate"],
            },
            "freshSingletonInsurance": {
                "families": ["12T108", "12T109"],
                "budget": "exactly seven fresh singleton fields from the sealed July-31 census",
                "expected": "0-1 tc0 pair; stop after the bounded wave if zero",
            },
            "discriminantReserve": {
                "pair": "24T13571/r8",
                "candidateSha256": "afb3b6c60febc2c9b6a2283768df5e00bc8fe137648928d5fbaf8c63f32ba9a2",
                "status": "exact assignment and independent nfdisc improvement; fresh restage still required",
                "role": "small defensive relative-score improvement, not a rank-climbing engine",
            },
        },
        "campaignDecision": {
            "thesis": "replace generic volume with exact, target-conditioned breadth",
            "phase0": "repair fail-closed control plane and keep all POST paths stopped",
            "phase1": "defend rank 13 with exact 12T260/12T81 squareclass pilots for the two proven-lane rank-14 raids, while closing the q109 index-4 24T19727/r24 ambiguity; then run the q214 five-candidate pilot",
            "phase2": "scale proven exact squareclass breadth on current gold sibling families, starting with 12T260/185/195; calibrate 12T222 and 12T141 on five fields each",
            "phase3": "develop incidence-targeted Selmer modules and quartic-over-sextic dependency architectures for direct rank-11/rank-10 raids",
            "phase4": "after independent exact certification, use newly reached sources for one bounded F5/F6 closure cascade",
            "forbidden": [
                "untargeted tower volume",
                "resubmitting stale outboxes",
                "maximal-exclusion label claims without exact containment",
                "scaling ambiguous q77/q136/q138/q214 families before server-calibrated singleton precision",
                "parallel submitters or any POST before ledger reconciliation and a global lock",
            ],
        },
    }


def markdown(plan: dict) -> str:
    live = plan["liveBoundary"]
    siblings = plan["goldSiblingSurface"]
    q_rows = "\n".join(
        f"| {row['quotient']} | {row['currentGoldSignatureSiblings']} | "
        f"{row['currentSoloRaidTargets']['rank10']}/"
        f"{row['currentSoloRaidTargets']['rank12']}/"
        f"{row['currentSoloRaidTargets']['rank14']} | "
        f"{row['targetConditionedRelativeSwingCeiling']} | "
        f"{row['sourceCapacity']['uniqueQuotientPresentations']:,} | "
        f"{'yes' if row['historicallyProvenExactSquareclassLane'] else 'no'} | {row['priorityClass']} |"
        for row in siblings["rankedQuotientLanes"][:20]
    )
    gap_rows = "\n".join(
        f"| {rank} | {data['opponentScore']:.6f} | {data['scoreGap']:.6f} | {data['minimumWholeGolds']} |"
        for rank, data in live["gaps"].items()
    )
    r11 = plan["opponentRaidSurface"]["rank11Alex"]
    r10 = plan["opponentRaidSurface"]["rank10T00171"]
    r12 = plan["opponentRaidSurface"]["rank12T00087"]
    defense = plan["opponentRaidSurface"]["rank14Defense"]
    q214 = plan["immediateExactResearch"]["q214SeparatingResolvent"]
    return f"""# Team Dirac IGP24 live hill-climb plan — 2026-08-06

## Decision

**Stop generic volume. Use exact, target-conditioned breadth.** No submission or cloud launch is authorized by this plan.

Live snapshot: rank **{live['rank']}**, score **{live['score']:.6f}**, rank-14 cushion only **{live['rank14Cushion']:.6f}**.

| Objective | Opponent score | Gap | Minimum whole golds |
| --- | ---: | ---: | ---: |
{gap_rows}

## Rank-13 defense comes first

The live cushion over rank 14 is only **{live['rank14Cushion']:.6f}**. A fresh read-only crawl found **{defense['opponentSummary']['uniquePairs']}** rank-14 solo pairs, including **{defense['pairsOnGroupsDiracAlreadyReached']}** on groups Dirac has already reached and **{defense['mappedToExact12x2Action']}** with a known 12x2 quotient lane. The first two exact pilots are **24T23760/r18 via 12T260** and **24T18086/r20 via 12T81** because those quotient families have prior exact success. No defensive polynomial is submission-ready.

## Why raids matter

The refreshed read-only crawl found **{r11['uniquePairs']}** solo pairs held by current rank 11 and **{r10['uniquePairs']}** held by current rank 10. A direct raid is a one-point relative swing: Dirac gains 0.5 and that holder loses 0.5. The old product atlas reaches none of them, but squareclass reintersection is better: **{r10['pairsOnGroupsDiracAlreadyReached']}** top-10 pairs and **{r12['pairsOnGroupsDiracAlreadyReached']}** rank-12 pairs lie on groups Dirac has already reached; {r10['mappedToExact12x2Action']} and {r12['mappedToExact12x2Action']} respectively have an exact 12x2 action lane.

Rank-11 concentration: {', '.join(f"{row['quotient']} ({row['pairs']})" for row in r11['topDegree6Quotients'][:6])}.  
Rank-10 concentration: {', '.join(f"{row['quotient']} ({row['pairs']})" for row in r10['topDegree6Quotients'][:6])}.

## Largest scalable surface: gold signature siblings

There are **{siblings['currentGoldPairsOnAlreadyReachedGroups']}** current tc0 signatures on **{siblings['currentGoldGroups']}** groups Dirac has already reached. This is the only locally evidenced surface large enough to cover the top-10 gap.

| Degree-12 quotient | Gold siblings | Solo raids 10/12/14 | Relative-swing ceiling | Even quotient presentations* | Proven exact family | Class |
| --- | ---: | ---: | ---: | ---: | --- | --- |
{q_rows}

\\* Presentation count is an upper bound before canonical field-isomorphism deduplication.

## Immediate proof-producing experiment

The highest-confidence current gold research target is **24T19727/r24**. Candidate `8c7900cc…` has an exact 12T109 quotient, singleton character/core alignment, one exact sign solution, and excludes four of five proper transitive maximals after 9,988 squarefree primes. Its same-family sibling was server-accepted as 24T19727/r20. It is still **not submission-ready**: the remaining ambiguity is 24T17014, whose 12x2 kernel has F2-rank 9 versus the target's rank 11. Close that single gap by proving conjugate-squareclass rank 11 or by an index-4 relative coset resolvent.

The fail-closed q214 reintersection found **{q214['unsubmittedFailClosedFrontier']}** unsubmitted candidates whose remaining exact catalogs contain current tc0 pairs, but **zero are submission-ready**. Classify only the five smallest with a group-only separating relative resolvent. Promote only a singleton exact label.

In parallel, run only the seven sealed fresh singleton fields in 12T108/12T109. Do not widen the wave if it yields zero.

## Scale order

1. Repair the control plane: one reconciled ledger, one locked submitter, fail-closed certificates, global STOP.
2. Run five-field 12T260 and 12T81 defensive squareclass pilots for 24T23760/r18 and 24T18086/r20 while closing the q109 index-4 ambiguity; then run the q214 five-candidate pilot.
3. Exact squareclass breadth on proven current sibling lanes, beginning 12T260, 12T185, and 12T195.
4. Five-field calibration pilots for large unproven sibling surfaces such as 12T222 and 12T141; scale only after exact server precision.
5. Incidence-targeted Selmer modules for new Kummer ranks, then quartic-over-sextic dependency families aimed directly at rank-11/rank-10 unique pairs.
6. Apply F5/F6 closure only to genuinely new exact sources.

## Hard prohibitions

- No stale outbox replay.
- No untargeted tower volume.
- No exact-label claim from maximal exclusion without containment.
- No ambiguous-family scaling before a singleton precision gate.
- No competing submitters, automatic batch loops, or POST before explicit authorization.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    parser.add_argument("--rank11", type=Path, default=DEFAULT_RANK11)
    parser.add_argument("--rank10", type=Path, default=DEFAULT_RANK10)
    parser.add_argument("--rank12", type=Path, default=DEFAULT_RANK12)
    parser.add_argument("--rank14", type=Path, default=DEFAULT_RANK14)
    parser.add_argument("--q214", type=Path, default=DEFAULT_Q214)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()
    plan = build_plan(args)
    args.json.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(plan), encoding="utf-8")
    print(json.dumps({
        "json": str(args.json.resolve()),
        "markdown": str(args.markdown.resolve()),
        "rank": plan["liveBoundary"]["rank"],
        "score": plan["liveBoundary"]["score"],
        "goldSiblings": plan["goldSiblingSurface"]["currentGoldPairsOnAlreadyReachedGroups"],
        "q214Frontier": plan["immediateExactResearch"]["q214SeparatingResolvent"]["unsubmittedFailClosedFrontier"],
    }, indent=2))


if __name__ == "__main__":
    main()

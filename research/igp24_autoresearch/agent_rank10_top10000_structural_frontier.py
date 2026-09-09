#!/usr/bin/env python3
"""Seal a coefficient-free structural frontier for T00134's top-10,000.

The prefix and exclusion boundary are delegated to the independently checked
top-10,000 pair-orbit census.  This script then joins that eligible pair set
against:

* exact candidates already surviving the full ledger/receipt/outbox audit;
* exact arithmetic-character alignment certificates;
* the fresh T00134 character bank;
* lower-Kummer subset-product actions with compatible source signatures; and
* the fresh pair-orbit census.

No candidate coefficients are copied into the outputs and no heavy algebra,
network, ledger write, or submission is performed.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import fresh_t00134_top10000_pair_orbit_census as top10000_census  # noqa: F401
import fresh_t00134_top5000_pair_orbit_census as census


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"

ALIGNMENTS = DATA / "agent_gold_c_character_census_alignments"
FRESH_CHARACTER_BANK = DATA / "t00134_character_bank_20260730.jsonl"
SUBSET_ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
PAIR_ORBIT_FRONTIER = DATA / "fresh_t00134_top10000_pair_orbit_frontier.jsonl"
EXACT_PLAN = DATA / "rank10_t00134_top10000_exact_intersection_plan.json"

OUTPUT = DATA / "rank10_t00134_top10000_structural_frontier_20260730.jsonl"
SUMMARY = DATA / "rank10_t00134_top10000_structural_frontier_20260730_summary.json"


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def pair_key(label: str, r: int) -> tuple[str, int]:
    return str(label), int(r)


def ratio_one_reference_swing(row: dict) -> float:
    """Contest swing when the new candidate has discriminant ratio exactly 1."""

    return 2.0 ** (-int(row["kTeams"])) + float(row["points"]) / 2.0


def exact_contest_projection(candidate_disc: int, target: dict) -> dict:
    """Apply the contest's cached-team-count marginal formula.

    The discriminant ratio is intentionally not capped at one: a candidate
    below the incumbent minimum has ratio greater than one and receives the
    corresponding small score boost.
    """

    minimum_disc = int(target["minScoringDiscAbs"])
    ratio = math.log(minimum_disc) / math.log(int(candidate_disc))
    candidate_score = (2.0 ** (-int(target["kTeams"]))) * ratio
    holder_reduction = float(target["points"]) / 2.0
    return {
        "discriminantRatio": ratio,
        "candidateScoreAfterJoin": candidate_score,
        "holderScoreReductionAfterJoin": holder_reduction,
        "projectedNetRelativeSwing": candidate_score + holder_reduction,
        "ratioOneReferenceRelativeSwing": ratio_one_reference_swing(target),
        "formula": "2^(-current_team_count)*log(current_min_disc)/log(candidate_disc) + holder_points/2",
    }


def accepted_sources(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    query = """
        SELECT v.label,v.r,v.submission_id,v.polynomial_index,
               p.coefficient_hash,v.field_disc_abs
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
          AND v.label IS NOT NULL AND v.r IS NOT NULL
        ORDER BY v.label,v.r,v.submission_id,v.polynomial_index
    """
    for label, r, submission_id, polynomial_index, coefficient_hash, disc in (
        connection.execute(query)
    ):
        grouped[str(label)].append(
            {
                "label": str(label),
                "r": int(r),
                "submissionId": str(submission_id),
                "polynomialIndex": int(polynomial_index),
                "coefficientSha256": str(coefficient_hash),
                "fieldDiscriminantAbs": str(disc) if disc else None,
            }
        )
    return grouped


def empty_entry(target: dict) -> dict:
    label = str(target["label"])
    r = int(target["r"])
    return {
        "targetPair": {
            "label": label,
            "r": r,
            "kTeamsAtCrawl": int(target["kTeams"]),
            "holderPointsAtCrawl": float(target["points"]),
            "holderScoringDiscAbsAtCrawl": str(target["scoringDiscAbs"]),
            "minimumDiscAbsAtCrawl": str(target["minScoringDiscAbs"]),
        },
        "ratioOneReferenceRelativeSwing": ratio_one_reference_swing(target),
        "readyExactCandidates": [],
        "characterAlignmentRoutes": [],
        "freshCharacterBankRoutes": [],
        "subsetActionRoutes": [],
        "pairOrbitRoutes": [],
    }


def append_unique(rows: list[dict], row: dict, key_fields: tuple[str, ...]) -> None:
    key = tuple(json.dumps(row.get(field), sort_keys=True) for field in key_fields)
    seen = {
        tuple(json.dumps(existing.get(field), sort_keys=True) for field in key_fields)
        for existing in rows
    }
    if key not in seen:
        rows.append(row)


def main() -> int:
    prefix = census.validated_prefix()
    with sqlite3.connect(DB) as connection:
        connection.row_factory = sqlite3.Row
        eligible, exclusion_meta = census.exclusion_boundary(
            connection, set(prefix)
        )
        sources_by_label = accepted_sources(connection)

    entries: dict[tuple[str, int], dict] = {}

    def entry_for(pair: tuple[str, int]) -> dict:
        if pair not in eligible:
            raise ValueError(f"route escaped eligible boundary: {pair}")
        if pair not in entries:
            entries[pair] = empty_entry(prefix[pair])
        return entries[pair]

    exact_plan = json.loads(EXACT_PLAN.read_text(encoding="utf-8"))
    exact_rows = exact_plan.get("selectedBestExactCandidatePerPair", [])
    exact_plan_corrected_total = 0.0
    exact_survivors_now_excluded: list[dict] = []
    for candidate in exact_rows:
        target = candidate["targetPair"]
        pair = pair_key(target["label"], target["r"])
        coefficient_free = {
            key: value
            for key, value in candidate.items()
            if key not in {"coefficients", "polynomial", "originalLine"}
        }
        coefficient_free["correctedContestProjection"] = exact_contest_projection(
            int(candidate["candidateFieldDiscriminantAbs"]), prefix[pair]
        )
        exact_plan_corrected_total += float(
            coefficient_free["correctedContestProjection"][
                "projectedNetRelativeSwing"
            ]
        )
        if pair not in eligible:
            exact_survivors_now_excluded.append(
                {
                    "targetPair": target,
                    "coefficientSha256": candidate["coefficientSha256"],
                    "correctedContestProjection": coefficient_free[
                        "correctedContestProjection"
                    ],
                    "status": "excluded_by_current_boundary_after_plan_seal",
                }
            )
            continue
        entry_for(pair)["readyExactCandidates"].append(coefficient_free)

    alignment_files = sorted(ALIGNMENTS.glob("*.json"))
    accepted_index = {
        (source["submissionId"], source["polynomialIndex"]): source
        for rows in sources_by_label.values()
        for source in rows
    }
    aligned_files_used = 0
    for path in alignment_files:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if (
            artifact.get("status") != "aligned_target"
            or artifact.get("sourceAlignmentRecovered") is not True
        ):
            continue
        source = artifact.get("source", {})
        source_key = (
            str(source.get("submissionId")),
            int(source.get("polynomialIndex", -1)),
        )
        ledger_source = accepted_index.get(source_key)
        if ledger_source is None:
            continue
        if str(source.get("coefficientSha256")) != ledger_source["coefficientSha256"]:
            raise ValueError(f"alignment source hash drift: {relative(path)}")
        target_cores = artifact.get("alignment", {}).get(
            "labelToUnambiguousSquarefreeNormCores", {}
        )
        used = False
        for label, cores in sorted(target_cores.items()):
            if not isinstance(cores, list) or not cores:
                continue
            for pair in sorted(p for p in eligible if p[0] == str(label)):
                route = {
                    "artifact": relative(path),
                    "artifactSha256": sha256_file(path),
                    "source": ledger_source,
                    "normCores": sorted({int(core) for core in cores}),
                    "quotientT": int(artifact["alignment"]["quotientT"]),
                    "ramifiedPrimes": artifact["alignment"].get("ramifiedPrimes"),
                }
                append_unique(
                    entry_for(pair)["characterAlignmentRoutes"],
                    route,
                    ("artifact", "source", "normCores"),
                )
                used = True
        aligned_files_used += int(used)

    for row in read_jsonl(FRESH_CHARACTER_BANK):
        if row.get("status") != "executable":
            continue
        target = row["target"]
        pair = pair_key(target["label"], target["r"])
        if pair not in eligible:
            continue
        for base in row.get("bases", []):
            route = {
                "logicalTaskId": row.get("logicalTaskId"),
                "source": {
                    key: base.get(key)
                    for key in (
                        "submissionId",
                        "polynomialIndex",
                        "label",
                        "r",
                        "coefficientSha256",
                        "fieldDiscAbs",
                    )
                },
                "normCores": sorted(
                    {
                        int(core)
                        for core in base.get("alignment", {})
                        .get("targetNormCores", {})
                        .get(target["label"], [])
                    }
                ),
                "quotientT": int(target["quotientT12"]),
                "ramifiedPrimes": base.get("alignment", {}).get("ramifiedPrimes"),
                "attempts": [
                    {
                        "auxiliaryPrimes": attempt.get("auxiliaryPrimes", []),
                        "output": str(
                            Path(attempt["output"]).relative_to(ROOT)
                            if Path(attempt["output"]).is_absolute()
                            else Path(attempt["output"])
                        ),
                    }
                    for attempt in row.get("attempts", [])
                ],
            }
            append_unique(
                entry_for(pair)["freshCharacterBankRoutes"],
                route,
                ("source", "normCores"),
            )

    subset_action_rows = 0
    subset_source_routes = 0
    for action_index, action in enumerate(read_jsonl(SUBSET_ACTIONS)):
        target_label = str(action["targetLabel"])
        source_label = str(action["sourceLabel"])
        compatible = action.get("sourceSignatureToPossibleTargetSignatures", {})
        for source in sources_by_label.get(source_label, []):
            possible_target_r = {
                int(value) for value in compatible.get(str(source["r"]), [])
            }
            if not possible_target_r:
                continue
            for target_r in sorted(possible_target_r):
                pair = pair_key(target_label, target_r)
                if pair not in eligible:
                    continue
                route = {
                    "actionRow": action_index + 1,
                    "source": source,
                    "sourceT": int(action["sourceT"]),
                    "subsetSize": int(action["subsetSize"]),
                    "targetKernelOrder": int(action["targetKernelOrder"]),
                    "targetKummerRank": int(action["targetKummerRank"]),
                    "incidenceMatrixRank": int(action["incidenceMatrixRank"]),
                }
                before = len(entry_for(pair)["subsetActionRoutes"])
                append_unique(
                    entry_for(pair)["subsetActionRoutes"],
                    route,
                    ("actionRow", "source"),
                )
                if len(entry_for(pair)["subsetActionRoutes"]) > before:
                    subset_source_routes += 1
                    subset_action_rows += 1

    for route in read_jsonl(PAIR_ORBIT_FRONTIER):
        source = {
            key: route.get(key)
            for key in (
                "submissionId",
                "polynomialIndex",
                "sourceLabel",
                "sourceR",
                "sourceCoefficientSha256",
                "sourceFieldDiscAbs",
            )
        }
        guaranteed = {
            pair_key(row["label"], row["r"])
            for row in route.get("guaranteedTargetPairs", [])
        }
        for competition in route.get("reachablePairCompetition", []):
            pair = pair_key(competition["label"], competition["r"])
            if pair not in eligible:
                continue
            pair_route = {
                "source": source,
                "guaranteed": pair in guaranteed,
                "estimatedAnyT00134HitProbability": float(
                    route["estimatedAnyT00134HitProbability"]
                ),
                "ratioOneReferenceRelativeSwing": ratio_one_reference_swing(
                    prefix[pair]
                ),
                "compatibleClassIndexes": route["compatibleClassIndexes"],
                "orbitEvidence": route.get("orbitEvidence", {}).get(
                    f"{pair[0]}/r{pair[1]}"
                ),
            }
            append_unique(
                entry_for(pair)["pairOrbitRoutes"],
                pair_route,
                ("source",),
            )

    mechanism_counts: Counter[str] = Counter()
    k_pair_counts: Counter[int] = Counter()
    output_rows: list[dict] = []
    ratio_one_reference_total = 0.0
    ready_exact_total = 0.0
    for pair, row in entries.items():
        mechanisms: list[str] = []
        if row["readyExactCandidates"]:
            mechanisms.append("ready_exact")
        if any(route["guaranteed"] for route in row["pairOrbitRoutes"]):
            mechanisms.append("pair_orbit_guaranteed")
        if row["characterAlignmentRoutes"]:
            mechanisms.append("cached_exact_character_alignment")
        if row["freshCharacterBankRoutes"]:
            mechanisms.append("fresh_exact_character_alignment")
        if row["subsetActionRoutes"]:
            mechanisms.append("lower_kummer_subset_action")
        if row["pairOrbitRoutes"]:
            mechanisms.append("pair_orbit_structural")
        row["mechanisms"] = mechanisms
        for mechanism in mechanisms:
            mechanism_counts[mechanism] += 1
        k = int(row["targetPair"]["kTeamsAtCrawl"])
        k_pair_counts[k] += 1
        ratio_one_reference_total += float(row["ratioOneReferenceRelativeSwing"])
        exact_net = max(
            (
                float(
                    candidate["correctedContestProjection"][
                        "projectedNetRelativeSwing"
                    ]
                )
                for candidate in row["readyExactCandidates"]
            ),
            default=0.0,
        )
        ready_exact_total += exact_net
        row["bestReadyExactProjectedNetRelativeSwing"] = exact_net or None
        row["routeCounts"] = {
            "readyExact": len(row["readyExactCandidates"]),
            "cachedCharacter": len(row["characterAlignmentRoutes"]),
            "freshCharacter": len(row["freshCharacterBankRoutes"]),
            "subsetAction": len(row["subsetActionRoutes"]),
            "pairOrbit": len(row["pairOrbitRoutes"]),
        }
        certainty_tier = (
            0
            if row["readyExactCandidates"]
            else 1
            if "pair_orbit_guaranteed" in mechanisms
            else 2
            if (
                row["characterAlignmentRoutes"]
                or row["freshCharacterBankRoutes"]
            )
            else 3
            if row["subsetActionRoutes"]
            else 4
        )
        row["certaintyTier"] = certainty_tier
        row["priorityScore"] = (
            exact_net
            if exact_net
            else float(row["ratioOneReferenceRelativeSwing"])
        )
        output_rows.append(row)

    output_rows.sort(
        key=lambda row: (
            -float(row["priorityScore"]),
            int(row["certaintyTier"]),
            int(row["targetPair"]["kTeamsAtCrawl"]),
            row["targetPair"]["label"],
            int(row["targetPair"]["r"]),
        )
    )
    with OUTPUT.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    summary = {
        "schemaVersion": "rank10-t00134-top10000-structural-frontier-v1",
        "status": "sealed_coefficient_free_structural_frontier",
        "prefix": {
            "path": relative(census.PREFIX),
            "sha256": sha256_file(census.PREFIX),
            "pairs": len(prefix),
        },
        "exclusions": exclusion_meta,
        "inputs": {
            "exactPlan": {
                "path": relative(EXACT_PLAN),
                "sha256": sha256_file(EXACT_PLAN),
            },
            "cachedAlignmentDirectory": relative(ALIGNMENTS),
            "cachedAlignmentFilesScanned": len(alignment_files),
            "cachedAlignmentFilesUsed": aligned_files_used,
            "freshCharacterBank": {
                "path": relative(FRESH_CHARACTER_BANK),
                "sha256": sha256_file(FRESH_CHARACTER_BANK),
            },
            "subsetActions": {
                "path": relative(SUBSET_ACTIONS),
                "sha256": sha256_file(SUBSET_ACTIONS),
            },
            "pairOrbitFrontier": {
                "path": relative(PAIR_ORBIT_FRONTIER),
                "sha256": sha256_file(PAIR_ORBIT_FRONTIER),
            },
        },
        "frontier": {
            "path": relative(OUTPUT),
            "sha256": sha256_file(OUTPUT),
            "distinctPairs": len(output_rows),
            "pairCountsByK": {
                str(key): value for key, value in sorted(k_pair_counts.items())
            },
            "pairCountsByMechanism": dict(sorted(mechanism_counts.items())),
            "subsetActionSourceRoutes": subset_source_routes,
            "distinctPairRatioOneReferenceSwingSum": ratio_one_reference_total,
            "readyExactProjectedNetRelativeSwingSum": ready_exact_total,
        },
        "sealedExactPlan": {
            "survivorsAtSeal": len(exact_rows),
            "survivorsStillCurrentEligible": (
                len(exact_rows) - len(exact_survivors_now_excluded)
            ),
            "survivorsNowExcludedByCurrentBoundary": len(
                exact_survivors_now_excluded
            ),
            "correctedProjectedNetRelativeSwingSumAtSeal": (
                exact_plan_corrected_total
            ),
            "nowExcluded": exact_survivors_now_excluded,
        },
        "topRoutes": [
            {
                "targetPair": row["targetPair"],
                "certaintyTier": row["certaintyTier"],
                "priorityScore": row["priorityScore"],
                "ratioOneReferenceRelativeSwing": row[
                    "ratioOneReferenceRelativeSwing"
                ],
                "bestReadyExactProjectedNetRelativeSwing": row[
                    "bestReadyExactProjectedNetRelativeSwing"
                ],
                "mechanisms": row["mechanisms"],
                "routeCounts": row["routeCounts"],
            }
            for row in output_rows[:100]
        ],
        "checks": {
            "allTargetPairsWithinFreshEligibleBoundary": True,
            "coefficientPayloadOmitted": True,
            "credentialMaterialIncluded": False,
            "oneHeavyWorkerRuns": 0,
            "networkCalls": 0,
            "ledgerWrites": 0,
            "submissionCalls": 0,
        },
    }
    SUMMARY.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "frontier": relative(OUTPUT),
                "frontierSha256": sha256_file(OUTPUT),
                "pairs": len(output_rows),
                "readyExactProjectedNet": ready_exact_total,
                "summary": relative(SUMMARY),
                "summarySha256": sha256_file(SUMMARY),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

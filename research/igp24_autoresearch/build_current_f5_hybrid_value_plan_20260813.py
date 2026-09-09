#!/usr/bin/env python3
"""Build a novelty-safe F5 plan for current gold through low-holder silver."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import prepare_f5_untried_wave as f5


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--max-team-count", type=int, default=3)
    parser.add_argument("--max-sources", type=int, default=400)
    parser.add_argument("--sources-per-label-signature", type=int, default=4)
    parser.add_argument("--exclude-plan", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.max_team_count < 0 or args.max_sources <= 0:
        raise ValueError("invalid target or source cap")
    if args.sources_per_label_signature <= 0:
        raise ValueError("sources-per-label-signature must be positive")
    output = DATA / f"current_f5_hybrid_value_{args.tag}.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")

    actions: dict[str, dict] = {}
    action_artifacts = []
    for relative, expected in f5.ACTION_SHARDS.items():
        path = ROOT / relative
        actual = f5.sha256_path(path)
        if actual != expected:
            raise ValueError(f"F5 action shard changed: {relative}")
        action_artifacts.append({"path": relative, "sha256": actual})
        for row in f5.iter_jsonl(path):
            digest = f5.action_key(row)
            incumbent = actions.get(digest)
            if incumbent is not None and f5.action_core(incumbent) != f5.action_core(row):
                raise ValueError("conflicting deduplicated F5 action")
            actions[digest] = row
    if len(actions) != 3771:
        raise ValueError("deduplicated F5 action count changed")
    by_label: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for digest, action in actions.items():
        by_label[str(action["sourceLabel"])].append((digest, action))
    unique_actions = {
        label: rows[0] for label, rows in by_label.items() if len(rows) == 1
    }

    destination = DATA / f"current_f5_hybrid_value_{args.tag}"
    prior_plans, prior_results = f5.discover_prior_f5_paths(destination)
    # Later recovery campaigns live outside the original ``f5_untried*``
    # directory convention.  Include every parseable F5 plan/result artifact
    # so a silver-aware run cannot recycle a source already resolved elsewhere.
    prior_plan_set = {path.resolve() for path in prior_plans}
    prior_result_set = {path.resolve() for path in prior_results}
    for path in DATA.rglob("*.json"):
        if "f5" not in str(path.relative_to(DATA)).lower() or not path.is_file():
            continue
        try:
            payload = f5.read_json(path)
            f5.source_digests_from_plan(payload, path)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        prior_plan_set.add(path.resolve())
    for path in DATA.rglob("*.jsonl"):
        if "f5" not in str(path.relative_to(DATA)).lower() or not path.is_file():
            continue
        try:
            has_source = any(
                isinstance(row.get("source"), dict)
                and f5.source_digest_from_result(row) is not None
                for row in f5.iter_jsonl(path)
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if has_source:
            prior_result_set.add(path.resolve())
    prior_plans = sorted(prior_plan_set)
    prior_results = sorted(prior_result_set)
    tried, known_candidate_sources, prior_result_rows = f5.collect_prior_sources(
        prior_plans, prior_results
    )
    tried_hashes = {row[2] for row in tried}
    explicitly_excluded_plans = []
    for raw_path in args.exclude_plan:
        path = (ROOT / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
        if DATA.resolve() not in path.parents or not path.is_file():
            raise ValueError("excluded plan must be an existing artifact under data")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != "current-f5-hybrid-value-plan-v1":
            raise ValueError("excluded plan has the wrong schema")
        tried_hashes.update(
            str(row["canonicalQuotientSha256"]) for row in payload["groups"]
        )
        explicitly_excluded_plans.append(
            {"path": str(path.relative_to(ROOT)), "sha256": f5.sha256_path(path)}
        )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE status='accepted'"
        )
    }
    desired = {
        (str(label), int(r)): int(team_count)
        for label, r, team_count in connection.execute(
            "SELECT label,r,team_count FROM targets WHERE team_count<=?",
            (args.max_team_count,),
        )
        if (str(label), int(r)) not in baseline
        and (str(label), int(r)) not in owned
    }

    candidates: dict[tuple[str, int, str], dict] = {}
    skip_counts = Counter()
    for row in connection.execute(
        "SELECT v.label,v.t,v.r,p.coefficients,p.coefficient_hash,p.submission_id,"
        "p.polynomial_index,length(p.coefficients) AS coefficient_bytes "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE v.status='accepted'"
    ):
        values = str(row["coefficients"]).split(",")
        if (
            len(values) != 25
            or values[-1] != "1"
            or any(int(values[index]) for index in range(1, 25, 2))
        ):
            continue
        label = str(row["label"])
        action_item = unique_actions.get(label)
        if action_item is None:
            skip_counts["not_unique_action_label"] += 1
            continue
        action_digest, action = action_item
        source_r = int(row["r"])
        possible_r = sorted(
            {
                int(value)
                for value in action["sourceSignatureToPossibleTargetSignatures"].get(
                    str(source_r), []
                )
            }
        )
        target_label = str(action["targetLabel"])
        desired_counts = {
            str(r): desired[(target_label, r)]
            for r in possible_r
            if (target_label, r) in desired
        }
        if not desired_counts:
            skip_counts["no_current_low_holder_outcome"] += 1
            continue
        quotient_line = ",".join(values[::2])
        canonical = f5.canonical_hash(quotient_line)
        if canonical in tried_hashes:
            skip_counts["prior_f5_canonical_source"] += 1
            continue
        key = (label, source_r, canonical)
        height_bits = max(abs(int(value)) for value in values[::2]).bit_length()
        candidate = {
            "action": action,
            "actionSha256": action_digest,
            "canonicalQuotientSha256": canonical,
            "coefficientHeightBits": height_bits,
            "desiredTeamCounts": desired_counts,
            "possibleR": possible_r,
            "quotientLine": quotient_line,
            "quotientSha256": hashlib.sha256(quotient_line.encode("ascii")).hexdigest(),
            "source": {
                "coefficientBytes": int(row["coefficient_bytes"]),
                "coefficientSha256": str(row["coefficient_hash"]),
                "label": label,
                "polynomialIndex": int(row["polynomial_index"]),
                "r": source_r,
                "submissionId": str(row["submission_id"]),
                "t": int(row["t"]),
            },
            "targetLabel": target_label,
            "targetT": int(action["targetT"]),
        }
        rank = (
            height_bits,
            int(row["coefficient_bytes"]),
            str(row["coefficient_hash"]),
        )
        incumbent = candidates.get(key)
        if incumbent is None or rank < incumbent["_rank"]:
            candidate["_rank"] = rank
            candidates[key] = candidate

    connection.close()
    ordered = sorted(
        candidates.values(),
        key=lambda row: (
            min(int(value) for value in row["desiredTeamCounts"].values()),
            -len(row["desiredTeamCounts"]) / max(1, len(row["possibleR"])),
            -len(row["desiredTeamCounts"]),
            int(row["coefficientHeightBits"]),
            int(row["source"]["coefficientBytes"]),
            row["canonicalQuotientSha256"],
        ),
    )
    selected = []
    by_source_signature = Counter()
    selected_canonical = set()
    for row in ordered:
        signature = (str(row["source"]["label"]), int(row["source"]["r"]))
        canonical = str(row["canonicalQuotientSha256"])
        if (
            canonical in selected_canonical
            or by_source_signature[signature] >= args.sources_per_label_signature
        ):
            continue
        row.pop("_rank", None)
        selected.append(row)
        selected_canonical.add(canonical)
        by_source_signature[signature] += 1
        if len(selected) >= args.max_sources:
            break
    for ordinal, row in enumerate(selected, 1):
        row["groupOrdinal"] = ordinal
    covered_pairs = {
        (str(row["targetLabel"]), int(r)): int(team_count)
        for row in selected
        for r, team_count in row["desiredTeamCounts"].items()
    }
    payload = {
        "actionArtifacts": action_artifacts,
        "deduplicatedActions": len(actions),
        "eligibleCanonicalSourceCandidates": len(candidates),
        "explicitlyExcludedPlans": explicitly_excluded_plans,
        "groups": selected,
        "knownCandidateSourceHashesFromPriorResults": len(known_candidate_sources),
        "maximumSources": args.max_sources,
        "maximumTeamCount": args.max_team_count,
        "priorCanonicalSources": len(tried_hashes),
        "priorF5Plans": len(prior_plans),
        "priorF5ResultArtifacts": len(prior_results),
        "priorF5ResultRows": prior_result_rows,
        "schemaVersion": "current-f5-hybrid-value-plan-v1",
        "selectedGroups": len(selected),
        "selectedPairCeiling": len(covered_pairs),
        "selectedPairTeamCountDistribution": dict(
            sorted(Counter(covered_pairs.values()).items())
        ),
        "skipCounts": dict(sorted(skip_counts.items())),
        "sourcesPerLabelSignature": args.sources_per_label_signature,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), **{k: payload[k] for k in ("selectedGroups", "selectedPairCeiling", "selectedPairTeamCountDistribution")}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

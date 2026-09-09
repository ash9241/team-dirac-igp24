#!/usr/bin/env python3
"""Stage exact fresh-prime twists from accepted pending even presentations.

This is a post-reset recovery lane.  A source is allowed to be accepted but
not yet scoreable; the server's exact source label and signature are still
valid mathematical evidence.  Candidate target pairs must have no scoreable
Dirac row, no frozen-baseline row, and no accepted Dirac row in the current
recovery window.  Every admitted action-map row is unanimous across all
12x2 block systems.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import fresh_t00134_twist_route_audit as action_audit
import fresh_t00134_twist_stage_top10000 as twist_stage
import stage_current_thin_twist_portfolio_20260812 as common


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-team-count", type=int, default=10)
    parser.add_argument(
        "--source-selection",
        choices=("pending", "scoreable"),
        default="pending",
        help="accepted source-row class to search (default: %(default)s)",
    )
    parser.add_argument(
        "--exclude-since", default="2026-08-14T09:00:00Z"
    )
    parser.add_argument(
        "--tag", default="current_pending_even_twist_closure_20260814"
    )
    args = parser.parse_args()
    if not 0 <= args.max_team_count <= 64:
        parser.error("--max-team-count must be between 0 and 64")
    if not args.tag or Path(args.tag).name != args.tag:
        parser.error("--tag must be one path component")
    return args


def coefficient_hash(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def outbox_hashes(excluded: Path) -> set[str]:
    hashes: set[str] = set()
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        if path.resolve() == excluded.resolve():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = common.canonical_line(raw.split("#", 1)[0].strip())
            if line is not None:
                hashes.add(coefficient_hash(line))
    return hashes


def source_rank(source: dict, candidate: dict) -> tuple:
    field_disc = source.get("field_disc_abs")
    return (
        int(candidate["polynomialDiscriminantAbs"]),
        field_disc is None,
        int(field_disc) if field_disc is not None else 0,
        int(candidate["coefficientBytes"]),
        str(candidate["coefficientSha256"]),
    )


def main() -> int:
    args = parse_args()
    manifest = ROOT / "outbox" / f"{args.tag}.txt"
    index = DATA / f"{args.tag}.jsonl"
    certificate = DATA / f"{args.tag}_certificate.json"
    for path in (manifest, index, certificate):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite sealed output: {path}")

    actions: dict[str, tuple[dict, str]] = {}
    for action in action_audit.read_jsonl(ACTION_MAP):
        target_label = action_audit.unanimous_target(action)
        if target_label is not None:
            actions[str(action["sourceLabel"])] = (action, target_label)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        targets = {
            (str(row["label"]), int(row["r"])): dict(row)
            for row in connection.execute(
                """
                SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
                FROM targets WHERE team_count BETWEEN 0 AND ?
                """,
                (int(args.max_team_count),),
            )
        }
        scoreable = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT DISTINCT label,r FROM verifications
                WHERE status='accepted' AND scoreable=1
                  AND label IS NOT NULL AND r IS NOT NULL
                """
            )
        }
        baseline = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        recovery_window = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT DISTINCT v.label,v.r
                FROM verifications AS v
                JOIN submissions AS s USING(submission_id)
                WHERE v.status='accepted' AND s.created_at>=?
                  AND v.label IS NOT NULL AND v.r IS NOT NULL
                """,
                (str(args.exclude_since),),
            )
        }
        excluded_pairs = scoreable | baseline | recovery_window
        eligible_target_labels = {
            label for label, r in set(targets) - excluded_pairs
        }
        relevant_source_labels = sorted(
            label
            for label, (_action, target_label) in actions.items()
            if target_label in eligible_target_labels
        )
        if not relevant_source_labels:
            raise ValueError("no action-map sources can reach eligible targets")

        sources_by_hash: dict[str, dict] = {}
        source_predicate = (
            "v.scoring_status='pending'"
            if args.source_selection == "pending"
            else "v.scoreable=1"
        )
        source_placeholders = ",".join("?" for _ in relevant_source_labels)
        for raw in connection.execute(
            f"""
            SELECT v.submission_id,v.polynomial_index,v.label,v.t,v.r,
                   v.scoring_status,v.field_disc_abs,v.poly_disc_abs,
                   p.coefficients,p.coefficient_hash
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND {source_predicate}
              AND v.label IS NOT NULL AND v.r IS NOT NULL
              AND v.label IN ({source_placeholders})
            """
            ,
            relevant_source_labels,
        ):
            source = dict(raw)
            label = str(source["label"])
            if label not in actions:
                continue
            coefficients = tuple(
                int(value) for value in str(source["coefficients"]).split(",")
            )
            if (
                len(coefficients) != 25
                or coefficients[-1] != 1
                or coefficients[0] == 0
                or math.gcd(*coefficients) != 1
                or any(coefficients[index] for index in range(1, 25, 2))
            ):
                continue
            source["coefficientsList"] = list(coefficients)
            digest = str(source["coefficient_hash"])
            incumbent = sources_by_hash.get(digest)
            if incumbent is not None:
                if (
                    incumbent["label"],
                    int(incumbent["r"]),
                    incumbent["coefficientsList"],
                ) != (label, int(source["r"]), list(coefficients)):
                    raise ValueError(f"conflicting accepted source hash {digest}")
                continue
            sources_by_hash[digest] = source

        quotients: list[tuple[int, ...]] = []
        quotient_index: dict[tuple[int, ...], int] = {}
        for source in sources_by_hash.values():
            quotient = tuple(source["coefficientsList"][::2])
            if quotient not in quotient_index:
                quotient_index[quotient] = len(quotients)
                quotients.append(quotient)
        quotient_roots = common.gp_real_root_counts(quotients)

        routes: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for source in sources_by_hash.values():
            action, target_label = actions[str(source["label"])]
            roots = quotient_roots[
                quotient_index[tuple(source["coefficientsList"][::2])]
            ]
            negative_r = 2 * roots - int(source["r"])
            for sign, target_r in (
                ("positive", int(source["r"])),
                ("negative", negative_r),
            ):
                pair = (target_label, target_r)
                if pair not in targets or pair in excluded_pairs:
                    continue
                routes[pair].append(
                    {
                        "sign": sign,
                        "source": source,
                        "action": action,
                        "quotientRealRootCount": roots,
                        "negativeTwistRealRootCount": negative_r,
                    }
                )

        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        prior_outbox_hashes = outbox_hashes(manifest)
        forbidden_hashes = ledger_hashes | prior_outbox_hashes
        selected: list[dict] = []
        selected_hashes: set[str] = set()
        for pair, alternatives in sorted(
            routes.items(),
            key=lambda item: (
                int(targets[item[0]]["team_count"]),
                int(item[0][0][3:]),
                int(item[0][1]),
            ),
        ):
            proposals: list[tuple[dict, dict]] = []
            for route in alternatives:
                source = dict(route["source"])
                polynomial_disc = twist_stage.exact_even_polynomial_discriminant(
                    list(source["coefficientsList"])
                )
                recorded = source.get("poly_disc_abs")
                if recorded is not None and int(recorded) != polynomial_disc:
                    raise ValueError("source polynomial discriminant changed")
                source["poly_disc_abs"] = str(polynomial_disc)
                candidate = twist_stage.first_candidate_for_route(
                    source,
                    list(source["coefficientsList"]),
                    {"sign": route["sign"]},
                    forbidden_hashes | selected_hashes,
                )
                proposals.append((route, {**candidate, "source": source}))
            route, proposal = min(
                proposals,
                key=lambda value: source_rank(
                    value[1]["source"], value[1]
                ),
            )
            source = proposal.pop("source")
            line = str(proposal.pop("line"))
            digest = str(proposal["coefficientSha256"])
            selected_hashes.add(digest)
            target = targets[pair]
            selected.append(
                {
                    "coefficientLine": line,
                    **proposal,
                    "sourceSubmissionId": str(source["submission_id"]),
                    "sourcePolynomialIndex": int(source["polynomial_index"]),
                    "sourceCoefficientSha256": str(source["coefficient_hash"]),
                    "sourceLabel": str(source["label"]),
                    "sourceR": int(source["r"]),
                    "sourceFieldDiscriminantAbs": source.get("field_disc_abs"),
                    "sourceScoringStatus": str(source["scoring_status"]),
                    "targetLabel": pair[0],
                    "targetR": pair[1],
                    "targetTeamCount": int(target["team_count"]),
                    "targetMinimumDiscAbs": target["minimum_disc_abs"],
                    "targetSnapshotGeneratedAt": target["generated_at"],
                    "sign": str(route["sign"]),
                    "quotientRealRootCount": int(
                        route["quotientRealRootCount"]
                    ),
                    "negativeTwistRealRootCount": int(
                        route["negativeTwistRealRootCount"]
                    ),
                    "actionSystemCount": int(route["action"]["systemCount"]),
                    "eligibleSourceAlternatives": len(alternatives),
                    "projectedMarginalBase": 2.0
                    ** (-int(target["team_count"])),
                }
            )

        checks = common.gp_verify_candidates(
            [str(row["coefficientLine"]) for row in selected]
        )
        for row, (irreducible, real_roots) in zip(selected, checks):
            if not irreducible or real_roots != int(row["targetR"]):
                raise ValueError(
                    "direct PARI check failed for "
                    f"{row['targetLabel']}/r{row['targetR']}"
                )
            row["directPariIrreducible"] = True
            row["directPariRealRootCount"] = real_roots

        current_scoreable = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        current_recovery = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT DISTINCT v.label,v.r FROM verifications v
                JOIN submissions s USING(submission_id)
                WHERE v.status='accepted' AND s.created_at>=?
                """,
                (str(args.exclude_since),),
            )
        }
        selected_pairs = {
            (str(row["targetLabel"]), int(row["targetR"])) for row in selected
        }
        if (
            selected_pairs & (current_scoreable | baseline | current_recovery)
            or selected_hashes & ledger_hashes
            or selected_hashes & outbox_hashes(manifest)
        ):
            raise ValueError("final pair or coefficient novelty gate failed")
    finally:
        connection.close()

    manifest_payload = "".join(
        str(row["coefficientLine"]) + "\n" for row in selected
    )
    index_payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in selected
    )
    histogram = Counter(int(row["targetTeamCount"]) for row in selected)
    marginal = sum(
        (
            Fraction(1, 2 ** int(row["targetTeamCount"]))
            for row in selected
        ),
        Fraction(),
    )
    cert = {
        "schemaVersion": "current-pending-even-twist-closure-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_safe_staged_not_submitted",
        "candidateRows": len(selected),
        "distinctCandidateHashes": len(selected_hashes),
        "distinctTargetPairs": len(selected_pairs),
        "teamCountDistribution": {
            str(key): value for key, value in sorted(histogram.items())
        },
        "projectedMarginalBaseExact": (
            str(marginal.numerator)
            if marginal.denominator == 1
            else f"{marginal.numerator}/{marginal.denominator}"
        ),
        "projectedMarginalBase": float(marginal),
        "inputs": {
            "ledger": str(DB.relative_to(ROOT)),
            "actionMap": str(ACTION_MAP.relative_to(ROOT)),
            "actionMapSha256": common.sha256_path(ACTION_MAP),
            "unanimousActionLabels": len(actions),
            "acceptedEvenSources": len(sources_by_hash),
            "sourceSelection": str(args.source_selection),
            "uniqueDegree12QuotientsChecked": len(quotients),
            "excludeSince": str(args.exclude_since),
            "maximumTargetTeamCount": int(args.max_team_count),
        },
        "proof": {
            "acceptedExactSourcesPinned": True,
            "primitiveMonicEvenSources": True,
            "allBlockSystemsSameTargetLabel": True,
            "freshPrimeRamificationDisjointness": True,
            "exactPolynomialDiscriminants": len(selected),
            "directPariIrreducibilityChecks": len(selected),
            "directPariRealRootChecks": len(selected),
            "noScoreableOrRecoveryWindowTargetPairs": True,
        },
        "artifacts": {
            "manifest": str(manifest.relative_to(ROOT)),
            "manifestSha256": hashlib.sha256(
                manifest_payload.encode("utf-8")
            ).hexdigest(),
            "index": str(index.relative_to(ROOT)),
            "indexSha256": hashlib.sha256(
                index_payload.encode("utf-8")
            ).hexdigest(),
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    common.atomic_new(manifest, manifest_payload)
    common.atomic_new(index, index_payload)
    common.atomic_new(
        certificate, json.dumps(cert, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(cert, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

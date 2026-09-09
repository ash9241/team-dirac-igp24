#!/usr/bin/env python3
"""Build a current-value plan for exact index-24 unordered-pair resolvents."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_MAPS = sorted((ROOT / "data").glob("agent_index24_missing_pair_shard*.jsonl"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--orbit-map", action="append", type=Path, default=[])
    parser.add_argument(
        "--exclude-plan",
        action="append",
        type=Path,
        default=[],
        help="exclude source/action triples already present in an earlier plan",
    )
    parser.add_argument("--max-team-count", type=int, default=15)
    parser.add_argument("--sources-per-signature", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite index-24 plan outputs")
    maps = [path.resolve() for path in (args.orbit_map or DEFAULT_MAPS)]
    if not maps:
        raise FileNotFoundError("no unordered-pair orbit maps")

    excluded_sources = set()
    for plan_path in args.exclude_plan:
        for row in read_jsonl(plan_path):
            excluded_sources.add(
                (
                    str(row["sourceSubmissionId"]),
                    int(row["sourcePolynomialIndex"]),
                    str(row["orbitMap"]),
                )
            )

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    jobs: list[dict] = []
    all_pairs: set[tuple[str, int]] = set()
    try:
        for orbit_map in maps:
            for action in read_jsonl(orbit_map):
                if action.get("status") not in (None, "certified") or not action.get("targets"):
                    continue
                source_label = str(action["sourceLabel"])
                profiles_by_r: dict[int, list[dict]] = defaultdict(list)
                for profile in action.get("profiles", []):
                    profiles_by_r[int(profile["sourceR"])].append(profile)
                if profiles_by_r:
                    source_routes = list(profiles_by_r.items())
                else:
                    source_routes = [
                        (int(row[0]), [])
                        for row in connection.execute(
                            "SELECT DISTINCT r FROM verifications "
                            "WHERE label=? AND status='accepted' ORDER BY r",
                            (source_label,),
                        )
                    ]
                for source_r, profiles in source_routes:
                    live_pairs: dict[tuple[str, int], int] = {}
                    routed_targets = [
                        target
                        for profile in profiles
                        for target in profile.get("targets", [])
                    ]
                    if not profiles:
                        routed_targets = [
                            {"targetLabel": action_target["targetLabel"], "targetR": int(row[0])}
                            for action_target in action.get("targets", [])
                            if str(action_target["targetLabel"]) != source_label
                            for row in connection.execute(
                                "SELECT r FROM targets WHERE label=? AND team_count<=? ORDER BY r",
                                (str(action_target["targetLabel"]), args.max_team_count),
                            )
                        ]
                    for target in routed_targets:
                            pair = (str(target["targetLabel"]), int(target["targetR"]))
                            target_row = connection.execute(
                                "SELECT team_count FROM targets WHERE label=? AND r=?", pair
                            ).fetchone()
                            if target_row is None or int(target_row["team_count"]) > args.max_team_count:
                                continue
                            if connection.execute(
                                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
                            ).fetchone():
                                continue
                            if connection.execute(
                                "SELECT 1 FROM verifications WHERE label=? AND r=? AND status='accepted'",
                                pair,
                            ).fetchone():
                                continue
                            live_pairs[pair] = int(target_row["team_count"])
                    if not live_pairs:
                        continue
                    sources = connection.execute(
                        """
                        SELECT p.submission_id,p.polynomial_index,p.coefficient_hash,
                               LENGTH(p.coefficients) AS coefficient_bytes,
                               v.field_disc_abs
                        FROM polynomials p JOIN verifications v
                          USING(submission_id,polynomial_index)
                        WHERE v.label=? AND v.r=? AND v.status='accepted'
                        ORDER BY CASE WHEN v.field_disc_abs IS NULL THEN 1 ELSE 0 END,
                                 LENGTH(v.field_disc_abs),v.field_disc_abs,
                                 LENGTH(p.coefficients),p.submission_id,p.polynomial_index
                        LIMIT ?
                        """,
                        (source_label, source_r, args.sources_per_signature),
                    ).fetchall()
                    for source_ordinal, source in enumerate(sources, start=1):
                        source_key = (
                            str(source["submission_id"]),
                            int(source["polynomial_index"]),
                            str(orbit_map.relative_to(ROOT)),
                        )
                        if source_key in excluded_sources:
                            continue
                        value = sum(
                            (Fraction(1, 2**team_count) for team_count in live_pairs.values()),
                            Fraction(0, 1),
                        )
                        minimum_team_count = min(live_pairs.values())
                        targets = [
                            {"label": label, "r": r, "teamCount": live_pairs[(label, r)]}
                            for label, r in sorted(
                                live_pairs,
                                key=lambda pair: (live_pairs[pair], int(pair[0][3:]), pair[1]),
                            )
                        ]
                        job = {
                            "coefficientBytes": int(source["coefficient_bytes"]),
                            "coefficientSha256": str(source["coefficient_hash"]),
                            "jobId": hashlib.sha256(
                                f"{source['submission_id']}:{source['polynomial_index']}:{orbit_map.name}".encode()
                            ).hexdigest()[:20],
                            "length24OrbitCount": int(action["length24OrbitCount"]),
                            "minimumTeamCount": minimum_team_count,
                            "orbitMap": str(orbit_map.relative_to(ROOT)),
                            "orbitMapSha256": hashlib.sha256(orbit_map.read_bytes()).hexdigest(),
                            "potentialPairCount": len(live_pairs),
                            "potentialValueExact": str(value),
                            "potentialValueFloat": float(value),
                            "sourceFieldDiscriminantAbs": source["field_disc_abs"],
                            "sourceLabel": source_label,
                            "sourceOrdinalWithinSignature": source_ordinal,
                            "sourcePolynomialIndex": int(source["polynomial_index"]),
                            "sourceR": source_r,
                            "sourceSubmissionId": str(source["submission_id"]),
                            "targets": targets,
                        }
                        jobs.append(job)
                        all_pairs.update(live_pairs)
    finally:
        connection.close()

    jobs.sort(
        key=lambda row: (
            int(row["minimumTeamCount"]),
            -float(row["potentialValueFloat"]),
            -int(row["potentialPairCount"]),
            int(row["coefficientBytes"]),
            int(str(row["sourceLabel"])[3:]),
            int(row["sourceR"]),
        )
    )
    for ordinal, job in enumerate(jobs, start=1):
        job["jobOrdinal"] = ordinal
    rendered = "".join(json.dumps(row, sort_keys=True) + "\n" for row in jobs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    total_ceiling = sum(
        (
            Fraction(
                1,
                2
                ** int(
                    connection_team_count
                ),
            )
            for connection_team_count in []
        ),
        Fraction(0, 1),
    )
    # Reopen only to score the union of pairs once; job potentials overlap.
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as score_connection:
        pair_ceiling = sum(
            (
                Fraction(
                    1,
                    2
                    ** int(
                        score_connection.execute(
                            "SELECT team_count FROM targets WHERE label=? AND r=?", pair
                        ).fetchone()[0]
                    ),
                )
                for pair in all_pairs
            ),
            Fraction(0, 1),
        )
    summary = {
        "distinctPotentialPairs": len(all_pairs),
        "excludedSourceActions": len(excluded_sources),
        "jobCount": len(jobs),
        "manifestSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "maximumTeamCount": args.max_team_count,
        "orbitMaps": [str(path.relative_to(ROOT)) for path in maps],
        "excludedPlans": [str(path.resolve().relative_to(ROOT)) for path in args.exclude_plan],
        "pairContentionCeilingExact": str(pair_ceiling),
        "pairContentionCeilingFloat": float(pair_ceiling),
        "plan": str(args.output.resolve().relative_to(ROOT)),
        "sourcesPerSignature": args.sources_per_signature,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Light-only raid audit for one configured opponent's cached sole placements.

The program joins the latest complete local public-placement crawl to the
current read-only ledger, every recoverable receipt/outbox exclusion, the
complete allowlisted exact-candidate corpus, sealed unordered-pair actions,
generic twist actions, lower-Kummer actions, and certified index-24 actions.

It never runs Sage/GAP, performs network traffic, writes coefficients, stages
a manifest, or submits.  A stale public-placement crawl is a hard execution
gate: runbooks remain descriptive until a fresh crawl and a new audit agree.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as exclusions
import run_low_contention_sequential as lane
import stage_all_exact_frobenius_unowned as exact_frobenius
import stage_exact_shared_census as exact_shared
import stage_single_exact_census as exact_single
import stage_v14_negative_twist as negative_twist


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
TEAM_NAME = "Low hanging fruit"
TEAM_NUMBER = "IGP24-T00013"
TEAM_ID = "teamv2_26ddfb8c4e1e4193a4075695b88c5fb0"
MAX_PUBLIC_CACHE_AGE_SECONDS = 6 * 60 * 60
TEAM_ID_RE = re.compile(r"teamv2_[0-9a-f]{32}\Z")
TEAM_NUMBER_RE = re.compile(r"IGP24-T[0-9]{5}\Z")

CERTIFICATE = DATA / "rank11_low_hanging_fruit_raid_refresh_v2_certificate.json"
SUMMARY = DATA / "rank11_low_hanging_fruit_raid_refresh_v2_summary.json"
PLAN = DATA / "rank11_low_hanging_fruit_raid_refresh_v2_plan.json"

TWIST_ACTIONS = DATA / "agent_gold_b_even_twist_action_map.jsonl"
INDEX24_ACTIONS = DATA / "agent_index24_isomorphism_complete.jsonl"
KUMMER_ACTIONS = (
    DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl",
    DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl",
    *(DATA / f"agent_f5_full_ledger_pair_product_actions_shard{i}of4.jsonl"
      for i in range(4)),
)


@dataclass(frozen=True)
class Opponent:
    team_id: str
    team_number: str
    team_name: str

    def public(self) -> dict[str, str]:
        return {
            "teamId": self.team_id,
            "teamNumber": self.team_number,
            "teamName": self.team_name,
        }


DEFAULT_OPPONENT = Opponent(TEAM_ID, TEAM_NUMBER, TEAM_NAME)


def validate_opponent(
    team_id: object,
    team_number: object,
    team_name: object,
) -> Opponent:
    if not all(isinstance(value, str) for value in (team_id, team_number, team_name)):
        raise ValueError("opponent identity values must be strings")
    opponent = Opponent(team_id, team_number, team_name)
    if TEAM_ID_RE.fullmatch(opponent.team_id) is None:
        raise ValueError("invalid opponent --team-id")
    if TEAM_NUMBER_RE.fullmatch(opponent.team_number) is None:
        raise ValueError("invalid opponent --team-number")
    if (
        not opponent.team_name
        or opponent.team_name != opponent.team_name.strip()
        or any(not character.isprintable() for character in opponent.team_name)
    ):
        raise ValueError("invalid opponent --team-name")
    return opponent


def opponent_output_defaults(opponent: Opponent) -> tuple[Path, Path, Path]:
    if opponent == DEFAULT_OPPONENT:
        return CERTIFICATE, SUMMARY, PLAN
    slug = opponent.team_number.lower().replace("-", "_")
    prefix = DATA / f"{slug}_sole_pair_raid"
    return (
        prefix.with_name(prefix.name + "_certificate.json"),
        prefix.with_name(prefix.name + "_summary.json"),
        prefix.with_name(prefix.name + "_plan.json"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-id", default=TEAM_ID)
    parser.add_argument("--team-number", default=TEAM_NUMBER)
    parser.add_argument("--team-name", default=TEAM_NAME)
    parser.add_argument(
        "--certificate", type=Path,
        help="output certificate (default is scoped to --team-number)",
    )
    parser.add_argument(
        "--summary", type=Path,
        help="output summary (default is scoped to --team-number)",
    )
    parser.add_argument(
        "--plan", type=Path,
        help="output plan (default is scoped to --team-number)",
    )
    args = parser.parse_args()
    args.opponent = validate_opponent(
        args.team_id, args.team_number, args.team_name
    )
    defaults = opponent_output_defaults(args.opponent)
    args.certificate = args.certificate or defaults[0]
    args.summary = args.summary or defaults[1]
    args.plan = args.plan or defaults[2]
    return args


def read_jsonl(path: Path):
    for record, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{record}")
            yield record, value


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def public_pair(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def holder_discriminant(placement: dict) -> int | None:
    value = placement.get("scoring_disc_abs") or placement.get("minimum_disc_abs")
    return int(value) if value is not None else None


def head_to_head_projection(
    candidate_field_disc: int | None, holder_field_disc: int | None
) -> dict:
    """Return the contest's cached sole-holder comparison projection.

    A new polynomial earns 0.5*min(1,log(D_holder)/log(D_candidate)) on a
    two-team pair.  Relative to the opponent's existing 0.5, the maximum net
    head-to-head swing is therefore 1.0.
    """

    if (
        candidate_field_disc is None
        or holder_field_disc is None
        or candidate_field_disc <= 1
        or holder_field_disc <= 1
    ):
        return {
            "candidateScoreAfterJoin": None,
            "projectedNetRelativeSwing": None,
            "maximumNetRelativeSwing": 1.0,
        }
    ratio = min(
        1.0,
        math.log(holder_field_disc) / math.log(candidate_field_disc),
    )
    candidate_score = 0.5 * ratio
    return {
        "candidateScoreAfterJoin": candidate_score,
        "projectedNetRelativeSwing": 0.5 + candidate_score,
        "maximumNetRelativeSwing": 1.0,
    }


def latest_public_placements(
    connection: sqlite3.Connection,
    now: datetime,
    opponent: Opponent = DEFAULT_OPPONENT,
) -> tuple[dict, dict[tuple[str, int], dict]]:
    crawl = connection.execute(
        """
        SELECT * FROM public_team_placement_crawls
        WHERE complete=1 AND scope='unique' AND team_id=?
          AND team_number=? AND team_name=?
        ORDER BY crawl_id DESC LIMIT 1
        """,
        (opponent.team_id, opponent.team_number, opponent.team_name),
    ).fetchone()
    if crawl is None:
        raise ValueError(
            "no complete public placement crawl for "
            f"{opponent.team_number} / {opponent.team_name!r}"
        )
    crawl = dict(crawl)
    if (
        str(crawl["team_id"]) != opponent.team_id
        or str(crawl["team_number"]) != opponent.team_number
        or str(crawl["team_name"]) != opponent.team_name
        or str(crawl["scope"]) != "unique"
    ):
        raise ValueError("public placement crawl opponent identity changed")
    placements = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute(
            "SELECT * FROM public_team_placements "
            "WHERE crawl_id=? AND k_teams=1",
            (int(crawl["crawl_id"]),),
        )
    }
    if (
        int(crawl["complete"]) != 1
        or int(crawl["placement_rows"]) != int(crawl["unique_rows"])
        or int(crawl["unique_rows"]) != len(placements)
        or any(int(row["k_teams"]) != 1 for row in placements.values())
        or any(
            str(row["team_id"]) != opponent.team_id
            or str(row["team_number"]) != opponent.team_number
            or str(row["team_name"]) != opponent.team_name
            for row in placements.values()
        )
    ):
        raise ValueError("public sole-placement cache is incomplete or inconsistent")
    generated_raw = crawl.get("leaderboard_generated_at")
    if generated_raw in (None, "", "unavailable"):
        # The current placements endpoint does not return a leaderboard
        # timestamp.  The completed crawl time is the conservative freshness
        # anchor for this read-only snapshot.
        generated_raw = crawl.get("completed_at")
        crawl["freshnessTimestampSource"] = "completed_at"
    else:
        crawl["freshnessTimestampSource"] = "leaderboard_generated_at"
    generated = parse_timestamp(str(generated_raw))
    age = (now - generated).total_seconds()
    if age < -300:
        raise ValueError("public placement cache is unexpectedly in the future")
    crawl["ageSecondsAtAudit"] = max(0, int(age))
    crawl["freshAtAudit"] = 0 <= age <= MAX_PUBLIC_CACHE_AGE_SECONDS
    return crawl, placements


def validate_public_placement_snapshot(
    crawl: dict,
    placements: dict[tuple[str, int], dict],
    opponent: Opponent,
) -> dict:
    """Pin the JSONL backing the selected DB crawl and rejoin every sole pair."""

    snapshot_path = Path(str(crawl.get("jsonl_path") or "")).expanduser().resolve()
    if (
        not snapshot_path.is_relative_to(DATA)
        or not snapshot_path.is_file()
    ):
        raise ValueError("public placement snapshot is absent or outside project data")
    snapshot_rows: dict[tuple[str, int], dict] = {}
    for record, row in read_jsonl(snapshot_path):
        if (
            row.get("teamId") != opponent.team_id
            or row.get("teamNumber") != opponent.team_number
            or row.get("teamName") != opponent.team_name
        ):
            raise ValueError(
                f"public placement snapshot opponent mismatch at record {record}"
            )
        try:
            pair = (str(row["label"]), int(row["r"]))
            k_teams = int(row["kTeams"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"malformed public placement snapshot at record {record}"
            ) from exc
        if pair in snapshot_rows or k_teams != 1:
            raise ValueError("public placement snapshot is nonunique or not sole-held")
        snapshot_rows[pair] = row
    if set(snapshot_rows) != set(placements):
        raise ValueError("public placement snapshot pair set differs from ledger crawl")
    for pair, placement in placements.items():
        row = snapshot_rows[pair]
        expected = (
            float(placement["points"]),
            (
                str(placement["scoring_disc_abs"])
                if placement.get("scoring_disc_abs") is not None else None
            ),
            (
                str(placement["minimum_disc_abs"])
                if placement.get("minimum_disc_abs") is not None else None
            ),
        )
        actual = (
            float(row["points"]),
            (
                str(row["scoringDiscAbs"])
                if row.get("scoringDiscAbs") is not None else None
            ),
            (
                str(row["minScoringDiscAbs"])
                if row.get("minScoringDiscAbs") is not None else None
            ),
        )
        if actual != expected:
            raise ValueError(
                f"public placement snapshot differs from ledger crawl at {public_pair(pair)}"
            )
    return lane.artifact(snapshot_path)


def merge_exact(
    pool: dict[str, dict],
    digest: str,
    pair: tuple[str, int],
    field_disc: object,
    families: set[str],
    proofs: list[dict],
    source_keys: set[tuple[str, int, str, int]],
    coefficient_bytes: int,
) -> None:
    field = int(field_disc) if field_disc is not None else None
    incumbent = pool.get(digest)
    if incumbent is None:
        pool[digest] = {
            "coefficientSha256": digest,
            "pair": pair,
            "fieldDiscriminantAbs": field,
            "families": set(families),
            "proofs": list(proofs),
            "sourceKeys": set(source_keys),
            "coefficientBytes": int(coefficient_bytes),
        }
        return
    if incumbent["pair"] != pair:
        raise ValueError(f"conflicting exact pair for {digest}")
    old_field = incumbent["fieldDiscriminantAbs"]
    if old_field is not None and field is not None and old_field != field:
        raise ValueError(f"conflicting field discriminant for {digest}")
    if old_field is None:
        incumbent["fieldDiscriminantAbs"] = field
    incumbent["families"].update(families)
    incumbent["proofs"].extend(proofs)
    incumbent["sourceKeys"].update(source_keys)
    incumbent["coefficientBytes"] = min(
        incumbent["coefficientBytes"], int(coefficient_bytes)
    )


def exact_corpus(
    connection: sqlite3.Connection,
) -> tuple[dict[str, dict], list[dict], dict, dict[str, set[tuple[str, int]]]]:
    single_rows, single_meta = exact_single.scan_candidates(DATA)
    validated_single = [
        row for row in single_rows
        if exact_single.validate_source_pins(connection, row)
    ]
    pool: dict[str, dict] = {}
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for row in validated_single:
        digest = str(row["coefficientSha256"])
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        pair_index[digest].add(pair)
        merge_exact(
            pool,
            digest,
            pair,
            row.get("fieldDiscriminantAbs"),
            {str((row.get("proof") or {}).get("schema", "single_exact"))},
            [row.get("proof") or {}],
            {
                (
                    str(pin["submissionId"]),
                    int(pin["polynomialIndex"]),
                    str(pin["label"]),
                    int(pin["r"]),
                )
                for pin in row.get("sourcePins") or []
            },
            len(str(row["coefficientLine"]).encode("ascii")),
        )

    stable_pool, stable_meta = exact_shared.scan_stable_multi(
        DATA, DATA / "pair_signature_map.jsonl"
    )
    valid_stable = 0
    for digest, row in stable_pool.items():
        if not all(
            exact_shared.source_evidence(connection, tuple(key)) is not None
            for key in row["sourceKeys"]
        ):
            continue
        valid_stable += 1
        pair = tuple(row["pair"])
        pair_index[digest].add(pair)
        merge_exact(
            pool,
            digest,
            pair,
            row.get("fieldDiscriminantAbs"),
            set(row["families"]),
            list(row["proofs"]),
            set(row["sourceKeys"]),
            int(row["coefficientBytes"]),
        )

    certificates, _staged = exact_frobenius.scan_json_artifacts(DATA)
    frobenius_pool, _certificate_audit, frobenius_meta = (
        exact_frobenius.collect_exact_pool(
            certificates, connection, ROOT, expected_certificates=None
        )
    )
    for digest, row in frobenius_pool.items():
        fields = row["fieldDiscriminants"]
        field = next(iter(fields)) if fields else None
        pair = tuple(row["pair"])
        pair_index[digest].add(pair)
        source_keys = {
            (
                str(proof["sourceSubmissionId"]),
                int(proof["sourcePolynomialIndex"]),
                str(proof["sourceLabel"]),
                int(proof["sourceR"]),
            )
            for proof in row["proofs"]
        }
        merge_exact(
            pool,
            digest,
            pair,
            field,
            {"exact_frobenius"},
            list(row["proofs"]),
            source_keys,
            int(row["coefficientBytes"]),
        )

    supplemental, supplemental_artifacts = exclusions.supplemental_exact_pair_index()
    for digest, pairs in supplemental.items():
        pair_index[digest].update(pairs)
    meta = {
        "singleOccurrencesScanned": len(single_rows),
        "singleOccurrencesWithValidAcceptedSources": len(validated_single),
        "singleSchemaCounts": single_meta["acceptedSchemaCounts"],
        "stableUniqueHashesScanned": len(stable_pool),
        "stableUniqueHashesWithValidAcceptedSources": valid_stable,
        "stableMeta": stable_meta,
        "frobeniusUniqueHashes": len(frobenius_pool),
        "frobeniusMeta": frobenius_meta,
        "mergedUniqueExactHashes": len(pool),
        "supplementalPairMaps": supplemental_artifacts,
    }
    return pool, validated_single, meta, pair_index


def proof_artifacts(proofs: list[dict]) -> list[dict]:
    result = {}
    for proof in proofs:
        path = proof.get("artifact")
        digest = proof.get("artifactSha256")
        if not path:
            continue
        if digest is None:
            candidate = (ROOT / str(path)).resolve()
            if candidate.is_file():
                digest = exact_single.sha256_path(candidate)
        result[(str(path), str(digest))] = {
            "path": str(path), "sha256": str(digest)
        }
    return [result[key] for key in sorted(result)]


def even_anchors(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        WITH ranked AS (
          SELECT v.submission_id,v.polynomial_index,v.label,v.r,
                 p.coefficient_hash,p.coefficients,
                 length(p.coefficients) AS coefficient_bytes,
                 ROW_NUMBER() OVER(
                   PARTITION BY p.coefficient_hash
                   ORDER BY length(p.coefficients),v.submission_id,
                            v.polynomial_index
                 ) AS row_number
          FROM verifications v JOIN polynomials p
          USING(submission_id,polynomial_index)
          WHERE v.status='accepted' AND v.scoreable=1
            AND v.label IS NOT NULL AND v.r IS NOT NULL
            AND length(p.coefficients)-length(replace(p.coefficients,',',''))=24
            AND NOT EXISTS (
              SELECT 1 FROM json_each('['||p.coefficients||']') j
              WHERE CAST(j.key AS INTEGER)%2=1
                AND CAST(j.value AS INTEGER)!=0
            )
        )
        SELECT * FROM ranked WHERE row_number=1
        ORDER BY coefficient_bytes,submission_id,polynomial_index
        """
    )
    return [dict(row) for row in rows]


def public_anchor(row: dict) -> dict:
    return {
        "submissionId": str(row["submission_id"]),
        "polynomialIndex": int(row["polynomial_index"]),
        "label": str(row["label"]),
        "r": int(row["r"]),
        "coefficientSha256": str(row["coefficient_hash"]),
        "coefficientBytes": int(row["coefficient_bytes"]),
        "ledgerStatus": "accepted_scoreable",
    }


def twist_routes(
    anchors: list[dict],
    eligible: set[tuple[str, int]],
    placements: dict[tuple[str, int], dict],
    tested_source_hashes: set[str],
) -> list[dict]:
    labels = {pair[0] for pair in eligible}
    actions = {}
    for _record, action in read_jsonl(TWIST_ACTIONS):
        targets = [str(value) for value in action.get("targetLabels") or []]
        if len(targets) == 1 and targets[0] in labels:
            actions[str(action["sourceLabel"])] = action
    result = []
    for anchor in anchors:
        action = actions.get(str(anchor["label"]))
        digest = str(anchor["coefficient_hash"])
        if action is None or digest in tested_source_hashes:
            continue
        coefficients = [int(value) for value in str(anchor["coefficients"]).split(",")]
        quotient_r, sturm_length = negative_twist.exact_real_root_count(
            coefficients[::2]
        )
        target_label = str(action["targetLabels"][0])
        signatures = {
            "positive": int(anchor["r"]),
            "negative": 2 * quotient_r - int(anchor["r"]),
        }
        for sign, target_r in signatures.items():
            pair = (target_label, target_r)
            if pair not in eligible:
                continue
            holder = holder_discriminant(placements[pair])
            result.append(
                {
                    "family": "generic_quadratic_twist",
                    "status": "guarded_public_placement_refresh_required",
                    "source": public_anchor(anchor),
                    "targetPair": public_pair(pair),
                    "twistSign": sign,
                    "quotientRealRootCount": quotient_r,
                    "sturmSequenceLength": sturm_length,
                    "actionResolution": "all_block_systems_same_target",
                    "holderScoringDiscAbsAtCrawl": str(holder) if holder else None,
                    "projection": head_to_head_projection(None, holder),
                }
            )
    return result


def kummer_routes(
    anchors: list[dict],
    eligible: set[tuple[str, int]],
    placements: dict[tuple[str, int], dict],
    tested_source_hashes: set[str],
) -> tuple[list[dict], list[dict]]:
    labels = {pair[0] for pair in eligible}
    by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for anchor in anchors:
        digest = str(anchor["coefficient_hash"])
        if digest not in tested_source_hashes:
            by_pair[(str(anchor["label"]), int(anchor["r"]))].append(anchor)

    leads = []
    artifacts = []
    for path in KUMMER_ACTIONS:
        artifacts.append(lane.artifact(path))
        kind = "subset_product" if "subset" in path.name else "pair_product"
        for record, action in read_jsonl(path):
            target_label = str(action["targetLabel"])
            if target_label not in labels:
                continue
            source_label = str(action["sourceLabel"])
            mapping = action.get("sourceSignatureToPossibleTargetSignatures") or {}
            for source_r_text, values in mapping.items():
                outcomes = sorted({int(value) for value in values})
                source_r = int(source_r_text)
                anchor_rows = by_pair.get((source_label, source_r), [])
                if len(outcomes) != 1 or not anchor_rows:
                    continue
                pair = (target_label, outcomes[0])
                if pair not in eligible:
                    continue
                holder = holder_discriminant(placements[pair])
                leads.append(
                    {
                        "family": f"lower_kummer_{kind}",
                        "status": "structural_deterministic_missing_general_worker",
                        "source": public_anchor(anchor_rows[0]),
                        "targetPair": public_pair(pair),
                        "deterministicTargetSignature": True,
                        "actionArtifact": {
                            "path": str(path.relative_to(ROOT)),
                            "sha256": artifacts[-1]["sha256"],
                            "record": record,
                        },
                        "holderScoringDiscAbsAtCrawl": str(holder) if holder else None,
                        "projection": head_to_head_projection(None, holder),
                        "executionSupport": (
                            "no reusable single-source arithmetic worker is "
                            "allowlisted; do not run historical hard-coded waves"
                        ),
                    }
                )
    unique = {}
    for lead in leads:
        key = (
            lead["source"]["coefficientSha256"],
            lead["targetPair"],
            lead["family"],
        )
        unique.setdefault(key, lead)
    ranked = sorted(
        unique.values(),
        key=lambda row: (
            -int(row["holderScoringDiscAbsAtCrawl"] or 0),
            int(row["source"]["coefficientBytes"]),
            row["targetPair"],
            row["family"],
        ),
    )
    return ranked, artifacts


def index24_routes(
    anchors: list[dict],
    eligible: set[tuple[str, int]],
    placements: dict[tuple[str, int], dict],
) -> list[dict]:
    labels = {pair[0] for pair in eligible}
    by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for anchor in anchors:
        by_pair[(str(anchor["label"]), int(anchor["r"]))].append(anchor)
    leads = []
    for record, action in read_jsonl(INDEX24_ACTIONS):
        if (
            action.get("status") != "certified_isomorphic"
            or str(action.get("targetLabel")) not in labels
        ):
            continue
        source_label = str(action["sourceLabel"])
        target_label = str(action["targetLabel"])
        for source_r in sorted({int(value) for value in action.get("sourceR") or []}):
            source_anchors = by_pair.get((source_label, source_r), [])
            if not source_anchors:
                continue
            for subgroup_index, subgroup in enumerate(action.get("subgroupClasses") or []):
                profiles = [
                    profile for profile in subgroup.get("profiles") or []
                    if int(profile["sourceR"]) == source_r
                ]
                target_rs = {int(profile["targetR"]) for profile in profiles}
                if not profiles or len(target_rs) != 1:
                    continue
                pair = (target_label, next(iter(target_rs)))
                if pair not in eligible:
                    continue
                holder = holder_discriminant(placements[pair])
                leads.append(
                    {
                        "family": "index24_core_free_relative_action",
                        "status": "structural_deterministic_missing_arithmetic_worker",
                        "source": public_anchor(source_anchors[0]),
                        "targetPair": public_pair(pair),
                        "actionArtifact": {
                            "path": str(INDEX24_ACTIONS.relative_to(ROOT)),
                            "record": record,
                            "subgroupClassIndex": subgroup_index,
                        },
                        "compatibleProfileCount": len(profiles),
                        "holderScoringDiscAbsAtCrawl": str(holder) if holder else None,
                        "projection": head_to_head_projection(None, holder),
                    }
                )
    return leads


def make_pair_runbook(
    route: dict,
    placement: dict,
    certificate_path: Path,
    opponent: Opponent = DEFAULT_OPPONENT,
) -> dict:
    source_pair = route["sourcePair"]
    target_pair = next(iter(route["lowOutcomes"]))
    anchor = route["anchor"]
    route_prefix = (
        "rank11_raid"
        if opponent == DEFAULT_OPPONENT
        else f"{opponent.team_number.lower().replace('-', '_')}_raid"
    )
    route_id = (
        f"{route_prefix}_{source_pair[0]}_r{source_pair[1]}_to_"
        f"{target_pair[0]}_r{target_pair[1]}"
    )
    output = DATA / f"{route_id}_result.jsonl"
    if output.exists() or output.with_suffix(output.suffix + ".tmp").exists():
        raise ValueError(f"isolated runbook output already exists: {output}")
    action_path = ROOT / route["actionArtifact"]["path"]
    command = [
        "/usr/local/bin/sage", "-python", "pair_sum_one.sage.py",
        str(anchor["submissionId"]), str(anchor["polynomialIndex"]),
        "--orbit-map", str(action_path.relative_to(ROOT)),
        "--expected-source-hash", str(anchor["coefficientSha256"]),
        "--expected-target", target_pair[0],
        "--output-jsonl", str(output.relative_to(ROOT)),
    ]
    holder = holder_discriminant(placement)
    return {
        "routeId": route_id,
        "family": "unordered_pair_single_orbit",
        "status": "guarded_public_placement_refresh_required",
        "source": {
            "submissionId": str(anchor["submissionId"]),
            "polynomialIndex": int(anchor["polynomialIndex"]),
            "label": source_pair[0],
            "r": source_pair[1],
            "coefficientSha256": str(anchor["coefficientSha256"]),
            "coefficientBytes": int(anchor["coefficientBytes"]),
            "ledgerStatus": "accepted_scoreable",
            "freshAgainstCachedPairSumLineage": True,
        },
        "target": {
            "pair": public_pair(target_pair),
            "soleHolderTeamAtCrawl": opponent.team_name,
            "soleHolderTeamIdAtCrawl": opponent.team_id,
            "soleHolderTeamNumberAtCrawl": opponent.team_number,
            "kTeamsAtCrawl": int(placement["k_teams"]),
            "pointsAtCrawl": float(placement["points"]),
            "scoringDiscAbsAtCrawl": placement.get("scoring_disc_abs"),
            "minimumDiscAbsAtCrawl": placement.get("minimum_disc_abs"),
        },
        "projection": head_to_head_projection(None, holder),
        "exactAction": {
            "orbitIndex": int(route["orbitIndex"]),
            "length24OrbitCount": int(route["orbitCount"]),
            "deterministicAcrossCompatibleClasses": True,
            "compatibleClassCount": int(route["compatibleClassCount"]),
            "proofKind": str(route["proofKind"]),
            "actionArtifact": route["actionArtifact"],
            "profileArtifacts": route["profileArtifacts"],
        },
        "guards": {
            "refreshPublicPlacementsBeforeExecution": True,
            "rerunThisAuditAgainstNewCrawlBeforeExecution": True,
            "targetMustStillBeSolelyHeldByConfiguredOpponent": True,
            "targetMustRemainNonbaselineUnownedUnreceiptedUnoutboxed": True,
            "acceptedSourceKeyAndHashMustRemainExact": True,
            "outputAndTemporaryMustRemainAbsent": True,
            "runExactlyOneWorker": True,
            "submissionAuthorized": False,
        },
        "output": str(output.relative_to(ROOT)),
        "heavyCommand": command,
        "guardedShellCommandAfterFreshAuditOnly": (
            f"test ! -e {shlex.quote(str(output.relative_to(ROOT)))} && "
            + " ".join(shlex.quote(value) for value in command)
        ),
        "postflightCommandAfterWorker": [
            "python3", "validate_rank11_raid_pair_route.py",
            "--certificate", str(certificate_path.relative_to(ROOT)),
            "--route-id", route_id,
            "--result", str(output.relative_to(ROOT)),
            "--team-id", opponent.team_id,
            "--team-number", opponent.team_number,
            "--team-name", opponent.team_name,
        ],
    }


def main() -> int:
    args = parse_args()
    opponent = args.opponent
    output_paths = [path.resolve() for path in (args.certificate, args.summary, args.plan)]
    if len(set(output_paths)) != 3:
        raise ValueError("certificate, summary, and plan outputs must be distinct")
    if any(path.exists() for path in output_paths):
        raise FileExistsError("refusing to overwrite rank11 raid audit artifact")

    now = datetime.now(timezone.utc)
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        crawl, placements = latest_public_placements(connection, now, opponent)
        placement_snapshot = validate_public_placement_snapshot(
            crawl, placements, opponent
        )
        exact_pool, single_rows, exact_meta, pair_index = exact_corpus(connection)
        receipt_hashes, receipt_pairs, receipt_audit = exact_single.receipt_exclusions(
            lane.RECEIPTS, DATA, connection, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_index = exclusions.outbox_exclusions(
            pair_index, connection
        )
        known_exact_hashes = exact_single.query_known_hashes(
            connection, set(exact_pool)
        )

        snapshot = pair_routes.load_ledger_snapshot(connection)
        all_placements = set(placements)
        pair_exclusions = (
            snapshot["baseline"] | snapshot["owned"] | snapshot["knownPairs"]
            | receipt_pairs | outbox_pairs
        )
        eligible = all_placements - pair_exclusions
        hash_exclusions = known_exact_hashes | receipt_hashes | outbox_hashes

        exact_hits = []
        for digest, candidate in exact_pool.items():
            pair = candidate["pair"]
            if pair not in eligible or digest in hash_exclusions:
                continue
            holder = holder_discriminant(placements[pair])
            projection = head_to_head_projection(
                candidate["fieldDiscriminantAbs"], holder
            )
            exact_hits.append(
                {
                    "coefficientSha256": digest,
                    "coefficientBytes": int(candidate["coefficientBytes"]),
                    "targetPair": public_pair(pair),
                    "candidateFieldDiscriminantAbs": (
                        str(candidate["fieldDiscriminantAbs"])
                        if candidate["fieldDiscriminantAbs"] is not None else None
                    ),
                    "holderScoringDiscAbsAtCrawl": str(holder) if holder else None,
                    "families": sorted(candidate["families"]),
                    "proofArtifacts": proof_artifacts(candidate["proofs"]),
                    "sourceKeys": [
                        {
                            "submissionId": key[0], "polynomialIndex": key[1],
                            "label": key[2], "r": key[3],
                        }
                        for key in sorted(candidate["sourceKeys"])
                    ],
                    "projection": projection,
                    "status": "exact_ready_after_mandatory_public_refresh",
                }
            )
        exact_hits.sort(
            key=lambda row: (
                -(row["projection"]["projectedNetRelativeSwing"] or -1),
                int(row["candidateFieldDiscriminantAbs"] or 10**100),
                row["targetPair"],
            )
        )

        actions, action_provenance, action_artifacts = (
            pair_routes.load_sealed_actions()
        )
        profiles, profile_provenance, profile_artifacts = (
            pair_routes.load_profiles(actions, action_artifacts)
        )
        missing_action_sources = {
            pair for pair in snapshot["owned"] if pair[0] not in actions
        }
        route_snapshot = {
            **snapshot,
            "owned": snapshot["owned"] - missing_action_sources,
            "targets": {
                pair: {
                    "teamCount": 1,
                    "discovered": True,
                    "minimumDiscAbs": placements[pair].get("minimum_disc_abs"),
                    "generatedAt": str(crawl["leaderboard_generated_at"]),
                }
                for pair in placements
            },
        }
        single_pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for row in single_rows:
            single_pair_index[str(row["coefficientSha256"])].add(
                (str(row["targetLabel"]), int(row["targetR"]))
            )
        candidate_snapshot = {
            "candidates": single_rows,
            "candidatePairIndex": single_pair_index,
            "ambiguousHashes": {
                digest for digest, pairs in single_pair_index.items()
                if len(pairs) != 1
            },
            "knownCandidateHashes": exact_single.query_known_hashes(
                connection, set(single_pair_index)
            ),
            "receiptHashes": receipt_hashes | outbox_hashes,
            "receiptPairs": receipt_pairs | outbox_pairs,
        }
        routes, route_coverage = pair_routes.enumerate_routes(
            route_snapshot, actions, action_provenance, profiles,
            profile_provenance, candidate_snapshot["receiptPairs"]
        )
        lineage = pair_routes.cached_lineage(
            routes, candidate_snapshot, route_snapshot
        )
        for route in routes:
            route["targetTeamCounts"] = {
                public_pair(pair): 1 for pair in route["lowOutcomes"]
            }
            route["anchor"] = pair_routes.select_anchor(
                connection, route["sourcePair"], lineage["testedSourceKeys"],
                lineage["testedSourceHashes"]
            )
        executable_routes = [
            route for route in routes
            if route["deterministic"]
            and int(route["orbitCount"]) == 1
            and len(route["lowOutcomes"]) == 1
            and route["anchor"] is not None
        ]
        best_by_target = {}
        for route in sorted(executable_routes, key=pair_routes.route_rank):
            best_by_target.setdefault(next(iter(route["lowOutcomes"])), route)
        pair_runbooks = [
            make_pair_runbook(
                route, placements[target], args.certificate.resolve(), opponent
            )
            for target, route in best_by_target.items()
        ]

        anchors = even_anchors(connection)
        twist_tested = {
            str(pin["coefficientSha256"])
            for row in single_rows
            if (row.get("proof") or {}).get("schema")
            == "generic_quadratic_twist_exact_v1"
            for pin in row.get("sourcePins") or []
        }
        kummer_tested = {
            str(pin["coefficientSha256"])
            for row in single_rows
            if "kummer" in str((row.get("proof") or {}).get("schema", ""))
            for pin in row.get("sourcePins") or []
        }
        twists = twist_routes(anchors, eligible, placements, twist_tested)
        kummer, kummer_artifacts = kummer_routes(
            anchors, eligible, placements, kummer_tested
        )
        alternate = index24_routes(anchors, eligible, placements)
    finally:
        connection.close()

    refresh_required = not bool(crawl["freshAtAudit"])
    placement_rows = [
        [
            pair[0], pair[1], float(row["points"]), int(row["k_teams"]),
            row.get("scoring_disc_abs"), row.get("minimum_disc_abs"),
        ]
        for pair, row in sorted(placements.items(), key=lambda item: pair_routes.pair_sort_key(item[0]))
    ]
    plan = {
        "schemaVersion": "rank11-low-hanging-fruit-raid-plan-v1",
        "createdAt": now.isoformat(),
        "opponent": opponent.public(),
        "publicPlacementSnapshot": placement_snapshot,
        "status": (
            "guarded_stale_public_placement_cache"
            if refresh_required else "fresh_audit_ready_for_explicit_worker_selection"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "publicPlacementRefreshRequiredBeforeExecution": refresh_required,
        "exactReadyManifestPlan": exact_hits,
        "guardedOneWorkerRunbooks": pair_runbooks,
        "twistRunbooks": twists,
        "deterministicStructuralLeadsWithoutReusableWorker": kummer + alternate,
        "executionPolicy": {
            "refreshCrawlThenRerunAudit": True,
            "runAtMostOneGuardedWorkerAfterFreshAgreement": True,
            "stageOnlyAfterExactPostflight": True,
            "submissionAuthorized": False,
        },
    }
    lane.exclusive_json(args.plan.resolve(), plan)

    certificate = {
        "schemaVersion": "rank11-low-hanging-fruit-raid-audit-v1",
        "createdAt": now.isoformat(),
        "scope": (
            "cached_rank11_low_hanging_fruit_sole_pair_raid"
            if opponent == DEFAULT_OPPONENT
            else "cached_configured_opponent_sole_pair_raid"
        ),
        "opponent": opponent.public(),
        "status": plan["status"],
        "method": (
            "authoritative local public placement cache + current accepted ledger + "
            "complete exact corpus + sealed pair/twist/Kummer/index24 actions + "
            "all current receipt and txt-outbox exclusions"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "cacheFreshness": {
            "crawlId": int(crawl["crawl_id"]),
            "teamId": str(crawl["team_id"]),
            "teamNumber": str(crawl["team_number"]),
            "teamName": str(crawl["team_name"]),
            "scope": str(crawl["scope"]),
            "leaderboardGeneratedAt": str(crawl["leaderboard_generated_at"]),
            "crawlStartedAt": str(crawl["started_at"]),
            "crawlCompletedAt": str(crawl["completed_at"]),
            "pages": int(crawl["pages"]),
            "placementRows": int(crawl["placement_rows"]),
            "uniqueRows": int(crawl["unique_rows"]),
            "ageSecondsAtAudit": int(crawl["ageSecondsAtAudit"]),
            "ageHoursAtAudit": round(int(crawl["ageSecondsAtAudit"]) / 3600, 3),
            "maximumAllowedAgeSeconds": MAX_PUBLIC_CACHE_AGE_SECONDS,
            "freshAtAudit": bool(crawl["freshAtAudit"]),
            "refreshRequiredBeforeExecution": refresh_required,
            "pairSetSha256": pair_routes.canonical_digest(placement_rows),
            "placementSnapshot": placement_snapshot,
        },
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "baselinePairs": len(snapshot["baseline"]),
            "cachedOpponentSolePairs": len(placements),
            "eligibleOpponentPairsAfterAllExclusions": len(eligible),
            "receiptHashesExcluded": len(receipt_hashes),
            "receiptPairsExcluded": len(receipt_pairs),
            "outboxHashesExcluded": len(outbox_hashes),
            "outboxPairsExcluded": len(outbox_pairs),
        },
        "exactCorpus": {
            **exact_meta,
            "knownExactHashesExcluded": len(known_exact_hashes),
            "readyOpponentHits": len(exact_hits),
            "rejoinConclusion": (
                "no exact cached lineage survives the current pair/hash exclusions"
                if not exact_hits else "exact cached lineage survives; see plan"
            ),
        },
        "pairActionAudit": {
            "coverage": route_coverage,
            "acceptedPairsSkippedForMissingSealedActionLabel": len(missing_action_sources),
            "candidateRoutesTouchingOpponentPairs": len(routes),
            "deterministicSingleOrbitRoutesWithFreshAnchor": len(executable_routes),
            "distinctExecutableTargets": len(best_by_target),
            "cachedExactReadyCandidates": len(lineage["readyNovelLowCandidates"]),
            "cachedMissClosures": int(lineage["closedAnchorCount"]),
        },
        "otherActionAudit": {
            "genericTwistDeterministicFreshRoutes": len(twists),
            "lowerKummerDeterministicStructuralRoutes": len(kummer),
            "lowerKummerDistinctTargets": len({row["targetPair"] for row in kummer}),
            "index24DeterministicStructuralRoutes": len(alternate),
        },
        "ranking": {
            "exactHits": [
                "maximum projected net relative swing descending",
                "candidate field discriminant ascending",
                "target pair deterministic tie-break",
            ],
            "unknownDiscriminantConstructions": [
                "executable exact worker support before structural-only actions",
                "opponent holder discriminant descending",
                "accepted source coefficient bytes ascending",
            ],
            "scoreFormula": (
                "candidateScore=0.5*min(1,log(holderDisc)/log(candidateDisc)); "
                "netRelativeSwing=0.5+candidateScore; maximum=1.0"
            ),
        },
        "artifacts": {
            "auditProgram": lane.artifact(Path(__file__).resolve()),
            "sealedPairActions": action_artifacts,
            "pairProfiles": profile_artifacts,
            "twistActions": lane.artifact(TWIST_ACTIONS),
            "kummerActions": kummer_artifacts,
            "index24Actions": lane.artifact(INDEX24_ACTIONS),
            "plan": lane.artifact(args.plan.resolve()),
        },
        "receiptAudit": receipt_audit,
        "outboxAudit": {
            key: outbox_index[key]
            for key in (
                "outboxFiles", "nonemptyOutboxFiles", "canonicalPolynomialRows",
                "distinctCoefficientHashes", "distinctPairsExcluded",
                "coefficientHashSetSha256", "pairSetSha256",
            )
        },
        "checks": {
            "latestCompleteLocalPublicCrawlUsed": True,
            "publicCrawlContainsOnlySolePlacements": True,
            "allCurrentReceiptsExcluded": True,
            "allCurrentTxtOutboxesExcluded": True,
            "allExactSourcePinsRejoinedToAcceptedLedger": True,
            "coefficientPayloadOmitted": True,
            "staleCacheBlocksEveryWorker": (
                not refresh_required
                or all(
                    row["status"] == "guarded_public_placement_refresh_required"
                    for row in pair_runbooks + twists
                )
            ),
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "polynomialArithmeticRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "coefficientManifestWrites": 0,
        },
    }
    lane.exclusive_json(args.certificate.resolve(), certificate)

    summary = {
        "schemaVersion": "rank11-low-hanging-fruit-raid-summary-v1",
        "status": certificate["status"],
        "opponent": opponent.public(),
        "certificate": lane.artifact(args.certificate.resolve()),
        "plan": lane.artifact(args.plan.resolve()),
        "publicCrawlId": int(crawl["crawl_id"]),
        "leaderboardGeneratedAt": str(crawl["leaderboard_generated_at"]),
        "publicCacheAgeHours": certificate["cacheFreshness"]["ageHoursAtAudit"],
        "publicPlacementRefreshRequiredBeforeExecution": refresh_required,
        "opponentSolePairsAtCrawl": len(placements),
        "eligibleOpponentPairs": len(eligible),
        "exactCorpusUniqueHashes": len(exact_pool),
        "exactReadyHits": len(exact_hits),
        "guardedOneWorkerRunbooks": len(pair_runbooks),
        "twistRunbooks": len(twists),
        "kummerStructuralLeads": len(kummer),
        "kummerDistinctTargets": len({row["targetPair"] for row in kummer}),
        "index24StructuralLeads": len(alternate),
        "bestExecutableTargetPair": (
            pair_runbooks[0]["target"]["pair"] if pair_runbooks else None
        ),
        "bestStructuralTargetPair": (
            kummer[0]["targetPair"] if kummer else None
        ),
        "coefficientMaterialIncluded": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    lane.exclusive_json(args.summary.resolve(), summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileExistsError, ValueError, OSError, sqlite3.Error,
        json.JSONDecodeError, lane.GuardFailure,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

#!/usr/bin/env python3
"""Intersect the fresh high-value T00134 placement prefix with exact local proofs.

This audit is coefficient-free, offline, and read-only apart from its two
exclusive JSON outputs.  It treats the placement prefix as a bounded
opportunity set rather than a complete team snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PREFIX = DATA / "rank10_t00134_top5000_placements_20260730.jsonl"
CHECKPOINT = DATA / "rank10_t00134_top5000_checkpoint_20260730.json"
CERTIFICATE = DATA / "rank10_t00134_top5000_exact_intersection_certificate.json"
PLAN = DATA / "rank10_t00134_top5000_exact_intersection_plan.json"
TEAM_ID = "teamv2_32d618912a0e471fb418e886de946622"
TEAM_NUMBER = "IGP24-T00134"
TEAM_NAME = ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_prefix() -> tuple[list[dict], dict]:
    rows = [
        json.loads(line)
        for line in PREFIX.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    page_limit = int(checkpoint.get("pageLimit", -1))
    if (
        page_limit < 1
        or len(rows) != 100 * page_limit
        or checkpoint.get("complete") is not True
        or int(checkpoint.get("pages", -1)) != page_limit
        or len(checkpoint.get("rows", [])) != len(rows)
    ):
        raise ValueError("rank-10 placement prefix is incomplete")
    pairs: set[tuple[str, int]] = set()
    previous = math.inf
    for row in rows:
        if (
            row.get("teamId") != TEAM_ID
            or row.get("teamNumber") != TEAM_NUMBER
            or row.get("teamName") != TEAM_NAME
        ):
            raise ValueError("rank-10 placement prefix identity changed")
        pair = (str(row["label"]), int(row["r"]))
        if pair in pairs:
            raise ValueError(f"duplicate placement-prefix pair: {pair}")
        pairs.add(pair)
        points = float(row["points"])
        if not math.isfinite(points) or points <= 0 or points > previous + 1e-12:
            raise ValueError("placement prefix is not positive and descending")
        previous = points
        if int(row["kTeams"]) < 1:
            raise ValueError("placement prefix has an invalid team count")
    return rows, checkpoint


def candidate_ratio(candidate_disc: int | None, minimum_disc: int | None) -> float | None:
    if (
        candidate_disc is None
        or minimum_disc is None
        or candidate_disc <= 1
        or minimum_disc <= 1
    ):
        return None
    return min(1.0, math.log(minimum_disc) / math.log(candidate_disc))


def projection(candidate_disc: int | None, placement: dict) -> dict:
    k_teams = int(placement["kTeams"])
    holder_points = float(placement["points"])
    minimum_raw = placement.get("minScoringDiscAbs")
    minimum_disc = int(minimum_raw) if minimum_raw is not None else None
    ratio = candidate_ratio(candidate_disc, minimum_disc)
    maximum = (1.0 + holder_points) / (k_teams + 1)
    if ratio is None:
        return {
            "candidateScoreAfterJoin": None,
            "holderScoreReductionAfterJoin": holder_points / (k_teams + 1),
            "projectedNetRelativeSwing": None,
            "maximumNetRelativeSwing": maximum,
        }
    candidate_score = ratio / (k_teams + 1)
    holder_reduction = holder_points / (k_teams + 1)
    return {
        "candidateScoreAfterJoin": candidate_score,
        "holderScoreReductionAfterJoin": holder_reduction,
        "projectedNetRelativeSwing": candidate_score + holder_reduction,
        "maximumNetRelativeSwing": maximum,
    }


def rank_key(row: dict) -> tuple:
    value = row["projection"]["projectedNetRelativeSwing"]
    maximum = row["projection"]["maximumNetRelativeSwing"]
    return (
        -(float(value) if value is not None else float(maximum)),
        value is None,
        int(row["targetPair"]["kTeamsAtCrawl"]),
        int(row["coefficientBytes"]),
        str(row["coefficientSha256"]),
    )


def main() -> int:
    if CERTIFICATE.exists() or PLAN.exists():
        raise FileExistsError("refusing to overwrite rank-10 prefix audit artifacts")
    rows, checkpoint = read_prefix()
    placements = {(str(row["label"]), int(row["r"])): row for row in rows}
    now = datetime.now(timezone.utc)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exact_pool, _single_rows, exact_meta, pair_index = raid.exact_corpus(connection)
        receipt_hashes, receipt_pairs, receipt_meta = exact_single.receipt_exclusions(
            lane.RECEIPTS, DATA, connection, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
            pair_index, connection
        )
        known_hashes = exact_single.query_known_hashes(connection, set(exact_pool))
        snapshot = pair_routes.load_ledger_snapshot(connection)
        eligible = set(placements) - (
            snapshot["baseline"]
            | snapshot["owned"]
            | snapshot["knownPairs"]
            | receipt_pairs
            | outbox_pairs
        )
        excluded_hashes = known_hashes | receipt_hashes | outbox_hashes
        hits = []
        for digest, candidate in exact_pool.items():
            pair = tuple(candidate["pair"])
            if pair not in eligible or digest in excluded_hashes:
                continue
            placement = placements[pair]
            candidate_disc = candidate["fieldDiscriminantAbs"]
            hits.append(
                {
                    "coefficientSha256": str(digest),
                    "coefficientBytes": int(candidate["coefficientBytes"]),
                    "targetPair": {
                        "label": pair[0],
                        "r": pair[1],
                        "kTeamsAtCrawl": int(placement["kTeams"]),
                        "holderPointsAtCrawl": float(placement["points"]),
                        "holderScoringDiscAbsAtCrawl": placement.get(
                            "scoringDiscAbs"
                        ),
                        "minimumDiscAbsAtCrawl": placement.get(
                            "minScoringDiscAbs"
                        ),
                    },
                    "candidateFieldDiscriminantAbs": (
                        str(candidate_disc) if candidate_disc is not None else None
                    ),
                    "families": sorted(candidate["families"]),
                    "proofArtifacts": raid.proof_artifacts(candidate["proofs"]),
                    "sourceKeys": [
                        {
                            "submissionId": key[0],
                            "polynomialIndex": key[1],
                            "label": key[2],
                            "r": key[3],
                        }
                        for key in sorted(candidate["sourceKeys"])
                    ],
                    "projection": projection(candidate_disc, placement),
                    "status": "exact_unowned_unreceipted_unoutboxed",
                }
            )
    finally:
        connection.close()

    hits.sort(key=rank_key)
    best_by_pair: dict[tuple[str, int], dict] = {}
    for hit in hits:
        pair = (hit["targetPair"]["label"], int(hit["targetPair"]["r"]))
        best_by_pair.setdefault(pair, hit)
    selected = sorted(best_by_pair.values(), key=rank_key)
    known_swing = sum(
        float(row["projection"]["projectedNetRelativeSwing"])
        for row in selected
        if row["projection"]["projectedNetRelativeSwing"] is not None
    )
    maximum_swing = sum(
        float(row["projection"]["maximumNetRelativeSwing"]) for row in selected
    )
    plan = {
        "schemaVersion": "rank10-placement-prefix-exact-plan-v1",
        "createdAt": now.isoformat(),
        "status": "fresh_prefix_exact_intersection_ready",
        "team": {
            "teamId": TEAM_ID,
            "teamNumber": TEAM_NUMBER,
            "teamName": TEAM_NAME,
        },
        "selectedBestExactCandidatePerPair": selected,
        "allSurvivingExactCandidates": hits,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
    }
    lane.exclusive_json(PLAN, plan)
    certificate = {
        "schemaVersion": "rank10-placement-prefix-exact-audit-v1",
        "createdAt": now.isoformat(),
        "status": plan["status"],
        "team": plan["team"],
        "prefix": {
            "path": str(PREFIX.relative_to(ROOT)),
            "sha256": sha256(PREFIX),
            "checkpointPath": str(CHECKPOINT.relative_to(ROOT)),
            "checkpointSha256": sha256(CHECKPOINT),
            "completedAt": checkpoint.get("completedAt"),
            "pages": int(checkpoint["pages"]),
            "rows": len(rows),
            "pairSetSha256": hashlib.sha256(
                "\n".join(
                    f"{label}/{r}" for label, r in sorted(placements)
                ).encode()
            ).hexdigest(),
            "firstPoints": float(rows[0]["points"]),
            "lastPoints": float(rows[-1]["points"]),
        },
        "boundary": {
            "eligibleOpponentPairsAfterAllExclusions": len(eligible),
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "locallyOwnedScoreablePairs": len(snapshot["owned"]),
            "baselinePairs": len(snapshot["baseline"]),
            "knownExactHashesExcluded": len(known_hashes),
            "receiptHashesExcluded": len(receipt_hashes),
            "receiptPairsExcluded": len(receipt_pairs),
            "outboxHashesExcluded": len(outbox_hashes),
            "outboxPairsExcluded": len(outbox_pairs),
        },
        "exactCorpus": {
            **exact_meta,
            "mergedUniqueExactHashes": len(exact_pool),
            "survivingCandidateHashes": len(hits),
            "distinctSurvivingPairs": len(selected),
            "knownProjectedNetRelativeSwing": known_swing,
            "maximumNetRelativeSwing": maximum_swing,
        },
        "receiptAudit": receipt_meta,
        "outboxAudit": outbox_meta,
        "plan": {
            "path": str(PLAN.relative_to(ROOT)),
            "sha256": sha256(PLAN),
        },
        "checks": {
            "completeBoundedPrefixUsed": True,
            "allRowsPinnedAndOrdered": True,
            "baselineOwnedKnownReceiptAndOutboxExclusionsPassed": True,
            "oneBestCandidateSelectedPerTargetPair": True,
            "coefficientPayloadOmitted": True,
        },
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "coefficientManifestWrites": 0,
            "auditArtifactWrites": 2,
        },
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
    }
    lane.exclusive_json(CERTIFICATE, certificate)
    print(
        json.dumps(
            {
                "prefixRows": len(rows),
                "eligiblePairs": len(eligible),
                "exactHashes": len(exact_pool),
                "candidateHashes": len(hits),
                "candidatePairs": len(selected),
                "knownProjectedNetRelativeSwing": known_swing,
                "maximumNetRelativeSwing": maximum_swing,
                "certificate": str(CERTIFICATE),
                "plan": str(PLAN),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

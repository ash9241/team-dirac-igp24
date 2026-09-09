#!/usr/bin/env python3
"""Read-only exact live-pair audit over the complete local verified ledger."""

from __future__ import annotations

import glob
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ORBIT_MAP = DATA / "pair_orbit_map.jsonl"
PROFILE_PATHS = (
    DATA / "pair_signature_map.jsonl",
    DATA / "rank12_route_signature_profiles.jsonl",
    DATA / "agent_pair_sibling_shard2_profiles.jsonl",
    DATA / "agent_rank10_pair_stage2_profiles.jsonl",
)
R10 = Path("/private/tmp/rank10_raid/rank10_current_unique_placements.jsonl")
R11 = DATA / "low_hanging_fruit_unique_placements.jsonl"


def rows(path: Path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def holder_pairs(path: Path) -> set[tuple[str, int]]:
    return {
        (str(row["label"]), int(row["r"]))
        for row in rows(path)
        if int(row.get("kTeams", 0)) == 1
    }


def owned_pairs_for_labels(
    conn: sqlite3.Connection,
    labels: set[str],
    *,
    chunk_size: int = 400,
) -> set[tuple[str, int]]:
    """Use the pair index to probe only labels relevant to this audit.

    The verified ledger is much larger than the orbit-map source universe.
    Chunked ``IN`` probes prevent both global DISTINCT scans and SQLite's
    parameter limit while retaining exact local-ownership semantics.
    """

    ordered = sorted(labels)
    owned: set[tuple[str, int]] = set()
    for offset in range(0, len(ordered), chunk_size):
        chunk = ordered[offset : offset + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        owned.update(
            (str(label), int(r))
            for label, r in conn.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE scoreable=1 AND label IN (" + placeholders + ")",
                chunk,
            )
        )
    return owned


def main() -> None:
    conn = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    baseline = {(str(a), int(b)) for a, b in conn.execute("SELECT label,r FROM baseline_pairs")}
    team_counts = {
        (str(a), int(b)): int(n)
        for a, b, n in conn.execute("SELECT label,r,team_count FROM targets")
    }
    gold = {
        pair
        for pair, count in team_counts.items()
        if count == 0 and pair not in baseline
    }
    rank10_candidates = {
        pair
        for pair in holder_pairs(R10)
        if team_counts.get(pair) == 1 and pair not in baseline
    }
    rank11_candidates = {
        pair
        for pair in holder_pairs(R11)
        if team_counts.get(pair) == 1 and pair not in baseline
    }
    holder_owned = owned_pairs_for_labels(
        conn,
        {label for label, _r in rank10_candidates | rank11_candidates},
    )
    rank10 = rank10_candidates - holder_owned
    rank11 = rank11_candidates - holder_owned
    priority = gold | rank10 | rank11

    ready_artifacts = []
    certified_artifact_rows = 0
    for name in glob.glob(str(DATA / "*pair*.jsonl")):
        if any(token in name for token in ("18495", "profile", "orbit", "frontier")):
            continue
        for row in rows(Path(name)):
            if (
                row.get("status") != "certified"
                or not row.get("coefficientLine")
                or row.get("targetLabel") is None
                or row.get("targetR") is None
            ):
                continue
            certified_artifact_rows += 1
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            digest = hashlib.sha256(str(row["coefficientLine"]).encode("ascii")).hexdigest()
            if pair not in priority:
                continue
            if conn.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
            ).fetchone():
                continue
            ready_artifacts.append(
                {
                    "path": str(Path(name).relative_to(ROOT)),
                    "pair": [pair[0], pair[1]],
                    "coefficientSha256": digest,
                    "coefficientLine": str(row["coefficientLine"]),
                }
            )

    orbits = {}
    for row in rows(ORBIT_MAP):
        targets = list(row.get("targets") or [])
        if len(targets) == 1 and str(targets[0]["targetLabel"]) != str(row["sourceLabel"]):
            orbits[str(row["sourceLabel"])] = row
    profiles = {}
    for path in PROFILE_PATHS:
        for row in rows(path):
            if row.get("status") in (None, "certified"):
                profiles[str(row["sourceLabel"])] = row

    forced = {}
    source_pairs = sorted(owned_pairs_for_labels(conn, set(orbits)))
    direct_ledger = len(gold & set(source_pairs))
    for source_label, source_r_raw in source_pairs:
        source_label = str(source_label)
        source_r = int(source_r_raw)
        orbit = orbits.get(source_label)
        if orbit is None:
            continue
        target_label = str(orbit["targets"][0]["targetLabel"])
        if source_r == 24:
            pair = (target_label, 24)
        else:
            profile = profiles.get(source_label)
            if profile is None:
                continue
            compatible = [
                row for row in profile.get("profiles", [])
                if int(row["sourceR"]) == source_r
            ]
            realized = set()
            valid = bool(compatible)
            for class_row in compatible:
                signatures = list(class_row.get("orbitSignatures") or [])
                if len(signatures) != 1:
                    valid = False
                    break
                signature = signatures[0]
                if str(signature["targetLabel"]) != target_label:
                    valid = False
                    break
                realized.add((target_label, int(signature["targetR"])))
            if not valid or len(realized) != 1:
                continue
            pair = next(iter(realized))
        if pair in priority:
            forced[(source_label, source_r)] = pair

    tested_keys = set()
    for name in glob.glob(str(DATA / "*pair*.jsonl")):
        for row in rows(Path(name)):
            if row.get("status") not in ("certified", "certified_multi", "certified_staged"):
                continue
            submission = row.get("sourceSubmissionId", row.get("submissionId"))
            index = row.get("sourcePolynomialIndex", row.get("polynomialIndex"))
            if submission is not None and index is not None:
                tested_keys.add((str(submission), int(index)))

    routes = []
    for (source_label, source_r), target_pair in forced.items():
        source_rows = conn.execute(
            """
            SELECT v.submission_id,v.polynomial_index,p.coefficient_hash,
                   length(p.original_line) AS coefficient_bytes
            FROM verifications v JOIN polynomials p USING(submission_id,polynomial_index)
            WHERE v.scoreable=1 AND v.label=? AND v.r=?
            ORDER BY length(p.original_line),v.submission_id,v.polynomial_index
            """,
            (source_label, source_r),
        ).fetchall()
        for source in source_rows:
            key = (str(source["submission_id"]), int(source["polynomial_index"]))
            if key in tested_keys:
                continue
            kind = "gold" if target_pair in gold else "rank10" if target_pair in rank10 else "rank11"
            routes.append(
                {
                    "sourceSubmissionId": key[0],
                    "sourcePolynomialIndex": key[1],
                    "sourceLabel": source_label,
                    "sourceR": source_r,
                    "sourceCoefficientSha256": str(source["coefficient_hash"]),
                    "sourceCoefficientBytes": int(source["coefficient_bytes"]),
                    "targetLabel": target_pair[0],
                    "targetR": target_pair[1],
                    "targetKind": kind,
                }
            )

    routes.sort(
        key=lambda row: (
            {"gold": 0, "rank10": 1, "rank11": 2}[row["targetKind"]],
            row["sourceCoefficientBytes"],
            int(row["targetLabel"][3:]),
            row["targetR"],
        )
    )
    distinct_targets = {(r["targetLabel"], r["targetR"]) for r in routes}
    summary = {
        "currentGoldPairs": len(gold),
        "currentRank10SolePairsFromMonotoneEvidence": len(rank10),
        "currentRank11SolePairsFromMonotoneEvidence": len(rank11),
        "currentPriorityPairs": len(priority),
        "candidateOrbitSourceLabels": len(orbits),
        "directVerifiedGoldHitsAmongOrbitSources": direct_ledger,
        "queriedOwnedSourcePairs": len(source_pairs),
        "certifiedPairArtifactRowsAudited": certified_artifact_rows,
        "directCertifiedArtifactHits": len(ready_artifacts),
        "exactSingleNonselfOrbitLabels": len(orbits),
        "exactForcedPrioritySourcePairs": len(forced),
        "untestedExactOneStepSourcePolynomials": len(routes),
        "untestedExactOneStepDistinctTargets": len(distinct_targets),
        "routesByKind": {
            kind: sum(r["targetKind"] == kind for r in routes)
            for kind in ("gold", "rank10", "rank11")
        },
        "topRoutes": routes[:24],
        "readyArtifacts": ready_artifacts,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

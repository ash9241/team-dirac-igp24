#!/usr/bin/env python3
"""Close v14 conditional routes from exact cached construction lineage.

This audit is light and coefficient-free.  It reads coefficient-bearing cache
and receipt artifacts only to recompute hashes; raw coefficients are never
printed or written.  It does not import Sage/GAP, call the network, mutate the
ledger, construct factors, or submit.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
V14 = DATA / "autopilot_pair_delta_20260722_v14"
DB = DATA / "ledger.sqlite3"
CENSUS = V14 / "missing_pair_all.jsonl"
INVENTORY = V14 / "exact_source_inventory.json"
PLAN = V14 / "provenance_plan.json"
CANDIDATES = DATA / "team1_swing_candidates.jsonl"
FROBENIUS = DATA / "uncertified_multi_14921_r12_frobenius_certificate.json"
CERTIFICATE = V14 / "route_triage_certificate.json"
SUMMARY = V14 / "route_triage_summary.json"

PARENT = {
    "submissionId": "sub_2eb12f10d8804fb6ba4baeca24c81aea",
    "polynomialIndex": 771,
    "label": "24T14921",
    "r": 12,
}
CHILDREN = {
    ("24T15578", 12): {
        "factorIndex": 0,
        "coefficientSha256":
            "8a8057e0db4475182619b635cd8830f8872282a61cf710e0381a86ce07cc7d4d",
    },
    ("24T15712", 8): {
        "factorIndex": 1,
        "coefficientSha256":
            "34841db3b2f09deb7a1ddb974a206506f14eda06cd08d72cb98c4e418031cfca",
    },
}
CURRENT_SUBMISSION = "sub_e8301c0d8abe4765bd3d7d82ff0d1199"
CURRENT_RECEIPT = ROOT / f"receipts/{CURRENT_SUBMISSION}.json"
CURRENT_MANIFEST = ROOT / "outbox/uncertified_multi_14921_r12_shared.txt"
COEFFICIENT_LINE_RE = re.compile(r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])")


def relative(path: Path) -> dict:
    return helper.relative_artifact(path)


def canonical_hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def canonical_coefficient_hash(line: str) -> str:
    part = line.split("#", 1)[0].strip()
    values = [int(field.strip()) for field in part.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("receipt manifest row is not monic degree 24")
    return hashlib.sha256(",".join(str(value) for value in values).encode()).hexdigest()


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{DB.resolve()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def ledger_anchor(
    connection: sqlite3.Connection,
    submission_id: str,
    polynomial_index: int,
    label: str,
    r: int,
    expected_hash: str | None = None,
) -> str:
    row = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,"
        "v.in_baseline,v.scoring_status FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
        "AND p.polynomial_index=?",
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"missing ledger anchor: {submission_id}/{polynomial_index}")
    digest = str(row["coefficient_hash"])
    if expected_hash is not None and digest != expected_hash:
        raise ValueError("ledger anchor hash mismatch")
    if (
        str(row["status"]) != "accepted"
        or str(row["label"]) != label
        or int(row["r"]) != r
        or int(row["scoreable"] or 0) != 1
        or int(row["in_baseline"] or 0) != 0
        or str(row["scoring_status"]) != "scoreable"
    ):
        raise ValueError(f"ledger anchor is not accepted-scoreable: {label}/r{r}")
    return digest


def validate_census() -> dict[tuple[str, int], tuple[dict, dict]]:
    rows = helper.read_jsonl(CENSUS)
    if len(rows) != 13:
        raise ValueError(f"expected 13 v14 census rows, found {len(rows)}")
    pairs = {
        (str(row["sourceLabel"]), int(r))
        for row in rows for r in row.get("sourceR") or []
    }
    if len(pairs) != 14:
        raise ValueError(f"expected 14 v14 census signatures, found {len(pairs)}")
    if any(
        row.get("status") != "certified" or not helper.certificate_is_exact(row)
        for row in rows
    ):
        raise ValueError("v14 census has an error or invalid exact certificate")

    routes: dict[tuple[str, int], tuple[dict, dict]] = {}
    for row in rows:
        targets = {int(item["orbitIndex"]): item for item in row.get("targets") or []}
        for route in row.get("routes") or []:
            key = (str(row["sourceLabel"]), int(route["sourceR"]))
            target = targets.get(int(route["orbitIndex"]))
            if key in routes:
                raise ValueError(f"duplicate v14 route for {key}")
            if (
                key not in CHILDREN
                or str(route["targetLabel"]) != PARENT["label"]
                or target is None
                or str(target.get("targetLabel")) != PARENT["label"]
                or int(target.get("kernelOrder", -1)) != 1
                or int((row.get("targetCounts") or {}).get(PARENT["label"], 0)) != 1
            ):
                raise ValueError(f"v14 route is not the expected unique faithful reverse: {key}")
            routes[key] = (row, route)
    if set(routes) != set(CHILDREN):
        raise ValueError(f"unexpected v14 conditional-route set: {set(routes)}")
    return routes


def validate_inventory(
    connection: sqlite3.Connection,
) -> dict[tuple[str, int], dict]:
    inventory = helper.read_json(INVENTORY)
    if (
        inventory.get("schemaVersion") != "v14-accepted-scoreable-source-inventory-v1"
        or inventory.get("status") != "certified"
        or inventory.get("coefficientMaterialIncluded") is not False
    ):
        raise ValueError("v14 accepted-source inventory is not certified")
    anchors = inventory.get("acceptedAnchorsByPair") or {}
    result = {}
    for pair, spec in CHILDREN.items():
        rows = anchors.get(f"{pair[0]}:{pair[1]}") or []
        if len(rows) != 1:
            raise ValueError(f"v14 child inventory anchor is not unique: {pair}")
        row = rows[0]
        if (
            str(row["submissionId"]) != CURRENT_SUBMISSION
            or int(row["polynomialIndex"]) != spec["factorIndex"]
            or str(row["coefficientSha256"]) != spec["coefficientSha256"]
        ):
            raise ValueError(f"v14 child inventory provenance mismatch: {pair}")
        ledger_anchor(
            connection,
            str(row["submissionId"]),
            int(row["polynomialIndex"]),
            pair[0], pair[1],
            str(row["coefficientSha256"]),
        )
        result[pair] = row
    return result


def validate_receipt() -> dict:
    receipt = helper.read_json(CURRENT_RECEIPT)
    response = receipt.get("response") or {}
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != 2
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or response.get("submissionId") != CURRENT_SUBMISSION
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
        or Path(str(receipt.get("manifest"))).resolve() != CURRENT_MANIFEST.resolve()
        or str(receipt.get("manifestHash")) != helper.sha256_path(CURRENT_MANIFEST)
    ):
        raise ValueError("14921 packet receipt envelope mismatch")
    lines = CURRENT_MANIFEST.read_text(encoding="utf-8").splitlines()
    if len(lines) != 2:
        raise ValueError("14921 packet manifest does not contain exactly two rows")
    line_hashes = [canonical_coefficient_hash(line) for line in lines]
    expected = [
        CHILDREN[("24T15578", 12)]["coefficientSha256"],
        CHILDREN[("24T15712", 8)]["coefficientSha256"],
    ]
    if line_hashes != expected:
        raise ValueError("14921 packet receipt index/hash mapping mismatch")
    return {
        "receipt": relative(CURRENT_RECEIPT),
        "manifest": relative(CURRENT_MANIFEST),
        "submissionId": CURRENT_SUBMISSION,
        "polynomialIndexHashesRecomputed": True,
        "knownLocalHashesAtCommit": 0,
    }


def exact_construction_lineage(connection: sqlite3.Connection) -> dict:
    matches = [
        row for row in helper.read_jsonl(CANDIDATES)
        if str(row.get("sourceSubmissionId")) == PARENT["submissionId"]
        and int(row.get("sourcePolynomialIndex", -1)) == PARENT["polynomialIndex"]
        and str(row.get("sourceLabel")) == PARENT["label"]
        and int(row.get("sourceR", -1)) == PARENT["r"]
    ]
    if len(matches) != 1:
        raise ValueError("14921 cached construction row is not unique")
    candidate = matches[0]
    orbit = candidate.get("orbitCertificate") or {}
    if (
        candidate.get("status") != "certified_multi"
        or int(candidate.get("workerExitCode", -1)) != 0
        or orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or not orbit.get("exponents")
        or any(int(value) != 1 for value in orbit["exponents"])
    ):
        raise ValueError("14921 construction packet is not exact and squarefree")
    parent_hash = ledger_anchor(
        connection,
        PARENT["submissionId"], PARENT["polynomialIndex"],
        PARENT["label"], PARENT["r"],
    )

    candidates = {int(row["factorIndex"]): row for row in candidate.get("candidates") or []}
    if set(candidates) != {0, 1, 2}:
        raise ValueError("14921 construction packet does not have exactly three factors")
    for pair, child in CHILDREN.items():
        factor = candidates[child["factorIndex"]]
        if (
            str(factor.get("coefficientSha256")) != child["coefficientSha256"]
            or int(factor.get("targetR", -1)) != pair[1]
        ):
            raise ValueError(f"14921 cached factor mismatch: {pair}")

    certificate = helper.read_json(FROBENIUS)
    rows = certificate.get("rows") or []
    summary = certificate.get("summary") or {}
    if (
        str(certificate.get("inputSha256")) != helper.sha256_path(CANDIDATES)
        or int(summary.get("rows", -1)) != 1
        or int(summary.get("resolved", -1)) != 1
        or int(summary.get("unresolved", -1)) != 0
        or int(summary.get("contradiction", -1)) != 0
        or len(rows) != 1
    ):
        raise ValueError("14921 Frobenius certificate envelope is not exactly resolved")
    proof = rows[0]
    if (
        proof.get("status") != "resolved"
        or str(proof.get("sourceSubmissionId")) != PARENT["submissionId"]
        or int(proof.get("sourcePolynomialIndex", -1)) != PARENT["polynomialIndex"]
        or str(proof.get("sourceLabel")) != PARENT["label"]
        or int(proof.get("sourceR", -1)) != PARENT["r"]
    ):
        raise ValueError("14921 Frobenius source provenance mismatch")
    assignments = {int(row["factorIndex"]): row for row in proof.get("assignments") or []}
    if set(assignments) != {0, 1, 2}:
        raise ValueError("14921 Frobenius factor assignment is incomplete")
    expected_labels = {0: "24T15578", 1: "24T15712", 2: "24T15712"}
    for index, factor in candidates.items():
        assignment = assignments[index]
        if (
            str(assignment.get("coefficientSha256")) != str(factor["coefficientSha256"])
            or int(assignment.get("targetR", -1)) != int(factor["targetR"])
            or str(assignment.get("targetLabel")) != expected_labels[index]
        ):
            raise ValueError(f"14921 exact factor assignment mismatch: {index}")
    return {
        "method": "exact-resolved-multifactor-packet-and-unique-faithful-reverse-v1",
        "candidateArtifact": relative(CANDIDATES),
        "frobeniusCertificate": relative(FROBENIUS),
        "parent": {
            **PARENT,
            "coefficientSha256": parent_hash,
            "ledgerStatus": "accepted_scoreable_nonbaseline",
        },
        "packet": {
            "factorCount": 3,
            "factorAssignmentsResolved": 3,
            "squarefree": True,
            "submittedChildFactorIndexes": [0, 1],
        },
    }


def live_target_snapshot(connection: sqlite3.Connection, route: dict) -> dict:
    label = str(route["targetLabel"])
    rows = connection.execute(
        "SELECT r,team_count,discovered,generated_at FROM targets "
        "WHERE label=? ORDER BY r", (label,),
    ).fetchall()
    if not rows:
        raise ValueError(f"target cache lacks {label}")
    states = []
    live_gold = []
    for row in rows:
        r = int(row["r"])
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", (label, r)
        ).fetchone() is not None
        owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? "
            "AND status='accepted' AND scoreable=1 LIMIT 1", (label, r)
        ).fetchone() is not None
        if int(row["team_count"]) == 0 and not baseline and not owned:
            live_gold.append(r)
        states.append({
            "r": r,
            "teamCount": int(row["team_count"]),
            "discovered": bool(row["discovered"]),
            "owned": owned,
            "baseline": baseline,
        })
    frozen = sorted(int(value) for value in route.get("goldR") or [])
    if sorted(live_gold) != frozen:
        raise ValueError(f"v14 route target snapshot is stale: {frozen} != {live_gold}")
    return {
        "targetLabel": label,
        "generatedAt": sorted({str(row["generated_at"]) for row in rows}),
        "currentLiveGoldR": sorted(live_gold),
        "mappedTargetR": sorted(int(value) for value in route["mappedTargetR"]),
        "reachableLiveGoldR": sorted(
            set(live_gold) & {int(value) for value in route["mappedTargetR"]}
        ),
        "exactLineageTargetR": PARENT["r"],
        "targetState": states,
    }


def assert_coefficient_free(value: object) -> None:
    text = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if (
        COEFFICIENT_LINE_RE.search(text)
        or '"coefficients"' in text.lower()
        or '"coefficientline"' in text.lower()
        or "bearer " in text.lower()
        or '"authorization"' in text.lower()
    ):
        raise ValueError("route triage output contains coefficient or credential material")


def main() -> int:
    routes = validate_census()
    plan = helper.read_json(PLAN)
    if (
        plan.get("status") != "ready_for_one_heavy_worker"
        or int((plan.get("delta") or {}).get("selectedSignatures", -1)) != 14
    ):
        raise ValueError("v14 finalized plan envelope mismatch")

    connection = connect()
    try:
        target_rows, target_labels, target_pairs = connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT label),"
            "COUNT(DISTINCT label||':'||r) FROM targets"
        ).fetchone()
        if (int(target_rows), int(target_labels), int(target_pairs)) != (
            165_836, 25_000, 165_836
        ):
            raise ValueError("refreshed target cache is incomplete or non-unique")
        anchors = validate_inventory(connection)
        receipt = validate_receipt()
        lineage = exact_construction_lineage(connection)

        audit_rows = []
        for priority, pair in enumerate(sorted(CHILDREN), 1):
            census_row, route = routes[pair]
            live = live_target_snapshot(connection, route)
            exact_r = PARENT["r"]
            if exact_r in live["currentLiveGoldR"]:
                raise ValueError("exact reverse unexpectedly reaches a live-gold signature")
            anchor = anchors[pair]
            audit_rows.append({
                "priority": 0,
                "source": {
                    "submissionId": str(anchor["submissionId"]),
                    "polynomialIndex": int(anchor["polynomialIndex"]),
                    "coefficientSha256": str(anchor["coefficientSha256"]),
                    "label": pair[0],
                    "r": pair[1],
                    "ledgerStatus": "accepted_scoreable_nonbaseline",
                    "receiptProvenance": receipt,
                },
                "route": {
                    "censusExactCertificateSha256": str(
                        census_row["exactCertificateSha256"]
                    ),
                    "orbitIndex": int(route["orbitIndex"]),
                    "targetLabel": str(route["targetLabel"]),
                    "frozenGoldR": sorted(int(value) for value in route["goldR"]),
                    "mappedTargetR": sorted(int(value) for value in route["mappedTargetR"]),
                    "uniqueTargetCount": 1,
                    "kernelOrder": 1,
                },
                "liveSnapshot": live,
                "proofKind": "receipt_anchored_exact_14921_packet_faithful_reverse",
                "constructionFactorIndex": CHILDREN[pair]["factorIndex"],
                "forcedReversePair": f"{PARENT['label']}/r{PARENT['r']}",
                "exactTargetR": exact_r,
                "outcome": "closed_exact_signature_miss",
                "projectedMarginalScoreExact": "0",
            })
    finally:
        connection.close()

    certificate = {
        "schemaVersion": "v14-coefficient-free-route-triage-certificate-v1",
        "method": "receipt-anchored-exact-construction-lineage-and-live-audit-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "all_conditional_routes_closed_exact_lineage_misses",
        "inputs": {
            "census": relative(CENSUS),
            "exactSourceInventory": relative(INVENTORY),
            "finalizedPlan": relative(PLAN),
            "auditScript": relative(Path(__file__).resolve()),
            "ledger": {
                "path": str(DB.relative_to(ROOT)),
                "mode": "read_only_immutable_after_complete_target_refresh",
                "targetRows": int(target_rows),
                "targetLabels": int(target_labels),
                "uniqueTargetPairs": int(target_pairs),
            },
        },
        "counts": {
            "censusRows": 13,
            "censusSignatures": 14,
            "censusErrors": 0,
            "conditionalRoutes": 2,
            "cachedExactLineageClosures": 2,
            "exactMisses": 2,
            "exactHits": 0,
            "unresolvedExecutableRoutes": 0,
        },
        "sharedConstructionLineage": lineage,
        "routes": audit_rows,
        "prioritizedExecutableRoutes": [],
        "executionRecommendation": {
            "status": "no_factor_worker_needed",
            "reason": "both unique faithful reverse routes are forced to owned 24T14921/r12",
            "requiresRootHeavyWorkerClearance": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "polynomialArithmeticRuns": 0,
            "factorWorkersLaunched": 0,
        },
    }
    assert_coefficient_free(certificate)
    helper.atomic_json(CERTIFICATE, certificate)
    summary = {
        "schemaVersion": "v14-coefficient-free-route-triage-summary-v1",
        "status": certificate["status"],
        "census": {
            "rows": 13,
            "signatures": 14,
            "certified": 13,
            "errors": 0,
            "conditionalRoutes": 2,
            "safeRoutes": 0,
        },
        "routeTriage": {
            "cachedExactLineageClosures": 2,
            "exactSignatureMisses": 2,
            "unresolvedExecutableRoutes": 0,
            "prioritizedExecutableRoutes": [],
            "forcedReversePair": "24T14921/r12",
            "liveGoldR": [16, 20, 24],
        },
        "certificate": relative(CERTIFICATE),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": certificate["sideEffects"],
    }
    assert_coefficient_free(summary)
    helper.atomic_json(SUMMARY, summary)
    print(json.dumps({
        "status": certificate["status"],
        "conditionalRoutes": 2,
        "cachedLineageClosures": 2,
        "unresolvedExecutableRoutes": 0,
        "factorWorkersLaunched": 0,
        "certificate": str(CERTIFICATE.relative_to(ROOT)),
        "summary": str(SUMMARY.relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

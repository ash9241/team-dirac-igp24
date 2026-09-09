#!/usr/bin/env python3
"""Audit the four v13 conditional routes without Sage/GAP or submissions.

Three v13 sources are exact faithful pair children of cached accepted parents.
For those rows the unique reverse pair action recovers the accepted parent
signature and closes the route without repeating polynomial arithmetic.  The
fourth source has only non-invertible Kummer ancestry in the cache, so it is
left as the sole executable pair-resolvent route.

The certificate is coefficient-free.  Coefficient payloads are read only to
recompute their pinned SHA-256 digests; they are never printed or written.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
V13 = DATA / "autopilot_pair_delta_20260722_v13"
DB = DATA / "ledger.sqlite3"
CENSUS = V13 / "missing_pair_all.jsonl"
INVENTORY = V13 / "exact_source_inventory.json"
CERTIFICATE = DATA / "v13_route_lineage_certificate.json"
SUMMARY = DATA / "v13_route_lineage_summary.json"


ROUTES = [
    {
        "source": ("24T6221", 0),
        "target": "24T6471",
        "kind": "faithful_reverse_cached_multi_anchor",
        "exactTargetR": 0,
        "parent": (
            "sub_9ff8dd684fc54c65b2e93099fcad2098",
            13,
            "24T6471",
            0,
        ),
        "candidateArtifact": DATA / "agent_gold_a_remaining_multi_candidates.jsonl",
        "anchorArtifact": DATA / "autopilot_unresolved_frobenius_safe_20260722_certificate.json",
    },
    {
        "source": ("24T10977", 4),
        "target": "24T10979",
        "kind": "faithful_reverse_cached_single_child",
        "exactTargetR": 0,
        "parent": (
            "sub_9f3f5138fa1c41c69e375ecc0e1b4cd1",
            857,
            "24T10979",
            0,
        ),
        "candidateArtifact": DATA / "agent_gold_b_backfill_closure_complete_results.jsonl",
    },
    {
        "source": ("24T15528", 0),
        "target": "24T15240",
        "kind": "faithful_reverse_cached_single_child",
        "exactTargetR": 0,
        "parent": (
            "sub_d73e2097458649a9973f61c5094119d3",
            250,
            "24T15240",
            0,
        ),
        "candidateArtifact": DATA / "agent_gold_b_backfill_closure_complete_results.jsonl",
    },
    {
        "source": ("24T17513", 16),
        "target": "24T16970",
        "kind": "unresolved_pair_resolvent_required",
        "candidateArtifact": DATA / "agent_f5_full_ledger_safe_unique_orbit_root_sigalign_v9_results.jsonl",
        "generationArtifact": DATA / "agent_gold_c_lower_kummer_pair_product_final_results.jsonl",
    },
]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: dict) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return sha256_bytes(payload)


def canonical_coefficient_hash(line: str) -> str:
    part = line.split("#", 1)[0].strip()
    values = [int(field.strip()) for field in part.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("manifest source is not monic degree 24")
    canonical = ",".join(str(value) for value in values)
    return sha256_bytes(canonical.encode())


def relative(path: Path) -> dict:
    resolved = path.resolve()
    return {
        "path": str(resolved.relative_to(ROOT)),
        "sha256": sha256_path(resolved),
    }


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSONL objects: {path}")
            rows.append(value)
    return rows


def connect_immutable() -> sqlite3.Connection:
    # The autoresearch ledger is WAL-enabled.  The audit runs only after the
    # complete target refresh has checkpointed, and immutable mode prevents a
    # read-only audit from creating shared-memory sidecars.
    connection = sqlite3.connect(
        f"file:{DB.resolve()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def validate_census(rows: list[dict]) -> dict[tuple[str, int, str], tuple[dict, dict]]:
    routes = {}
    for row in rows:
        base = {key: value for key, value in row.items() if key != "exactCertificateSha256"}
        if row.get("status") != "certified" or canonical_sha256(base) != row.get(
            "exactCertificateSha256"
        ):
            raise ValueError(f"uncertified v13 census row: {row.get('sourceLabel')}")
        targets = {int(target["orbitIndex"]): target for target in row.get("targets") or []}
        for route in row.get("routes") or []:
            key = (
                str(row["sourceLabel"]),
                int(route["sourceR"]),
                str(route["targetLabel"]),
            )
            if key in routes:
                raise ValueError(f"duplicate v13 route: {key}")
            target = targets.get(int(route["orbitIndex"]))
            if (
                target is None
                or str(target["targetLabel"]) != key[2]
                or int(target.get("kernelOrder", -1)) != 1
                or int((row.get("targetCounts") or {}).get(key[2], 0)) != 1
            ):
                raise ValueError(f"v13 route is not a unique faithful action: {key}")
            routes[key] = (row, route)
    if len(routes) != 4:
        raise ValueError(f"expected four conditional routes, found {len(routes)}")
    return routes


def validate_ledger_anchor(
    connection: sqlite3.Connection,
    submission_id: str,
    polynomial_index: int,
    label: str,
    r: int,
    expected_hash: str | None = None,
) -> str:
    row = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
        "v.scoring_status FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
        "AND p.polynomial_index=?",
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"ledger anchor missing: {submission_id}/{polynomial_index}")
    digest = str(row["coefficient_hash"])
    if expected_hash is not None and digest != expected_hash:
        raise ValueError("ledger coefficient hash mismatch")
    if (
        str(row["status"]) != "accepted"
        or str(row["label"]) != label
        or int(row["r"]) != r
        or int(row["scoreable"] or 0) != 1
        or int(row["in_baseline"] or 0) != 0
        or str(row["scoring_status"]) != "scoreable"
    ):
        raise ValueError(f"ledger pair/status mismatch: {label}/r{r}")
    return digest


def validate_receipt(
    connection: sqlite3.Connection,
    anchor: dict,
) -> dict:
    submission_id = str(anchor["submissionId"])
    polynomial_index = int(anchor["polynomialIndex"])
    expected_hash = str(anchor["coefficientSha256"])
    receipt_path = ROOT / "receipts" / f"{submission_id}.json"
    receipt = read_json(receipt_path)
    if (
        not bool(receipt.get("commit"))
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or str((receipt.get("response") or {}).get("submissionId")) != submission_id
        or int(receipt.get("polynomials", -1)) <= polynomial_index
    ):
        raise ValueError(f"submission receipt envelope mismatch: {submission_id}")
    manifest = Path(str(receipt["manifest"])).resolve()
    if not manifest.is_relative_to(ROOT):
        raise ValueError("receipt manifest escaped the project root")
    manifest_hash = sha256_path(manifest)
    if manifest_hash != str(receipt["manifestHash"]):
        raise ValueError(f"receipt manifest hash mismatch: {submission_id}")
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if len(lines) != int(receipt["polynomials"]):
        raise ValueError(f"receipt manifest cardinality mismatch: {submission_id}")
    if canonical_coefficient_hash(lines[polynomial_index]) != expected_hash:
        raise ValueError(f"receipt polynomial index/hash mismatch: {submission_id}")
    ledger_receipt = connection.execute(
        "SELECT manifest_path,manifest_hash FROM submission_receipts "
        "WHERE submission_id=?",
        (submission_id,),
    ).fetchone()
    if (
        ledger_receipt is None
        or Path(str(ledger_receipt["manifest_path"])).resolve() != manifest
        or str(ledger_receipt["manifest_hash"]) != manifest_hash
    ):
        raise ValueError(f"ledger/filesystem receipt mismatch: {submission_id}")
    return {
        "receipt": relative(receipt_path),
        "manifest": {"path": str(manifest.relative_to(ROOT)), "sha256": manifest_hash},
        "polynomialIndexHashRecomputed": True,
        "knownLocalHashesAtCommit": 0,
    }


def validate_orbit_certificate(row: dict) -> None:
    certificate = row.get("orbitCertificate") or {}
    if (
        certificate.get("actualDegrees") != certificate.get("expectedDegrees")
        or not certificate.get("exponents")
        or any(int(value) != 1 for value in certificate["exponents"])
    ):
        raise ValueError("cached pair factorization certificate is not exact/squarefree")


def find_jsonl_row(path: Path, digest: str) -> dict:
    matches = [row for row in read_jsonl(path) if digest in json.dumps(row)]
    if len(matches) != 1:
        raise ValueError(f"cached hash row is not unique in {path}: {len(matches)}")
    return matches[0]


def validate_cached_reverse(
    connection: sqlite3.Connection,
    spec: dict,
    source_hash: str,
) -> dict:
    parent_id, parent_index, parent_label, parent_r = spec["parent"]
    parent_hash = validate_ledger_anchor(
        connection, parent_id, parent_index, parent_label, parent_r
    )
    candidate_path = Path(spec["candidateArtifact"])
    candidate = find_jsonl_row(candidate_path, source_hash)
    validate_orbit_certificate(candidate)
    if (
        str(candidate.get("sourceSubmissionId")) != parent_id
        or int(candidate.get("sourcePolynomialIndex", -1)) != parent_index
        or str(candidate.get("sourceLabel")) != parent_label
        or int(candidate.get("sourceR", -1)) != parent_r
        or str(candidate.get("sourceCoefficientSha256")) not in ("None", parent_hash)
    ):
        raise ValueError("cached reverse parent provenance mismatch")

    current_label, current_r = spec["source"]
    orbit_targets = [
        target
        for target in candidate.get("orbitTargets") or []
        if str(target.get("targetLabel")) == current_label
        and int(target.get("kernelOrder", -1)) == 1
    ]
    if len(orbit_targets) != 1:
        raise ValueError("current source is not a unique faithful cached child")

    if candidate.get("status") == "certified_multi":
        factors = [
            row
            for row in candidate.get("candidates") or []
            if str(row.get("coefficientSha256")) == source_hash
            and int(row.get("targetR", -1)) == current_r
        ]
        if len(factors) != 1:
            raise ValueError("cached multi child hash/signature mismatch")
        anchor = read_json(Path(spec["anchorArtifact"]))
        selected = [
            row
            for row in anchor.get("selected") or []
            if str(row.get("coefficientSha256")) == source_hash
        ]
        if (
            len(selected) != 1
            or selected[0].get("kind") != "verifier_anchor_forced_exact"
            or selected[0].get("possiblePairs") != [f"{current_label}/r{current_r}"]
        ):
            raise ValueError("cached verifier anchor does not force the current child")
        extra = {"anchorArtifact": relative(Path(spec["anchorArtifact"]))}
    elif candidate.get("status") == "certified":
        if (
            str(candidate.get("coefficientSha256")) != source_hash
            or str(candidate.get("targetLabel")) != current_label
            or int(candidate.get("targetR", -1)) != current_r
        ):
            raise ValueError("cached single child hash/signature mismatch")
        extra = {}
    else:
        raise ValueError("unsupported cached child status")

    return {
        "method": "unique-faithful-pair-child-reverse-to-accepted-parent-v1",
        "candidateArtifact": relative(candidate_path),
        **extra,
        "parent": {
            "submissionId": parent_id,
            "polynomialIndex": parent_index,
            "coefficientSha256": parent_hash,
            "label": parent_label,
            "r": parent_r,
            "ledgerStatus": "accepted_scoreable_nonbaseline",
        },
        "childOrbitIndex": int(orbit_targets[0]["orbitIndex"]),
        "childPair": f"{current_label}/r{current_r}",
        "forcedReversePair": f"{parent_label}/r{parent_r}",
    }


def validate_unresolved_ancestry(
    connection: sqlite3.Connection,
    spec: dict,
    source_hash: str,
) -> dict:
    result = find_jsonl_row(Path(spec["candidateArtifact"]), source_hash)
    candidate = result.get("candidate") or {}
    exact_action = result.get("exactAction") or {}
    source = result.get("source") or {}
    if (
        str(candidate.get("coefficientSha256")) != source_hash
        or int(candidate.get("r", -1)) != spec["source"][1]
        or str(exact_action.get("targetLabel")) != spec["source"][0]
        or int(exact_action.get("targetOrder", -1)) <= 0
        or str(source.get("label")) == spec["target"]
    ):
        raise ValueError("unresolved source-generation cache mismatch")
    generator_hash = validate_ledger_anchor(
        connection,
        str(source["submissionId"]),
        int(source["polynomialIndex"]),
        str(source["label"]),
        int(source["r"]),
        str(source["coefficientSha256"]),
    )
    final = find_jsonl_row(Path(spec["generationArtifact"]), source_hash)
    exact_actions = final.get("exactActions") or []
    if (
        len(exact_actions) != 1
        or int(exact_actions[0].get("targetKernelOrder", 0)) <= 1
        or str(exact_actions[0].get("targetLabel")) != spec["source"][0]
    ):
        raise ValueError("expected non-faithful Kummer ancestry was not pinned")
    return {
        "cachedGeneration": {
            "candidateArtifact": relative(Path(spec["candidateArtifact"])),
            "generationArtifact": relative(Path(spec["generationArtifact"])),
            "generator": {
                "submissionId": str(source["submissionId"]),
                "polynomialIndex": int(source["polynomialIndex"]),
                "coefficientSha256": generator_hash,
                "label": str(source["label"]),
                "r": int(source["r"]),
            },
            "targetKernelOrder": int(exact_actions[0]["targetKernelOrder"]),
        },
        "whyNotAReverseCertificate": (
            "the cached source is a non-faithful Kummer quotient from a different "
            "parent label; it cannot determine the faithful 24T17513->24T16970 "
            "pair-action signature"
        ),
        "requiredWorker": "one exact unique-orbit pair resolvent; no Frobenius labeling",
    }


def live_route_snapshot(
    connection: sqlite3.Connection,
    row: dict,
    route: dict,
) -> dict:
    target_label = str(route["targetLabel"])
    target_rows = connection.execute(
        "SELECT r,team_count,discovered,generated_at FROM targets WHERE label=? "
        "ORDER BY r",
        (target_label,),
    ).fetchall()
    if not target_rows:
        raise ValueError(f"target cache missing label: {target_label}")
    live_gold = []
    target_state = []
    for target in target_rows:
        pair = (target_label, int(target["r"]))
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
        ).fetchone()
        owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
            pair,
        ).fetchone()
        if int(target["team_count"]) == 0 and baseline is None and owned is None:
            live_gold.append(pair[1])
        target_state.append(
            {
                "r": pair[1],
                "teamCount": int(target["team_count"]),
                "discovered": bool(target["discovered"]),
                "owned": owned is not None,
                "baseline": baseline is not None,
            }
        )
    frozen_gold = sorted(int(value) for value in route.get("goldR") or [])
    if sorted(live_gold) != frozen_gold:
        raise ValueError(
            f"v13 route gold set is stale for {target_label}: "
            f"frozen={frozen_gold}, current={sorted(live_gold)}"
        )
    stamps = {str(target["generated_at"]) for target in target_rows}
    return {
        "targetLabel": target_label,
        "generatedAt": sorted(stamps),
        "currentLiveGoldR": sorted(live_gold),
        "mappedTargetR": sorted(int(value) for value in route["mappedTargetR"]),
        "reachableLiveGoldR": sorted(
            set(live_gold) & {int(value) for value in route["mappedTargetR"]}
        ),
        "targetState": target_state,
    }


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    census_rows = read_jsonl(CENSUS)
    census_routes = validate_census(census_rows)
    inventory = read_json(INVENTORY)
    if (
        inventory.get("schemaVersion") != "v13-accepted-scoreable-source-inventory-v1"
        or inventory.get("status") != "certified"
    ):
        raise ValueError("v13 source inventory is not certified")
    anchors = inventory.get("acceptedAnchorsByPair") or {}

    connection = connect_immutable()
    target_count, label_count = connection.execute(
        "SELECT COUNT(*),COUNT(DISTINCT label) FROM targets"
    ).fetchone()
    if (int(target_count), int(label_count)) != (165_836, 25_000):
        raise ValueError("target cache is incomplete; wait for the refresh checkpoint")

    audit_rows = []
    used = set()
    for spec in ROUTES:
        source_label, source_r = spec["source"]
        key = (source_label, source_r, spec["target"])
        if key not in census_routes:
            raise ValueError(f"route absent from v13 census: {key}")
        used.add(key)
        census_row, route = census_routes[key]
        inventory_rows = anchors.get(f"{source_label}:{source_r}") or []
        if len(inventory_rows) != 1:
            raise ValueError(f"source inventory anchor not unique: {source_label}/r{source_r}")
        anchor = inventory_rows[0]
        source_hash = validate_ledger_anchor(
            connection,
            str(anchor["submissionId"]),
            int(anchor["polynomialIndex"]),
            source_label,
            source_r,
            str(anchor["coefficientSha256"]),
        )
        receipt = validate_receipt(connection, anchor)
        live = live_route_snapshot(connection, census_row, route)

        if spec["kind"] == "unresolved_pair_resolvent_required":
            evidence = validate_unresolved_ancestry(connection, spec, source_hash)
            exact_target_r = None
            outcome = "one_exact_pair_resolvent_required"
            projected_score = {
                "ifTargetR8": "0",
                "ifTargetR16Or20": "1",
                "maximum": "1",
                "probabilityClaim": None,
            }
        else:
            evidence = validate_cached_reverse(connection, spec, source_hash)
            exact_target_r = int(spec["exactTargetR"])
            if exact_target_r in set(live["currentLiveGoldR"]):
                raise ValueError("cached reverse unexpectedly lands on current gold")
            outcome = "closed_exact_signature_miss"
            projected_score = {"exact": "0", "maximum": "0"}

        audit_rows.append(
            {
                "priority": 1 if exact_target_r is None else 0,
                "source": {
                    "submissionId": str(anchor["submissionId"]),
                    "polynomialIndex": int(anchor["polynomialIndex"]),
                    "coefficientSha256": source_hash,
                    "label": source_label,
                    "r": source_r,
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
                },
                "liveSnapshot": live,
                "proofKind": spec["kind"],
                "evidence": evidence,
                "exactTargetR": exact_target_r,
                "outcome": outcome,
                "projectedMarginalScoreExact": projected_score,
            }
        )
    connection.close()

    if used != set(census_routes):
        raise ValueError("not every v13 conditional route was audited")
    if sum(row["outcome"] == "closed_exact_signature_miss" for row in audit_rows) != 3:
        raise ValueError("expected exactly three cached-lineage closures")
    if sum(row["outcome"] == "one_exact_pair_resolvent_required" for row in audit_rows) != 1:
        raise ValueError("expected exactly one executable unresolved route")

    certificate = {
        "schemaVersion": "v13-exact-route-lineage-certificate-v1",
        "method": "receipt-anchored-faithful-reverse-lineage-and-live-audit-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "three_closed_one_exact_worker_ready",
        "inputs": {
            "census": relative(CENSUS),
            "exactSourceInventory": relative(INVENTORY),
            "ledger": {
                "path": str(DB.relative_to(ROOT)),
                "mode": "read_only_immutable_after_complete_target_checkpoint",
                "targetRows": int(target_count),
                "targetLabels": int(label_count),
            },
            "auditScript": relative(Path(__file__)),
        },
        "counts": {
            "conditionalRoutes": 4,
            "cachedLineageClosures": 3,
            "exactMisses": 3,
            "exactHits": 0,
            "unresolvedExecutableRoutes": 1,
            "polynomialArithmeticRuns": 0,
            "heavyWorkers": 0,
        },
        "routes": audit_rows,
        "recommendedExecution": {
            "source": "24T17513/r16",
            "target": "24T16970",
            "worker": "pair_sum_one.sage.py",
            "frobeniusRequired": False,
            "reachableLiveGoldR": [16, 20],
            "missR": [8],
            "maximumMarginalScore": "1",
            "requiresRootHeavyWorkerClearance": True,
        },
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "polynomialArithmeticRuns": 0,
        },
    }
    atomic_json(CERTIFICATE, certificate)
    summary = {
        "schemaVersion": "v13-exact-route-lineage-summary-v1",
        "status": certificate["status"],
        "conditionalRoutes": 4,
        "cachedLineageClosures": 3,
        "unresolvedExecutableRoutes": 1,
        "bestRoute": {
            "source": "24T17513/r16",
            "target": "24T16970",
            "reachableLiveGoldR": [16, 20],
            "missR": [8],
            "maximumMarginalScore": "1",
            "frobeniusRequired": False,
        },
        "certificate": relative(CERTIFICATE),
        "sideEffects": certificate["sideEffects"],
    }
    atomic_json(SUMMARY, summary)
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "closed": 3,
                "workerReady": 1,
                "certificate": str(CERTIFICATE.relative_to(ROOT)),
                "summary": str(SUMMARY.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

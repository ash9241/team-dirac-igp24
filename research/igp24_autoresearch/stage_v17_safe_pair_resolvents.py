#!/usr/bin/env python3
"""Seal and stage the two exact all-compatible v17 pair routes offline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as base
import audit_low_contention_tc7_tc9_routes as outbox_audit
import run_low_contention_remaining_batch as exact
import run_low_contention_sequential as lane
import stage_frobenius_gold as frobenius


ROOT = lane.ROOT
DATA = lane.DATA
V17 = DATA / "autopilot_pair_delta_20260722_v17"
ROUTES = V17 / "missing_pair_all.jsonl"
INVENTORY = V17 / "exact_source_inventory.json"
PLAN = V17 / "provenance_plan.json"
TRIAGE = V17 / "route_triage_certificate.json"
TRIAGE_SUMMARY = V17 / "route_triage_summary.json"
MANIFEST = ROOT / "outbox/v17_safe_pair_resolvents.txt"
POSTFLIGHT = DATA / "v17_safe_pair_resolvent_postflight.json"
SUMMARY = DATA / "v17_safe_pair_resolvent_stage_summary.json"
MAPPING = DATA / "v17_safe_pair_resolvent_receipt_mapping_ready.json"
EBC_RECEIPT = lane.RECEIPTS / "sub_ebc16254512a411b902ac0212d62f21b.json"
SPECS = (
    {
        "source": ("sub_0434bc0351bd4bc3b19a55b1c3d39079", 4, "24T10357", 20),
        "sourceHash": "f98045695e7d0ae2cdebb1ebcd6b2597ade49749d38dbdf59291f845e59fb4c0",
        "target": ("24T11827", 16),
        "candidates": DATA / "v17_safe_24T10357_r20_pair_resolvent.jsonl",
        "frobenius": DATA / "v17_safe_24T10357_r20_frobenius_certificate.json",
    },
    {
        "source": ("sub_0434bc0351bd4bc3b19a55b1c3d39079", 19, "24T18583", 20),
        "sourceHash": "0e9c51ee50871364ea95424414f1c8c0c9caed3f5122a9907d95de51aad24c98",
        "target": ("24T19066", 16),
        "candidates": DATA / "v17_safe_24T18583_r20_pair_resolvent.jsonl",
        "frobenius": DATA / "v17_safe_24T18583_r20_frobenius_certificate.json",
    },
)


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def canonical(candidate: dict) -> tuple[str, str]:
    line = str(candidate["coefficientLine"])
    values = [int(value) for value in line.split(",")]
    if len(values) != 25 or values[-1] != 1 or values[0] == 0:
        raise lane.GuardFailure("invalid v17 candidate payload")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(candidate["coefficientSha256"]):
        raise lane.GuardFailure("v17 candidate hash changed")
    return line, digest


def validate_ebc() -> dict:
    receipt = lane.read_json(EBC_RECEIPT)
    response = receipt.get("response") or {}
    manifest = Path(str(receipt.get("manifest"))).resolve()
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != 8
        or str(response.get("submissionId")) != EBC_RECEIPT.stem
        or int(response.get("rejectedCount", 0)) != 0
        or list(response.get("failedPolynomials") or [])
        or lane.sha256_path(manifest) != str(receipt.get("manifestHash"))
    ):
        raise lane.GuardFailure("newest ebc receipt is not intact")
    return {
        "submissionId": EBC_RECEIPT.stem,
        "receipt": lane.artifact(EBC_RECEIPT),
        "manifest": {**lane.artifact(manifest), "polynomials": 8},
        "submissionStatus": str(response.get("submissionStatus")),
    }


def validate_routes() -> tuple[list[dict], dict]:
    route_rows = rows(ROUTES)
    if len(route_rows) != 39 or any(row.get("status") != "certified" for row in route_rows):
        raise lane.GuardFailure("v17 census is not 39/39 certified")
    by_label = {str(row["sourceLabel"]): row for row in route_rows}
    inventory = lane.read_json(INVENTORY)
    anchors = inventory.get("acceptedAnchorsByPair") or {}
    selected = []
    for spec in SPECS:
        sid, index, label, source_r = spec["source"]
        row = by_label.get(label)
        anchor_rows = anchors.get(f"{label}:{source_r}") or []
        matching = [
            route for route in (row or {}).get("routes") or []
            if route.get("allCompatibleClassesGold") is True
            and str(route.get("targetLabel")) == spec["target"][0]
            and int(route.get("sourceR", -1)) == source_r
            and spec["target"][1] in [int(value) for value in route.get("goldR") or []]
        ]
        if row is None or len(anchor_rows) != 1 or len(matching) != 1:
            raise lane.GuardFailure(f"v17 safe route envelope changed: {label}")
        anchor = anchor_rows[0]
        if (
            str(anchor["submissionId"]) != sid
            or int(anchor["polynomialIndex"]) != index
            or str(anchor["coefficientSha256"]) != spec["sourceHash"]
            or str(anchor["scoringStatus"]) != "scoreable"
            or bool(anchor["inBaseline"])
        ):
            raise lane.GuardFailure(f"v17 source inventory changed: {label}")
        selected.append({"sourceRow": row, "route": matching[0]})
    return selected, {
        "rows": len(route_rows),
        "routes": sum(len(row.get("routes") or []) for row in route_rows),
        "allCompatibleSafeRoutes": sum(
            route.get("allCompatibleClassesGold") is True
            for row in route_rows for route in row.get("routes") or []
        ),
        "sha256": lane.sha256_path(ROUTES),
    }


def join_exact() -> tuple[list[dict], list[dict]]:
    selected, public = [], []
    for spec in SPECS:
        candidate_rows = rows(spec["candidates"])
        if len(candidate_rows) != 1:
            raise lane.GuardFailure("v17 resolvent artifact cardinality changed")
        packet = candidate_rows[0]
        proof = lane.read_json(spec["frobenius"])
        joined = frobenius.join_resolved_assignments(
            proof, candidate_rows, spec["candidates"], allow_unresolved=False
        )
        if len(joined) != 3:
            raise lane.GuardFailure("v17 Frobenius assignment is not exact three-factor")
        matches = [
            row for row in joined
            if (str(row["targetLabel"]), int(row["targetR"])) == spec["target"]
        ]
        if len(matches) != 1:
            raise lane.GuardFailure("v17 safe target did not resolve uniquely")
        candidate_by_index = {
            int(row["factorIndex"]): row for row in packet.get("candidates") or []
        }
        exact_row = {**matches[0], **candidate_by_index[int(matches[0]["factorIndex"])]}
        line, digest = canonical(exact_row)
        exact_row["coefficientLine"] = line
        exact_row["coefficientSha256"] = digest
        selected.append(exact_row)
        public.append({
            "sourceLabel": spec["source"][2],
            "sourceR": spec["source"][3],
            "sourceSubmissionId": spec["source"][0],
            "sourcePolynomialIndex": spec["source"][1],
            "targetPair": f"{spec['target'][0]}/r{spec['target'][1]}",
            "factorIndex": int(exact_row["factorIndex"]),
            "coefficientSha256": digest,
            "candidateArtifact": lane.artifact(spec["candidates"]),
            "frobeniusCertificate": lane.artifact(spec["frobenius"]),
        })
    return selected, public


def main() -> int:
    for path in (TRIAGE, TRIAGE_SUMMARY, MANIFEST, POSTFLIGHT, SUMMARY, MAPPING):
        if path.exists():
            raise lane.GuardFailure(f"refusing to overwrite v17 artifact: {path}")
    plan = lane.read_json(PLAN)
    if plan.get("status") != "ready_for_one_heavy_worker" or plan.get("execution", {}).get("heavyCommandFrozen") is not True:
        raise lane.GuardFailure("v17 provenance plan changed")
    ebc = validate_ebc()
    safe_routes, census = validate_routes()
    selected, public = join_exact()

    with lane.connect_ro() as connection:
        candidate_snapshot = base.exact_candidate_and_receipt_snapshot(connection)
        supplemental, supplemental_artifacts = outbox_audit.supplemental_exact_pair_index()
        pair_index = outbox_audit.merged_pair_index(candidate_snapshot, supplemental)
        outbox_hashes, outbox_pairs, current_outbox = outbox_audit.outbox_exclusions(
            pair_index, connection
        )
        snapshots = []
        for spec, row in zip(SPECS, selected, strict=True):
            sid, index, label, source_r = spec["source"]
            source = connection.execute(
                "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
                "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
                "WHERE p.submission_id=? AND p.polynomial_index=?", (sid, index)
            ).fetchone()
            pair = spec["target"]
            target = connection.execute(
                "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?", pair
            ).fetchone()
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()
            owned = connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1", pair
            ).fetchone()
            known = connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=?", (row["coefficientSha256"],)
            ).fetchone()
            if (
                source is None
                or (str(source["coefficient_hash"]), str(source["status"]), int(source["scoreable"]), str(source["label"]), int(source["r"]))
                != (spec["sourceHash"], "accepted", 1, label, source_r)
                or target is None or int(target["team_count"]) != 0 or bool(target["discovered"])
                or baseline is not None or owned is not None or known is not None
                or row["coefficientSha256"] in candidate_snapshot["receiptHashes"]
                or pair in candidate_snapshot["receiptPairs"]
                or row["coefficientSha256"] in outbox_hashes or pair in outbox_pairs
            ):
                raise lane.GuardFailure(f"v17 exact safe row is no longer live/novel: {pair}")
            snapshots.append({
                "pair": f"{pair[0]}/r{pair[1]}",
                "teamCount": 0,
                "discovered": False,
                "generatedAt": str(target["generated_at"]),
                "baseline": False,
                "owned": False,
                "receipted": False,
                "otherOutboxReserved": False,
                "coefficientNovel": True,
            })

    manifest_text = "".join(row["coefficientLine"] + "\n" for row in selected)
    lane.exclusive_text(MANIFEST, manifest_text)
    offline = exact.offline_manifest_check(
        MANIFEST,
        [str(row["coefficientSha256"]) for row in selected],
        candidate_snapshot["receiptHashes"],
    )
    triage = {
        "schemaVersion": "v17-route-triage-certificate-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "two_all_compatible_routes_exactly_resolved_and_staged",
        "census": {"path": lane.relative(ROUTES), **census},
        "inventory": lane.artifact(INVENTORY),
        "provenancePlan": lane.artifact(PLAN),
        "classification": {
            "allCompatibleSafeRoutes": 2,
            "conditionalRoutesHeld": 2,
            "cachedExactReadyRows": 0,
            "isolatedResolventRows": 2,
            "exactFrobeniusResolvedRows": 2,
        },
        "routes": public,
        "checks": {
            "census39Of39Certified": True,
            "censusHasFourRoutes": census["routes"] == 4,
            "twoAllCompatibleRoutesOnly": census["allCompatibleSafeRoutes"] == 2,
            "sourceAnchorsPinnedAcceptedScoreable": True,
            "resolventsExactThreeFactorsEach": True,
            "frobeniusAssignmentsResolvedExactly": True,
            "conditionalRoutesNotExecuted": True,
            "coefficientMaterialExcluded": True,
        },
        "coefficientMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    lane.exclusive_json(TRIAGE, triage)
    triage_summary = {
        "schemaVersion": "v17-route-triage-summary-v1",
        "status": triage["status"],
        "certificate": lane.artifact(TRIAGE),
        "safeRoutes": 2,
        "conditionalRoutesHeld": 2,
        "exactRowsStaged": 2,
        "coefficientMaterialIncluded": False,
    }
    lane.exclusive_json(TRIAGE_SUMMARY, triage_summary)

    projection = Fraction(2, 1)
    postflight = {
        "schemaVersion": "v17-safe-pair-resolvent-postflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_two_exact_live_tc0_novel_staged_not_submitted",
        "triage": lane.artifact(TRIAGE),
        "newestReceipt": ebc,
        "selected": public,
        "targetSnapshot": snapshots,
        "receiptExclusion": {
            key: value for key, value in candidate_snapshot["receiptAudit"].items()
            if key != "audit"
        },
        "outboxExclusion": {
            key: current_outbox[key] for key in (
                "outboxFiles", "nonemptyOutboxFiles", "distinctCoefficientHashes",
                "distinctPairsExcluded", "coefficientHashSetSha256", "pairSetSha256"
            )
        },
        "supplementalPairMaps": supplemental_artifacts,
        "manifest": {**lane.artifact(MANIFEST), "polynomials": 2, "bytes": MANIFEST.stat().st_size},
        "offlineDryRun": offline,
        "projectedMarginalScoreExact": str(projection),
        "checks": {
            "targetsTc0UndiscoveredNonbaselineUnowned": True,
            "coefficientNoveltyPassed": True,
            "receiptHashPairExclusionsPassed": True,
            "activeOutboxHashPairExclusionsPassed": True,
            "oneExactCandidatePerSafePair": True,
            "coefficientPayloadConfinedToManifest": True,
        },
        "coefficientMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0, "maximumConcurrentSageGapWorkers": 1},
    }
    lane.exclusive_json(POSTFLIGHT, postflight)
    mapping = {
        "schemaVersion": "v17-safe-pair-resolvent-receipt-mapping-ready-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_receipt_mapping_after_submission",
        "manifest": {**lane.artifact(MANIFEST), "polynomials": 2},
        "routeCount": 2,
        "distinctCandidateHashes": 2,
        "distinctTargetPairs": 2,
        "mappings": [
            {
                "manifestPosition": index,
                "routeId": f"v17_safe_{row['sourceLabel']}_r{row['sourceR']}",
                "targetPair": row["targetPair"],
                "candidateSha256": row["coefficientSha256"],
                "receiptSubmissionId": None,
            }
            for index, row in enumerate(public, start=1)
        ],
        "coefficientMaterialIncluded": False,
        "submissionAuthorized": False,
    }
    lane.exclusive_json(MAPPING, mapping)
    summary = {
        "schemaVersion": "v17-safe-pair-resolvent-stage-summary-v1",
        "status": postflight["status"],
        "polynomials": 2,
        "pairs": [row["targetPair"] for row in public],
        "projectedMarginalScoreExact": str(projection),
        "manifest": lane.relative(MANIFEST),
        "manifestSha256": lane.sha256_path(MANIFEST),
        "postflight": lane.artifact(POSTFLIGHT),
        "mapping": lane.artifact(MAPPING),
        "submissionCalls": 0,
    }
    lane.exclusive_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (lane.GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

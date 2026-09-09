#!/usr/bin/env python3
"""Final light-only deterministic pair-route audit for tc7/tc8/tc9.

The audit uses the current read-only ledger and target cache, the sealed exact
unordered-pair action/profile chain, exact candidate lineage, every intact
local receipt manifest, and every current outbox text manifest.  It performs
no polynomial arithmetic, network operation, ledger write, or submission.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_higher_tc_routes as higher
import audit_low_contention_pair_routes as base
import audit_low_contention_tc5_tc6_routes as scout
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_census


ROOT = lane.ROOT
DATA = lane.DATA
OUTBOX = ROOT / "outbox"
TEAM_COUNTS = (7, 8, 9)
CERTIFICATE = DATA / "low_contention_tc7_tc9_routes_certificate.json"
SUMMARY = DATA / "low_contention_tc7_tc9_routes_summary.json"
OUTBOX_INDEX = DATA / "low_contention_tc7_tc9_outbox_exclusion_index.json"
NAMED_RECEIPTS = {
    "sub_b4f7f2bd853446aca3382d22345a73ec": 22,
    "sub_6eeaa15d8d994fd19646243de90b4b09": 3,
    "sub_bc7a3dd9294543bd813bed8ba413972b": 14,
}


def manifest_hashes(path: Path, expected: int | None = None) -> set[str]:
    hashes = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        payload = raw_line.split("#", 1)[0].strip()
        if not payload:
            continue
        line = exact_census.canonical_polynomial_line(
            payload
        )
        if line is None:
            raise lane.GuardFailure(f"noncanonical outbox row: {path}")
        hashes.add(exact_census.sha256_bytes(line.encode("ascii")))
    if expected is not None and len(hashes) != expected:
        raise lane.GuardFailure(
            f"manifest hash count mismatch for {path}: "
            f"{len(hashes)} != {expected}"
        )
    return hashes


def parse_pair_text(value: object) -> tuple[str, int]:
    match = exact_census.PAIR_TEXT_RE.fullmatch(str(value))
    if match is None:
        raise lane.GuardFailure(f"invalid exact pair text: {value!r}")
    return match.group(1), int(match.group(2))


def supplemental_exact_pair_index() -> tuple[dict[str, set[tuple[str, int]]], list[dict]]:
    """Load coefficient-free exact receipt maps outside the generic scanner."""
    index: dict[str, set[tuple[str, int]]] = {}
    artifacts = []
    paths = sorted(DATA.glob("*receipt_mapping*.json"))
    global_delta = DATA / "global_exact_corpus_delta_after_b4f7_20260722_certificate.json"
    if global_delta.is_file():
        paths.append(global_delta)
    for path in paths:
        value = lane.read_json(path)
        used = False
        for row in value.get("mappings") or []:
            digest = str(row.get("candidateSha256", ""))
            pair_value = row.get("targetPair")
            if base.HASH_RE.fullmatch(digest) is None or pair_value is None:
                continue
            index.setdefault(digest, set()).add(parse_pair_text(pair_value))
            used = True
        for row in value.get("selected") or []:
            if str(row.get("type")) != "exact":
                continue
            digest = str(row.get("coefficientSha256", ""))
            pair_value = row.get("pair")
            if base.HASH_RE.fullmatch(digest) is None or pair_value is None:
                continue
            index.setdefault(digest, set()).add(parse_pair_text(pair_value))
            used = True
        if used:
            artifacts.append(lane.artifact(path))
    return index, artifacts


def merged_pair_index(
    candidate_snapshot: dict,
    supplemental: dict[str, set[tuple[str, int]]],
) -> dict[str, set[tuple[str, int]]]:
    result = {
        digest: set(pairs)
        for digest, pairs in candidate_snapshot["candidatePairIndex"].items()
    }
    for digest, pairs in supplemental.items():
        result.setdefault(digest, set()).update(pairs)
    return result


def validate_named_receipts(
    candidate_snapshot: dict,
    pair_index: dict[str, set[tuple[str, int]]],
    named_receipts: dict[str, int] | None = None,
) -> tuple[list[dict], set[tuple[str, int]]]:
    receipt_specs = NAMED_RECEIPTS if named_receipts is None else named_receipts
    audits = []
    all_pairs = set()
    for submission_id, expected in receipt_specs.items():
        path = lane.RECEIPTS / f"{submission_id}.json"
        receipt = lane.read_json(path)
        response = receipt.get("response") or {}
        receipt_manifest = Path(str(receipt.get("manifest"))).resolve()
        if (
            receipt.get("commit") is not True
            or int(receipt.get("polynomials", -1)) != expected
            or str(response.get("submissionId")) != submission_id
            or int(response.get("rejectedCount", -1)) != 0
            or response.get("failedPolynomials")
            or not receipt_manifest.is_file()
            or exact_census.sha256_path(receipt_manifest)
            != str(receipt.get("manifestHash"))
        ):
            raise lane.GuardFailure(
                f"named receipt is incomplete or changed: {submission_id}"
            )
        hashes = manifest_hashes(receipt_manifest, expected)
        if not hashes <= candidate_snapshot["receiptHashes"]:
            raise lane.GuardFailure(
                f"named receipt hashes are not excluded: {submission_id}"
            )
        pairs = set()
        for digest in hashes:
            mapped = pair_index.get(digest, set())
            if len(mapped) != 1:
                raise lane.GuardFailure(
                    f"named receipt hash lacks one exact pair: {submission_id}"
                )
            pairs.update(mapped)
        if len(pairs) != expected:
            raise lane.GuardFailure(
                f"named receipt exact pair mapping is incomplete: {submission_id}"
            )
        all_pairs.update(pairs)
        audits.append(
            {
                **lane.artifact(path),
                "submissionId": submission_id,
                "declaredPolynomials": expected,
                "submissionStatusAtAudit": str(
                    response.get("submissionStatus")
                ),
                "rejectedCount": 0,
                "intactManifestSha256": str(receipt["manifestHash"]),
                "exactHashesExcluded": len(hashes),
                "exactPairsExcluded": len(pairs),
            }
        )
    return audits, all_pairs


def query_ledger_pairs_for_hashes(
    connection: sqlite3.Connection, hashes: set[str]
) -> set[tuple[str, int]]:
    pairs = set()
    values = sorted(hashes)
    for start in range(0, len(values), 300):
        batch = values[start : start + 300]
        placeholders = ",".join("?" for _ in batch)
        pairs.update(
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT v.label,v.r FROM polynomials p "
                "JOIN verifications v USING(submission_id,polynomial_index) "
                f"WHERE p.coefficient_hash IN ({placeholders}) "
                "AND v.label IS NOT NULL AND v.r IS NOT NULL",
                batch,
            )
        )
    return pairs


def outbox_exclusions(
    pair_index: dict[str, set[tuple[str, int]]],
    connection: sqlite3.Connection,
) -> tuple[set[str], set[tuple[str, int]], dict]:
    artifacts = []
    hashes = set()
    canonical_rows = 0
    nonempty_files = 0
    for path in sorted(OUTBOX.glob("*.txt")):
        file_hashes = manifest_hashes(path)
        rows = sum(
            1 for line in path.read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()
        )
        if rows != len(file_hashes):
            raise lane.GuardFailure(
                f"duplicate coefficient row within outbox: {path}"
            )
        if rows:
            nonempty_files += 1
        canonical_rows += rows
        hashes.update(file_hashes)
        artifacts.append(
            {
                **lane.artifact(path),
                "canonicalPolynomialRows": rows,
            }
        )

    candidate_pairs = set()
    mapped_hashes = set()
    ambiguous_hashes = set()
    for digest in hashes:
        mapped = pair_index.get(digest, set())
        if mapped:
            mapped_hashes.add(digest)
            candidate_pairs.update(mapped)
            if len(mapped) != 1:
                ambiguous_hashes.add(digest)
    ledger_pairs = query_ledger_pairs_for_hashes(connection, hashes)
    pairs = candidate_pairs | ledger_pairs
    index = {
        "schemaVersion": "low-contention-outbox-exclusion-index-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_all_current_txt_outboxes_canonical",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "outboxFiles": len(artifacts),
        "nonemptyOutboxFiles": nonempty_files,
        "canonicalPolynomialRows": canonical_rows,
        "distinctCoefficientHashes": len(hashes),
        "hashesWithExactCandidatePairMapping": len(mapped_hashes),
        "ambiguousExactCandidateHashes": len(ambiguous_hashes),
        "candidatePairsExcluded": len(candidate_pairs),
        "ledgerPairsExcluded": len(ledger_pairs),
        "distinctPairsExcluded": len(pairs),
        "coefficientHashSetSha256": base.canonical_digest(sorted(hashes)),
        "pairSetSha256": base.canonical_digest(
            [
                [label, r]
                for label, r in sorted(pairs, key=base.pair_sort_key)
            ]
        ),
        "artifactIndexSha256": base.canonical_digest(artifacts),
        "artifacts": artifacts,
        "checks": {
            "allTxtOutboxesEnumerated": True,
            "allNonblankRowsCanonical": True,
            "noDuplicateRowsWithinAnyOutbox": True,
            "coefficientPayloadOmitted": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    return hashes, pairs, index


def finalize_runbooks(
    censuses: list[dict],
    snapshot: dict,
    certificate: Path = CERTIFICATE,
) -> list[dict]:
    runbooks = scout.make_runbooks(censuses, snapshot)
    for row in runbooks:
        row["status"] = (
            "finalized_receipt_outbox_aware_waiting_for_heavy_clearance"
        )
        row["guards"].pop("postTc4FullAuditRerunRequired", None)
        row["guards"].update(
            {
                "allCurrentReceiptHashesAndPairsExcludedAtSeal": True,
                "allCurrentOutboxHashesAndMappedPairsExcludedAtSeal": True,
                "sourceAnchorAcceptedAndHashRevalidatedAtSeal": True,
                "targetPairCurrentAndNovelAtSeal": True,
                "currentBoundaryMustRemainUnchangedAtExecution": True,
            }
        )
        command = row["postflightCommand"]
        certificate_index = command.index("--certificate") + 1
        command[certificate_index] = str(certificate.relative_to(ROOT))
    return runbooks


def census_summary(census: dict) -> dict:
    targets = {
        next(iter(route["lowOutcomes"]))
        for route in census["executableRoutes"]
    }
    proof_counts: dict[str, int] = {}
    for route in census["selectedRoutes"]:
        kind = str(route["proofKind"])
        proof_counts[kind] = proof_counts.get(kind, 0) + 1
    return {
        "teamCount": census["teamCount"],
        "deterministicRoutes": len(census["deterministicRoutes"]),
        "deterministicSingleOrbitRoutes": len(census["singleOrbitRoutes"]),
        "deterministicSingleOrbitRoutesWithFreshAnchor": len(
            census["executableRoutes"]
        ),
        "distinctExecutableTargets": len(targets),
        "selectedRunbooks": len(census["selectedRoutes"]),
        "selectedProofKindCounts": dict(sorted(proof_counts.items())),
        "testedSourceKeys": len(census["lineage"]["testedSourceKeys"]),
        "testedSourceHashes": len(
            census["lineage"]["testedSourceHashes"]
        ),
        "cachedExactMissClosures": census["lineage"][
            "closedAnchorCount"
        ],
        "cachedNovelReadyCandidates": len(
            census["lineage"]["readyNovelLowCandidates"]
        ),
    }


def main() -> int:
    for path in (CERTIFICATE, SUMMARY, OUTBOX_INDEX):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite audit artifact: {path}")

    actions, action_provenance, action_artifacts = base.load_sealed_actions()
    profiles, profile_provenance, profile_artifacts = base.load_profiles(
        actions, action_artifacts
    )
    connection = sqlite3.connect(
        f"file:{lane.DB.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        snapshot = base.load_ledger_snapshot(connection)
        receipt_snapshot = base.exact_candidate_and_receipt_snapshot(
            connection
        )
        supplemental_index, supplemental_artifacts = (
            supplemental_exact_pair_index()
        )
        pair_index = merged_pair_index(receipt_snapshot, supplemental_index)
        named_receipt_audits, named_receipt_pairs = validate_named_receipts(
            receipt_snapshot, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_index = outbox_exclusions(
            pair_index, connection
        )
        exclusion_snapshot = {
            **receipt_snapshot,
            "receiptHashes": (
                receipt_snapshot["receiptHashes"] | outbox_hashes
            ),
            "receiptPairs": (
                receipt_snapshot["receiptPairs"]
                | named_receipt_pairs
                | outbox_pairs
            ),
        }
        censuses = []
        for team_count in TEAM_COUNTS:
            census = higher.census_for_count(
                team_count,
                snapshot,
                actions,
                action_provenance,
                profiles,
                profile_provenance,
                exclusion_snapshot,
                connection,
            )
            censuses.append(scout.rerank_census(census, snapshot))
    finally:
        connection.close()

    lane.exclusive_json(OUTBOX_INDEX, outbox_index)
    runbooks = finalize_runbooks(censuses, snapshot)
    target_pairs = [
        (row["target"]["label"], int(row["target"]["r"]))
        for row in runbooks
    ]
    source_hashes = [
        str(row["source"]["coefficientSha256"]) for row in runbooks
    ]
    source_keys = [
        (
            str(row["source"]["submissionId"]),
            int(row["source"]["polynomialIndex"]),
        )
        for row in runbooks
    ]
    if (
        len(target_pairs) != len(set(target_pairs))
        or len(source_hashes) != len(set(source_hashes))
        or len(source_keys) != len(set(source_keys))
    ):
        raise lane.GuardFailure(
            "runbook target pairs or exact source anchors are not distinct"
        )

    receipt_audit = receipt_snapshot["receiptAudit"]
    target_rows = [
        [
            label,
            r,
            state["teamCount"],
            state["discovered"],
            state["minimumDiscAbs"],
            state["generatedAt"],
        ]
        for (label, r), state in sorted(
            snapshot["targets"].items(),
            key=lambda item: base.pair_sort_key(item[0]),
        )
    ]
    accepted_rows = [
        [label, r]
        for label, r in sorted(snapshot["owned"], key=base.pair_sort_key)
    ]
    layer_counts = {
        str(census["teamCount"]): len(census["selectedRoutes"])
        for census in censuses
    }
    total_score = sum(
        (
            Fraction(
                len(census["selectedRoutes"]),
                2 ** int(census["teamCount"]),
            )
            for census in censuses
        ),
        Fraction(0),
    )
    all_non_submission_guards = [
        value
        for row in runbooks
        for key, value in row["guards"].items()
        if key != "submissionAuthorized"
    ]
    certificate = {
        "schemaVersion": "low-contention-receipt-outbox-route-audit-v1",
        "scope": "finalized_deterministic_tc7_tc8_with_tc9_scout",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_final_receipt_outbox_aware_runbooks_ready"
            if runbooks
            else "certified_final_receipt_outbox_aware_no_runbooks"
        ),
        "method": (
            "sealed exact pair actions/profiles + current accepted ledger + "
            "exact candidate lineage + all local receipts and txt outboxes"
        ),
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "boundary": {
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "acceptedPairSetSha256": base.canonical_digest(accepted_rows),
            "targetRows": len(snapshot["targets"]),
            "targetSnapshotSha256": base.canonical_digest(target_rows),
            "targetGeneratedAtMin": min(
                state["generatedAt"]
                for state in snapshot["targets"].values()
            ),
            "targetGeneratedAtMax": max(
                state["generatedAt"]
                for state in snapshot["targets"].values()
            ),
        },
        "artifacts": {
            "sealedActionMaps": action_artifacts,
            "profileArtifacts": profile_artifacts,
            "candidateCorpus": receipt_snapshot["corpus"],
            "supplementalExactPairMaps": supplemental_artifacts,
            "database": {"path": str(lane.DB.relative_to(ROOT))},
            "receiptsDirectory": str(lane.RECEIPTS.relative_to(ROOT)),
            "outboxExclusionIndex": lane.artifact(OUTBOX_INDEX),
            "auditProgram": lane.artifact(Path(__file__).resolve()),
        },
        "namedReceiptAudit": named_receipt_audits,
        "receiptExclusion": {
            "receiptCount": receipt_audit["receiptCount"],
            "receiptPolynomialHashes": receipt_audit[
                "receiptPolynomialHashes"
            ],
            "receiptTargetPairs": receipt_audit["receiptTargetPairs"],
            "queuedPossiblePairMapRows": receipt_audit[
                "queuedPossiblePairMapRows"
            ],
        },
        "outboxExclusion": {
            "outboxFiles": outbox_index["outboxFiles"],
            "nonemptyOutboxFiles": outbox_index[
                "nonemptyOutboxFiles"
            ],
            "canonicalPolynomialRows": outbox_index[
                "canonicalPolynomialRows"
            ],
            "distinctCoefficientHashes": len(outbox_hashes),
            "distinctPairsExcluded": len(outbox_pairs),
            "coefficientHashSetSha256": outbox_index[
                "coefficientHashSetSha256"
            ],
            "pairSetSha256": outbox_index["pairSetSha256"],
        },
        "combinedExclusion": {
            "distinctCoefficientHashes": len(
                exclusion_snapshot["receiptHashes"]
            ),
            "distinctPairs": len(exclusion_snapshot["receiptPairs"]),
        },
        "censuses": {
            str(census["teamCount"]): census_summary(census)
            for census in censuses
        },
        "ranking": {
            "order": [
                "tc7 projected score before tc8 before tc9",
                "identity complex-conjugation proof before exact cached profiles",
                "fewer compatible exact conjugacy classes",
                "shorter accepted source coefficient encoding",
                "smaller current target minimum discriminant",
                "target/source/orbit deterministic tie-break",
            ],
            "scoreFormula": (
                "1/2^teamCount per target, upper bound before "
                "discriminant penalty"
            ),
        },
        "runbooks": runbooks,
        "runbookProjection": {
            "count": len(runbooks),
            "teamCountDistribution": layer_counts,
            "marginalScoreExact": base.fraction_text(total_score),
            "qualifier": "upper_bound_before_discriminant_penalty",
        },
        "checks": {
            "allNamedReceiptsPinnedIntactAndExcluded": (
                len(named_receipt_audits) == len(NAMED_RECEIPTS)
            ),
            "allCurrentReceiptHashesAndPairsExcluded": True,
            "allCurrentTxtOutboxesCanonicalAndExcluded": all(
                outbox_index["checks"].values()
            ),
            "cachedNovelTc7Tc8Tc9CandidatesAbsent": all(
                not census["lineage"]["readyNovelLowCandidates"]
                for census in censuses
            ),
            "allRunbooksCurrentAcceptedFreshExactAnchors": all(
                row["guards"]["sourceAcceptedScoreableAtSeal"]
                and row["guards"]["sourceAnchorFreshUntestedAtSeal"]
                and row["guards"][
                    "sourceAnchorAcceptedAndHashRevalidatedAtSeal"
                ]
                for row in runbooks
            ),
            "allRunbooksDeterministicSingleOrbit": all(
                row["target"]["teamCountAtSeal"] in TEAM_COUNTS
                and row["exactAction"]["length24OrbitCount"] == 1
                and row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
                for row in runbooks
            ),
            "allSourceAnchorsDistinct": len(source_hashes)
            == len(set(source_hashes))
            == len(source_keys)
            == len(set(source_keys)),
            "allTargetPairsDistinct": len(target_pairs)
            == len(set(target_pairs)),
            "allTargetsCurrentNovelAndReceiptOutboxExcluded": all(
                all_non_submission_guards
            ),
            "allOutputsAbsent": all(
                row["guards"]["outputAbsentAtSeal"]
                and row["guards"]["outputTemporaryAbsentAtSeal"]
                for row in runbooks
            ),
            "tc7RankedBeforeTc8BeforeTc9": all(
                runbooks[index]["target"]["teamCountAtSeal"]
                <= runbooks[index + 1]["target"]["teamCountAtSeal"]
                for index in range(len(runbooks) - 1)
            ),
            "certificateContainsNoCoefficientPayload": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "polynomialArithmeticRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if base.COEFFICIENT_LINE_RE.search(rendered):
        raise lane.GuardFailure(
            "coefficient payload entered tc7/tc8/tc9 certificate"
        )
    lane.exclusive_text(CERTIFICATE, rendered)

    best = runbooks[0] if runbooks else None
    summary = {
        "schemaVersion": "low-contention-tc7-tc9-summary-v1",
        "status": certificate["status"],
        "certificate": lane.artifact(CERTIFICATE),
        "censuses": certificate["censuses"],
        "runbooks": len(runbooks),
        "teamCountDistribution": layer_counts,
        "projectedMarginalScoreExact": base.fraction_text(total_score),
        "bestRouteId": best["routeId"] if best else None,
        "bestRouteTeamCount": (
            best["target"]["teamCountAtSeal"] if best else None
        ),
        "bestRouteProjectedMarginalScoreExact": (
            best["target"]["projectedMarginalScoreExact"] if best else None
        ),
        "bestRouteReliabilityTier": (
            best["routeReliability"]["tier"] if best else None
        ),
        "namedReceiptsExcluded": len(named_receipt_audits),
        "receiptCount": receipt_audit["receiptCount"],
        "receiptPolynomialHashes": receipt_audit[
            "receiptPolynomialHashes"
        ],
        "outboxFilesExcluded": outbox_index["outboxFiles"],
        "outboxPolynomialHashesExcluded": len(outbox_hashes),
        "acceptedScoreablePairs": len(snapshot["owned"]),
        "targetRows": len(snapshot["targets"]),
        "coefficientMaterialIncluded": False,
        "heavyWorkerLaunched": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    lane.exclusive_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        lane.GuardFailure,
        FileExistsError,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

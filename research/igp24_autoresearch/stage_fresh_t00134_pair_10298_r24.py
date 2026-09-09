#!/usr/bin/env python3
"""Stage the exact 24T10298/r24 pair factor offline; never submit it."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import run_low_contention_remaining_batch as offline
import run_low_contention_sequential as lane
import stage_frobenius_gold as frobenius


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PACKET = DATA / "fresh_t00134_top10000_pair_pilot_10513_r24_idx863.jsonl"
FROBENIUS = (
    DATA
    / "fresh_t00134_top10000_pair_pilot_10513_r24_idx863_frobenius.json"
)
AUDIT = (
    DATA / "fresh_t00134_top10000_pair_pilot_10513_r24_idx863_audit.json"
)
MANIFEST = ROOT / "outbox/fresh_t00134_pair_24T10298_r24_20260730.txt"
CERTIFICATE = (
    DATA / "fresh_t00134_pair_24T10298_r24_20260730_stage_certificate.json"
)
SUMMARY = DATA / "fresh_t00134_pair_24T10298_r24_20260730_stage_summary.json"

SOURCE = {
    "submissionId": "sub_d3d5ec8995bd4d4b896095b2fb27095c",
    "polynomialIndex": 863,
    "label": "24T10513",
    "r": 24,
    "coefficientSha256": (
        "1f36cf6ee20d84364820d45931314c4b601d5ce8baca83f884e42920a9d99c37"
    ),
}
TARGET = ("24T10298", 24)
CANDIDATE_HASH = (
    "197ec87b3e56ccb08fdc1359d823a13d97f42b4d34b7a47b24fc1ab5b16e4f3c"
)
EXPECTED_TARGET_STATE = {
    "teamCount": 6,
    "minimumDiscAbs": "134563615099687074959319698928077287691649024",
    "discovered": True,
}


def rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise lane.GuardFailure(message)


def other_outbox_exclusions(
    connection: sqlite3.Connection,
    pair_index: dict[str, set[tuple[str, int]]],
) -> tuple[set[str], set[tuple[str, int]], dict]:
    hashes: set[str] = set()
    canonical_rows = 0
    nonempty_files = 0
    artifacts = []
    paths = [
        path
        for path in sorted((ROOT / "outbox").glob("*.txt"))
        if path.resolve() != MANIFEST.resolve()
    ]
    for path in paths:
        file_hashes = outbox_audit.manifest_hashes(path)
        rows_here = sum(
            bool(line.strip())
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        require(
            rows_here == len(file_hashes),
            f"duplicate coefficient row in other outbox: {lane.relative(path)}",
        )
        nonempty_files += bool(rows_here)
        canonical_rows += rows_here
        hashes.update(file_hashes)
        artifacts.append(
            {**lane.artifact(path), "canonicalPolynomialRows": rows_here}
        )
    pairs = {
        pair
        for digest in hashes
        for pair in pair_index.get(digest, set())
    }
    pairs.update(
        outbox_audit.query_ledger_pairs_for_hashes(connection, hashes)
    )
    return hashes, pairs, {
        "outboxFiles": len(paths),
        "nonemptyOutboxFiles": nonempty_files,
        "canonicalPolynomialRows": canonical_rows,
        "distinctCoefficientHashes": len(hashes),
        "distinctPairsExcluded": len(pairs),
        "coefficientHashSetSha256": pair_routes.canonical_digest(
            sorted(hashes)
        ),
        "pairSetSha256": pair_routes.canonical_digest(
            [
                [label, r]
                for label, r in sorted(
                    pairs, key=pair_routes.pair_sort_key
                )
            ]
        ),
        "artifactIndexSha256": pair_routes.canonical_digest(artifacts),
        "intendedManifestExcluded": lane.relative(MANIFEST),
    }


def main() -> int:
    for path in (CERTIFICATE, SUMMARY):
        if path.exists():
            raise lane.GuardFailure(f"refusing to overwrite: {lane.relative(path)}")

    packet_rows = rows(PACKET)
    proof = lane.read_json(FROBENIUS)
    joined = frobenius.join_resolved_assignments(
        proof, packet_rows, PACKET, allow_unresolved=False
    )
    matches = [
        row
        for row in joined
        if (str(row["targetLabel"]), int(row["targetR"])) == TARGET
    ]
    require(len(packet_rows) == 1, "pair packet cardinality changed")
    require(len(joined) == 3, "Frobenius join is not exactly three factors")
    require(len(matches) == 1, "target factor did not resolve uniquely")
    candidate_metadata = {
        int(row["factorIndex"]): row
        for row in packet_rows[0].get("candidates") or []
    }
    candidate = {
        **matches[0],
        **candidate_metadata[int(matches[0]["factorIndex"])],
    }
    line = frobenius.validated_coefficient_line(
        candidate,
        (
            SOURCE["submissionId"],
            SOURCE["polynomialIndex"],
            SOURCE["label"],
            SOURCE["r"],
        ),
    )
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    require(digest == CANDIDATE_HASH, "canonical candidate hash changed")
    require(
        int(candidate["factorIndex"]) == 0
        and int(candidate["targetT"]) == 10298,
        "resolved target factor identity changed",
    )

    with lane.connect_ro() as connection:
        candidate_snapshot = pair_routes.exact_candidate_and_receipt_snapshot(
            connection
        )
        supplemental, supplemental_artifacts = (
            outbox_audit.supplemental_exact_pair_index()
        )
        pair_index = outbox_audit.merged_pair_index(
            candidate_snapshot, supplemental
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            other_outbox_exclusions(connection, pair_index)
        )
        source = connection.execute(
            """
            SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r,
                   v.field_disc_abs
            FROM polynomials AS p
            JOIN verifications AS v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (SOURCE["submissionId"], SOURCE["polynomialIndex"]),
        ).fetchone()
        target = connection.execute(
            """
            SELECT t,team_count,minimum_disc_abs,discovered,generated_at
            FROM targets WHERE label=? AND r=?
            """,
            TARGET,
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", TARGET
        ).fetchone()
        owned = connection.execute(
            """
            SELECT 1 FROM verifications
            WHERE label=? AND r=? AND scoreable=1 LIMIT 1
            """,
            TARGET,
        ).fetchone()
        known_hash = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (CANDIDATE_HASH,),
        ).fetchone()

    require(
        source is not None
        and (
            str(source["coefficient_hash"]),
            str(source["status"]),
            int(source["scoreable"]),
            str(source["label"]),
            int(source["r"]),
        )
        == (
            SOURCE["coefficientSha256"],
            "accepted",
            1,
            SOURCE["label"],
            SOURCE["r"],
        ),
        "immutable accepted-scoreable source pin failed",
    )
    require(target is not None, "target is absent from the current cache")
    require(
        int(target["t"]) == 10298
        and int(target["team_count"]) == EXPECTED_TARGET_STATE["teamCount"]
        and str(target["minimum_disc_abs"])
        == EXPECTED_TARGET_STATE["minimumDiscAbs"]
        and bool(target["discovered"]) is EXPECTED_TARGET_STATE["discovered"],
        "current target gate changed",
    )
    require(baseline is None, "target is a baseline pair")
    require(owned is None, "target is already locally owned")
    require(known_hash is None, "candidate hash is already in the ledger")
    require(
        CANDIDATE_HASH not in candidate_snapshot["receiptHashes"]
        and TARGET not in candidate_snapshot["receiptPairs"],
        "candidate hash or pair is already receipted",
    )
    require(
        CANDIDATE_HASH not in outbox_hashes and TARGET not in outbox_pairs,
        "candidate hash or pair is reserved in another outbox",
    )

    manifest_text = line + "\n"
    if MANIFEST.exists():
        require(
            MANIFEST.read_text(encoding="ascii") == manifest_text,
            "pre-existing intended manifest differs from exact candidate",
        )
    else:
        lane.exclusive_text(MANIFEST, manifest_text)
    offline_check = offline.offline_manifest_check(
        MANIFEST, [CANDIDATE_HASH], candidate_snapshot["receiptHashes"]
    )
    require(
        offline_check["manifestHash"]
        == hashlib.sha256(manifest_text.encode("ascii")).hexdigest(),
        "offline manifest hash changed",
    )

    now = datetime.now(timezone.utc).isoformat()
    candidate_disc = str(candidate["fieldDiscriminantAbs"])
    projected = Fraction(1, 2 ** int(target["team_count"]))
    certificate = {
        "schemaVersion": "fresh-t00134-pair-10298-r24-stage-certificate-v1",
        "createdAt": now,
        "status": "certified_exact_novel_current_tc6_staged_not_submitted",
        "source": {
            **SOURCE,
            "fieldDiscriminantAbs": str(source["field_disc_abs"]),
            "ledgerStatus": "accepted_scoreable",
        },
        "exactProof": {
            "packet": lane.artifact(PACKET),
            "frobenius": lane.artifact(FROBENIUS),
            "prestageAudit": lane.artifact(AUDIT),
            "factorIndex": 0,
            "assignmentStatus": "resolved",
            "assignmentMethod": str(proof["method"]),
            "jointEliminatingPrime": 13,
            "targetMultiplicity": {"24T10298": 1, "24T11781": 2},
            "exactSquarefreeFactorDegrees": [12, 24, 24, 24, 96, 96],
            "irreducibilityProof": "pinned_pair_sum_one_sage_certified_multi",
            "fieldDiscriminantProof": "exact_nfdisc",
        },
        "candidate": {
            "label": TARGET[0],
            "r": TARGET[1],
            "coefficientSha256": CANDIDATE_HASH,
            "coefficientBytes": int(candidate["coefficientBytes"]),
            "primitiveMonicDegree24": True,
            "irreducible": True,
            "fieldDiscriminantAbs": candidate_disc,
            "polynomialDiscriminantAbs": str(
                candidate["polynomialDiscriminantAbs"]
            ),
            "ledgerKnownHash": False,
            "receiptHash": False,
            "otherOutboxHash": False,
        },
        "currentTargetGate": {
            "label": TARGET[0],
            "r": TARGET[1],
            "teamCount": int(target["team_count"]),
            "minimumDiscAbs": str(target["minimum_disc_abs"]),
            "discovered": bool(target["discovered"]),
            "generatedAt": str(target["generated_at"]),
            "baseline": False,
            "locallyOwned": False,
            "receiptReserved": False,
            "otherOutboxReserved": False,
            "candidateImprovesCurrentMinimum": (
                int(candidate_disc) < int(target["minimum_disc_abs"])
            ),
        },
        "exclusionBoundary": {
            "receiptAudit": {
                key: value
                for key, value in candidate_snapshot["receiptAudit"].items()
                if key != "audit"
            },
            "otherOutboxAudit": {
                key: outbox_meta[key]
                for key in (
                    "outboxFiles",
                    "nonemptyOutboxFiles",
                    "canonicalPolynomialRows",
                    "distinctCoefficientHashes",
                    "distinctPairsExcluded",
                    "coefficientHashSetSha256",
                    "pairSetSha256",
                )
            },
            "supplementalPairMaps": supplemental_artifacts,
        },
        "manifest": {
            **lane.artifact(MANIFEST),
            "bytes": MANIFEST.stat().st_size,
            "polynomials": 1,
            "canonicalCoefficientSha256": CANDIDATE_HASH,
        },
        "offlineDryRun": offline_check,
        "scoreProjection": {
            "formula": "2^(-current_cached_team_count), before discriminant penalty",
            "projectedMarginalScore": float(projected),
            "projectedMarginalScoreExact": str(projected),
            "prestageAuditNetRelativeSwingUsingPublishedPoints": (
                0.028142347729339143
            ),
        },
        "checks": {
            "oneCanonicalPolynomialOnly": True,
            "sourcePinnedAcceptedScoreable": True,
            "exactFrobeniusAssignmentResolved": True,
            "targetCurrentCacheGatePassed": True,
            "baselineOwnedReceiptOtherOutboxExclusionsPassed": True,
            "coefficientPayloadConfinedToManifest": True,
        },
        "coefficientMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    lane.exclusive_json(CERTIFICATE, certificate)

    summary = {
        "schemaVersion": "fresh-t00134-pair-10298-r24-stage-summary-v1",
        "createdAt": now,
        "status": certificate["status"],
        "pair": f"{TARGET[0]}/r{TARGET[1]}",
        "coefficientSha256": CANDIDATE_HASH,
        "polynomials": 1,
        "projectedMarginalScoreExact": str(projected),
        "projectedNetRelativeSwing": 0.028142347729339143,
        "manifest": {
            **lane.artifact(MANIFEST),
            "bytes": MANIFEST.stat().st_size,
            "polynomials": 1,
        },
        "certificate": lane.artifact(CERTIFICATE),
        "coefficientMaterialIncluded": False,
        "submissionCalls": 0,
    }
    lane.exclusive_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
        json.JSONDecodeError,
        lane.GuardFailure,
    ) as exc:
        print(f"error: {exc}")
        raise SystemExit(1)

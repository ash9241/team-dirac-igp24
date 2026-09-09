#!/usr/bin/env python3
"""Build the sealed-F7 current tc2/tc3 negative-twist fast wave.

The expensive signature work is reused from the immutable exact rational
Sturm audit.  This light-only program rejoins every cached source to the live
ledger, exact generic-twist action map, current target snapshot, complete
candidate-pair corpus, receipts, and txt outboxes.  It emits one
coefficient-bearing candidate JSONL plus a coefficient-free certificate, but
does not stage or submit anything.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as outbox_audit
import stage_single_exact_census as exact
from stage_v14_negative_twist import is_prime, squarefree_mod_prime


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
SIGNATURE_AUDIT = DATA / "agent_f7_negative_twist_signature_audit.jsonl"
OUTPUT = DATA / "current_tc2_tc3_negative_twist_candidates_20260729.jsonl"
CERTIFICATE = DATA / "current_tc2_tc3_negative_twist_certificate_20260729.json"
PRIME_START = 10009


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def artifact(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": sha256_path(path),
    }


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite differing artifact: {path}")
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def action_index() -> dict[str, tuple[int, dict, str]]:
    result = {}
    for record, raw in enumerate(
        ACTION_MAP.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        row = json.loads(raw)
        label = str(row["sourceLabel"])
        if label in result:
            raise ValueError(f"duplicate action row for {label}")
        result[label] = (
            record,
            row,
            hashlib.sha256(canonical_json(row)).hexdigest(),
        )
    return result


def live_boundary(connection: sqlite3.Connection):
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    targets = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute("SELECT * FROM targets")
    }
    live = {
        pair
        for pair, row in targets.items()
        if int(row["team_count"]) in (2, 3)
        and int(row["discovered"]) == 1
        and pair not in baseline
        and pair not in owned
    }
    return baseline, owned, targets, live


def source_row(
    connection: sqlite3.Connection, submission_id: str, polynomial_index: int
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,
               v.scoreable,v.in_baseline,v.scoring_status,v.field_disc_abs,
               s.created_at
        FROM polynomials p
        JOIN verifications v USING(submission_id,polynomial_index)
        JOIN submissions s USING(submission_id)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"cached source disappeared: {submission_id}:{polynomial_index}")
    return row


def candidate_from_audit(
    connection: sqlite3.Connection,
    actions: dict[str, tuple[int, dict, str]],
    audit_record: int,
    audit: dict,
    audit_row_sha256: str,
    pair: tuple[str, int],
    target: dict,
) -> dict:
    submission_id = str(audit["sourceSubmissionId"])
    polynomial_index = int(audit["sourcePolynomialIndex"])
    source = source_row(connection, submission_id, polynomial_index)
    source_hash = str(audit["sourceCoefficientSha256"])
    source_label = str(audit["sourceLabel"])
    source_r = int(audit["sourceR"])
    if (
        str(source["coefficient_hash"]) != source_hash
        or str(source["label"]) != source_label
        or int(source["r"]) != source_r
        or str(source["status"]) != "accepted"
        or int(source["scoreable"] or 0) != 1
        or int(source["in_baseline"] or 0) != 0
        or str(source["scoring_status"]) != "scoreable"
    ):
        raise ValueError(f"cached accepted source pin changed: {submission_id}:{polynomial_index}")
    line = str(source["coefficients"])
    values = [int(value) for value in line.split(",")]
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] == 0
        or math.gcd(*values) != 1
        or any(values[index] != 0 for index in range(1, 25, 2))
        or hashlib.sha256(line.encode("ascii")).hexdigest() != source_hash
        or int(audit.get("sourceCoefficientBytes", -1)) != len(line.encode("ascii"))
    ):
        raise ValueError(f"cached source reconstruction failed: {source_hash}")

    action_record, action, action_row_sha256 = actions[source_label]
    systems = list(action.get("systems") or [])
    target_labels = list(map(str, action.get("targetLabels") or []))
    if (
        int(action.get("systemCount", -1)) != 1
        or len(systems) != 1
        or target_labels != [pair[0]]
        or str(systems[0].get("targetLabel")) != pair[0]
        or systems[0].get("flipInSource") is not True
        or list(map(str, audit.get("actionTargetLabels") or [])) != target_labels
        or int(audit.get("actionSystemCount", -1)) != 1
    ):
        raise ValueError(f"cached route is not an exact unique same-label action: {source_label}")
    quotient_roots = int(audit["quotientRealRootCount"])
    target_r = int(audit["negativeTwistRealRootCount"])
    if (
        target_r != 2 * quotient_roots - source_r
        or pair != (pair[0], target_r)
    ):
        raise ValueError(f"cached exact negative signature changed: {source_hash}")

    prime = PRIME_START
    while True:
        while not is_prime(prime):
            prime += 1
        if squarefree_mod_prime(values, prime):
            break
        prime += 1
    twist_d = -prime
    twisted = [0] * 25
    for index in range(13):
        twisted[2 * index] = values[2 * index] * twist_d ** (12 - index)
    candidate_line = ",".join(str(value) for value in twisted)
    candidate_hash = hashlib.sha256(candidate_line.encode("ascii")).hexdigest()
    if (
        len(twisted) != 25
        or twisted[-1] != 1
        or twisted[0] == 0
        or math.gcd(*twisted) != 1
    ):
        raise ValueError(f"constructed twist is noncanonical: {source_hash}")

    team_count = int(target["team_count"])
    return {
        "schemaVersion": "current-tc2-tc3-negative-twist-candidate-v1",
        "status": "certified_exact_cached_sturm_novel_unstaged",
        "coefficientLine": candidate_line,
        "coefficientSha256": candidate_hash,
        "coefficientBytes": len(candidate_line.encode("ascii")),
        "sourceSubmissionId": submission_id,
        "sourcePolynomialIndex": polynomial_index,
        "sourceCoefficientSha256": source_hash,
        "sourceFieldDiscriminantAbs": str(source["field_disc_abs"]),
        "sourceCreatedAt": str(source["created_at"]),
        "sourceLabel": source_label,
        "sourceR": source_r,
        "targetLabel": pair[0],
        "targetR": pair[1],
        "targetTeamCount": team_count,
        "targetGeneratedAt": str(target["generated_at"]),
        "targetMinimumDiscAbs": str(target["minimum_disc_abs"]),
        "projectedMarginalScoreExact": fraction_text(Fraction(1, 2**team_count)),
        "signatureAuditRecord": audit_record,
        "signatureAuditRowSha256": audit_row_sha256,
        "quotientRealRootCount": quotient_roots,
        "actionRecord": action_record,
        "actionRowSha256": action_row_sha256,
        "ramificationPrime": prime,
        "twistD": twist_d,
        "sourceSquarefreeModRamificationPrime": True,
        "primitiveMonicDegree24": True,
        "irreducible": True,
        "irreducibilityProof": (
            "The accepted source has a transitive exact degree-24 action. "
            "The displayed prime is squarefree for the source, so its splitting "
            "field is unramified there, while Q(sqrt(twistD)) is ramified and "
            "linearly disjoint. The pinned unique block action contains the "
            "global flip and has the same transitive target label; therefore "
            "the constructed negative twist is irreducible with that exact label."
        ),
    }


def main() -> int:
    actions = action_index()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        baseline, owned, targets, live = live_boundary(connection)
        routes_by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
        cached_rows = 0
        for record, raw in enumerate(
            SIGNATURE_AUDIT.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            cached_rows += 1
            audit = json.loads(raw)
            action_entry = actions.get(str(audit["sourceLabel"]))
            if action_entry is None:
                continue
            action = action_entry[1]
            target_labels = list(map(str, action.get("targetLabels") or []))
            if len(target_labels) != 1 or int(action.get("systemCount", -1)) != 1:
                continue
            pair = (target_labels[0], int(audit["negativeTwistRealRootCount"]))
            if pair not in live:
                continue
            route = candidate_from_audit(
                connection,
                actions,
                record,
                audit,
                hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                pair,
                targets[pair],
            )
            routes_by_pair[pair].append(route)

        all_routes = [route for routes in routes_by_pair.values() for route in routes]
        if cached_rows != 14993 or len(all_routes) != 14 or len(routes_by_pair) != 12:
            raise ValueError(
                f"sealed F7 current join changed: cached={cached_rows}, "
                f"routes={len(all_routes)}, pairs={len(routes_by_pair)}"
            )
        selected = [
            min(
                routes,
                key=lambda row: (
                    int(row["coefficientBytes"]),
                    str(row["sourceCoefficientSha256"]),
                ),
            )
            for _pair, routes in sorted(
                routes_by_pair.items(), key=lambda item: (int(item[0][0][3:]), item[0][1])
            )
        ]
        selected.sort(
            key=lambda row: (
                int(row["targetTeamCount"]),
                int(str(row["targetLabel"])[3:]),
                int(row["targetR"]),
            )
        )
        selected_hashes = {str(row["coefficientSha256"]) for row in selected}
        selected_pairs = {
            (str(row["targetLabel"]), int(row["targetR"])) for row in selected
        }
        if len(selected_hashes) != 12 or len(selected_pairs) != 12:
            raise ValueError("selected F7 wave is not hash/pair distinct")

        ledger_hashes = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        if selected_hashes & ledger_hashes:
            raise ValueError("selected twist hash already exists in ledger")

        corpus_candidates, corpus = exact.scan_candidates(DATA.resolve())
        pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for candidate in corpus_candidates:
            pair_index[str(candidate["coefficientSha256"])].add(
                (str(candidate["targetLabel"]), int(candidate["targetR"]))
            )
        for row in selected:
            pair_index[str(row["coefficientSha256"])].add(
                (str(row["targetLabel"]), int(row["targetR"]))
            )
        receipt_hashes, receipt_pairs, receipt_meta = exact.receipt_exclusions(
            RECEIPTS, DATA, connection, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
            pair_index, connection
        )
        if (
            selected_hashes & receipt_hashes
            or selected_hashes & outbox_hashes
            or selected_pairs & receipt_pairs
            or selected_pairs & outbox_pairs
        ):
            raise ValueError("selected F7 wave intersects a receipt or txt outbox")

        # Recheck every live target after the slower complete artifact scan.
        for row in selected:
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            current = connection.execute(
                "SELECT team_count,discovered,generated_at FROM targets "
                "WHERE label=? AND r=?",
                pair,
            ).fetchone()
            if (
                current is None
                or int(current["team_count"]) != int(row["targetTeamCount"])
                or int(current["team_count"]) not in (2, 3)
                or int(current["discovered"]) != 1
                or pair in baseline
                or pair in owned
            ):
                raise ValueError(f"selected target changed during audit: {pair}")
    finally:
        connection.close()

    payload = (
        "\n".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in selected
        )
        + "\n"
    ).encode("utf-8")
    write_new(OUTPUT, payload)

    tc_counts = {
        str(team_count): sum(
            int(row["targetTeamCount"]) == team_count for row in selected
        )
        for team_count in (2, 3)
    }
    projection = sum(
        (
            Fraction(1, 2 ** int(row["targetTeamCount"]))
            for row in selected
        ),
        Fraction(0),
    )
    if tc_counts != {"2": 4, "3": 8} or projection != Fraction(2):
        raise ValueError(f"current F7 score distribution changed: {tc_counts}, {projection}")
    created_at = datetime.now(timezone.utc).isoformat()
    corpus_for_certificate = corpus
    if CERTIFICATE.exists():
        existing = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
        if (
            existing.get("schemaVersion")
            != "current-tc2-tc3-negative-twist-certificate-v1"
            or not existing.get("createdAt")
            or not isinstance(existing.get("candidateCorpusAudit"), dict)
        ):
            raise ValueError("existing certificate identity changed")
        created_at = str(existing["createdAt"])
        # The corpus index is the exact pre-output snapshot sealed by the
        # original certificate.  On revalidation our own newly written
        # candidate/certificate files are naturally additional data artifacts,
        # so retain the pinned historical index while recomputing every live
        # exclusion from the expanded corpus.
        corpus_for_certificate = dict(existing["candidateCorpusAudit"])
    certificate = {
        "schemaVersion": "current-tc2-tc3-negative-twist-certificate-v1",
        "createdAt": created_at,
        "status": "certified_12_exact_current_tc2_tc3_negative_twists_unstaged",
        "method": (
            "sealed exact rational-Sturm signatures + accepted source pins + "
            "unique same-label global-flip actions + fresh-prime "
            "ramification/disjointness + complete receipt/outbox candidate corpus"
        ),
        "coefficientMaterialIncluded": False,
        "signatureAuditArtifact": {
            **artifact(SIGNATURE_AUDIT),
            "rows": cached_rows,
        },
        "actionArtifact": artifact(ACTION_MAP),
        "candidateArtifact": artifact(OUTPUT),
        "cachedCurrentRouteRowsBeforePairDedup": len(all_routes),
        "duplicatePairAlternativesExcluded": len(all_routes) - len(selected),
        "candidateRows": len(selected),
        "distinctCandidateHashes": len(selected_hashes),
        "distinctTargetPairs": len(selected_pairs),
        "teamCountDistribution": tc_counts,
        "projectedMarginalScoreExact": fraction_text(projection),
        "targets": [
            {
                "pair": pair_text(
                    (str(row["targetLabel"]), int(row["targetR"]))
                ),
                "teamCount": int(row["targetTeamCount"]),
                "candidateSha256": str(row["coefficientSha256"]),
                "sourcePin": {
                    "submissionId": row["sourceSubmissionId"],
                    "polynomialIndex": row["sourcePolynomialIndex"],
                    "coefficientSha256": row["sourceCoefficientSha256"],
                    "pair": pair_text(
                        (str(row["sourceLabel"]), int(row["sourceR"]))
                    ),
                },
                "signatureAuditRecord": int(row["signatureAuditRecord"]),
                "projectedMarginalScoreExact": row[
                    "projectedMarginalScoreExact"
                ],
            }
            for row in selected
        ],
        "proofChecks": {
            "sealedSignatureAuditAll14993RowsRejoined": True,
            "allCachedSignaturesExactByRationalSturm": True,
            "allSourcesAcceptedScoreableNonbaselineAndHashPinned": True,
            "allSourcesPrimitiveMonicEvenDegree24": True,
            "allActionsUniqueSameLabelWithGlobalFlip": True,
            "allCandidatesHaveFreshSquarefreeRamificationPrime": True,
            "allCandidatesPrimitiveMonicDegree24": True,
            "allCandidatesIrreducibleByExactTransitiveActionProof": True,
            "allCandidateHashesAbsentFromLedger": True,
            "allTargetsCurrentTc2OrTc3DiscoveredNonbaselineAndLocallyUnknown": True,
            "allCandidateHashesAndPairsAbsentFromReceipts": True,
            "allCandidateHashesAndPairsAbsentFromTxtOutboxes": True,
            "candidateHashesAndTargetPairsDuplicateFree": True,
            "completeKnownCandidateCorpusUsedForPairExclusions": True,
        },
        "candidateCorpusAudit": corpus_for_certificate,
        "exclusionAudit": {
            "receiptCount": int(receipt_meta["receiptCount"]),
            "receiptHashes": len(receipt_hashes),
            "receiptPairs": len(receipt_pairs),
            "outboxFiles": int(outbox_meta["outboxFiles"]),
            "outboxHashes": len(outbox_hashes),
            "outboxPairs": len(outbox_pairs),
        },
        "sideEffects": {
            "candidateArtifactWrites": 1,
            "certificateWrites": 1,
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "ledgerWrites": 0,
            "outboxWrites": 0,
            "receiptWrites": 0,
            "submissionCalls": 0,
        },
    }
    write_new(
        CERTIFICATE,
        (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "candidates": artifact(OUTPUT),
                "certificate": artifact(CERTIFICATE),
                "candidateRows": len(selected),
                "teamCountDistribution": tc_counts,
                "projectedMarginalScoreExact": fraction_text(projection),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

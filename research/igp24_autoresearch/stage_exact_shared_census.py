#!/usr/bin/env python3
"""Audit every saved exact/shared MULTI-output candidate without submitting.

This is an offline, receipt-aware closure pass over three independently
checkable local proof families:

* stable assignments in certified pair-sum MULTI packets;
* exact assignments in Frobenius exclusion certificates; and
* unresolved Frobenius factors for which *every* remaining exact outcome is
  a current safe pair (an all-compatible shared polynomial).

The ledger is opened read-only.  Baseline pairs, scoreably owned pairs, known
polynomial hashes, and both the hashes and possible pairs of every filesystem
receipt are excluded.  Every construction source is rejoined to an accepted,
scoreable ledger row.  Coefficients are written only to an optional manifest;
the certificate and summary are coefficient-free.  No Sage/GAP, network, or
submission operation is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

import stage_all_exact_frobenius_unowned as all_exact
import stage_frobenius_gold as frobenius
import stage_pair_sum_multi_stable as stable
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RECEIPTS = ROOT / "receipts"
DATABASE = DATA / "ledger.sqlite3"
SIGNATURES = DATA / "pair_signature_map.jsonl"
CERTIFICATE = DATA / "exact_shared_census_20260722_certificate.json"
SUMMARY = DATA / "exact_shared_census_20260722_summary.json"
MANIFEST = ROOT / "outbox" / "exact_shared_census_20260722.txt"
METHOD = "receipt-aware-exact-and-all-compatible-multi-census-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def display_path(path: Path, root: Path = ROOT) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(root.expanduser().resolve()))
    except ValueError:
        return str(resolved)


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new(path: Path, payload: bytes) -> None:
    """Atomically create a sealed output, accepting an identical prior file."""

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite nonidentical sealed output: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def iter_jsonl_objects(path: Path):
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            # An unrelated JSONL file is not part of this allowlisted proof
            # family.  A file containing certified_multi must be wholly valid,
            # but there is no safe way to infer that from malformed JSON.
            return
        if isinstance(value, dict):
            yield line_number, value


def source_evidence(
    connection: sqlite3.Connection,
    key: tuple[str, int, str, int],
) -> dict | None:
    rows = connection.execute(
        """
        SELECT p.coefficient_hash,v.status,v.label,v.t,v.r,v.scoreable
        FROM polynomials p JOIN verifications v
          ON v.submission_id=p.submission_id
         AND v.polynomial_index=p.polynomial_index
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        key[:2],
    ).fetchall()
    if len(rows) != 1:
        return None
    digest, status, label, t_value, r_value, scoreable = rows[0]
    if (
        str(status) != "accepted"
        or str(label) != key[2]
        or int(t_value) != frobenius.label_t(key[2])
        or int(r_value) != key[3]
        or int(scoreable or 0) != 1
    ):
        return None
    return {
        "coefficientSha256": str(digest),
        "label": key[2],
        "polynomialIndex": key[1],
        "r": key[3],
        "scoreable": True,
        "status": "accepted",
        "submissionId": key[0],
    }


def new_exact(
    *,
    line: str,
    digest: str,
    pair: tuple[str, int],
    polynomial_disc: int,
    field_disc: int | None,
    proof: dict,
    source: tuple[str, int, str, int],
    family: str,
) -> dict:
    return {
        "coefficientBytes": len(line.encode("ascii")),
        "coefficientLine": line,
        "coefficientSha256": digest,
        "families": {family},
        "fieldDiscriminantAbs": field_disc,
        "pair": pair,
        "polynomialDiscriminantAbs": polynomial_disc,
        "proofs": [proof],
        "sourceKeys": {source},
    }


def merge_exact(pool: dict[str, dict], candidate: dict) -> None:
    digest = candidate["coefficientSha256"]
    incumbent = pool.get(digest)
    if incumbent is None:
        pool[digest] = candidate
        return
    fixed = (
        "coefficientLine",
        "coefficientBytes",
        "pair",
        "polynomialDiscriminantAbs",
    )
    if any(incumbent[key] != candidate[key] for key in fixed):
        raise ValueError(f"conflicting exact claims for coefficient hash {digest}")
    first_field = incumbent["fieldDiscriminantAbs"]
    second_field = candidate["fieldDiscriminantAbs"]
    if first_field is not None and second_field is not None and first_field != second_field:
        raise ValueError(f"conflicting field discriminants for coefficient hash {digest}")
    if first_field is None:
        incumbent["fieldDiscriminantAbs"] = second_field
    incumbent["families"].update(candidate["families"])
    incumbent["proofs"].extend(candidate["proofs"])
    incumbent["sourceKeys"].update(candidate["sourceKeys"])


def scan_stable_multi(data_dir: Path, signatures_path: Path) -> tuple[dict, dict]:
    signatures = stable.load_signatures(signatures_path)
    pool: dict[str, dict] = {}
    counts: Counter = Counter()
    files = sorted(path for path in data_dir.rglob("*.jsonl") if path.is_file())
    for path in files:
        for record_number, row in iter_jsonl_objects(path):
            if row.get("status") != "certified_multi":
                continue
            counts["certifiedMultiOccurrences"] += 1
            try:
                source = stable.source_key(row)
                signature = signatures.get(source[2])
                if signature is None:
                    counts["missingCertifiedSignature"] += 1
                    continue
                assignments, _proof = stable.stable_assignments(row, signature)
            except (KeyError, TypeError, ValueError):
                counts["rejectedInvalidOrIncompleteStableProof"] += 1
                continue
            counts["validatedStableOccurrences"] += 1
            if assignments:
                counts["stableOccurrencesWithAssignments"] += 1
            for factor in assignments:
                line, polynomial_disc = stable.validated_coefficient(factor, source)
                digest = str(factor["coefficientSha256"])
                pair = (str(factor["targetLabel"]), int(factor["targetR"]))
                merge_exact(
                    pool,
                    new_exact(
                        line=line,
                        digest=digest,
                        pair=pair,
                        polynomial_disc=polynomial_disc,
                        field_disc=None,
                        proof={
                            "artifact": display_path(path),
                            "artifactSha256": all_exact.sha256_path(path),
                            "factorIndex": int(factor["factorIndex"]),
                            "record": record_number,
                            "schema": "stable-certified-multi-assignment-v1",
                        },
                        source=source,
                        family="stable_multi",
                    ),
                )
                counts["stableAssignmentOccurrences"] += 1
    counts["artifactJsonlFiles"] = len(files)
    counts["uniqueStableCoefficientHashes"] = len(pool)
    return pool, dict(sorted(counts.items()))


def add_frobenius_exact(
    exact_pool: dict[str, dict], frobenius_pool: dict[str, dict]
) -> None:
    for digest, row in frobenius_pool.items():
        field_values = row["fieldDiscriminants"]
        field_disc = next(iter(field_values)) if field_values else None
        for proof in row["proofs"]:
            source = (
                str(proof["sourceSubmissionId"]),
                int(proof["sourcePolynomialIndex"]),
                str(proof["sourceLabel"]),
                int(proof["sourceR"]),
            )
            merge_exact(
                exact_pool,
                new_exact(
                    line=str(row["coefficientLine"]),
                    digest=digest,
                    pair=row["pair"],
                    polynomial_disc=int(row["polynomialDiscriminantAbs"]),
                    field_disc=field_disc,
                    proof={**proof, "schema": "exact-frobenius-assignment-v1"},
                    source=source,
                    family="exact_frobenius",
                ),
            )


def candidate_packet_map(rows: list[dict]) -> dict[tuple, dict]:
    packets = {}
    for row in rows:
        if row.get("status") != "certified_multi":
            continue
        key = frobenius.source_key(row)
        if key in packets:
            raise ValueError(f"duplicate certified-multi source key: {key}")
        packets[key] = row
    return packets


def unresolved_options(
    certificates: list[tuple[Path, dict]], root: Path
) -> tuple[dict[str, dict], dict]:
    """Return the narrowest independently certified option set per hash."""

    options: dict[str, dict] = {}
    blocked_hashes: set[str] = set()
    counts: Counter = Counter()
    for certificate_path, certificate in certificates:
        input_path = all_exact.resolve_saved_input(
            certificate_path, certificate.get("input"), root
        )
        rows = frobenius.read_jsonl(input_path)
        # This validates the method, input digest, selected-row digest, status
        # census, and every resolved row in the same certificate.
        frobenius.join_resolved_assignments(
            certificate, rows, input_path, allow_unresolved=True
        )
        packets = candidate_packet_map(rows)
        certificate_sha = all_exact.sha256_path(certificate_path)
        input_sha = all_exact.sha256_path(input_path)
        for row_index, proof_row in enumerate(certificate.get("rows") or []):
            if proof_row.get("status") != "unresolved":
                continue
            counts["unresolvedProofRows"] += 1
            source = frobenius.source_key(proof_row)
            packet = packets[source]
            if "workerExitCode" in packet and int(packet["workerExitCode"]) != 0:
                raise ValueError(f"failed worker in unresolved packet: {source}")
            candidates = {
                int(candidate["factorIndex"]): candidate
                for candidate in packet.get("candidates") or []
            }
            if sorted(candidates) != list(range(len(candidates))) or not candidates:
                raise ValueError(f"invalid unresolved candidate indexes: {source}")
            targets = packet.get("orbitTargets") or []
            if not isinstance(targets, list) or len(targets) != len(candidates):
                raise ValueError(f"invalid unresolved orbit targets: {source}")
            slot_labels = []
            for target in targets:
                label = str(target["targetLabel"])
                if int(target["targetT"]) != frobenius.label_t(label):
                    raise ValueError(f"unresolved orbit target label/T mismatch: {source}")
                if int(target.get("orbitSize", 24)) != 24:
                    raise ValueError(f"non-degree-24 unresolved orbit: {source}")
                slot_labels.append(label)
            remaining = proof_row.get("remainingLabelAssignments") or []
            if not isinstance(remaining, list) or not remaining:
                raise ValueError(f"unresolved proof has no remaining assignments: {source}")
            declared = proof_row.get("remainingSlotAssignmentCount")
            # ``remainingLabelAssignments`` is deduplicated after forgetting
            # distinctions between orbit slots carrying the same label.  The
            # saved slot-assignment count can therefore be larger, but never
            # smaller, than the explicit label-assignment list.
            if declared is not None and int(declared) < len(remaining):
                raise ValueError(f"unresolved assignment count mismatch: {source}")
            normalized = []
            for assignment in remaining:
                labels = [str(label) for label in assignment]
                if len(labels) != len(candidates) or Counter(labels) != Counter(slot_labels):
                    raise ValueError(f"invalid unresolved label permutation: {source}")
                for label in labels:
                    frobenius.label_t(label)
                normalized.append(labels)
            counts["unresolvedFactorOccurrences"] += len(candidates)
            for factor_index, candidate in candidates.items():
                line = frobenius.validated_coefficient_line(candidate, source)
                digest = str(candidate["coefficientSha256"])
                polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
                target_r = int(candidate["targetR"])
                if polynomial_disc <= 0 or target_r not in range(0, 25, 2):
                    raise ValueError(f"invalid unresolved candidate metadata: {source}")
                possible = frozenset(
                    (assignment[factor_index], target_r) for assignment in normalized
                )
                proof = {
                    "artifact": display_path(certificate_path, root),
                    "artifactSha256": certificate_sha,
                    "input": display_path(input_path, root),
                    "inputSha256": input_sha,
                    "row": row_index,
                    "schema": "all-compatible-unresolved-frobenius-v1",
                }
                candidate_row = {
                    "coefficientBytes": len(line.encode("ascii")),
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "factorIndex": factor_index,
                    "polynomialDiscriminantAbs": polynomial_disc,
                    "possiblePairs": possible,
                    "proof": proof,
                    "sourceKey": source,
                }
                incumbent = options.get(digest)
                if incumbent is None:
                    options[digest] = candidate_row
                    continue
                if (
                    incumbent["coefficientLine"] != line
                    or incumbent["polynomialDiscriminantAbs"] != polynomial_disc
                ):
                    raise ValueError(f"conflicting unresolved payload metadata: {digest}")
                prior = incumbent["possiblePairs"]
                if possible < prior:
                    options[digest] = candidate_row
                elif prior < possible or prior == possible:
                    continue
                else:
                    # Two individually exact but incomparable old snapshots are
                    # not used without a purpose-built joint certificate.
                    blocked_hashes.add(digest)
    for digest in blocked_hashes:
        options.pop(digest, None)
    counts["incomparableOptionSetHashesRejected"] = len(blocked_hashes)
    counts["uniqueUnresolvedCoefficientHashes"] = len(options)
    return options, dict(sorted(counts.items()))


def target_snapshot(connection: sqlite3.Connection) -> dict[tuple[str, int], dict]:
    return {
        (str(row["label"]), int(row["r"])): {
            "discovered": bool(row["discovered"]),
            "generatedAt": str(row["generated_at"]),
            "minimumDiscAbs": (
                str(row["minimum_disc_abs"])
                if row["minimum_disc_abs"] is not None
                else None
            ),
            "teamCount": int(row["team_count"]),
            "t": int(row["t"]),
        }
        for row in connection.execute("SELECT * FROM targets")
    }


def exact_best_key(row: dict) -> tuple:
    infinity = 10**100000
    return (
        row["fieldDiscriminantAbs"]
        if row["fieldDiscriminantAbs"] is not None
        else infinity,
        row["polynomialDiscriminantAbs"],
        row["coefficientBytes"],
        row["coefficientSha256"],
    )


def public_exact(row: dict, target: dict) -> dict:
    score = Fraction(1, 2 ** target["teamCount"])
    return {
        "coefficientBytes": row["coefficientBytes"],
        "coefficientSha256": row["coefficientSha256"],
        "families": sorted(row["families"]),
        "fieldDiscriminantAbs": (
            str(row["fieldDiscriminantAbs"])
            if row["fieldDiscriminantAbs"] is not None
            else None
        ),
        "pair": f"{row['pair'][0]}/r{row['pair'][1]}",
        "polynomialDiscriminantAbs": str(row["polynomialDiscriminantAbs"]),
        "projectedMarginalScoreExact": str(score),
        "proofs": row["proofs"],
        "sources": row["sourceEvidence"],
        "target": target,
        "type": "exact",
    }


def public_shared(row: dict, targets: dict[tuple[str, int], dict]) -> dict:
    possible = sorted(
        row["possiblePairs"], key=lambda pair: (frobenius.label_t(pair[0]), pair[1])
    )
    scores = [Fraction(1, 2 ** targets[pair]["teamCount"]) for pair in possible]
    return {
        "coefficientBytes": row["coefficientBytes"],
        "coefficientSha256": row["coefficientSha256"],
        "factorIndex": row["factorIndex"],
        "polynomialDiscriminantAbs": str(row["polynomialDiscriminantAbs"]),
        "possiblePairs": [f"{label}/r{r}" for label, r in possible],
        "possibleTargets": [
            {**targets[pair], "label": pair[0], "r": pair[1]} for pair in possible
        ],
        "projectedMarginalScoreRangeExact": {
            "maximum": str(max(scores)),
            "minimum": str(min(scores)),
        },
        "proof": row["proof"],
        "source": row["sourceEvidence"],
        "type": "all-compatible-shared",
    }


def stage(args: argparse.Namespace) -> dict:
    root = args.root.expanduser().resolve()
    data_dir = args.data.expanduser().resolve()
    receipts_dir = args.receipts.expanduser().resolve()
    database = args.database.expanduser().resolve()
    signatures_path = args.signatures.expanduser().resolve()
    certificate_output = args.certificate.expanduser().resolve()
    summary_output = args.summary.expanduser().resolve()
    manifest_output = args.manifest.expanduser().resolve()
    outputs = {certificate_output, summary_output, manifest_output}
    if len(outputs) != 3 or database in outputs:
        raise ValueError("output paths must be distinct and must not overwrite the ledger")

    stable_pool, stable_audit = scan_stable_multi(data_dir, signatures_path)
    certificates, _staged_pairs = all_exact.scan_json_artifacts(data_dir)
    unresolved, unresolved_audit = unresolved_options(certificates, root)

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        frobenius_pool, certificate_audit, frobenius_audit = all_exact.collect_exact_pool(
            certificates, connection, root, None
        )
        exact_pool = stable_pool
        add_frobenius_exact(exact_pool, frobenius_pool)

        # Build a conservative hash -> possible-pair index before reading
        # receipts.  Exact SINGLE artifacts are included only to recover the
        # pair of a receipt whose ledger sync is still pending.
        single_candidates, single_audit = single.scan_candidates(data_dir)
        pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for candidate in single_candidates:
            pair_index[candidate["coefficientSha256"]].add(
                (candidate["targetLabel"], candidate["targetR"])
            )
        for digest, row in exact_pool.items():
            pair_index[digest].add(row["pair"])
        for digest, row in unresolved.items():
            pair_index[digest].update(row["possiblePairs"])
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            receipts_dir, data_dir, connection, pair_index
        )

        known_hashes = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        baseline = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        targets = target_snapshot(connection)
        source_cache: dict[tuple, dict | None] = {}

        def source_for(key: tuple) -> dict | None:
            if key not in source_cache:
                source_cache[key] = source_evidence(connection, key)
            return source_cache[key]

        exact_skips: Counter = Counter()
        exact_eligible = []
        for digest, row in exact_pool.items():
            pair = row["pair"]
            if len(pair_index[digest]) != 1:
                exact_skips["ambiguous_hash_claim"] += 1
                continue
            if digest in receipt_hashes:
                exact_skips["receipt_hash"] += 1
                continue
            if pair in receipt_pairs:
                exact_skips["receipt_pair"] += 1
                continue
            if digest in known_hashes:
                exact_skips["known_ledger_hash"] += 1
                continue
            if pair in baseline:
                exact_skips["baseline_pair"] += 1
                continue
            if pair in owned:
                exact_skips["locally_owned_scoreable_pair"] += 1
                continue
            target = targets.get(pair)
            if target is None:
                exact_skips["target_missing"] += 1
                continue
            evidence = [source_for(key) for key in sorted(row["sourceKeys"])]
            evidence = [item for item in evidence if item is not None]
            if not evidence:
                exact_skips["no_scoreable_source_provenance"] += 1
                continue
            exact_eligible.append({**row, "sourceEvidence": evidence})

        best_exact: dict[tuple[str, int], dict] = {}
        for row in exact_eligible:
            pair = row["pair"]
            incumbent = best_exact.get(pair)
            if incumbent is None or exact_best_key(row) < exact_best_key(incumbent):
                if incumbent is not None:
                    exact_skips["duplicate_exact_pair"] += 1
                best_exact[pair] = row
            else:
                exact_skips["duplicate_exact_pair"] += 1
        selected_exact = sorted(
            best_exact.values(),
            key=lambda row: (
                targets[row["pair"]]["teamCount"],
                frobenius.label_t(row["pair"][0]),
                row["pair"][1],
                row["coefficientSha256"],
            ),
        )

        shared_skips: Counter = Counter()
        shared_eligible = []
        for digest, row in unresolved.items():
            possible = row["possiblePairs"]
            if digest in exact_pool:
                shared_skips["stronger_exact_claim_available"] += 1
                continue
            if not pair_index[digest].issubset(possible):
                shared_skips["conflicting_saved_pair_claim"] += 1
                continue
            if digest in receipt_hashes:
                shared_skips["receipt_hash"] += 1
                continue
            if digest in known_hashes:
                shared_skips["known_ledger_hash"] += 1
                continue
            if any(pair in receipt_pairs for pair in possible):
                shared_skips["receipt_pair_option"] += 1
                continue
            if any(pair in baseline for pair in possible):
                shared_skips["baseline_pair_option"] += 1
                continue
            if any(pair in owned for pair in possible):
                shared_skips["locally_owned_pair_option"] += 1
                continue
            if any(pair not in targets for pair in possible):
                shared_skips["target_missing_option"] += 1
                continue
            evidence = source_for(row["sourceKey"])
            if evidence is None:
                shared_skips["no_scoreable_source_provenance"] += 1
                continue
            shared_eligible.append({**row, "sourceEvidence": evidence})

        # A batch of ambiguous polynomials is safe only when their possible
        # pair sets are mutually disjoint and disjoint from exact selections.
        occupied = set(best_exact)
        selected_shared = []
        shared_eligible.sort(
            key=lambda row: (
                -min(
                    Fraction(1, 2 ** targets[pair]["teamCount"])
                    for pair in row["possiblePairs"]
                ),
                len(row["possiblePairs"]),
                row["polynomialDiscriminantAbs"],
                row["coefficientSha256"],
            )
        )
        for row in shared_eligible:
            if occupied & row["possiblePairs"]:
                shared_skips["overlapping_batch_outcome_set"] += 1
                continue
            selected_shared.append(row)
            occupied.update(row["possiblePairs"])

        public_exact_rows = [public_exact(row, targets[row["pair"]]) for row in selected_exact]
        public_shared_rows = [public_shared(row, targets) for row in selected_shared]
        selected = public_exact_rows + public_shared_rows
        lines = [row["coefficientLine"] for row in selected_exact + selected_shared]
        hashes = [row["coefficientSha256"] for row in selected_exact + selected_shared]
        if len(hashes) != len(set(hashes)) or len(lines) != len(set(lines)):
            raise ValueError("duplicate polynomial remains in selected batch")
        manifest_payload = "".join(line + "\n" for line in lines).encode("ascii")
        exact_score = sum(
            (
                Fraction(1, 2 ** targets[row["pair"]]["teamCount"])
                for row in selected_exact
            ),
            Fraction(0, 1),
        )
        shared_min = sum(
            (
                min(
                    Fraction(1, 2 ** targets[pair]["teamCount"])
                    for pair in row["possiblePairs"]
                )
                for row in selected_shared
            ),
            Fraction(0, 1),
        )
        shared_max = sum(
            (
                max(
                    Fraction(1, 2 ** targets[pair]["teamCount"])
                    for pair in row["possiblePairs"]
                )
                for row in selected_shared
            ),
            Fraction(0, 1),
        )

        certificate_value = {
            "checks": {
                "allCandidatePayloadHashesCanonicalAndMatched": True,
                "allExactClaimsCarryAllowlistedStoredProofs": True,
                "allSharedOutcomesAreFiniteExactFrobeniusPossibilities": True,
                "allSourcesRejoinedToAcceptedScoreableLedgerRows": True,
                "baselineOwnedKnownAndReceiptHashesPairsExcluded": True,
                "certificateContainsNoCoefficientPayload": True,
                "selectedSharedOutcomeSetsArePairwiseDisjoint": True,
            },
            "corpus": {
                "exactFrobenius": frobenius_audit,
                "exactFrobeniusCertificates": certificate_audit,
                "singleProofIndexForReceiptMapping": single_audit,
                "stableMulti": stable_audit,
                "unresolvedFrobenius": unresolved_audit,
            },
            "database": display_path(database, root),
            "databaseSha256": all_exact.sha256_path(database),
            "manifest": {
                "bytes": len(manifest_payload),
                "path": display_path(manifest_output, root) if selected else None,
                "polynomials": len(selected),
                "sha256": sha256_bytes(manifest_payload),
            },
            "method": METHOD,
            "networkCalls": 0,
            "receiptExclusion": receipt_audit,
            "scoreProjection": {
                "beforeDiscriminantPenalty": True,
                "maximumExact": str(exact_score + shared_max),
                "minimumExact": str(exact_score + shared_min),
            },
            "selected": selected,
            "selectedCounts": {
                "allCompatibleShared": len(selected_shared),
                "exact": len(selected_exact),
                "polynomials": len(selected),
            },
            "selectionAudit": {
                "exactPoolUniqueHashes": len(exact_pool),
                "exactSequentialSkipCounts": dict(sorted(exact_skips.items())),
                "sharedPoolUniqueHashes": len(unresolved),
                "sharedSequentialSkipCounts": dict(sorted(shared_skips.items())),
            },
            "submissionCalls": 0,
            "targetSnapshot": {
                "generatedAtMax": max(row["generatedAt"] for row in targets.values()),
                "rows": len(targets),
            },
        }
        certificate_payload = json_bytes(certificate_value)
        summary_value = {
            "certificate": display_path(certificate_output, root),
            "certificateSha256": sha256_bytes(certificate_payload),
            "manifest": display_path(manifest_output, root) if selected else None,
            "manifestSha256": sha256_bytes(manifest_payload) if selected else None,
            "method": METHOD,
            "networkCalls": 0,
            "scoreProjection": certificate_value["scoreProjection"],
            "selectedCounts": certificate_value["selectedCounts"],
            "selectionAudit": certificate_value["selectionAudit"],
            "status": "sealed_not_submitted" if selected else "zero_safe_frontier",
            "submissionCalls": 0,
        }
        summary_payload = json_bytes(summary_value)

    write_new(certificate_output, certificate_payload)
    write_new(summary_output, summary_payload)
    if selected:
        write_new(manifest_output, manifest_payload)
    return summary_value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--signatures", type=Path, default=SIGNATURES)
    parser.add_argument("--certificate", type=Path, default=CERTIFICATE)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    args = parser.parse_args()
    try:
        result = stage(args)
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

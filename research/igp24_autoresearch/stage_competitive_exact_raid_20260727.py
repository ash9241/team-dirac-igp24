#!/usr/bin/env python3
"""Seal every currently competitive polynomial in the saved exact corpus.

The census combines three independently audited proof families: explicit
SINGLE schemas, stable certified-MULTI assignments, and resolved exact
Frobenius assignments.  Conflicting hash assignments or discriminants are a
hard failure.  The ledger is read-only and no network or submission call is
made.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import stage_all_exact_frobenius_unowned as frobenius
import stage_exact_shared_census as shared
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATABASE = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
MANIFEST = ROOT / "outbox" / "competitive_shared_exact_2_20260727.txt"
CERTIFICATE = DATA / "competitive_shared_exact_2_20260727_certificate.json"
SUMMARY = DATA / "competitive_shared_exact_2_20260727_summary.json"
METHOD = "competitive-complete-saved-exact-corpus-raid-v1"


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_payload(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def display(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def write_new_or_identical(path: Path, payload: bytes) -> str:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite nonidentical sealed output: {path}")
        return "existing_identical"
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
    return "created"


def merge(
    pool: dict[str, dict],
    *,
    line: str,
    digest: str,
    pair: tuple[str, int],
    polynomial_disc: object,
    field_discs: list[object] | set[object],
    family: str,
    proofs: list[dict],
) -> None:
    canonical = single.canonical_polynomial_line(line)
    if canonical is None or digest_bytes(canonical.encode("ascii")) != digest:
        raise ValueError(f"invalid coefficient payload/hash: {digest}")
    row = pool.setdefault(
        digest,
        {
            "coefficientLines": set(),
            "pairs": set(),
            "polynomialDiscriminants": set(),
            "fieldDiscriminants": set(),
            "families": set(),
            "proofs": [],
        },
    )
    row["coefficientLines"].add(canonical)
    row["pairs"].add(pair)
    polynomial_disc = single.positive_integer(polynomial_disc)
    if polynomial_disc is not None:
        row["polynomialDiscriminants"].add(polynomial_disc)
    for value in field_discs:
        value = single.positive_integer(value)
        if value is not None:
            row["fieldDiscriminants"].add(value)
    row["families"].add(family)
    row["proofs"].extend(proofs)


def outbox_hashes_excluding(destination: Path) -> tuple[set[str], dict]:
    hashes: set[str] = set()
    manifests = 0
    rows = 0
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        if path.resolve() == destination.resolve():
            continue
        manifests += 1
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = frobenius.canonical_line(raw)
            if line is None:
                continue
            hashes.add(digest_bytes(line.encode("ascii")))
            rows += 1
    return hashes, {
        "manifestsExcludingDestination": manifests,
        "polynomialRows": rows,
        "uniqueCoefficientHashes": len(hashes),
    }


def run() -> dict:
    pool: dict[str, dict] = {}
    counts: Counter = Counter()
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row

        single_rows, single_meta = single.scan_candidates(DATA)
        for row in single_rows:
            if not single.validate_source_pins(connection, row):
                counts["singleRejectedSourceProvenance"] += 1
                continue
            merge(
                pool,
                line=row["coefficientLine"],
                digest=row["coefficientSha256"],
                pair=(str(row["targetLabel"]), int(row["targetR"])),
                polynomial_disc=row.get("polynomialDiscriminantAbs"),
                field_discs=[row.get("fieldDiscriminantAbs")],
                family=str(row["proof"]["schema"]),
                proofs=[row["proof"]],
            )
            counts["singleAcceptedOccurrences"] += 1

        stable_pool, stable_meta = shared.scan_stable_multi(
            DATA, DATA / "pair_signature_map.jsonl"
        )
        for digest, row in stable_pool.items():
            if not all(
                shared.source_evidence(connection, tuple(key)) is not None
                for key in row["sourceKeys"]
            ):
                counts["stableRejectedSourceProvenance"] += 1
                continue
            merge(
                pool,
                line=row["coefficientLine"],
                digest=digest,
                pair=tuple(row["pair"]),
                polynomial_disc=row["polynomialDiscriminantAbs"],
                field_discs=[row.get("fieldDiscriminantAbs")],
                family="stable_multi",
                proofs=row["proofs"],
            )
            counts["stableAcceptedUniqueHashes"] += 1

        certificates, staged_pairs = frobenius.scan_json_artifacts(DATA)
        exact_pool, certificate_audit, frobenius_meta = (
            frobenius.collect_exact_pool(
                certificates, connection, ROOT, expected_certificates=None
            )
        )
        for digest, row in exact_pool.items():
            merge(
                pool,
                line=row["coefficientLine"],
                digest=digest,
                pair=tuple(row["pair"]),
                polynomial_disc=row["polynomialDiscriminantAbs"],
                field_discs=row["fieldDiscriminants"],
                family="exact_frobenius",
                proofs=row["proofs"],
            )
            counts["frobeniusAcceptedUniqueHashes"] += 1

        for digest, row in pool.items():
            if (
                len(row["coefficientLines"]) != 1
                or len(row["pairs"]) != 1
                or len(row["polynomialDiscriminants"]) > 1
                or len(row["fieldDiscriminants"]) > 1
            ):
                raise ValueError(f"ambiguous/conflicting exact evidence: {digest}")

        receipt_hashes, receipt_pairs, receipt_audit = (
            frobenius.receipt_exclusions(
                connection,
                RECEIPTS,
                staged_pairs,
                exact_pool,
                ROOT,
            )
        )
        other_outbox_hashes, outbox_audit = outbox_hashes_excluding(MANIFEST)
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        baseline_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
            )
        }
        targets = {
            (str(row["label"]), int(row["r"])): dict(row)
            for row in connection.execute("SELECT * FROM targets")
        }

        skips: Counter = Counter()
        selected = []
        for digest in sorted(pool):
            row = pool[digest]
            pair = next(iter(row["pairs"]))
            if pair in baseline_pairs:
                skips["baselinePair"] += 1
                continue
            if pair in owned_pairs:
                skips["locallyOwnedScoreablePair"] += 1
                continue
            if digest in ledger_hashes:
                skips["knownLedgerHash"] += 1
                continue
            if digest in receipt_hashes:
                skips["receiptHash"] += 1
                continue
            if pair in receipt_pairs:
                skips["receiptPair"] += 1
                continue
            if digest in other_outbox_hashes:
                skips["otherOutboxHash"] += 1
                continue
            target = targets.get(pair)
            if target is None:
                skips["missingTarget"] += 1
                continue
            minimum = target.get("minimum_disc_abs")
            if minimum is None:
                skips["missingTargetMinimum"] += 1
                continue
            if row["fieldDiscriminants"]:
                basis = "fieldDiscriminantAbs"
                candidate_disc = next(iter(row["fieldDiscriminants"]))
            elif row["polynomialDiscriminants"]:
                basis = "polynomialDiscriminantAbs"
                candidate_disc = next(iter(row["polynomialDiscriminants"]))
            else:
                skips["missingCertifiedDiscriminant"] += 1
                continue
            minimum = int(minimum)
            if candidate_disc > minimum:
                skips["notCompetitive"] += 1
                continue
            score = Fraction(1, 2 ** int(target["team_count"]))
            proof_rows = []
            for proof in row["proofs"]:
                item = dict(proof)
                artifact = item.get("artifact")
                if artifact and "artifactSha256" not in item:
                    artifact_path = (ROOT / str(artifact)).resolve()
                    item["artifactSha256"] = digest_path(artifact_path)
                proof_rows.append(item)
            selected.append(
                {
                    "coefficientLine": next(iter(row["coefficientLines"])),
                    "coefficientSha256": digest,
                    "coefficientBytes": len(
                        next(iter(row["coefficientLines"])).encode("ascii")
                    ),
                    "pair": pair,
                    "families": sorted(row["families"]),
                    "fieldDiscriminantAbs": (
                        next(iter(row["fieldDiscriminants"]))
                        if row["fieldDiscriminants"]
                        else None
                    ),
                    "polynomialDiscriminantAbs": (
                        next(iter(row["polynomialDiscriminants"]))
                        if row["polynomialDiscriminants"]
                        else None
                    ),
                    "comparisonBasis": basis,
                    "candidateDiscAbs": candidate_disc,
                    "currentMinimumDiscAbs": minimum,
                    "improvementMultipleExact": f"{minimum}/{candidate_disc}",
                    "projectedMarginalScoreExact": (
                        f"{score.numerator}/{score.denominator}"
                    ),
                    "target": {
                        "teamCount": int(target["team_count"]),
                        "discovered": bool(target["discovered"]),
                        "generatedAt": str(target["generated_at"]),
                    },
                    "proofs": proof_rows,
                }
            )

        selected.sort(
            key=lambda row: (
                row["target"]["teamCount"],
                Fraction(row["candidateDiscAbs"], row["currentMinimumDiscAbs"]),
                row["pair"],
                row["coefficientSha256"],
            )
        )
        score = sum(
            (
                Fraction(
                    int(value.split("/")[0]), int(value.split("/")[1])
                )
                for value in (
                    row["projectedMarginalScoreExact"] for row in selected
                )
            ),
            Fraction(),
        )
        manifest_payload = "".join(
            row["coefficientLine"] + "\n" for row in selected
        ).encode("ascii")
        if not selected:
            raise ValueError("complete exact corpus has no competitive candidates")

        public_selected = []
        for row in selected:
            public_selected.append(
                {
                    key: (
                        f"{value[0]}/r{value[1]}" if key == "pair" else
                        str(value) if key.endswith("DiscAbs") and value is not None else
                        value
                    )
                    for key, value in row.items()
                    if key != "coefficientLine"
                }
            )

        certificate = {
            "method": METHOD,
            "checks": {
                "allCoefficientPayloadsCanonicalAndHashMatched": True,
                "allExactAssignmentsGloballyUnambiguous": True,
                "allSourcePinsRejoinedToAcceptedScoreableLedgerRows": True,
                "baselineOwnedKnownReceiptAndOtherOutboxExclusionsPassed": True,
                "candidateDiscriminantAtMostCurrentMinimum": True,
                "coefficientPayloadPresentOnlyInManifest": True,
            },
            "networkCalls": 0,
            "submissionCalls": 0,
            "database": display(DATABASE),
            "census": {
                **dict(sorted(counts.items())),
                "mergedUniqueExactHashes": len(pool),
                # The scanner's full file-index digest would include this
                # certificate on a second run.  Retain the proof census, not a
                # self-referential inventory of unrelated JSON artifacts.
                "explicitSingle": {
                    "acceptedOccurrences": single_meta["acceptedOccurrences"],
                    "acceptedSchemaCounts": single_meta["acceptedSchemaCounts"],
                },
                "stableMulti": stable_meta,
                "exactFrobenius": frobenius_meta,
                "exactFrobeniusCertificates": len(certificate_audit),
                "sequentialSkipCounts": dict(sorted(skips.items())),
            },
            "exclusions": {
                "ledgerPolynomialHashes": len(ledger_hashes),
                "baselinePairs": len(baseline_pairs),
                "locallyOwnedScoreablePairs": len(owned_pairs),
                "receiptHashes": len(receipt_hashes),
                "receiptPairs": len(receipt_pairs),
                "receiptAuditSha256": digest_bytes(json_payload(receipt_audit)),
                "otherOutbox": outbox_audit,
            },
            "manifest": {
                "path": display(MANIFEST),
                "sha256": digest_bytes(manifest_payload),
                "bytes": len(manifest_payload),
                "polynomials": len(selected),
            },
            "selected": public_selected,
            "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
            "script": {
                "path": display(Path(__file__)),
                "sha256": digest_path(Path(__file__)),
            },
        }
        certificate_payload = json_payload(certificate)
        summary = {
            "method": METHOD,
            "certificate": display(CERTIFICATE),
            "certificateSha256": digest_bytes(certificate_payload),
            "manifest": display(MANIFEST),
            "manifestSha256": digest_bytes(manifest_payload),
            "polynomials": len(selected),
            "selectedPairs": [
                {
                    "pair": f"{row['pair'][0]}/r{row['pair'][1]}",
                    "coefficientSha256": row["coefficientSha256"],
                    "teamCount": row["target"]["teamCount"],
                    "projectedMarginalScoreExact": row[
                        "projectedMarginalScoreExact"
                    ],
                }
                for row in selected
            ],
            "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        summary_payload = json_payload(summary)

    statuses = {
        display(MANIFEST): write_new_or_identical(MANIFEST, manifest_payload),
        display(CERTIFICATE): write_new_or_identical(CERTIFICATE, certificate_payload),
        display(SUMMARY): write_new_or_identical(SUMMARY, summary_payload),
    }
    return {
        **summary,
        "outputStatus": statuses,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))

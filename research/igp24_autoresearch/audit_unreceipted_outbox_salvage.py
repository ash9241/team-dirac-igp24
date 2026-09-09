#!/usr/bin/env python3
"""Light-only salvage audit for coefficient rows stranded in text outboxes.

The audit inventories every nonempty ``outbox/*.txt`` manifest and canonical
coefficient hash.  It subtracts coefficient hashes already present in the
ledger or recoverable from any committed receipt/consolidated receipt mapping,
then rejoins the remainder to the validated exact local proof corpus.

Only current team-count 0 or 1 pairs may be selected, and baseline, locally
owned, known-verification, and receipt-reserved pairs are excluded.  The
program performs no network, Sage, GAP, PARI, or submission operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as pair_routes
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DB = DATA / "ledger.sqlite3"

INVENTORY = DATA / "unreceipted_outbox_salvage_inventory_20260730.json"
CERTIFICATE = DATA / "unreceipted_outbox_salvage_certificate_20260730.json"
MANIFEST = OUTBOX / "unreceipted_outbox_salvage_tc01_20260730.txt"

PRIORITIZED_NAMES = {
    "forced_gold_24T9394_r16_24T11461_r24.txt",
    "frobenius_frob14_live_gold.txt",
    "pair_sum_gold_routes2_gold.txt",
    "pair_sum_single_expanded_gold.txt",
    "pair_sum_multi_gold.txt",
    "f6_post22_14293_r16_gold.txt",
    "pending_exact_live_combined_23.txt",
    "agent_rank10_raid_character_signature_solos.txt",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_digest(value: object) -> str:
    return sha256_bytes(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    )


def canonical_rows(path: Path) -> list[dict]:
    result = []
    seen = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            coefficients = [int(item.strip()) for item in value.split(",")]
        except ValueError as exc:
            raise ValueError(
                f"nonintegral coefficient at {path}:{line_number}"
            ) from exc
        if not coefficients or coefficients[0] == 0 or coefficients[-1] != 1:
            raise ValueError(f"nonmonic/zero-constant row at {path}:{line_number}")
        canonical = ",".join(str(item) for item in coefficients)
        digest = sha256_bytes(canonical.encode("ascii"))
        if digest in seen:
            raise ValueError(f"duplicate coefficient row in {path}: {digest}")
        seen.add(digest)
        result.append(
            {
                "lineNumber": line_number,
                "coefficientSha256": digest,
                "_coefficientLine": canonical,
            }
        )
    return result


def outbox_snapshot() -> tuple[list[dict], dict[str, dict]]:
    manifests = []
    distinct: dict[str, dict] = {}
    for path in sorted(OUTBOX.glob("*.txt")):
        if not path.is_file() or path.stat().st_size == 0:
            continue
        rows = canonical_rows(path)
        if not rows:
            continue
        relative = str(path.relative_to(ROOT))
        manifest = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_path(path),
            "rows": len(rows),
            "coefficientHashes": [
                row["coefficientSha256"] for row in rows
            ],
        }
        manifests.append(manifest)
        for row in rows:
            digest = row["coefficientSha256"]
            entry = distinct.setdefault(
                digest,
                {
                    "coefficientSha256": digest,
                    "_coefficientLine": row["_coefficientLine"],
                    "locations": [],
                },
            )
            if entry["_coefficientLine"] != row["_coefficientLine"]:
                raise ValueError(f"hash collision for {digest}")
            entry["locations"].append(
                {"path": relative, "lineNumber": row["lineNumber"]}
            )
    return manifests, distinct


def filesystem_fingerprint() -> str:
    rows = []
    for root, pattern in ((OUTBOX, "*.txt"), (RECEIPTS, "sub_*.json")):
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                rows.append(
                    [
                        str(path.relative_to(ROOT)),
                        path.stat().st_size,
                        sha256_path(path),
                    ]
                )
    return canonical_digest(rows)


def batches(values: list[str], size: int = 300):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def ledger_hash_membership(
    connection: sqlite3.Connection, hashes: set[str]
) -> set[str]:
    result = set()
    for batch in batches(sorted(hashes)):
        placeholders = ",".join("?" for _ in batch)
        result.update(
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials "
                f"WHERE coefficient_hash IN ({placeholders})",
                batch,
            )
        )
    return result


def receipt_artifact_audit(
    manifests: list[dict],
) -> tuple[set[str], list[dict]]:
    """Recover coefficient hashes from every intact committed receipt.

    A receipt is matched both through its recorded path and through an exact
    whole-manifest SHA-256 match to any current outbox copy.  Candidate
    exclusion later uses the union of this set and the repository's sealed
    consolidated receipt mappings.
    """

    by_raw_sha = {
        str(row["sha256"]): ROOT / str(row["path"]) for row in manifests
    }
    hashes = set()
    audit = []
    for receipt_path in sorted(RECEIPTS.glob("sub_*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        recorded = str(receipt.get("manifestHash") or "")
        manifest_value = receipt.get("manifest")
        source = None
        recovery = None
        if manifest_value:
            candidate = Path(str(manifest_value)).expanduser().resolve()
            if (
                candidate.is_file()
                and sha256_path(candidate) == recorded
            ):
                source = candidate
                recovery = "intact_recorded_path"
        if source is None and recorded in by_raw_sha:
            source = by_raw_sha[recorded]
            recovery = "whole_manifest_sha256_copy"
        entry = {
            "path": str(receipt_path.relative_to(ROOT)),
            "sha256": sha256_path(receipt_path),
            "recordedManifestSha256": recorded or None,
            "recordedManifest": str(manifest_value) if manifest_value else None,
            "recoverable": source is not None,
            "recovery": recovery,
        }
        if source is not None:
            recovered = {
                row["coefficientSha256"] for row in canonical_rows(source)
            }
            hashes.update(recovered)
            entry.update(
                {
                    "recoveredManifest": str(source.relative_to(ROOT)),
                    "coefficientHashes": len(recovered),
                }
            )
        audit.append(entry)
    return hashes, audit


def target_state(
    connection: sqlite3.Connection, pair: tuple[str, int]
) -> dict | None:
    row = connection.execute(
        """
        SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        pair,
    ).fetchone()
    if row is None:
        return None
    return {
        "label": str(row["label"]),
        "t": int(row["t"]),
        "r": int(row["r"]),
        "teamCount": int(row["team_count"]),
        "minimumDiscAbs": (
            str(row["minimum_disc_abs"])
            if row["minimum_disc_abs"] is not None
            else None
        ),
        "discovered": bool(row["discovered"]),
        "generatedAt": str(row["generated_at"]),
    }


def fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    before = filesystem_fingerprint()
    manifests, distinct = outbox_snapshot()
    direct_receipt_hashes, receipt_artifacts = receipt_artifact_audit(manifests)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    try:
        all_hashes = set(distinct)
        ledger_hashes = ledger_hash_membership(connection, all_hashes)
        exact_pool, _single_rows, exact_meta, pair_index = raid.exact_corpus(
            connection
        )
        (
            sealed_receipt_hashes,
            sealed_receipt_pairs,
            sealed_receipt_meta,
        ) = exact_single.receipt_exclusions(
            lane.RECEIPTS, DATA, connection, pair_index
        )
        receipt_hashes = direct_receipt_hashes | sealed_receipt_hashes
        snapshot = pair_routes.load_ledger_snapshot(connection)

        candidates = []
        exclusion_counts = Counter()
        for digest in sorted(all_hashes - ledger_hashes - receipt_hashes):
            exact = exact_pool.get(digest)
            entry = distinct[digest]
            if exact is None:
                entry["salvageStatus"] = "excluded_missing_validated_exact_proof"
                exclusion_counts["missingValidatedExactProof"] += 1
                continue
            pair = (str(exact["pair"][0]), int(exact["pair"][1]))
            state = target_state(connection, pair)
            reasons = []
            if state is None:
                reasons.append("missingCurrentTarget")
            else:
                if state["teamCount"] not in (0, 1):
                    reasons.append("currentTeamCountNotZeroOrOne")
            if pair in snapshot["baseline"]:
                reasons.append("baselinePair")
            if pair in snapshot["owned"]:
                reasons.append("locallyOwnedPair")
            if pair in snapshot["knownPairs"]:
                reasons.append("knownVerificationPair")
            if pair in sealed_receipt_pairs:
                reasons.append("receiptReservedPair")
            entry.update(
                {
                    "exactPair": [pair[0], pair[1]],
                    "target": state,
                    "exactFamilies": sorted(str(x) for x in exact["families"]),
                    "proofArtifacts": raid.proof_artifacts(exact["proofs"]),
                }
            )
            if reasons:
                entry["salvageStatus"] = "excluded"
                entry["exclusionReasons"] = reasons
                exclusion_counts.update(reasons)
                continue
            entry["salvageStatus"] = "eligible_exact_current_tc01"
            candidates.append(
                {
                    "coefficientSha256": digest,
                    "coefficientLine": entry["_coefficientLine"],
                    "pair": pair,
                    "target": state,
                    "fieldDiscriminantAbs": exact.get(
                        "fieldDiscriminantAbs"
                    ),
                    "coefficientBytes": int(exact["coefficientBytes"]),
                    "families": sorted(str(x) for x in exact["families"]),
                    "proofArtifacts": raid.proof_artifacts(exact["proofs"]),
                    "locations": entry["locations"],
                }
            )

        by_pair = defaultdict(list)
        for candidate in candidates:
            by_pair[candidate["pair"]].append(candidate)
        selected = []
        for pair, rows in by_pair.items():
            def selection_key(row: dict):
                field = row["fieldDiscriminantAbs"]
                return (
                    field is None,
                    int(field) if field is not None else 0,
                    int(row["coefficientBytes"]),
                    str(row["coefficientSha256"]),
                )

            chosen = min(rows, key=selection_key)
            chosen["eligibleAlternativesForPair"] = len(rows)
            selected.append(chosen)
        selected.sort(
            key=lambda row: (
                int(row["target"]["teamCount"]),
                int(row["pair"][0][3:]),
                int(row["pair"][1]),
                str(row["coefficientSha256"]),
            )
        )
    finally:
        connection.rollback()
        connection.close()

    after = filesystem_fingerprint()
    if before != after:
        raise RuntimeError(
            "outbox/receipt filesystem changed during audit; rerun to seal "
            "one coherent boundary"
        )

    selected_hashes = {row["coefficientSha256"] for row in selected}
    selected_pairs = {row["pair"] for row in selected}
    if (
        len(selected_hashes) != len(selected)
        or len(selected_pairs) != len(selected)
    ):
        raise ValueError("selected salvage rows are not hash/pair unique")

    manifest_payload = "".join(
        f"{row['coefficientLine']}\n" for row in selected
    )
    manifest_sha = (
        sha256_bytes(manifest_payload.encode("ascii")) if selected else None
    )
    if selected:
        write_atomic(MANIFEST, manifest_payload)
    elif MANIFEST.exists():
        raise RuntimeError(
            f"zero-row result refuses to alter existing manifest: {MANIFEST}"
        )

    for entry in distinct.values():
        entry.pop("_coefficientLine", None)
    per_manifest = []
    for manifest in manifests:
        row_states = Counter(
            (
                "ledger"
                if digest in ledger_hashes
                else "receipt"
                if digest in receipt_hashes
                else distinct[digest].get("salvageStatus", "unclassified")
            )
            for digest in manifest["coefficientHashes"]
        )
        per_manifest.append(
            {
                **manifest,
                "prioritizedByRequest": (
                    Path(str(manifest["path"])).name in PRIORITIZED_NAMES
                    or Path(str(manifest["path"])).match(
                        "agent_rank10_raid_character_*_gold.txt"
                    )
                ),
                "rowStateCounts": dict(sorted(row_states.items())),
            }
        )

    score = sum(
        Fraction(1, 2 ** int(row["target"]["teamCount"]))
        for row in selected
    )
    inventory = {
        "schemaVersion": "unreceipted-outbox-salvage-inventory-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "complete_all_nonempty_txt_outboxes",
        "manifests": per_manifest,
        "distinctHashes": [
            distinct[digest] for digest in sorted(distinct)
        ],
        "counts": {
            "nonemptyTxtManifests": len(manifests),
            "canonicalRows": sum(row["rows"] for row in manifests),
            "distinctCoefficientHashes": len(distinct),
            "hashesInLedger": len(ledger_hashes & set(distinct)),
            "hashesInCommittedReceipts": len(receipt_hashes & set(distinct)),
            "hashesAbsentFromLedgerAndReceipts": len(
                set(distinct) - ledger_hashes - receipt_hashes
            ),
            "eligibleExactCurrentTc01Occurrences": len(candidates),
            "selectedDistinctPairs": len(selected),
        },
        "hashSetSha256": canonical_digest(sorted(distinct)),
        "filesystemBoundarySha256": before,
    }
    inventory_payload = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    write_atomic(INVENTORY, inventory_payload)

    selected_public = []
    for row in selected:
        selected_public.append(
            {
                key: value
                for key, value in row.items()
                if key != "coefficientLine"
            }
        )
    certificate = {
        "schemaVersion": "unreceipted-outbox-salvage-certificate-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "certified_exact_tc01_manifest_staged_not_submitted"
            if selected
            else "certified_no_eligible_exact_tc01_rows"
        ),
        "scope": (
            "all nonempty outbox/*.txt coefficient hashes absent from the "
            "ledger and all committed receipt/consolidated receipt boundaries"
        ),
        "inventory": {
            "path": str(INVENTORY.relative_to(ROOT)),
            "sha256": sha256_bytes(inventory_payload.encode("utf-8")),
            **inventory["counts"],
        },
        "receiptBoundary": {
            "receiptArtifacts": len(receipt_artifacts),
            "recoverableReceiptArtifacts": sum(
                bool(row["recoverable"]) for row in receipt_artifacts
            ),
            "directRecoveredCoefficientHashes": len(direct_receipt_hashes),
            "sealedReceiptAndConsolidatedHashes": len(
                sealed_receipt_hashes
            ),
            "unionReceiptHashes": len(receipt_hashes),
            "sealedReceiptPairs": len(sealed_receipt_pairs),
            "sealedReceiptMeta": sealed_receipt_meta,
            "receiptArtifactsAudit": receipt_artifacts,
        },
        "exactCorpus": exact_meta,
        "selectionGate": {
            "requiredCurrentTeamCounts": [0, 1],
            "excludedPairs": [
                "baseline",
                "locally owned accepted scoreable",
                "known verification",
                "committed receipt reserved",
            ],
            "ambiguousOrMissingProofPolicy": "fail_closed",
            "exclusionCounts": dict(sorted(exclusion_counts.items())),
            "candidateOccurrences": len(candidates),
            "selectedDistinctHashes": len(selected_hashes),
            "selectedDistinctPairs": len(selected_pairs),
            "teamCountDistribution": dict(
                sorted(
                    Counter(
                        int(row["target"]["teamCount"]) for row in selected
                    ).items()
                )
            ),
        },
        "selected": selected_public,
        "projectedBaseScoreExactBeforeDiscriminantPenalty": fraction_text(score),
        "manifest": (
            {
                "path": str(MANIFEST.relative_to(ROOT)),
                "rows": len(selected),
                "bytes": len(manifest_payload.encode("ascii")),
                "sha256": manifest_sha,
                "dryRunCommand": (
                    "python3 sair_api.py submit --file "
                    f"{MANIFEST.relative_to(ROOT)}"
                ),
            }
            if selected
            else {
                "path": None,
                "rows": 0,
                "bytes": 0,
                "sha256": None,
                "dryRunCommand": None,
                "reason": (
                    "an empty submission manifest is invalid; no eligible "
                    "tc0/tc1 row survived the exact fail-closed gates"
                ),
            }
        ),
        "checks": {
            "everyNonemptyTxtOutboxInventoried": True,
            "everyCoefficientRowCanonicalAndHashInventoried": True,
            "allSelectedHashesAbsentFromLedger": not bool(
                selected_hashes & ledger_hashes
            ),
            "allSelectedHashesAbsentFromReceipts": not bool(
                selected_hashes & receipt_hashes
            ),
            "allSelectedPairsDistinct": len(selected_pairs) == len(selected),
            "allSelectedPairsCurrentTcZeroOrOne": all(
                row["target"]["teamCount"] in (0, 1) for row in selected
            ),
            "allSelectedProofsExactAndSourceValidated": True,
            "filesystemBoundaryStableDuringAudit": before == after,
        },
        "sideEffects": {
            "inventoryWrites": 1,
            "certificateWrites": 1,
            "manifestWrites": int(bool(selected)),
            "ledgerWrites": 0,
            "receiptWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "sageCalls": 0,
            "gapCalls": 0,
            "pariCalls": 0,
        },
    }
    certificate_payload = (
        json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    )
    write_atomic(CERTIFICATE, certificate_payload)
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "inventory": str(INVENTORY.relative_to(ROOT)),
                "inventorySha256": sha256_path(INVENTORY),
                "certificate": str(CERTIFICATE.relative_to(ROOT)),
                "certificateSha256": sha256_path(CERTIFICATE),
                "manifest": certificate["manifest"],
                "projectedBaseScoreExact": fraction_text(score),
                "counts": inventory["counts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

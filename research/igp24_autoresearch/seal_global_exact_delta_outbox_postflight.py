#!/usr/bin/env python3
"""Seal the stricter outbox-aware postflight for the global exact delta.

This is an offline, light-only verifier around the receipt-aware exact/shared
census.  It excludes the newly staged manifest itself, inventories every
other outbox polynomial, maps all locally certified exact/possible pairs, and
proves that the selected exact hashes and pairs are absent from the ledger,
receipts, baseline, ownership, and the pre-existing outbox coverage.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import audit_tc2_nonpair_closure as common
import stage_all_exact_frobenius_unowned as all_exact
import stage_exact_shared_census as shared
import stage_legacy_exact_shortlist as legacy
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DB = DATA / "ledger.sqlite3"

MANIFEST = OUTBOX / "global_exact_corpus_delta_after_b4f7_20260722.txt"
BASE_CERTIFICATE = DATA / "global_exact_corpus_delta_after_b4f7_20260722_certificate.json"
POSTFLIGHT = DATA / "global_exact_corpus_delta_after_b4f7_20260722_outbox_postflight.json"
POSTFLIGHT_SUMMARY = DATA / "global_exact_corpus_delta_after_b4f7_20260722_outbox_postflight_summary.json"

PINNED_RECEIPTS = (
    RECEIPTS / "sub_a1115b5e820c47c59e3be632d1b673b5.json",
    RECEIPTS / "sub_b4f7f2bd853446aca3382d22345a73ec.json",
)


def sha256_path(path: Path) -> str:
    return common.sha256_path(path)


def artifact(path: Path) -> dict:
    return common.artifact(path)


def pair_from_object(value: dict) -> tuple[str, int] | None:
    if isinstance(value.get("targetLabel"), str) and value.get("targetR") is not None:
        try:
            return str(value["targetLabel"]), int(value["targetR"])
        except (TypeError, ValueError):
            return None
    raw = value.get("pair")
    if isinstance(raw, str) and "/r" in raw:
        label, raw_r = raw.rsplit("/r", 1)
        try:
            return label, int(raw_r)
        except ValueError:
            return None
    return None


def main() -> int:
    for path in (MANIFEST, BASE_CERTIFICATE, *PINNED_RECEIPTS):
        if not path.is_file():
            raise ValueError(f"missing pinned input: {path}")

    base = json.loads(BASE_CERTIFICATE.read_text(encoding="utf-8"))
    selected = base.get("selected") or []
    if (
        (base.get("selectedCounts") or {}).get("exact") != 3
        or (base.get("selectedCounts") or {}).get("allCompatibleShared") != 0
        or len(selected) != 3
        or any(row.get("type") != "exact" for row in selected)
    ):
        raise ValueError("base census is not the expected three-row exact-only seal")

    lines = []
    manifest_hashes = []
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = single.canonical_polynomial_line(raw)
        if line is None:
            raise ValueError("invalid line in global exact delta manifest")
        lines.append(line)
        manifest_hashes.append(hashlib.sha256(line.encode("ascii")).hexdigest())
    selected_hashes = [str(row["coefficientSha256"]) for row in selected]
    selected_pairs = {
        common.parse_pair(str(row["pair"])) for row in selected
    }
    if (
        len(lines) != 3
        or manifest_hashes != selected_hashes
        or len(set(selected_hashes)) != 3
        or len(selected_pairs) != 3
        or sha256_path(MANIFEST) != str((base.get("manifest") or {})["sha256"])
    ):
        raise ValueError("manifest/base-certificate identity mismatch")

    outbox_hash_paths: dict[str, set[str]] = defaultdict(set)
    outbox_file_index = []
    for path in sorted(OUTBOX.glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        outbox_file_index.append((str(path.relative_to(ROOT)), sha256_path(path)))
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = single.canonical_polynomial_line(raw)
            if line is None:
                if raw.split("#", 1)[0].strip():
                    raise ValueError(f"invalid outbox polynomial: {path}")
                continue
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            outbox_hash_paths[digest].add(str(path.relative_to(ROOT)))
    outbox_hashes = set(outbox_hash_paths)
    if set(selected_hashes) & outbox_hashes:
        raise ValueError("selected hash is already covered by another outbox")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        known_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
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
        targets = {
            (str(row["label"]), int(row["r"])): dict(row)
            for row in connection.execute("SELECT * FROM targets")
        }

        single_pool, single_audit = single.scan_candidates(DATA)
        stable_pool, stable_audit = shared.scan_stable_multi(
            DATA, DATA / "pair_signature_map.jsonl"
        )
        certificates, _staged = all_exact.scan_json_artifacts(DATA)
        unresolved_pool, unresolved_audit = shared.unresolved_options(
            certificates, ROOT
        )
        frobenius_pool, certificate_audit, frobenius_audit = (
            all_exact.collect_exact_pool(certificates, connection, ROOT, None)
        )

        pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for row in single_pool:
            pair_index[str(row["coefficientSha256"])].add(
                (str(row["targetLabel"]), int(row["targetR"]))
            )
        for digest, row in stable_pool.items():
            pair_index[str(digest)].add(row["pair"])
        for digest, row in frobenius_pool.items():
            pair_index[str(digest)].add(row["pair"])
        for digest, row in unresolved_pool.items():
            pair_index[str(digest)].update(row["possiblePairs"])
        for digest, pairs in legacy.collect_global_exact_claims(outbox_hashes).items():
            pair_index[str(digest)].update(pairs)

        # Directly indexed deterministic stage/result metadata fills the
        # non-corpus outbox routes without interpreting any coefficient body.
        def walk(value) -> None:
            if isinstance(value, dict):
                digests = {
                    str(value[key])
                    for key in ("coefficientSha256", "candidateSha256", "coefficientHash")
                    if isinstance(value.get(key), str)
                    and str(value[key]) in outbox_hashes
                }
                pair = pair_from_object(value)
                if pair is not None:
                    for digest in digests:
                        pair_index[digest].add(pair)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, (dict, list)):
                        walk(child)

        for path in sorted(DATA.rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
                continue
            try:
                if path.suffix == ".json":
                    walk(json.loads(path.read_text(encoding="utf-8")))
                else:
                    for raw in path.read_text(encoding="utf-8").splitlines():
                        try:
                            walk(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue

        receipt_pair_index = defaultdict(set)
        for digest, pair_set in pair_index.items():
            receipt_pair_index[digest].update(pair_set)
        for row in selected:
            receipt_pair_index[str(row["coefficientSha256"])].add(
                common.parse_pair(str(row["pair"]))
            )
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, receipt_pair_index
        )

        # The coordinator may commit this freshly sealed manifest before this
        # postflight finishes.  Treat only a receipt whose intact manifest hash
        # is exactly this manifest as the self receipt; every other receipt and
        # ledger row remains an exclusion.
        self_receipt_paths = []
        self_submission_ids = set()
        manifest_sha256 = sha256_path(MANIFEST)
        for path in sorted(RECEIPTS.glob("sub_*.json")):
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if str(receipt.get("manifestHash")) != manifest_sha256:
                continue
            self_receipt_paths.append(path)
            response = receipt.get("response") or {}
            self_submission_ids.add(
                str(response.get("submissionId") or receipt.get("submissionId") or path.stem)
            )
        if len(self_receipt_paths) > 1:
            raise ValueError("global exact manifest has multiple receipts")

        external_receipt_hashes = set(receipt_hashes) - set(selected_hashes)
        external_receipt_pairs = set(receipt_pairs) - selected_pairs
        for path in sorted(RECEIPTS.glob("sub_*.json")):
            if path in self_receipt_paths:
                continue
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest_value = receipt.get("manifest")
            manifest_hash = receipt.get("manifestHash")
            if not manifest_value or not manifest_hash:
                continue
            receipt_manifest = Path(str(manifest_value)).expanduser().resolve()
            if not receipt_manifest.is_file() or sha256_path(receipt_manifest) != str(manifest_hash):
                continue
            other_hashes = set()
            for raw in receipt_manifest.read_text(encoding="utf-8").splitlines():
                line = single.canonical_polynomial_line(raw)
                if line is not None:
                    other_hashes.add(hashlib.sha256(line.encode("ascii")).hexdigest())
            if other_hashes & set(selected_hashes):
                raise ValueError("selected hash occurs in a nonself receipt")

        external_hash_rows = []
        external_pair_rows = []
        for digest in selected_hashes:
            external_hash_rows.extend(
                row
                for row in connection.execute(
                    "SELECT submission_id,polynomial_index FROM polynomials "
                    "WHERE coefficient_hash=?",
                    (digest,),
                )
                if str(row["submission_id"]) not in self_submission_ids
            )
        for pair in selected_pairs:
            external_pair_rows.extend(
                row
                for row in connection.execute(
                    "SELECT submission_id,polynomial_index FROM verifications "
                    "WHERE scoreable=1 AND label=? AND r=?",
                    pair,
                )
                if str(row["submission_id"]) not in self_submission_ids
            )

        outbox_pairs = {
            pair
            for digest in outbox_hashes
            for pair in pair_index.get(digest, set())
        }
        if external_hash_rows or set(selected_hashes) & external_receipt_hashes:
            raise ValueError("selected hash is externally ledger/receipt-covered")
        if (
            external_pair_rows
            or selected_pairs
            & (baseline | external_receipt_pairs | outbox_pairs)
        ):
            raise ValueError("selected pair is excluded or already covered")
        if any(pair not in targets for pair in selected_pairs):
            raise ValueError("selected pair is absent from target snapshot")

        pinned_receipts = []
        for path in PINNED_RECEIPTS:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            response = receipt.get("response") or {}
            if (
                receipt.get("commit") is not True
                or int(receipt.get("knownLocalHashes", -1)) != 0
                or int(response.get("rejectedCount", -1)) != 0
                or list(response.get("failedPolynomials") or [])
            ):
                raise ValueError(f"pinned receipt is not a clean commit: {path}")
            pinned_receipts.append(artifact(path))

        file_index_payload = "".join(
            f"{path}\0{digest}\n" for path, digest in outbox_file_index
        ).encode("utf-8")
        certificate = {
            "schemaVersion": "global-exact-corpus-delta-outbox-postflight-v1",
            "status": (
                "sealed_three_exact_rows_precommit_novel_now_self_receipted"
                if self_receipt_paths
                else "sealed_three_exact_rows_receipt_and_outbox_novel"
            ),
            "coefficientMaterialIncluded": False,
            "credentialMaterialIncluded": False,
            "checks": {
                "allRowsFullyExactNotShared": True,
                "allCandidateHashesAbsentFromExternalLedgerReceiptsAndOtherOutboxes": True,
                "allCandidatePairsNonbaselineExternallyUnownedReceiptNovelAndOutboxNovel": True,
                "allTargetsPresentInCurrentLocalSnapshot": True,
                "baseManifestAndCertificateIdentityMatched": True,
                "pinnedBoundaryReceiptsAreCleanCommits": True,
            },
            "inputs": {
                "baseCertificate": artifact(BASE_CERTIFICATE),
                "database": {"path": str(DB.relative_to(ROOT))},
                "manifest": artifact(MANIFEST),
                "pinnedBoundaryReceipts": pinned_receipts,
                "selfReceiptAfterSeal": (
                    artifact(self_receipt_paths[0]) if self_receipt_paths else None
                ),
            },
            "selected": [
                {
                    "coefficientSha256": str(row["coefficientSha256"]),
                    "pair": str(row["pair"]),
                    "projectedMarginalScoreExact": str(
                        row["projectedMarginalScoreExact"]
                    ),
                    "targetTeamCount": int((row.get("target") or {})["teamCount"]),
                }
                for row in selected
            ],
            "selectedCount": 3,
            "projectedMarginalScoreExact": "7/32",
            "outboxExclusionSnapshot": {
                "excludedManifestItself": str(MANIFEST.relative_to(ROOT)),
                "otherManifestFiles": len(outbox_file_index),
                "otherManifestFileIndexSha256": hashlib.sha256(
                    file_index_payload
                ).hexdigest(),
                "otherUniqueCoefficientHashes": len(outbox_hashes),
                "mappedPossiblePairs": len(outbox_pairs),
                "unmappedCoefficientHashes": sum(
                    not pair_index.get(digest) for digest in outbox_hashes
                ),
            },
            "receiptExclusion": {
                key: value
                for key, value in receipt_audit.items()
                if key != "audit"
            },
            "corpus": {
                "single": single_audit,
                "stableMulti": stable_audit,
                "exactFrobenius": frobenius_audit,
                "exactFrobeniusCertificates": certificate_audit,
                "unresolvedFrobenius": unresolved_audit,
            },
            "sideEffects": {
                "heavyWorkersLaunched": 0,
                "ledgerWrites": 0,
                "networkCalls": 0,
                "submissionCalls": 0,
            },
        }
    finally:
        connection.close()

    common.write_new(POSTFLIGHT, certificate)
    summary = {
        "schemaVersion": "global-exact-corpus-delta-outbox-postflight-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "selectedCount": 3,
        "projectedMarginalScoreExact": "7/32",
        "certificate": artifact(POSTFLIGHT),
        "manifest": artifact(MANIFEST),
        "heavyWorkersLaunched": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    common.write_new(POSTFLIGHT_SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

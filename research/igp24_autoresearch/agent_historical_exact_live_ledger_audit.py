#!/usr/bin/env python3
"""Recover forgotten exact live degree-24 candidates from local history.

This is a read-only census of JSON/JSONL candidate artifacts, outbox manifests,
submission receipts, and the local polynomial ledger.  Polynomial payloads are
canonicalized and deduplicated by SHA-256.  Only candidates carrying an exact
pair certificate, matching the frozen live-undiscovered census, absent from
the polynomial ledger, and absent from every submitted receipt survive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DATABASE = DATA / "ledger.sqlite3"
DEFAULT_LIVE = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_RECEIPTS = ROOT / "receipts"
DEFAULT_OUTBOX = ROOT / "outbox"
DEFAULT_AUDIT = DATA / "agent_historical_exact_live_ledger_audit.json"
DEFAULT_HITS = DATA / "agent_historical_exact_live_ledger_hits.jsonl"
DEFAULT_MANIFEST = ROOT / "outbox" / "agent_historical_exact_live_ledger_hits.txt"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
PAIR_TEXT_RE = re.compile(r"(24T[1-9][0-9]*)/r(0|2|4|6|8|10|12|14|16|18|20|22|24)\Z")
FILE_PAIR_RE = re.compile(r"24T([1-9][0-9]*)[_-]r(0|2|4|6|8|10|12|14|16|18|20|22|24)(?:\D|$)")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
LINE_KEYS = ("coefficientLine", "candidateCoefficientLine", "coefficients")
HASH_KEYS = ("coefficientSha256", "candidateSha256")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def canonical_polynomial_line(value) -> str | None:
    if isinstance(value, list):
        if len(value) != 25:
            return None
        try:
            coefficients = [int(entry) for entry in value]
        except (TypeError, ValueError):
            return None
    elif isinstance(value, str):
        if value.count(",") != 24:
            return None
        try:
            coefficients = [int(entry) for entry in value.strip().split(",")]
        except ValueError:
            return None
    else:
        return None
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
    ):
        return None
    return ",".join(str(value) for value in coefficients)


def valid_pair(label, r) -> tuple[str, int] | None:
    label = str(label)
    if LABEL_RE.fullmatch(label) is None:
        return None
    try:
        r = int(r)
    except (TypeError, ValueError):
        return None
    if r < 0 or r > 24 or r % 2:
        return None
    return label, r


def direct_pair(row: dict) -> tuple[str, int] | None:
    for label_key, r_key in (("targetLabel", "targetR"), ("label", "r")):
        if label_key in row and r_key in row:
            pair = valid_pair(row[label_key], row[r_key])
            if pair is not None:
                return pair
    for key in ("exactTarget", "frozenLiveTarget", "liveTarget", "target"):
        target = row.get(key)
        if not isinstance(target, dict):
            continue
        pair = valid_pair(
            target.get("label", target.get("targetLabel")),
            target.get("r", target.get("targetR")),
        )
        if pair is not None:
            return pair
        text_pair = target.get("pair")
        if isinstance(text_pair, str):
            match = PAIR_TEXT_RE.fullmatch(text_pair)
            if match is not None:
                return match.group(1), int(match.group(2))
    return None


def exact_evidence(row: dict, root: dict, source_path: Path) -> str | None:
    status = str(row.get("status", root.get("status", ""))).lower()
    if status.startswith("certified") and "incomplete" not in status:
        return f"status:{status}"
    if status in {"accepted", "verified", "scoreable"}:
        return f"status:{status}"
    orbit = row.get("orbitCertificate", root.get("orbitCertificate"))
    if isinstance(orbit, dict):
        actual = orbit.get("actualDegrees")
        expected = orbit.get("expectedDegrees")
        exponents = orbit.get("exponents")
        if (
            isinstance(actual, list)
            and actual
            and actual == expected
            and isinstance(exponents, list)
            and len(exponents) == len(actual)
            and all(int(value) == 1 for value in exponents)
            and 24 in [int(value) for value in actual]
        ):
            return "exact_orbit_factorization_certificate"
    maximal = row.get("maximalSubgroupCertificate")
    if (
        row.get("irreducible") is True
        and isinstance(maximal, dict)
        and maximal.get("complete") is True
    ):
        return "complete_transitive_maximal_exclusion"
    h_module = row.get("hOrbitSubmoduleCertificate")
    if (
        row.get("irreducible") is True
        and isinstance(h_module, dict)
        and str(h_module.get("status", "")).startswith("certified_")
    ):
        return "certified_h_orbit_submodule"
    if (
        row.get("irreducible") is True
        and isinstance(row.get("exactTarget"), dict)
        and (
            isinstance(root.get("assignmentCertificate"), dict)
            or isinstance(root.get("exactAssignmentCertificate"), dict)
        )
    ):
        return "exact_target_assignment_certificate"
    # Assignment/certificate files often carry the pair-to-hash proof while
    # the coefficient payload lives in a separate result artifact.
    if direct_pair(row) is not None and (
        "certificate" in source_path.name.lower()
        or row.get("exactCertified") is True
        or row.get("assignmentCertified") is True
    ):
        return "exact_hash_pair_certificate"
    return None


def root_context(root: dict, source_path: Path):
    pairs: set[tuple[str, int]] = set()
    labels: set[str] = set()
    match = FILE_PAIR_RE.search(source_path.name)
    if match is not None:
        pairs.add((f"24T{match.group(1)}", int(match.group(2))))

    def walk(value, key: str = "") -> None:
        if isinstance(value, dict):
            if key in {"exactTarget", "frozenLiveTarget", "liveTarget", "target"}:
                pair = direct_pair({key: value})
                if pair is not None:
                    pairs.add(pair)
            label = value.get("targetLabel")
            if LABEL_RE.fullmatch(str(label)) is not None:
                labels.add(str(label))
            orbit_targets = value.get("orbitTargets")
            if isinstance(orbit_targets, list):
                for target in orbit_targets:
                    if not isinstance(target, dict):
                        continue
                    label = str(target.get("targetLabel"))
                    if LABEL_RE.fullmatch(label) is not None:
                        labels.add(label)
            for child_key, child in value.items():
                if isinstance(child, (dict, list)):
                    walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child, key)

    walk(root)
    return pairs, labels


def iter_json_records(path: Path):
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
                if isinstance(value, dict):
                    yield line_number, value
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            yield 1, value
        elif isinstance(value, list):
            for index, row in enumerate(value, start=1):
                if isinstance(row, dict):
                    yield index, row


def scan_artifacts(data_dir: Path):
    occurrences = []
    claims = []
    files = sorted(data_dir.glob("*.json")) + sorted(data_dir.glob("*.jsonl"))
    files = [path for path in files if path.is_file()]

    for source_path in files:
        for record_number, root in iter_json_records(source_path):
            context_pairs, context_labels = root_context(root, source_path)

            def walk(value, inherited_pair=None, pointer: str = "$") -> None:
                if isinstance(value, list):
                    for index, child in enumerate(value):
                        if isinstance(child, (dict, list)):
                            walk(child, inherited_pair, f"{pointer}/{index}")
                    return
                if not isinstance(value, dict):
                    return
                row_pair = direct_pair(value)
                active_pair = row_pair or inherited_pair
                evidence = exact_evidence(value, root, source_path)

                declared_hash = None
                for hash_key in HASH_KEYS:
                    candidate_hash = value.get(hash_key)
                    if isinstance(candidate_hash, str) and SHA_RE.fullmatch(candidate_hash):
                        declared_hash = candidate_hash
                        break
                claim_pair = row_pair
                if claim_pair is None and value.get("targetR") is not None and len(context_labels) == 1:
                    claim_pair = valid_pair(next(iter(context_labels)), value.get("targetR"))
                if declared_hash and claim_pair and evidence:
                    claims.append(
                        {
                            "coefficientSha256": declared_hash,
                            "evidence": evidence,
                            "pair": claim_pair,
                            "source": str(source_path.relative_to(ROOT)),
                            "sourcePointer": pointer,
                        }
                    )

                for line_key in LINE_KEYS:
                    line = canonical_polynomial_line(value.get(line_key))
                    if line is None:
                        continue
                    digest = sha256_bytes(line.encode("ascii"))
                    if declared_hash is not None and declared_hash != digest:
                        raise ValueError(
                            f"coefficient hash mismatch at {source_path}:{record_number}:{pointer}"
                        )
                    pair = row_pair
                    if pair is None and value.get("targetR") is not None and len(context_labels) == 1:
                        pair = valid_pair(next(iter(context_labels)), value.get("targetR"))
                    if pair is None and value.get("realRoots") is not None and len(context_labels) == 1:
                        pair = valid_pair(next(iter(context_labels)), value.get("realRoots"))
                    if pair is None and len(context_pairs) == 1:
                        pair = next(iter(context_pairs))
                    field_disc = value.get(
                        "fieldDiscriminantAbs", value.get("candidateFieldDiscriminantAbs")
                    )
                    polynomial_disc = value.get("polynomialDiscriminantAbs")
                    occurrences.append(
                        {
                            "coefficientLine": line,
                            "coefficientSha256": digest,
                            "evidence": evidence,
                            "fieldDiscriminantAbs": (
                                str(field_disc) if field_disc is not None else None
                            ),
                            "pair": pair,
                            "polynomialDiscriminantAbs": (
                                str(polynomial_disc) if polynomial_disc is not None else None
                            ),
                            "source": str(source_path.relative_to(ROOT)),
                            "sourcePointer": pointer,
                        }
                    )
                for child_key, child in value.items():
                    if isinstance(child, (dict, list)):
                        walk(child, active_pair, f"{pointer}/{child_key}")

            walk(root)
    return files, occurrences, claims


def scan_outbox(outbox_dir: Path):
    occurrences = []
    files = sorted(path for path in outbox_dir.iterdir() if path.is_file())
    for path in files:
        filename_pair = None
        match = FILE_PAIR_RE.search(path.name)
        if match is not None:
            filename_pair = (f"24T{match.group(1)}", int(match.group(2)))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, raw_line in enumerate(lines, start=1):
            line = canonical_polynomial_line(raw_line.strip())
            if line is None:
                continue
            occurrences.append(
                {
                    "coefficientLine": line,
                    "coefficientSha256": sha256_bytes(line.encode("ascii")),
                    "evidence": "historical_exact_outbox_manifest",
                    "fieldDiscriminantAbs": None,
                    "pair": filename_pair,
                    "polynomialDiscriminantAbs": None,
                    "source": str(path.relative_to(ROOT)),
                    "sourcePointer": f"line:{line_number}",
                }
            )
    return files, occurrences


def read_receipts(receipts_dir: Path, connection):
    hashes: set[str] = set()
    pairs: set[tuple[str, int]] = set()
    submission_ids = []
    audit = []
    for receipt_path in sorted(receipts_dir.glob("sub_*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        response = receipt.get("response") or {}
        submission_id = str(response.get("submissionId") or receipt_path.stem)
        submission_ids.append(submission_id)
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials WHERE submission_id=?",
                (submission_id,),
            )
        }
        expected_count = int(receipt.get("polynomials", 0))
        if len(ledger_hashes) < expected_count:
            raise ValueError(
                f"receipt {receipt_path} has {expected_count} polynomials but only "
                f"{len(ledger_hashes)} ledger hashes"
            )
        hashes.update(ledger_hashes)
        ledger_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE submission_id=?",
                (submission_id,),
            )
            if valid_pair(label, r) is not None
        }
        pairs.update(ledger_pairs)

        manifest_value = receipt.get("manifest")
        recorded_manifest_hash = receipt.get("manifestHash")
        current_manifest_hash = None
        manifest_hashes: set[str] = set()
        provenance = "ledger_submission_rows"
        if manifest_value and recorded_manifest_hash:
            manifest = Path(str(manifest_value)).expanduser().resolve()
            if manifest.is_file():
                current_manifest_hash = sha256_path(manifest)
                if current_manifest_hash == str(recorded_manifest_hash):
                    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
                        line = canonical_polynomial_line(raw_line.strip())
                        if line is not None:
                            manifest_hashes.add(sha256_bytes(line.encode("ascii")))
                    if not manifest_hashes <= ledger_hashes:
                        raise ValueError(
                            f"receipt manifest contains hashes absent from ledger: {receipt_path}"
                        )
                    hashes.update(manifest_hashes)
                    provenance = "matching_manifest_and_ledger_rows"
        audit.append(
            {
                "ledgerPolynomialHashes": len(ledger_hashes),
                "ledgerTargetPairs": len(ledger_pairs),
                "manifestPolynomialHashes": len(manifest_hashes),
                "provenance": provenance,
                "receipt": str(receipt_path.relative_to(ROOT)),
                "recordedManifestSha256": recorded_manifest_hash,
                "currentManifestSha256": current_manifest_hash,
                "submissionId": submission_id,
            }
        )
    return hashes, pairs, submission_ids, audit


def query_known_hashes(connection, hashes: set[str]) -> set[str]:
    result: set[str] = set()
    values = sorted(hashes)
    for start in range(0, len(values), 400):
        batch = values[start : start + 400]
        placeholders = ",".join("?" for _ in batch)
        result.update(
            str(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT coefficient_hash FROM polynomials "
                f"WHERE coefficient_hash IN ({placeholders})",
                batch,
            )
        )
    return result


def positive_integer(value: str | None):
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--outbox", type=Path, default=DEFAULT_OUTBOX)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    for output in (args.audit, args.hits, args.manifest):
        if output.exists():
            raise ValueError(f"refusing to overwrite frozen output: {output}")

    live_rows = [
        json.loads(line)
        for line in args.live.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    live_pairs = {
        (str(row["label"]), int(row["r"]))
        for row in live_rows
        if int(row.get("teamCount", -1)) == 0
    }
    artifact_files, artifact_occurrences, claims = scan_artifacts(DATA)
    outbox_files, outbox_occurrences = scan_outbox(args.outbox)
    all_occurrences = artifact_occurrences + outbox_occurrences

    claim_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    claim_evidence: dict[tuple[str, tuple[str, int]], set[str]] = defaultdict(set)
    for claim in claims:
        digest = claim["coefficientSha256"]
        pair = tuple(claim["pair"])
        claim_index[digest].add(pair)
        claim_evidence[(digest, pair)].add(claim["evidence"])
    for occurrence in artifact_occurrences:
        if occurrence["pair"] is not None and occurrence["evidence"] is not None:
            digest = occurrence["coefficientSha256"]
            pair = tuple(occurrence["pair"])
            claim_index[digest].add(pair)
            claim_evidence[(digest, pair)].add(occurrence["evidence"])

    connection = sqlite3.connect(
        f"file:{args.database.resolve()}?immutable=1", uri=True
    )
    try:
        receipt_hashes, receipt_pairs, receipt_ids, receipt_audit = read_receipts(
            args.receipts, connection
        )
        occurrence_hashes = {row["coefficientSha256"] for row in all_occurrences}
        known_hashes = query_known_hashes(connection, occurrence_hashes)
    finally:
        connection.close()

    source_index: dict[str, list[dict]] = defaultdict(list)
    line_index: dict[str, str] = {}
    for occurrence in all_occurrences:
        digest = occurrence["coefficientSha256"]
        previous = line_index.setdefault(digest, occurrence["coefficientLine"])
        if previous != occurrence["coefficientLine"]:
            raise ValueError(f"SHA-256 collision or inconsistent canonical line: {digest}")
        source_index[digest].append(occurrence)

    ambiguous_claim_hashes = {
        digest: sorted([list(pair) for pair in pairs])
        for digest, pairs in claim_index.items()
        if len(pairs) > 1
    }
    skips: Counter = Counter()
    hits = []
    for digest in sorted(source_index):
        if digest in known_hashes:
            skips["already_in_polynomial_ledger"] += 1
            continue
        if digest in receipt_hashes:
            skips["submitted_receipt_hash"] += 1
            continue
        pairs = claim_index.get(digest, set())
        if len(pairs) != 1:
            skips["missing_or_ambiguous_exact_pair_claim"] += 1
            continue
        pair = next(iter(pairs))
        if pair not in live_pairs:
            skips["not_in_frozen_live_undiscovered"] += 1
            continue
        if pair in receipt_pairs:
            skips["target_pair_covered_by_submitted_receipt"] += 1
            continue
        exact_sources = [
            row
            for row in source_index[digest]
            if row["evidence"] is not None or row["source"].startswith("outbox/")
        ]
        if not exact_sources:
            skips["no_exact_payload_evidence"] += 1
            continue
        field_discs = [
            positive_integer(row["fieldDiscriminantAbs"]) for row in exact_sources
        ]
        field_discs = [value for value in field_discs if value is not None]
        polynomial_discs = [
            positive_integer(row["polynomialDiscriminantAbs"]) for row in exact_sources
        ]
        polynomial_discs = [value for value in polynomial_discs if value is not None]
        source_rows = sorted(
            {
                (row["source"], row["sourcePointer"], row["evidence"])
                for row in source_index[digest]
            }
        )
        hits.append(
            {
                "coefficientLine": line_index[digest],
                "coefficientSha256": digest,
                "exactPairEvidence": sorted(claim_evidence[(digest, pair)]),
                "fieldDiscriminantAbs": str(min(field_discs)) if field_discs else None,
                "pair": {"label": pair[0], "r": pair[1]},
                "polynomialDiscriminantAbs": (
                    str(min(polynomial_discs)) if polynomial_discs else None
                ),
                "sources": [
                    {
                        "artifact": source,
                        "evidence": evidence,
                        "pointer": pointer,
                    }
                    for source, pointer, evidence in source_rows
                ],
                "status": "exact_live_unsubmitted",
            }
        )

    best_by_pair = {}
    for row in hits:
        pair = (row["pair"]["label"], row["pair"]["r"])
        key = (
            positive_integer(row["fieldDiscriminantAbs"]) or 10**100000,
            positive_integer(row["polynomialDiscriminantAbs"]) or 10**100000,
            len(row["coefficientLine"]),
            row["coefficientSha256"],
        )
        if pair not in best_by_pair or key < best_by_pair[pair][0]:
            best_by_pair[pair] = (key, row)
    staged = [
        value[1]
        for _pair, value in sorted(
            best_by_pair.items(), key=lambda item: (int(item[0][0][3:]), item[0][1])
        )
    ]

    hits_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in hits
    )
    manifest_text = "".join(row["coefficientLine"] + "\n" for row in staged)
    audit_payload = {
        "artifactScan": {
            "artifactFiles": len(artifact_files),
            "artifactPolynomialOccurrences": len(artifact_occurrences),
            "exactHashPairClaims": len(claims),
            "outboxFiles": len(outbox_files),
            "outboxPolynomialOccurrences": len(outbox_occurrences),
            "uniquePolynomialHashes": len(source_index),
        },
        "deduplication": {
            "ambiguousExactPairClaimHashes": ambiguous_claim_hashes,
            "ambiguousExactPairClaimCount": len(ambiguous_claim_hashes),
            "knownLedgerHashesAmongCorpus": len(known_hashes),
            "method": "canonical 25-integral-coefficient line SHA-256",
            "skipCounts": dict(sorted(skips.items())),
        },
        "exactHits": {
            "distinctLivePairs": len(best_by_pair),
            "manifestPolynomialCount": len(staged),
            "uniqueExactLiveUnsubmittedHashes": len(hits),
            "pairs": [row["pair"] for row in staged],
            "polynomialHashes": [row["coefficientSha256"] for row in staged],
        },
        "inputs": {
            "database": str(args.database.resolve()),
            "databaseSha256": sha256_path(args.database),
            "frozenLive": str(args.live.resolve()),
            "frozenLivePairCount": len(live_pairs),
            "frozenLiveSha256": sha256_path(args.live),
        },
        "networkCalls": 0,
        "outputs": {
            "hits": str(args.hits.resolve()),
            "hitsSha256": sha256_bytes(hits_text.encode()),
            "manifest": str(args.manifest.resolve()),
            "manifestSha256": sha256_bytes(manifest_text.encode()),
        },
        "receiptExclusion": {
            "audit": receipt_audit,
            "receiptCount": len(receipt_ids),
            "submittedPolynomialHashes": len(receipt_hashes),
            "submittedTargetPairs": len(receipt_pairs),
        },
        "submissionCalls": 0,
    }
    audit_text = json.dumps(audit_payload, indent=2, sort_keys=True) + "\n"
    write_atomic(args.hits, hits_text)
    write_atomic(args.manifest, manifest_text)
    write_atomic(args.audit, audit_text)
    print(
        json.dumps(
            {
                "audit": str(args.audit.resolve()),
                "auditSha256": sha256_bytes(audit_text.encode()),
                "distinctLivePairs": len(best_by_pair),
                "hits": str(args.hits.resolve()),
                "hitsSha256": sha256_bytes(hits_text.encode()),
                "manifest": str(args.manifest.resolve()),
                "manifestSha256": sha256_bytes(manifest_text.encode()),
                "uniqueExactLiveUnsubmittedHashes": len(hits),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

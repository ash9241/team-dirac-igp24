#!/usr/bin/env python3
"""Seal the exact-certified legacy k<=4 shortlist without network access.

The script reads only frozen local candidate/certificate artifacts, the current
SQLite ledger, and local submission receipts.  It writes a new isolated
manifest plus coefficient-free certificate and summary.  Existing outputs are
never overwritten: an existing byte-identical output is accepted, while any
other collision fails closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import defaultdict
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DATABASE = DATA / "ledger.sqlite3"

MANIFEST = OUTBOX / "legacy_exact_shortlist_kle4_20260722.txt"
CERTIFICATE = DATA / "legacy_exact_shortlist_kle4_20260722_certificate.json"
SUMMARY = DATA / "legacy_exact_shortlist_kle4_20260722_summary.json"

LEGACY_MANIFESTS = (
    OUTBOX / "resume_post80_diversity_cap5_shared.txt",
    OUTBOX / "agent_gold_a_closure_batch2_shared.txt",
    OUTBOX / "agent_gold_a_exact_closure2_shared.txt",
    OUTBOX / "agent_gold_a_exact_diversity3_shared.txt",
    OUTBOX / "agent_gold_a_closure_batch_shared.txt",
    OUTBOX / "agent_gold_a_exact_closure1_shared.txt",
    OUTBOX / "agent_gold_b_positive_shared.txt",
    OUTBOX / "rank11_solo_pair_sum_stable.txt",
)

C1_CERT = DATA / "agent_gold_a_exact_closure1_frobenius_certificate.json"
C2_CERT = DATA / "agent_gold_a_exact_closure2_frobenius_certificate.json"
D3_CERT = DATA / "agent_gold_a_exact_diversity3_frobenius_certificate.json"
B1_CERT = DATA / "agent_gold_b_closure_batch1_frobenius_certificate.json"
B2_CERT = DATA / "agent_gold_b_closure_batch2_frobenius_certificate.json"


# Three target pairs have two exact legacy payloads.  Both alternatives are
# validated; selection is made below by field discriminant when available,
# otherwise polynomial discriminant, payload size, and hash.
CANDIDATE_SPECS = (
    ("70fe3f4440c613a8a166cbef5c5f224cc040cdee8b7c21ba44aef7471f418cb1", "24T13578", 8, C2_CERT),
    ("d263eea4ad87d574711dff877f715e347a89a98ce843c0ffda34b8b3b9650f48", "24T3408", 4, B2_CERT),
    ("d15148766fc00eba4bcf85fc9fb6be55adf9bab75252d15a5bfc4a382b9b8b90", "24T6176", 8, C2_CERT),
    ("4a12d38c8049026c37914d68559beffc5f87b20c5016db670d5ecd6f13e5921c", "24T8535", 0, D3_CERT),
    ("889629bbb4a842440421634a77881dd9e1cc62cd9a829bc84ae10641e8017991", "24T8535", 0, C2_CERT),
    ("239a17afccdb503b187e49a0d9642e82f976ec0abf99567d590602e3a56e2348", "24T8893", 0, C2_CERT),
    ("b09c3171a7c3d1f5a330b54d318589f090d09fcbb4d2fccfc22b92ee921234fc", "24T8893", 0, D3_CERT),
    ("4982cbaee419192290bdd48c799c56c2d2249ce4224684abe441fb25a58594c0", "24T14576", 0, C2_CERT),
    ("821ade30cf88eb12e2a3704de710cd9816b82f7bc00cf1b905992515c95a0fe8", "24T4031", 8, C1_CERT),
    ("3f3e10b75ec9f07b00e6d85aebc7a131dd5acf283c26af081c6a6f91241137e2", "24T6192", 8, C2_CERT),
    ("f9f0dbcd6ac333aae0cb4257aab0701c430d96f859ec8355dffe6a2938ebc5ed", "24T11835", 8, B1_CERT),
    ("3cf52230485af2e4c67f58b1c6e9bf1bdff7a1ae9c25e802437da8a96a38de45", "24T13786", 16, C2_CERT),
    ("2ad1b27a06f2ec110f23d0b9c0f2e5b05163981a7593342f9f556d19f1ebaed4", "24T13786", 16, D3_CERT),
    ("de77934450b636fd6792672ed30940afd021caa2d67414bb6a8d59ccc27d8a58", "24T15963", 16, C2_CERT),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def canonical_line(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.split("#", 1)[0].strip()
    if value.count(",") != 24:
        return None
    try:
        coefficients = [int(entry.strip()) for entry in value.split(",")]
    except ValueError:
        return None
    if (
        len(coefficients) != 25
        or coefficients[0] == 0
        or coefficients[-1] != 1
        or math.gcd(*coefficients) != 1
    ):
        return None
    return ",".join(str(entry) for entry in coefficients)


def json_text(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sealed_write(path: Path, payload: bytes) -> str:
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
        return "created"
    finally:
        temporary.unlink(missing_ok=True)


def load_legacy_payloads() -> tuple[dict[str, str], dict[str, list[dict]]]:
    lines: dict[str, str] = {}
    sources: dict[str, list[dict]] = defaultdict(list)
    for manifest in LEGACY_MANIFESTS:
        if not manifest.is_file():
            raise ValueError(f"missing legacy manifest: {manifest}")
        for line_number, raw in enumerate(
            manifest.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = canonical_line(raw)
            if line is None:
                continue
            digest = sha256_bytes(line.encode("ascii"))
            previous = lines.setdefault(digest, line)
            if previous != line:
                raise ValueError(f"hash collision or inconsistent payload: {digest}")
            sources[digest].append(
                {"manifest": relative(manifest), "lineNumber": line_number}
            )
    return lines, sources


def load_certificate(path: Path) -> tuple[dict, Path, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    input_path = Path(str(payload["input"])).expanduser().resolve()
    expected = str(payload["inputSha256"])
    actual = sha256_path(input_path)
    if actual != expected:
        raise ValueError(f"certificate input SHA mismatch: {path}")
    selected_sha = payload.get("selectedInputRowsSha256")
    if selected_sha is not None and str(selected_sha) != expected:
        raise ValueError(f"certificate selected-input SHA mismatch: {path}")
    return payload, input_path, actual


def exact_assignments(certificate: dict, digest: str) -> list[dict]:
    matches = []
    for row_index, row in enumerate(certificate.get("rows") or []):
        if row.get("status") != "resolved":
            continue
        remaining = row.get("remainingLabelAssignments")
        if not isinstance(remaining, list) or len(remaining) != 1:
            continue
        for assignment_index, assignment in enumerate(row.get("assignments") or []):
            if assignment.get("coefficientSha256") != digest:
                continue
            matches.append(
                {
                    "assignment": assignment,
                    "assignmentIndex": assignment_index,
                    "row": row,
                    "rowIndex": row_index,
                }
            )
    return matches


def candidate_payload(input_path: Path, digest: str) -> dict:
    matches = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            for candidate_index, candidate in enumerate(row.get("candidates") or []):
                if candidate.get("coefficientSha256") == digest:
                    matches.append(
                        {
                            "candidate": candidate,
                            "candidateIndex": candidate_index,
                            "lineNumber": line_number,
                            "row": row,
                        }
                    )
    if len(matches) != 1:
        raise ValueError(
            f"expected one candidate payload for {digest}, found {len(matches)}"
        )
    return matches[0]


def collect_global_exact_claims(digests: set[str]) -> dict[str, set[tuple[str, int]]]:
    claims: dict[str, set[tuple[str, int]]] = defaultdict(set)
    paths = sorted(DATA.glob("*frobenius_certificate.json")) + sorted(
        DATA.glob("*frobenius.json")
    )
    for path in paths:
        try:
            certificate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(certificate, dict):
            continue
        for digest in digests:
            for match in exact_assignments(certificate, digest):
                assignment = match["assignment"]
                label = assignment.get("targetLabel")
                r = assignment.get("targetR")
                if isinstance(label, str) and r is not None:
                    claims[digest].add((label, int(r)))
    return claims


def collect_discriminant_evidence(digests: set[str]) -> tuple[dict, int]:
    evidence: dict[str, list[dict]] = defaultdict(list)
    files_with_hashes = 0

    def walk(value: object, source: Path, pointer: str) -> None:
        if isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, (dict, list)):
                    walk(child, source, f"{pointer}/{index}")
            return
        if not isinstance(value, dict):
            return
        digest = value.get("coefficientSha256", value.get("candidateSha256"))
        if digest in digests:
            field_disc = value.get(
                "fieldDiscriminantAbs", value.get("candidateFieldDiscriminantAbs")
            )
            if field_disc is not None:
                field_disc = int(field_disc)
                if field_disc <= 0:
                    raise ValueError(f"invalid field discriminant at {source}:{pointer}")
                evidence[str(digest)].append(
                    {
                        "artifact": relative(source),
                        "fieldDiscriminantAbs": str(field_disc),
                        "pointer": pointer,
                    }
                )
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                walk(child, source, f"{pointer}/{key}")

    for source in sorted(DATA.glob("*.json")) + sorted(DATA.glob("*.jsonl")):
        try:
            raw = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(digest in raw for digest in digests):
            continue
        files_with_hashes += 1
        try:
            if source.suffix == ".jsonl":
                values = [json.loads(line) for line in raw.splitlines() if line.strip()]
            else:
                values = [json.loads(raw)]
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in discriminant scan: {source}") from exc
        for index, value in enumerate(values):
            walk(value, source, f"$/{index}")
    return evidence, files_with_hashes


def receipt_audit(connection: sqlite3.Connection, digests: set[str], pairs: set[tuple[str, int]]) -> dict:
    receipt_files = sorted(RECEIPTS.glob("sub_*.json"))
    matching_manifests = 0
    changed_manifests = 0
    missing_manifests = 0
    receipt_hash_hits = []
    receipt_pair_hits = []
    audited_ids = set()
    unsynced_resolved_by_stage_summary = []

    stage_summaries_by_manifest_sha: dict[str, list[dict]] = defaultdict(list)
    for summary_path in sorted(DATA.glob("*.json")):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict):
            continue
        artifact_sha = summary.get("artifactSha256")
        manifest_sha = (
            artifact_sha.get("manifest")
            if isinstance(artifact_sha, dict)
            else summary.get("manifestSha256")
        )
        staged_pairs = summary.get("stagedPairs")
        if not isinstance(staged_pairs, list):
            staged_pairs = summary.get("selectedPairs")
        if not isinstance(staged_pairs, list):
            continue
        if not isinstance(manifest_sha, str):
            continue
        parsed_pairs = set()
        for raw_pair in staged_pairs:
            if isinstance(raw_pair, str) and "/r" in raw_pair:
                label, raw_r = raw_pair.rsplit("/r", 1)
                parsed_pairs.add((label, int(raw_r)))
            elif isinstance(raw_pair, dict):
                label = raw_pair.get("label", raw_pair.get("targetLabel"))
                r = raw_pair.get("r", raw_pair.get("targetR"))
                if not isinstance(label, str) or r is None:
                    raise ValueError(f"invalid staged pair in {summary_path}")
                parsed_pairs.add((label, int(r)))
            else:
                raise ValueError(f"invalid staged pair in {summary_path}")
        if not parsed_pairs:
            continue
        stage_summaries_by_manifest_sha[manifest_sha].append(
            {
                "artifact": relative(summary_path),
                "artifactSha256": sha256_path(summary_path),
                "pairs": parsed_pairs,
                "status": summary.get("status"),
            }
        )

    for path in receipt_files:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        response = receipt.get("response") or {}
        submission_id = str(response.get("submissionId") or path.stem)
        audited_ids.add(submission_id)
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials WHERE submission_id=?",
                (submission_id,),
            )
        }
        expected_count = int(receipt.get("polynomials", 0))
        manifest_value = receipt.get("manifest")
        recorded_sha = receipt.get("manifestHash")
        matching_manifest_hashes = None
        matching_manifest_path = None
        if manifest_value and recorded_sha:
            candidate_manifest = Path(str(manifest_value)).expanduser().resolve()
            if candidate_manifest.is_file() and sha256_path(candidate_manifest) == str(
                recorded_sha
            ):
                matching_manifest_path = candidate_manifest
                matching_manifest_hashes = set()
                for raw in candidate_manifest.read_text(encoding="utf-8").splitlines():
                    line = canonical_line(raw)
                    if line is not None:
                        matching_manifest_hashes.add(sha256_bytes(line.encode("ascii")))
        if len(ledger_hashes) < expected_count:
            if (
                matching_manifest_hashes is None
                or len(matching_manifest_hashes) != expected_count
            ):
                raise ValueError(f"unsynced receipt lacks an intact manifest: {path}")
            stage_summaries = stage_summaries_by_manifest_sha.get(str(recorded_sha), [])
            if not stage_summaries:
                raise ValueError(
                    f"unsynced receipt lacks a hash-bound exact stage summary: {path}"
                )
            stage_pairs = set().union(
                *(summary["pairs"] for summary in stage_summaries)
            )
            overlap = pairs & stage_pairs
            if overlap:
                raise ValueError(
                    f"unsynced exact receipt overlaps selected target pairs: {overlap}"
                )
            unsynced_resolved_by_stage_summary.append(
                {
                    "manifestSha256": str(recorded_sha),
                    "receipt": relative(path),
                    "stageSummaries": [
                        {
                            "artifact": summary["artifact"],
                            "artifactSha256": summary["artifactSha256"],
                            "pairs": [
                                f"{label}/r{r}"
                                for label, r in sorted(summary["pairs"])
                            ],
                            "status": summary["status"],
                        }
                        for summary in stage_summaries
                    ],
                }
            )
        for digest in sorted(digests & ledger_hashes):
            receipt_hash_hits.append(
                {"coefficientSha256": digest, "receipt": relative(path)}
            )
        ledger_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT label,r FROM verifications WHERE submission_id=?",
                (submission_id,),
            )
            if label is not None and r is not None
        }
        for label, r in sorted(pairs & ledger_pairs):
            receipt_pair_hits.append(
                {"pair": f"{label}/r{r}", "receipt": relative(path)}
            )

        if not manifest_value or not recorded_sha:
            missing_manifests += 1
            continue
        if matching_manifest_path is None or matching_manifest_hashes is None:
            manifest = Path(str(manifest_value)).expanduser().resolve()
            if not manifest.is_file():
                missing_manifests += 1
                continue
            changed_manifests += 1
            continue
        manifest = matching_manifest_path
        if not manifest.is_file():
            missing_manifests += 1
            continue
        matching_manifests += 1
        for digest in sorted(digests & matching_manifest_hashes):
            receipt_hash_hits.append(
                {"coefficientSha256": digest, "receiptManifest": relative(manifest)}
            )

    table_receipts = [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT submission_id,manifest_path,manifest_hash FROM submission_receipts"
        )
    ]
    for submission_id, manifest_value, recorded_sha in table_receipts:
        audited_ids.add(submission_id)
        manifest = Path(manifest_value).expanduser().resolve()
        if not manifest.is_file() or sha256_path(manifest) != recorded_sha:
            continue
        manifest_hashes = set()
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            line = canonical_line(raw)
            if line is not None:
                manifest_hashes.add(sha256_bytes(line.encode("ascii")))
        for digest in sorted(digests & manifest_hashes):
            receipt_hash_hits.append(
                {
                    "coefficientSha256": digest,
                    "submissionReceiptManifest": relative(manifest),
                }
            )

    if receipt_hash_hits or receipt_pair_hits:
        raise ValueError("candidate hash or target pair is covered by a prior receipt")
    return {
        "auditedDistinctSubmissionIds": len(audited_ids),
        "filesystemReceiptCount": len(receipt_files),
        "matchingCurrentReceiptManifests": matching_manifests,
        "changedCurrentReceiptManifests": changed_manifests,
        "missingOrUnrecordedReceiptManifests": missing_manifests,
        "submissionReceiptTableRows": len(table_receipts),
        "unsyncedReceiptsResolvedByHashBoundStageSummary": (
            unsynced_resolved_by_stage_summary
        ),
        "candidateHashHits": 0,
        "targetPairHits": 0,
    }


def main() -> int:
    legacy_lines, legacy_sources = load_legacy_payloads()
    configured_hashes = {spec[0] for spec in CANDIDATE_SPECS}
    if not configured_hashes <= legacy_lines.keys():
        missing = sorted(configured_hashes - legacy_lines.keys())
        raise ValueError(f"configured candidate absent from legacy manifests: {missing}")

    global_claims = collect_global_exact_claims(set(legacy_lines))
    candidate_rows = []
    certificate_cache = {}
    for digest, label, r, certificate_path in CANDIDATE_SPECS:
        expected_pair = (label, r)
        claims = global_claims.get(digest, set())
        if claims != {expected_pair}:
            raise ValueError(f"nonunique global exact assignment for {digest}: {claims}")

        if certificate_path not in certificate_cache:
            certificate_cache[certificate_path] = load_certificate(certificate_path)
        certificate, input_path, input_sha = certificate_cache[certificate_path]
        matches = exact_assignments(certificate, digest)
        match_pairs = {
            (
                str(match["assignment"].get("targetLabel")),
                int(match["assignment"].get("targetR")),
            )
            for match in matches
        }
        if len(matches) != 1 or match_pairs != {expected_pair}:
            raise ValueError(f"primary certificate is not unique for {digest}")
        proof = matches[0]
        assignment = proof["assignment"]
        proof_row = proof["row"]

        payload = candidate_payload(input_path, digest)
        candidate = payload["candidate"]
        source_row = payload["row"]
        line = canonical_line(candidate.get("coefficientLine"))
        if line is None or sha256_bytes(line.encode("ascii")) != digest:
            raise ValueError(f"candidate payload/hash mismatch: {digest}")
        if line != legacy_lines[digest]:
            raise ValueError(f"legacy and candidate payload differ: {digest}")
        if int(candidate.get("targetR", -1)) != r:
            raise ValueError(f"candidate signature mismatch: {digest}")
        if int(candidate.get("factorIndex", -1)) != int(assignment["factorIndex"]):
            raise ValueError(f"candidate factor-index mismatch: {digest}")
        if int(candidate.get("coefficientBytes", -1)) != len(line.encode("ascii")):
            raise ValueError(f"candidate byte-count mismatch: {digest}")
        polynomial_disc = int(candidate.get("polynomialDiscriminantAbs", 0))
        if polynomial_disc <= 0:
            raise ValueError(f"missing positive polynomial discriminant: {digest}")
        if not str(source_row.get("status", "")).startswith("certified_"):
            raise ValueError(f"candidate source row is not certified: {digest}")
        if int(source_row.get("workerExitCode", -1)) != 0:
            raise ValueError(f"candidate worker did not exit cleanly: {digest}")
        for key in (
            "sourceLabel",
            "sourceR",
            "sourceSubmissionId",
            "sourcePolynomialIndex",
        ):
            if source_row.get(key) != proof_row.get(key):
                raise ValueError(f"candidate/proof source mismatch for {digest}: {key}")

        candidate_rows.append(
            {
                "coefficientLine": line,
                "coefficientSha256": digest,
                "coefficientBytes": len(line.encode("ascii")),
                "legacySources": legacy_sources[digest],
                "pair": expected_pair,
                "polynomialDiscriminantAbs": polynomial_disc,
                "proof": {
                    "artifact": relative(certificate_path),
                    "artifactSha256": sha256_path(certificate_path),
                    "input": relative(input_path),
                    "inputSha256": input_sha,
                    "method": certificate.get("method"),
                    "pointer": (
                        f"rows[{proof['rowIndex']}].assignments"
                        f"[{proof['assignmentIndex']}]"
                    ),
                    "remainingLabelAssignments": 1,
                    "rowStatus": proof_row.get("status"),
                },
                "result": {
                    "artifact": relative(input_path),
                    "artifactSha256": input_sha,
                    "candidateIndex": payload["candidateIndex"],
                    "factorIndex": int(candidate["factorIndex"]),
                    "lineNumber": payload["lineNumber"],
                    "sourceLabel": source_row.get("sourceLabel"),
                    "sourcePolynomialIndex": source_row.get("sourcePolynomialIndex"),
                    "sourceR": source_row.get("sourceR"),
                    "sourceSubmissionId": source_row.get("sourceSubmissionId"),
                },
            }
        )

    field_evidence, discriminant_files = collect_discriminant_evidence(
        configured_hashes
    )
    for row in candidate_rows:
        values = {
            int(item["fieldDiscriminantAbs"])
            for item in field_evidence.get(row["coefficientSha256"], [])
        }
        if len(values) > 1:
            raise ValueError(
                f"conflicting field-discriminant claims: {row['coefficientSha256']}"
            )
        row["fieldDiscriminantAbs"] = next(iter(values)) if values else None
        row["fieldDiscriminantEvidence"] = field_evidence.get(
            row["coefficientSha256"], []
        )

    desired_pairs = {row["pair"] for row in candidate_rows}
    exact_legacy_pool = defaultdict(set)
    with sqlite3.connect(f"file:{DATABASE.resolve()}?mode=ro", uri=True) as connection:
        for digest, claims in global_claims.items():
            if len(claims) != 1:
                continue
            pair = next(iter(claims))
            if pair not in desired_pairs:
                continue
            known = connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (digest,),
            ).fetchone()
            if known is None:
                exact_legacy_pool[pair].add(digest)
        configured_pool = defaultdict(set)
        for row in candidate_rows:
            configured_pool[row["pair"]].add(row["coefficientSha256"])
        if dict(exact_legacy_pool) != dict(configured_pool):
            raise ValueError("configured pool does not exhaust exact unknown legacy pool")

        selected = []
        alternatives = []
        by_pair = defaultdict(list)
        for row in candidate_rows:
            by_pair[row["pair"]].append(row)
        for pair, rows in by_pair.items():
            field_complete = all(row["fieldDiscriminantAbs"] is not None for row in rows)
            if field_complete:
                basis = "field_discriminant_abs"
                key = lambda row: (
                    row["fieldDiscriminantAbs"],
                    row["polynomialDiscriminantAbs"],
                    row["coefficientBytes"],
                    row["coefficientSha256"],
                )
            else:
                if any(row["fieldDiscriminantAbs"] is not None for row in rows):
                    raise ValueError(f"partial field-discriminant evidence for {pair}")
                basis = "polynomial_discriminant_abs_fallback"
                key = lambda row: (
                    row["polynomialDiscriminantAbs"],
                    row["coefficientBytes"],
                    row["coefficientSha256"],
                )
            ordered = sorted(rows, key=key)
            winner = ordered[0]
            winner["dedupeBasis"] = basis
            winner["dedupeCandidateCount"] = len(rows)
            selected.append(winner)
            for loser in ordered[1:]:
                alternatives.append(
                    {
                        "coefficientSha256": loser["coefficientSha256"],
                        "fieldDiscriminantAbs": (
                            str(loser["fieldDiscriminantAbs"])
                            if loser["fieldDiscriminantAbs"] is not None
                            else None
                        ),
                        "pair": f"{pair[0]}/r{pair[1]}",
                        "polynomialDiscriminantAbs": str(
                            loser["polynomialDiscriminantAbs"]
                        ),
                        "reason": f"deduped_by_{basis}",
                        "selectedCoefficientSha256": winner["coefficientSha256"],
                    }
                )

        selected.sort(
            key=lambda row: (
                int(connection.execute(
                    "SELECT team_count FROM targets WHERE label=? AND r=?",
                    row["pair"],
                ).fetchone()[0]),
                int(row["pair"][0][3:]),
                row["pair"][1],
            )
        )
        if len(selected) != 11 or len({row["pair"] for row in selected}) != 11:
            raise ValueError("stage must contain exactly eleven distinct pairs")

        score = Fraction(0, 1)
        target_generated = []
        for row in selected:
            digest = row["coefficientSha256"]
            label, r = row["pair"]
            if connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (digest,),
            ).fetchone() is not None:
                raise ValueError(f"candidate hash is already in ledger: {digest}")
            owned_rows = connection.execute(
                "SELECT submission_id,polynomial_index,status FROM verifications "
                "WHERE label=? AND r=?",
                (label, r),
            ).fetchall()
            if owned_rows:
                raise ValueError(f"target pair is already locally owned: {label}/r{r}")
            if connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1",
                (label, r),
            ).fetchone() is not None:
                raise ValueError(f"target pair is in baseline: {label}/r{r}")
            target = connection.execute(
                "SELECT team_count,minimum_disc_abs,discovered,generated_at "
                "FROM targets WHERE label=? AND r=?",
                (label, r),
            ).fetchone()
            if target is None:
                raise ValueError(f"missing current target row: {label}/r{r}")
            team_count = int(target[0])
            if team_count < 0 or team_count > 4:
                raise ValueError(f"target left k<=4 shortlist: {label}/r{r}")
            if not target[3]:
                raise ValueError(f"target row lacks generation timestamp: {label}/r{r}")
            target_generated.append(str(target[3]))
            marginal = Fraction(1, 2**team_count)
            score += marginal
            row["target"] = {
                "discovered": bool(target[2]),
                "generatedAt": str(target[3]),
                "label": label,
                "minimumDiscAbs": str(target[1]) if target[1] is not None else None,
                "r": r,
                "teamCount": team_count,
            }
            row["projectedMarginalScore"] = float(marginal)
            row["projectedMarginalScoreExact"] = (
                f"{marginal.numerator}/{marginal.denominator}"
            )
            row["checks"] = {
                "baselineAbsent": True,
                "coefficientHashAbsentFromLedger": True,
                "exactAssignmentUniqueAcrossLocalCertificates": True,
                "localPairUnowned": True,
                "targetPresentAndTeamCountAtMost4": True,
            }

        selected_hashes = {row["coefficientSha256"] for row in selected}
        selected_pairs = {row["pair"] for row in selected}
        receipts = receipt_audit(connection, selected_hashes, selected_pairs)

        manifest_payload = (
            "".join(row["coefficientLine"] + "\n" for row in selected)
        ).encode("ascii")
        manifest_sha = sha256_bytes(manifest_payload)
        script_sha = sha256_path(Path(__file__).resolve())

        public_selected = []
        for row in selected:
            public_selected.append(
                {
                    key: value
                    for key, value in row.items()
                    if key != "coefficientLine"
                }
            )
            public_selected[-1]["fieldDiscriminantAbs"] = (
                str(row["fieldDiscriminantAbs"])
                if row["fieldDiscriminantAbs"] is not None
                else None
            )
            public_selected[-1]["polynomialDiscriminantAbs"] = str(
                row["polynomialDiscriminantAbs"]
            )
            public_selected[-1]["pair"] = f"{row['pair'][0]}/r{row['pair'][1]}"

        certificate_payload = {
            "checks": {
                "allCertificateInputSha256Matched": True,
                "allCandidatePayloadHashesMatched": True,
                "allGlobalExactAssignmentsUnique": True,
                "allPairsNonbaseline": True,
                "allPairsUnownedLocally": True,
                "allSelectedHashesAbsentFromLedger": True,
                "allTargetsCurrentAndTeamCountAtMost4": True,
                "receiptExclusionsPassed": True,
            },
            "database": relative(DATABASE),
            "discriminantEvidenceFilesContainingPoolHashes": discriminant_files,
            "duplicateAlternatives": sorted(
                alternatives, key=lambda row: (row["pair"], row["coefficientSha256"])
            ),
            "legacyManifests": [
                {"path": relative(path), "sha256": sha256_path(path)}
                for path in LEGACY_MANIFESTS
            ],
            "manifest": {
                "bytes": len(manifest_payload),
                "path": relative(MANIFEST),
                "polynomials": len(selected),
                "sha256": manifest_sha,
            },
            "method": "legacy-exact-frobenius-kle4-sealed-stage-v1",
            "networkCalls": 0,
            "projectedMarginalScore": float(score),
            "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
            "receiptExclusion": receipts,
            "selected": public_selected,
            "selection": {
                "candidatePoolHashes": len(candidate_rows),
                "deduplicatedTargetPairs": len(selected),
                "fieldDiscriminantPolicy": (
                    "use fieldDiscriminantAbs only when every candidate for a pair has "
                    "consistent local evidence; otherwise use polynomialDiscriminantAbs, "
                    "coefficientBytes, then coefficientSha256"
                ),
                "poolScope": "ledger-absent exact-certified hashes in eight named legacy manifests",
                "requiredMaximumTeamCount": 4,
            },
            "stageScript": {
                "path": relative(Path(__file__).resolve()),
                "sha256": script_sha,
            },
            "submissionCalls": 0,
            "targetSnapshot": {
                "generatedAtMax": max(target_generated),
                "generatedAtMin": min(target_generated),
                "targetTableRows": int(
                    connection.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
                ),
            },
        }
        certificate_bytes = json_text(certificate_payload)
        certificate_sha = sha256_bytes(certificate_bytes)
        summary_payload = {
            "certificate": relative(CERTIFICATE),
            "certificateSha256": certificate_sha,
            "checksPassed": len(certificate_payload["checks"]),
            "duplicateAlternativesExcluded": len(alternatives),
            "manifest": relative(MANIFEST),
            "manifestBytes": len(manifest_payload),
            "manifestSha256": manifest_sha,
            "networkCalls": 0,
            "polynomials": len(selected),
            "projectedMarginalScore": float(score),
            "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
            "selectedPairs": [
                {
                    "coefficientSha256": row["coefficientSha256"],
                    "pair": f"{row['pair'][0]}/r{row['pair'][1]}",
                    "projectedMarginalScoreExact": row[
                        "projectedMarginalScoreExact"
                    ],
                    "teamCount": row["target"]["teamCount"],
                }
                for row in selected
            ],
            "status": "sealed_not_submitted",
            "submissionCalls": 0,
        }
        summary_bytes = json_text(summary_payload)

    output_status = {
        "manifest": sealed_write(MANIFEST, manifest_payload),
        "certificate": sealed_write(CERTIFICATE, certificate_bytes),
        "summary": sealed_write(SUMMARY, summary_bytes),
    }
    print(
        json.dumps(
            {
                "manifest": relative(MANIFEST),
                "manifestSha256": manifest_sha,
                "outputs": output_status,
                "polynomials": len(selected),
                "projectedMarginalScoreExact": (
                    f"{score.numerator}/{score.denominator}"
                ),
                "status": "sealed_not_submitted",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

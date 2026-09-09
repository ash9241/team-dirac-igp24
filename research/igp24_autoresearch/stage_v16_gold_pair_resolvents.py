#!/usr/bin/env python3
"""Stage the two v16 deterministic gold pairs after exact factor assignment.

The stager accepts only the sealed two-source worker artifact and an exact
Frobenius assignment certificate.  It rechecks the live ledger, baseline,
ownership, coefficient/field novelty, every local receipt, and all outbox
hashes.  It selects one best field per target pair and writes a manifest plus
coefficient-free sealed postflight artifacts.  It has no network or submit
code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import stage_frobenius_gold as frobenius
import stage_single_exact_census as receipt_helper


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
RUN_CERTIFICATE = DATA / "v16_gold_pair_resolvent_run_certificate.json"
CANDIDATES = DATA / "v16_gold_pair_resolvent_candidates.jsonl"
FROBENIUS = DATA / "v16_gold_pair_resolvent_frobenius_certificate.json"
MANIFEST = ROOT / "outbox/v16_gold_pair_resolvents.txt"
POSTFLIGHT = DATA / "v16_gold_pair_resolvent_postflight.json"
SUMMARY = DATA / "v16_gold_pair_resolvent_stage_summary.json"

ALLOWED_SOURCES = {
    ("sub_574f9e56ce804797ab49160e2f9838e6", 4, "24T16875", 24),
    ("sub_574f9e56ce804797ab49160e2f9838e6", 12, "24T17260", 24),
}
TARGET_MULTIPLICITIES = {("24T17570", 24): 1, ("24T17796", 24): 2}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def canonical_line(raw: object) -> tuple[str, str, int]:
    values = [int(value) for value in str(raw).split(",")]
    if (
        len(values) != 25
        or values[0] == 0
        or values[-1] != 1
        or math.gcd(*values) != 1
    ):
        raise ValueError("invalid primitive monic degree-24 payload")
    line = ",".join(str(value) for value in values)
    return line, sha256_bytes(line.encode("ascii")), len(line.encode("ascii"))


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_new(path: Path, payload: bytes) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite sealed output: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite sealed output: {destination}")
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_run_certificate(path: Path, candidates_path: Path) -> dict:
    certificate = read_json(path)
    artifact = certificate.get("candidateArtifact") or {}
    checks = certificate.get("checks") or {}
    if (
        certificate.get("schemaVersion")
        != "v16-gold-pair-resolvent-run-certificate-v1"
        or certificate.get("status") != "two_sources_certified_sequentially"
        or int(artifact.get("rows", -1)) != 2
        or str(artifact.get("sha256")) != sha256_path(candidates_path)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise ValueError("v16 pair-resolvent run certificate is not sealed")
    return certificate


def exact_join(
    proof: dict, candidate_rows: list[dict], candidate_path: Path
) -> tuple[list[dict], list[dict]]:
    joined = frobenius.join_resolved_assignments(
        proof, candidate_rows, candidate_path, allow_unresolved=False
    )
    source_keys = {
        (
            str(row.get("sourceSubmissionId")),
            int(row.get("sourcePolynomialIndex", -1)),
            str(row.get("sourceLabel")),
            int(row.get("sourceR", -1)),
        )
        for row in candidate_rows
    }
    if source_keys != ALLOWED_SOURCES or len(joined) != 6:
        raise ValueError("exact assignment does not cover the two pinned v16 sources")
    payload_by_hash = {}
    for row in candidate_rows:
        for candidate in row.get("candidates") or []:
            line, digest, byte_count = canonical_line(candidate.get("coefficientLine"))
            if digest != str(candidate.get("coefficientSha256")):
                raise ValueError("candidate hash mismatch")
            field_disc = int(candidate.get("fieldDiscriminantAbs", 0))
            poly_disc = int(candidate.get("polynomialDiscriminantAbs", 0))
            if field_disc <= 0 or poly_disc <= 0:
                raise ValueError("candidate discriminants are not positive and exact")
            payload_by_hash[digest] = {
                "coefficientLine": line,
                "coefficientBytes": byte_count,
                "fieldDiscriminantAbs": field_disc,
                "polynomialDiscriminantAbs": poly_disc,
            }
    enriched = [{**row, **payload_by_hash[row["coefficientSha256"]]} for row in joined]
    target_rows = [
        row
        for row in enriched
        if (str(row["targetLabel"]), int(row["targetR"])) in TARGET_MULTIPLICITIES
    ]
    counts = Counter((str(row["targetLabel"]), int(row["targetR"])) for row in target_rows)
    if counts != Counter(TARGET_MULTIPLICITIES):
        raise ValueError("resolved v16 gold factor multiplicities changed")
    best = {}
    alternatives = []
    for row in target_rows:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        key = (
            int(row["fieldDiscriminantAbs"]),
            int(row["polynomialDiscriminantAbs"]),
            int(row["coefficientBytes"]),
            str(row["coefficientSha256"]),
        )
        incumbent = best.get(pair)
        if incumbent is None:
            best[pair] = row
        else:
            incumbent_key = (
                int(incumbent["fieldDiscriminantAbs"]),
                int(incumbent["polynomialDiscriminantAbs"]),
                int(incumbent["coefficientBytes"]),
                str(incumbent["coefficientSha256"]),
            )
            if key < incumbent_key:
                alternatives.append(incumbent)
                best[pair] = row
            else:
                alternatives.append(row)
    selected = [best[pair] for pair in sorted(best, key=lambda item: int(item[0][3:]))]
    if set(best) != set(TARGET_MULTIPLICITIES) or len(selected) != 2 or len(alternatives) != 1:
        raise ValueError("failed to select exactly one best candidate per v16 gold pair")
    return selected, alternatives


def receipt_snapshot(
    connection: sqlite3.Connection, data_dir: Path, receipts_dir: Path
) -> tuple[set[str], set[tuple[str, int]], dict]:
    prior_candidates, corpus = receipt_helper.scan_candidates(data_dir)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for candidate in prior_candidates:
        pair_index[str(candidate["coefficientSha256"])].add(
            (str(candidate["targetLabel"]), int(candidate["targetR"]))
        )
    hashes, pairs, audit = receipt_helper.receipt_exclusions(
        receipts_dir, data_dir, connection, pair_index
    )
    return hashes, pairs, {
        **{key: value for key, value in audit.items() if key != "audit"},
        "artifactFiles": int(corpus["artifactFiles"]),
        "artifactFileIndexSha256": str(corpus["artifactFileIndexSha256"]),
    }


def outbox_hashes(outbox: Path) -> set[str]:
    hashes = set()
    for path in sorted(outbox.glob("*.txt")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.split("#", 1)[0].strip()
            if stripped.count(",") != 24:
                continue
            try:
                _, digest, _ = canonical_line(stripped)
            except (TypeError, ValueError):
                continue
            hashes.add(digest)
    return hashes


def validate_live(
    connection: sqlite3.Connection,
    selected: list[dict],
    receipt_hashes: set[str],
    receipt_pairs: set[tuple[str, int]],
    staged_hashes: set[str],
) -> list[dict]:
    target_rows, target_labels = connection.execute(
        "SELECT COUNT(*),COUNT(DISTINCT label) FROM targets"
    ).fetchone()
    if (int(target_rows), int(target_labels)) != (165_836, 25_000):
        raise ValueError("target cache is incomplete")
    snapshots = []
    for row in selected:
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        target = connection.execute(
            "SELECT team_count,discovered,generated_at FROM targets "
            "WHERE label=? AND r=?", pair,
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
        ).fetchone()
        owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
            pair,
        ).fetchone()
        known = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (row["coefficientSha256"],),
        ).fetchone()
        same_field = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND "
            "field_disc_abs=? AND scoreable=1 LIMIT 1",
            (pair[0], pair[1], str(row["fieldDiscriminantAbs"])),
        ).fetchone()
        if (
            target is None
            or int(target[0]) != 0
            or bool(target[1])
            or baseline is not None
            or owned is not None
            or known is not None
            or same_field is not None
            or row["coefficientSha256"] in receipt_hashes
            or pair in receipt_pairs
            or row["coefficientSha256"] in staged_hashes
        ):
            raise ValueError(f"v16 gold candidate is no longer exact/live/novel: {pair}")
        snapshots.append(
            {
                "label": pair[0],
                "r": pair[1],
                "teamCount": int(target[0]),
                "discovered": bool(target[1]),
                "generatedAt": str(target[2]),
                "baseline": False,
                "owned": False,
                "knownCoefficientHash": False,
                "sameTargetField": False,
                "receiptedHash": False,
                "receiptedPair": False,
                "alreadyInOutbox": False,
            }
        )
    return snapshots


def public_row(row: dict) -> dict:
    return {
        "sourceLabel": str(row["sourceLabel"]),
        "sourceR": int(row["sourceR"]),
        "sourcePolynomialIndex": int(row["sourcePolynomialIndex"]),
        "factorIndex": int(row["factorIndex"]),
        "targetLabel": str(row["targetLabel"]),
        "targetR": int(row["targetR"]),
        "coefficientSha256": str(row["coefficientSha256"]),
        "coefficientBytes": int(row["coefficientBytes"]),
        "fieldDiscriminantAbs": str(row["fieldDiscriminantAbs"]),
        "polynomialDiscriminantAbs": str(row["polynomialDiscriminantAbs"]),
    }


def stage(
    *,
    candidates_path: Path = CANDIDATES,
    proof_path: Path = FROBENIUS,
    run_certificate_path: Path = RUN_CERTIFICATE,
    database: Path = DB,
    data_dir: Path = DATA,
    receipts_dir: Path = RECEIPTS,
    manifest: Path = MANIFEST,
    postflight: Path = POSTFLIGHT,
    summary: Path = SUMMARY,
) -> dict:
    outputs = [manifest.resolve(), postflight.resolve(), summary.resolve()]
    if len(set(outputs)) != 3 or any(path.exists() for path in outputs):
        raise FileExistsError("refusing to overwrite or alias v16 stage outputs")
    validate_run_certificate(run_certificate_path, candidates_path)
    candidate_rows = read_jsonl(candidates_path)
    proof = read_json(proof_path)
    selected, alternatives = exact_join(proof, candidate_rows, candidates_path)
    with sqlite3.connect(
        f"file:{database.expanduser().resolve()}?mode=ro", uri=True
    ) as connection:
        receipt_hashes, receipt_pairs, receipt_audit = receipt_snapshot(
            connection, data_dir.resolve(), receipts_dir.resolve()
        )
        snapshots = validate_live(
            connection,
            selected,
            receipt_hashes,
            receipt_pairs,
            outbox_hashes(ROOT / "outbox"),
        )
    manifest_payload = "".join(row["coefficientLine"] + "\n" for row in selected).encode("ascii")
    manifest_sha = sha256_bytes(manifest_payload)
    score = sum((Fraction(1, 2 ** snapshot["teamCount"]) for snapshot in snapshots), Fraction())
    postflight_value = {
        "schemaVersion": "v16-gold-pair-resolvent-postflight-v1",
        "status": "certified_exact_live_tc0_unreceipted_staged",
        "checks": {
            "acceptedSourcesPinned": True,
            "candidateArtifactHashMatched": True,
            "exactFrobeniusAssignmentsResolved": True,
            "factorMultiplicityMatched": True,
            "oneBestCandidatePerTargetPair": True,
            "targetCacheComplete": True,
            "targetsTc0UndiscoveredNonbaselineUnowned": True,
            "coefficientAndFieldNoveltyPassed": True,
            "receiptHashAndPairExclusionsPassed": True,
            "outboxHashExclusionsPassed": True,
            "coefficientPayloadConfinedToManifest": True,
        },
        "artifacts": {
            "candidates": {"path": display(candidates_path), "sha256": sha256_path(candidates_path)},
            "frobeniusCertificate": {"path": display(proof_path), "sha256": sha256_path(proof_path)},
            "runCertificate": {"path": display(run_certificate_path), "sha256": sha256_path(run_certificate_path)},
            "manifest": {"path": display(manifest), "sha256": manifest_sha, "polynomials": 2},
        },
        "selected": [public_row(row) for row in selected],
        "excludedExactAlternatives": [public_row(row) for row in alternatives],
        "targetSnapshot": snapshots,
        "receiptExclusion": receipt_audit,
        "projectedMarginalScoreExact": f"{score.numerator}/{score.denominator}",
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    postflight_payload = (
        json.dumps(postflight_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    summary_value = {
        "status": postflight_value["status"],
        "polynomials": 2,
        "pairs": [f"{row['targetLabel']}/r{row['targetR']}" for row in selected],
        "projectedMarginalScoreExact": postflight_value["projectedMarginalScoreExact"],
        "manifest": display(manifest),
        "manifestSha256": manifest_sha,
        "postflight": display(postflight),
        "postflightSha256": sha256_bytes(postflight_payload),
        "submissionCalls": 0,
    }
    summary_payload = (
        json.dumps(summary_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_new(manifest, manifest_payload)
    atomic_new(postflight, postflight_payload)
    atomic_new(summary, summary_payload)
    return summary_value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--proof", type=Path, default=FROBENIUS)
    parser.add_argument("--run-certificate", type=Path, default=RUN_CERTIFICATE)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--postflight", type=Path, default=POSTFLIGHT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    args = parser.parse_args()
    print(json.dumps(stage(
        candidates_path=args.candidates,
        proof_path=args.proof,
        run_certificate_path=args.run_certificate,
        database=args.database,
        data_dir=args.data,
        receipts_dir=args.receipts,
        manifest=args.manifest,
        postflight=args.postflight,
        summary=args.summary,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

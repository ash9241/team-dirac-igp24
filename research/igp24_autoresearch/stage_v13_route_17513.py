#!/usr/bin/env python3
"""Stage the sole unresolved v13 conditional pair route after exact resolution.

The input must be the isolated output of ``pair_sum_one.sage.py`` for the
pinned accepted 24T17513/r16 source.  The adapter stages only a still-live
24T16970/r16 or r20 result.  An r8 result is sealed as an exact miss and never
written to the manifest.  No network call, ledger write, or submission occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_LINEAGE = ROOT / "data" / "v13_route_lineage_certificate.json"

SOURCE_SUBMISSION = "sub_c7f02606a17649e098b4643b58e039b2"
SOURCE_INDEX = 5
SOURCE_HASH = "4654719623963eb482222cfd4c79510eb9f300501cbb9219f7eafba121ebe484"
SOURCE_LABEL = "24T17513"
SOURCE_R = 16
TARGET_LABEL = "24T16970"
MAPPED_R = {8, 16, 20}
GOLD_R = {16, 20}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_single_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("candidate JSONL must contain exactly one object")
    return rows[0]


def validated_coefficient_line(candidate: dict) -> tuple[str, str]:
    line = str(candidate["coefficientLine"])
    try:
        values = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError("candidate coefficient payload is not integral") from exc
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] == 0
        or math.gcd(*values) != 1
    ):
        raise ValueError("candidate is not primitive monic degree 24")
    canonical = ",".join(str(value) for value in values)
    digest = sha256_bytes(canonical.encode())
    if digest != str(candidate.get("coefficientSha256")):
        raise ValueError("candidate coefficient SHA-256 mismatch")
    if len(canonical.encode()) != int(candidate.get("coefficientBytes", -1)):
        raise ValueError("candidate coefficient byte count mismatch")
    return canonical, digest


def validate_lineage(path: Path) -> dict:
    certificate = read_json(path)
    if (
        certificate.get("schemaVersion") != "v13-exact-route-lineage-certificate-v1"
        or certificate.get("status") != "three_closed_one_exact_worker_ready"
        or int((certificate.get("counts") or {}).get("conditionalRoutes", -1)) != 4
        or int((certificate.get("counts") or {}).get("unresolvedExecutableRoutes", -1)) != 1
    ):
        raise ValueError("v13 lineage certificate is missing or not worker-ready")
    rows = [
        row
        for row in certificate.get("routes") or []
        if str((row.get("source") or {}).get("label")) == SOURCE_LABEL
        and int((row.get("source") or {}).get("r", -1)) == SOURCE_R
    ]
    if len(rows) != 1:
        raise ValueError("v13 unresolved route is not unique")
    row = rows[0]
    source = row["source"]
    route = row["route"]
    if (
        str(source.get("submissionId")) != SOURCE_SUBMISSION
        or int(source.get("polynomialIndex", -1)) != SOURCE_INDEX
        or str(source.get("coefficientSha256")) != SOURCE_HASH
        or str(route.get("targetLabel")) != TARGET_LABEL
        or {int(value) for value in route.get("mappedTargetR") or []} != MAPPED_R
        or {int(value) for value in row["liveSnapshot"].get("reachableLiveGoldR") or []}
        != GOLD_R
        or row.get("outcome") != "one_exact_pair_resolvent_required"
    ):
        raise ValueError("v13 unresolved route provenance changed")
    return row


def validate_candidate(candidate: dict) -> tuple[str, str, int]:
    if (
        candidate.get("status") != "certified"
        or int(candidate.get("workerExitCode", -1)) != 0
        or str(candidate.get("sourceSubmissionId")) != SOURCE_SUBMISSION
        or int(candidate.get("sourcePolynomialIndex", -1)) != SOURCE_INDEX
        or str(candidate.get("sourceCoefficientSha256")) != SOURCE_HASH
        or str(candidate.get("sourceLabel")) != SOURCE_LABEL
        or int(candidate.get("sourceR", -1)) != SOURCE_R
        or str(candidate.get("targetLabel")) != TARGET_LABEL
    ):
        raise ValueError("isolated candidate source/target provenance mismatch")
    target_r = int(candidate.get("targetR", -1))
    if target_r not in MAPPED_R:
        raise ValueError(f"candidate target r={target_r} is outside the exact route map")
    targets = candidate.get("orbitTargets") or []
    if (
        len(targets) != 1
        or str(targets[0].get("targetLabel")) != TARGET_LABEL
        or int(targets[0].get("kernelOrder", -1)) != 1
        or int(targets[0].get("orbitSize", -1)) != 24
    ):
        raise ValueError("candidate is not the unique faithful target action")
    orbit = candidate.get("orbitCertificate") or {}
    if (
        orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or not orbit.get("exponents")
        or any(int(value) != 1 for value in orbit["exponents"])
    ):
        raise ValueError("pair-resolvent factorization is not exact/squarefree")
    if int(candidate.get("fieldDiscriminantAbs", 0)) <= 0:
        raise ValueError("candidate must be produced with --nfdisc")
    line, digest = validated_coefficient_line(candidate)
    return line, digest, target_r


def connect_immutable(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def validate_live_state(
    connection: sqlite3.Connection,
    target_r: int,
    digest: str,
    field_discriminant: str,
    allow_incomplete_target_cache: bool,
) -> dict:
    target_count, label_count = connection.execute(
        "SELECT COUNT(*),COUNT(DISTINCT label) FROM targets"
    ).fetchone()
    if not allow_incomplete_target_cache and (
        int(target_count), int(label_count)
    ) != (165_836, 25_000):
        raise ValueError("target cache is incomplete; refresh/checkpoint before staging")
    source = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
        "v.scoring_status FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
        "AND p.polynomial_index=?",
        (SOURCE_SUBMISSION, SOURCE_INDEX),
    ).fetchone()
    if source is None or (
        str(source["coefficient_hash"]),
        str(source["status"]),
        str(source["label"]),
        int(source["r"]),
        int(source["scoreable"] or 0),
        int(source["in_baseline"] or 0),
        str(source["scoring_status"]),
    ) != (SOURCE_HASH, "accepted", SOURCE_LABEL, SOURCE_R, 1, 0, "scoreable"):
        raise ValueError("pinned source is not accepted/scoreable in the ledger")
    target = connection.execute(
        "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
        (TARGET_LABEL, target_r),
    ).fetchone()
    if target is None:
        raise ValueError("resolved target pair is absent from the target cache")
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (TARGET_LABEL, target_r),
    ).fetchone()
    owned = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
        (TARGET_LABEL, target_r),
    ).fetchone()
    known_hash = connection.execute(
        "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
    ).fetchone()
    same_field = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND field_disc_abs=? "
        "AND scoreable=1 LIMIT 1",
        (TARGET_LABEL, target_r, field_discriminant),
    ).fetchone()
    state = {
        "targetRows": int(target_count),
        "targetLabels": int(label_count),
        "teamCount": int(target["team_count"]),
        "discovered": bool(target["discovered"]),
        "generatedAt": str(target["generated_at"]),
        "baseline": baseline is not None,
        "owned": owned is not None,
        "knownCoefficientHash": known_hash is not None,
        "sameTargetField": same_field is not None,
    }
    if target_r in GOLD_R:
        if (
            state["teamCount"] != 0
            or state["discovered"]
            or state["baseline"]
            or state["owned"]
            or state["knownCoefficientHash"]
            or state["sameTargetField"]
        ):
            raise ValueError("resolved gold result is no longer novel/live")
    else:
        if target_r != 8 or not state["owned"]:
            raise ValueError("exact miss does not match the pinned owned r8 branch")
    return state


def atomic_text_new(path: Path, payload: str) -> None:
    destination = path.resolve()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if destination.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(destination)


def stage(
    candidate_path: Path,
    lineage_path: Path,
    database: Path,
    manifest_path: Path,
    certificate_path: Path,
    allow_incomplete_target_cache: bool = False,
) -> dict:
    if manifest_path.exists() or manifest_path.with_suffix(manifest_path.suffix + ".tmp").exists():
        raise FileExistsError(f"refusing to overwrite output: {manifest_path}")
    if certificate_path.exists() or certificate_path.with_suffix(
        certificate_path.suffix + ".tmp"
    ).exists():
        raise FileExistsError(f"refusing to overwrite output: {certificate_path}")
    route = validate_lineage(lineage_path)
    candidate = read_single_jsonl(candidate_path)
    line, digest, target_r = validate_candidate(candidate)
    connection = connect_immutable(database)
    try:
        live_state = validate_live_state(
            connection,
            target_r,
            digest,
            str(candidate["fieldDiscriminantAbs"]),
            allow_incomplete_target_cache,
        )
    finally:
        connection.close()

    hit = target_r in GOLD_R
    manifest = None
    if hit:
        manifest_payload = line + "\n"
        atomic_text_new(manifest_path, manifest_payload)
        manifest = {
            "path": display_path(manifest_path),
            "sha256": sha256_bytes(manifest_payload.encode()),
            "polynomials": 1,
        }
    certificate = {
        "schemaVersion": "v13-route-17513-exact-stage-certificate-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "staged_exact_live_gold" if hit else "closed_exact_signature_miss",
        "source": {
            "submissionId": SOURCE_SUBMISSION,
            "polynomialIndex": SOURCE_INDEX,
            "coefficientSha256": SOURCE_HASH,
            "label": SOURCE_LABEL,
            "r": SOURCE_R,
        },
        "target": {
            "label": TARGET_LABEL,
            "r": target_r,
            "liveState": live_state,
            "isExactLiveGold": hit,
            "projectedMarginalScoreExact": "1" if hit else "0",
        },
        "exactWorker": {
            "candidateArtifact": {
                "path": display_path(candidate_path),
                "sha256": sha256_path(candidate_path),
            },
            "coefficientSha256": digest,
            "factorIndex": int(candidate["factorIndex"]),
            "orbitCertificate": candidate["orbitCertificate"],
            "resolventSha256": str(candidate["attempts"][-1]["resolventSha256"]),
        },
        "lineageCertificate": {
            "path": display_path(lineage_path),
            "sha256": sha256_path(lineage_path),
            "censusExactCertificateSha256": str(
                route["route"]["censusExactCertificateSha256"]
            ),
        },
        "manifest": manifest,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    atomic_text_new(
        certificate_path, json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    )
    return {
        "status": certificate["status"],
        "targetLabel": TARGET_LABEL,
        "targetR": target_r,
        "manifest": str(manifest_path) if manifest else None,
        "certificate": str(certificate_path),
        "projectedMarginalScoreExact": "1" if hit else "0",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--lineage", type=Path, default=DEFAULT_LINEAGE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument(
        "--allow-incomplete-target-cache",
        action="store_true",
        help="test-only escape hatch; never use in the production runbook",
    )
    args = parser.parse_args()
    result = stage(
        candidate_path=args.candidate,
        lineage_path=args.lineage,
        database=args.db,
        manifest_path=args.manifest,
        certificate_path=args.certificate,
        allow_incomplete_target_cache=args.allow_incomplete_target_cache,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

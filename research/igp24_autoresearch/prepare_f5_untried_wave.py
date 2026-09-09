#!/usr/bin/env python3
"""Preview or seal a later coefficient-free, aligned F5 frontier wave.

Preview mode is intentionally non-runnable: it writes only a coefficient-free
report.  ``--seal`` is the sole path that writes a worker plan, and it refuses
to do so while submissions are queued or any pinned boundary is stale/moving.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
PREVIEW_ROOT = DATA / "f5_wave_previews"
DB = DATA / "ledger.sqlite3"
DB_WAL = DB.with_name(DB.name + "-wal")
GOLD = DATA / "live_undiscovered_signatures.jsonl"
WORKER = ROOT / "run_f5_untried_compact_plan.sage.py"
BASE_WORKER = ROOT / "run_f5_untried_plan.sage.py"

PAIR_IN_NAME = re.compile(r"(24T\d+)_r(\d+)")
SAFE_WAVE_ID = re.compile(r"[a-z0-9][a-z0-9_]{2,127}")
RESERVED_RESULT_STATUSES = {
    "certified_live_gold",
    "certified_live_gold_even_generic_negative_quadratic_twist",
    "certified_live_gold_staged_negative_quadratic_twist",
    "certified_staged",
    "exact_frozen_gold_hit",
    "exact_live_hit",
    "hit_staged",
}
ACTION_SHARDS = {
    "data/agent_f5_full_ledger_pair_product_actions_shard0of1.jsonl":
        "7cc2b72c3be5232544dae8bff6adf1fa486a5bad77a4a27e89148888ac402ee0",
    "data/agent_f5_full_ledger_pair_product_actions_shard0of4.jsonl":
        "e45db230d4036745865cf65a8cd8721c23b2ecf022bca79d3c1e909cf150a607",
    "data/agent_f5_full_ledger_pair_product_actions_shard1of4.jsonl":
        "17c979c1e4a7b46b5cff8ac78a12afac2f4e785fd30dba80d68ffa6262e4e6fe",
    "data/agent_f5_full_ledger_pair_product_actions_shard2of4.jsonl":
        "5fa649b859c3c54526bbaa7f8af8e1dae1dfc5dd4ffff44134c5b9e9d2c1c705",
    "data/agent_f5_full_ledger_pair_product_actions_shard3of4.jsonl":
        "6ef782de7890b6a6fa8b8b0efc90c3ce666095699abe3799c015532317d711c2",
}
PRIORITY_TAIL_CANONICAL_SHA256 = (
    "f8bb54ac797aa5b23dec0576e08933c07df18243775688aa004315c9060346fc"
)
PRIORITY_TAIL_SOURCE = ("24T15093", 24)
PRIORITY_TAIL_PAIR = ("24T9502", 24)
PRIORITY_TAIL_HEIGHT_BITS = 322
EXPECTED_STALE_QUEUE_IDS = (
    "sub_0c9424918b57492ca2828eea6e0a7f69",
    "sub_24cb8b8e834b4a9db29fb440aa013807",
    "sub_3ad4ec6847c549b29d7b20be93854f8b",
    "sub_3b3ae7c6adeb40a1b6e1a46493883c95",
    "sub_415d350ba30d4ec6af8f9f5931eea7c8",
    "sub_45f261e5e4074d339c8b741f6f146f0e",
    "sub_46244e6211eb4781b9733bc1624ec16a",
    "sub_55d3dc40607f45ff843469bae7b52850",
    "sub_a70f65e3800140e58197c9fa022b5c6d",
    "sub_aa56a4c08bde43a184f7fdeb1fc6d7e5",
    "sub_b1600aae94be478aba0d6609532b577f",
    "sub_dd232377c74e41b48f390b53b641475c",
)
EXPECTED_STALE_QUEUE_BOUNDARY_SHA256 = (
    "1b6cbedcee733b9585371ac1b4cbe2a50e4e6e407cf76396e0dab58780e8567d"
)
EXPECTED_STALE_QUEUE_RAW_STATE_SHA256 = (
    "ea6dbfc6afe10bf006054cac373ceda4149fbdc329d78932763672c384104ff7"
)
EXPECTED_STALE_QUEUE_POLYNOMIAL_BOUNDARY_SHA256 = (
    "1397321041be6d9305c3f71bd56b7f89aa9725e73e8289dde38760f24c34fede"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object at {path}:{line_number}")
            yield value


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def artifact(path: Path) -> dict:
    return {"path": relative(path), "sha256": sha256_path(path)}


def payload_artifact(path: Path, value: str) -> dict:
    return {"path": relative(path), "sha256": sha256_bytes(value.encode())}


def artifact_boundary(paths: list[Path]) -> dict:
    rows = [artifact(path) for path in sorted(set(paths)) if path.is_file()]
    return {
        "files": len(rows),
        "indexSha256": sha256_bytes(
            json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        ),
    }


def stat_boundary(paths: list[Path]) -> dict:
    rows = []
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        stat = path.stat()
        rows.append(
            {
                "mtimeNs": int(stat.st_mtime_ns),
                "path": relative(path),
                "size": int(stat.st_size),
            }
        )
    return {
        "files": len(rows),
        "indexSha256": sha256_bytes(
            json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        ),
    }


def canonical_hash(quotient_line: str) -> str:
    values = quotient_line.split(",")
    reflected = ",".join(
        str(-int(value) if index % 2 else int(value))
        for index, value in enumerate(values)
    )
    return sha256_bytes(min(quotient_line, reflected).encode())


def polynomial_hash(line: str, path: Path) -> str:
    payload = line.split("#", 1)[0].strip()
    if not payload:
        raise ValueError(f"empty polynomial payload: {path}")
    values = payload.split(",")
    try:
        parsed = [int(value) for value in values]
    except ValueError as exc:
        raise ValueError(f"malformed polynomial line: {path}") from exc
    if len(parsed) != 25 or parsed[-1] != 1:
        raise ValueError(f"malformed degree-24 monic polynomial line: {path}")
    return sha256_bytes(payload.encode())


def action_core(row: dict) -> dict:
    return {
        "pairOrbit": row["pairOrbit"],
        "sourceLabel": row["sourceLabel"],
        "sourceSignatureToPossibleTargetSignatures": row[
            "sourceSignatureToPossibleTargetSignatures"
        ],
        "targetLabel": row["targetLabel"],
        "targetT": int(row["targetT"]),
    }


def action_key(row: dict) -> str:
    return sha256_bytes(
        json.dumps(action_core(row), separators=(",", ":"), sort_keys=True).encode()
    )


def result_reserved_pair(row: dict) -> tuple[str, int] | None:
    status = str(row.get("status") or "")
    if status not in RESERVED_RESULT_STATUSES and not row.get("manifest"):
        return None
    target = row.get("target") or {}
    label = target.get("label", row.get("targetLabel"))
    signature = target.get("r", row.get("targetR"))
    if isinstance(label, str) and signature is not None:
        return label, int(signature)
    return None


def source_digest_from_result(row: dict) -> tuple[str, int, str] | None:
    source = row.get("source") or {}
    digest = row.get("sourceCanonicalQuotientSha256")
    if digest is None and source.get("quotientLine"):
        digest = canonical_hash(str(source["quotientLine"]))
    label = source.get("label")
    signature = source.get("r")
    if isinstance(label, str) and signature is not None and isinstance(digest, str):
        if len(digest) != 64:
            raise ValueError("prior F5 result has malformed canonical source hash")
        return label, int(signature), digest
    return None


def frontier_identity(row: dict) -> tuple[str, int, str]:
    source = row.get("source") or {}
    return (
        str(source["label"]),
        int(source["r"]),
        str(row["canonicalQuotientSha256"]),
    )


def source_digests_from_plan(plan: dict, path: Path) -> set[tuple[str, int, str]]:
    selected = plan.get("selectedSources")
    if selected is None:
        selected = plan.get("selected") or []
    result = set()
    for row in selected:
        source = row.get("source") or {}
        signature = row.get("sourceSignature") or {}
        label = signature.get("label", source.get("label"))
        r_value = signature.get("r", source.get("r"))
        digest = row.get("canonicalQuotientSha256")
        if digest is None:
            digest = row.get("canonicalQuotientPairSha256")
        quotient = source.get("quotientLine", row.get("canonicalQuotientLine"))
        if quotient:
            calculated = canonical_hash(str(quotient))
            if digest and digest != calculated:
                raise ValueError(f"prior F5 plan canonical hash mismatch: {path}")
            digest = calculated
        if label is None or r_value is None or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"prior F5 plan lacks coefficient-free source provenance: {path}")
        result.add((str(label), int(r_value), digest))
    return result


def discover_prior_f5_paths(destination: Path) -> tuple[list[Path], list[Path]]:
    plans = set(DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_plan.json"))
    results = set(DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl"))
    for directory in DATA.glob("f5_untried*"):
        if not directory.is_dir() or directory.resolve() == destination.resolve():
            continue
        plans.update(directory.rglob("*plan.json"))
        results.update(directory.rglob("*results.jsonl"))
    for path in list(plans):
        plan = read_json(path)
        result_path = (plan.get("artifacts") or {}).get("results")
        if isinstance(result_path, str):
            resolved = (ROOT / result_path).resolve()
            resolved.relative_to(ROOT)
            if resolved.is_file() and destination.resolve() not in resolved.parents:
                results.add(resolved)
    return sorted(path for path in plans if path.is_file()), sorted(
        path for path in results if path.is_file()
    )


def collect_prior_sources(
    plan_paths: list[Path],
    result_paths: list[Path],
    retryable_failed_plans: set[Path] | None = None,
) -> tuple[set[tuple[str, int, str]], set[str], int]:
    tried = set()
    retryable = {path.resolve() for path in (retryable_failed_plans or set())}
    for path in plan_paths:
        if path.resolve() in retryable:
            continue
        tried.update(source_digests_from_plan(read_json(path), path))
    ledger_known_candidate_sources = set()
    result_rows = 0
    for path in result_paths:
        for row in iter_jsonl(path):
            result_rows += 1
            source = source_digest_from_result(row)
            if source is not None:
                tried.add(source)
                candidate = row.get("candidate") or {}
                candidate_hash = candidate.get("coefficientSha256")
                if isinstance(candidate_hash, str) and len(candidate_hash) == 64:
                    ledger_known_candidate_sources.add(source[2])
    return tried, ledger_known_candidate_sources, result_rows


def walk_candidate_targets(value: object, result: dict[str, set[tuple[str, int]]]) -> None:
    if isinstance(value, dict):
        target_value = value.get("target")
        target = target_value if isinstance(target_value, dict) else {}
        label = target.get("label", value.get("targetLabel"))
        signature = target.get("r", value.get("targetR"))
        candidate_value = value.get("candidate")
        candidate = candidate_value if isinstance(candidate_value, dict) else {}
        digest = candidate.get("coefficientSha256", value.get("candidateSha256"))
        if (
            isinstance(digest, str)
            and len(digest) == 64
            and isinstance(label, str)
            and signature is not None
        ):
            result[digest].add((label, int(signature)))
        for item in value.values():
            walk_candidate_targets(item, result)
    elif isinstance(value, list):
        for item in value:
            walk_candidate_targets(item, result)


def root_claim_pair(value: object) -> tuple[str, int] | None:
    if not isinstance(value, dict):
        return None
    label = value.get("targetLabel", value.get("label"))
    signature = value.get("targetR", value.get("r"))
    if isinstance(label, str) and signature is not None:
        return label, int(signature)
    return None


def claim_paths(destination: Path) -> list[Path]:
    return sorted(
        path
        for path in DATA.rglob("*.json")
        if path.is_file()
        and destination.resolve() not in path.resolve().parents
        and (
            "claim" in path.name.lower()
            or any("claim" in parent.name.lower() for parent in path.parents if parent != ROOT)
        )
    )


def collect_reservations(
    connection: sqlite3.Connection,
    destination: Path,
) -> tuple[
    set[tuple[str, int]],
    set[tuple[str, int]],
    set[str],
    list[dict],
    list[dict],
    list[dict],
    dict,
]:
    pairs = set()
    candidate_targets: dict[str, set[tuple[str, int]]] = defaultdict(set)
    claims = claim_paths(destination)
    claim_artifacts = [artifact(path) for path in claims]
    for path in claims:
        value = json.loads(path.read_text(encoding="utf-8"))
        walk_candidate_targets(value, candidate_targets)
        pair = root_claim_pair(value)
        if pair is not None:
            pairs.add(pair)

    # Results can reserve a pair even when their containing path is not named
    # like a claim.  Stream the corpus so the 200MB result archive stays light.
    jsonl_paths = sorted(path for path in DATA.rglob("*.jsonl") if path.is_file())
    for path in jsonl_paths:
        if destination.resolve() in path.resolve().parents:
            continue
        for row in iter_jsonl(path):
            pair = result_reserved_pair(row)
            if pair is not None:
                pairs.add(pair)
            walk_candidate_targets(row, candidate_targets)

    outbox_paths = sorted(path for path in OUTBOX.glob("*.txt") if path.is_file())
    outbox_artifacts = [artifact(path) for path in outbox_paths]
    reserved_hashes = set()
    for path in outbox_paths:
        match = PAIR_IN_NAME.search(path.name)
        if match is not None:
            pairs.add((match.group(1), int(match.group(2))))
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.split("#", 1)[0].strip():
                reserved_hashes.add(polynomial_hash(line, path))

    receipt_paths = sorted(path for path in RECEIPTS.glob("sub_*.json") if path.is_file())
    receipt_artifacts = [artifact(path) for path in receipt_paths]
    missing_completed_manifests = 0
    active_receipt_manifest_errors = []
    queued_receipt_submissions = []
    for path in receipt_paths:
        receipt = read_json(path)
        response = receipt.get("response") or {}
        submission_id = str(response.get("submissionId") or path.stem)
        state = connection.execute(
            "SELECT queued_count FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        queued = int(state[0]) if state is not None else int(response.get("queuedCount") or 0)
        if queued:
            queued_receipt_submissions.append(
                {"submissionId": submission_id, "queuedCount": queued}
            )
        manifest_value = receipt.get("manifest")
        manifest = (
            Path(manifest_value).expanduser().resolve()
            if isinstance(manifest_value, str)
            else None
        )
        intact = bool(
            manifest is not None
            and manifest.is_file()
            and sha256_path(manifest) == str(receipt.get("manifestHash"))
        )
        if not intact:
            if queued:
                active_receipt_manifest_errors.append(path.name)
            else:
                missing_completed_manifests += 1
            continue
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                reserved_hashes.add(polynomial_hash(line, manifest))

    selection_reserved_pairs = set(pairs)
    for digest in reserved_hashes:
        selection_reserved_pairs.update(candidate_targets.get(digest, set()))
    if reserved_hashes:
        placeholders = ",".join("?" for _ in reserved_hashes)
        for row in connection.execute(
            "SELECT DISTINCT p.coefficient_hash,v.label,v.r FROM polynomials p "
            "JOIN verifications v USING(submission_id,polynomial_index) "
            f"WHERE p.coefficient_hash IN ({placeholders})",
            tuple(sorted(reserved_hashes)),
        ):
            selection_reserved_pairs.add((str(row[1]), int(row[2])))
    health = {
        "activeReceiptManifestErrors": active_receipt_manifest_errors,
        "completedReceiptsWithoutIntactManifest": missing_completed_manifests,
        "queuedReceiptSubmissions": sorted(
            queued_receipt_submissions, key=lambda row: row["submissionId"]
        ),
        "reservedCoefficientHashes": len(reserved_hashes),
        "reservationArtifactsStableDuringScan": (
            claim_artifacts == [artifact(path) for path in claim_paths(destination)]
            and outbox_artifacts
            == [
                artifact(path)
                for path in sorted(path for path in OUTBOX.glob("*.txt") if path.is_file())
            ]
            and receipt_artifacts
            == [
                artifact(path)
                for path in sorted(
                    path for path in RECEIPTS.glob("sub_*.json") if path.is_file()
                )
            ]
        ),
    }
    return (
        pairs,
        selection_reserved_pairs,
        reserved_hashes,
        claim_artifacts,
        outbox_artifacts,
        receipt_artifacts,
        health,
    )


def target_boundary(connection: sqlite3.Connection) -> dict:
    digest = hashlib.sha256()
    rows = 0
    current_zero = 0
    generated_values = set()
    for row in connection.execute(
        "SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at "
        "FROM targets ORDER BY label,r"
    ):
        values = [row[index] for index in range(7)]
        digest.update(
            json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        )
        rows += 1
        if int(row[3]) == 0 and int(row[5]) == 0:
            current_zero += 1
        if row[6] is not None:
            generated_values.add(str(row[6]))
    return {
        "rows": rows,
        "currentZeroUndiscoveredRows": current_zero,
        "indexSha256": digest.hexdigest(),
        "generatedAtMax": max(generated_values) if generated_values else None,
        "generatedAtMin": min(generated_values) if generated_values else None,
        "generatedAtDistinct": len(generated_values),
    }


def queued_boundary(connection: sqlite3.Connection, queued_rows: list[dict]) -> dict:
    rows = []
    coefficient_rows = 0
    distinct_hashes = set()
    synced_values = []
    raw_state_rows = []
    polynomial_boundary_rows = []
    for queued in sorted(queued_rows, key=lambda row: row["submissionId"]):
        submission_id = queued["submissionId"]
        state = connection.execute(
            "SELECT raw_json,synced_at FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        polynomial_values = [
            (int(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT polynomial_index,coefficients,coefficient_hash "
                "FROM polynomials WHERE submission_id=? "
                "ORDER BY polynomial_index",
                (submission_id,),
            )
        ]
        hashes = [row[2] for row in polynomial_values]
        coefficient_rows += len(hashes)
        distinct_hashes.update(hashes)
        if state is not None:
            raw_state_rows.append(
                {
                    "submissionId": submission_id,
                    "rawJsonSha256": sha256_bytes(str(state[0]).encode()),
                }
            )
            synced_values.append(float(state[1]))
        polynomial_boundary_rows.extend(
            {
                "submissionId": submission_id,
                "polynomialIndex": index,
                "storedCoefficientSha256": digest,
                "recomputedCoefficientSha256": sha256_bytes(coefficients.encode()),
            }
            for index, coefficients, digest in polynomial_values
        )
        rows.append(
            {
                "submissionId": submission_id,
                "queuedCount": int(queued["queuedCount"]),
                "verifiedCount": int(queued.get("verifiedCount") or 0),
                "failedCount": int(queued.get("failedCount") or 0),
                "updatedAt": queued.get("updatedAt"),
                "polynomialRows": len(hashes),
                "coefficientHashesSha256": sha256_bytes(
                    json.dumps(hashes, separators=(",", ":")).encode()
                ),
            }
        )
    return {
        "submissions": len(rows),
        "queuedPolynomials": sum(int(row["queuedCount"]) for row in queued_rows),
        "ledgerPolynomialRows": coefficient_rows,
        "distinctLedgerCoefficientHashes": len(distinct_hashes),
        "updatedAtMin": min(
            (str(row["updatedAt"]) for row in rows if row["updatedAt"] is not None),
            default=None,
        ),
        "updatedAtMax": max(
            (str(row["updatedAt"]) for row in rows if row["updatedAt"] is not None),
            default=None,
        ),
        "indexSha256": sha256_bytes(
            json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        ),
        "rawStateBoundarySha256": sha256_bytes(
            json.dumps(
                raw_state_rows, separators=(",", ":"), sort_keys=True
            ).encode()
        ),
        "polynomialRowHashBoundarySha256": sha256_bytes(
            json.dumps(
                polynomial_boundary_rows, separators=(",", ":"), sort_keys=True
            ).encode()
        ),
        "syncedAtUnixMin": min(synced_values, default=None),
        "syncedAtUnixMax": max(synced_values, default=None),
        "rowsMaterializedInReport": False,
    }


def certify_stale_queue_exception(
    connection: sqlite3.Connection,
    queued_rows: list[dict],
    receipt_submission_ids: set[str],
    selected: list[dict],
    now: datetime | None = None,
) -> dict:
    certified_at = now or datetime.now(timezone.utc)
    if certified_at.tzinfo is None:
        raise ValueError("stale-queue certification time must be timezone-aware")
    minimum_updated = certified_at - timedelta(days=14)
    exact_polynomial_rows = True
    all_hashes_ledger_reserved = True
    no_verification_rows = True
    no_failure_rows = True
    all_ids_in_database = True
    all_old_enough = True
    recently_synced = True
    raw_queue_shapes_exact = True
    stored_indices_exact = True
    stored_hashes_recompute = True
    queue_hashes = set()
    for queued in queued_rows:
        submission_id = queued["submissionId"]
        state = connection.execute(
            "SELECT queued_count,verified_count,failed_count,updated_at,raw_json,synced_at "
            "FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        if state is None:
            all_ids_in_database = False
            continue
        polynomial_rows = [
            (int(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT polynomial_index,coefficients,coefficient_hash "
                "FROM polynomials WHERE submission_id=? "
                "ORDER BY polynomial_index",
                (submission_id,),
            )
        ]
        hashes = [row[2] for row in polynomial_rows]
        queue_hashes.update(hashes)
        exact_polynomial_rows &= len(hashes) == int(state[0]) == int(
            queued["queuedCount"]
        )
        all_hashes_ledger_reserved &= all(
            len(digest) == 64
            and int(
                connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                    (digest,),
                ).fetchone()[0]
            )
            > 0
            for digest in hashes
        )
        no_verification_rows &= int(
            connection.execute(
                "SELECT COUNT(*) FROM verifications WHERE submission_id=?",
                (submission_id,),
            ).fetchone()[0]
        ) == 0
        no_failure_rows &= int(
            connection.execute(
                "SELECT COUNT(*) FROM failures WHERE submission_id=?",
                (submission_id,),
            ).fetchone()[0]
        ) == 0
        stored_indices_exact &= [row[0] for row in polynomial_rows] == list(range(25))
        stored_hashes_recompute &= all(
            sha256_bytes(coefficients.encode()) == digest
            for _, coefficients, digest in polynomial_rows
        )
        raw = json.loads(str(state[4]))
        raw_payload = raw.get("payload") if isinstance(raw, dict) else None
        raw_queued = (
            raw_payload.get("queuedPolynomials")
            if isinstance(raw_payload, dict)
            else None
        )
        raw_queue_shapes_exact &= bool(
            isinstance(raw_queued, list)
            and [int(row.get("polynomialIndex", -1)) for row in raw_queued]
            == list(range(25))
            and all(str(row.get("status")) == "queued" for row in raw_queued)
            and raw.get("submissionId") == submission_id
            and raw.get("updatedAt") == state[3]
            and raw.get("verifiedPolynomials") == []
            and raw.get("failedPolynomials") == []
        )
        synced_at = datetime.fromtimestamp(float(state[5]), timezone.utc)
        sync_age = certified_at - synced_at
        recently_synced &= timedelta(0) <= sync_age <= timedelta(hours=24)
        updated_value = state[3]
        if updated_value is None:
            all_old_enough = False
        else:
            updated = datetime.fromisoformat(str(updated_value).replace("Z", "+00:00"))
            all_old_enough &= updated <= minimum_updated
    selected_source_hashes = {
        str(row["source"]["coefficientSha256"]) for row in selected
    }
    checks = {
        "hasQueuedRows": bool(queued_rows),
        "exactPinnedSubmissionIds": tuple(
            sorted(row["submissionId"] for row in queued_rows)
        )
        == EXPECTED_STALE_QUEUE_IDS,
        "exactQueueIdsPresentInDatabase": all_ids_in_database,
        "minimumAgeFourteenDays": all_old_enough,
        "noLocalReceipts": not (
            {row["submissionId"] for row in queued_rows} & receipt_submission_ids
        ),
        "noReceiptOnlyOrNewQueue": all_ids_in_database
        and all(row.get("updatedAt") is not None for row in queued_rows),
        "noPartialTerminalCounts": all(
            int(row.get("queuedCount") or 0) == 25
            and int(row.get("verifiedCount") or 0) == 0
            and int(row.get("failedCount") or 0) == 0
            for row in queued_rows
        ),
        "exactStoredQueuedPolynomialRows": exact_polynomial_rows,
        "storedPolynomialIndicesExactlyZeroThroughTwentyFour": stored_indices_exact,
        "storedCoefficientHashesRecompute": stored_hashes_recompute,
        "rawQueuedCountersAndIndicesExact": raw_queue_shapes_exact,
        "allQueuedHashesLedgerReserved": all_hashes_ledger_reserved,
        "exactAggregateRowsAndDistinctHashes": len(queue_hashes) == 296
        and sum(int(row["queuedCount"]) for row in queued_rows) == 300,
        "noVerificationRows": no_verification_rows,
        "noFailureRows": no_failure_rows,
        "selectedSourceHashesAbsentFromQueue": not (
            selected_source_hashes & queue_hashes
        ),
        "authoritativeSyncWithinTwentyFourHours": recently_synced,
        "runtimeCandidateHashesMustBeAbsentFromLedger": True,
        "mandatoryFinalLivePreSubmitRefresh": True,
    }
    boundary = queued_boundary(connection, queued_rows)
    checks["exactPinnedQueueBoundary"] = (
        boundary["indexSha256"] == EXPECTED_STALE_QUEUE_BOUNDARY_SHA256
    )
    checks["exactPinnedRawStateBoundary"] = (
        boundary["rawStateBoundarySha256"]
        == EXPECTED_STALE_QUEUE_RAW_STATE_SHA256
    )
    checks["exactPinnedPolynomialRowHashBoundary"] = (
        boundary["polynomialRowHashBoundarySha256"]
        == EXPECTED_STALE_QUEUE_POLYNOMIAL_BOUNDARY_SHA256
    )
    return {
        "schemaVersion": "f5-pinned-stale-queue-exception-v1",
        "enabled": True,
        "certified": all(checks.values()),
        "certifiedAt": certified_at.isoformat(),
        "minimumAgeDays": 14,
        "checks": checks,
        "queueBoundary": boundary,
        "submissionIdsSha256": sha256_bytes(
            json.dumps(
                sorted(row["submissionId"] for row in queued_rows),
                separators=(",", ":"),
            ).encode()
        ),
        "mandatoryPreSubmitGate": (
            "fresh live targets plus nonbaseline/unowned/known-hash recheck"
        ),
    }


def known_data_paths(destination: Path) -> list[Path]:
    return [
        path
        for suffix in ("*.json", "*.jsonl")
        for path in DATA.rglob(suffix)
        if path.is_file() and destination.resolve() not in path.resolve().parents
    ]


def mutable_boundary(
    destination: Path,
    manifest: Path,
    ignored_data_paths: set[Path] | None = None,
) -> dict:
    ignored = {path.resolve() for path in (ignored_data_paths or set())}
    known = [
        path for path in known_data_paths(destination) if path.resolve() not in ignored
    ]
    return {
        "knownDataFiles": {
            "content": artifact_boundary(known),
            "stat": stat_boundary(known),
        },
        "outboxes": artifact_boundary(
            [
                path
                for path in OUTBOX.glob("*.txt")
                if path.is_file() and path.resolve() != manifest.resolve()
            ]
        ),
        "receipts": artifact_boundary(
            [path for path in RECEIPTS.glob("sub_*.json") if path.is_file()]
        ),
    }


def select_aligned(
    candidates: dict[tuple[str, int, str], dict],
    maximum: int,
    height_cap_bits: int,
    core_height_cap_bits: int,
    priority_tail_sources: int,
) -> tuple[list[dict], set[tuple[str, int]]]:
    within_pilot_cap = {
        key: dict(row)
        for key, row in candidates.items()
        if int(row["coefficientHeightBits"]) <= height_cap_bits
        and any(int(pair["r"]) == key[1] for pair in row["possibleGoldPairs"])
    }
    selected = []
    covered = set()
    covered_aligned = set()
    selected_signatures = set()
    selected_hashes = set()

    def add_selected(key: tuple[str, int, str], row: dict, tier: str) -> bool:
        pairs = {
            (pair["label"], int(pair["r"])) for pair in row["possibleGoldPairs"]
        }
        aligned_pairs = {pair for pair in pairs if pair[1] == key[1]}
        if len(aligned_pairs) != 1:
            raise ValueError("aligned F5 source does not map to one exact aligned pair")
        if (
            key[:2] in selected_signatures
            or aligned_pairs & covered_aligned
            or row["canonicalQuotientSha256"] in selected_hashes
        ):
            return False
        row.pop("_rank", None)
        row["signatureAligned"] = True
        row["selectionTier"] = tier
        row["selectionRank"] = len(selected) + 1
        row["newGoldPairsAtSelection"] = len(pairs - covered)
        selected.append(row)
        covered.update(pairs)
        covered_aligned.update(aligned_pairs)
        selected_signatures.add(key[:2])
        selected_hashes.add(row["canonicalQuotientSha256"])
        return True

    priority_remaining = {
        key: row
        for key, row in within_pilot_cap.items()
        if core_height_cap_bits < int(row["coefficientHeightBits"]) <= height_cap_bits
        and row["canonicalQuotientSha256"] == PRIORITY_TAIL_CANONICAL_SHA256
        and key[:2] == PRIORITY_TAIL_SOURCE
        and int(row["coefficientHeightBits"]) == PRIORITY_TAIL_HEIGHT_BITS
        and any(
            (pair["label"], int(pair["r"])) == PRIORITY_TAIL_PAIR
            for pair in row["possibleGoldPairs"]
        )
    }
    for key, row in sorted(
        priority_remaining.items(),
        key=lambda item: (int(item[1]["coefficientHeightBits"]), item[0]),
    )[:priority_tail_sources]:
        add_selected(key, row, "priority_tail")

    core_candidates = {
        key: row
        for key, row in within_pilot_cap.items()
        if int(row["coefficientHeightBits"]) <= core_height_cap_bits
    }
    best_by_aligned_pair: dict[tuple[str, int], tuple[tuple, tuple, dict]] = {}
    for key, row in core_candidates.items():
        aligned_pairs = {
            (pair["label"], int(pair["r"]))
            for pair in row["possibleGoldPairs"]
            if int(pair["r"]) == key[1]
        }
        if len(aligned_pairs) != 1:
            raise ValueError("core F5 source does not map to one exact aligned pair")
        aligned_pair = next(iter(aligned_pairs))
        representative_rank = (
            int(row["coefficientHeightBits"]),
            row["canonicalQuotientSha256"],
            key[0],
            key[1],
        )
        incumbent = best_by_aligned_pair.get(aligned_pair)
        if incumbent is None or representative_rank < incumbent[0]:
            best_by_aligned_pair[aligned_pair] = (representative_rank, key, row)
    ordered_core = sorted(
        (
            (
                int(row["coefficientHeightBits"]),
                aligned_pair[0],
                aligned_pair[1],
                key[0],
                key[1],
                row["canonicalQuotientSha256"],
            ),
            key,
            row,
        )
        for aligned_pair, (_, key, row) in best_by_aligned_pair.items()
    )
    for _, key, row in ordered_core:
        if len(selected) >= maximum:
            break
        add_selected(key, row, "core")
    return selected, covered


def audit_selected_frontier(
    candidates: dict[tuple[str, int, str], dict],
    selected: list[dict],
    tried_hashes: set[str],
    selection_reserved_pairs: set[tuple[str, int]],
) -> dict:
    """Prove the prospective sealed subset without materializing its rows.

    Preview reports intentionally omit source identities.  These checks mirror
    the base worker's exact canonical-JSON subset gate and the preparer's seal
    invariants so a blocked preview can still be audited before publication.
    """
    selected_by_identity = {frontier_identity(row): row for row in selected}
    prospective_frontier = [
        selected_by_identity.get(frontier_identity(row), row)
        for row in candidates.values()
    ]
    selected_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True) for row in selected
    }
    frontier_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True)
        for row in prospective_frontier
    }
    original_identities = {frontier_identity(row) for row in candidates.values()}
    prospective_identities = {
        frontier_identity(row) for row in prospective_frontier
    }
    annotation_keys = {
        "newGoldPairsAtSelection",
        "selectionRank",
        "selectionTier",
        "signatureAligned",
    }
    canonical_hashes = {
        str(row["canonicalQuotientSha256"]) for row in selected
    }
    source_signatures = {
        (str(row["source"]["label"]), int(row["source"]["r"]))
        for row in selected
    }
    aligned_pairs = {
        (str(pair["label"]), int(pair["r"]))
        for row in selected
        for pair in row["possibleGoldPairs"]
        if int(pair["r"]) == int(row["source"]["r"])
    }
    covered_pairs = {
        (str(pair["label"]), int(pair["r"]))
        for row in selected
        for pair in row["possibleGoldPairs"]
    }
    checks = {
        "selectedRowsExactProspectiveFrontierSubset": (
            len(selected_exact) == len(selected) and selected_exact <= frontier_exact
        ),
        "frontierIdentitiesPreservedExactly": (
            len(original_identities)
            == len(candidates)
            == len(prospective_identities)
            == len(prospective_frontier)
            and original_identities == prospective_identities
        ),
        "exactlySelectedRowsAnnotated": sum(
            annotation_keys <= set(row) for row in prospective_frontier
        )
        == len(selected),
        "canonicalSourcesUnique": len(canonical_hashes) == len(selected),
        "sourceSignaturesUnique": len(source_signatures) == len(selected),
        "alignedTargetPairsUnique": len(aligned_pairs) == len(selected),
        "canonicalSourcesUntried": not (canonical_hashes & tried_hashes),
        "coveredPairsUnreserved": not (
            covered_pairs & selection_reserved_pairs
        ),
    }
    return {
        "checks": checks,
        "passed": bool(selected) and all(checks.values()),
        "rowsMaterializedInReport": False,
    }


def resolve_under(path: Path, parent: Path, kind: str) -> Path:
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if resolved == parent.resolve() or parent.resolve() not in resolved.parents:
        raise ValueError(f"{kind} must be below {parent}")
    return resolved


def validate_report_ownership(report_path: Path, wave_id: str) -> None:
    if not report_path.exists():
        return
    existing_report = read_json(report_path)
    if (
        existing_report.get("schemaVersion")
        != "f5-untried-wave-preparation-report-v1"
        or existing_report.get("waveId") != wave_id
    ):
        raise ValueError("refusing to overwrite a report not owned by this wave")


def validate_retryable_failed_plan(path: Path, expected_sha256: str) -> dict:
    resolved = path.resolve()
    if DATA.resolve() not in resolved.parents or not resolved.is_file():
        raise ValueError("retryable failed plan must be an existing data artifact")
    if len(expected_sha256) != 64 or sha256_path(resolved) != expected_sha256:
        raise ValueError("retryable failed plan differs from its explicit SHA pin")
    plan = read_json(resolved)
    execution = plan.get("execution") or {}
    artifacts = plan.get("artifacts") or {}
    if (
        plan.get("schemaVersion") != "f5-untried-frontier-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or execution.get("heavyWorkerLaunched") is not False
        or execution.get("submissionAuthorized") is not False
    ):
        raise ValueError("retryable failed plan is not an unlaunched sealed handoff")
    for name in ("results", "summary", "manifest"):
        value = artifacts.get(name)
        if not isinstance(value, str) or (ROOT / value).exists():
            raise ValueError(f"retryable failed plan has worker state: {name}")
    claims_value = artifacts.get("claimsDirectory")
    if not isinstance(claims_value, str):
        raise ValueError("retryable failed plan lacks claims-directory provenance")
    claims = ROOT / claims_value
    if claims.exists() and any(item.is_file() for item in claims.rglob("*")):
        raise ValueError("retryable failed plan has claim state")
    return {"path": relative(resolved), "sha256": expected_sha256}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-sources", type=int, default=50)
    parser.add_argument("--height-cap-bits", type=int, default=256)
    parser.add_argument("--core-height-cap-bits", type=int, default=256)
    parser.add_argument("--priority-tail-sources", type=int, default=0)
    parser.add_argument("--allow-pinned-stale-queue", action="store_true")
    parser.add_argument("--retry-failed-plan", type=Path, action="append", default=[])
    parser.add_argument(
        "--expected-retry-failed-plan-sha256", action="append", default=[]
    )
    parser.add_argument("--seal", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if not SAFE_WAVE_ID.fullmatch(args.wave_id):
        raise ValueError("wave-id must be lowercase alphanumeric/underscore")
    if not 1 <= args.max_sources <= 50:
        raise ValueError("max-sources must be between 1 and 50")
    if args.height_cap_bits < 1:
        raise ValueError("height-cap-bits must be positive")
    if not 1 <= args.core_height_cap_bits <= args.height_cap_bits:
        raise ValueError("core-height-cap-bits must be within the pilot cap")
    if args.priority_tail_sources not in (0, 1) or args.priority_tail_sources > args.max_sources:
        raise ValueError("priority-tail-sources must be zero or one")
    destination = resolve_under(args.destination, DATA, "destination")
    manifest = resolve_under(args.manifest, OUTBOX, "manifest")
    report_path = resolve_under(args.report, PREVIEW_ROOT, "report")
    if manifest.suffix != ".txt" or report_path.suffix != ".json":
        raise ValueError("manifest/report suffix mismatch")
    validate_report_ownership(report_path, args.wave_id)
    if len(args.retry_failed_plan) != len(args.expected_retry_failed_plan_sha256):
        raise ValueError("each retry-failed-plan needs one expected SHA")
    retry_failed_artifacts = []
    retry_failed_paths: set[Path] = set()
    for retry_argument, retry_sha256 in zip(
        args.retry_failed_plan, args.expected_retry_failed_plan_sha256
    ):
        retry_path = (
            (ROOT / retry_argument).resolve()
            if not retry_argument.is_absolute()
            else retry_argument.resolve()
        )
        retry_failed_artifacts.append(
            validate_retryable_failed_plan(retry_path, str(retry_sha256))
        )
        retry_failed_paths.add(retry_path)

    index = destination / "frontier.jsonl"
    claimed_index = destination / "claimed_pairs_at_seal.json"
    plan_path = destination / "plan.json"
    preflight_path = destination / "preflight.json"
    results = destination / "results.jsonl"
    summary = destination / "summary.json"
    claims = destination / "claims"
    sealed_outputs = (index, claimed_index, plan_path, preflight_path, results, summary)
    if args.seal and (
        destination.exists()
        or any(path.exists() for path in sealed_outputs)
        or manifest.exists()
        or (claims.is_dir() and any(path.is_file() for path in claims.rglob("*")))
    ):
        raise ValueError("refusing to overwrite existing wave state")

    initial_core = {
        "database": artifact(DB),
        "databaseWal": artifact(DB_WAL) if DB_WAL.is_file() else None,
        "frozenGold": artifact(GOLD),
        "worker": artifact(WORKER),
        "baseWorker": artifact(BASE_WORKER),
    }
    census_volatile = mutable_boundary(
        destination, manifest, ignored_data_paths={report_path}
    )
    action_artifacts = []
    for relative_path, digest in ACTION_SHARDS.items():
        path = ROOT / relative_path
        if not path.is_file() or sha256_path(path) != digest:
            raise ValueError(f"F5 action shard changed: {relative_path}")
        action_artifacts.append({"path": relative_path, "sha256": digest})

    actions = {}
    for relative_path in ACTION_SHARDS:
        for row in iter_jsonl(ROOT / relative_path):
            digest = action_key(row)
            incumbent = actions.get(digest)
            if incumbent is not None and action_core(incumbent) != action_core(row):
                raise ValueError("conflicting deduped F5 action")
            actions[digest] = row
    if len(actions) != 3771:
        raise ValueError("deduped F5 action count changed")
    by_label = defaultdict(list)
    for digest, row in actions.items():
        by_label[str(row["sourceLabel"])].append((digest, row))
    unique_actions = {label: rows[0] for label, rows in by_label.items() if len(rows) == 1}

    plan_paths, result_paths = discover_prior_f5_paths(destination)
    prior_plans = [artifact(path) for path in plan_paths]
    prior_results = [artifact(path) for path in result_paths]
    tried, known_candidate_sources, prior_result_rows = collect_prior_sources(
        plan_paths, result_paths, retryable_failed_plans=retry_failed_paths
    )
    tried_hashes = {row[2] for row in tried}

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    target_snapshot = target_boundary(connection)
    db_queued_rows = [
        {
            "submissionId": str(row[0]),
            "queuedCount": int(row[1]),
            "verifiedCount": int(row[2]),
            "failedCount": int(row[3]),
            "updatedAt": row[4],
        }
        for row in connection.execute(
            "SELECT submission_id,queued_count,verified_count,failed_count,updated_at "
            "FROM submissions "
            "WHERE queued_count>0 ORDER BY submission_id"
        )
    ]
    latest_submission_updated = connection.execute(
        "SELECT MAX(updated_at) FROM submissions"
    ).fetchone()[0]
    worker_claimed_pairs, selection_reserved_pairs, reserved_hashes, claim_artifacts, outbox_artifacts, receipt_artifacts, receipt_health = collect_reservations(
        connection, destination
    )
    # Ancient receipt-less queue rows remain a default seal blocker.  The sole
    # opt-in exception below is a compute-only certificate over one exact pinned
    # cohort; it never authorizes submission.
    target_generation_floor = target_snapshot["generatedAtMin"]
    legacy_db_queued = [
        row
        for row in db_queued_rows
        if target_generation_floor is not None
        and row["updatedAt"] is not None
        and str(row["updatedAt"]) < str(target_generation_floor)
    ]
    queued_by_id = {row["submissionId"]: row for row in db_queued_rows}
    for row in receipt_health["queuedReceiptSubmissions"]:
        queued_by_id.setdefault(
            row["submissionId"],
            {
                "submissionId": row["submissionId"],
                "queuedCount": int(row["queuedCount"]),
                "verifiedCount": 0,
                "failedCount": 0,
                "updatedAt": None,
            },
        )
    queued_rows = [queued_by_id[key] for key in sorted(queued_by_id)]
    queue_snapshot = queued_boundary(connection, queued_rows)
    frozen_rows = list(iter_jsonl(GOLD))
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    frozen_target_values = {
        str(row["targetGeneratedAt"])
        for row in frozen_rows
        if row.get("targetGeneratedAt") is not None
    }
    owned_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    baseline_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    current_zero_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT label,r FROM targets WHERE team_count=0 AND discovered=0"
        )
    }
    gold = {
        pair: row
        for pair, row in frozen.items()
        if pair in current_zero_pairs
        and pair not in owned_pairs
        and pair not in baseline_pairs
        and pair not in selection_reserved_pairs
    }

    candidates: dict[tuple[str, int, str], dict] = {}
    accepted_rows = 0
    even_rows = 0
    eligible_even_rows = 0
    for row in connection.execute(
        "SELECT v.label,v.t,v.r,p.coefficients,p.coefficient_hash,p.submission_id,"
        "p.polynomial_index FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE v.status='accepted' AND v.scoreable=1"
    ):
        accepted_rows += 1
        coefficients = str(row[3])
        values = coefficients.split(",")
        if (
            len(values) != 25
            or values[-1] != "1"
            or any(int(values[index]) for index in range(1, 25, 2))
        ):
            continue
        even_rows += 1
        label = str(row[0])
        action_item = unique_actions.get(label)
        if action_item is None:
            continue
        eligible_even_rows += 1
        action_digest, action = action_item
        r_value = int(row[2])
        target_pairs = {
            (str(action["targetLabel"]), int(target_r))
            for target_r in action["sourceSignatureToPossibleTargetSignatures"].get(
                str(r_value), []
            )
            if (str(action["targetLabel"]), int(target_r)) in gold
        }
        if not target_pairs:
            continue
        quotient_line = ",".join(values[::2])
        canonical = canonical_hash(quotient_line)
        if canonical in tried_hashes:
            continue
        key = (label, r_value, canonical)
        height_bits = max(abs(int(value)) for value in values[::2]).bit_length()
        proposed = {
            "actionSha256": action_digest,
            "canonicalQuotientSha256": canonical,
            "coefficientHeightBits": height_bits,
            "possibleGoldPairs": [
                {"label": pair[0], "r": pair[1]} for pair in sorted(target_pairs)
            ],
            "source": {
                "coefficientSha256": str(row[4]),
                "label": label,
                "polynomialIndex": int(row[6]),
                "r": r_value,
                "submissionId": str(row[5]),
                "t": int(row[1]),
            },
        }
        rank = (height_bits, len(coefficients.encode()), str(row[4]))
        incumbent = candidates.get(key)
        if incumbent is None or rank < incumbent["_rank"]:
            proposed["_rank"] = rank
            candidates[key] = proposed
    distinct_known_hashes = int(
        connection.execute(
            "SELECT COUNT(DISTINCT coefficient_hash) FROM polynomials"
        ).fetchone()[0]
    )

    selected, covered = select_aligned(
        candidates,
        args.max_sources,
        args.height_cap_bits,
        args.core_height_cap_bits,
        args.priority_tail_sources,
    )
    for row in candidates.values():
        row.pop("_rank", None)
    selected_heights = sorted(int(row["coefficientHeightBits"]) for row in selected)
    selected_priority = [row for row in selected if row.get("selectionTier") == "priority_tail"]
    selected_core = [row for row in selected if row.get("selectionTier") == "core"]
    receipt_submission_ids = {
        Path(row["path"]).stem for row in receipt_artifacts
    }
    stale_queue_exception = certify_stale_queue_exception(
        connection,
        queued_rows,
        receipt_submission_ids,
        selected,
    )
    stale_queue_exception["enabled"] = bool(args.allow_pinned_stale_queue)
    connection.close()
    selected_digest = sha256_bytes(
        json.dumps(selected, separators=(",", ":"), sort_keys=True).encode()
    )
    selection_audit = audit_selected_frontier(
        candidates, selected, tried_hashes, selection_reserved_pairs
    )
    worker_claimed_pair_boundary_sha256 = sha256_bytes(
        json.dumps(
            sorted([label, signature] for label, signature in worker_claimed_pairs),
            separators=(",", ":"),
        ).encode()
    )

    blockers = []
    if queued_rows and not args.allow_pinned_stale_queue:
        blockers.append(
            {
                "code": "queued_submissions",
                "count": len(queued_rows),
                "submissionIds": [row["submissionId"] for row in queued_rows],
            }
        )
    elif queued_rows and not stale_queue_exception["certified"]:
        blockers.append(
            {
                "code": "pinned_stale_queue_exception_failed",
                "failedChecks": sorted(
                    key
                    for key, value in stale_queue_exception["checks"].items()
                    if not value
                ),
            }
        )
    if receipt_health["activeReceiptManifestErrors"]:
        blockers.append(
            {
                "code": "active_receipt_manifest_not_intact",
                "receipts": receipt_health["activeReceiptManifestErrors"],
            }
        )
    if not receipt_health["reservationArtifactsStableDuringScan"]:
        blockers.append({"code": "reservation_boundary_changed_during_census"})
    if not selected:
        blockers.append(
            {
                "code": "no_aligned_sources_within_height_cap",
                "available": len(selected),
                "maximum": args.max_sources,
            }
        )
    if len(selected_priority) != args.priority_tail_sources:
        blockers.append(
            {
                "code": "priority_tail_source_unavailable",
                "available": len(selected_priority),
                "required": args.priority_tail_sources,
            }
        )
    if selected and not selection_audit["passed"]:
        blockers.append(
            {
                "code": "prospective_frontier_selection_audit_failed",
                "failedChecks": sorted(
                    key
                    for key, value in selection_audit["checks"].items()
                    if not value
                ),
            }
        )
    frozen_target_max = max(frozen_target_values) if frozen_target_values else None
    if frozen_target_max != target_snapshot["generatedAtMax"]:
        blockers.append(
            {
                "code": "frozen_gold_precedes_target_snapshot",
                "frozenTargetGeneratedAtMax": frozen_target_max,
                "targetGeneratedAtMax": target_snapshot["generatedAtMax"],
            }
        )
    if (
        latest_submission_updated is not None
        and target_snapshot["generatedAtMax"] is not None
        and str(target_snapshot["generatedAtMax"]) < str(latest_submission_updated)
    ):
        blockers.append(
            {
                "code": "target_snapshot_precedes_latest_submission",
                "latestSubmissionUpdatedAt": latest_submission_updated,
                "targetGeneratedAtMax": target_snapshot["generatedAtMax"],
            }
        )

    # The audited base worker explicitly recomputes these legacy top-level
    # globs.  Keep its compatibility fields exact, while pinning the broader
    # nested wave corpus through pinnedInputs below.
    worker_prior_plan_paths = sorted(
        path
        for path in DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_plan.json")
        if path.is_file()
    )
    worker_prior_result_paths = sorted(
        path
        for path in DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl")
        if path.is_file()
    )
    worker_prior_plans = [artifact(path) for path in worker_prior_plan_paths]
    worker_prior_results = [artifact(path) for path in worker_prior_result_paths]
    volatile = mutable_boundary(
        destination, manifest, ignored_data_paths={report_path}
    )
    final_core = {
        "database": artifact(DB),
        "databaseWal": artifact(DB_WAL) if DB_WAL.is_file() else None,
        "frozenGold": artifact(GOLD),
        "worker": artifact(WORKER),
        "baseWorker": artifact(BASE_WORKER),
    }
    if final_core != initial_core:
        blockers.append({"code": "core_boundary_changed_during_census"})
    if volatile != census_volatile:
        blockers.append({"code": "volatile_boundary_changed_during_census"})
    current_plan_paths, current_result_paths = discover_prior_f5_paths(destination)
    if (
        current_plan_paths != plan_paths
        or current_result_paths != result_paths
        or [artifact(path) for path in current_plan_paths] != prior_plans
        or [artifact(path) for path in current_result_paths] != prior_results
    ):
        blockers.append({"code": "prior_f5_boundary_changed_during_census"})
    if any(
        not (ROOT / row["path"]).is_file()
        or sha256_path(ROOT / row["path"]) != row["sha256"]
        for row in action_artifacts
    ):
        blockers.append({"code": "action_boundary_changed_during_census"})

    preview = {
        "schemaVersion": "f5-untried-wave-preparation-report-v1",
        "waveId": args.wave_id,
        "status": "blocked_before_seal" if blockers else "ready_for_explicit_seal",
        "sealRequested": bool(args.seal),
        "blockers": blockers,
        "selection": {
            "maximumSources": args.max_sources,
            "selectedSources": len(selected),
            "heightCapBits": args.height_cap_bits,
            "coreHeightCapBits": args.core_height_cap_bits,
            "priorityTailSourcesRequested": args.priority_tail_sources,
            "priorityTailSourcesSelected": len(selected_priority),
            "coreSourcesSelected": len(selected_core),
            "remainingCandidatesBeforeAlignedCap": len(candidates),
            "selectedAlignedSources": len(selected),
            "selectedSourceSignatures": len(
                {(row["source"]["label"], int(row["source"]["r"])) for row in selected}
            ),
            "selectedDistinctGoldPairs": len(covered),
            "selectedDistinctAlignedPairs": len(
                {
                    (pair["label"], int(pair["r"]))
                    for row in selected
                    for pair in row["possibleGoldPairs"]
                    if int(pair["r"]) == int(row["source"]["r"])
                }
            ),
            "selectedMultiGoldSources": sum(
                len(row["possibleGoldPairs"]) > 1 for row in selected
            ),
            "selectedHeightBits": (
                {
                    "min": min(selected_heights),
                    "median": statistics.median(selected_heights),
                    "max": max(selected_heights),
                }
                if selected_heights
                else None
            ),
            "coefficientFreeSelectionSha256": selected_digest,
            "rowsMaterializedInReport": False,
        },
        "selectionAudit": selection_audit,
        "exclusions": {
            "priorCanonicalSources": len(tried),
            "priorCanonicalHashes": len(tried_hashes),
            "priorPlanArtifacts": len(prior_plans),
            "priorResultArtifacts": len(prior_results),
            "priorResultRows": prior_result_rows,
            "ledgerKnownCandidateSourceHashes": len(known_candidate_sources),
            "workerClaimedPairs": len(worker_claimed_pairs),
            "selectionReservedPairs": len(selection_reserved_pairs),
            "reservedCoefficientHashesFromOutboxesAndReceipts": len(reserved_hashes),
            "claimArtifacts": len(claim_artifacts),
            "outboxArtifacts": len(outbox_artifacts),
            "receiptArtifacts": len(receipt_artifacts),
            "workerClaimedPairBoundarySha256": worker_claimed_pair_boundary_sha256,
            "workerClaimedPairRowsMaterializedInReport": False,
        },
        "ledgerInventory": {
            "acceptedScoreableRows": accepted_rows,
            "acceptedEvenRows": even_rows,
            "eligibleUniqueActionEvenRows": eligible_even_rows,
            "distinctKnownCoefficientHashes": distinct_known_hashes,
            "frozenGoldPairs": len(frozen),
            "currentUnreservedFrozenGoldPairs": len(gold),
        },
        "receiptHealth": receipt_health,
        "queueHealth": {
            "activeQueuedSubmissions": len(queued_rows),
            "legacyQueuedRowsStillBlocking": len(legacy_db_queued),
            "staleQueueExceptionEnabled": bool(
                args.allow_pinned_stale_queue
                and stale_queue_exception["certified"]
            ),
            "staleQueueExceptionRequiredPins": [
                "exactSubmissionIds",
                "exactQueuedStatusCounts",
                "exactCoefficientHashBoundary",
                "minimumAge",
                "allRowsLedgerReserved",
                "mandatoryFinalLivePreSubmitRefresh",
            ],
            "queuedBoundary": queue_snapshot,
            "pinnedStaleQueueException": stale_queue_exception,
        },
        "pinnedPreviewBoundaries": {
            **final_core,
            "targets": target_snapshot,
            "volatile": volatile,
        },
        "plannedOutputs": {
            "destination": relative(destination),
            "manifest": relative(manifest),
            "plan": relative(plan_path),
            "preflight": relative(preflight_path),
            "retryOfFailedPlans": retry_failed_artifacts,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    atomic_text(report_path, json.dumps(preview, indent=2, sort_keys=True) + "\n")
    if not args.seal:
        print(json.dumps(preview, indent=2, sort_keys=True))
        return 0
    if blockers:
        raise ValueError(
            "refusing to seal blocked F5 wave: "
            + ",".join(str(row["code"]) for row in blockers)
        )

    selected_canonical_hashes = {
        row["canonicalQuotientSha256"] for row in selected
    }
    selected_source_signatures = {
        (row["source"]["label"], int(row["source"]["r"])) for row in selected
    }
    selected_aligned_pairs = {
        (pair["label"], int(pair["r"]))
        for row in selected
        for pair in row["possibleGoldPairs"]
        if int(pair["r"]) == int(row["source"]["r"])
    }
    seal_checks = {
        "compactAlignedCountPinned": 1 <= len(selected) <= 50
        and all(row["signatureAligned"] for row in selected),
        "heightCapEnforced": max(selected_heights) <= args.height_cap_bits,
        "canonicalSourcesUnique": len(selected) == len(selected_canonical_hashes),
        "sourceSignaturesUnique": len(selected) == len(selected_source_signatures),
        "alignedTargetPairsUnique": len(selected) == len(selected_aligned_pairs),
        "canonicalSourcesUntried": not (selected_canonical_hashes & tried_hashes),
        "pairsUnreserved": not (covered & selection_reserved_pairs),
        "priorityTailExact": len(selected_priority) == args.priority_tail_sources
        and (
            args.priority_tail_sources == 0
            or (
                selected[0]["canonicalQuotientSha256"]
                == PRIORITY_TAIL_CANONICAL_SHA256
                and int(selected[0]["coefficientHeightBits"])
                == PRIORITY_TAIL_HEIGHT_BITS
            )
        ),
        "coreHeightCapEnforced": all(
            int(row["coefficientHeightBits"]) <= args.core_height_cap_bits
            for row in selected_core
        ),
        "pinnedStaleQueueExceptionGate": not queued_rows
        or (
            args.allow_pinned_stale_queue
            and stale_queue_exception["certified"]
            and stale_queue_exception["enabled"]
        ),
    }
    if not all(seal_checks.values()):
        raise ValueError("compact F5 selection invariants failed before artifact creation")

    # The report write is outside destination and therefore part of the final
    # known-data boundary.  First prove every census input still has the exact
    # content used for selection, then include the owned report in the worker
    # boundary without re-reading any selection input into the plan.
    def assert_census_stable() -> None:
        current_core = {
            "database": artifact(DB),
            "databaseWal": artifact(DB_WAL) if DB_WAL.is_file() else None,
            "frozenGold": artifact(GOLD),
            "worker": artifact(WORKER),
            "baseWorker": artifact(BASE_WORKER),
        }
        current_plans, current_results = discover_prior_f5_paths(destination)
        if current_core != initial_core:
            raise ValueError("core boundary changed before compact seal")
        if (
            [artifact(path) for path in current_plans] != prior_plans
            or [artifact(path) for path in current_results] != prior_results
        ):
            raise ValueError("prior F5 boundary changed before compact seal")
        if mutable_boundary(
            destination, manifest, ignored_data_paths={report_path}
        ) != census_volatile:
            raise ValueError("volatile census boundary changed before compact seal")
        if any(
            not (ROOT / row["path"]).is_file()
            or sha256_path(ROOT / row["path"]) != row["sha256"]
            for row in action_artifacts
        ):
            raise ValueError("action boundary changed before compact seal")

    assert_census_stable()
    volatile = mutable_boundary(destination, manifest)
    selected_frontier_rows = {frontier_identity(row): row for row in selected}
    ordered_index = sorted(
        (
            selected_frontier_rows.get(frontier_identity(row), row)
            for row in candidates.values()
        ),
        key=lambda row: (
            int(row["source"]["t"]),
            int(row["source"]["r"]),
            row["canonicalQuotientSha256"],
        ),
    )
    selected_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True) for row in selected
    }
    frontier_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True)
        for row in ordered_index
    }
    seal_checks["selectedRowsExactFrontierSubset"] = (
        len(selected_exact) == len(selected) and selected_exact <= frontier_exact
    )
    original_frontier_identities = {
        frontier_identity(row) for row in candidates.values()
    }
    sealed_frontier_identities = {
        frontier_identity(row) for row in ordered_index
    }
    seal_checks["frontierIdentitiesPreservedExactly"] = (
        len(original_frontier_identities)
        == len(candidates)
        == len(sealed_frontier_identities)
        == len(ordered_index)
        and original_frontier_identities == sealed_frontier_identities
    )
    selection_annotation_keys = {
        "newGoldPairsAtSelection",
        "selectionRank",
        "selectionTier",
        "signatureAligned",
    }
    seal_checks["exactlySelectedRowsAnnotated"] = sum(
        selection_annotation_keys <= set(row) for row in ordered_index
    ) == len(selected)
    index_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in ordered_index
    )
    claimed_index_text = json.dumps(
        {
            "pairs": [
                {"label": label, "r": signature}
                    for label, signature in sorted(worker_claimed_pairs)
            ],
            "sourceArtifacts": claim_artifacts,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    command = (
        "/usr/bin/caffeinate -i /usr/local/bin/sage -python "
        f"{WORKER.name} --plan {relative(plan_path)}"
    )
    pinned_inputs = [
        initial_core["frozenGold"],
        initial_core["database"],
        initial_core["worker"],
        initial_core["baseWorker"],
        *claim_artifacts,
        *prior_plans,
        *prior_results,
    ]
    plan = {
        "schemaVersion": "f5-untried-frontier-plan-v1",
        "waveId": args.wave_id,
        "status": "ready_for_one_heavy_worker",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "frontier": preview["selection"],
        "ledgerInventory": preview["ledgerInventory"],
        "triedExclusions": preview["exclusions"],
        "selectedSources": selected,
        "artifacts": {
            "actionShards": action_artifacts,
            "databaseSidecars": {"wal": initial_core["databaseWal"]},
            "frozenGold": initial_core["frozenGold"],
            "frontierIndex": payload_artifact(index, index_text),
            "claimedPairIndex": payload_artifact(claimed_index, claimed_index_text),
            "pinnedInputs": pinned_inputs,
            "priorPlans": worker_prior_plans,
            "priorResults": worker_prior_results,
            "allPriorPlans": prior_plans,
            "allPriorResults": prior_results,
            "worker": initial_core["worker"],
            "results": relative(results),
            "summary": relative(summary),
            "manifest": relative(manifest),
            "claimsDirectory": relative(claims),
        },
        "targetBoundary": target_snapshot,
        "staleQueueException": stale_queue_exception,
        "retryOfFailedPlans": retry_failed_artifacts,
        "volatileBoundary": volatile,
        "execution": {
            "commandTemplate": command + " --expected-plan-sha256 <PLAN_SHA256>",
            "heavyWorkerLaunched": False,
            "oneWorkerAtATimeLockRequired": True,
            "submissionAuthorized": False,
            "preSubmissionRefreshRequired": True,
            "preSubmissionRefreshChecks": [
                "liveTargets",
                "queueBoundary",
                "nonbaseline",
                "locallyUnowned",
                "knownCoefficientHashes",
                "externalClaims",
            ],
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    seal_checks["coefficientFreePlan"] = (
        "quotientLine" not in plan_text and "coefficientLine" not in plan_text
    )
    seal_checks["compactCountMatchesPlan"] = len(selected) == int(
        plan["frontier"]["selectedSources"]
    )
    if not all(seal_checks.values()):
        raise ValueError("compact F5 plan invariants failed before plan publication")
    plan_sha = sha256_bytes(plan_text.encode())
    launch = command + f" --expected-plan-sha256 {plan_sha}"
    preflight = {
        "schemaVersion": "f5-untried-frontier-preflight-v1",
        "status": "certified_light_only_wave_ready",
        "checks": seal_checks,
        "plan": payload_artifact(plan_path, plan_text),
        "launchCommand": launch,
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }
    preflight_text = json.dumps(preflight, indent=2, sort_keys=True) + "\n"
    assert_census_stable()
    if mutable_boundary(destination, manifest) != volatile:
        raise ValueError("final volatile boundary changed before atomic publication")
    # Publish the complete runnable handoff with one directory rename.  Until
    # that rename succeeds, neither the plan nor its index exists at a final
    # worker-visible path.
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        atomic_text(staging / index.name, index_text)
        atomic_text(staging / claimed_index.name, claimed_index_text)
        atomic_text(staging / plan_path.name, plan_text)
        atomic_text(staging / preflight_path.name, preflight_text)
        if destination.exists():
            raise ValueError("destination appeared during atomic seal")
        os.rename(staging, destination)
        parent_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        if staging.exists():
            for child in staging.iterdir():
                if child.is_file():
                    child.unlink()
            staging.rmdir()
    print(
        json.dumps(
            {
                "plan": relative(plan_path),
                "planSha256": plan_sha,
                "launchCommand": launch,
                **preview["selection"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

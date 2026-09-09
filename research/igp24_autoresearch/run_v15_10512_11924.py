#!/usr/bin/env python3
"""Resolve and dry-run only the sole v15 24T10512/r16 conditional route."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import stage_frobenius_gold as frobenius
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
ROUTE_MAP = DATA / "autopilot_pair_delta_20260722_v15" / "missing_pair_all.jsonl"
SOURCE_RECEIPT = RECEIPTS / "sub_67ba82cbecda4192860fea618b883b48.json"
SOURCE_RESULT = DATA / "low_contention_lc02_24T10676_r16_to_24T10512_r16_result.jsonl"
SOURCE_POSTFLIGHT = DATA / "low_contention_lc02_24T10676_r16_to_24T10512_r16_result_postflight.json"
SOURCE_STAGE = DATA / "low_contention_lc02_stage_certificate.json"
RESULTS = DATA / "v15_10512_r16_pair_candidates.jsonl"
CERTIFICATE = DATA / "v15_10512_r16_frobenius_certificate.json"
MANIFEST = ROOT / "outbox" / "v15_10512_r16_to_11924_live.txt"
SUMMARY = DATA / "v15_10512_r16_to_11924_summary.json"
SOURCE_ID = "sub_67ba82cbecda4192860fea618b883b48"
SOURCE_INDEX = 0
SOURCE_HASH = "66e0a3d6b8bbf97213a53f238b8e53b259f3278253c9220b3866e1162cc96771"
SOURCE_PAIR = ("24T10512", 16)
TARGET_LABEL = "24T11924"
GOLD_R = {12, 16}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical_line_hash(line: str) -> str:
    values = [int(value.strip()) for value in line.split("#", 1)[0].split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("source manifest line is not monic degree 24")
    return sha256_bytes(",".join(map(str, values)).encode("ascii"))


def write_new(path: Path, payload: bytes) -> None:
    path = path.resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite guarded output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_route() -> dict:
    matches = [row for row in read_jsonl(ROUTE_MAP) if row.get("sourceLabel") == SOURCE_PAIR[0]]
    if len(matches) != 1:
        raise ValueError("v15 source route is missing or nonunique")
    row = matches[0]
    expected = str(row.pop("exactCertificateSha256"))
    actual = sha256_bytes(json.dumps(row, separators=(",", ":"), sort_keys=True).encode())
    if actual != expected or row.get("status") != "certified":
        raise ValueError("v15 route certificate hash/status mismatch")
    routes = [route for route in row.get("routes") or [] if route.get("targetLabel") == TARGET_LABEL]
    targets = [target for target in row.get("targets") or [] if target.get("targetLabel") == TARGET_LABEL]
    if len(routes) != 1 or len(targets) != 1 or int(targets[0].get("kernelOrder", -1)) != 1:
        raise ValueError("v15 target action is missing, repeated, or nonfaithful")
    route = routes[0]
    if set(map(int, route.get("mappedTargetR") or [])) != {8, 16} or set(map(int, route.get("goldR") or [])) != GOLD_R:
        raise ValueError("v15 conditional signature envelope changed")
    return {"routeSha256": expected, "mappedTargetR": [8, 16], "reachableGoldR": [16]}


def validate_source(connection: sqlite3.Connection) -> dict:
    row = connection.execute(
        "SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable,v.in_baseline,v.scoring_status "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        (SOURCE_ID, SOURCE_INDEX),
    ).fetchone()
    if row is None or tuple(row[:7]) != (SOURCE_HASH, SOURCE_PAIR[0], SOURCE_PAIR[1], "accepted", 1, 0, "scoreable"):
        raise ValueError("source ledger anchor is not the pinned accepted scoreable row")

    receipt = read_json(SOURCE_RECEIPT)
    manifest = Path(str(receipt["manifest"])).resolve()
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if (
        receipt.get("commit") is not True
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or str((receipt.get("response") or {}).get("submissionId")) != SOURCE_ID
        or sha256_path(manifest) != str(receipt.get("manifestHash"))
        or len(lines) != int(receipt.get("polynomials", -1))
        or canonical_line_hash(lines[SOURCE_INDEX]) != SOURCE_HASH
    ):
        raise ValueError("source committed receipt/manifest chain mismatch")

    generated = read_jsonl(SOURCE_RESULT)
    if len(generated) != 1:
        raise ValueError("source construction result is not unique")
    candidate = generated[0]
    orbit = candidate.get("orbitCertificate") or {}
    if (
        candidate.get("status") != "certified"
        or candidate.get("coefficientSha256") != SOURCE_HASH
        or candidate.get("targetLabel") != SOURCE_PAIR[0]
        or int(candidate.get("targetR", -1)) != SOURCE_PAIR[1]
        or candidate.get("sourceLabel") != "24T10676"
        or orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or any(int(value) != 1 for value in orbit.get("exponents") or [])
    ):
        raise ValueError("cached exact source construction chain mismatch")
    postflight = read_json(SOURCE_POSTFLIGHT)
    stage = read_json(SOURCE_STAGE)
    if (
        str((postflight.get("result") or {}).get("sha256")) != sha256_path(SOURCE_RESULT)
        or str((postflight.get("candidate") or {}).get("coefficientSha256")) != SOURCE_HASH
        or str((stage.get("candidate") or {}).get("coefficientSha256")) != SOURCE_HASH
    ):
        raise ValueError("source postflight/stage pins changed")
    return {
        "submissionId": SOURCE_ID,
        "polynomialIndex": SOURCE_INDEX,
        "coefficientSha256": SOURCE_HASH,
        "cachedParentPair": "24T10676/r16",
        "lineageDecision": "undecidable_between_target_r8_and_r16",
    }


def target_guard(connection: sqlite3.Connection, receipt_pairs: set[tuple[str, int]]) -> dict:
    state = []
    for r_value in sorted(GOLD_R):
        row = connection.execute(
            "SELECT team_count,generated_at FROM targets WHERE label=? AND r=?", (TARGET_LABEL, r_value)
        ).fetchone()
        baseline = connection.execute("SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", (TARGET_LABEL, r_value)).fetchone()
        owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1", (TARGET_LABEL, r_value)
        ).fetchone()
        if row is None or int(row[0]) != 0 or baseline is not None or owned is not None or (TARGET_LABEL, r_value) in receipt_pairs:
            raise ValueError(f"live/baseline/ownership/receipt guard failed for {TARGET_LABEL}/r{r_value}")
        state.append({"r": r_value, "teamCount": 0, "generatedAt": str(row[1])})
    return {"targetLabel": TARGET_LABEL, "liveGold": state}


def preflight() -> dict:
    if any(path.exists() for path in (RESULTS, CERTIFICATE, MANIFEST, SUMMARY)):
        raise FileExistsError("one or more guarded outputs already exist")
    route = validate_route()
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        source = validate_source(connection)
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(RECEIPTS, DATA, connection, {})
        live = target_guard(connection, receipt_pairs)
    return {
        "status": "preflight_passed",
        "route": route,
        "source": source,
        "live": live,
        "receiptCount": int(receipt_audit["receiptCount"]),
        "receiptPolynomialHashes": len(receipt_hashes),
        "outputsAbsent": True,
    }


def run() -> dict:
    before = preflight()
    print(json.dumps({"event": "heavy_worker_launch", "source": "24T10512/r16", "workers": 1}), flush=True)
    command = [
        "/usr/local/bin/sage", "-python", str(ROOT / "pair_sum_one.sage.py"), SOURCE_ID, str(SOURCE_INDEX),
        "--orbit-map", str(ROUTE_MAP), "--expected-target", TARGET_LABEL, "--expected-source-hash", SOURCE_HASH,
        "--all-degree-24", "--transforms", "1,2,3,5,7", "--reduce", "best",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)
    try:
        packet = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"pair worker returned no parseable certificate (exit {completed.returncode})") from exc
    if completed.returncode != 0 or packet.get("status") != "certified_multi" or len(packet.get("candidates") or []) != 3:
        raise RuntimeError(f"pair worker failed exact multi certificate (exit {completed.returncode})")
    packet_bytes = (json.dumps(packet, separators=(",", ":"), sort_keys=True) + "\n").encode()
    write_new(RESULTS, packet_bytes)

    frob = subprocess.run(
        ["/usr/local/bin/sage", "-python", str(ROOT / "frobenius_discriminate.sage.py"), "--input", str(RESULTS),
         "--source-pair", SOURCE_PAIR[0], str(SOURCE_PAIR[1]), "--prime-bound", "5000", "--require-resolved", "--output", str(CERTIFICATE)],
        cwd=ROOT, capture_output=True, text=True, timeout=600, check=False,
    )
    if frob.returncode != 0 or not CERTIFICATE.exists():
        raise RuntimeError(f"Frobenius assignment failed (exit {frob.returncode})")
    certificate = read_json(CERTIFICATE)
    joined = frobenius.join_resolved_assignments(certificate, [packet], RESULTS)
    route_rows = [row for row in joined if row["targetLabel"] == TARGET_LABEL]
    if len(route_rows) != 1:
        raise ValueError("Frobenius certificate did not assign exactly one target route factor")
    realized_r = int(route_rows[0]["targetR"])
    base_summary = {
        "status": "exact_signature_miss" if realized_r not in GOLD_R else "resolved_live_exact",
        "source": before["source"], "targetLabel": TARGET_LABEL, "realizedTargetR": realized_r,
        "pairResultsSha256": sha256_path(RESULTS), "frobeniusCertificateSha256": sha256_path(CERTIFICATE),
        "workersLaunched": 1, "submissionCalls": 0, "coefficientMaterialIncluded": False,
    }
    if realized_r not in GOLD_R:
        write_new(SUMMARY, (json.dumps(base_summary, indent=2, sort_keys=True) + "\n").encode())
        return base_summary

    pair = (TARGET_LABEL, realized_r)
    digest = str(route_rows[0]["coefficientSha256"])
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(RECEIPTS, DATA, connection, {digest: {pair}})
        target_guard(connection, receipt_pairs)
        selected, skips = frobenius.filter_live_gold(route_rows, DB)
    if digest in receipt_hashes or pair in receipt_pairs or len(selected) != 1:
        raise ValueError(f"post-resolution receipt/live guard excluded exact route: {dict(skips)}")
    write_new(MANIFEST, (str(selected[0]["coefficientLine"]) + "\n").encode("ascii"))
    dry = subprocess.run(
        ["python3", str(ROOT / "sair_api.py"), "submit", "--file", str(MANIFEST), "--description", "Team Dirac exact candidate dry run"],
        cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
    )
    if dry.returncode != 0:
        raise RuntimeError(f"API dry run failed (exit {dry.returncode})")
    dry_run = json.loads(dry.stdout)
    if dry_run.get("commit") is not False or int(dry_run.get("knownLocalHashes", -1)) != 0 or int(dry_run.get("polynomials", -1)) != 1:
        raise ValueError("API dry-run envelope is unsafe")
    summary = {
        **base_summary, "status": "staged_exact_api_dry_run_passed_not_submitted",
        "manifest": str(MANIFEST.relative_to(ROOT)), "manifestSha256": sha256_path(MANIFEST),
        "dryRun": {key: dry_run[key] for key in ("bytes", "commit", "knownLocalHashes", "manifestHash", "polynomials")},
        "receiptCountAtStage": int(receipt_audit["receiptCount"]),
    }
    write_new(SUMMARY, (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode())
    return summary


if __name__ == "__main__":
    result = run()
    print(json.dumps({key: value for key, value in result.items() if key not in {"source"}}, indent=2, sort_keys=True))

#!/usr/bin/env python3
"""Stage exact live golds derived from committed queued source polynomials.

This is the strict companion to ``pair_sum_staged_source.sage.py``.  It uses
the generic Frobenius certificate validator, then additionally enforces the
v3 exact-route census, rechecks the queued-source provenance artifacts, and
blocks coefficient or target-pair collisions in the ledger, receipts,
outboxes, and existing exact stage certificates.  It never makes network
requests, submits polynomials, or writes the ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from derived_stage_provenance import validate_derived_stage_source


ROOT = Path(__file__).resolve().parent
JOINER_PATH = ROOT / "stage_frobenius_gold.py"
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not one JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def canonical_json_sha256(value: dict) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return sha256_bytes(payload.encode("utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    destination = resolved(path)
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


def import_joiner():
    spec = importlib.util.spec_from_file_location("frobenius_gold_joiner", JOINER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import certificate joiner from {JOINER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_declared_artifact(value: dict, name: str) -> Path:
    if not isinstance(value, dict):
        raise ValueError(f"source certificate has no {name} artifact")
    path = resolved(Path(str(value.get("path"))))
    if not path.is_file():
        raise ValueError(f"source certificate {name} artifact is missing")
    if str(value.get("sha256")) != sha256_path(path):
        raise ValueError(f"source certificate {name} artifact hash mismatch")
    return path


def validate_no_external_mutation(
    value: dict, context: str, *, require_ledger_field: bool
) -> None:
    if int(value.get("networkCalls", -1)) != 0 or int(
        value.get("submissionCalls", -1)
    ) != 0:
        raise ValueError(f"{context} records external calls")
    if require_ledger_field:
        if int(value.get("ledgerWrites", -1)) != 0:
            raise ValueError(f"{context} does not certify zero ledger writes")
    elif "ledgerWrites" in value and int(value["ledgerWrites"]) != 0:
        # Older source-stage and F5-plan artifacts predate this field.  Accept
        # an absent field for compatibility, but never accept an explicit write.
        raise ValueError(f"{context} records ledger writes")


def validate_safe_source_proof(
    row: dict,
    certificate: dict,
    manifest_path: Path,
    source_index: int,
    source_line: str,
    source_hash: str,
) -> None:
    manifest_path = resolved(manifest_path)
    results_path = validate_declared_artifact(
        certificate.get("safeResults"), "safeResults"
    )
    stage_path = validate_declared_artifact(
        certificate.get("safeStageCertificate"), "safeStageCertificate"
    )
    stage = read_json(stage_path)
    if stage.get("status") != "staged_exact":
        raise ValueError("safe source stage is not staged_exact")
    validate_no_external_mutation(
        stage, "safe source stage", require_ledger_field=False
    )
    stage_manifest = stage.get("manifest")
    if (
        not isinstance(stage_manifest, dict)
        or resolved(Path(str(stage_manifest.get("path")))) != manifest_path
        or str(stage_manifest.get("sha256")) != sha256_path(manifest_path)
    ):
        raise ValueError("safe source stage manifest provenance mismatch")
    candidates = stage.get("candidates")
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    if (
        not isinstance(candidates, list)
        or len(candidates) != len(lines)
        or int(stage.get("batchSize", -1)) != len(lines)
    ):
        raise ValueError("safe source stage cardinality mismatch")
    staged = candidates[source_index]
    if not isinstance(staged, dict):
        raise ValueError("safe source stage candidate is not an object")
    target = staged.get("target")
    if (
        str(staged.get("coefficientLine")) != source_line
        or str(staged.get("coefficientSha256")) != source_hash
        or not isinstance(target, dict)
        or str(target.get("label")) != str(row["sourceLabel"])
        or int(target.get("r", -1)) != int(row["sourceR"])
        or str(staged.get("fieldDiscriminantAbs"))
        != str(row.get("sourceFieldDiscriminantAbs"))
    ):
        raise ValueError("safe source stage candidate provenance mismatch")
    factor = staged.get("factorCertificate")
    if (
        not isinstance(factor, dict)
        or factor.get("resolventSquarefree") is not True
        or sorted(map(int, factor.get("actualDegrees", [])))
        != sorted(map(int, factor.get("expectedDegrees", [])))
        or any(int(value) != 1 for value in factor.get("exponents", []))
    ):
        raise ValueError("safe source stage factor certificate is incomplete")

    matching = []
    for result in read_jsonl(results_path):
        candidate = result.get("candidate")
        if not isinstance(candidate, dict):
            continue
        if (
            str(result.get("candidateSha256")) == source_hash
            and str(candidate.get("coefficientSha256")) == source_hash
            and str(candidate.get("coefficientLine")) == source_line
            and str(candidate.get("targetLabel")) == str(row["sourceLabel"])
            and int(candidate.get("targetR", -1)) == int(row["sourceR"])
        ):
            matching.append(result)
    if not matching:
        raise ValueError("safe results do not reproduce the queued source")
    invariants = set()
    for result in matching:
        candidate = result["candidate"]
        orbit = candidate.get("orbitCertificate")
        if (
            result.get("status") != "exact_frozen_gold_hit"
            or result.get("exactFrozenGoldHit") is not True
            or result.get("routeMode") not in {"safe", "ambiguous"}
            or int(result.get("realizedTargetR", -1)) != int(row["sourceR"])
            or candidate.get("status") != "certified"
            or not isinstance(orbit, dict)
            or sorted(map(int, orbit.get("actualDegrees", [])))
            != sorted(map(int, orbit.get("expectedDegrees", [])))
            or any(int(value) != 1 for value in orbit.get("exponents", []))
            or str(candidate.get("fieldDiscriminantAbs"))
            != str(row.get("sourceFieldDiscriminantAbs"))
        ):
            raise ValueError("safe result proof envelope is incomplete")
        invariants.add(
            (
                str(candidate.get("coefficientSha256")),
                str(candidate.get("coefficientLine")),
                str(candidate.get("targetLabel")),
                int(candidate.get("targetR", -1)),
                str(candidate.get("fieldDiscriminantAbs")),
                canonical_json_sha256(orbit),
            )
        )
    if len(invariants) != 1:
        raise ValueError("duplicate safe-result derivations disagree")


def validate_f5_source_proof(
    row: dict,
    certificate: dict,
    source_line: str,
    source_hash: str,
) -> None:
    results_path = validate_declared_artifact(
        certificate.get("f5Results"), "f5Results"
    )
    plan_path = validate_declared_artifact(certificate.get("f5Plan"), "f5Plan")
    matches = [
        value
        for value in read_jsonl(results_path)
        if isinstance(value.get("candidate"), dict)
        and isinstance(value.get("target"), dict)
        and str(value["candidate"].get("coefficientSha256")) == source_hash
        and str(value["candidate"].get("coefficientLine")) == source_line
        and str(value["target"].get("label")) == str(row["sourceLabel"])
        and int(value["target"].get("r", -1)) == int(row["sourceR"])
    ]
    if len(matches) != 1:
        raise ValueError("F5 provenance does not uniquely reproduce the queued source")
    result = matches[0]
    matching_digest = canonical_json_sha256(result)
    f5_results = certificate["f5Results"]
    if matching_digest != str(f5_results.get("matchingRowSha256")):
        raise ValueError("F5 provenance matching-row digest mismatch")
    candidate = result["candidate"]
    target = result["target"]
    action = result.get("exactAction")
    upstream = result.get("source")
    factor_degrees = result.get("factorDegrees")
    if result.get("status") not in {
        "resolved_not_frozen_live_signature",
        "resolved_known_coefficient",
        "resolved_pair_claimed",
        "hit_staged",
    }:
        raise ValueError("F5 result does not record an exact resolved candidate")
    if (
        candidate.get("irreducible") is not True
        or int(candidate.get("r", -1)) != int(row["sourceR"])
        or not isinstance(action, dict)
        or not isinstance(upstream, dict)
        or str(action.get("targetLabel")) != str(row["sourceLabel"])
        or int(action.get("targetT", -1))
        != int(str(row["sourceLabel"]).split("T", 1)[1])
        or str(action.get("sourceLabel")) != str(upstream.get("label"))
        or int(action.get("sourceT", -1)) != int(upstream.get("t", -2))
        or not isinstance(factor_degrees, list)
        or any(int(value.get("exponent", -1)) != 1 for value in factor_degrees)
        or sum(int(value.get("degree", -1)) == 12 for value in factor_degrees) != 1
        or HEX64_RE.fullmatch(str(result.get("pairResolventSha256"))) is None
    ):
        raise ValueError("F5 exact proof envelope is incomplete")

    construction = certificate.get("f5ExactConstruction")
    if (
        not isinstance(construction, dict)
        or construction.get("uniqueDegree12Factor") is not True
        or str(construction.get("matchingResultRowSha256")) != matching_digest
        or construction.get("exactAction") != action
        or construction.get("factorDegrees") != sorted(
            (
                {
                    "degree": int(value["degree"]),
                    "exponent": int(value["exponent"]),
                }
                for value in factor_degrees
            ),
            key=lambda value: (value["degree"], value["exponent"]),
        )
        or str(construction.get("pairResolventSha256"))
        != str(result.get("pairResolventSha256"))
        or str(construction.get("upstreamSubmissionId"))
        != str(upstream.get("submissionId"))
        or int(construction.get("upstreamPolynomialIndex", -1))
        != int(upstream.get("polynomialIndex", -2))
        or str(construction.get("upstreamCoefficientSha256"))
        != str(upstream.get("coefficientSha256"))
        or str(construction.get("upstreamLabel")) != str(upstream.get("label"))
        or int(construction.get("upstreamR", -1)) != int(upstream.get("r", -2))
        or str(construction.get("upstreamFieldDiscriminantAbs"))
        != str(upstream.get("fieldDiscAbs"))
    ):
        raise ValueError("F5 exact construction certificate is inconsistent")

    plan = read_json(plan_path)
    validate_no_external_mutation(plan, "F5 source plan", require_ledger_field=False)
    exact_actions = plan.get("exactActions")
    if not isinstance(exact_actions, dict) or exact_actions.get(str(upstream["label"])) != action:
        raise ValueError("F5 source plan action mismatch")
    selected = plan.get("selected")
    if not isinstance(selected, list):
        raise ValueError("F5 source plan has no selected-source list")
    selected_matches = [
        value
        for value in selected
        if isinstance(value, dict)
        and isinstance(value.get("source"), dict)
        and str(value["source"].get("submissionId"))
        == str(upstream.get("submissionId"))
        and int(value["source"].get("polynomialIndex", -1))
        == int(upstream.get("polynomialIndex", -2))
        and str(value["source"].get("coefficientSha256"))
        == str(upstream.get("coefficientSha256"))
        and str(value["source"].get("label")) == str(upstream.get("label"))
        and int(value["source"].get("r", -1)) == int(upstream.get("r", -2))
    ]
    if len(selected_matches) != 1:
        raise ValueError("F5 source plan does not uniquely nominate its upstream source")


def validate_derived_source_proof(
    row: dict,
    certificate: dict,
    manifest_path: Path,
    source_index: int,
    source_line: str,
    source_hash: str,
    depth: int,
) -> None:
    stage_path = validate_declared_artifact(
        certificate.get("derivedStageCertificate"), "derivedStageCertificate"
    )
    proof = validate_derived_stage_source(
        manifest_path,
        stage_path,
        source_index,
        str(row["sourceLabel"]),
        int(row["sourceR"]),
        source_hash,
        lambda upstream: validate_source_certificate(upstream, depth + 1),
    )
    if str(proof["line"]) != source_line:
        raise ValueError("derived source proof reconstructed a different source line")
    declarations = {
        "derivedCandidates": (proof["candidatesPath"], proof["candidatesSha256"]),
        "derivedFrobenius": (proof["frobeniusPath"], proof["frobeniusSha256"]),
        "derivedOrbitMap": (proof["orbitMapPath"], proof["orbitMapSha256"]),
    }
    for name, (expected_path, expected_hash) in declarations.items():
        actual_path = validate_declared_artifact(certificate.get(name), name)
        if actual_path != expected_path or str(certificate[name].get("sha256")) != str(
            expected_hash
        ):
            raise ValueError(f"derived source {name} declaration mismatch")
    orbit = certificate.get("derivedOrbitMap")
    if (
        not isinstance(orbit, dict)
        or str(orbit.get("exactCertificateSha256"))
        != str(proof["pairCensusCertificateSha256"])
    ):
        raise ValueError("derived source orbit certificate digest mismatch")


def validate_source_certificate(row: dict, depth: int = 0) -> dict:
    if depth > 8:
        raise ValueError("queued-source provenance chain exceeds eight stages")
    if row.get("status") != "certified_multi":
        raise ValueError("candidate row is not certified_multi")
    validate_no_external_mutation(
        row, "candidate row", require_ledger_field=True
    )
    certificate = row.get("sourceCertificate")
    if not isinstance(certificate, dict):
        raise ValueError("candidate row has no supported queued-source certificate")
    kind = str(certificate.get("kind"))
    if kind not in {
        "committed-queued-exact-stage-provenance-v1",
        "committed-queued-f5-unique-pair-provenance-v1",
        "committed-queued-derived-stage-provenance-v1",
    }:
        raise ValueError("candidate row has no supported queued-source certificate")
    manifest_path = validate_declared_artifact(certificate.get("manifest"), "manifest")
    receipt_path = validate_declared_artifact(
        certificate.get("committedReceipt"), "committedReceipt"
    )
    manifest = certificate["manifest"]
    source_index = int(row["sourcePolynomialIndex"])
    if int(manifest.get("polynomialIndex", -1)) != source_index:
        raise ValueError("source certificate manifest index mismatch")
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    if source_index < 0 or source_index >= len(lines):
        raise ValueError("source certificate manifest index is outside its manifest")
    source_hash = sha256_bytes(lines[source_index].encode("utf-8"))
    if source_hash != str(row.get("sourceCoefficientSha256")) or source_hash != str(
        manifest.get("coefficientSha256")
    ):
        raise ValueError("queued-source coefficient hash/index mismatch")
    if kind == "committed-queued-exact-stage-provenance-v1":
        validate_safe_source_proof(
            row,
            certificate,
            manifest_path,
            source_index,
            lines[source_index],
            source_hash,
        )
    elif kind == "committed-queued-f5-unique-pair-provenance-v1":
        validate_f5_source_proof(row, certificate, lines[source_index], source_hash)
    else:
        validate_derived_source_proof(
            row,
            certificate,
            manifest_path,
            source_index,
            lines[source_index],
            source_hash,
            depth,
        )
    receipt = read_json(receipt_path)
    if receipt.get("commit") is not True:
        raise ValueError("queued-source receipt is not committed")
    if resolved(Path(str(receipt.get("manifest")))) != manifest_path:
        raise ValueError("queued-source receipt manifest path mismatch")
    if str(receipt.get("manifestHash")) != sha256_path(manifest_path):
        raise ValueError("queued-source receipt manifest hash mismatch")
    response = receipt.get("response")
    if not isinstance(response, dict) or str(response.get("submissionId")) != str(
        row["sourceSubmissionId"]
    ):
        raise ValueError("queued-source receipt submission id mismatch")
    receipt_declaration = certificate.get("committedReceipt")
    if (
        not isinstance(receipt_declaration, dict)
        or str(receipt_declaration.get("submissionId"))
        != str(row["sourceSubmissionId"])
        or str(receipt_declaration.get("submissionStatus")) != "queued"
        or str(response.get("submissionStatus")) != "queued"
        or int(response.get("rejectedCount", -1)) != 0
    ):
        raise ValueError("queued-source receipt is not an all-accepted queued submission")
    payload = response.get("payload")
    queued = payload.get("queuedPolynomials") if isinstance(payload, dict) else []
    matching = [
        value
        for value in queued
        if isinstance(value, dict)
        and int(value.get("polynomialIndex", -1)) == source_index
        and str(value.get("status")) == "queued"
    ]
    if len(matching) != 1:
        raise ValueError("queued-source receipt index is not uniquely certified")
    checks = certificate.get("sourceChecks")
    if not isinstance(checks, dict):
        raise ValueError("queued-source exact source checks are absent")
    if (
        int(checks.get("degree", -1)) != 24
        or checks.get("monic") is not True
        or checks.get("primitive") is not True
        or checks.get("irreducible") is not True
        or int(checks.get("realRoots", -1)) != int(row["sourceR"])
        or str(checks.get("fieldDiscriminantAbs"))
        != str(row.get("sourceFieldDiscriminantAbs"))
    ):
        raise ValueError("queued-source exact algebraic checks are inconsistent")
    return {
        "receipt": receipt_path,
        "manifest": manifest_path,
        "sourceCoefficientSha256": source_hash,
    }


def coefficient_hashes_in_outboxes(
    outbox_root: Path, excluded_output: Path
) -> dict[str, set[str]]:
    occurrences: dict[str, set[str]] = {}
    if not outbox_root.exists():
        return occurrences
    for path in sorted(outbox_root.glob("*.txt")):
        if resolved(path) == resolved(excluded_output):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest = sha256_bytes(line.encode("utf-8"))
            occurrences.setdefault(digest, set()).add(str(resolved(path)))
    return occurrences


def committed_receipt_manifests(
    receipts_root: Path, database: Path,
) -> tuple[dict[Path, dict], Counter]:
    manifests: dict[Path, dict] = {}
    audit: Counter = Counter()
    if not receipts_root.exists():
        return manifests, audit
    ledger = sqlite3.connect(f"file:{resolved(database)}?mode=ro", uri=True)
    try:
        for path in sorted(receipts_root.glob("*.json")):
            receipt = read_json(path)
            if receipt.get("commit") is not True:
                audit["not_committed"] += 1
                continue
            audit["committed"] += 1
            manifest_value = receipt.get("manifest")
            if not manifest_value:
                audit["missing_manifest_path"] += 1
                continue
            manifest = resolved(Path(str(manifest_value)))
            stale_reason = None
            if not manifest.is_file():
                stale_reason = "manifest_missing"
            else:
                digest = sha256_path(manifest)
                if str(receipt.get("manifestHash")) != digest:
                    stale_reason = "manifest_rotated"
            if stale_reason is not None:
                # Historical outbox paths were sometimes rotated or removed.
                # Such a receipt is safe to omit from the live-manifest hash
                # census only when every submitted row is already represented
                # in both ledger tables, where the later collision gates see it.
                response = receipt.get("response")
                submission_id = (
                    str(response.get("submissionId"))
                    if isinstance(response, dict) and response.get("submissionId")
                    else path.stem
                )
                expected = int(receipt.get("polynomials") or 0)
                if expected <= 0:
                    raise ValueError(
                        f"stale committed receipt {path} has no positive row count"
                    )
                polynomial_rows = int(
                    ledger.execute(
                        "SELECT COUNT(*) FROM polynomials WHERE submission_id=?",
                        (submission_id,),
                    ).fetchone()[0]
                )
                verification_rows = int(
                    ledger.execute(
                        "SELECT COUNT(*) FROM verifications WHERE submission_id=?",
                        (submission_id,),
                    ).fetchone()[0]
                )
                if polynomial_rows < expected or verification_rows < expected:
                    raise ValueError(
                        f"stale committed receipt {path} is not fully represented "
                        "in the ledger collision tables"
                    )
                audit[stale_reason] += 1
                audit[f"{stale_reason}_fully_ledgered"] += 1
                continue
            incumbent = manifests.get(manifest)
            if incumbent is not None and incumbent["manifestSha256"] != digest:
                raise ValueError("one receipt manifest path has inconsistent digests")
            manifests[manifest] = {
                "receipt": str(resolved(path)),
                "receiptSha256": sha256_path(path),
                "manifestSha256": digest,
            }
            audit["manifest_current_and_hash_valid"] += 1
    finally:
        ledger.close()
    return manifests, audit


def receipt_hashes(manifests: dict[Path, dict]) -> dict[str, set[str]]:
    occurrences: dict[str, set[str]] = {}
    for manifest in manifests:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest = sha256_bytes(line.encode("utf-8"))
            occurrences.setdefault(digest, set()).add(str(manifest))
    return occurrences


def candidate_pairs(value: dict) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            target = candidate.get("target")
            if isinstance(target, dict) and target.get("label") is not None:
                pairs.add((str(target["label"]), int(target["r"])))
            elif candidate.get("targetLabel") is not None:
                pairs.add(
                    (str(candidate["targetLabel"]), int(candidate["targetR"]))
                )
    selected_pairs = value.get("selectedPairs")
    if isinstance(selected_pairs, list):
        for pair in selected_pairs:
            if isinstance(pair, dict) and pair.get("label") is not None:
                pairs.add((str(pair["label"]), int(pair["r"])))
    return pairs


def exact_staged_pairs(
    data_root: Path, excluded_certificate: Path
) -> dict[tuple[str, int], set[str]]:
    occurrences: dict[tuple[str, int], set[str]] = {}
    if not data_root.exists():
        return occurrences
    paths = {
        *data_root.rglob("*stage*certificate*.json"),
        *data_root.rglob("*certificate*stage*.json"),
    }
    for path in sorted(paths):
        if resolved(path) == resolved(excluded_certificate):
            continue
        try:
            value = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if value.get("status") != "staged_exact":
            continue
        manifest = value.get("manifest")
        if not isinstance(manifest, dict):
            continue
        manifest_path = resolved(Path(str(manifest.get("path"))))
        if not manifest_path.is_file():
            continue
        if str(manifest.get("sha256")) != sha256_path(manifest_path):
            raise ValueError(f"exact stage certificate {path} manifest hash mismatch")
        for pair in candidate_pairs(value):
            occurrences.setdefault(pair, set()).add(str(resolved(path)))
    return occurrences


def exact_gold_routes(
    orbit_row: dict, source_r: int, allow_ambiguous_exact_routes: bool = False
) -> set[tuple[str, int]]:
    return {
        (str(route["targetLabel"]), int(target_r))
        for route in orbit_row.get("routes", [])
        if int(route.get("sourceR", -1)) == source_r
        and (
            route.get("allCompatibleClassesGold") is True
            or allow_ambiguous_exact_routes
        )
        for target_r in set(map(int, route.get("mappedTargetR", [])))
        & set(map(int, route.get("goldR", [])))
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--frobenius", type=Path, required=True)
    parser.add_argument("--orbit-map", type=Path, required=True)
    parser.add_argument("--outbox-root", type=Path, default=ROOT / "outbox")
    parser.add_argument("--receipts-root", type=Path, default=ROOT / "receipts")
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument(
        "--allow-ambiguous-exact-routes",
        action="store_true",
        help=(
            "after exact Frobenius/signature resolution, accept a realized pair "
            "in the route's mapped/gold intersection even when the structural "
            "route was not all-class-safe"
        ),
    )
    args = parser.parse_args()

    protected = {
        resolved(args.db),
        resolved(args.candidates),
        resolved(args.frobenius),
        resolved(args.orbit_map),
    }
    if resolved(args.manifest) in protected or resolved(args.certificate) in protected:
        parser.error("outputs must not overwrite proof inputs or the ledger")
    if resolved(args.manifest) == resolved(args.certificate):
        parser.error("manifest and certificate outputs must differ")
    if resolved(args.manifest).exists() or resolved(args.certificate).exists():
        parser.error("outputs must not overwrite existing files")

    candidate_rows = read_jsonl(args.candidates)
    frobenius = read_json(args.frobenius)
    joiner = import_joiner()
    joined = joiner.join_resolved_assignments(
        frobenius, candidate_rows, args.candidates, allow_unresolved=False
    )
    source_by_key = {}
    for row in candidate_rows:
        key = (
            str(row["sourceSubmissionId"]),
            int(row["sourcePolynomialIndex"]),
            str(row["sourceLabel"]),
            int(row["sourceR"]),
        )
        if key in source_by_key:
            raise ValueError(f"duplicate candidate source key: {key}")
        validate_source_certificate(row)
        source_by_key[key] = row

    orbit_rows = {}
    for orbit_row in read_jsonl(args.orbit_map):
        label = str(orbit_row["sourceLabel"])
        if label in orbit_rows:
            raise ValueError(f"duplicate v3/v4 orbit-map source label: {label}")
        orbit_rows[label] = orbit_row
    orbit_map_path = resolved(args.orbit_map)
    orbit_map_hash = sha256_path(orbit_map_path)
    outbox_hashes = coefficient_hashes_in_outboxes(args.outbox_root, args.manifest)
    committed_manifests, receipt_manifest_audit = committed_receipt_manifests(
        args.receipts_root, args.db
    )
    committed_hashes = receipt_hashes(committed_manifests)
    staged_pairs = exact_staged_pairs(args.data_root, args.certificate)

    eligible = []
    skip_counts: Counter = Counter()
    connection = sqlite3.connect(
        f"file:{resolved(args.db)}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        for assignment in joined:
            key = (
                str(assignment["sourceSubmissionId"]),
                int(assignment["sourcePolynomialIndex"]),
                str(assignment["sourceLabel"]),
                int(assignment["sourceR"]),
            )
            source = source_by_key.get(key)
            if source is None:
                raise ValueError(f"joined assignment has no exact source row: {key}")
            pair = (str(assignment["targetLabel"]), int(assignment["targetR"]))
            orbit_row = orbit_rows.get(key[2])
            if orbit_row is None:
                raise ValueError(f"no v3 orbit row for {key[2]}")
            source_orbit = source.get("orbitMap")
            if (
                not isinstance(source_orbit, dict)
                or resolved(Path(str(source_orbit.get("path")))) != orbit_map_path
                or str(source_orbit.get("sha256")) != orbit_map_hash
                or str(source_orbit.get("exactCertificateSha256"))
                != str(orbit_row.get("exactCertificateSha256"))
                or orbit_row.get("status") != "certified"
                or int(orbit_row.get("sourceT", -1))
                != int(key[2].split("T", 1)[1])
                or key[3] not in set(map(int, orbit_row.get("sourceR", [])))
                or HEX64_RE.fullmatch(
                    str(orbit_row.get("exactCertificateSha256"))
                )
                is None
            ):
                raise ValueError(f"source {key} orbit-census provenance mismatch")
            routes = exact_gold_routes(
                orbit_row, key[3], args.allow_ambiguous_exact_routes
            )
            if pair not in routes:
                skip_counts["not_exact_census_gold_route"] += 1
                continue
            factor_index = int(assignment["factorIndex"])
            source_candidates = source.get("candidates")
            if not isinstance(source_candidates, list) or not source_candidates:
                raise ValueError(f"source {key} has no exact candidates")
            factor_indexes = [int(value["factorIndex"]) for value in source_candidates]
            if (
                len(factor_indexes) != len(set(factor_indexes))
                or sorted(factor_indexes) != list(range(len(factor_indexes)))
            ):
                raise ValueError(f"source {key} candidate factor indexes are invalid")
            candidates = {
                int(value["factorIndex"]): value for value in source_candidates
            }
            candidate = candidates.get(factor_index)
            if candidate is None:
                raise ValueError(f"source {key} has no factor {factor_index}")
            candidate_hash = str(candidate["coefficientSha256"])
            if candidate_hash != str(assignment["coefficientSha256"]):
                raise ValueError("joined Frobenius assignment hash mismatch")
            line = str(candidate["coefficientLine"])
            if sha256_bytes(line.encode("utf-8")) != candidate_hash:
                raise ValueError("candidate coefficient hash does not recompute")
            try:
                coefficients = [int(value) for value in line.split(",")]
            except ValueError as exc:
                raise ValueError("candidate coefficient line is nonintegral") from exc
            if (
                len(coefficients) != 25
                or coefficients[-1] != 1
                or coefficients[0] == 0
                or math.gcd(*coefficients) != 1
                or candidate.get("irreducible") is not True
            ):
                raise ValueError("candidate exact algebraic checks are incomplete")
            if candidate_hash in outbox_hashes:
                skip_counts["coefficient_in_existing_outbox"] += 1
                continue
            if candidate_hash in committed_hashes:
                skip_counts["coefficient_in_committed_receipt"] += 1
                continue
            if pair in staged_pairs:
                skip_counts["target_pair_in_existing_exact_stage"] += 1
                continue

            target = connection.execute(
                "SELECT t,team_count,generated_at FROM targets WHERE label=? AND r=?",
                pair,
            ).fetchone()
            if target is None:
                skip_counts["target_missing"] += 1
                continue
            if int(target["team_count"]) != 0:
                skip_counts["not_current_team_count_zero"] += 1
                continue
            baseline = connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()[0]
            owned = connection.execute(
                """SELECT COUNT(*) FROM verifications
                   WHERE label=? AND r=? AND scoreable=1""",
                pair,
            ).fetchone()[0]
            coefficient_rows = connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0]
            field_disc = str(candidate.get("fieldDiscriminantAbs"))
            if not field_disc.isdigit() or int(field_disc) <= 0:
                raise ValueError("candidate has no positive exact field discriminant")
            same_field = connection.execute(
                """SELECT COUNT(*) FROM verifications
                   WHERE label=? AND r=? AND field_disc_abs=? AND scoreable=1""",
                (pair[0], pair[1], field_disc),
            ).fetchone()[0]
            if int(baseline):
                skip_counts["baseline_pair"] += 1
                continue
            if int(owned):
                skip_counts["locally_owned_pair"] += 1
                continue
            if int(coefficient_rows):
                skip_counts["coefficient_in_ledger"] += 1
                continue
            if int(same_field):
                skip_counts["same_field_at_target_pair"] += 1
                continue
            eligible.append(
                {
                    "candidate": candidate,
                    "frobeniusAssignment": assignment,
                    "noveltyAudit": {
                        "baselinePairRows": int(baseline),
                        "ownedExactTargetPairRows": int(owned),
                        "coefficientHashRows": int(coefficient_rows),
                        "sameTargetFieldDiscriminantRows": int(same_field),
                        "existingOutboxHashRows": 0,
                        "committedReceiptHashRows": 0,
                        "existingExactStagePairRows": 0,
                    },
                    "pairCensusCertificateSha256": str(
                        orbit_row["exactCertificateSha256"]
                    ),
                    "source": {
                        "submissionId": key[0],
                        "polynomialIndex": key[1],
                        "label": key[2],
                        "r": key[3],
                        "coefficientSha256": str(
                            source["sourceCoefficientSha256"]
                        ),
                        "fieldDiscriminantAbs": str(
                            source["sourceFieldDiscriminantAbs"]
                        ),
                        "provenance": source["sourceCertificate"],
                    },
                    "target": {
                        "label": pair[0],
                        "r": pair[1],
                        "t": int(target["t"]),
                        "teamCountAtStage": int(target["team_count"]),
                        "targetGeneratedAt": str(target["generated_at"]),
                    },
                }
            )
    finally:
        connection.close()

    best: dict[tuple[str, int], dict] = {}
    for row in eligible:
        pair = (str(row["target"]["label"]), int(row["target"]["r"]))
        key = (
            int(row["candidate"]["fieldDiscriminantAbs"]),
            int(row["candidate"]["polynomialDiscriminantAbs"]),
            len(str(row["candidate"]["coefficientLine"])),
            str(row["candidate"]["coefficientSha256"]),
        )
        incumbent = best.get(pair)
        if incumbent is None:
            best[pair] = row
        else:
            incumbent_key = (
                int(incumbent["candidate"]["fieldDiscriminantAbs"]),
                int(incumbent["candidate"]["polynomialDiscriminantAbs"]),
                len(str(incumbent["candidate"]["coefficientLine"])),
                str(incumbent["candidate"]["coefficientSha256"]),
            )
            if key < incumbent_key:
                best[pair] = row
            skip_counts["duplicate_target_pair"] += 1
    selected = sorted(
        best.values(),
        key=lambda row: (
            int(row["target"]["t"]),
            int(row["target"]["r"]),
            str(row["candidate"]["coefficientSha256"]),
        ),
    )
    if not selected:
        raise ValueError(
            "no exact current gold survives receipt/outbox/pair collision gates; "
            f"skips={dict(sorted(skip_counts.items()))}"
        )
    hashes = [str(row["candidate"]["coefficientSha256"]) for row in selected]
    pairs = [(str(row["target"]["label"]), int(row["target"]["r"])) for row in selected]
    if len(hashes) != len(set(hashes)) or len(pairs) != len(set(pairs)):
        raise ValueError("selected batch contains duplicate hashes or target pairs")

    manifest_text = "".join(
        str(row["candidate"]["coefficientLine"]) + "\n" for row in selected
    )
    write_text_atomic(args.manifest, manifest_text)
    certificate = {
        "certificateVersion": "staged-queued-source-pair-frobenius-v1",
        "status": "staged_exact",
        "createdAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "batchSize": len(selected),
        "candidates": selected,
        "inputs": {
            "candidates": {
                "path": str(resolved(args.candidates)),
                "sha256": sha256_path(args.candidates),
            },
            "frobenius": {
                "path": str(resolved(args.frobenius)),
                "sha256": sha256_path(args.frobenius),
            },
            "orbitMap": {
                "path": str(resolved(args.orbit_map)),
                "sha256": sha256_path(args.orbit_map),
            },
            "database": str(resolved(args.db)),
        },
        "collisionCensus": {
            "outboxDistinctHashes": len(outbox_hashes),
            "committedReceiptManifests": len(committed_manifests),
            "committedReceiptDistinctHashes": len(committed_hashes),
            "receiptManifestAudit": dict(sorted(receipt_manifest_audit.items())),
            "existingExactStagePairs": len(staged_pairs),
            "skipCounts": dict(sorted(skip_counts.items())),
        },
        "manifest": {
            "path": str(resolved(args.manifest)),
            "sha256": sha256_bytes(manifest_text.encode("utf-8")),
        },
        "networkCalls": 0,
        "ledgerWrites": 0,
        "submissionCalls": 0,
        "routeGateMode": (
            "exact-resolved-signature"
            if args.allow_ambiguous_exact_routes
            else "all-compatible-classes-gold"
        ),
    }
    write_text_atomic(
        args.certificate, json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "batchSize": len(selected),
                "certificate": str(resolved(args.certificate)),
                "certificateSha256": sha256_path(args.certificate),
                "manifest": str(resolved(args.manifest)),
                "manifestSha256": sha256_path(args.manifest),
                "selectedPairs": [
                    {
                        "label": row["target"]["label"],
                        "r": row["target"]["r"],
                        "coefficientSha256": row["candidate"]["coefficientSha256"],
                    }
                    for row in selected
                ],
                "skipCounts": dict(sorted(skip_counts.items())),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

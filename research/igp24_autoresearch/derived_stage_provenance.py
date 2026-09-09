#!/usr/bin/env python3
"""Validate a queued polynomial produced by the strict staged-source pipeline.

This module is deliberately read-only.  It verifies the complete retained
stage/candidate/Frobenius/orbit chain and delegates validation of the upstream
candidate packet's source certificate to its caller.  It never opens the
network or writes the ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable


HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
FROBENIUS_METHOD = "exact-unramified-frobenius-cycle-type-exclusion-v1"


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(resolved(path).read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(resolved(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain one JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        resolved(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid degree-24 label: {label!r}")
    return int(match.group(1))


def validate_declared_artifact(value: dict, name: str) -> tuple[Path, str]:
    if not isinstance(value, dict):
        raise ValueError(f"derived stage has no {name} artifact")
    path = resolved(Path(str(value.get("path"))))
    if not path.is_file():
        raise ValueError(f"derived stage {name} artifact is missing")
    digest = sha256_path(path)
    if str(value.get("sha256")) != digest:
        raise ValueError(f"derived stage {name} artifact hash mismatch")
    return path, digest


def validate_zero_side_effects(value: dict, context: str) -> None:
    if (
        int(value.get("networkCalls", -1)) != 0
        or int(value.get("submissionCalls", -1)) != 0
        or int(value.get("ledgerWrites", -1)) != 0
    ):
        raise ValueError(f"{context} does not certify zero external writes")


def validate_factor_certificate(value: dict, context: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{context} has no factor certificate")
    actual = sorted(map(int, value.get("actualDegrees", [])))
    expected = sorted(map(int, value.get("expectedDegrees", [])))
    exponents = list(map(int, value.get("exponents", [])))
    if not actual or actual != expected or len(actual) != len(exponents):
        raise ValueError(f"{context} factor-degree certificate mismatch")
    if any(exponent != 1 for exponent in exponents):
        raise ValueError(f"{context} factorization is not squarefree")


def validate_derived_stage_source(
    manifest_path: Path,
    stage_path: Path,
    polynomial_index: int,
    expected_label: str,
    expected_r: int,
    expected_hash: str,
    validate_upstream_source: Callable[[dict], object],
) -> dict:
    """Return a sealed metadata envelope after validating one derived source."""

    manifest_path = resolved(manifest_path)
    stage_path = resolved(stage_path)
    if HEX64_RE.fullmatch(expected_hash) is None:
        raise ValueError("derived source expected hash is not a SHA-256 digest")
    if expected_r < 0 or expected_r > 24 or expected_r % 2:
        raise ValueError("derived source expected signature is invalid")
    if label_t(expected_label) <= 0:
        raise ValueError("derived source label is invalid")

    stage = read_json(stage_path)
    if stage.get("status") != "staged_exact":
        raise ValueError("derived source stage is not staged_exact")
    if stage.get("certificateVersion") != "staged-queued-source-pair-frobenius-v1":
        raise ValueError("derived source stage certificate version is unsupported")
    validate_zero_side_effects(stage, "derived source stage")

    stage_manifest = stage.get("manifest")
    if not isinstance(stage_manifest, dict):
        raise ValueError("derived source stage has no manifest declaration")
    if resolved(Path(str(stage_manifest.get("path")))) != manifest_path:
        raise ValueError("derived source stage points at a different manifest")
    manifest_hash = sha256_path(manifest_path)
    if str(stage_manifest.get("sha256")) != manifest_hash:
        raise ValueError("derived source stage manifest hash mismatch")
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    stage_candidates = stage.get("candidates")
    if (
        not isinstance(stage_candidates, list)
        or len(stage_candidates) != len(lines)
        or int(stage.get("batchSize", -1)) != len(lines)
    ):
        raise ValueError("derived source stage/manifest cardinality mismatch")
    if polynomial_index < 0 or polynomial_index >= len(lines):
        raise ValueError("derived source index is outside its manifest")

    staged = stage_candidates[polynomial_index]
    if not isinstance(staged, dict):
        raise ValueError("derived source stage candidate is not an object")
    candidate = staged.get("candidate")
    target = staged.get("target")
    assignment = staged.get("frobeniusAssignment")
    source = staged.get("source")
    if not all(isinstance(value, dict) for value in (candidate, target, assignment, source)):
        raise ValueError("derived source stage candidate envelope is incomplete")
    line = lines[polynomial_index]
    line_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
    if line_hash != expected_hash:
        raise ValueError("derived source manifest hash/index mismatch")
    if (
        str(candidate.get("coefficientLine")) != line
        or str(candidate.get("coefficientSha256")) != line_hash
        or int(candidate.get("degree", -1)) != 24
        or candidate.get("monic") is not True
        or candidate.get("irreducible") is not True
        or int(candidate.get("targetR", -1)) != expected_r
    ):
        raise ValueError("derived source candidate exact checks are inconsistent")
    field_disc = str(candidate.get("fieldDiscriminantAbs"))
    polynomial_disc = str(candidate.get("polynomialDiscriminantAbs"))
    if not field_disc.isdigit() or int(field_disc) <= 0:
        raise ValueError("derived source has no positive field discriminant")
    if not polynomial_disc.isdigit() or int(polynomial_disc) <= 0:
        raise ValueError("derived source has no positive polynomial discriminant")
    if (
        str(target.get("label")) != expected_label
        or int(target.get("r", -1)) != expected_r
        or int(target.get("t", -1)) != label_t(expected_label)
        or int(target.get("teamCountAtStage", -1)) != 0
    ):
        raise ValueError("derived source stage target does not match the expected pair")
    novelty = staged.get("noveltyAudit")
    required_novelty = {
        "baselinePairRows",
        "ownedExactTargetPairRows",
        "coefficientHashRows",
        "sameTargetFieldDiscriminantRows",
        "existingOutboxHashRows",
        "committedReceiptHashRows",
        "existingExactStagePairRows",
    }
    if (
        not isinstance(novelty, dict)
        or not required_novelty.issubset(novelty)
        or any(int(value) != 0 for value in novelty.values())
    ):
        raise ValueError("derived source stage novelty audit is nonzero")
    pair_certificate = str(staged.get("pairCensusCertificateSha256"))
    if HEX64_RE.fullmatch(pair_certificate) is None:
        raise ValueError("derived source stage lacks a pair-census certificate")
    if (
        str(assignment.get("coefficientLine")) != line
        or str(assignment.get("coefficientSha256")) != line_hash
        or str(assignment.get("targetLabel")) != expected_label
        or int(assignment.get("targetR", -1)) != expected_r
        or int(assignment.get("targetT", -1)) != label_t(expected_label)
    ):
        raise ValueError("derived source Frobenius assignment mismatch")

    inputs = stage.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("derived source stage has no input declaration")
    candidates_path, candidates_hash = validate_declared_artifact(
        inputs.get("candidates"), "candidates"
    )
    frobenius_path, frobenius_hash = validate_declared_artifact(
        inputs.get("frobenius"), "frobenius"
    )
    orbit_path, orbit_hash = validate_declared_artifact(
        inputs.get("orbitMap"), "orbitMap"
    )

    packet = read_json(candidates_path)
    if packet.get("status") != "certified_multi":
        raise ValueError("derived source candidate packet is not certified_multi")
    validate_zero_side_effects(packet, "derived source candidate packet")
    validate_upstream_source(packet)
    if (
        str(source.get("submissionId")) != str(packet.get("sourceSubmissionId"))
        or int(source.get("polynomialIndex", -1))
        != int(packet.get("sourcePolynomialIndex", -2))
        or str(source.get("label")) != str(packet.get("sourceLabel"))
        or int(source.get("r", -1)) != int(packet.get("sourceR", -2))
        or str(source.get("coefficientSha256"))
        != str(packet.get("sourceCoefficientSha256"))
        or str(source.get("fieldDiscriminantAbs"))
        != str(packet.get("sourceFieldDiscriminantAbs"))
        or source.get("provenance") != packet.get("sourceCertificate")
    ):
        raise ValueError("derived source upstream packet provenance mismatch")
    packet_candidates = packet.get("candidates")
    if not isinstance(packet_candidates, list) or not packet_candidates:
        raise ValueError("derived source packet has no candidates")
    if any(not isinstance(value, dict) for value in packet_candidates):
        raise ValueError("derived source packet contains a nonobject candidate")
    factor_indexes = [int(value.get("factorIndex", -1)) for value in packet_candidates]
    if (
        len(factor_indexes) != len(set(factor_indexes))
        or sorted(factor_indexes) != list(range(len(packet_candidates)))
    ):
        raise ValueError("derived source packet factor indexes are invalid")
    factor_index = int(assignment.get("factorIndex", -1))
    packet_by_index = {
        int(value.get("factorIndex", -1)): value
        for value in packet_candidates
        if isinstance(value, dict)
    }
    packet_candidate = packet_by_index.get(factor_index)
    if packet_candidate is None:
        raise ValueError("derived source factor index is absent from its packet")
    for key in (
        "coefficientLine",
        "coefficientSha256",
        "fieldDiscriminantAbs",
        "polynomialDiscriminantAbs",
        "targetR",
        "degree",
        "monic",
        "irreducible",
    ):
        if packet_candidate.get(key) != candidate.get(key):
            raise ValueError(f"derived source packet candidate {key} mismatch")
    validate_factor_certificate(packet.get("orbitCertificate"), "derived source packet")
    packet_orbit = packet.get("orbitMap")
    if (
        not isinstance(packet_orbit, dict)
        or resolved(Path(str(packet_orbit.get("path")))) != orbit_path
        or str(packet_orbit.get("sha256")) != orbit_hash
        or str(packet_orbit.get("exactCertificateSha256")) != pair_certificate
    ):
        raise ValueError("derived source packet orbit provenance mismatch")

    orbit_rows = [
        value
        for value in read_jsonl(orbit_path)
        if str(value.get("sourceLabel")) == str(packet["sourceLabel"])
    ]
    if len(orbit_rows) != 1:
        raise ValueError("derived source orbit map does not contain one source row")
    orbit_row = orbit_rows[0]
    if (
        orbit_row.get("status") != "certified"
        or str(orbit_row.get("exactCertificateSha256")) != pair_certificate
        or int(orbit_row.get("sourceT", -1)) != label_t(str(packet["sourceLabel"]))
        or int(packet["sourceR"])
        not in {int(value) for value in orbit_row.get("sourceR", [])}
    ):
        raise ValueError("derived source orbit row certificate mismatch")
    orbit_sizes = list(map(int, orbit_row.get("orbitSizes", [])))
    if (
        sum(orbit_sizes) != 24 * 23 // 2
        or orbit_sizes.count(24) != len(orbit_row.get("targets", []))
        or int(orbit_row.get("length24OrbitCount", -1))
        != len(orbit_row.get("targets", []))
        or sorted(map(int, packet["orbitCertificate"].get("actualDegrees", [])))
        != sorted(orbit_sizes)
    ):
        raise ValueError("derived source orbit-size certificate mismatch")
    packet_slots = list(packet.get("orbitTargets") or [])
    if len(packet_slots) != len(packet_candidates):
        raise ValueError("derived source packet target/candidate cardinality mismatch")
    if packet_slots != list(orbit_row.get("targets") or []):
        raise ValueError("derived source packet target slots differ from its orbit row")
    orbit_indexes = [int(value.get("orbitIndex", -1)) for value in packet_slots]
    if (
        len(orbit_indexes) != len(set(orbit_indexes))
        or any(int(value.get("orbitSize", -1)) != 24 for value in packet_slots)
        or any(int(value.get("kernelOrder", -1)) != 1 for value in packet_slots)
        or any(
            int(value.get("targetT", -1))
            != label_t(str(value.get("targetLabel")))
            for value in packet_slots
        )
    ):
        raise ValueError("derived source packet target-slot certificate is invalid")
    if expected_label not in {str(value.get("targetLabel")) for value in packet_slots}:
        raise ValueError("derived source target label is absent from orbit slots")

    route_gate_mode = str(
        stage.get("routeGateMode", "all-compatible-classes-gold")
    )
    if route_gate_mode not in {
        "all-compatible-classes-gold",
        "exact-resolved-signature",
    }:
        raise ValueError("derived source stage route-gate mode is unsupported")
    matching_routes = [
        route
        for route in orbit_row.get("routes", [])
        if int(route.get("sourceR", -1)) == int(packet["sourceR"])
        and str(route.get("targetLabel")) == expected_label
        and int(route.get("targetT", -1)) == label_t(expected_label)
        and expected_r
        in (
            set(map(int, route.get("mappedTargetR", [])))
            & set(map(int, route.get("goldR", [])))
        )
        and (
            route.get("allCompatibleClassesGold") is True
            or route_gate_mode == "exact-resolved-signature"
        )
    ]
    if not matching_routes:
        raise ValueError("derived source target does not satisfy its recorded route gate")

    frobenius = read_json(frobenius_path)
    if frobenius.get("method") != FROBENIUS_METHOD:
        raise ValueError("derived source Frobenius method is unsupported")
    if (
        resolved(Path(str(frobenius.get("input")))) != candidates_path
        or str(frobenius.get("inputSha256")) != candidates_hash
        or str(frobenius.get("selectedInputRowsSha256")) != candidates_hash
    ):
        raise ValueError("derived source Frobenius input provenance mismatch")
    summary = frobenius.get("summary")
    rows = frobenius.get("rows")
    if (
        not isinstance(summary, dict)
        or not isinstance(rows, list)
        or int(summary.get("rows", -1)) != 1
        or int(summary.get("resolved", -1)) != 1
        or int(summary.get("unresolved", -1)) != 0
        or int(summary.get("contradiction", -1)) != 0
        or len(rows) != 1
        or rows[0].get("status") != "resolved"
    ):
        raise ValueError("derived source Frobenius packet is not uniquely resolved")
    proof_row = rows[0]
    if (
        str(proof_row.get("sourceSubmissionId"))
        != str(packet.get("sourceSubmissionId"))
        or int(proof_row.get("sourcePolynomialIndex", -1))
        != int(packet.get("sourcePolynomialIndex", -2))
        or str(proof_row.get("sourceLabel")) != str(packet.get("sourceLabel"))
        or int(proof_row.get("sourceR", -1)) != int(packet.get("sourceR", -2))
    ):
        raise ValueError("derived source Frobenius source key mismatch")
    assignments = proof_row.get("assignments")
    if not isinstance(assignments, list) or len(assignments) != len(packet_candidates):
        raise ValueError("derived source Frobenius assignment cardinality mismatch")
    assignment_indexes = [int(value.get("factorIndex", -1)) for value in assignments]
    if sorted(assignment_indexes) != sorted(factor_indexes):
        raise ValueError("derived source Frobenius factor indexes are not exact")
    assigned_labels = []
    for assigned in assignments:
        assigned_index = int(assigned["factorIndex"])
        assigned_candidate = packet_by_index[assigned_index]
        target_label = str(assigned.get("targetLabel"))
        if (
            str(assigned.get("coefficientSha256"))
            != str(assigned_candidate.get("coefficientSha256"))
            or int(assigned.get("targetR", -1))
            != int(assigned_candidate.get("targetR", -2))
            or int(assigned.get("targetT", -1)) != label_t(target_label)
        ):
            raise ValueError("derived source Frobenius assignment envelope mismatch")
        assigned_labels.append(target_label)
    slot_labels = [str(value.get("targetLabel")) for value in packet_slots]
    if Counter(assigned_labels) != Counter(slot_labels):
        raise ValueError("derived source Frobenius target-label multiset mismatch")
    matches = [
        value
        for value in assignments
        if int(value.get("factorIndex", -1)) == factor_index
        and str(value.get("coefficientSha256")) == line_hash
        and str(value.get("targetLabel")) == expected_label
        and int(value.get("targetR", -1)) == expected_r
        and int(value.get("targetT", -1)) == label_t(expected_label)
    ]
    if len(matches) != 1:
        raise ValueError("derived source Frobenius assignment is not unique")

    return {
        "line": line,
        "candidate": candidate,
        "stage": stage,
        "stagePath": stage_path,
        "stageSha256": sha256_path(stage_path),
        "manifestSha256": manifest_hash,
        "candidatesPath": candidates_path,
        "candidatesSha256": candidates_hash,
        "frobeniusPath": frobenius_path,
        "frobeniusSha256": frobenius_hash,
        "orbitMapPath": orbit_path,
        "orbitMapSha256": orbit_hash,
        "pairCensusCertificateSha256": pair_certificate,
        "sourceFieldDiscriminantAbs": field_disc,
    }

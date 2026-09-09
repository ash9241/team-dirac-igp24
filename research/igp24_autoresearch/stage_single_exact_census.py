#!/usr/bin/env python3
"""Seal exact, locally unowned SINGLE-output candidates from local artifacts.

This program is deliberately offline.  It recursively audits JSON/JSONL files,
accepts only explicit proof schemas, rejoins every applicable construction
source to a scoreable immutable-ledger row, and excludes baseline/owned/known
rows plus all receipt hashes and receipt target pairs (including queued rows).

Only the manifest contains coefficient payloads.  The certificate and summary
contain hashes, exact target pairs, proof pointers, and aggregate counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable


# Exact polynomial discriminants in this corpus can have tens of thousands of
# decimal digits.  They are trusted local arithmetic outputs and are used only
# for deterministic ordering, so retain Python's pre-3.11 unbounded conversion.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data"
DEFAULT_DATABASE = DEFAULT_DATA / "ledger.sqlite3"
DEFAULT_RECEIPTS = ROOT / "receipts"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
STATUS_PAIR_RE = re.compile(
    r"certified_(24T[1-9][0-9]*)_r(0|2|4|6|8|10|12|14|16|18|20|22|24)(?:_not_live)?\Z"
)
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
PAIR_TEXT_RE = re.compile(
    r"(24T[1-9][0-9]*)/r(0|2|4|6|8|10|12|14|16|18|20|22|24)\Z"
)
PAYLOAD_KEYS = (
    "coefficientLine",
    "candidateCoefficientLine",
    "coefficients",
    "minimalPolynomial",
)
HASH_KEYS = (
    "coefficientSha256",
    "candidateSha256",
    "derivedFactorSha256",
)
BAD_STATUS_PARTS = (
    "incomplete",
    "without_certificate",
    "failed",
    "error",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_polynomial_line(value: Any) -> str | None:
    if isinstance(value, list):
        if len(value) != 25:
            return None
        raw = value
    elif isinstance(value, str) and value.strip().count(",") == 24:
        raw = value.strip().split(",")
    else:
        return None
    try:
        coefficients = [int(entry) for entry in raw]
    except (TypeError, ValueError):
        return None
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients[0] == 0
        or math.gcd(*coefficients) != 1
    ):
        return None
    return ",".join(str(entry) for entry in coefficients)


def valid_pair(label: Any, r: Any) -> tuple[str, int] | None:
    label = str(label)
    if LABEL_RE.fullmatch(label) is None:
        return None
    try:
        r = int(r)
    except (TypeError, ValueError):
        return None
    if not 0 <= r <= 24 or r % 2:
        return None
    return label, r


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid target label: {label!r}")
    return int(match.group(1))


def direct_pair(row: dict) -> tuple[str, int] | None:
    for label_key, r_key in (("targetLabel", "targetR"), ("label", "r")):
        if label_key in row and r_key in row:
            result = valid_pair(row[label_key], row[r_key])
            if result is not None:
                return result
    for key in ("exactTarget", "target", "frozenGoldTarget", "liveTarget"):
        target = row.get(key)
        if not isinstance(target, dict):
            continue
        result = valid_pair(
            target.get("label", target.get("targetLabel")),
            target.get("r", target.get("targetR")),
        )
        if result is not None:
            return result
    return None


def positive_integer(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def read_json_roots(path: Path) -> list[tuple[int, Any]]:
    if path.suffix == ".jsonl":
        result = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            result.append((line_number, value))
        return result
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at {path}: {exc}") from exc
    if isinstance(value, list):
        return list(enumerate(value, start=1))
    return [(1, value)]


def iter_nodes(value: Any, pointer: str = "$", ancestors: tuple[dict, ...] = ()):
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_nodes(child, f"{pointer}/{index}", ancestors)
        return
    if not isinstance(value, dict):
        return
    yield value, pointer, ancestors
    for key, child in value.items():
        if isinstance(child, (dict, list)):
            yield from iter_nodes(child, f"{pointer}/{key}", ancestors + (value,))


def coefficient_payload(row: dict) -> tuple[str, str] | None:
    found = []
    for key in PAYLOAD_KEYS:
        line = canonical_polynomial_line(row.get(key))
        if line is not None:
            found.append((key, line))
    if not found:
        return None
    lines = {line for _key, line in found}
    if len(lines) != 1:
        raise ValueError("one proof object contains contradictory polynomial payloads")
    line = next(iter(lines))
    digest = sha256_bytes(line.encode("ascii"))
    declared = {
        str(row[key])
        for key in HASH_KEYS
        if isinstance(row.get(key), str) and SHA_RE.fullmatch(str(row[key]))
    }
    if declared and digest not in declared:
        raise ValueError("coefficient payload does not match its declared SHA-256")
    return line, digest


def orbit_certificate(row: dict) -> tuple[list[int], list[int]] | None:
    certificate = row.get("orbitCertificate")
    if not isinstance(certificate, dict):
        return None
    try:
        actual = [int(value) for value in certificate.get("actualDegrees") or []]
        expected = [int(value) for value in certificate.get("expectedDegrees") or []]
        exponents = [int(value) for value in certificate.get("exponents") or []]
    except (TypeError, ValueError):
        return None
    if (
        not actual
        or actual != expected
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
    ):
        return None
    return actual, exponents


def contradictory_claim(row: dict, ancestors: tuple[dict, ...]) -> bool:
    for value in (row,) + ancestors:
        status = str(value.get("status", "")).lower()
        if any(part in status for part in BAD_STATUS_PARTS):
            return True
        if "workerExitCode" in value:
            try:
                if int(value["workerExitCode"]) != 0:
                    return True
            except (TypeError, ValueError):
                return True
    return row.get("irreducible") is False


def source_pin_from_flat(row: dict) -> dict | None:
    if "sourceSubmissionId" not in row or "sourcePolynomialIndex" not in row:
        return None
    try:
        index = int(row["sourcePolynomialIndex"])
    except (TypeError, ValueError):
        return None
    return {
        "submissionId": str(row["sourceSubmissionId"]),
        "polynomialIndex": index,
        "coefficientSha256": row.get("sourceCoefficientSha256"),
        "label": row.get("sourceLabel"),
        "r": row.get("sourceR"),
    }


def source_pin_from_nested(row: dict) -> dict | None:
    if "submissionId" not in row or "polynomialIndex" not in row:
        return None
    try:
        index = int(row["polynomialIndex"])
    except (TypeError, ValueError):
        return None
    return {
        "submissionId": str(row["submissionId"]),
        "polynomialIndex": index,
        "coefficientSha256": row.get("coefficientSha256"),
        "label": row.get("label"),
        "r": row.get("r"),
    }


def merge_source_pins(pins: Iterable[dict]) -> list[dict]:
    merged: dict[tuple[str, int], dict] = {}
    for pin in pins:
        key = (str(pin["submissionId"]), int(pin["polynomialIndex"]))
        current = merged.setdefault(
            key,
            {
                "submissionId": key[0],
                "polynomialIndex": key[1],
                "coefficientSha256": None,
                "label": None,
                "r": None,
            },
        )
        for field in ("coefficientSha256", "label", "r"):
            value = pin.get(field)
            if value is None:
                continue
            if field == "r":
                value = int(value)
            else:
                value = str(value)
            if current[field] is not None and current[field] != value:
                raise ValueError(f"contradictory source pin for {key}: {field}")
            current[field] = value
    return list(merged.values())


def generic_source_pins(row: dict, ancestors: tuple[dict, ...]) -> list[dict]:
    pins = []
    for value in (row,) + tuple(reversed(ancestors)):
        pin = source_pin_from_flat(value)
        if pin is not None:
            pins.append(pin)
        nested = value.get("source")
        if isinstance(nested, dict):
            pin = source_pin_from_nested(nested)
            if pin is not None:
                pins.append(pin)
    return merge_source_pins(pins)


def candidate_record(
    *,
    row: dict,
    pair: tuple[str, int],
    schema: str,
    line: str,
    digest: str,
    source_pins: list[dict],
    source_path: Path,
    record_number: int,
    pointer: str,
    proof_details: dict,
) -> dict:
    return {
        "coefficientLine": line,
        "coefficientSha256": digest,
        "coefficientBytes": len(line.encode("ascii")),
        "fieldDiscriminantAbs": (
            str(value)
            if (value := positive_integer(
                row.get("fieldDiscriminantAbs", row.get("candidateFieldDiscriminantAbs"))
            ))
            is not None
            else None
        ),
        "polynomialDiscriminantAbs": (
            str(value)
            if (value := positive_integer(row.get("polynomialDiscriminantAbs")))
            is not None
            else None
        ),
        "proof": {
            "artifact": str(source_path.relative_to(ROOT)),
            "record": record_number,
            "pointer": pointer,
            "schema": schema,
            **proof_details,
        },
        "sourcePins": source_pins,
        "targetLabel": pair[0],
        "targetR": pair[1],
        "targetT": label_t(pair[0]),
    }


def validate_pair_sum(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    if row.get("status") != "certified" or contradictory_claim(row, ancestors):
        return None
    payload = coefficient_payload(row)
    pair = direct_pair(row)
    certificate = orbit_certificate(row)
    targets = row.get("orbitTargets")
    if payload is None or pair is None or certificate is None:
        return None
    degrees, _exponents = certificate
    if degrees.count(24) != 1 or not isinstance(targets, list) or len(targets) != 1:
        return None
    target = targets[0]
    if (
        not isinstance(target, dict)
        or str(target.get("targetLabel")) != pair[0]
        or int(target.get("targetT", -1)) != label_t(pair[0])
        or int(target.get("orbitSize", -1)) != 24
        or int(row.get("targetT", -1)) != label_t(pair[0])
    ):
        return None
    # ``factorIndex`` is the index within the degree-24 factor sublist, not
    # within the complete factor-degree list.  A SINGLE packet therefore has
    # exactly one valid value: zero.
    factor_index = int(row.get("factorIndex", 0))
    if factor_index != 0:
        return None

    parent = ancestors[-1] if ancestors else None
    if isinstance(parent, dict) and parent.get("candidate") is row:
        allowed_parent_status = {
            "exact_signature_miss",
            "exact_frozen_gold_hit",
            "exact_frozen_gold_hit_staged",
        }
        if parent.get("status") not in allowed_parent_status:
            return None
        if str(parent.get("candidateSha256")) != payload[1]:
            return None
        if str(parent.get("targetLabel")) != pair[0]:
            return None
        if "realizedTargetR" in parent and int(parent["realizedTargetR"]) != pair[1]:
            return None
        if "mappedTargetR" in parent:
            mapped = parent["mappedTargetR"]
            if isinstance(mapped, list):
                if pair[1] not in {int(value) for value in mapped}:
                    return None
            elif int(mapped) != pair[1]:
                return None
    pins = generic_source_pins(row, ancestors)
    if not pins:
        return None
    return candidate_record(
        row=row,
        pair=pair,
        schema="pair_sum_single_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=pins,
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "degree24FactorIndex": factor_index,
            "factorDegrees": degrees,
            "orbitIndex": int(target.get("orbitIndex", -1)),
        },
    )


def validate_twist(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    status = str(row.get("status", ""))
    if "quadratic_twist" not in status or contradictory_claim(row, ancestors):
        return None
    payload = coefficient_payload(row)
    pair = direct_pair(row)
    labels = row.get("allBlockSystemsTargetLabels")
    if payload is None or pair is None:
        return None
    if (
        row.get("twistIrreducible") is not True
        or row.get("sourceSquarefreeModRamificationPrime") is not True
        or row.get("actionResolutionMethod") != "all-block-systems-same-target"
        or not isinstance(labels, list)
        or not labels
        or set(str(value) for value in labels) != {pair[0]}
        or int(row.get("actionSystemCount", -1)) != len(labels)
        or int(row.get("twistDirectRealRootCount", -1)) != pair[1]
        or int(row.get("targetT", -1)) != label_t(pair[0])
        or positive_integer(row.get("ramificationPrime")) is None
        or positive_integer(row.get("sourceFieldDiscAbs")) is None
        or not str(row.get("genericActionProof", "")).strip()
    ):
        return None
    pins = generic_source_pins(row, ancestors)
    if len(pins) != 1 or not pins[0].get("coefficientSha256"):
        return None
    return candidate_record(
        row=row,
        pair=pair,
        schema="generic_quadratic_twist_exact_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=pins,
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "actionResolutionMethod": row["actionResolutionMethod"],
            "actionSystemCount": int(row["actionSystemCount"]),
            "ramificationPrime": int(row["ramificationPrime"]),
            "twistSign": str(row.get("twistSign")),
        },
    )


def validate_kummer_assignment(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    if row.get("status") not in {
        "resolved_not_live_signature",
        "hit_staged",
        "resolved_pair_claimed",
    } or row.get("irreducible") is not True or contradictory_claim(row, ancestors):
        return None
    payload = coefficient_payload(row)
    pair = direct_pair(row)
    if payload is None or pair is None or not isinstance(row.get("exactTarget"), dict):
        return None
    root = None
    for value in reversed(ancestors):
        if isinstance(value.get("assignmentCertificate"), dict):
            root = value
            break
    if root is None:
        return None
    certificate = root["assignmentCertificate"]
    assignments = certificate.get("remainingSlotAssignments")
    if (
        int(certificate.get("labelAssignmentCount", -1)) != 1
        or not isinstance(assignments, list)
        or len(assignments) != 1
        or int(row["exactTarget"].get("t", -1)) != label_t(pair[0])
        or positive_integer(row.get("fieldDiscriminantAbs")) is None
        or positive_integer(row.get("polynomialDiscriminantAbs")) is None
    ):
        return None
    source = root.get("source")
    pin = source_pin_from_nested(source) if isinstance(source, dict) else None
    if pin is None or not pin.get("coefficientSha256"):
        return None
    return candidate_record(
        row=row,
        pair=pair,
        schema="lower_kummer_exact_assignment_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=merge_source_pins([pin]),
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "assignmentMethod": str(certificate.get("assignmentMethod")),
            "factorIndex": int(row.get("factorIndex", -1)),
            "labelAssignmentCount": 1,
        },
    )


def validate_kummer_triple(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    if row.get("irreducible") is not True or contradictory_claim(row, ancestors):
        return None
    payload = coefficient_payload(row)
    if payload is None:
        return None
    root = None
    for value in reversed(ancestors):
        certificate = value.get("exactTargetCertificate")
        if isinstance(certificate, dict) and value.get("candidate") is row:
            root = value
            break
    if root is None or root.get("status") != "exact_miss_live_signature":
        return None
    certificate = root["exactTargetCertificate"]
    target = root.get("target")
    pair = direct_pair({"target": target}) if isinstance(target, dict) else None
    if (
        pair is None
        or certificate.get("uniqueDegree12Factor") is not True
        or int(certificate.get("tripleResolventDegree", -1)) <= 24
        or str(certificate.get("targetLabel")) != pair[0]
        or int(row.get("r", -1)) != pair[1]
        or positive_integer(row.get("fieldDiscriminantAbs")) is None
        or positive_integer(row.get("polynomialDiscriminantAbs")) is None
    ):
        return None
    source = root.get("source")
    pin = source_pin_from_nested(source) if isinstance(source, dict) else None
    if pin is None or not pin.get("coefficientSha256"):
        return None
    return candidate_record(
        row=row,
        pair=pair,
        schema="higher_kummer_triple_exact_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=merge_source_pins([pin]),
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "tripleResolventSha256": str(certificate.get("tripleResolventSha256")),
            "uniqueDegree12Factor": True,
        },
    )


def validate_character_maximal_exclusion(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    status_match = STATUS_PAIR_RE.fullmatch(str(row.get("status", "")))
    if (
        status_match is None
        or row.get("irreducible") is not True
        or contradictory_claim(row, ancestors)
    ):
        return None
    pair = valid_pair(status_match.group(1), status_match.group(2))
    payload = coefficient_payload(row)
    maximal = row.get("maximalSubgroupCertificate")
    containment = row.get("containmentProof")
    if pair is None or payload is None:
        return None
    if (
        int(row.get("realRoots", -1)) != pair[1]
        or not isinstance(maximal, dict)
        or maximal.get("complete") is not True
        or positive_integer(maximal.get("checkedSquarefreePrimes")) is None
        or not isinstance(maximal.get("properTransitiveMaximals"), list)
        or not maximal["properTransitiveMaximals"]
        or not all(
            isinstance(value, dict)
            and valid_pair(value.get("label"), 0) is not None
            and isinstance(value.get("witness"), dict)
            and positive_integer(value["witness"].get("prime")) is not None
            for value in maximal["properTransitiveMaximals"]
        )
        or not isinstance(containment, dict)
        or not (
            containment.get("sameDegree12Field") is True
            or containment.get("sameCanonicalDegree12Field") is True
        )
        or not str(containment.get("theorem", "")).strip()
        or positive_integer(row.get("fieldDiscriminantAbs")) is None
        or positive_integer(row.get("polynomialDiscriminantAbs")) is None
    ):
        return None

    root = None
    for value in ancestors:
        audit = value.get("audit")
        if isinstance(audit, dict) and isinstance(audit.get("targetStructure"), dict):
            root = value
            break
    if root is None:
        return None
    audit = root["audit"]
    if (
        str(audit["targetStructure"].get("targetLabel")) != pair[0]
        or int(audit.get("networkCalls", 0)) != 0
        or int(audit.get("submissionCalls", 0)) != 0
    ):
        return None
    source = audit.get("source")
    pin = source_pin_from_nested(source) if isinstance(source, dict) else None
    if pin is None or not pin.get("coefficientSha256") or source.get("scoreable") is not True:
        return None
    return candidate_record(
        row=row,
        pair=pair,
        schema="character_kernel_maximal_exclusion_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=merge_source_pins([pin]),
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "checkedSquarefreePrimes": int(maximal["checkedSquarefreePrimes"]),
            "properTransitiveMaximalsExcluded": len(
                maximal["properTransitiveMaximals"]
            ),
            "targetStructure": {
                key: audit["targetStructure"].get(key)
                for key in (
                    "blockCount",
                    "blockKernelOrder",
                    "groupOrder",
                    "quotientOrder",
                    "quotientT",
                )
            },
        },
    )


def validate_simple_compositum(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    if row.get("status") != "certified_simple_compositum" or contradictory_claim(
        row, ancestors
    ):
        return None
    payload = coefficient_payload(row)
    pair = direct_pair(row)
    proof = row.get("exactLabelProof")
    first = row.get("first")
    second = row.get("second")
    target_group = row.get("targetGroup")
    if payload is None or pair is None:
        return None
    if (
        int(row.get("exactDegreeByLinearDisjointness", -1)) != 24
        or int(row.get("factorDiscriminantGcd", -1)) != 1
        or row.get("duplicateCompositeFieldDiscriminant") is not False
        or not isinstance(proof, dict)
        or proof.get("coprimeFieldDiscriminants") is not True
        or proof.get("galoisClosuresLinearlyDisjoint") is not True
        or not isinstance(first, dict)
        or not isinstance(second, dict)
        or not isinstance(target_group, dict)
        or str(target_group.get("label")) != pair[0]
        or int(target_group.get("t", -1)) != label_t(pair[0])
        or int(first.get("subfieldDegree", -1))
        * int(second.get("subfieldDegree", -1))
        != 24
        or int(first.get("realRoots", -1)) * int(second.get("realRoots", -1))
        != pair[1]
        or not isinstance(first.get("exactProof"), dict)
        or not isinstance(second.get("exactProof"), dict)
        or positive_integer(row.get("fieldDiscriminantAbs")) is None
    ):
        return None
    pins = []
    for source in (first, second):
        pin = source_pin_from_flat(source)
        if pin is None or not pin.get("coefficientSha256"):
            return None
        pins.append(pin)
    return candidate_record(
        row=row,
        pair=pair,
        schema="linearly_disjoint_simple_compositum_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=merge_source_pins(pins),
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "factorDiscriminantGcd": 1,
            "shape": str(row.get("shape")),
            "sourceSubfieldDegrees": [
                int(first["subfieldDegree"]),
                int(second["subfieldDegree"]),
            ],
        },
    )


def exact_factor_degrees(root: dict) -> bool:
    values = root.get("factorDegrees")
    if not isinstance(values, list) or not values:
        return False
    for value in values:
        if not isinstance(value, dict):
            return False
        if positive_integer(value.get("degree")) is None or int(value.get("exponent", -1)) != 1:
            return False
    return True


def validate_higher_kummer_subset(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    payload = coefficient_payload(row)
    pair = direct_pair(row)
    if payload is None or pair is None or contradictory_claim(row, ancestors):
        return None
    root = None
    for value in reversed(ancestors):
        if isinstance(value.get("source"), dict) and value.get("status") in {
            "exact_live_hit",
            "exact_miss_live_signature",
        }:
            if any(value.get(key) is row for key in ("candidate",)) or any(
                isinstance(value.get(key), list) and row in value[key]
                for key in ("candidates", "liveCandidates", "candidateRows")
            ):
                root = value
                break
    if root is None:
        return None
    if (
        int(root.get("networkCalls", -1)) != 0
        or int(root.get("submissionCalls", -1)) != 0
        or int(row.get("r", -1)) != pair[1]
        or positive_integer(row.get("fieldDiscriminantAbs")) is None
        or positive_integer(row.get("polynomialDiscriminantAbs")) is None
    ):
        return None
    source_pin = source_pin_from_nested(root["source"])
    if source_pin is None or not source_pin.get("coefficientSha256"):
        return None

    details: dict[str, Any]
    schema: str
    if isinstance(root.get("candidates"), list) and row in root["candidates"]:
        certificate = root.get("assignmentCertificate")
        if (
            not isinstance(certificate, dict)
            or int(certificate.get("targetLabelAssignmentCount", -1)) != 1
            or positive_integer(certificate.get("actionAssignmentCount")) is None
            or not str(certificate.get("proof", "")).strip()
            or not exact_factor_degrees(root)
        ):
            return None
        schema = "higher_kummer_k3_exact_assignment_v1"
        details = {
            "actionAssignmentCount": int(certificate["actionAssignmentCount"]),
            "targetLabelAssignmentCount": 1,
        }
    elif isinstance(root.get("liveCandidates"), list) and row in root["liveCandidates"]:
        certificate = root.get("assignmentCertificate")
        resolvent = root.get("resolvent")
        if (
            row.get("irreducible") is not True
            or not isinstance(certificate, dict)
            or int(certificate.get("assignmentCount", -1)) != 1
            or not str(certificate.get("proof", "")).strip()
            or not exact_factor_degrees(root)
            or not isinstance(resolvent, dict)
            or int(resolvent.get("degree", -1)) <= 24
            or not SHA_RE.fullmatch(str(resolvent.get("sha256", "")))
        ):
            return None
        schema = "higher_kummer_k4_exact_assignment_v1"
        details = {
            "assignmentCount": 1,
            "resolventDegree": int(resolvent["degree"]),
            "resolventSha256": str(resolvent["sha256"]),
        }
    elif isinstance(root.get("candidateRows"), list) and row in root["candidateRows"]:
        certificate = root.get("exactMechanismCertificate")
        if (
            row.get("irreducible") is not True
            or not isinstance(certificate, dict)
            or certificate.get("assignmentFree") is not True
            or not str(certificate.get("proof", "")).strip()
            or not isinstance(certificate.get("actions"), list)
            or not certificate["actions"]
            or {str(action.get("targetLabel")) for action in certificate["actions"]}
            != {pair[0]}
        ):
            return None
        schema = "higher_kummer_k5_assignment_free_v1"
        details = {
            "actionCount": len(certificate["actions"]),
            "assignmentFree": True,
        }
    else:
        return None
    return candidate_record(
        row=row,
        pair=pair,
        schema=schema,
        line=payload[0],
        digest=payload[1],
        source_pins=merge_source_pins([source_pin]),
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details=details,
    )


def validate_non12_induced_exact(
    row: dict,
    ancestors: tuple[dict, ...],
    source_path: Path,
    record_number: int,
    pointer: str,
) -> dict | None:
    if row.get("status") != "certified_24T21564" or contradictory_claim(row, ancestors):
        return None
    payload = coefficient_payload(row)
    pair = valid_pair("24T21564", row.get("candidateRealRoots"))
    containment = row.get("containmentProof")
    maximal = row.get("maximalSubgroupCertificate")
    outer = row.get("outerInput")
    if payload is None or pair is None:
        return None
    if (
        row.get("candidateIrreducibleDegree24") is not True
        or row.get("absentFromLedger") is not True
        or not isinstance(containment, dict)
        or str(containment.get("constructedTransitiveIdentification")) != pair[0]
        or int(containment.get("constructedOrder", -1)) <= 0
        or not isinstance(maximal, dict)
        or maximal.get("complete") is not True
        or positive_integer(maximal.get("checkedSquarefreePrimes")) is None
        or not isinstance(maximal.get("properTransitiveMaximals"), list)
        or not maximal["properTransitiveMaximals"]
        or positive_integer(row.get("candidateFieldDiscriminantAbs")) is None
        or not isinstance(outer, dict)
    ):
        return None
    pin = source_pin_from_nested(outer)
    if pin is None or not pin.get("coefficientSha256"):
        return None
    pin["r"] = int(outer.get("realRoots", -1))
    return candidate_record(
        row={
            **row,
            "fieldDiscriminantAbs": row["candidateFieldDiscriminantAbs"],
        },
        pair=pair,
        schema="non12_induced_maximal_exclusion_v1",
        line=payload[0],
        digest=payload[1],
        source_pins=merge_source_pins([pin]),
        source_path=source_path,
        record_number=record_number,
        pointer=pointer,
        proof_details={
            "checkedSquarefreePrimes": int(maximal["checkedSquarefreePrimes"]),
            "constructedOrder": int(containment["constructedOrder"]),
            "outerQuotient": str(containment.get("outerQuotient")),
        },
    )


VALIDATORS = (
    validate_pair_sum,
    validate_twist,
    validate_kummer_assignment,
    validate_kummer_triple,
    validate_character_maximal_exclusion,
    validate_simple_compositum,
    validate_higher_kummer_subset,
    validate_non12_induced_exact,
)


def scan_candidates(
    data_dir: Path,
    source_files: Iterable[Path] | None = None,
) -> tuple[list[dict], dict]:
    candidates = []
    schema_counts: Counter = Counter()
    if source_files is None:
        files = sorted(
            path
            for path in data_dir.rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl"}
        )
    else:
        files = sorted(
            path.resolve()
            for path in source_files
            if path.is_file() and path.suffix in {".json", ".jsonl"}
        )
    file_index = []
    for source_path in files:
        file_digest = sha256_path(source_path)
        file_index.append((str(source_path.relative_to(ROOT)), file_digest))
        for record_number, root in read_json_roots(source_path):
            for row, pointer, ancestors in iter_nodes(root):
                if not any(canonical_polynomial_line(row.get(key)) for key in PAYLOAD_KEYS):
                    continue
                accepted = None
                for validator in VALIDATORS:
                    accepted = validator(
                        row, ancestors, source_path, record_number, pointer
                    )
                    if accepted is not None:
                        break
                if accepted is not None:
                    candidates.append(accepted)
                    schema_counts[accepted["proof"]["schema"]] += 1
    index_text = "".join(f"{path}\0{digest}\n" for path, digest in file_index)
    return candidates, {
        "artifactFiles": len(files),
        "artifactFileIndexSha256": sha256_bytes(index_text.encode("utf-8")),
        "acceptedOccurrences": len(candidates),
        "acceptedSchemaCounts": dict(sorted(schema_counts.items())),
    }


def validate_source_pins(connection: sqlite3.Connection, candidate: dict) -> bool:
    pins = candidate["sourcePins"]
    if not pins:
        return False
    for pin in pins:
        row = connection.execute(
            """
            SELECT p.coefficient_hash,v.label,v.r,v.scoreable,v.status
            FROM polynomials p JOIN verifications v
              ON v.submission_id=p.submission_id
             AND v.polynomial_index=p.polynomial_index
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (pin["submissionId"], pin["polynomialIndex"]),
        ).fetchone()
        if row is None or int(row["scoreable"] or 0) != 1:
            return False
        if pin.get("coefficientSha256") is not None and str(
            pin["coefficientSha256"]
        ) != str(row["coefficient_hash"]):
            return False
        if pin.get("label") is not None and str(pin["label"]) != str(row["label"]):
            return False
        if pin.get("r") is not None and int(pin["r"]) != int(row["r"]):
            return False
        pin.update(
            {
                "coefficientSha256": str(row["coefficient_hash"]),
                "label": str(row["label"]),
                "r": int(row["r"]),
                "scoreable": True,
                "status": str(row["status"]),
            }
        )
    return True


def receipt_exclusions(
    receipts_dir: Path,
    data_dir: Path,
    connection: sqlite3.Connection,
    candidate_pair_index: dict[str, set[tuple[str, int]]],
) -> tuple[set[str], set[tuple[str, int]], dict]:
    hashes: set[str] = set()
    pairs: set[tuple[str, int]] = set()
    intact_manifest_hashes: set[str] = set()
    audit = []
    for receipt_path in sorted(receipts_dir.glob("sub_*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        response = receipt.get("response") or {}
        submission_id = str(
            response.get("submissionId")
            or receipt.get("submissionId")
            or receipt_path.stem
        )
        expected = int(receipt.get("polynomials", 0))
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials WHERE submission_id=?",
                (submission_id,),
            )
        }
        hashes.update(ledger_hashes)
        ledger_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE submission_id=? AND label IS NOT NULL AND r IS NOT NULL",
                (submission_id,),
            )
            if valid_pair(label, r) is not None
        }
        pairs.update(ledger_pairs)
        manifest_hashes: set[str] = set()
        manifest_value = receipt.get("manifest")
        recorded_hash = receipt.get("manifestHash")
        provenance = "ledger_submission_rows"
        if manifest_value and recorded_hash:
            manifest = Path(str(manifest_value)).expanduser().resolve()
            if manifest.is_file() and sha256_path(manifest) == str(recorded_hash):
                intact_manifest_hashes.add(str(recorded_hash))
                for raw_line in manifest.read_text(encoding="utf-8").splitlines():
                    line = canonical_polynomial_line(
                        raw_line.split("#", 1)[0].strip()
                    )
                    if line is not None:
                        manifest_hashes.add(sha256_bytes(line.encode("ascii")))
                if len(manifest_hashes) != expected:
                    raise ValueError(
                        f"receipt manifest count mismatch for {receipt_path}: "
                        f"{len(manifest_hashes)} != {expected}"
                    )
                hashes.update(manifest_hashes)
                provenance = "matching_manifest"
        if len(ledger_hashes | manifest_hashes) < expected:
            raise ValueError(
                f"receipt {receipt_path} has {expected} polynomials but only "
                f"{len(ledger_hashes | manifest_hashes)} recoverable hashes"
            )
        audit.append(
            {
                "submissionId": submission_id,
                "declaredPolynomials": expected,
                "ledgerHashes": len(ledger_hashes),
                "manifestHashes": len(manifest_hashes),
                "ledgerPairs": len(ledger_pairs),
                "provenance": provenance,
            }
        )

    # Explicit possible-pair maps conservatively cover queued multi-orbit rows.
    mapped_rows = 0
    for path in sorted(data_dir.rglob("*receipt_mapping*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        artifact_hashes = value.get("artifactSha256")
        manifest_hash = (
            str(artifact_hashes.get("manifest"))
            if isinstance(artifact_hashes, dict) and artifact_hashes.get("manifest")
            else None
        )
        if manifest_hash not in intact_manifest_hashes:
            continue
        rows = value.get("receiptPolynomialPossiblePairs")
        if not isinstance(rows, list):
            continue
        for row in rows:
            digest = str(row.get("coefficientSha256", ""))
            if digest not in hashes:
                raise ValueError(f"receipt pair map references a nonreceipt hash: {path}")
            for text in row.get("possiblePairs") or []:
                match = PAIR_TEXT_RE.fullmatch(str(text))
                if match is None:
                    raise ValueError(f"invalid receipt possible pair in {path}: {text!r}")
                pairs.add((match.group(1), int(match.group(2))))
            mapped_rows += 1

    # Exact SINGLE claims for any intact/ledger receipt hash also exclude pairs.
    for digest in hashes:
        pairs.update(candidate_pair_index.get(digest, set()))
    return hashes, pairs, {
        "receiptCount": len(audit),
        "receiptPolynomialHashes": len(hashes),
        "receiptTargetPairs": len(pairs),
        "queuedPossiblePairMapRows": mapped_rows,
        "audit": audit,
    }


def query_known_hashes(
    connection: sqlite3.Connection, hashes: set[str]
) -> set[str]:
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


def best_key(candidate: dict) -> tuple:
    infinity = 10**100000
    return (
        positive_integer(candidate.get("fieldDiscriminantAbs")) or infinity,
        positive_integer(candidate.get("polynomialDiscriminantAbs")) or infinity,
        int(candidate["coefficientBytes"]),
        candidate["coefficientSha256"],
    )


def write_atomic(path: Path, text: str) -> None:
    destination = path.expanduser().resolve()
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


def stage(args: argparse.Namespace) -> dict:
    outputs = [args.manifest.resolve(), args.certificate.resolve(), args.summary.resolve()]
    if len(set(outputs)) != len(outputs):
        raise ValueError("manifest, certificate, and summary must be distinct")
    for output in outputs:
        if output.exists():
            raise ValueError(f"refusing to overwrite sealed output: {output}")
    if args.database.resolve() in outputs:
        raise ValueError("an output may not overwrite the ledger")

    candidates, corpus = scan_candidates(args.data)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for candidate in candidates:
        pair_index[candidate["coefficientSha256"]].add(
            (candidate["targetLabel"], candidate["targetR"])
        )
    ambiguous_hashes = {
        digest for digest, pairs in pair_index.items() if len(pairs) != 1
    }

    connection = sqlite3.connect(
        f"file:{args.database.expanduser().resolve()}?immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        known_hashes = query_known_hashes(connection, set(pair_index))
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        targets = {
            (str(row["label"]), int(row["r"])): dict(row)
            for row in connection.execute("SELECT * FROM targets")
        }
        receipt_hashes, receipt_pairs, receipt_audit = receipt_exclusions(
            args.receipts, args.data, connection, pair_index
        )

        skips: Counter = Counter()
        eligible = []
        seen_occurrences = set()
        for candidate in candidates:
            occurrence_key = (
                candidate["coefficientSha256"],
                candidate["targetLabel"],
                candidate["targetR"],
                candidate["proof"]["artifact"],
                candidate["proof"]["record"],
                candidate["proof"]["pointer"],
            )
            if occurrence_key in seen_occurrences:
                skips["duplicate_proof_occurrence"] += 1
                continue
            seen_occurrences.add(occurrence_key)
            digest = candidate["coefficientSha256"]
            pair = (candidate["targetLabel"], candidate["targetR"])
            if digest in ambiguous_hashes:
                skips["ambiguous_exact_pair_claim"] += 1
                continue
            if digest in receipt_hashes:
                skips["receipt_hash"] += 1
                continue
            if pair in receipt_pairs:
                skips["receipt_pair"] += 1
                continue
            if pair in baseline:
                skips["baseline_pair"] += 1
                continue
            if pair in owned:
                skips["locally_owned_pair"] += 1
                continue
            if digest in known_hashes:
                skips["known_polynomial_hash"] += 1
                continue
            target = targets.get(pair)
            if target is None:
                skips["target_missing"] += 1
                continue
            team_count = int(target["team_count"])
            if args.min_team_count is not None and team_count < args.min_team_count:
                skips["below_team_count_floor"] += 1
                continue
            if args.max_team_count is not None and team_count > args.max_team_count:
                skips["above_team_count_cap"] += 1
                continue
            if not validate_source_pins(connection, candidate):
                skips["invalid_or_unscoreable_source_provenance"] += 1
                continue
            projection = Fraction(1, 2**team_count)
            eligible.append(
                {
                    **candidate,
                    "targetTeamCount": team_count,
                    "targetGeneratedAt": target["generated_at"],
                    "targetMinimumDiscAbs": (
                        str(target["minimum_disc_abs"])
                        if target["minimum_disc_abs"] is not None
                        else None
                    ),
                    "projectedMarginalScore": float(projection),
                    "projectedMarginalScoreExact": str(projection),
                }
            )
    finally:
        connection.close()

    best_by_pair: dict[tuple[str, int], dict] = {}
    for candidate in eligible:
        pair = (candidate["targetLabel"], candidate["targetR"])
        incumbent = best_by_pair.get(pair)
        if incumbent is None or best_key(candidate) < best_key(incumbent):
            if incumbent is not None:
                skips["duplicate_target_pair"] += 1
            best_by_pair[pair] = candidate
        else:
            skips["duplicate_target_pair"] += 1

    selected = sorted(
        best_by_pair.values(),
        key=lambda row: (
            row["targetTeamCount"],
            row["targetT"],
            row["targetR"],
            row["coefficientSha256"],
        ),
    )
    if not selected and not args.allow_empty:
        raise ValueError("no exact SINGLE-output candidates remain after exclusions")
    manifest_text = "".join(row["coefficientLine"] + "\n" for row in selected)
    if len({row["coefficientSha256"] for row in selected}) != len(selected):
        raise ValueError("one polynomial hash remains assigned more than once")
    projection = sum(
        (Fraction(1, 2 ** row["targetTeamCount"]) for row in selected),
        Fraction(0, 1),
    )
    distribution = Counter(row["targetTeamCount"] for row in selected)
    schema_distribution = Counter(row["proof"]["schema"] for row in selected)

    public_rows = []
    for row in selected:
        public_rows.append(
            {
                key: value
                for key, value in row.items()
                if key != "coefficientLine"
            }
        )
    certificate = {
        "method": "offline-exact-single-output-sealed-stage-v1",
        "checks": {
            "allPayloadsCanonicalPrimitiveMonicDegree24AndHashMatched": True,
            "allTargetsCarryAllowlistedExactSingleOutputProofs": True,
            "allApplicableSourcesRejoinedToScoreablePinnedLedgerRows": True,
            "baselineOwnedKnownAndAllReceiptHashesPairsExcluded": True,
            "bestPayloadPerTargetPairSelected": True,
            "certificateContainsNoCoefficientPayload": True,
        },
        "database": str(args.database.resolve().relative_to(ROOT)),
        "databaseSha256": sha256_path(args.database),
        "artifactCorpus": corpus,
        "receiptExclusion": receipt_audit,
        "teamCountBounds": {
            "minimum": args.min_team_count,
            "maximum": args.max_team_count,
        },
        "selected": public_rows,
        "selectedCount": len(selected),
        "scoreProjection": {
            "formula": "2^(-current_cached_team_count), before discriminant penalty",
            "marginalScore": float(projection),
            "marginalScoreExact": str(projection),
            "teamCountDistribution": {
                str(key): distribution[key] for key in sorted(distribution)
            },
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    summary = {
        "method": certificate["method"],
        "status": "sealed_not_submitted",
        "manifest": str(args.manifest.resolve().relative_to(ROOT)),
        "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
        "manifestBytes": len(manifest_text.encode("utf-8")),
        "certificate": str(args.certificate.resolve().relative_to(ROOT)),
        "certificateSha256": sha256_bytes(certificate_text.encode("utf-8")),
        "summary": str(args.summary.resolve().relative_to(ROOT)),
        "selected": len(selected),
        "selectedDistinctPairs": len(best_by_pair),
        "selectedSchemaCounts": dict(sorted(schema_distribution.items())),
        "scoreProjection": certificate["scoreProjection"],
        "skipCounts": dict(sorted(skips.items())),
        "artifactCorpus": corpus,
        "receiptExclusion": {
            key: value for key, value in receipt_audit.items() if key != "audit"
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    write_atomic(args.manifest, manifest_text)
    write_atomic(args.certificate, certificate_text)
    write_atomic(args.summary, summary_text)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--min-team-count", type=int)
    parser.add_argument("--max-team-count", type=int)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Write a coefficient-free zero-frontier audit and empty manifest.",
    )
    args = parser.parse_args()
    if args.min_team_count is not None and args.min_team_count < 0:
        raise ValueError("minimum team count must be nonnegative")
    if args.max_team_count is not None and args.max_team_count < 0:
        raise ValueError("maximum team count must be nonnegative")
    if (
        args.min_team_count is not None
        and args.max_team_count is not None
        and args.min_team_count > args.max_team_count
    ):
        raise ValueError("minimum team count exceeds maximum team count")
    summary = stage(args)
    print(
        json.dumps(
            {
                "certificate": str(args.certificate.resolve()),
                "manifest": str(args.manifest.resolve()),
                "manifestSha256": summary["manifestSha256"],
                "scoreProjection": summary["scoreProjection"],
                "selected": summary["selected"],
                "summary": str(args.summary.resolve()),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

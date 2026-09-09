#!/usr/bin/env sage -python
"""Strictly derive the queued 24T15337/r20 -> 24T15437/r20 pair sibling.

This is intentionally a one-source worker.  It reads a raw queued row from the
local ledger in SQLite read-only mode, validates two pinned JSON certificates
and one pinned GAP orbit census, independently reproduces the queued signature
profile, and then performs one exact unordered-pair resolvent computation.

The worker has no network or submission code.  Its only write is the atomic
publication of one fixed JSONL result under ``data/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sage.all import PolynomialRing, ZZ, libgap, pari
from sage.env import SAGE_VERSION


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "ledger.sqlite3"
SOURCE_CERTIFICATE_PATH = DATA / "f5_28_derivative_24T15337_r20_certificate.json"
SIGNATURE_CERTIFICATE_PATH = (
    DATA / "queued_15337_r20_pair_signature_20260729_certificate.json"
)
ORBIT_CENSUS_PATH = (
    DATA / "campaign_20260727_f627" / "f6_post22_pair_revival_census.jsonl"
)
SIGNATURE_WORKER_PATH = ROOT / "pair_signature_one.sage.py"
SOURCE_MANIFEST_PATH = ROOT / "outbox" / "agent_f5_28_derivative_24T15337_r20.txt"
OUTPUT_PATH = DATA / "queued_15337_r20_to_15437_r20_pair_sum_20260729.jsonl"

EXPECTED_SUBMISSION_ID = "sub_2602de74812a402e9ed473a6a6dde981"
EXPECTED_POLYNOMIAL_INDEX = 4
EXPECTED_SOURCE_LABEL = "24T15337"
EXPECTED_SOURCE_T = 15337
EXPECTED_SOURCE_R = 20
EXPECTED_SOURCE_SHA256 = (
    "6fe04f8a3e089f7e85420da2b6d756dbefac6789dc8611f766b0593309bd9650"
)
EXPECTED_TARGET_LABEL = "24T15437"
EXPECTED_TARGET_T = 15437
EXPECTED_TARGET_R = 20
EXPECTED_TRANSFORM = 1

EXPECTED_SOURCE_CERTIFICATE_SHA256 = (
    "598d95483d2bf152bf8c6dff8d95de73b4d2860ce4f08383fd77c01c4050d1b3"
)
EXPECTED_SIGNATURE_CERTIFICATE_SHA256 = (
    "b3ecde686ed7557ae922788a56307e5fca767bb7291e7690a2770f8f05e94887"
)
EXPECTED_ORBIT_CENSUS_SHA256 = (
    "2cc1eb8503f8fa5157b386fcce2a3b7bc1461751ccfa279329ab9b91f7dacc8c"
)
EXPECTED_ORBIT_ROW_NUMBER = 84
EXPECTED_ORBIT_ROW_CANONICAL_SHA256 = (
    "924cdb73a2f7f3188e44695e931737f1b15e93697dc3ca52b46d08155d674359"
)
EXPECTED_ORBIT_EXACT_CERTIFICATE_SHA256 = (
    "b6d585aa49bde7886cee140266fdef19553495fdb11e5e9ec2f88c780c7eb525"
)
EXPECTED_SIGNATURE_WORKER_SHA256 = (
    "1bdbe729dcc1e9861b22606dcc67bf86035e6c99664e2008fd553f1e00a9ee2b"
)
EXPECTED_SIGNATURE_OUTPUT_SHA256 = (
    "898a47e61e318d877ed249d2dbe71c8858deddc50105aa84c2d58272f6470ebf"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "e82d546022a6842c1af79b134c998fe27591c0305baa735cfdcec48f6c4abc8e"
)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha256(value) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return sha256_bytes(payload.encode("utf-8"))


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain one JSON object")
    return value


def coefficient_list(line: str, description: str) -> list[int]:
    if not isinstance(line, str) or not line:
        raise ValueError(f"{description} coefficient line is absent")
    try:
        values = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError(f"{description} coefficient line contains a noninteger") from exc
    if len(values) != 25 or values[-1] != 1:
        raise ValueError(f"{description} is not monic of degree 24")
    if values[0] == 0 or math.gcd(*values) != 1:
        raise ValueError(f"{description} is not primitive with nonzero constant term")
    return values


def coefficient_line(polynomial) -> str:
    values = [int(value) for value in polynomial.list()]
    values += [0] * (25 - len(values))
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("derived candidate is not monic of degree 24")
    if values[0] == 0 or math.gcd(*values) != 1:
        raise ValueError("derived candidate is not primitive with nonzero constant term")
    return ",".join(str(value) for value in values)


def validate_source_certificate() -> tuple[str, dict]:
    byte_sha = sha256_path(SOURCE_CERTIFICATE_PATH)
    if byte_sha != EXPECTED_SOURCE_CERTIFICATE_SHA256:
        raise ValueError("exact source certificate byte digest mismatch")
    certificate = read_json(SOURCE_CERTIFICATE_PATH)
    if int(certificate.get("networkCalls", -1)) != 0:
        raise ValueError("exact source certificate records network calls")
    if int(certificate.get("submissionCalls", -1)) != 0:
        raise ValueError("exact source certificate records submission calls")

    target = certificate.get("target")
    if target != {
        "label": EXPECTED_SOURCE_LABEL,
        "r": EXPECTED_SOURCE_R,
        "t": EXPECTED_SOURCE_T,
    }:
        raise ValueError("exact source certificate target label/T/r mismatch")

    candidate = certificate.get("candidate")
    if not isinstance(candidate, dict):
        raise ValueError("exact source certificate has no candidate")
    required_candidate = {
        "coefficientSha256": EXPECTED_SOURCE_SHA256,
        "degree": 24,
        "irreducible": True,
        "monic": True,
        "r": EXPECTED_SOURCE_R,
    }
    for key, expected in required_candidate.items():
        if candidate.get(key) != expected:
            raise ValueError(f"exact source candidate {key} mismatch")
    line = str(candidate.get("coefficientLine"))
    values = coefficient_list(line, "exact source candidate")
    if sha256_bytes(line.encode("utf-8")) != EXPECTED_SOURCE_SHA256:
        raise ValueError("exact source candidate coefficient digest mismatch")
    if any(values[index] != 0 for index in range(1, 24, 2)):
        raise ValueError("exact source candidate is not the certified even lift")

    factorization = certificate.get("factorization")
    if not isinstance(factorization, dict):
        raise ValueError("exact source certificate has no factorization")
    expected_degrees = [
        {"degree": 6, "exponent": 1, "index": 0},
        {"degree": 12, "exponent": 1, "index": 1},
        {"degree": 48, "exponent": 1, "index": 2},
    ]
    if factorization.get("factorDegrees") != expected_degrees:
        raise ValueError("exact source factor-degree certificate mismatch")
    if int(factorization.get("selectedDegree12FactorIndex", -1)) != 1:
        raise ValueError("exact source selected factor index mismatch")
    factor_line = str(factorization.get("selectedDegree12FactorLine"))
    if (
        sha256_bytes(factor_line.encode("utf-8"))
        != str(factorization.get("selectedDegree12FactorSha256"))
        or str(factorization.get("selectedDegree12FactorSha256"))
        != "55c5c485df1141014575f3c9b1e198ebf8556cd04595ad1d53285427df1536ce"
    ):
        raise ValueError("exact source selected factor digest mismatch")
    factor_values = [int(value) for value in factor_line.split(",")]
    if len(factor_values) != 13 or factor_values[-1] != 1:
        raise ValueError("exact source selected factor is not monic degree 12")
    lifted_values = [
        factor_values[index // 2] if index % 2 == 0 else 0 for index in range(25)
    ]
    if lifted_values != values:
        raise ValueError("selected degree-12 factor does not reconstruct queued source")

    action = certificate.get("exactAction")
    if not isinstance(action, dict):
        raise ValueError("exact source certificate has no action")
    expected_action_scalars = {
        "mechanism": "full-ledger H-equivariant unordered-pair product",
        "sourceLabel": "24T19738",
        "sourceT": 19738,
        "targetKernelOrder": 256,
        "targetLabel": EXPECTED_SOURCE_LABEL,
        "targetOrder": 49152,
        "targetT": EXPECTED_SOURCE_T,
    }
    for key, expected in expected_action_scalars.items():
        if action.get(key) != expected:
            raise ValueError(f"exact source action {key} mismatch")
    if len(action.get("pairOrbit", [])) != 12:
        raise ValueError("exact source action pair orbit has the wrong size")
    if len(action.get("incidenceRows", [])) != 12:
        raise ValueError("exact source action incidence certificate has the wrong size")

    manifest_name = str(certificate.get("manifest"))
    if manifest_name != "outbox/agent_f5_28_derivative_24T15337_r20.txt":
        raise ValueError("exact source certificate manifest path mismatch")
    if str(certificate.get("manifestSha256")) != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise ValueError("exact source certificate manifest digest field mismatch")
    if sha256_path(SOURCE_MANIFEST_PATH) != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise ValueError("exact source manifest byte digest mismatch")
    manifest_lines = SOURCE_MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
    if manifest_lines != [line]:
        raise ValueError("exact source manifest does not contain only the certified line")

    return line, {
        "path": str(SOURCE_CERTIFICATE_PATH),
        "sha256": byte_sha,
        "canonicalSha256": canonical_sha256(certificate),
        "manifest": {
            "path": str(SOURCE_MANIFEST_PATH),
            "sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
        },
        "selectedDegree12FactorSha256": str(
            factorization["selectedDegree12FactorSha256"]
        ),
        "pairResolventCoefficientSha256": str(
            factorization["pairResolventCoefficientSha256"]
        ),
    }


def fixed_points(permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def reproduce_signature_profiles() -> dict:
    group = libgap.TransitiveGroup(24, EXPECTED_SOURCE_T)
    pairs = libgap.Combinations(libgap.eval("[1..24]"), 2)
    pair_orbits = list(libgap.Orbits(group, pairs, libgap.OnSets))
    length_24 = []
    for orbit_index, orbit in enumerate(pair_orbits):
        if int(libgap.Length(orbit)) != 24:
            continue
        homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(homomorphism)
        length_24.append(
            {
                "orbitIndex": orbit_index,
                "targetLabel": f"24T{int(libgap.TransitiveIdentification(image))}",
                "homomorphism": homomorphism,
            }
        )

    profiles = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        orbit_signatures = []
        for orbit in length_24:
            image_element = libgap.Image(orbit["homomorphism"], representative)
            orbit_signatures.append(
                {
                    "orbitIndex": orbit["orbitIndex"],
                    "targetLabel": orbit["targetLabel"],
                    "targetR": fixed_points(image_element, 24),
                }
            )
        profiles.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "order": order,
                "sourceR": fixed_points(representative, 24),
                "orbitSignatures": orbit_signatures,
            }
        )
    return {
        "sourceLabel": EXPECTED_SOURCE_LABEL,
        "sourceT": EXPECTED_SOURCE_T,
        "length24OrbitCount": len(length_24),
        "profiles": profiles,
    }


def validate_signature_certificate() -> tuple[dict, dict]:
    byte_sha = sha256_path(SIGNATURE_CERTIFICATE_PATH)
    if byte_sha != EXPECTED_SIGNATURE_CERTIFICATE_SHA256:
        raise ValueError("queued signature certificate byte digest mismatch")
    certificate = read_json(SIGNATURE_CERTIFICATE_PATH)
    if int(certificate.get("networkCalls", -1)) != 0:
        raise ValueError("queued signature certificate records network calls")
    if int(certificate.get("submissionCalls", -1)) != 0:
        raise ValueError("queued signature certificate records submission calls")

    expected_checks = {
        "coefficientMaterialIncluded": False,
        "currentTargetIsNonbaseline": True,
        "currentTargetIsNotLocallyOwnedScoreable": True,
        "currentTargetIsTc0AndUndiscovered": True,
        "exactlyOneLength24Orbit": True,
        "exactlyOneSourceR20ConjugacyClass": True,
        "sourceIsQueuedNotAccepted": True,
        "sourceR20MapsDeterministicallyToTargetR20": True,
    }
    if certificate.get("checks") != expected_checks:
        raise ValueError("queued signature certificate check envelope mismatch")

    source = certificate.get("source")
    if not isinstance(source, dict):
        raise ValueError("queued signature certificate has no source")
    expected_source_scalars = {
        "coefficientSha256": EXPECTED_SOURCE_SHA256,
        "label": EXPECTED_SOURCE_LABEL,
        "polynomialIndex": EXPECTED_POLYNOMIAL_INDEX,
        "r": EXPECTED_SOURCE_R,
        "sourceCertificate": "data/f5_28_derivative_24T15337_r20_certificate.json",
        "sourceCertificateSha256": EXPECTED_SOURCE_CERTIFICATE_SHA256,
        "submissionId": EXPECTED_SUBMISSION_ID,
        "t": EXPECTED_SOURCE_T,
    }
    for key, expected in expected_source_scalars.items():
        if source.get(key) != expected:
            raise ValueError(f"queued signature source {key} mismatch")
    expected_queue_state = {
        "failedCount": 0,
        "queuedCount": 8,
        "updatedAt": "2026-07-28T06:06:45Z",
        "verifiedCount": 0,
    }
    if source.get("submissionStateAtSeal") != expected_queue_state:
        raise ValueError("queued signature sealed submission state mismatch")

    orbit = certificate.get("orbitCertificate")
    if not isinstance(orbit, dict):
        raise ValueError("queued signature certificate has no orbit certificate")
    expected_orbit_scalars = {
        "artifact": "data/campaign_20260727_f627/f6_post22_pair_revival_census.jsonl",
        "artifactRecord": EXPECTED_ORBIT_ROW_NUMBER,
        "artifactSha256": EXPECTED_ORBIT_CENSUS_SHA256,
        "exactCertificateSha256": EXPECTED_ORBIT_EXACT_CERTIFICATE_SHA256,
        "length24OrbitCount": 1,
        "orbitSizes": [12, 24, 48, 192],
        "sourceOrder": 49152,
    }
    for key, expected in expected_orbit_scalars.items():
        if orbit.get(key) != expected:
            raise ValueError(f"queued signature orbit {key} mismatch")
    expected_orbit_target = {
        "imageOrder": 49152,
        "kernelOrder": 1,
        "orbitIndex": 1,
        "orbitSize": 24,
        "targetLabel": EXPECTED_TARGET_LABEL,
        "targetT": EXPECTED_TARGET_T,
    }
    if orbit.get("target") != expected_orbit_target:
        raise ValueError("queued signature orbit target mismatch")

    result = certificate.get("result")
    if not isinstance(result, dict):
        raise ValueError("queued signature certificate has no result")
    expected_profile = {
        "classIndex": 17,
        "classSize": 6,
        "orbitSignatures": [
            {
                "orbitIndex": 1,
                "targetLabel": EXPECTED_TARGET_LABEL,
                "targetR": EXPECTED_TARGET_R,
            }
        ],
        "order": 2,
        "sourceR": EXPECTED_SOURCE_R,
    }
    if result.get("deterministicTargetPair") != f"{EXPECTED_TARGET_LABEL}/r20":
        raise ValueError("queued signature deterministic target pair mismatch")
    if int(result.get("sourceR20ProfileCount", -1)) != 1:
        raise ValueError("queued signature source-r20 profile count mismatch")
    if result.get("sourceR20Profile") != expected_profile:
        raise ValueError("queued signature source-r20 profile mismatch")

    proof = certificate.get("signatureProof")
    if not isinstance(proof, dict):
        raise ValueError("queued signature certificate has no proof")
    if sha256_path(SIGNATURE_WORKER_PATH) != EXPECTED_SIGNATURE_WORKER_SHA256:
        raise ValueError("pinned signature worker byte digest mismatch")
    expected_proof_scalars = {
        "canonicalFullOutputSha256": EXPECTED_SIGNATURE_OUTPUT_SHA256,
        "command": "sage -python pair_signature_one.sage.py 24T15337 15337",
        "fullProfileCount": 28,
        "script": "pair_signature_one.sage.py",
        "scriptSha256": EXPECTED_SIGNATURE_WORKER_SHA256,
    }
    for key, expected in expected_proof_scalars.items():
        if proof.get(key) != expected:
            raise ValueError(f"queued signature proof {key} mismatch")
    software = proof.get("software")
    gap_version = str(libgap.eval("GAPInfo.Version"))
    if software != {"gapVersion": gap_version, "sageVersion": str(SAGE_VERSION)}:
        raise ValueError("queued signature software version mismatch")

    reproduced = reproduce_signature_profiles()
    if canonical_sha256(reproduced) != EXPECTED_SIGNATURE_OUTPUT_SHA256:
        raise ValueError("independent queued signature reproduction digest mismatch")
    if len(reproduced["profiles"]) != 28:
        raise ValueError("independent queued signature profile count mismatch")
    matching = [
        profile
        for profile in reproduced["profiles"]
        if int(profile["sourceR"]) == EXPECTED_SOURCE_R
    ]
    if matching != [expected_profile]:
        raise ValueError("independent source-r20 signature profile mismatch")

    snapshot = certificate.get("targetSnapshot")
    expected_snapshot = {
        "baseline": False,
        "discovered": False,
        "generatedAt": "2026-07-29T04:41:01Z",
        "label": EXPECTED_TARGET_LABEL,
        "locallyOwnedScoreableCount": 0,
        "minimumDiscAbs": None,
        "r": EXPECTED_TARGET_R,
        "t": EXPECTED_TARGET_T,
        "teamCount": 0,
    }
    if snapshot != expected_snapshot:
        raise ValueError("queued signature target snapshot mismatch")

    return certificate, {
        "path": str(SIGNATURE_CERTIFICATE_PATH),
        "sha256": byte_sha,
        "canonicalSha256": canonical_sha256(certificate),
        "signatureWorker": {
            "path": str(SIGNATURE_WORKER_PATH),
            "sha256": EXPECTED_SIGNATURE_WORKER_SHA256,
        },
        "reproducedCanonicalOutputSha256": canonical_sha256(reproduced),
        "sourceR20Profile": expected_profile,
        "software": software,
    }


def validate_orbit_census(signature_certificate: dict) -> tuple[dict, dict]:
    byte_sha = sha256_path(ORBIT_CENSUS_PATH)
    if byte_sha != EXPECTED_ORBIT_CENSUS_SHA256:
        raise ValueError("pinned orbit census byte digest mismatch")
    rows = []
    for line_number, line in enumerate(
        ORBIT_CENSUS_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"orbit census row {line_number} is not an object")
        rows.append((line_number, value))
    matching = [
        (line_number, row)
        for line_number, row in rows
        if row.get("sourceLabel") == EXPECTED_SOURCE_LABEL
    ]
    if len(matching) != 1:
        raise ValueError("pinned orbit census lacks one unique source row")
    line_number, row = matching[0]
    if line_number != EXPECTED_ORBIT_ROW_NUMBER:
        raise ValueError("pinned orbit census source row ordinal mismatch")
    if canonical_sha256(row) != EXPECTED_ORBIT_ROW_CANONICAL_SHA256:
        raise ValueError("pinned orbit census source row digest mismatch")
    expected_scalars = {
        "actionKind": "unordered_pair_missing_from_prior_maps",
        "exactCertificateSha256": EXPECTED_ORBIT_EXACT_CERTIFICATE_SHA256,
        "length24OrbitCount": 1,
        "orbitSizes": [12, 24, 48, 192],
        "sourceLabel": EXPECTED_SOURCE_LABEL,
        "sourceOrder": 49152,
        "sourceT": EXPECTED_SOURCE_T,
        "status": "certified",
        "targetCounts": {EXPECTED_TARGET_LABEL: 1},
    }
    for key, expected in expected_scalars.items():
        if row.get(key) != expected:
            raise ValueError(f"pinned orbit census {key} mismatch")
    if sum(int(value) for value in row["orbitSizes"]) != 24 * 23 // 2:
        raise ValueError("pinned orbit sizes do not sum to 276")
    if row["orbitSizes"].count(24) != 1:
        raise ValueError("pinned orbit census does not have one length-24 orbit")
    expected_target = signature_certificate["orbitCertificate"]["target"]
    if row.get("targets") != [expected_target]:
        raise ValueError("pinned orbit census and signature certificate disagree")
    return row, {
        "path": str(ORBIT_CENSUS_PATH),
        "sha256": byte_sha,
        "record": line_number,
        "recordCanonicalSha256": canonical_sha256(row),
        "exactCertificateSha256": str(row["exactCertificateSha256"]),
    }


def read_only_connection() -> sqlite3.Connection:
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def validate_ledger_queue(line: str, signature_certificate: dict) -> dict:
    with read_only_connection() as connection:
        submission = connection.execute(
            """
            SELECT submission_id,created_at,updated_at,queued_count,
                   verified_count,failed_count,raw_json
            FROM submissions WHERE submission_id=?
            """,
            (EXPECTED_SUBMISSION_ID,),
        ).fetchone()
        if submission is None:
            raise ValueError("queued submission is absent from the local ledger")
        if (
            str(submission["submission_id"]) != EXPECTED_SUBMISSION_ID
            or str(submission["created_at"]) != "2026-07-28T06:06:45Z"
            or str(submission["updated_at"]) != "2026-07-28T06:06:45Z"
            or int(submission["queued_count"]) != 8
            or int(submission["verified_count"]) != 0
            or int(submission["failed_count"]) != 0
        ):
            raise ValueError("current submission is not in the pinned all-queued state")
        sealed = signature_certificate["source"]["submissionStateAtSeal"]
        if (
            int(submission["queued_count"]) != int(sealed["queuedCount"])
            or int(submission["verified_count"]) != int(sealed["verifiedCount"])
            or int(submission["failed_count"]) != int(sealed["failedCount"])
            or str(submission["updated_at"]) != str(sealed["updatedAt"])
        ):
            raise ValueError("current queue state differs from the signature seal")

        raw_text = str(submission["raw_json"])
        raw = json.loads(raw_text)
        if (
            raw.get("submissionId") != EXPECTED_SUBMISSION_ID
            or raw.get("createdAt") != "2026-07-28T06:06:45Z"
            or raw.get("updatedAt") != "2026-07-28T06:06:45Z"
            or raw.get("verifiedPolynomials") != []
            or raw.get("failedPolynomials") != []
        ):
            raise ValueError("raw queued submission envelope mismatch")
        payload = raw.get("payload")
        queued = payload.get("queuedPolynomials") if isinstance(payload, dict) else None
        expected_queued = [
            {"polynomialIndex": index, "status": "queued"} for index in range(8)
        ]
        if queued != expected_queued:
            raise ValueError("raw queued-polynomial list mismatch")

        polynomial = connection.execute(
            """
            SELECT original_line,coefficients,coefficient_hash
            FROM polynomials
            WHERE submission_id=? AND polynomial_index=?
            """,
            (EXPECTED_SUBMISSION_ID, EXPECTED_POLYNOMIAL_INDEX),
        ).fetchone()
        if polynomial is None:
            raise ValueError("raw queued polynomial row is absent")
        if (
            str(polynomial["original_line"]) != line
            or str(polynomial["coefficients"]) != line
            or str(polynomial["coefficient_hash"]) != EXPECTED_SOURCE_SHA256
            or sha256_bytes(str(polynomial["original_line"]).encode("utf-8"))
            != EXPECTED_SOURCE_SHA256
        ):
            raise ValueError("raw queued polynomial coefficient/hash mismatch")
        indexes = [
            int(row[0])
            for row in connection.execute(
                "SELECT polynomial_index FROM polynomials "
                "WHERE submission_id=? ORDER BY polynomial_index",
                (EXPECTED_SUBMISSION_ID,),
            )
        ]
        if indexes != list(range(8)):
            raise ValueError("local queued submission polynomial index set mismatch")
        verification_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM verifications "
                "WHERE submission_id=? AND polynomial_index=?",
                (EXPECTED_SUBMISSION_ID, EXPECTED_POLYNOMIAL_INDEX),
            ).fetchone()[0]
        )
        failure_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM failures "
                "WHERE submission_id=? AND polynomial_index=?",
                (EXPECTED_SUBMISSION_ID, EXPECTED_POLYNOMIAL_INDEX),
            ).fetchone()[0]
        )
        if verification_count != 0 or failure_count != 0:
            raise ValueError("source row is no longer strictly queued")

        target = connection.execute(
            """
            SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
            FROM targets WHERE label=? AND r=?
            """,
            (EXPECTED_TARGET_LABEL, EXPECTED_TARGET_R),
        ).fetchone()
        if target is None:
            raise ValueError("target pair is absent from the local target snapshot")
        target_dict = {
            "label": str(target["label"]),
            "t": int(target["t"]),
            "r": int(target["r"]),
            "teamCount": int(target["team_count"]),
            "minimumDiscAbs": target["minimum_disc_abs"],
            "discovered": bool(target["discovered"]),
            "generatedAt": str(target["generated_at"]),
        }
        expected_snapshot = signature_certificate["targetSnapshot"]
        if target_dict != {
            "label": expected_snapshot["label"],
            "t": expected_snapshot["t"],
            "r": expected_snapshot["r"],
            "teamCount": expected_snapshot["teamCount"],
            "minimumDiscAbs": expected_snapshot["minimumDiscAbs"],
            "discovered": expected_snapshot["discovered"],
            "generatedAt": expected_snapshot["generatedAt"],
        }:
            raise ValueError("local target row differs from the sealed target snapshot")
        baseline_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                (EXPECTED_TARGET_LABEL, EXPECTED_TARGET_R),
            ).fetchone()[0]
        )
        owned_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM verifications "
                "WHERE label=? AND r=? AND scoreable=1",
                (EXPECTED_TARGET_LABEL, EXPECTED_TARGET_R),
            ).fetchone()[0]
        )
        if baseline_count != 0 or owned_count != 0:
            raise ValueError("target pair is baseline or locally owned")

    evidence = {
        "database": str(DB_PATH),
        "submissionId": EXPECTED_SUBMISSION_ID,
        "polynomialIndex": EXPECTED_POLYNOMIAL_INDEX,
        "coefficientSha256": EXPECTED_SOURCE_SHA256,
        "createdAt": str(submission["created_at"]),
        "updatedAt": str(submission["updated_at"]),
        "queuedCount": int(submission["queued_count"]),
        "verifiedCount": int(submission["verified_count"]),
        "failedCount": int(submission["failed_count"]),
        "rawSubmissionSha256": sha256_bytes(raw_text.encode("utf-8")),
        "rawPolynomialState": "queued",
        "verificationRows": verification_count,
        "failureRows": failure_count,
        "targetSnapshot": target_dict
        | {
            "baseline": False,
            "locallyOwnedScoreableCount": owned_count,
        },
    }
    evidence["stateCanonicalSha256"] = canonical_sha256(evidence)
    return evidence


def root_power_sums(coefficients: list[int], maximum: int) -> list[int]:
    degree = len(coefficients) - 1
    descending_tail = [0] + [
        coefficients[degree - index] for index in range(1, degree + 1)
    ]
    powers = [0] * (maximum + 1)
    powers[0] = degree
    for k in range(1, maximum + 1):
        if k <= degree:
            value = sum(
                descending_tail[index] * powers[k - index]
                for index in range(1, k)
            )
            value += k * descending_tail[k]
        else:
            value = sum(
                descending_tail[index] * powers[k - index]
                for index in range(1, degree + 1)
            )
        powers[k] = -value
    return powers


def transformed_power_sums(
    root_powers: list[int], maximum: int, transform: int
) -> list[int]:
    transformed = [0] * (maximum + 1)
    transformed[0] = root_powers[0]
    for k in range(1, maximum + 1):
        transformed[k] = sum(
            math.comb(k, exponent)
            * (transform**exponent)
            * root_powers[k + exponent]
            for exponent in range(k + 1)
        )
    return transformed


def pair_sum_resolvent(ring, transformed_powers: list[int], degree: int):
    pair_powers = [0] * (degree + 1)
    pair_powers[0] = degree
    for power in range(1, degree + 1):
        numerator = sum(
            math.comb(power, exponent)
            * transformed_powers[exponent]
            * transformed_powers[power - exponent]
            for exponent in range(power + 1)
        ) - (2**power) * transformed_powers[power]
        if numerator % 2:
            raise ArithmeticError(f"nonintegral pair power sum at {power}")
        pair_powers[power] = numerator // 2

    elementary = [0] * (degree + 1)
    elementary[0] = 1
    for k in range(1, degree + 1):
        numerator = sum(
            ((-1) ** (power - 1))
            * elementary[k - power]
            * pair_powers[power]
            for power in range(1, k + 1)
        )
        if numerator % k:
            raise ArithmeticError(f"nonintegral elementary symmetric sum at {k}")
        elementary[k] = numerator // k
    coefficients = [0] * (degree + 1)
    for k in range(degree + 1):
        coefficients[degree - k] = ((-1) ** k) * elementary[k]
    return ring(coefficients)


def compute_pair_sum(line: str, orbit_row: dict) -> dict:
    coefficients = coefficient_list(line, "queued source")
    ring = PolynomialRing(ZZ, "x")
    source = ring(coefficients)
    if (
        source.degree() != 24
        or not source.is_monic()
        or not source.is_irreducible()
    ):
        raise ValueError("queued source fails degree/monicity/irreducibility")
    source_r = int(source.number_of_real_roots())
    if source_r != EXPECTED_SOURCE_R:
        raise ValueError("queued source real-root signature mismatch")

    pair_degree = 24 * 23 // 2
    arithmetic_started = time.monotonic()
    powers = root_power_sums(coefficients, 2 * pair_degree)
    newton_seconds = round(time.monotonic() - arithmetic_started, 3)
    transform_started = time.monotonic()
    transformed = transformed_power_sums(powers, pair_degree, EXPECTED_TRANSFORM)
    resolvent = pair_sum_resolvent(ring, transformed, pair_degree)
    resolvent_seconds = round(time.monotonic() - transform_started, 3)
    if resolvent.degree() != pair_degree or not resolvent.is_monic():
        raise ValueError("pair resolvent is not monic degree 276")
    resolvent_line = ",".join(str(int(value)) for value in resolvent.list())
    resolvent_sha = sha256_bytes(resolvent_line.encode("utf-8"))

    factor_started = time.monotonic()
    if not resolvent.is_squarefree():
        raise ValueError("pair resolvent is not squarefree")
    factorization = [
        (factor, int(exponent)) for factor, exponent in resolvent.factor()
    ]
    factor_seconds = round(time.monotonic() - factor_started, 3)
    actual_degrees = sorted(
        int(factor.degree())
        for factor, exponent in factorization
        for _ in range(exponent)
    )
    exponents = [exponent for _, exponent in factorization]
    expected_degrees = sorted(int(value) for value in orbit_row["orbitSizes"])
    if actual_degrees != expected_degrees or any(exponent != 1 for exponent in exponents):
        raise ValueError("pair factorization does not match pinned GAP orbit census")
    degree_24 = [
        factor
        for factor, exponent in factorization
        if int(factor.degree()) == 24 and exponent == 1
    ]
    if len(degree_24) != 1:
        raise ValueError("pair factorization does not have one degree-24 factor")
    raw_factor = degree_24[0]
    raw_r = int(raw_factor.number_of_real_roots())
    if raw_r != EXPECTED_TARGET_R:
        raise ValueError("raw pair factor does not realize the certified target r")
    raw_line = coefficient_line(raw_factor)

    reduction_started = time.monotonic()
    reduced = ring(pari(raw_factor).polredbest())
    reduction_seconds = round(time.monotonic() - reduction_started, 3)
    if (
        reduced.degree() != 24
        or not reduced.is_monic()
        or not reduced.is_irreducible()
    ):
        raise ValueError("reduced pair factor fails exact polynomial checks")
    reduced_r = int(reduced.number_of_real_roots())
    if reduced_r != EXPECTED_TARGET_R:
        raise ValueError("reduced pair factor real-root signature mismatch")
    candidate_line = coefficient_line(reduced)
    candidate_sha = sha256_bytes(candidate_line.encode("utf-8"))

    nfdisc_started = time.monotonic()
    field_disc = str(abs(int(pari(reduced).nfdisc())))
    nfdisc_seconds = round(time.monotonic() - nfdisc_started, 3)
    if not field_disc.isdigit() or int(field_disc) <= 0:
        raise ValueError("derived pair factor has no positive field discriminant")

    return {
        "method": "exact unordered-pair sum after h(x)=x+x^2",
        "sourceChecks": {
            "degree": 24,
            "monic": True,
            "irreducible": True,
            "r": source_r,
        },
        "transform": {"kind": "x+c*x^2", "c": EXPECTED_TRANSFORM},
        "resolvent": {
            "degree": pair_degree,
            "coefficientSha256": resolvent_sha,
            "squarefree": True,
            "newtonSeconds": newton_seconds,
            "constructionSeconds": resolvent_seconds,
            "factorSeconds": factor_seconds,
        },
        "orbitFactorCertificate": {
            "actualDegrees": actual_degrees,
            "expectedDegrees": expected_degrees,
            "exponents": exponents,
            "uniqueDegree24Factor": True,
        },
        "rawFactor": {
            "coefficientSha256": sha256_bytes(raw_line.encode("utf-8")),
            "r": raw_r,
        },
        "reduction": {
            "method": "pari-polredbest",
            "seconds": reduction_seconds,
        },
        "candidate": {
            "targetLabel": EXPECTED_TARGET_LABEL,
            "targetT": EXPECTED_TARGET_T,
            "targetR": reduced_r,
            "degree": 24,
            "monic": True,
            "irreducible": True,
            "coefficientLine": candidate_line,
            "coefficientBytes": len(candidate_line.encode("utf-8")),
            "coefficientSha256": candidate_sha,
            "polynomialDiscriminantAbs": str(abs(int(reduced.discriminant()))),
            "fieldDiscriminantAbs": field_disc,
            "nfdiscSeconds": nfdisc_seconds,
        },
        "arithmeticSeconds": round(time.monotonic() - arithmetic_started, 3),
    }


def validate_candidate_novelty(candidate_sha: str) -> dict:
    with read_only_connection() as connection:
        occurrences = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_sha,),
            ).fetchone()[0]
        )
    if occurrences != 0:
        raise ValueError("derived candidate coefficient already occurs in the ledger")
    return {"ledgerCoefficientOccurrences": occurrences}


def write_one_jsonl_atomic(path: Path, value: dict) -> None:
    destination = path.resolve()
    if destination != OUTPUT_PATH.resolve():
        raise ValueError("worker output path is not the fixed result path")
    if destination.parent != DATA.resolve() or not destination.parent.is_dir():
        raise ValueError("fixed data output directory is unavailable")
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite {destination}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
        temporary.unlink()
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission_id")
    parser.add_argument("polynomial_index", type=int)
    args = parser.parse_args()
    if args.submission_id != EXPECTED_SUBMISSION_ID:
        parser.error("this worker is pinned to sub_2602...dde981")
    if args.polynomial_index != EXPECTED_POLYNOMIAL_INDEX:
        parser.error("this worker is pinned to polynomial index 4")
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT_PATH}")

    log(
        f"starting strict queued-pair worker pid={os.getpid()} "
        f"source={args.submission_id}:{args.polynomial_index}"
    )
    started = time.monotonic()
    line, source_certificate_evidence = validate_source_certificate()
    log("validated pinned exact source certificate and one-line manifest")
    signature_certificate, signature_evidence = validate_signature_certificate()
    log("independently reproduced pinned source-r20 signature profile")
    orbit_row, orbit_evidence = validate_orbit_census(signature_certificate)
    log("validated pinned unique length-24 orbit census row")
    ledger_before = validate_ledger_queue(line, signature_certificate)
    log("validated raw ledger source is still queued and target is still tc0")

    arithmetic = compute_pair_sum(line, orbit_row)
    log(
        "derived exact target "
        f"{EXPECTED_TARGET_LABEL}/r{arithmetic['candidate']['targetR']} "
        f"with SHA-256 {arithmetic['candidate']['coefficientSha256']}"
    )
    novelty = validate_candidate_novelty(
        str(arithmetic["candidate"]["coefficientSha256"])
    )
    ledger_after = validate_ledger_queue(line, signature_certificate)
    if ledger_after["stateCanonicalSha256"] != ledger_before["stateCanonicalSha256"]:
        raise ValueError("ledger queue/target state changed during arithmetic")

    result = {
        "status": "certified_queued_pair_sum",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "method": "strict-pinned-queued-pair-sum-v1",
        "sourcePair": f"{EXPECTED_SOURCE_LABEL}/r{EXPECTED_SOURCE_R}",
        "targetPair": f"{EXPECTED_TARGET_LABEL}/r{EXPECTED_TARGET_R}",
        "source": {
            "submissionId": EXPECTED_SUBMISSION_ID,
            "polynomialIndex": EXPECTED_POLYNOMIAL_INDEX,
            "label": EXPECTED_SOURCE_LABEL,
            "t": EXPECTED_SOURCE_T,
            "r": EXPECTED_SOURCE_R,
            "coefficientSha256": EXPECTED_SOURCE_SHA256,
            "queueEvidence": ledger_after,
        },
        "proofs": {
            "exactSourceCertificate": source_certificate_evidence,
            "queuedSignatureCertificate": signature_evidence,
            "pinnedOrbitCensus": orbit_evidence,
        },
        "arithmetic": arithmetic,
        "novelty": novelty,
        "software": {
            "sageVersion": str(SAGE_VERSION),
            "gapVersion": str(libgap.eval("GAPInfo.Version")),
        },
        "worker": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_path(Path(__file__).resolve()),
        },
        "runtimeSeconds": round(time.monotonic() - started, 3),
        "networkCalls": 0,
        "ledgerWrites": 0,
        "outboxWrites": 0,
        "receiptWrites": 0,
        "submissionCalls": 0,
        "output": {
            "path": str(OUTPUT_PATH),
            "format": "one-json-object-jsonl",
            "atomicPublication": "fsync-temp-link-fsync-directory",
        },
    }
    write_one_jsonl_atomic(OUTPUT_PATH, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "targetPair": result["targetPair"],
                "candidateSha256": arithmetic["candidate"]["coefficientSha256"],
                "fieldDiscriminantAbs": arithmetic["candidate"][
                    "fieldDiscriminantAbs"
                ],
                "output": str(OUTPUT_PATH),
                "runtimeSeconds": result["runtimeSeconds"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

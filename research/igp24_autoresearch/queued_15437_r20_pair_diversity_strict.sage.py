#!/usr/bin/env sage -python
"""Strict receipt-backed pair closure from queued 24T15437/r20.

The sole committed source is ``sub_381634...:0``.  The first generation
extracts the two degree-24/r16 factors whose equivalent pair actions are
24T15896.  Their polynomial factors are canonically ordered; only one is
marked submission-eligible for that scoring pair.  A diversity pass then
applies the unique length-24 pair orbit of each distinct child and records
whether it lands on 24T15437/r16 (fresh gold) or 24T15437/r20 (duplicate).

All provenance, signature, orbit, target, and novelty checks are fail-closed.
There is no network or submission code.  The sole write is one fixed,
atomically published JSONL result under ``data/``.
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

SOURCE_SUBMISSION_ID = "sub_3816341bfa214c59b29a7aa42637ffbc"
SOURCE_POLYNOMIAL_INDEX = 0
SOURCE_LABEL = "24T15437"
SOURCE_T = 15437
SOURCE_R = 20
SOURCE_COEFFICIENT_SHA256 = (
    "2321e9a2ba10341577044c892ac1150f626ad57e58c50a27fa4074593387db08"
)

PRIMARY_TARGET_LABEL = "24T15896"
PRIMARY_TARGET_T = 15896
PRIMARY_TARGET_R = 16
DIVERSITY_TARGET_LABEL = "24T15437"
DIVERSITY_TARGET_T = 15437
DIVERSITY_FRESH_R = 16
DIVERSITY_DUPLICATE_R = 20
TRANSFORM = 1

PRIOR_RESULT_PATH = DATA / "queued_15337_r20_to_15437_r20_pair_sum_20260729.jsonl"
PRIOR_RESULT_SHA256 = (
    "34b8c9675ab1ed55df687b9f615e7d62dea022de99fb3e9cb712c3247f689d89"
)
PRIOR_WORKER_PATH = ROOT / "queued_15337_r20_pair_sum_strict.sage.py"
PRIOR_WORKER_SHA256 = (
    "e3b1d1843c473ca8967ab8c0699d949229314908013d5ddcec4f2f728b0bc69c"
)
SOURCE_RECEIPT_PATH = ROOT / "receipts" / f"{SOURCE_SUBMISSION_ID}.json"
SOURCE_RECEIPT_SHA256 = (
    "bec58c9e02adbed67d5ab6db06809010e15eda736101165ce8a8aa0f97819a22"
)
SOURCE_MANIFEST_PATH = (
    ROOT / "outbox" / "queued_15337_child_exact_24T15437_r20_20260729.txt"
)
SOURCE_MANIFEST_SHA256 = (
    "9ecb368d08beb04684e7c27b2c87e8f0faedde7cd955ec66c156e78e9f6c0f37"
)

PRIMARY_ORBIT_CENSUS_PATH = (
    DATA / "autopilot_pair_delta_20260722_v3" / "missing_pair_all.jsonl"
)
PRIMARY_ORBIT_CENSUS_SHA256 = (
    "6947eb84faf71f95ab9d8154739afe2067f0fb3edba7a8ce7a8245d65815672b"
)
PRIMARY_ORBIT_RECORD = 21
PRIMARY_ORBIT_ROW_SHA256 = (
    "3fca5744b155f2f26a6c889ad1c5bca3cf33b2e59442fecfd58b3870a7d9087a"
)
PRIMARY_EXACT_CERTIFICATE_SHA256 = (
    "982baa0483af1399401f9677aeb4b1a246797f16e27732d1d6c2d0070f131c71"
)

DIVERSITY_ORBIT_CENSUS_PATH = (
    DATA / "autopilot_pair_delta_20260722_v5" / "missing_pair_all.jsonl"
)
DIVERSITY_ORBIT_CENSUS_SHA256 = (
    "365c820c97d818593d30c3d0195a7508de5f68f31cc0d5e459e0c6f3b8ed5849"
)
DIVERSITY_ORBIT_RECORD = 6
DIVERSITY_ORBIT_ROW_SHA256 = (
    "b5ba9065907cd8609588a73e1264e43a89f7104787a7ae56c4b71f92ac113841"
)
DIVERSITY_EXACT_CERTIFICATE_SHA256 = (
    "874342c1f2aeb162781e7eeb9dfac1b93449b009c34b80f870f50f570e889d3b"
)

SIGNATURE_WORKER_PATH = ROOT / "pair_signature_one.sage.py"
SIGNATURE_WORKER_SHA256 = (
    "1bdbe729dcc1e9861b22606dcc67bf86035e6c99664e2008fd553f1e00a9ee2b"
)
PRIMARY_SIGNATURE_SHA256 = (
    "5a6c431e07c2a08855c62df5166bcda32cac6df8f5eb9bd3aca28cb50ce31ca6"
)
DIVERSITY_SIGNATURE_SHA256 = (
    "722c80df6583f7ab0e1eb9d6d0cffbc44d23db90877c8b405dba6daf1e507a7a"
)

OUTPUT_PATH = DATA / "queued_15437_r20_pair_diversity_20260729.jsonl"


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


def read_one_jsonl(path: Path) -> dict:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(lines) != 1:
        raise ValueError(f"{path} is not exactly one JSONL row")
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise ValueError(f"{path} JSONL row is not an object")
    return value


def coefficient_values(line: str, description: str) -> list[int]:
    if not isinstance(line, str) or not line:
        raise ValueError(f"{description} coefficient line is absent")
    try:
        values = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError(f"{description} coefficient line contains a noninteger") from exc
    if len(values) != 25 or values[-1] != 1:
        raise ValueError(f"{description} is not monic degree 24")
    if values[0] == 0 or math.gcd(*values) != 1:
        raise ValueError(f"{description} is not primitive with nonzero constant term")
    return values


def coefficient_line(polynomial) -> str:
    values = [int(value) for value in polynomial.list()]
    values += [0] * (25 - len(values))
    line = ",".join(str(value) for value in values)
    coefficient_values(line, "derived candidate")
    return line


def read_only_connection() -> sqlite3.Connection:
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def validate_prior_exact_chain() -> tuple[str, dict]:
    if sha256_path(PRIOR_RESULT_PATH) != PRIOR_RESULT_SHA256:
        raise ValueError("prior strict result byte digest mismatch")
    if sha256_path(PRIOR_WORKER_PATH) != PRIOR_WORKER_SHA256:
        raise ValueError("prior strict worker byte digest mismatch")
    result = read_one_jsonl(PRIOR_RESULT_PATH)
    expected_zeroes = {
        "networkCalls": 0,
        "ledgerWrites": 0,
        "outboxWrites": 0,
        "receiptWrites": 0,
        "submissionCalls": 0,
    }
    for key, expected in expected_zeroes.items():
        if int(result.get(key, -1)) != expected:
            raise ValueError(f"prior strict result {key} mismatch")
    if (
        result.get("status") != "certified_queued_pair_sum"
        or result.get("sourcePair") != "24T15337/r20"
        or result.get("targetPair") != "24T15437/r20"
    ):
        raise ValueError("prior strict result pair/status mismatch")
    if result.get("worker") != {
        "path": str(PRIOR_WORKER_PATH),
        "sha256": PRIOR_WORKER_SHA256,
    }:
        raise ValueError("prior strict result worker provenance mismatch")

    arithmetic = result.get("arithmetic")
    candidate = arithmetic.get("candidate") if isinstance(arithmetic, dict) else None
    if not isinstance(candidate, dict):
        raise ValueError("prior strict result has no candidate")
    expected_candidate = {
        "targetLabel": SOURCE_LABEL,
        "targetT": SOURCE_T,
        "targetR": SOURCE_R,
        "degree": 24,
        "monic": True,
        "irreducible": True,
        "coefficientSha256": SOURCE_COEFFICIENT_SHA256,
    }
    for key, expected in expected_candidate.items():
        if candidate.get(key) != expected:
            raise ValueError(f"prior strict candidate {key} mismatch")
    line = str(candidate.get("coefficientLine"))
    values = coefficient_values(line, "prior strict candidate")
    if sha256_bytes(line.encode("utf-8")) != SOURCE_COEFFICIENT_SHA256:
        raise ValueError("prior strict candidate coefficient digest mismatch")
    factor_certificate = arithmetic.get("orbitFactorCertificate")
    if factor_certificate != {
        "actualDegrees": [12, 24, 48, 192],
        "expectedDegrees": [12, 24, 48, 192],
        "exponents": [1, 1, 1, 1],
        "uniqueDegree24Factor": True,
    }:
        raise ValueError("prior strict result factor certificate mismatch")

    ring = PolynomialRing(ZZ, "x_prior_source")
    polynomial = ring(values)
    if (
        polynomial.degree() != 24
        or not polynomial.is_monic()
        or not polynomial.is_irreducible()
        or int(polynomial.number_of_real_roots()) != SOURCE_R
    ):
        raise ValueError("prior strict candidate fails independent exact checks")
    field_disc = str(abs(int(pari(polynomial).nfdisc())))
    if field_disc != str(candidate.get("fieldDiscriminantAbs")):
        raise ValueError("prior strict candidate field discriminant mismatch")

    proofs = result.get("proofs")
    if not isinstance(proofs, dict):
        raise ValueError("prior strict result has no proof chain")
    path_digest_pairs = [
        (
            Path(proofs["exactSourceCertificate"]["path"]),
            str(proofs["exactSourceCertificate"]["sha256"]),
        ),
        (
            Path(proofs["exactSourceCertificate"]["manifest"]["path"]),
            str(proofs["exactSourceCertificate"]["manifest"]["sha256"]),
        ),
        (
            Path(proofs["pinnedOrbitCensus"]["path"]),
            str(proofs["pinnedOrbitCensus"]["sha256"]),
        ),
        (
            Path(proofs["queuedSignatureCertificate"]["path"]),
            str(proofs["queuedSignatureCertificate"]["sha256"]),
        ),
        (
            Path(
                proofs["queuedSignatureCertificate"]["signatureWorker"]["path"]
            ),
            str(
                proofs["queuedSignatureCertificate"]["signatureWorker"]["sha256"]
            ),
        ),
    ]
    for path, expected_sha in path_digest_pairs:
        if not path.is_absolute() or sha256_path(path) != expected_sha:
            raise ValueError(f"prior exact-chain dependency mismatch: {path}")

    return line, {
        "path": str(PRIOR_RESULT_PATH),
        "sha256": PRIOR_RESULT_SHA256,
        "canonicalSha256": canonical_sha256(result),
        "priorWorker": {
            "path": str(PRIOR_WORKER_PATH),
            "sha256": PRIOR_WORKER_SHA256,
        },
        "sourceFieldDiscriminantAbs": field_disc,
        "dependencyCount": len(path_digest_pairs),
        "dependencyDigestsRevalidated": True,
    }


def validate_receipt_and_manifest(source_line: str) -> tuple[dict, dict]:
    if sha256_path(SOURCE_RECEIPT_PATH) != SOURCE_RECEIPT_SHA256:
        raise ValueError("source receipt byte digest mismatch")
    if sha256_path(SOURCE_MANIFEST_PATH) != SOURCE_MANIFEST_SHA256:
        raise ValueError("source manifest byte digest mismatch")
    manifest_lines = SOURCE_MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
    if manifest_lines != [source_line]:
        raise ValueError("source manifest is not the one certified source line")
    receipt = read_json(SOURCE_RECEIPT_PATH)
    if (
        receipt.get("commit") is not True
        or int(receipt.get("polynomials", -1)) != 1
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or str(receipt.get("manifest")) != str(SOURCE_MANIFEST_PATH)
        or str(receipt.get("manifestHash")) != SOURCE_MANIFEST_SHA256
    ):
        raise ValueError("source receipt envelope mismatch")
    response = receipt.get("response")
    if not isinstance(response, dict):
        raise ValueError("source receipt has no response")
    if (
        response.get("submissionId") != SOURCE_SUBMISSION_ID
        or response.get("submissionStatus") != "queued"
        or int(response.get("queuedCount", -1)) != 1
        or int(response.get("rejectedCount", -1)) != 0
        or response.get("failedPolynomials") != []
        or response.get("verifiedPolynomials") != []
        or response.get("payload")
        != {"queuedPolynomials": [{"polynomialIndex": 0, "status": "queued"}]}
    ):
        raise ValueError("source receipt queued response mismatch")
    return response, {
        "path": str(SOURCE_RECEIPT_PATH),
        "sha256": SOURCE_RECEIPT_SHA256,
        "canonicalSha256": canonical_sha256(receipt),
        "manifest": {
            "path": str(SOURCE_MANIFEST_PATH),
            "sha256": SOURCE_MANIFEST_SHA256,
            "coefficientSha256": SOURCE_COEFFICIENT_SHA256,
            "polynomialIndex": SOURCE_POLYNOMIAL_INDEX,
        },
        "submissionId": SOURCE_SUBMISSION_ID,
        "submissionStatus": "queued",
        "createdAt": str(response.get("createdAt")),
    }


def validate_target_row(
    connection: sqlite3.Connection, label: str, t: int, r: int
) -> dict:
    row = connection.execute(
        """
        SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        raise ValueError(f"live target is absent: {label}/r{r}")
    target = {
        "label": str(row["label"]),
        "t": int(row["t"]),
        "r": int(row["r"]),
        "teamCount": int(row["team_count"]),
        "minimumDiscAbs": row["minimum_disc_abs"],
        "discovered": bool(row["discovered"]),
        "generatedAt": str(row["generated_at"]),
    }
    if (
        target["label"] != label
        or target["t"] != t
        or target["r"] != r
        or target["teamCount"] != 0
        or target["minimumDiscAbs"] is not None
        or target["discovered"] is not False
    ):
        raise ValueError(f"live target is not tc0/undiscovered: {label}/r{r}")
    baseline_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
            (label, r),
        ).fetchone()[0]
    )
    owned_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications "
            "WHERE label=? AND r=? AND scoreable=1",
            (label, r),
        ).fetchone()[0]
    )
    if baseline_count != 0 or owned_count != 0:
        raise ValueError(f"live target is baseline/locally owned: {label}/r{r}")
    target["baseline"] = False
    target["locallyOwnedScoreableCount"] = 0
    return target


def validate_local_state(receipt_response: dict) -> dict:
    connection = read_only_connection()
    try:
        receipt_row = connection.execute(
            """
            SELECT submission_id,manifest_path,manifest_hash,description,
                   submitted_at,raw_json
            FROM submission_receipts WHERE submission_id=?
            """,
            (SOURCE_SUBMISSION_ID,),
        ).fetchone()
        if receipt_row is None:
            raise ValueError("ledger submission-receipt record is absent")
        if (
            str(receipt_row["manifest_path"]) != str(SOURCE_MANIFEST_PATH)
            or str(receipt_row["manifest_hash"]) != SOURCE_MANIFEST_SHA256
            or json.loads(str(receipt_row["raw_json"])) != receipt_response
        ):
            raise ValueError("ledger submission-receipt record mismatch")

        submission = connection.execute(
            """
            SELECT submission_id,queued_count,verified_count,failed_count,raw_json
            FROM submissions WHERE submission_id=?
            """,
            (SOURCE_SUBMISSION_ID,),
        ).fetchone()
        synchronized_state = "receipt_only_unsynced"
        if submission is not None:
            if (
                int(submission["queued_count"]) != 1
                or int(submission["verified_count"]) != 0
                or int(submission["failed_count"]) != 0
            ):
                raise ValueError("synced source submission is not strictly queued")
            raw = json.loads(str(submission["raw_json"]))
            if raw.get("submissionId") != SOURCE_SUBMISSION_ID:
                raise ValueError("synced source submission id mismatch")
            polynomial = connection.execute(
                """
                SELECT original_line,coefficients,coefficient_hash
                FROM polynomials
                WHERE submission_id=? AND polynomial_index=?
                """,
                (SOURCE_SUBMISSION_ID, SOURCE_POLYNOMIAL_INDEX),
            ).fetchone()
            if (
                polynomial is None
                or str(polynomial["coefficient_hash"])
                != SOURCE_COEFFICIENT_SHA256
                or str(polynomial["original_line"])
                != SOURCE_MANIFEST_PATH.read_text(encoding="utf-8").rstrip("\n")
                or str(polynomial["coefficients"]) != str(polynomial["original_line"])
            ):
                raise ValueError("synced queued source polynomial mismatch")
            synchronized_state = "synced_queued"

        verification_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM verifications "
                "WHERE submission_id=? AND polynomial_index=?",
                (SOURCE_SUBMISSION_ID, SOURCE_POLYNOMIAL_INDEX),
            ).fetchone()[0]
        )
        failure_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM failures "
                "WHERE submission_id=? AND polynomial_index=?",
                (SOURCE_SUBMISSION_ID, SOURCE_POLYNOMIAL_INDEX),
            ).fetchone()[0]
        )
        if verification_count or failure_count:
            raise ValueError("source is no longer receipt-backed queued")

        source_occurrences = [
            (str(row["submission_id"]), int(row["polynomial_index"]))
            for row in connection.execute(
                "SELECT submission_id,polynomial_index FROM polynomials "
                "WHERE coefficient_hash=?",
                (SOURCE_COEFFICIENT_SHA256,),
            )
        ]
        allowed = (
            []
            if synchronized_state == "receipt_only_unsynced"
            else [(SOURCE_SUBMISSION_ID, SOURCE_POLYNOMIAL_INDEX)]
        )
        if source_occurrences != allowed:
            raise ValueError("source coefficient has an unexpected ledger occurrence")

        primary_target = validate_target_row(
            connection,
            PRIMARY_TARGET_LABEL,
            PRIMARY_TARGET_T,
            PRIMARY_TARGET_R,
        )
        diversity_target = validate_target_row(
            connection,
            DIVERSITY_TARGET_LABEL,
            DIVERSITY_TARGET_T,
            DIVERSITY_FRESH_R,
        )
    finally:
        connection.close()

    evidence = {
        "database": str(DB_PATH),
        "submissionReceiptPresent": True,
        "sourceState": synchronized_state,
        "sourceVerificationRows": verification_count,
        "sourceFailureRows": failure_count,
        "sourceCoefficientOccurrences": len(source_occurrences),
        "primaryTarget": primary_target,
        "diversityTarget": diversity_target,
    }
    evidence["stateCanonicalSha256"] = canonical_sha256(evidence)
    return evidence


def read_pinned_orbit_row(
    path: Path,
    file_sha: str,
    record_number: int,
    row_sha: str,
    source_label: str,
) -> dict:
    if sha256_path(path) != file_sha:
        raise ValueError(f"orbit census byte digest mismatch: {path}")
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"orbit census row is not an object: {path}:{line_number}")
        rows.append((line_number, value))
    matching = [
        (line_number, row)
        for line_number, row in rows
        if row.get("sourceLabel") == source_label
    ]
    if len(matching) != 1:
        raise ValueError(f"orbit census lacks one source row: {source_label}")
    line_number, row = matching[0]
    if line_number != record_number or canonical_sha256(row) != row_sha:
        raise ValueError(f"orbit census row pin mismatch: {source_label}")
    return row


def validate_orbit_censuses() -> tuple[dict, dict, dict]:
    primary = read_pinned_orbit_row(
        PRIMARY_ORBIT_CENSUS_PATH,
        PRIMARY_ORBIT_CENSUS_SHA256,
        PRIMARY_ORBIT_RECORD,
        PRIMARY_ORBIT_ROW_SHA256,
        SOURCE_LABEL,
    )
    expected_primary_targets = [
        {
            "imageOrder": 49152,
            "kernelOrder": 1,
            "orbitIndex": 1,
            "orbitSize": 24,
            "targetLabel": "24T15337",
            "targetT": 15337,
        },
        {
            "imageOrder": 49152,
            "kernelOrder": 1,
            "orbitIndex": 2,
            "orbitSize": 24,
            "targetLabel": PRIMARY_TARGET_LABEL,
            "targetT": PRIMARY_TARGET_T,
        },
        {
            "imageOrder": 49152,
            "kernelOrder": 1,
            "orbitIndex": 3,
            "orbitSize": 24,
            "targetLabel": PRIMARY_TARGET_LABEL,
            "targetT": PRIMARY_TARGET_T,
        },
    ]
    if (
        primary.get("status") != "certified"
        or int(primary.get("sourceT", -1)) != SOURCE_T
        or int(primary.get("sourceOrder", -1)) != 49152
        or primary.get("exactCertificateSha256")
        != PRIMARY_EXACT_CERTIFICATE_SHA256
        or primary.get("orbitSizes") != [12, 24, 24, 24, 192]
        or int(primary.get("length24OrbitCount", -1)) != 3
        or primary.get("targets") != expected_primary_targets
        or primary.get("targetCounts")
        != {"24T15337": 1, PRIMARY_TARGET_LABEL: 2}
    ):
        raise ValueError("primary orbit census content mismatch")

    diversity = read_pinned_orbit_row(
        DIVERSITY_ORBIT_CENSUS_PATH,
        DIVERSITY_ORBIT_CENSUS_SHA256,
        DIVERSITY_ORBIT_RECORD,
        DIVERSITY_ORBIT_ROW_SHA256,
        PRIMARY_TARGET_LABEL,
    )
    expected_diversity_target = {
        "imageOrder": 49152,
        "kernelOrder": 1,
        "orbitIndex": 1,
        "orbitSize": 24,
        "targetLabel": DIVERSITY_TARGET_LABEL,
        "targetT": DIVERSITY_TARGET_T,
    }
    if (
        diversity.get("status") != "certified"
        or int(diversity.get("sourceT", -1)) != PRIMARY_TARGET_T
        or int(diversity.get("sourceOrder", -1)) != 49152
        or diversity.get("exactCertificateSha256")
        != DIVERSITY_EXACT_CERTIFICATE_SHA256
        or diversity.get("orbitSizes") != [12, 24, 48, 192]
        or int(diversity.get("length24OrbitCount", -1)) != 1
        or diversity.get("targets") != [expected_diversity_target]
        or diversity.get("targetCounts") != {DIVERSITY_TARGET_LABEL: 1}
    ):
        raise ValueError("diversity orbit census content mismatch")

    evidence = {
        "primary": {
            "path": str(PRIMARY_ORBIT_CENSUS_PATH),
            "sha256": PRIMARY_ORBIT_CENSUS_SHA256,
            "record": PRIMARY_ORBIT_RECORD,
            "recordCanonicalSha256": PRIMARY_ORBIT_ROW_SHA256,
            "exactCertificateSha256": PRIMARY_EXACT_CERTIFICATE_SHA256,
        },
        "diversity": {
            "path": str(DIVERSITY_ORBIT_CENSUS_PATH),
            "sha256": DIVERSITY_ORBIT_CENSUS_SHA256,
            "record": DIVERSITY_ORBIT_RECORD,
            "recordCanonicalSha256": DIVERSITY_ORBIT_ROW_SHA256,
            "exactCertificateSha256": DIVERSITY_EXACT_CERTIFICATE_SHA256,
        },
    }
    return primary, diversity, evidence


def fixed_points(permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def reproduce_signature_profiles(label: str, t: int) -> dict:
    group = libgap.TransitiveGroup(24, t)
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
        profiles.append(
            {
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "order": order,
                "sourceR": fixed_points(representative, 24),
                "orbitSignatures": [
                    {
                        "orbitIndex": orbit["orbitIndex"],
                        "targetLabel": orbit["targetLabel"],
                        "targetR": fixed_points(
                            libgap.Image(orbit["homomorphism"], representative), 24
                        ),
                    }
                    for orbit in length_24
                ],
            }
        )
    return {
        "sourceLabel": label,
        "sourceT": t,
        "length24OrbitCount": len(length_24),
        "profiles": profiles,
    }


def validate_signatures() -> tuple[dict, dict, dict]:
    if sha256_path(SIGNATURE_WORKER_PATH) != SIGNATURE_WORKER_SHA256:
        raise ValueError("signature worker byte digest mismatch")
    primary = reproduce_signature_profiles(SOURCE_LABEL, SOURCE_T)
    diversity = reproduce_signature_profiles(PRIMARY_TARGET_LABEL, PRIMARY_TARGET_T)
    if canonical_sha256(primary) != PRIMARY_SIGNATURE_SHA256:
        raise ValueError("primary signature reproduction digest mismatch")
    if canonical_sha256(diversity) != DIVERSITY_SIGNATURE_SHA256:
        raise ValueError("diversity signature reproduction digest mismatch")
    if len(primary["profiles"]) != 28 or primary["length24OrbitCount"] != 3:
        raise ValueError("primary signature census cardinality mismatch")
    if len(diversity["profiles"]) != 28 or diversity["length24OrbitCount"] != 1:
        raise ValueError("diversity signature census cardinality mismatch")

    expected_primary_profile = {
        "classIndex": 16,
        "classSize": 6,
        "order": 2,
        "sourceR": SOURCE_R,
        "orbitSignatures": [
            {"orbitIndex": 1, "targetLabel": "24T15337", "targetR": 20},
            {
                "orbitIndex": 2,
                "targetLabel": PRIMARY_TARGET_LABEL,
                "targetR": PRIMARY_TARGET_R,
            },
            {
                "orbitIndex": 3,
                "targetLabel": PRIMARY_TARGET_LABEL,
                "targetR": PRIMARY_TARGET_R,
            },
        ],
    }
    primary_matches = [
        profile for profile in primary["profiles"] if profile["sourceR"] == SOURCE_R
    ]
    if primary_matches != [expected_primary_profile]:
        raise ValueError("primary source-r20 profile is not uniquely deterministic")

    expected_diversity_profiles = [
        {
            "classIndex": 2,
            "classSize": 3,
            "order": 2,
            "sourceR": PRIMARY_TARGET_R,
            "orbitSignatures": [
                {
                    "orbitIndex": 1,
                    "targetLabel": DIVERSITY_TARGET_LABEL,
                    "targetR": DIVERSITY_FRESH_R,
                }
            ],
        },
        {
            "classIndex": 11,
            "classSize": 6,
            "order": 2,
            "sourceR": PRIMARY_TARGET_R,
            "orbitSignatures": [
                {
                    "orbitIndex": 1,
                    "targetLabel": DIVERSITY_TARGET_LABEL,
                    "targetR": DIVERSITY_DUPLICATE_R,
                }
            ],
        },
        {
            "classIndex": 18,
            "classSize": 12,
            "order": 2,
            "sourceR": PRIMARY_TARGET_R,
            "orbitSignatures": [
                {
                    "orbitIndex": 1,
                    "targetLabel": DIVERSITY_TARGET_LABEL,
                    "targetR": DIVERSITY_FRESH_R,
                }
            ],
        },
    ]
    diversity_matches = [
        profile
        for profile in diversity["profiles"]
        if profile["sourceR"] == PRIMARY_TARGET_R
    ]
    if diversity_matches != expected_diversity_profiles:
        raise ValueError("diversity source-r16 profile mismatch")

    evidence = {
        "worker": {
            "path": str(SIGNATURE_WORKER_PATH),
            "sha256": SIGNATURE_WORKER_SHA256,
        },
        "primaryCanonicalSha256": PRIMARY_SIGNATURE_SHA256,
        "primarySourceR20Profile": expected_primary_profile,
        "diversityCanonicalSha256": DIVERSITY_SIGNATURE_SHA256,
        "diversitySourceR16Profiles": expected_diversity_profiles,
        "software": {
            "sageVersion": str(SAGE_VERSION),
            "gapVersion": str(libgap.eval("GAPInfo.Version")),
        },
    }
    return primary, diversity, evidence


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
            * transform**exponent
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
        ) - 2**power * transformed_powers[power]
        if numerator % 2:
            raise ArithmeticError(f"nonintegral pair power sum at {power}")
        pair_powers[power] = numerator // 2
    elementary = [0] * (degree + 1)
    elementary[0] = 1
    for k in range(1, degree + 1):
        numerator = sum(
            (-1) ** (power - 1)
            * elementary[k - power]
            * pair_powers[power]
            for power in range(1, k + 1)
        )
        if numerator % k:
            raise ArithmeticError(f"nonintegral elementary sum at {k}")
        elementary[k] = numerator // k
    coefficients = [0] * (degree + 1)
    for k in range(degree + 1):
        coefficients[degree - k] = (-1) ** k * elementary[k]
    return ring(coefficients)


def factor_pair_resolvent(
    source_line: str,
    expected_source_r: int,
    expected_degrees: list[int],
    description: str,
) -> tuple[list, dict]:
    values = coefficient_values(source_line, description)
    ring = PolynomialRing(ZZ, f"x_{description.replace('-', '_')}")
    source = ring(values)
    if (
        source.degree() != 24
        or not source.is_monic()
        or not source.is_irreducible()
        or int(source.number_of_real_roots()) != expected_source_r
    ):
        raise ValueError(f"{description} source exact checks failed")
    pair_degree = 24 * 23 // 2
    started = time.monotonic()
    powers = root_power_sums(values, 2 * pair_degree)
    newton_seconds = round(time.monotonic() - started, 3)
    construction_started = time.monotonic()
    transformed = transformed_power_sums(powers, pair_degree, TRANSFORM)
    resolvent = pair_sum_resolvent(ring, transformed, pair_degree)
    construction_seconds = round(time.monotonic() - construction_started, 3)
    if resolvent.degree() != pair_degree or not resolvent.is_monic():
        raise ValueError(f"{description} resolvent is not monic degree 276")
    resolvent_line = ",".join(str(int(value)) for value in resolvent.list())
    factor_started = time.monotonic()
    if not resolvent.is_squarefree():
        raise ValueError(f"{description} resolvent is not squarefree")
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
    if (
        actual_degrees != sorted(expected_degrees)
        or any(exponent != 1 for exponent in exponents)
    ):
        raise ValueError(f"{description} factor degrees differ from pinned census")
    degree_24 = [
        factor
        for factor, exponent in factorization
        if int(factor.degree()) == 24 and exponent == 1
    ]
    if len(degree_24) != expected_degrees.count(24):
        raise ValueError(f"{description} degree-24 factor count mismatch")
    return degree_24, {
        "transform": {"kind": "x+c*x^2", "c": TRANSFORM},
        "resolventDegree": pair_degree,
        "resolventCoefficientSha256": sha256_bytes(
            resolvent_line.encode("utf-8")
        ),
        "squarefree": True,
        "actualDegrees": actual_degrees,
        "expectedDegrees": sorted(expected_degrees),
        "exponents": exponents,
        "newtonSeconds": newton_seconds,
        "constructionSeconds": construction_seconds,
        "factorSeconds": factor_seconds,
        "arithmeticSeconds": round(time.monotonic() - started, 3),
    }


def reduce_factor(factor, target_label: str, target_t: int) -> dict:
    raw_line = coefficient_line(factor)
    raw_sha = sha256_bytes(raw_line.encode("utf-8"))
    raw_r = int(factor.number_of_real_roots())
    started = time.monotonic()
    reduced = factor.parent()(pari(factor).polredbest())
    reduction_seconds = round(time.monotonic() - started, 3)
    if (
        reduced.degree() != 24
        or not reduced.is_monic()
        or not reduced.is_irreducible()
    ):
        raise ValueError("reduced degree-24 factor fails exact checks")
    target_r = int(reduced.number_of_real_roots())
    if target_r != raw_r:
        raise ValueError("polynomial reduction changed the real-root signature")
    line = coefficient_line(reduced)
    candidate_sha = sha256_bytes(line.encode("utf-8"))
    nfdisc_started = time.monotonic()
    field_disc = str(abs(int(pari(reduced).nfdisc())))
    return {
        "targetLabel": target_label,
        "targetT": target_t,
        "targetR": target_r,
        "degree": 24,
        "monic": True,
        "irreducible": True,
        "coefficientLine": line,
        "coefficientBytes": len(line.encode("utf-8")),
        "coefficientSha256": candidate_sha,
        "polynomialDiscriminantAbs": str(abs(int(reduced.discriminant()))),
        "fieldDiscriminantAbs": field_disc,
        "nfdiscSeconds": round(time.monotonic() - nfdisc_started, 3),
        "rawFactorSha256": raw_sha,
        "reduction": {"method": "pari-polredbest", "seconds": reduction_seconds},
    }


def outbox_occurrences(candidate_sha: str) -> list[dict]:
    matches = []
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if sha256_bytes(line.encode("utf-8")) == candidate_sha:
                matches.append({"path": str(path), "line": line_number})
    return matches


def novelty_evidence(candidate_sha: str, require_zero: bool) -> dict:
    connection = read_only_connection()
    try:
        ledger_rows = [
            {
                "submissionId": str(row["submission_id"]),
                "polynomialIndex": int(row["polynomial_index"]),
            }
            for row in connection.execute(
                "SELECT submission_id,polynomial_index FROM polynomials "
                "WHERE coefficient_hash=? ORDER BY submission_id,polynomial_index",
                (candidate_sha,),
            )
        ]
    finally:
        connection.close()
    outbox_rows = outbox_occurrences(candidate_sha)
    if require_zero and (ledger_rows or outbox_rows):
        raise ValueError("fresh candidate already occurs in ledger/outbox")
    return {
        "coefficientSha256": candidate_sha,
        "ledgerOccurrences": ledger_rows,
        "outboxOccurrences": outbox_rows,
        "zeroRequired": require_zero,
        "zeroCertified": not ledger_rows and not outbox_rows,
    }


def primary_generation(source_line: str, orbit_row: dict) -> tuple[list[dict], dict]:
    factors, arithmetic = factor_pair_resolvent(
        source_line,
        SOURCE_R,
        [int(value) for value in orbit_row["orbitSizes"]],
        "queued_15437_r20",
    )
    raw_signatures = sorted(int(factor.number_of_real_roots()) for factor in factors)
    if raw_signatures != [16, 16, 20]:
        raise ValueError("primary degree-24 factor signatures are not [16,16,20]")
    scoring_factors = [
        factor
        for factor in factors
        if int(factor.number_of_real_roots()) == PRIMARY_TARGET_R
    ]
    return_factor = [
        factor for factor in factors if int(factor.number_of_real_roots()) == SOURCE_R
    ]
    if len(scoring_factors) != 2 or len(return_factor) != 1:
        raise ValueError("primary factor/signature partition mismatch")

    reduced = [
        reduce_factor(factor, PRIMARY_TARGET_LABEL, PRIMARY_TARGET_T)
        for factor in scoring_factors
    ]
    reduced.sort(
        key=lambda row: (row["coefficientSha256"], row["rawFactorSha256"])
    )
    groups = []
    for row in reduced:
        if groups and groups[-1]["candidate"]["coefficientSha256"] == row[
            "coefficientSha256"
        ]:
            groups[-1]["rawFactorSha256"].append(row["rawFactorSha256"])
        else:
            groups.append(
                {
                    "candidate": row,
                    "rawFactorSha256": [row["rawFactorSha256"]],
                }
            )
    if len(groups) not in (1, 2):
        raise ValueError("primary canonical child count is not one or two")
    for index, group in enumerate(groups):
        if len(groups) == 1:
            orbit_representatives = [2, 3]
        else:
            orbit_representatives = [2 + index]
        group["equivalentOrbitRepresentatives"] = orbit_representatives
        group["canonicalOrdinal"] = index
        group["submissionEligibleForPrimaryPair"] = index == 0
        group["novelty"] = novelty_evidence(
            str(group["candidate"]["coefficientSha256"]), require_zero=True
        )
    if sum(bool(group["submissionEligibleForPrimaryPair"]) for group in groups) != 1:
        raise ValueError("primary selection did not retain exactly one candidate")

    arithmetic["degree24RealRootSignatures"] = raw_signatures
    arithmetic["returnOrbit"] = {
        "orbitIndex": 1,
        "targetPair": "24T15337/r20",
        "rawFactorSha256": sha256_bytes(
            coefficient_line(return_factor[0]).encode("utf-8")
        ),
        "retained": False,
    }
    arithmetic["scoringOrbitIndexes"] = [2, 3]
    arithmetic["scoringPair"] = f"{PRIMARY_TARGET_LABEL}/r{PRIMARY_TARGET_R}"
    arithmetic["rawScoringFactorCount"] = 2
    arithmetic["distinctCanonicalChildCount"] = len(groups)
    arithmetic["submissionEligibleCount"] = 1
    arithmetic["orbitAssignmentRule"] = (
        "Equivalent 24T15896 actions are canonically represented by sorting "
        "(reduced coefficient SHA-256, raw factor SHA-256); only ordinal 0 "
        "is submission-eligible."
    )
    return groups, arithmetic


def diversity_generation(
    primary_children: list[dict], orbit_row: dict
) -> tuple[list[dict], dict]:
    results = []
    total_started = time.monotonic()
    for child in primary_children:
        candidate = child["candidate"]
        factors, arithmetic = factor_pair_resolvent(
            str(candidate["coefficientLine"]),
            PRIMARY_TARGET_R,
            [int(value) for value in orbit_row["orbitSizes"]],
            f"primary_child_{child['canonicalOrdinal']}",
        )
        if len(factors) != 1:
            raise ValueError("diversity census did not yield one degree-24 factor")
        derived = reduce_factor(
            factors[0], DIVERSITY_TARGET_LABEL, DIVERSITY_TARGET_T
        )
        target_r = int(derived["targetR"])
        if target_r not in (DIVERSITY_FRESH_R, DIVERSITY_DUPLICATE_R):
            raise ValueError("diversity result is outside exact r16 profile outcomes")
        fresh = target_r == DIVERSITY_FRESH_R
        novelty = novelty_evidence(
            str(derived["coefficientSha256"]), require_zero=fresh
        )
        results.append(
            {
                "sourceChildCanonicalOrdinal": child["canonicalOrdinal"],
                "sourceChildSha256": candidate["coefficientSha256"],
                "sourceOrbitRepresentatives": child[
                    "equivalentOrbitRepresentatives"
                ],
                "exactPairOrbitIndex": 1,
                "candidate": derived,
                "targetPair": f"{DIVERSITY_TARGET_LABEL}/r{target_r}",
                "freshGoldPair": fresh,
                "submissionEligibleForDiversityPair": False,
                "novelty": novelty,
                "arithmetic": arithmetic,
            }
        )

    fresh_rows = [row for row in results if row["freshGoldPair"]]
    fresh_rows.sort(
        key=lambda row: (
            row["candidate"]["coefficientSha256"],
            row["sourceChildSha256"],
        )
    )
    if fresh_rows:
        fresh_rows[0]["submissionEligibleForDiversityPair"] = True
    if sum(bool(row["submissionEligibleForDiversityPair"]) for row in results) > 1:
        raise ValueError("diversity selection retained more than one candidate")
    summary = {
        "sourceChildCount": len(primary_children),
        "resultCount": len(results),
        "targetOutcomes": [row["targetPair"] for row in results],
        "freshGoldResultCount": len(fresh_rows),
        "submissionEligibleCount": sum(
            bool(row["submissionEligibleForDiversityPair"]) for row in results
        ),
        "arithmeticSeconds": round(time.monotonic() - total_started, 3),
        "selectionRule": (
            "At most one lexicographically minimal fresh 24T15437/r16 "
            "candidate is submission-eligible; r20 outcomes are duplicates."
        ),
    }
    return results, summary


def write_one_jsonl_atomic(path: Path, value: dict) -> None:
    destination = path.resolve()
    if destination != OUTPUT_PATH.resolve():
        raise ValueError("output path is not the fixed diversity result")
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
    if args.submission_id != SOURCE_SUBMISSION_ID:
        parser.error("worker is pinned to sub_381634...ffbc")
    if args.polynomial_index != SOURCE_POLYNOMIAL_INDEX:
        parser.error("worker is pinned to polynomial index 0")
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT_PATH}")

    log(
        f"starting strict pair-diversity worker pid={os.getpid()} "
        f"source={args.submission_id}:{args.polynomial_index}"
    )
    started = time.monotonic()
    source_line, prior_evidence = validate_prior_exact_chain()
    log("validated prior strict result and its complete exact dependency chain")
    receipt_response, receipt_evidence = validate_receipt_and_manifest(source_line)
    log("validated committed one-line receipt/manifest source")
    primary_orbit, diversity_orbit, orbit_evidence = validate_orbit_censuses()
    log("validated both pinned exact pair-orbit censuses")
    _, _, signature_evidence = validate_signatures()
    log("independently reproduced both pinned full signature maps")
    state_before = validate_local_state(receipt_response)
    log("validated receipt-backed queue state and both live tc0 targets")

    primary_children, primary_arithmetic = primary_generation(
        source_line, primary_orbit
    )
    log(
        "primary generation certified "
        f"{len(primary_children)} distinct 24T15896/r16 child field(s)"
    )
    diversity_results, diversity_summary = diversity_generation(
        primary_children, diversity_orbit
    )
    log(
        "diversity generation outcomes: "
        + ", ".join(row["targetPair"] for row in diversity_results)
    )

    state_after = validate_local_state(receipt_response)
    if state_after["stateCanonicalSha256"] != state_before["stateCanonicalSha256"]:
        raise ValueError("receipt/target state changed during arithmetic")
    if sum(
        bool(child["submissionEligibleForPrimaryPair"])
        for child in primary_children
    ) != 1:
        raise ValueError("final primary eligibility count is not one")

    result = {
        "status": "certified_queued_pair_diversity",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "method": "strict-receipt-backed-recursive-pair-closure-v1",
        "source": {
            "submissionId": SOURCE_SUBMISSION_ID,
            "polynomialIndex": SOURCE_POLYNOMIAL_INDEX,
            "label": SOURCE_LABEL,
            "t": SOURCE_T,
            "r": SOURCE_R,
            "coefficientSha256": SOURCE_COEFFICIENT_SHA256,
        },
        "proofs": {
            "priorStrictExactChain": prior_evidence,
            "committedSourceReceipt": receipt_evidence,
            "pinnedOrbitCensuses": orbit_evidence,
            "independentSignatureMaps": signature_evidence,
            "liveState": state_after,
        },
        "primaryGeneration": {
            "targetPair": f"{PRIMARY_TARGET_LABEL}/r{PRIMARY_TARGET_R}",
            "children": primary_children,
            "arithmetic": primary_arithmetic,
            "uniqueScoringPairCount": 1,
            "submissionEligibleCandidateCount": 1,
        },
        "diversityGeneration": {
            "results": diversity_results,
            "summary": diversity_summary,
        },
        "submissionPolicy": {
            "actionsPerformed": 0,
            "primaryPairMaximumEligibleCandidates": 1,
            "diversityPairMaximumEligibleCandidates": 1,
            "alternativeFieldsAreNotSubmissionEligible": True,
        },
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
                "primaryTargetPair": result["primaryGeneration"]["targetPair"],
                "primaryChildren": [
                    child["candidate"]["coefficientSha256"]
                    for child in primary_children
                ],
                "primarySubmissionEligible": [
                    child["candidate"]["coefficientSha256"]
                    for child in primary_children
                    if child["submissionEligibleForPrimaryPair"]
                ],
                "diversityOutcomes": diversity_summary["targetOutcomes"],
                "diversityFreshGoldCount": diversity_summary[
                    "freshGoldResultCount"
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

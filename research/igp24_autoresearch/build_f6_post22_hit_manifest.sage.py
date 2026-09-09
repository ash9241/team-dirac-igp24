#!/usr/bin/env sage -python
"""Build a coefficient-bearing local proof manifest for the first F6 hit."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CAMPAIGN = DATA / "campaign_20260727_f627"
CENSUS = CAMPAIGN / "f6_post22_pair_revival_census.jsonl"
DEFAULT_RESULT = DATA / "f6_post22_14142_r20_to_14293_unique.jsonl"
DEFAULT_OUTPUT = CAMPAIGN / "f6_post22_14293_r16_certificate_manifest.json"
WAVE_HITS = CAMPAIGN / "f6_post22_unique_wave_exact_hits.jsonl"
WAVE_RESULTS = CAMPAIGN / "f6_post22_unique_wave_results.jsonl"


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1:
        raise ValueError(f"expected exactly one JSONL row in {path}")
    return rows[0]


def census_row(source_label: str) -> dict:
    for line in CENSUS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row["sourceLabel"]) == source_label:
            return row
    raise ValueError(f"missing census row for {source_label}")


def artifact_matches(pattern: str) -> list[str]:
    completed = subprocess.run(
        ["rg", "-l", "-F", "--", pattern, str(DATA)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"rg artifact scan failed: {completed.stderr[-500:]}")
    paths = []
    for line in completed.stdout.splitlines():
        path = Path(line).resolve()
        try:
            paths.append(str(path.relative_to(ROOT)))
        except ValueError:
            paths.append(str(path))
    return sorted(set(paths))


def atomic_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result_path = args.result.resolve()
    output_path = args.output.resolve()
    result = one_jsonl(result_path)
    expected = {
        "status": "certified",
        "sourceLabel": "24T14142",
        "sourceR": 20,
        "sourceSubmissionId": "sub_a937e93fe6f343d3a1aed8756c33bca0",
        "sourcePolynomialIndex": 13,
        "sourceCoefficientSha256": (
            "80f44cb2c7a12333c3aeb7165a2ad938f9377e8e6bc58f8a5d4347c2d25c8e0b"
        ),
        "targetLabel": "24T14293",
        "targetT": 14293,
        "targetR": 16,
    }
    for field, value in expected.items():
        if result.get(field) != value:
            raise ValueError(
                f"pinned result mismatch for {field}: {result.get(field)!r} != {value!r}"
            )

    line = str(result["coefficientLine"])
    coefficients = [int(value) for value in line.split(",")]
    candidate_hash = sha256_bytes(line.encode("ascii"))
    if candidate_hash != str(result["coefficientSha256"]):
        raise ValueError("candidate coefficient hash mismatch")
    ring = PolynomialRing(ZZ, "x")
    polynomial = ring(coefficients)
    candidate_checks = {
        "degree": int(polynomial.degree()),
        "monic": bool(polynomial.is_monic()),
        "primitive": math.gcd(*coefficients) == 1,
        "nonzeroConstant": coefficients[0] != 0,
        "irreducibleOverQ": bool(polynomial.is_irreducible()),
        "realRootCount": int(polynomial.number_of_real_roots()),
        "coefficientBytes": len(line.encode("ascii")),
        "coefficientSha256": candidate_hash,
        "polynomialDiscriminantAbs": str(abs(int(polynomial.discriminant()))),
        "fieldDiscriminantAbs": str(abs(int(pari(polynomial).nfdisc()))),
    }
    if candidate_checks != {
        "degree": 24,
        "monic": True,
        "primitive": True,
        "nonzeroConstant": True,
        "irreducibleOverQ": True,
        "realRootCount": 16,
        "coefficientBytes": int(result["coefficientBytes"]),
        "coefficientSha256": str(result["coefficientSha256"]),
        "polynomialDiscriminantAbs": str(result["polynomialDiscriminantAbs"]),
        "fieldDiscriminantAbs": str(result["fieldDiscriminantAbs"]),
    }:
        raise ValueError("independent candidate arithmetic does not match worker receipt")

    census = census_row(str(result["sourceLabel"]))
    if int(census["length24OrbitCount"]) != 1:
        raise ValueError("source census no longer has a unique degree-24 orbit")
    orbit_target = census["targets"][0]
    if (
        str(orbit_target["targetLabel"]) != str(result["targetLabel"])
        or int(orbit_target["orbitIndex"])
        != int(result["orbitTargets"][0]["orbitIndex"])
    ):
        raise ValueError("census target disagrees with worker orbit receipt")
    route = next(
        (
            item
            for item in census.get("routes") or []
            if int(item["sourceR"]) == int(result["sourceR"])
            and int(item["orbitIndex"]) == int(orbit_target["orbitIndex"])
            and str(item["targetLabel"]) == str(result["targetLabel"])
        ),
        None,
    )
    if route is None:
        raise ValueError("missing pinned source-signature route")
    compatible = []
    for profile in census.get("profiles") or []:
        if int(profile["sourceR"]) != int(result["sourceR"]):
            continue
        for target in profile.get("targets") or []:
            if int(target["orbitIndex"]) == int(route["orbitIndex"]):
                compatible.append(
                    {
                        "classIndex": int(profile["classIndex"]),
                        "classSize": int(profile["classSize"]),
                        "targetR": int(target["targetR"]),
                    }
                )
    if {item["targetR"] for item in compatible} != {16}:
        raise ValueError("source signature no longer forces target r=16")

    expected_degrees = sorted(int(value) for value in census["orbitSizes"])
    factor_certificate = result["orbitCertificate"]
    factor_proof = {
        "censusOrbitSizes": expected_degrees,
        "actualFactorDegrees": sorted(
            int(value) for value in factor_certificate["actualDegrees"]
        ),
        "expectedFactorDegrees": sorted(
            int(value) for value in factor_certificate["expectedDegrees"]
        ),
        "factorExponents": [
            int(value) for value in factor_certificate["exponents"]
        ],
        "uniqueDegree24FactorCount": sum(value == 24 for value in expected_degrees),
        "resolventSha256": str(result["attempts"][0]["resolventSha256"]),
        "transform": result["transform"],
        "factorSeconds": float(result["attempts"][0]["factorSeconds"]),
    }
    if not (
        factor_proof["censusOrbitSizes"]
        == factor_proof["actualFactorDegrees"]
        == factor_proof["expectedFactorDegrees"]
        and factor_proof["uniqueDegree24FactorCount"] == 1
        and set(factor_proof["factorExponents"]) == {1}
    ):
        raise ValueError("factorization receipt fails the exact orbit proof")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        source = connection.execute(
            """
            SELECT p.coefficients,p.original_line,p.coefficient_hash,
                   v.label,v.t,v.r,v.status,v.scoreable,
                   s.created_at,s.updated_at,s.description
            FROM polynomials p
            JOIN verifications v USING(submission_id,polynomial_index)
            JOIN submissions s USING(submission_id)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (
                result["sourceSubmissionId"],
                int(result["sourcePolynomialIndex"]),
            ),
        ).fetchone()
        if source is None:
            raise ValueError("pinned source receipt is absent from the ledger")
        source_pin = {
            "submissionId": str(result["sourceSubmissionId"]),
            "polynomialIndex": int(result["sourcePolynomialIndex"]),
            "coefficientSha256": str(source["coefficient_hash"]),
            "coefficientLineSha256": sha256_bytes(
                str(source["coefficients"]).encode("ascii")
            ),
            "originalLineSha256": sha256_bytes(
                str(source["original_line"]).encode("utf-8")
            ),
            "label": str(source["label"]),
            "t": int(source["t"]),
            "r": int(source["r"]),
            "status": str(source["status"]),
            "scoreable": int(source["scoreable"]),
            "submissionCreatedAt": str(source["created_at"]),
            "submissionUpdatedAt": str(source["updated_at"]),
            "submissionDescription": source["description"],
        }
        if (
            source_pin["coefficientSha256"]
            != str(result["sourceCoefficientSha256"])
            or source_pin["label"] != "24T14142"
            or source_pin["r"] != 20
            or source_pin["status"] != "accepted"
            or source_pin["scoreable"] != 1
        ):
            raise ValueError("source receipt pin failed")

        target = connection.execute(
            """
            SELECT team_count,discovered,generated_at
            FROM targets WHERE label=? AND r=?
            """,
            (result["targetLabel"], int(result["targetR"])),
        ).fetchone()
        baseline_matches = int(
            connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                (result["targetLabel"], int(result["targetR"])),
            ).fetchone()[0]
        )
        owned_matches = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND scoreable=1
                """,
                (result["targetLabel"], int(result["targetR"])),
            ).fetchone()[0]
        )
        hash_matches = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0]
        )
        line_matches = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficients=?",
                (line,),
            ).fetchone()[0]
        )
    finally:
        connection.close()

    target_gate = {
        "label": str(result["targetLabel"]),
        "r": int(result["targetR"]),
        "teamCount": int(target["team_count"]) if target is not None else None,
        "discovered": int(target["discovered"]) if target is not None else None,
        "generatedAt": str(target["generated_at"]) if target is not None else None,
        "baselineMatches": baseline_matches,
        "ownedScoreableMatches": owned_matches,
    }
    target_gate["currentTc0"] = bool(
        target is not None
        and target_gate["teamCount"] == 0
        and target_gate["discovered"] == 0
        and baseline_matches == 0
        and owned_matches == 0
    )
    if not target_gate["currentTc0"]:
        raise ValueError("target no longer passes the current tc0 gate")

    result_rel = str(result_path.relative_to(ROOT))
    output_rel = str(output_path.relative_to(ROOT))
    allowed_current_family = {result_rel, output_rel}
    if WAVE_HITS.exists():
        allowed_current_family.add(str(WAVE_HITS.relative_to(ROOT)))
    if WAVE_RESULTS.exists():
        allowed_current_family.add(str(WAVE_RESULTS.relative_to(ROOT)))
    hash_artifacts = artifact_matches(candidate_hash)
    line_artifacts = artifact_matches(line)
    unexpected_hash_artifacts = sorted(
        set(hash_artifacts) - allowed_current_family
    )
    unexpected_line_artifacts = sorted(
        set(line_artifacts) - allowed_current_family
    )
    duplicate_exclusions = {
        "ledgerCoefficientHashMatches": hash_matches,
        "ledgerCoefficientLineMatches": line_matches,
        "artifactHashMatches": hash_artifacts,
        "artifactLineMatches": line_artifacts,
        "allowedCurrentFamilyArtifacts": sorted(allowed_current_family),
        "unexpectedPriorHashArtifacts": unexpected_hash_artifacts,
        "unexpectedPriorLineArtifacts": unexpected_line_artifacts,
        "passes": (
            hash_matches == 0
            and line_matches == 0
            and not unexpected_hash_artifacts
            and not unexpected_line_artifacts
        ),
    }
    if not duplicate_exclusions["passes"]:
        raise ValueError("candidate fails duplicate exclusions")

    manifest = {
        "schemaVersion": "f6-post22-exact-hit-certificate-v1",
        "status": "certified_current_tc0",
        "candidateCoefficientLine": line,
        "candidateChecks": candidate_checks,
        "targetGate": target_gate,
        "sourceReceiptPin": source_pin,
        "actionProof": {
            "actionKind": str(census["actionKind"]),
            "censusExactCertificateSha256": str(
                census["exactCertificateSha256"]
            ),
            "censusPath": str(CENSUS.relative_to(ROOT)),
            "censusSha256": sha256_file(CENSUS),
            "sourceLabel": str(census["sourceLabel"]),
            "sourceOrder": int(census["sourceOrder"]),
            "sourceR": int(result["sourceR"]),
            "orbitTarget": orbit_target,
            "route": route,
            "compatibleSourceSignatureClasses": compatible,
            "deterministicTargetR": 16,
        },
        "factorProof": factor_proof,
        "duplicateExclusions": duplicate_exclusions,
        "workerReceipt": {
            "path": result_rel,
            "sha256": sha256_file(result_path),
            "status": str(result["status"]),
            "elapsedSeconds": float(result["elapsedSeconds"]),
            "workerExitCode": int(result["workerExitCode"]),
        },
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    atomic_new(output_path, payload)
    print(
        canonical_json(
            {
                "candidateCoefficientSha256": candidate_hash,
                "manifest": str(output_path),
                "manifestSha256": sha256_bytes(payload),
                "status": manifest["status"],
                "targetLabel": result["targetLabel"],
                "targetR": result["targetR"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

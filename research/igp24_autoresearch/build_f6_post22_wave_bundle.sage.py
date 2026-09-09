#!/usr/bin/env sage -python
"""Seal the four exact F6 post-22 hits as a line batch and proof bundle."""

from __future__ import annotations

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
RESULTS = CAMPAIGN / "f6_post22_unique_wave_results.jsonl"
HITS = CAMPAIGN / "f6_post22_unique_wave_exact_hits.jsonl"
SUMMARY = CAMPAIGN / "f6_post22_unique_wave_summary.json"
FIRST_MANIFEST = CAMPAIGN / "f6_post22_14293_r16_certificate_manifest.json"
BATCH = CAMPAIGN / "f6_post22_unique_wave_exact_4lines.txt"
CERTIFICATE = CAMPAIGN / "f6_post22_unique_wave_cumulative_certificate.json"


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


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


def artifact_matches(pattern: str) -> list[str]:
    completed = subprocess.run(
        ["rg", "-l", "-F", "--", pattern, str(DATA)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"rg scan failed: {completed.stderr[-500:]}")
    return sorted(
        {
            str(Path(line).resolve().relative_to(ROOT))
            for line in completed.stdout.splitlines()
            if line.strip()
        }
    )


def current_family_allowlist() -> set[str]:
    paths = {
        CENSUS,
        RESULTS,
        HITS,
        SUMMARY,
        FIRST_MANIFEST,
        BATCH,
        CERTIFICATE,
        DATA / "f6_post22_14142_r20_to_14293_unique.jsonl",
        DATA / "f6_post22_15266_r16_to_15251_unique.jsonl",
    }
    return {str(path.relative_to(ROOT)) for path in paths}


def main() -> int:
    hit_rows = read_jsonl(HITS)
    if len(hit_rows) != 4:
        raise ValueError(f"expected exactly four live hits, found {len(hit_rows)}")
    hits = sorted(
        (row["candidate"] for row in hit_rows),
        key=lambda row: (
            int(row["targetT"]),
            int(row["targetR"]),
            str(row["coefficientSha256"]),
        ),
    )
    expected_pairs = {
        ("24T10482", 8),
        ("24T14293", 16),
        ("24T16948", 16),
        ("24T16949", 16),
    }
    realized_pairs = {
        (str(candidate["targetLabel"]), int(candidate["targetR"]))
        for candidate in hits
    }
    if realized_pairs != expected_pairs:
        raise ValueError(f"four-hit set changed: {sorted(realized_pairs)}")

    census_rows = {
        str(row["sourceLabel"]): row for row in read_jsonl(CENSUS)
    }
    ring = PolynomialRing(ZZ, "x")
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    allowlist = current_family_allowlist()
    certified = []
    try:
        for ordinal, candidate in enumerate(hits, 1):
            line = str(candidate["coefficientLine"])
            digest = sha256_bytes(line.encode("ascii"))
            coefficients = [int(value) for value in line.split(",")]
            polynomial = ring(coefficients)
            checks = {
                "degree": int(polynomial.degree()),
                "monic": bool(polynomial.is_monic()),
                "primitive": math.gcd(*coefficients) == 1,
                "nonzeroConstant": coefficients[0] != 0,
                "irreducibleOverQ": bool(polynomial.is_irreducible()),
                "realRootCount": int(polynomial.number_of_real_roots()),
                "coefficientBytes": len(line.encode("ascii")),
                "coefficientSha256": digest,
                "polynomialDiscriminantAbs": str(
                    abs(int(polynomial.discriminant()))
                ),
                "fieldDiscriminantAbs": str(abs(int(pari(polynomial).nfdisc()))),
            }
            expected_checks = {
                "degree": 24,
                "monic": True,
                "primitive": True,
                "nonzeroConstant": True,
                "irreducibleOverQ": True,
                "realRootCount": int(candidate["targetR"]),
                "coefficientBytes": int(candidate["coefficientBytes"]),
                "coefficientSha256": str(candidate["coefficientSha256"]),
                "polynomialDiscriminantAbs": str(
                    candidate["polynomialDiscriminantAbs"]
                ),
                "fieldDiscriminantAbs": str(candidate["fieldDiscriminantAbs"]),
            }
            if checks != expected_checks:
                raise ValueError(
                    f"candidate arithmetic mismatch for {candidate['targetLabel']}"
                )

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
                    candidate["sourceSubmissionId"],
                    int(candidate["sourcePolynomialIndex"]),
                ),
            ).fetchone()
            if source is None:
                raise ValueError("source receipt disappeared")
            source_pin = {
                "submissionId": str(candidate["sourceSubmissionId"]),
                "polynomialIndex": int(candidate["sourcePolynomialIndex"]),
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
                != str(candidate["sourceCoefficientSha256"])
                or source_pin["label"] != str(candidate["sourceLabel"])
                or source_pin["r"] != int(candidate["sourceR"])
                or source_pin["status"] != "accepted"
                or source_pin["scoreable"] != 1
            ):
                raise ValueError("source receipt pin mismatch")

            census = census_rows[str(candidate["sourceLabel"])]
            if int(census["length24OrbitCount"]) != 1:
                raise ValueError("hit source is no longer a unique-orbit action")
            orbit = census["targets"][0]
            if str(orbit["targetLabel"]) != str(candidate["targetLabel"]):
                raise ValueError("unique orbit label mismatch")
            route = next(
                (
                    item
                    for item in census.get("routes") or []
                    if int(item["sourceR"]) == int(candidate["sourceR"])
                    and int(item["orbitIndex"]) == int(orbit["orbitIndex"])
                    and str(item["targetLabel"]) == str(candidate["targetLabel"])
                ),
                None,
            )
            if route is None or int(candidate["targetR"]) not in {
                int(value) for value in route["goldR"]
            }:
                raise ValueError("realized signature is not in the sealed gold route")
            compatible = []
            for profile in census.get("profiles") or []:
                if int(profile["sourceR"]) != int(candidate["sourceR"]):
                    continue
                for target in profile.get("targets") or []:
                    if int(target["orbitIndex"]) == int(orbit["orbitIndex"]):
                        compatible.append(
                            {
                                "classIndex": int(profile["classIndex"]),
                                "classSize": int(profile["classSize"]),
                                "targetR": int(target["targetR"]),
                            }
                        )
            if int(candidate["targetR"]) not in {
                item["targetR"] for item in compatible
            }:
                raise ValueError("realized signature contradicts action profiles")

            factor = candidate["orbitCertificate"]
            expected_degrees = sorted(int(value) for value in census["orbitSizes"])
            factor_proof = {
                "censusOrbitSizes": expected_degrees,
                "actualFactorDegrees": sorted(
                    int(value) for value in factor["actualDegrees"]
                ),
                "expectedFactorDegrees": sorted(
                    int(value) for value in factor["expectedDegrees"]
                ),
                "factorExponents": [
                    int(value) for value in factor["exponents"]
                ],
                "uniqueDegree24FactorCount": sum(
                    value == 24 for value in expected_degrees
                ),
                "resolventSha256": str(
                    candidate["attempts"][0]["resolventSha256"]
                ),
                "transform": candidate["transform"],
                "reduction": str(candidate["reduction"]),
            }
            if not (
                factor_proof["censusOrbitSizes"]
                == factor_proof["actualFactorDegrees"]
                == factor_proof["expectedFactorDegrees"]
                and factor_proof["uniqueDegree24FactorCount"] == 1
                and set(factor_proof["factorExponents"]) == {1}
            ):
                raise ValueError("factor/orbit certificate mismatch")

            pair = (str(candidate["targetLabel"]), int(candidate["targetR"]))
            target = connection.execute(
                """
                SELECT team_count,discovered,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                pair,
            ).fetchone()
            baseline = int(
                connection.execute(
                    "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                    pair,
                ).fetchone()[0]
            )
            owned = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM verifications
                    WHERE label=? AND r=? AND scoreable=1
                    """,
                    pair,
                ).fetchone()[0]
            )
            target_gate = {
                "label": pair[0],
                "r": pair[1],
                "teamCount": int(target["team_count"])
                if target is not None
                else None,
                "discovered": int(target["discovered"])
                if target is not None
                else None,
                "generatedAt": str(target["generated_at"])
                if target is not None
                else None,
                "baselineMatches": baseline,
                "ownedScoreableMatches": owned,
            }
            target_gate["currentTc0"] = bool(
                target is not None
                and target_gate["teamCount"] == 0
                and target_gate["discovered"] == 0
                and baseline == 0
                and owned == 0
            )
            if not target_gate["currentTc0"]:
                raise ValueError(f"target gate closed for {pair}")

            ledger_hash_matches = int(
                connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                    (digest,),
                ).fetchone()[0]
            )
            ledger_line_matches = int(
                connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficients=?",
                    (line,),
                ).fetchone()[0]
            )
            hash_artifacts = artifact_matches(digest)
            line_artifacts = artifact_matches(line)
            unexpected_hash = sorted(set(hash_artifacts) - allowlist)
            unexpected_line = sorted(set(line_artifacts) - allowlist)
            duplicate_gate = {
                "ledgerCoefficientHashMatches": ledger_hash_matches,
                "ledgerCoefficientLineMatches": ledger_line_matches,
                "artifactHashMatches": hash_artifacts,
                "artifactLineMatches": line_artifacts,
                "allowedCurrentFamilyArtifacts": sorted(allowlist),
                "unexpectedPriorHashArtifacts": unexpected_hash,
                "unexpectedPriorLineArtifacts": unexpected_line,
                "passes": (
                    ledger_hash_matches == 0
                    and ledger_line_matches == 0
                    and not unexpected_hash
                    and not unexpected_line
                ),
            }
            if not duplicate_gate["passes"]:
                raise ValueError(f"duplicate gate failed for {pair}")

            certified.append(
                {
                    "batchLineOrdinal": ordinal,
                    "candidateCoefficientLine": line,
                    "candidateChecks": checks,
                    "targetGate": target_gate,
                    "duplicateExclusions": duplicate_gate,
                    "sourceReceiptPin": source_pin,
                    "actionProof": {
                        "actionKind": str(census["actionKind"]),
                        "censusExactCertificateSha256": str(
                            census["exactCertificateSha256"]
                        ),
                        "sourceOrder": int(census["sourceOrder"]),
                        "orbitTarget": orbit,
                        "route": route,
                        "compatibleSourceSignatureClasses": compatible,
                    },
                    "factorProof": factor_proof,
                }
            )
    finally:
        connection.close()

    hashes = [
        row["candidateChecks"]["coefficientSha256"] for row in certified
    ]
    if len(set(hashes)) != 4:
        raise ValueError("candidate coefficient hashes are not pairwise distinct")
    batch_payload = "".join(
        row["candidateCoefficientLine"] + "\n" for row in certified
    ).encode("ascii")
    if len(batch_payload.splitlines()) != 4:
        raise ValueError("batch payload is not exactly four lines")
    batch_sha = sha256_bytes(batch_payload)
    certificate = {
        "schemaVersion": "f6-post22-unique-wave-cumulative-certificate-v1",
        "status": "certified_4_current_tc0",
        "batch": {
            "path": str(BATCH.relative_to(ROOT)),
            "sha256": batch_sha,
            "lineCount": 4,
            "ordering": "targetT,targetR,candidateCoefficientSha256",
        },
        "hits": certified,
        "crossCandidateChecks": {
            "distinctPairs": len(realized_pairs) == 4,
            "distinctCoefficientHashes": len(set(hashes)) == 4,
            "allDegree24Irreducible": all(
                row["candidateChecks"]["degree"] == 24
                and row["candidateChecks"]["irreducibleOverQ"]
                for row in certified
            ),
            "allCurrentTc0": all(
                row["targetGate"]["currentTc0"] for row in certified
            ),
            "allDuplicateExclusionsPass": all(
                row["duplicateExclusions"]["passes"] for row in certified
            ),
        },
        "inputs": {
            "censusPath": str(CENSUS.relative_to(ROOT)),
            "censusSha256": sha256_file(CENSUS),
            "resultsPath": str(RESULTS.relative_to(ROOT)),
            "resultsSha256": sha256_file(RESULTS),
            "hitsPath": str(HITS.relative_to(ROOT)),
            "hitsSha256": sha256_file(HITS),
            "summaryPath": str(SUMMARY.relative_to(ROOT)),
            "summarySha256": sha256_file(SUMMARY),
        },
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    certificate_payload = (
        json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_new(BATCH, batch_payload)
    atomic_new(CERTIFICATE, certificate_payload)
    print(
        canonical_json(
            {
                "batch": str(BATCH.relative_to(ROOT)),
                "batchLineCount": 4,
                "batchSha256": batch_sha,
                "certificate": str(CERTIFICATE.relative_to(ROOT)),
                "certificateSha256": sha256_bytes(certificate_payload),
                "status": certificate["status"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Seal the shortest certified q12T138 representatives into one manifest.

This is intentionally an offline staging step.  It revalidates the six
polynomials and their recorded Frobenius witnesses, checks that the frozen
target snapshot still marks the pairs as unowned tc0, and makes no network or
submission call.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
MANIFEST = OUTBOX / "q138_character_24T20767_six_gold_20260730.txt"
CERTIFICATE = DATA / "q138_character_24T20767_six_gold_stage_20260730.json"

ARTIFACTS = {
    DATA / "character_field_gate_q138_exact_517a67f30ace_20260730.json":
        "9fa9ccbdd6422fd2541d95da0745e2677eb9d8dfae4e55a5ff401e980be2b54d",
    DATA / "character_field_gate_q138_exact_6d2504bfd0ae_20260730.json":
        "fe9ea07cbb5febcc53accda212d5c97d8b3de6d806ad3c4aa4399276af67721d",
    DATA / "character_field_gate_q138_exact_7ec789b697f3_20260730.json":
        "a16ff4b6a7548eaba8c6fd28b57f0683194c20aa66a2facb124484c285f9ab33",
}
EXPECTED_SELECTION = {
    4: "52d15a01ea2f03ff506c0a54af6ac91069102988cb365c9c3a5472e8fedc85c7",
    8: "7e662037e0595d60de4cc2c9319eb2b1d4e2ff3d951431b878cb9876e322dfae",
    12: "a4ff0f27101081d96bdf0f4adcacbb548311dcdc64e20e1028487a776a5aefb5",
    16: "851227f8530f5252cb39fd0df972da090cd865ab9095e7b1a2b0aaa01f30ccb3",
    20: "5087111960f88d3873753ccfe7010780b7d657a92b087bfc48a022307fd05354",
    24: "4d31376424d7b75f4719dafa28395898cebeaa3aeded6d3fbbb3098c2cd8e03c",
}
TARGET_LABEL = "24T20767"
TARGET_GENERATED_AT = "2026-07-29T21:37:08Z"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def factor_degrees_mod_prime(polynomial, prime: int) -> list[int]:
    finite_polynomial = polynomial.change_ring(GF(prime))
    require(
        finite_polynomial.gcd(finite_polynomial.derivative()).degree() == 0,
        f"recorded witness prime {prime} is ramified",
    )
    degrees = []
    for factor, exponent in finite_polynomial.factor():
        degrees.extend([int(factor.degree())] * int(exponent))
    return sorted(degrees)


def collect_candidates():
    ring = PolynomialRing(QQ, "x")
    candidates = []
    artifact_audit = []
    for path, expected_hash in ARTIFACTS.items():
        actual_hash = file_sha256(path)
        require(
            actual_hash == expected_hash,
            f"exact artifact drift for {path.name}: {actual_hash}",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(payload["audit"]["conditionalOnGRH"] is True, "missing GRH flag")
        require(payload["audit"]["networkCalls"] == 0, "artifact used network")
        require(payload["audit"]["submissionCalls"] == 0, "artifact submitted")
        require(
            payload["audit"]["targetSnapshotGeneratedAt"]
            == [TARGET_GENERATED_AT],
            "target snapshot mismatch",
        )
        artifact_audit.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": actual_hash,
            }
        )
        for field in payload["fields"]:
            field_hash = field["fieldCanonicalSha256"]
            for pair in field["exactSelmerSignGate"].get("pairs", []):
                if pair.get("status") != "exact_selmer_sign_pass":
                    continue
                require(pair["label"] == TARGET_LABEL, "unexpected passing label")
                require(pair["exactCharacterMatch"] is True, "character mismatch")
                require(pair["localPass"] is True, "local gate mismatch")
                require(pair["teamCount"] == 0, "non-gold artifact route")
                require(
                    pair["possibleLabelsForCore"] == [TARGET_LABEL],
                    "ambiguous q138 route cannot be staged",
                )
                reconstruction = pair["firstExactSelmerReconstruction"]
                line = reconstruction["candidateCoefficientLine"]
                encoded = line.encode("ascii")
                digest = sha256_bytes(encoded)
                r = int(pair["r"])
                require(
                    digest == reconstruction["candidateSha256"],
                    "candidate digest mismatch",
                )
                require(
                    reconstruction["candidateStatus"]
                    == f"certified_{TARGET_LABEL}_r{r}",
                    "candidate status mismatch",
                )
                coefficients = [ZZ(value) for value in line.split(",")]
                require(len(coefficients) == 25, "candidate is not degree 24")
                require(coefficients[-1] == 1, "candidate is not monic")
                polynomial = ring(coefficients)
                require(polynomial.is_irreducible(), "candidate is reducible")
                require(
                    int(polynomial.number_of_real_roots()) == r,
                    "candidate real-root mismatch",
                )
                certificate = reconstruction["maximalSubgroupCertificate"]
                require(certificate["complete"] is True, "incomplete group proof")
                maximal_rows = certificate["properTransitiveMaximals"]
                require(len(maximal_rows) == 10, "unexpected maximal count")
                checked_witnesses = []
                for maximal in maximal_rows:
                    witness = maximal["witness"]
                    prime = int(witness["prime"])
                    actual_cycle = factor_degrees_mod_prime(polynomial, prime)
                    expected_cycle = sorted(
                        int(value) for value in witness["candidateCycleType"]
                    )
                    require(
                        actual_cycle == expected_cycle,
                        f"Frobenius witness drift at p={prime}",
                    )
                    checked_witnesses.append(
                        {
                            "excludedMaximalLabel": maximal["label"],
                            "prime": prime,
                            "verifiedCycleType": actual_cycle,
                        }
                    )
                candidates.append(
                    {
                        "bytes": len(encoded),
                        "fieldCanonicalSha256": field_hash,
                        "frobeniusWitnesses": checked_witnesses,
                        "line": line,
                        "r": r,
                        "sha256": digest,
                    }
                )
    return candidates, artifact_audit


def validate_live_and_unique(selected: list[dict]) -> list[dict]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        live_rows = []
        for candidate in selected:
            r = candidate["r"]
            target = connection.execute(
                """
                SELECT team_count,discovered,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                (TARGET_LABEL, r),
            ).fetchone()
            require(
                target == (0, 0, TARGET_GENERATED_AT),
                f"pair no longer frozen tc0: {TARGET_LABEL}/r{r}: {target}",
            )
            baseline = connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                (TARGET_LABEL, r),
            ).fetchone()[0]
            owned = connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND scoreable=1
                """,
                (TARGET_LABEL, r),
            ).fetchone()[0]
            ledger_hash = connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate["sha256"],),
            ).fetchone()[0]
            require(baseline == 0, f"baseline pair: {TARGET_LABEL}/r{r}")
            require(owned == 0, f"already owned pair: {TARGET_LABEL}/r{r}")
            require(ledger_hash == 0, f"candidate already in ledger: {candidate['sha256']}")
            live_rows.append(
                {
                    "discovered": False,
                    "generatedAt": target[2],
                    "label": TARGET_LABEL,
                    "locallyOwned": False,
                    "r": r,
                    "teamCount": 0,
                }
            )
    finally:
        connection.close()

    selected_hashes = {row["sha256"] for row in selected}
    require(len(selected_hashes) == len(selected), "duplicate selected polynomial")
    for path in sorted(OUTBOX.glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and sha256_bytes(stripped.encode("ascii")) in selected_hashes:
                raise RuntimeError(
                    f"candidate already staged in {path.relative_to(ROOT)}"
                )
    return live_rows


def main() -> int:
    candidates, artifact_audit = collect_candidates()
    grouped = {}
    for candidate in candidates:
        grouped.setdefault(candidate["r"], []).append(candidate)
    require(set(grouped) == set(EXPECTED_SELECTION), "signature coverage mismatch")
    selected = [
        min(grouped[r], key=lambda row: (row["bytes"], row["sha256"]))
        for r in sorted(grouped)
    ]
    require(
        {row["r"]: row["sha256"] for row in selected} == EXPECTED_SELECTION,
        "shortest-representative selection drift",
    )
    live_rows = validate_live_and_unique(selected)
    manifest_payload = (
        "\n".join(row["line"] for row in selected) + "\n"
    ).encode("ascii")
    certificate = {
        "audit": {
            "coefficientBoxSearches": 0,
            "exactArtifactCount": len(ARTIFACTS),
            "networkCalls": 0,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": TARGET_GENERATED_AT,
        },
        "exactArtifacts": artifact_audit,
        "manifest": {
            "bytes": len(manifest_payload),
            "path": str(MANIFEST.relative_to(ROOT)),
            "rowCount": len(selected),
            "sha256": sha256_bytes(manifest_payload),
        },
        "mathematicalStatus": {
            "classGroupCompleteness": "conditional_on_GRH_proof_false",
            "polynomialChecks": "exact",
            "targetGroupCertificates": "exact_complete_maximal_subgroup_exclusion",
        },
        "scoreStrategy": {
            "frozenGoldPairs": len(selected),
            "guaranteedScoreAtFrozenTc0": float(len(selected)),
            "selection": "minimum coefficient bytes per signature",
        },
        "selected": [
            {
                key: value
                for key, value in row.items()
                if key != "line"
            }
            for row in selected
        ],
        "targets": live_rows,
    }
    certificate_payload = (
        json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    if MANIFEST.exists():
        require(
            MANIFEST.read_bytes() == manifest_payload,
            f"existing manifest drift: {MANIFEST}",
        )
    else:
        write_atomic(MANIFEST, manifest_payload)
    if CERTIFICATE.exists():
        require(
            CERTIFICATE.read_bytes() == certificate_payload,
            f"existing certificate drift: {CERTIFICATE}",
        )
    else:
        write_atomic(CERTIFICATE, certificate_payload)

    print(
        json.dumps(
            {
                "certificate": str(CERTIFICATE),
                "guaranteedScoreAtFrozenTc0": len(selected),
                "manifest": str(MANIFEST),
                "manifestSha256": sha256_bytes(manifest_payload),
                "rows": [
                    {
                        "bytes": row["bytes"],
                        "r": row["r"],
                        "sha256": row["sha256"],
                    }
                    for row in selected
                ],
                "status": "staged_not_submitted",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

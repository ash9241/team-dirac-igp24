#!/usr/bin/env sage -python
"""Offline staging for the certified q136 and q193 character witnesses."""

from __future__ import annotations

import json
import importlib.util
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
MANIFEST = OUTBOX / "q136_q193_character_four_gold_20260730.txt"
CERTIFICATE = DATA / "q136_q193_character_four_gold_stage_20260730.json"
TARGET_GENERATED_AT = "2026-07-29T21:37:08Z"
ARTIFACTS = {
    DATA / "character_field_gate_q136_exact_317c9398_multi16_20260730.json":
        "7f8cc2f215068f4c6fb4de1b9cb97c8d4d4b33bcb2dab4daf6882596947d172a",
    DATA / "character_field_gate_q193_exact_57768ed9_w20000_20260730.json":
        "f9cf607eec7f1ad1f8330983ab6f77a8b47880c90f5d78b53da895a05a4a9a57",
}
EXPECTED_SELECTION = {
    ("24T20753", 4):
        "377cb7d304125de9e771e624f965227621eae4862c6741b7663843720eed9007",
    ("24T20753", 12):
        "69acab7a27e3fd155d3c6c6370d80a97a7e829cb9173b103e8f16b1da6b618c7",
    ("24T20753", 20):
        "6dd8da1d96906d1149214423afbc40a30ab6f7d7f0d8e9a3e3c7a94f8f17adc6",
    ("24T21913", 20):
        "14497e47fec1c4d5b42e9f63298131ef017baaf3c18d1c6934d7b462e659b669",
}


def load_stage_helpers():
    path = ROOT / "stage_q138_character_packet_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("q138_stage_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import staging helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPERS = load_stage_helpers()
factor_degrees_mod_prime = HELPERS.factor_degrees_mod_prime
file_sha256 = HELPERS.file_sha256
require = HELPERS.require
sha256_bytes = HELPERS.sha256_bytes
write_atomic = HELPERS.write_atomic


def selected_reconstruction(pair: dict):
    certified = pair.get("certifiedExactSelmerReconstruction")
    if certified and str(certified.get("candidateStatus", "")).startswith(
        "certified_"
    ):
        return certified
    first = pair.get("firstExactSelmerReconstruction")
    if first and str(first.get("candidateStatus", "")).startswith("certified_"):
        return first
    return None


def collect_candidates():
    ring = PolynomialRing(QQ, "x")
    candidates = []
    artifact_rows = []
    for path, expected_artifact_hash in ARTIFACTS.items():
        actual_artifact_hash = file_sha256(path)
        require(
            actual_artifact_hash == expected_artifact_hash,
            f"artifact drift: {path.name}",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(payload["audit"]["conditionalOnGRH"] is True, "missing GRH flag")
        require(payload["audit"]["networkCalls"] == 0, "artifact used network")
        require(payload["audit"]["submissionCalls"] == 0, "artifact submitted")
        artifact_timestamps = sorted(
            str(value)
            for value in payload["audit"]["targetSnapshotGeneratedAt"]
        )
        require(artifact_timestamps, "missing target snapshot timestamp")
        artifact_rows.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": actual_artifact_hash,
            }
        )
        for field in payload["fields"]:
            for pair in field["exactSelmerSignGate"].get("pairs", []):
                key = (str(pair["label"]), int(pair["r"]))
                if key not in EXPECTED_SELECTION:
                    continue
                reconstruction = selected_reconstruction(pair)
                if reconstruction is None:
                    continue
                line = reconstruction["candidateCoefficientLine"]
                encoded = line.encode("ascii")
                digest = sha256_bytes(encoded)
                require(
                    digest == reconstruction["candidateSha256"],
                    "candidate digest mismatch",
                )
                if digest != EXPECTED_SELECTION[key]:
                    continue
                coefficients = [ZZ(value) for value in line.split(",")]
                require(
                    len(coefficients) == 25 and coefficients[-1] == 1,
                    "candidate is not monic degree 24",
                )
                polynomial = ring(coefficients)
                require(polynomial.is_irreducible(), "candidate is reducible")
                require(
                    int(polynomial.number_of_real_roots()) == key[1],
                    "candidate real-root mismatch",
                )
                group_certificate = reconstruction[
                    "maximalSubgroupCertificate"
                ]
                require(group_certificate["complete"] is True, "group incomplete")
                witness_rows = []
                for maximal in group_certificate["properTransitiveMaximals"]:
                    witness = maximal["witness"]
                    require(witness is not None, "missing maximal witness")
                    prime = int(witness["prime"])
                    actual_cycle = factor_degrees_mod_prime(polynomial, prime)
                    expected_cycle = sorted(
                        int(value) for value in witness["candidateCycleType"]
                    )
                    require(
                        actual_cycle == expected_cycle,
                        f"Frobenius drift for {key} at p={prime}",
                    )
                    witness_rows.append(
                        {
                            "excludedMaximalLabel": maximal["label"],
                            "prime": prime,
                            "verifiedCycleType": actual_cycle,
                        }
                    )
                candidates.append(
                    {
                        "bytes": len(encoded),
                        "fieldCanonicalSha256":
                            field["fieldCanonicalSha256"],
                        "frobeniusWitnesses": witness_rows,
                        "label": key[0],
                        "line": line,
                        "r": key[1],
                        "sha256": digest,
                        "targetSnapshotGeneratedAt": artifact_timestamps,
                    }
                )
    return candidates, artifact_rows


def validate_targets_and_outbox(candidates: list[dict]) -> list[dict]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    target_rows = []
    try:
        for candidate in candidates:
            pair = (candidate["label"], candidate["r"])
            target = connection.execute(
                """
                SELECT team_count,discovered,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                pair,
            ).fetchone()
            require(target is not None, f"target pair missing: {pair}")
            require(
                target[0:2] == (0, 0),
                f"pair no longer frozen tc0: {pair}: {target}",
            )
            require(
                str(target[2]) in candidate["targetSnapshotGeneratedAt"],
                f"target timestamp drift for {pair}: {target[2]}",
            )
            baseline = connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                pair,
            ).fetchone()[0]
            owned = connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND scoreable=1
                """,
                pair,
            ).fetchone()[0]
            ledger_hash = connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate["sha256"],),
            ).fetchone()[0]
            require(baseline == 0, f"baseline pair: {pair}")
            require(owned == 0, f"already-owned pair: {pair}")
            require(ledger_hash == 0, "candidate already in ledger")
            target_rows.append(
                {
                    "discovered": False,
                    "generatedAt": target[2],
                    "label": pair[0],
                    "locallyOwned": False,
                    "r": pair[1],
                    "teamCount": 0,
                }
            )
    finally:
        connection.close()

    selected_hashes = {row["sha256"] for row in candidates}
    require(len(selected_hashes) == len(candidates), "duplicate candidate hashes")
    for path in sorted(OUTBOX.glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and sha256_bytes(stripped.encode("ascii")) in selected_hashes:
                raise RuntimeError(
                    f"candidate already staged in {path.relative_to(ROOT)}"
                )
    return target_rows


def main() -> int:
    candidates, artifact_rows = collect_candidates()
    by_pair = {(row["label"], row["r"]): row for row in candidates}
    require(set(by_pair) == set(EXPECTED_SELECTION), "packet coverage mismatch")
    require(len(by_pair) == len(candidates), "multiple selected candidates per pair")
    selected = [
        by_pair[pair]
        for pair in sorted(
            by_pair,
            key=lambda pair: (int(pair[0][3:]), pair[1]),
        )
    ]
    target_rows = validate_targets_and_outbox(selected)
    manifest_payload = (
        "\n".join(row["line"] for row in selected) + "\n"
    ).encode("ascii")
    certificate = {
        "audit": {
            "coefficientBoxSearches": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    timestamp
                    for row in selected
                    for timestamp in row["targetSnapshotGeneratedAt"]
                }
            ),
        },
        "exactArtifacts": artifact_rows,
        "manifest": {
            "bytes": len(manifest_payload),
            "path": str(MANIFEST.relative_to(ROOT)),
            "rowCount": len(selected),
            "sha256": sha256_bytes(manifest_payload),
        },
        "mathematicalStatus": {
            "classGroupCompleteness": "conditional_on_GRH_proof_false",
            "polynomialChecks": "exact",
            "targetGroupCertificates":
                "exact_complete_maximal_subgroup_exclusion",
        },
        "scoreStrategy": {
            "frozenGoldPairs": len(selected),
            "guaranteedScoreAtFrozenTc0": float(len(selected)),
        },
        "selected": [
            {key: value for key, value in row.items() if key != "line"}
            for row in selected
        ],
        "targets": target_rows,
    }
    certificate_payload = (
        json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    require(not MANIFEST.exists(), f"refusing to overwrite {MANIFEST}")
    require(not CERTIFICATE.exists(), f"refusing to overwrite {CERTIFICATE}")
    write_atomic(MANIFEST, manifest_payload)
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
                        "label": row["label"],
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

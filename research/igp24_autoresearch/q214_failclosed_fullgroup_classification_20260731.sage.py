#!/usr/bin/env sage -python
"""Fail-closed full-group audit for every exact core-1 q214 reconstruction.

This script does not infer containment in 24T22560 from a norm core.  It first
enumerates every degree-24 transitive group having an exact 12T214 quotient on
12 blocks of size two, excludes only the rank-12 group by the proved square
norm relation, and then applies archimedean and squarefree Dedekind cycle-type
exclusions to every retained exact Selmer reconstruction.

A candidate is called 24T22560 only if the exhaustive compatible catalog is
reduced to that singleton.  The expected result is a fail-closed audit with no
such candidate and therefore no stageable q214 packet.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from collections import Counter
from pathlib import Path

from sage.all import PolynomialRing, ZZ, prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HELPER_PATH = ROOT / "certify_shifted_full_kummer_lift.sage.py"
OUTPUT = DATA / "q214_failclosed_fullgroup_classification_20260731.json"
INPUTS = (
    DATA / "broad_structural_character_gate_q214_core1_all_reconstructions_20260731.json",
    DATA / "broad_structural_character_gate_q214_dense_core1_all_reconstructions_20260731.json",
)
QUOTIENT_T = 214
QUOTIENT_ORDER = 1296
TARGET_T = 22560
PRIME_BOUND = 2000
MAX_KERNEL_RANK_FROM_SQUARE_NORM = 11
RING = PolynomialRing(ZZ, "x")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_helper():
    spec = importlib.util.spec_from_file_location("q214_catalog_helper", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} is not a JSON object")
    return value


def polynomial_from_line(line: str):
    coefficients = [ZZ(value) for value in line.split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("candidate coefficient line is not monic degree 24")
    return RING(coefficients)


def candidate_rows(path: Path, payload: dict):
    for field in payload.get("fields") or []:
        field_hash = str(field["fieldCanonicalSha256"])
        pairs = ((field.get("exactSelmerSignGate") or {}).get("pairs") or [])
        for pair in pairs:
            if int(pair["core"]) != 1 or str(pair["label"]) != "24T22560":
                continue
            seen = set()
            for reconstruction in pair.get("testedExactSelmerReconstructions") or []:
                line = reconstruction.get("candidateCoefficientLine")
                candidate_hash = reconstruction.get("candidateSha256")
                if line is None or candidate_hash is None:
                    continue
                candidate_hash = str(candidate_hash)
                if candidate_hash in seen:
                    continue
                seen.add(candidate_hash)
                if hashlib.sha256(str(line).encode()).hexdigest() != candidate_hash:
                    raise ValueError(f"candidate hash mismatch: {candidate_hash}")
                if not bool(reconstruction.get("normOverCoreIsSquare")):
                    raise ValueError(
                        f"rank-12 exclusion unavailable for {candidate_hash}"
                    )
                yield {
                    "candidateSha256": candidate_hash,
                    "coefficientLine": str(line),
                    "fieldCanonicalSha256": field_hash,
                    "inputArtifact": str(path.relative_to(ROOT)),
                    "r": int(pair["r"]),
                    "realRoots": int(reconstruction["candidateRealRoots"]),
                }


def classify_candidate(helper, row: dict, profiles: dict[int, set], labels: set[int]):
    candidate = polynomial_from_line(row.pop("coefficientLine"))
    if not candidate.is_irreducible():
        raise ArithmeticError(f"reconstruction became reducible: {row['candidateSha256']}")
    if int(candidate.number_of_real_roots()) != int(row["realRoots"]):
        raise ArithmeticError(f"real-root mismatch: {row['candidateSha256']}")

    real_roots = int(row["realRoots"])
    archimedean_type = tuple([1] * real_roots + [2] * ((24 - real_roots) // 2))
    remaining = {target_t for target_t in labels if archimedean_type in profiles[target_t]}
    elimination = [
        {
            "afterCount": len(remaining),
            "cycleType": list(archimedean_type),
            "evidence": "archimedean_complex_conjugation",
        }
    ]
    checked = 0
    for prime in prime_range(2, PRIME_BOUND + 1):
        prime = int(prime)
        observed = helper.cycle_type(candidate, prime)
        if observed is None:
            continue
        checked += 1
        reduced = {
            target_t
            for target_t in remaining
            if observed in profiles[target_t]
        }
        if len(reduced) < len(remaining):
            elimination.append(
                {
                    "afterCount": len(reduced),
                    "cycleType": list(observed),
                    "evidence": "squarefree_modular_factorization",
                    "prime": prime,
                }
            )
            remaining = reduced
        if len(remaining) <= 1:
            break

    exact_t = next(iter(remaining)) if len(remaining) == 1 else None
    return {
        **row,
        "checkedSquarefreePrimes": checked,
        "elimination": elimination,
        "exactLabel": None if exact_t is None else f"24T{exact_t}",
        "fullyCertifiedTarget": exact_t == TARGET_T,
        "remainingLabels": [f"24T{value}" for value in sorted(remaining)],
        "status": (
            "certified_24T22560"
            if exact_t == TARGET_T
            else "certified_other_label"
            if exact_t is not None
            else "incompatible"
            if not remaining
            else "ambiguous_fail_closed"
        ),
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    helper = load_helper()
    catalog = helper.compatible_transitive_catalog(
        QUOTIENT_T,
        QUOTIENT_ORDER,
        0,
    )
    eligible_labels = {
        int(label)
        for bucket in catalog["buckets"]
        if int(bucket["kummerRank"]) <= MAX_KERNEL_RANK_FROM_SQUARE_NORM
        for label in bucket["compatibleLabels"]
    }
    excluded_rank12 = sorted(
        {
            int(label)
            for bucket in catalog["buckets"]
            if int(bucket["kummerRank"]) == 12
            for label in bucket["compatibleLabels"]
        }
    )
    if excluded_rank12 != [23278]:
        raise ArithmeticError(
            f"unexpected rank-12 exact-q214 catalog: {excluded_rank12}"
        )
    if TARGET_T not in eligible_labels:
        raise ArithmeticError("target absent from exact q214 catalog")

    profiles = {
        target_t: helper.group_cycle_profiles(target_t)
        for target_t in sorted(eligible_labels)
    }
    inputs = []
    rows = []
    for path in INPUTS:
        payload = read_json(path)
        input_rows = list(candidate_rows(path, payload))
        inputs.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
                "uniqueCandidateCount": len(input_rows),
            }
        )
        rows.extend(input_rows)

    classified = [
        classify_candidate(helper, dict(row), profiles, eligible_labels)
        for row in rows
    ]
    certified_target = [row for row in classified if row["fullyCertifiedTarget"]]
    distribution = Counter(
        (
            row["inputArtifact"],
            row["r"],
            tuple(row["remainingLabels"]),
        )
        for row in classified
    )
    payload = {
        "audit": {
            "networkCalls": 0,
            "primeBound": PRIME_BOUND,
            "stageCalls": 0,
            "submissionCalls": 0,
        },
        "catalog": {
            "buckets": catalog["buckets"],
            "eligibleLabels": [
                f"24T{value}" for value in sorted(eligible_labels)
            ],
            "eligibleSize": len(eligible_labels),
            "excludedByEveryCandidateSquareNormRelation": [
                f"24T{value}" for value in excluded_rank12
            ],
            "method": (
                "exhaustive GAP degree-24 transitive catalog, exact 12x2 "
                "block quotient identification as 12T214"
            ),
            "quotientOrder": QUOTIENT_ORDER,
            "quotientT": QUOTIENT_T,
        },
        "classificationMethod": (
            "archimedean complex conjugation plus squarefree Dedekind "
            "Frobenius cycle-type exclusion against every eligible catalog group"
        ),
        "inputs": inputs,
        "rows": classified,
        "schemaVersion": "q214-failclosed-fullgroup-classification-v1",
        "summary": {
            "certified24T22560Count": len(certified_target),
            "closedWithoutPacket": not certified_target,
            "distribution": [
                {
                    "count": count,
                    "inputArtifact": input_artifact,
                    "r": r,
                    "remainingLabels": list(remaining),
                }
                for (input_artifact, r, remaining), count in sorted(
                    distribution.items()
                )
            ],
            "fullyClassifiedCandidateCount": sum(
                len(row["remainingLabels"]) == 1 for row in classified
            ),
            "uniqueCandidateCount": len(classified),
        },
    }
    if certified_target:
        raise ArithmeticError(
            "unexpected 24T22560 singleton; inspect before staging anything"
        )
    atomic_write_json(OUTPUT, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    print(f"wrote {OUTPUT}")
    print(f"sha256 {sha256_path(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

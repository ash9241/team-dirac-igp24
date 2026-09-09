#!/usr/bin/env sage -python
"""Checkpoint all degree-24 pair-resolvent factors for two exact F6 sources."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT / "data" / "campaign_20260727_f627"
CERTIFICATE = CAMPAIGN / "f6_post22_unique_wave_cumulative_certificate.json"
CENSUS = CAMPAIGN / "f6_recursive_16948_16949_census.jsonl"
OUTPUT = CAMPAIGN / "f6_recursive_16948_16949_candidates.jsonl"
SOURCE_LABELS = ("24T16948", "24T16949")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode())


def canonical(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def atomic_write(path: Path, rows: list[dict]) -> None:
    payload = "".join(canonical(row) + "\n" for row in rows).encode()
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_worker():
    path = ROOT / "pair_sum_one.sage.py"
    spec = importlib.util.spec_from_file_location("f6_recursive_pair_worker", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot import pair_sum_one.sage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    prior = []
    if OUTPUT.exists():
        prior = [
            json.loads(line)
            for line in OUTPUT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    completed = {str(row["sourceLabel"]) for row in prior}
    if not completed <= set(SOURCE_LABELS):
        raise ValueError("candidate checkpoint contains an unexpected source")

    certificate_bytes = CERTIFICATE.read_bytes()
    certificate = json.loads(certificate_bytes)
    hits = {
        str(hit["targetGate"]["label"]): hit
        for hit in certificate["hits"]
        if str(hit["targetGate"]["label"]) in SOURCE_LABELS
    }
    census = {
        str(row["sourceLabel"]): row
        for row in (
            json.loads(line)
            for line in CENSUS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    if set(hits) != set(SOURCE_LABELS) or set(census) != set(SOURCE_LABELS):
        raise ValueError("source certificate or census is incomplete")

    worker = load_worker()
    ring = PolynomialRing(ZZ, "x")
    results = list(prior)
    for source_label in SOURCE_LABELS:
        if source_label in completed:
            continue
        started = time.monotonic()
        hit = hits[source_label]
        census_row = census[source_label]
        source_line = str(hit["candidateCoefficientLine"])
        source_hash = sha_text(source_line)
        checks = hit["candidateChecks"]
        if (
            source_hash != str(checks["coefficientSha256"])
            or int(hit["targetGate"]["r"]) != 16
            or int(census_row["sourceT"]) != int(source_label.removeprefix("24T"))
            or 16 not in {int(value) for value in census_row["sourceR"]}
        ):
            raise ValueError(f"source pin mismatch for {source_label}")
        source = ring([ZZ(value) for value in source_line.split(",")])
        if (
            source.degree() != 24
            or not source.is_monic()
            or not source.is_irreducible()
            or int(source.number_of_real_roots()) != 16
        ):
            raise ValueError(f"source algebraic gates failed for {source_label}")

        pair_degree = 24 * 23 // 2
        powers = worker.root_power_sums(
            [int(value) for value in source.list()], 2 * pair_degree
        )
        selected = None
        selected_transform = None
        attempts = []
        for transform in (1, 2, 3):
            attempt_started = time.monotonic()
            transformed = worker.transformed_power_sums(
                powers, pair_degree, transform
            )
            resolvent = worker.pair_sum_resolvent(
                ring, transformed, pair_degree
            )
            resolvent_line = ",".join(
                str(int(value)) for value in resolvent.list()
            )
            factors, factor_certificate = worker.factor_with_certificate(
                resolvent,
                [int(value) for value in census_row["orbitSizes"]],
                int(census_row["length24OrbitCount"]),
            )
            attempts.append(
                {
                    "factorCertificate": factor_certificate,
                    "resolventSha256": sha_text(resolvent_line),
                    "seconds": round(time.monotonic() - attempt_started, 3),
                    "transform": transform,
                }
            )
            if factors is not None:
                selected = factors
                selected_transform = transform
                break
        if selected is None or len(selected) != 3:
            raise ValueError(f"no exact separating transform for {source_label}")

        candidates = []
        for factor_index, factor in enumerate(selected):
            reduced = worker.reduce_polynomial(factor, "best")
            if (
                reduced.degree() != 24
                or not reduced.is_monic()
                or not reduced.is_irreducible()
            ):
                raise ValueError("reduced factor failed exact algebraic gates")
            line = worker.coefficient_line(reduced)
            candidates.append(
                {
                    "coefficientBytes": len(line.encode()),
                    "coefficientLine": line,
                    "coefficientSha256": sha_text(line),
                    "factorIndex": factor_index,
                    "polynomialDiscriminantAbs": str(abs(reduced.discriminant())),
                    "r": int(reduced.number_of_real_roots()),
                }
            )
        row = {
            "schemaVersion": "f6-recursive-multi-candidates-v1",
            "attempts": attempts,
            "candidates": candidates,
            "censusExactCertificateSha256": str(
                census_row["exactCertificateSha256"]
            ),
            "censusSha256": sha_bytes(CENSUS.read_bytes()),
            "factorCount": len(candidates),
            "orbitSizes": [int(value) for value in census_row["orbitSizes"]],
            "orbitTargets": census_row["targets"],
            "sourceCertificateSha256": sha_bytes(certificate_bytes),
            "sourceCoefficientLine": source_line,
            "sourceCoefficientSha256": source_hash,
            "sourceLabel": source_label,
            "sourceR": 16,
            "status": "certified_multi",
            "transform": {"kind": "x+c*x^2", "c": selected_transform},
            "wallSeconds": round(time.monotonic() - started, 3),
        }
        results.append(row)
        atomic_write(OUTPUT, results)
        print(
            canonical(
                {
                    "event": "checkpoint",
                    "sourceLabel": source_label,
                    "candidateHashes": [
                        candidate["coefficientSha256"]
                        for candidate in candidates
                    ],
                    "wallSeconds": row["wallSeconds"],
                }
            ),
            flush=True,
        )
    print(
        canonical(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "outputSha256": sha_bytes(OUTPUT.read_bytes()),
                "sources": len(results),
                "status": "complete",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

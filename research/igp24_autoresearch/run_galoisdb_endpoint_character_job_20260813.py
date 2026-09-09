#!/usr/bin/env python3
"""Certify one derivative-sign endpoint character job."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from sage.all import PolynomialRing, QQ

from routeA.build_t00035_campaign import _squarefree_part
from routeA.constructions.integral_basis_character_lift import (
    build_integral_basis_lift,
    certify_integral_basis_lift,
    enumerate_integral_basis_generators,
    family_from_integral_basis_seed,
)


def load_job(path: Path, index: int) -> dict:
    for current, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if current == index:
            return json.loads(line)
    raise IndexError(index)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--job-index", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--options-per-signature", type=int, default=16)
    parser.add_argument("--prime-limit", type=int, default=30000)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    started = time.time()
    job = load_job(args.plan, args.job_index)
    coefficients = tuple(int(value) for value in job["baseCoefficients"])
    ring = PolynomialRing(QQ, "x")
    polynomial = ring(coefficients)
    derivative = tuple(int(polynomial.derivative()[i]) for i in range(12))
    norm_class = _squarefree_part(abs(int(polynomial.discriminant())))
    family = family_from_integral_basis_seed(
        family=f"galoisdb_endpoint_sign_{job['catalogLabel'].replace(':', '_')}",
        base_t=int(job["baseT"]),
        target_t=int(job["targetT"]),
        expected_norm_squareclass=norm_class,
        base_coefficients=coefficients,
        seed_coefficients=derivative,
        target_root_counts=job["requestedRoots"],
        construction_overgroup=f"C2^11_sign_character_kernel_over_12T{int(job['baseT'])}",
        character_name="permutation_sign",
        base_root_count=12,
    )
    result = {"job": job, "status": "started", "candidates": [], "attempts": [], "errors": []}
    try:
        options = enumerate_integral_basis_generators(
            family,
            timeout=args.timeout,
            options_per_signature=args.options_per_signature,
            allow_missing_signatures=True,
        )
        for root in job["requestedRoots"]:
            for option_index, option in enumerate(options[int(root)]):
                attempt = {
                    "root": int(root),
                    "optionIndex": option_index,
                    "unitMask": int(option.unit_mask),
                    "unitSign": int(option.unit_sign),
                }
                try:
                    spec = build_integral_basis_lift(family, option, timeout=args.timeout, coefficient_limit=10**120)
                    candidate = certify_integral_basis_lift(
                        spec,
                        timeout=args.timeout,
                        prime_limit=args.prime_limit,
                    )
                    payload = candidate.to_json()
                    payload["liveTarget"] = job["targetRows"][str(root)]
                    payload["sourceJobId"] = job["jobId"]
                    payload["serverCalibrationBasis"] = "full_character_group"
                    result["candidates"].append(payload)
                    attempt["status"] = "exact_live_candidate"
                    result["attempts"].append(attempt)
                    break
                except Exception as exc:
                    attempt["status"] = "excluded"
                    attempt["error"] = f"{type(exc).__name__}: {exc}"[:1200]
                    result["attempts"].append(attempt)
        result["status"] = "complete"
    except Exception as exc:
        result["status"] = "job_failed"
        result["errors"].append(f"{type(exc).__name__}: {exc}"[:4000])
    result["elapsedSeconds"] = time.time() - started
    result["pid"] = os.getpid()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"job_{args.job_index:04d}_{job['jobId']}.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({
        "jobIndex": args.job_index,
        "status": result["status"],
        "candidateCount": len(result["candidates"]),
        "elapsedSeconds": result["elapsedSeconds"],
        "output": str(output),
    }, sort_keys=True))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one exact score700 signature-recovery job under Sage/PARI/GAP."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from pathlib import Path

from routeA.constructions.integral_basis_character_lift import (
    build_integral_basis_lift,
    certify_integral_basis_lift,
    enumerate_integral_basis_generators,
    family_from_integral_basis_seed,
)
from routeA.constructions.nested_character_lift import classify_transitive_subgroup


def load_job(path: Path, index: int) -> dict:
    for current, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if current == index:
            return json.loads(line)
    raise IndexError(f"job index {index} is outside {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--job-index", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--options-per-signature", type=int, default=12)
    parser.add_argument("--prime-limit", type=int, default=30000)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    args = parser.parse_args()

    started = time.time()
    job = load_job(args.plan, args.job_index)
    live_targets = json.loads(args.targets.read_text(encoding="utf-8"))
    family = family_from_integral_basis_seed(
        family=f"current_score700_signature_recovery_{job['jobId']}",
        base_t=int(job["baseT"]),
        target_t=int(job["startingTargetT"]),
        expected_norm_squareclass=int(job["normSquareclass"]),
        base_coefficients=job["baseCoefficients"],
        seed_coefficients=job["seedCoefficients"],
        target_root_counts=job["requestedRoots"],
        construction_overgroup=f"known_character_overgroup:24T{int(job['startingTargetT'])}",
        character_name="historically_exact_score700_character",
        base_root_count=int(job["baseRootCount"]),
    )
    result = {
        "job": job,
        "status": "started",
        "candidates": [],
        "attempts": [],
        "errors": [],
    }
    try:
        options = enumerate_integral_basis_generators(
            family,
            timeout=args.timeout,
            options_per_signature=args.options_per_signature,
            allow_missing_signatures=True,
        )
        for root in job["requestedRoots"]:
            seen_terminals = set()
            for option_index, option in enumerate(options[int(root)]):
                attempt = {
                    "root": int(root),
                    "optionIndex": int(option_index),
                    "unitMask": int(option.unit_mask),
                    "unitSign": int(option.unit_sign),
                }
                try:
                    spec = build_integral_basis_lift(
                        family,
                        option,
                        timeout=args.timeout,
                        coefficient_limit=args.coefficient_limit,
                    )
                    terminal_t, _excluded, _witnesses, chain = classify_transitive_subgroup(
                        spec.coefficients,
                        int(job["startingTargetT"]),
                        prime_limit=args.prime_limit,
                        timeout=args.timeout,
                    )
                    attempt["terminalT"] = int(terminal_t)
                    attempt["chain"] = [int(value) for value in chain]
                    if int(terminal_t) in seen_terminals:
                        attempt["status"] = "duplicate_terminal"
                        result["attempts"].append(attempt)
                        continue
                    seen_terminals.add(int(terminal_t))
                    calibrated_terminal = job["terminalCalibration"].get(str(int(terminal_t)))
                    if calibrated_terminal is None:
                        attempt["status"] = "terminal_not_server_calibrated"
                        result["attempts"].append(attempt)
                        continue
                    calibrated_terminal = int(calibrated_terminal)
                    attempt["serverCalibratedTerminalT"] = calibrated_terminal
                    target = live_targets.get(f"{calibrated_terminal}:{int(root)}")
                    if target is None:
                        attempt["status"] = "terminal_not_live_low_holder"
                        result["attempts"].append(attempt)
                        continue
                    chain_text = ">".join(f"24T{int(value)}" for value in chain)
                    terminal_family = replace(
                        family,
                        family=f"{job['sourceFamily']}_fresh_signature_subgroup_{int(terminal_t)}",
                        target_t=int(terminal_t),
                        target_root_counts=(int(root),),
                        construction_overgroup=f"exact_transitive_subgroup_descent:{chain_text}",
                        character_name="historically_exact_character_fresh_signature",
                    )
                    candidate = certify_integral_basis_lift(
                        replace(spec, family=terminal_family),
                        timeout=args.timeout,
                        prime_limit=args.prime_limit,
                    )
                    payload = candidate.to_json()
                    payload["offlineCertifiedTargetT"] = int(terminal_t)
                    payload["target_t"] = calibrated_terminal
                    payload["serverCalibrationBasis"] = "accepted_same_arithmetic_family"
                    payload["liveTarget"] = target
                    payload["sourceArchiveLine"] = int(job["sourceArchiveLine"])
                    payload["sourceJobId"] = str(job["jobId"])
                    result["candidates"].append(payload)
                    attempt["status"] = "exact_live_candidate"
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
        "jobId": job["jobId"],
        "status": result["status"],
        "candidateCount": len(result["candidates"]),
        "elapsedSeconds": result["elapsedSeconds"],
        "output": str(output),
    }, sort_keys=True))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Classify ambiguous degree-24 pair factors by exact joint cycle profiles."""

from __future__ import annotations

import itertools
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.constructions.compositum_8x3 import DEFAULT_GP


def _cycle(value: Any) -> tuple[int, ...]:
    if isinstance(value, str):
        raw = value.replace(",", ".").split(".")
        cycle = tuple(sorted(int(item) for item in raw if item.strip()))
    else:
        cycle = tuple(sorted(int(item) for item in value))
    if not cycle or sum(cycle) != 24:
        raise ValueError(f"invalid degree-24 cycle type: {value!r}")
    return cycle


def involution_cycle(real_roots: int) -> tuple[int, ...]:
    real_roots = int(real_roots)
    if real_roots < 0 or real_roots > 24 or (24 - real_roots) % 2:
        raise ValueError("real-root count must be an even integer in [0, 24]")
    return (1,) * real_roots + (2,) * ((24 - real_roots) // 2)


def real_root_sample(
    source_real_roots: int,
    factor_real_roots: Sequence[int],
) -> dict[str, Any]:
    return {
        "prime": "infinity",
        "source_cycle": involution_cycle(source_real_roots),
        "factor_cycles": [involution_cycle(value) for value in factor_real_roots],
    }


def classify_factor_targets(
    profile: Mapping[str, Any],
    samples: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return all target assignments compatible with exact GAP profiles.

    A permutation maps each concrete factor position to an abstract pair orbit.
    A prime accepts that permutation only if one conjugacy-class profile has
    the observed source cycle and all factor cycles simultaneously.  Surviving
    permutations are collapsed by target label, so exchanging two abstract
    orbits with the same 24T label does not create false ambiguity.
    """

    targets = tuple(int(value) for value in profile["orbit_targets"])
    orbit_count = len(targets)
    if int(profile.get("orbit_count", orbit_count)) != orbit_count:
        raise ValueError("profile orbit_count does not match orbit_targets")
    abstract = []
    for row in profile["profiles"]:
        orbit_cycles = tuple(_cycle(value) for value in row["orbit_cycles"])
        if len(orbit_cycles) != orbit_count:
            raise ValueError("abstract profile has the wrong orbit count")
        abstract.append((_cycle(row["source_cycle"]), orbit_cycles))
    if not abstract:
        raise ValueError("joint profile has no conjugacy-class rows")

    normalized_samples = []
    for sample in samples:
        factor_cycles = tuple(_cycle(value) for value in sample["factor_cycles"])
        if len(factor_cycles) != orbit_count:
            raise ValueError("sample factor count does not match pair-orbit count")
        normalized_samples.append((_cycle(sample["source_cycle"]), factor_cycles))
    if not normalized_samples:
        raise ValueError("at least one modular or real-place sample is required")

    surviving_permutations: list[tuple[int, ...]] = []
    for assignment in itertools.permutations(range(orbit_count)):
        if all(
            any(
                source_cycle == observed_source
                and all(
                    orbit_cycles[assignment[index]] == observed_factors[index]
                    for index in range(orbit_count)
                )
                for source_cycle, orbit_cycles in abstract
            )
            for observed_source, observed_factors in normalized_samples
        ):
            surviving_permutations.append(assignment)

    target_assignments = sorted({
        tuple(targets[orbit] for orbit in assignment)
        for assignment in surviving_permutations
    })
    factor_options = [
        sorted({assignment[index] for assignment in target_assignments})
        for index in range(orbit_count)
    ]
    resolved = bool(target_assignments) and all(len(values) == 1 for values in factor_options)
    return {
        "resolved": resolved,
        "sample_count": len(normalized_samples),
        "surviving_orbit_permutations": len(surviving_permutations),
        "target_assignments": [list(value) for value in target_assignments],
        "factor_target_options": factor_options,
        "factor_targets": [values[0] for values in factor_options] if resolved else None,
        "evidence": "gap-exact-pair-joint-cycle-plus-good-prime-factorization",
    }


def joint_profile_confidence(
    profile: Mapping[str, Any],
    good_prime_count: int,
) -> dict[str, Any]:
    """Return a conservative Chebotarev separation confidence bound.

    The joint GAP table gives the exact mass of every observable tuple.  For
    each possible concrete-to-abstract orbit assignment, this routine measures
    the mass on which a *different target-label assignment* could give the
    same tuple.  For ``n`` independent good-prime observations, a wrong orbit
    permutation with one-prime confusion mass ``q`` survives with probability
    ``q**n``.  A union bound over all wrong permutations and the worst possible
    true ordering gives the reported lower bound.

    This probability is an operational sampling bound, not a substitute for
    exact classification: callers must additionally require
    :func:`classify_factor_targets` to return one unique target assignment.
    """

    sample_count = int(good_prime_count)
    if sample_count < 0:
        raise ValueError("good_prime_count cannot be negative")
    targets = tuple(int(value) for value in profile["orbit_targets"])
    orbit_count = len(targets)
    if orbit_count < 2:
        raise ValueError("joint-profile confidence requires at least two orbits")
    if int(profile.get("orbit_count", orbit_count)) != orbit_count:
        raise ValueError("profile orbit_count does not match orbit_targets")

    abstract: list[tuple[int, tuple[int, ...], tuple[tuple[int, ...], ...]]] = []
    for row in profile["profiles"]:
        size = int(row.get("class_size", 0))
        orbit_cycles = tuple(_cycle(value) for value in row["orbit_cycles"])
        if size < 1:
            raise ValueError("joint-profile class sizes must be positive")
        if len(orbit_cycles) != orbit_count:
            raise ValueError("abstract profile has the wrong orbit count")
        abstract.append((size, _cycle(row["source_cycle"]), orbit_cycles))
    if not abstract:
        raise ValueError("joint profile has no conjugacy-class rows")
    group_order = int(profile.get("group_order", sum(row[0] for row in abstract)))
    if sum(row[0] for row in abstract) != group_order:
        raise ValueError("joint-profile class sizes do not sum to group_order")

    permutations = tuple(itertools.permutations(range(orbit_count)))
    observable_by_assignment = {
        assignment: {
            (
                source_cycle,
                tuple(orbit_cycles[assignment[index]] for index in range(orbit_count)),
            )
            for _, source_cycle, orbit_cycles in abstract
        }
        for assignment in permutations
    }
    confusions: dict[tuple[tuple[int, ...], tuple[int, ...]], Fraction] = {}
    maximum_confusion = Fraction(0, 1)
    comparison_count = 0
    for true_assignment in permutations:
        true_targets = tuple(targets[index] for index in true_assignment)
        for wrong_assignment in permutations:
            wrong_targets = tuple(targets[index] for index in wrong_assignment)
            if wrong_targets == true_targets:
                continue
            confusion_mass = 0
            for class_size, source_cycle, orbit_cycles in abstract:
                observation = tuple(
                    orbit_cycles[true_assignment[index]]
                    for index in range(orbit_count)
                )
                compatible = (
                    source_cycle,
                    observation,
                ) in observable_by_assignment[wrong_assignment]
                if compatible:
                    confusion_mass += class_size
            confusion = Fraction(confusion_mass, group_order)
            confusions[(true_assignment, wrong_assignment)] = confusion
            maximum_confusion = max(maximum_confusion, confusion)
            comparison_count += 1
    if not confusions:
        raise ValueError("joint profile has no distinct target-label assignments")

    # Union-bound the probability that *any* wrong orbit permutation survives
    # every sample, and then take the least favorable concrete factor ordering.
    worst_failure = 0.0
    for true_assignment in permutations:
        failure = sum(
            float(confusion) ** sample_count
            for (candidate_true, _), confusion in confusions.items()
            if candidate_true == true_assignment
        )
        worst_failure = max(worst_failure, failure)
    confidence = max(0.0, min(1.0, 1.0 - worst_failure))
    return {
        "good_prime_count": sample_count,
        "confidence_lower_bound": confidence,
        "maximum_one_prime_confusion": float(maximum_confusion),
        "minimum_one_prime_separation": float(1 - maximum_confusion),
        "target_assignment_comparisons": comparison_count,
        "evidence": "gap-exact-joint-profile-chebotarev-union-bound",
    }


def parse_pari_joint_samples(lines: Iterable[str]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in lines:
        line = raw.strip()
        if not line.startswith("JFP|"):
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            raise ValueError(f"malformed PARI joint-profile row: {line[:200]}")
        prime = int(parts[1])
        if prime in seen:
            raise ValueError(f"duplicate PARI sample at prime {prime}")
        patterns = json.loads(parts[2])
        if not isinstance(patterns, list) or len(patterns) < 2:
            raise ValueError("joint sample must contain source and factor patterns")
        cycles = [_cycle(value) for value in patterns]
        samples.append({
            "prime": prime,
            "source_cycle": cycles[0],
            "factor_cycles": cycles[1:],
        })
        seen.add(prime)
    return samples


def pari_joint_cycle_samples(
    source_coefficients: Sequence[int],
    factor_coefficients: Sequence[Sequence[int]],
    *,
    gp: str | Path = DEFAULT_GP,
    prime_count: int = 256,
    timeout: float = 1_800,
) -> list[dict[str, Any]]:
    """Factor source and concrete factors at the same squarefree primes."""

    polynomials = [tuple(int(value) for value in source_coefficients)] + [
        tuple(int(value) for value in coefficients)
        for coefficients in factor_coefficients
    ]
    if len(polynomials) < 2:
        raise ValueError("at least one degree-24 factor is required")
    if any(len(values) != 25 or values[-1] != 1 for values in polynomials):
        raise ValueError("source and factor polynomials must be monic of degree 24")
    prime_count = int(prime_count)
    if prime_count < 1:
        raise ValueError("prime_count must be positive")
    vectors = ["[" + ",".join(str(value) for value in values) + "]" for values in polynomials]
    # GP evaluates a newline-terminated expression immediately.  Keep each
    # nested ``for`` expression on one physical line; otherwise an innocent
    # formatting newline after ``for(...,`` becomes a syntax error.
    script = "\n".join([
        f'P=[{",".join(f"Polrev({vector})" for vector in vectors)}];',
        f"Q=primes([101,1000000]);Q=Q[1..{prime_count}];",
        "for(j=1,#Q,my(patterns=vector(#P),good=1);"
        "for(i=1,#P,my(fm=factormod(P[i],Q[j],1),pat=[]);"
        "for(k=1,matsize(fm)[1],if(fm[k,2]!=1,good=0);"
        "for(m=1,fm[k,2],pat=concat(pat,[fm[k,1]])));"
        "patterns[i]=vecsort(pat));"
        'if(good,print("JFP|",Q[j],"|",patterns)));',
        "quit;",
    ])
    result = subprocess.run(
        [str(gp), "-q", "-f", "-s", "400000000"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    samples = parse_pari_joint_samples(result.stdout.splitlines())
    if not samples:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "PARI returned no jointly unramified prime samples"
            + (f": {detail[:500]}" if detail else "")
        )
    return samples


def load_profile(path: str | Path, source_t: int) -> dict[str, Any]:
    matches = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and int(json.loads(line)["source_t"]) == int(source_t)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one joint profile for 24T{source_t}, found {len(matches)}")
    return matches[0]

#!/usr/bin/env sage -python
"""Exact single-source k=3 runner for 24T18035/r24 -> 24T9833/r24.

This file is intentionally separate from the frozen historical k=3 pilot.
It validates the exact ledger source and frozen route, assigns all degree-12
triple-product factors to the three exact subset actions, and accepts residual
action ambiguity only when the factor-to-target-label map is unique.  It never
makes network or submission calls.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, ZZ, libgap, prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes.jsonl"
K2_ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
FROZEN = DATA / "agent_f7_frozen_23018_live_pairs.jsonl"

EXPECTED_SHA256 = {
    ACTIONS: "d191f5d5ed251f2e7af6ede4b930da24a75ca6593b62f7f1e6e01f061abc4d00",
    ROUTES: "487e9d8e8dd83aae279af87740d1b6d0836d7dcff380c524791474571b54c5b2",
    K2_ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN: "e15386893d951acf921d2cce598accf7500e6f306b6ab7dbb73d0e3e5fc7eab2",
}

SOURCE = {
    "coefficientSha256": "ee9322e952f3e059003e74c1c8f7a9a026fd6af28702f36bb8937aa1168e480c",
    "label": "24T18035",
    "polynomialIndex": 212,
    "quotientPolynomialSha256": "1598ae2da50a0ac67f2783471c3c6f633c49154d6519fcdcba462003d1827460",
    "r": 24,
    "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
}
TARGET = ("24T9833", 24)
EXPECTED_ACTION_TARGET_MULTIPLICITIES = {"24T18035": 1, "24T9833": 2}
DEFAULT_PRIME_BOUND = 2000


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def polynomial_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def canonical_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def write_json(path: Path, payload: dict) -> str:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return sha256_bytes(rendered.encode())


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return sha256_bytes(rendered.encode())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assignment-prime-bound",
        choices=(400, 2000, 5000),
        default=DEFAULT_PRIME_BOUND,
        type=int,
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate all frozen inputs, provenance, target state, and paths without a resolvent.",
    )
    return parser.parse_args()


def output_paths(prime_bound: int) -> tuple[Path, Path, Path]:
    stem = "agent_f9_k3_18035_r24"
    if prime_bound != DEFAULT_PRIME_BOUND:
        stem += f"_pb{prime_bound}"
    return (
        DATA / f"{stem}_results.jsonl",
        DATA / f"{stem}_summary.json",
        OUTBOX / f"{stem}_live.txt",
    )


def validate_output_paths(paths: tuple[Path, Path, Path]) -> None:
    results_path, summary_path, manifest_path = paths
    resolved = tuple(path.resolve() for path in paths)
    if len(set(resolved)) != 3:
        raise ValueError("result, summary, and manifest output paths collide")
    if results_path.parent.resolve() != DATA.resolve() or summary_path.parent.resolve() != DATA.resolve():
        raise ValueError("data output escaped the data directory")
    if manifest_path.parent.resolve() != OUTBOX.resolve():
        raise ValueError("manifest escaped the outbox directory")
    collision_candidates = list(resolved) + [
        Path(str(path) + ".tmp").resolve() for path in resolved
    ]
    existing = [
        str(path.relative_to(ROOT)) for path in collision_candidates if path.exists()
    ]
    if existing:
        raise FileExistsError("refusing to overwrite k3 outputs: " + ", ".join(existing))


def outbox_hashes(active_manifest: Path) -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        if path == active_manifest:
            continue
        for line in path.read_text(errors="ignore").splitlines():
            try:
                values = [ZZ(value.strip()) for value in line.split(",")]
            except Exception:
                continue
            if len(values) == 25 and values[-1] == 1:
                normalized = ",".join(str(value) for value in values)
                hashes.add(sha256_bytes(normalized.encode()))
    return hashes


def target_state(connection: sqlite3.Connection, label: str, r: int) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        raise ValueError(f"target row missing for {label}/r{r}")
    return {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }


def require_live_gold(state: dict, pair: tuple[str, int]) -> None:
    if (
        state["teamCount"] != 0
        or state["discovered"]
        or state["minimumDiscAbs"] is not None
        or state["baseline"]
        or state["owned"]
    ):
        raise ValueError(f"target is no longer locally live gold: {pair}")


def block_action_data(blocks: list[tuple[int, int]], permutation) -> list[int]:
    point_to_block = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
    return [
        point_to_block[int(libgap.OnPoints(block[0], permutation))]
        for block in blocks
    ]


def permutation_images(permutation, degree: int) -> list[int]:
    return [int(libgap.OnPoints(index + 1, permutation)) - 1 for index in range(degree)]


def cycle_type(images: list[int]) -> tuple[int, ...]:
    seen = set()
    lengths = []
    for start in range(len(images)):
        if start in seen:
            continue
        current = start
        length = 0
        while current not in seen:
            seen.add(current)
            length += 1
            current = images[current]
        lengths.append(length)
    return tuple(sorted(lengths))


def orbit_images(orbit: list[tuple[int, ...]], block_images: list[int]) -> list[int]:
    position = {subset: index for index, subset in enumerate(orbit)}
    return [
        position[tuple(sorted(block_images[index] for index in subset))]
        for subset in orbit
    ]


def action_class_profiles(action_rows: list[dict]) -> tuple[list[dict], dict]:
    first = action_rows[0]
    subset_size = int(first["subsetSize"])
    source_t = int(first["sourceT"])
    quotient_t = int(first["quotientT12"])
    blocks = [
        tuple(sorted(int(value) for value in block)) for block in first["sourceBlockSystem"]
    ]
    if any(
        row["sourceBlockSystem"] != first["sourceBlockSystem"]
        or int(row["subsetSize"]) != subset_size
        for row in action_rows
    ):
        raise ValueError("subset actions disagree on size or exact source block system")
    source_group = libgap.TransitiveGroup(24, source_t)
    block_generators = [
        block_action_data(blocks, generator)
        for generator in libgap.GeneratorsOfGroup(source_group)
    ]
    quotient_group = libgap.Group(
        [libgap.PermList([value + 1 for value in images]) for images in block_generators]
    )
    observed_t = int(libgap.TransitiveIdentification(quotient_group))
    if observed_t != quotient_t:
        raise ValueError(f"quotient identification changed: 12T{observed_t} != 12T{quotient_t}")
    orbits = [
        [tuple(int(value) for value in subset) for subset in row["subsetOrbit"]]
        for row in action_rows
    ]
    for orbit in orbits:
        if len(orbit) != 12 or any(len(subset) != subset_size for subset in orbit):
            raise ValueError("invalid size-12 orbit of sealed subsets")
        for generator in block_generators:
            orbit_images(orbit, generator)
    profiles = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(quotient_group), start=1):
        representative = libgap.Representative(conjugacy_class)
        images = permutation_images(representative, 12)
        profiles.append(
            {
                "actionCycleTypes": [cycle_type(orbit_images(orbit, images)) for orbit in orbits],
                "classIndex": class_index,
                "classSize": int(libgap.Size(conjugacy_class)),
                "sourceCycleType": cycle_type(images),
            }
        )
    return profiles, {
        "conjugacyClasses": len(profiles),
        "quotientOrder": int(libgap.Size(quotient_group)),
        "quotientT12": observed_t,
    }


def modular_factor_degrees(polynomial, prime: int) -> tuple[int, ...] | None:
    finite_ring = PolynomialRing(GF(prime), "z")
    reduced = finite_ring(polynomial)
    if reduced.degree() != polynomial.degree() or reduced.gcd(reduced.derivative()).degree() != 0:
        return None
    degrees = []
    for factor, exponent in reduced.factor():
        degrees.extend([int(factor.degree())] * int(exponent))
    return tuple(sorted(degrees))


def target_label_maps(remaining: set[tuple[int, ...]], actions: list[dict]) -> set[tuple[str, ...]]:
    return {
        tuple(str(actions[action_index]["targetLabel"]) for action_index in assignment)
        for assignment in remaining
    }


def assign_factors(
    quotient, factors: list, actions: list[dict], prime_bound: int
) -> tuple[dict[int, int], dict]:
    profiles, group_metadata = action_class_profiles(actions)
    remaining = set(itertools.permutations(range(len(actions))))

    candidate_signatures = [
        int(factor(factor.parent().gen() ** 2).number_of_real_roots()) for factor in factors
    ]
    remaining = {
        assignment
        for assignment in remaining
        if all(
            candidate_signatures[factor_index]
            in {
                int(value)
                for value in actions[action_index][
                    "sourceSignatureToPossibleTargetSignatures"
                ][str(SOURCE["r"])]
            }
            for factor_index, action_index in enumerate(assignment)
        )
    }
    if not remaining:
        raise ValueError("candidate signatures eliminated every subset assignment")
    signature_assignment_count = len(remaining)

    quotient_real_roots = int(quotient.number_of_real_roots())
    source_archimedean = tuple(
        sorted([1] * quotient_real_roots + [2] * ((12 - quotient_real_roots) // 2))
    )
    factor_real_roots = [int(factor.number_of_real_roots()) for factor in factors]
    factor_archimedean = [
        tuple(sorted([1] * count + [2] * ((12 - count) // 2)))
        for count in factor_real_roots
    ]
    matching_archimedean = [
        row for row in profiles if row["sourceCycleType"] == source_archimedean
    ]
    before_archimedean = len(remaining)
    remaining = {
        assignment
        for assignment in remaining
        if any(
            all(
                factor_archimedean[factor_index]
                == row["actionCycleTypes"][assignment[factor_index]]
                for factor_index in range(len(factors))
            )
            for row in matching_archimedean
        )
    }
    if not remaining:
        raise ValueError("archimedean profiles eliminated every subset assignment")
    after_archimedean = len(remaining)

    prime_rows = []
    if len(target_label_maps(remaining, actions)) != 1:
        for prime in prime_range(5, prime_bound):
            source_profile = modular_factor_degrees(quotient, int(prime))
            factor_profiles = [modular_factor_degrees(factor, int(prime)) for factor in factors]
            if source_profile is None or any(profile is None for profile in factor_profiles):
                continue
            matching_classes = [
                row for row in profiles if row["sourceCycleType"] == source_profile
            ]
            compatible = {
                assignment
                for assignment in remaining
                if any(
                    all(
                        factor_profiles[factor_index]
                        == row["actionCycleTypes"][assignment[factor_index]]
                        for factor_index in range(len(factors))
                    )
                    for row in matching_classes
                )
            }
            before = len(remaining)
            remaining.intersection_update(compatible)
            prime_rows.append(
                {
                    "compatibleAssignmentsAfter": len(remaining),
                    "compatibleAssignmentsBefore": before,
                    "factorCycleTypes": [list(profile) for profile in factor_profiles],
                    "matchingConjugacyClasses": [row["classIndex"] for row in matching_classes],
                    "prime": int(prime),
                    "sourceCycleType": list(source_profile),
                }
            )
            if not remaining:
                raise ValueError("modular profiles eliminated every subset assignment")
            if len(target_label_maps(remaining, actions)) == 1:
                break

    label_maps = target_label_maps(remaining, actions)
    certificate = {
        "actionAssignmentCount": len(remaining),
        "archimedean": {
            "compatibleAssignmentsAfter": after_archimedean,
            "compatibleAssignmentsBefore": before_archimedean,
            "factorCycleTypes": [list(value) for value in factor_archimedean],
            "factorRealRoots": factor_real_roots,
            "matchingConjugacyClasses": [row["classIndex"] for row in matching_archimedean],
            "sourceCycleType": list(source_archimedean),
            "sourceRealRoots": quotient_real_roots,
        },
        "candidateSignatures": candidate_signatures,
        "factorActionIndexOptions": {
            factor_index: sorted({assignment[factor_index] for assignment in remaining})
            for factor_index in range(len(factors))
        },
        "group": group_metadata,
        "primeBound": prime_bound,
        "primes": prime_rows,
        "proof": (
            "Exact candidate signatures, complex-conjugation cycles, and finite-prime "
            "Frobenius factor degrees are intersected across all factor/action bijections. "
            "Residual action ambiguity is accepted only when every surviving bijection "
            "gives the same factor-to-target-label map."
        ),
        "signatureCompatibleAssignments": signature_assignment_count,
        "targetLabelAssignmentCount": len(label_maps),
    }
    if len(label_maps) != 1:
        raise RuntimeError(
            f"subset profiles leave {len(remaining)} action assignments and "
            f"{len(label_maps)} target-label assignments below prime {prime_bound}"
        )
    assignment = min(remaining)
    return {index: assignment[index] for index in range(len(factors))}, certificate


def main() -> int:
    args = parse_args()
    results_path, summary_path, manifest_path = output_paths(args.assignment_prime_bound)
    validate_output_paths((results_path, summary_path, manifest_path))

    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch for {path}")

    actions = [
        row
        for row in load_jsonl(ACTIONS)
        if row["sourceLabel"] == SOURCE["label"] and int(row["subsetSize"]) == 3
    ]
    actions = sorted(
        actions, key=lambda row: (str(row["targetLabel"]), json.dumps(row["subsetOrbit"]))
    )
    multiplicities = dict(sorted(Counter(row["targetLabel"] for row in actions).items()))
    if multiplicities != EXPECTED_ACTION_TARGET_MULTIPLICITIES:
        raise ValueError("24T18035 k=3 action multiplicities changed")

    routes = [
        row
        for row in load_jsonl(ROUTES)
        if row["source"]["label"] == SOURCE["label"]
        and int(row["source"]["r"]) == SOURCE["r"]
        and int(row["source"]["polynomialIndex"]) == SOURCE["polynomialIndex"]
        and int(row["action"]["subsetSize"]) == 3
    ]
    expected_source_identity = (
        SOURCE["coefficientSha256"],
        SOURCE["label"],
        SOURCE["polynomialIndex"],
        SOURCE["r"],
        SOURCE["submissionId"],
    )
    observed_sources = {
        (
            str(row["source"]["coefficientSha256"]),
            str(row["source"]["label"]),
            int(row["source"]["polynomialIndex"]),
            int(row["source"]["r"]),
            str(row["source"]["submissionId"]),
        )
        for row in routes
    }
    observed_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"])) for row in routes
    }
    action_keys = {json.dumps(action, sort_keys=True) for action in actions}
    route_action_keys = {json.dumps(row["action"], sort_keys=True) for row in routes}
    expected_target_action_keys = {
        json.dumps(action, sort_keys=True)
        for action in actions
        if action["targetLabel"] == TARGET[0]
    }
    if (
        len(routes) != 2
        or observed_sources != {expected_source_identity}
        or observed_pairs != {TARGET}
        or route_action_keys != expected_target_action_keys
        or not route_action_keys.issubset(action_keys)
    ):
        raise ValueError("frozen deterministic k=3 route identity changed")
    quotient_lines = {str(row["sourceQuotientLine"]) for row in routes}
    quotient_hashes = {str(row["sourceQuotientPolynomialSha256"]) for row in routes}
    if (
        len(quotient_lines) != 1
        or quotient_hashes != {SOURCE["quotientPolynomialSha256"]}
    ):
        raise ValueError("frozen k=3 source quotient changed")
    quotient_line = next(iter(quotient_lines))
    if sha256_bytes(quotient_line.encode()) != SOURCE["quotientPolynomialSha256"]:
        raise ValueError("k=3 source quotient hash mismatch")

    frozen = {
        (str(row["label"]), int(row["r"])): row for row in load_jsonl(FROZEN)
    }
    if TARGET not in frozen:
        raise ValueError("target left the frozen live frontier")
    k2_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in load_jsonl(K2_ROUTES)
    }
    if TARGET in k2_pairs:
        raise ValueError("deterministic k=3 target is no longer novel to k=2")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source_row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
        FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (SOURCE["submissionId"], SOURCE["polynomialIndex"]),
    ).fetchone()
    if source_row is None:
        raise ValueError("exact k=3 source is absent from ledger")
    source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
    quotient_coefficients = [ZZ(value) for value in quotient_line.split(",")]
    if (
        source_row["coefficient_hash"] != SOURCE["coefficientSha256"]
        or source_row["label"] != SOURCE["label"]
        or int(source_row["r"]) != SOURCE["r"]
        or source_row["status"] != "accepted"
        or int(source_row["scoreable"]) != 1
        or len(source_coefficients) != 25
        or source_coefficients[-1] != 1
        or any(source_coefficients[index] for index in range(1, 25, 2))
        or len(quotient_coefficients) != 13
        or quotient_coefficients[-1] != 1
        or source_coefficients[::2] != quotient_coefficients
    ):
        raise ValueError("exact k=3 ledger provenance or quotient identity mismatch")
    initial_target_state = target_state(connection, *TARGET)
    require_live_gold(initial_target_state, TARGET)

    preflight = {
        "assignmentPrimeBound": args.assignment_prime_bound,
        "event": "k3_18035_preflight_ok",
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "outputs": {
            "manifest": str(manifest_path.relative_to(ROOT)),
            "results": str(results_path.relative_to(ROOT)),
            "summary": str(summary_path.relative_to(ROOT)),
        },
        "source": {
            "coefficientSha256": SOURCE["coefficientSha256"],
            "label": SOURCE["label"],
            "polynomialIndex": SOURCE["polynomialIndex"],
            "r": SOURCE["r"],
            "submissionId": SOURCE["submissionId"],
        },
        "submissionCalls": 0,
        "target": {"label": TARGET[0], "r": TARGET[1], "state": initial_target_state},
    }
    if args.preflight_only:
        connection.close()
        print(json.dumps(preflight, sort_keys=True))
        return 0

    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    known_hashes.update(outbox_hashes(manifest_path))
    result_rows = []
    candidates = []
    obstruction = None
    try:
        ring_y = PolynomialRing(ZZ, "y")
        quotient = ring_y(quotient_coefficients)
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("source quotient is not monic irreducible degree 12")
        started = time.monotonic()
        resolvent = quotient.symmetric_power(3, monic=True)
        construction_seconds = time.monotonic() - started
        started = time.monotonic()
        factorization = [(factor, int(exponent)) for factor, exponent in resolvent.factor()]
        factorization_seconds = time.monotonic() - started
        degree_twelve = sorted(
            [factor for factor, exponent in factorization if factor.degree() == 12 and exponent == 1],
            key=lambda factor: sha256_bytes(polynomial_line(factor).encode()),
        )
        if len(degree_twelve) != len(actions):
            raise RuntimeError(
                f"found {len(degree_twelve)} degree-12 factors for {len(actions)} exact actions"
            )
        assignment, assignment_certificate = assign_factors(
            quotient, degree_twelve, actions, args.assignment_prime_bound
        )

        for factor_index, action_index in assignment.items():
            action = actions[action_index]
            if str(action["targetLabel"]) != TARGET[0]:
                continue
            ring_x = PolynomialRing(ZZ, f"x{factor_index}")
            x = ring_x.gen()
            factor = ring_x(degree_twelve[factor_index])
            candidate = factor(x**2)
            if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
                raise ValueError("assigned k=3 candidate is not irreducible monic degree 24")
            signature = int(candidate.number_of_real_roots())
            possible = {
                int(value)
                for value in action["sourceSignatureToPossibleTargetSignatures"][str(SOURCE["r"])]
            }
            if signature not in possible or (str(action["targetLabel"]), signature) != TARGET:
                raise ValueError("assigned k=3 candidate misses the exact deterministic target")
            coefficient_line = canonical_line(candidate)
            coefficient_sha = sha256_bytes(coefficient_line.encode())
            field_disc = abs(ZZ(NumberField(candidate, f"a{factor_index}").absolute_discriminant()))
            state = target_state(connection, *TARGET)
            fresh = coefficient_sha not in known_hashes
            live_hit = (
                state["teamCount"] == 0
                and not state["discovered"]
                and state["minimumDiscAbs"] is None
                and not state["baseline"]
                and not state["owned"]
                and fresh
            )
            candidate_row = {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha,
                "fieldDiscriminantAbs": str(field_disc),
                "freshHash": fresh,
                "liveHit": live_hit,
                "localTargetState": state,
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "r": signature,
                "target": {"label": TARGET[0], "r": TARGET[1]},
            }
            candidates.append(candidate_row)
            known_hashes.add(coefficient_sha)
        if len(candidates) != 2:
            raise RuntimeError(f"exact target-label assignment produced {len(candidates)} candidates, expected 2")
        result_rows.append(
            {
                "assignmentCertificate": assignment_certificate,
                "candidates": candidates,
                "factorDegrees": [
                    {"degree": int(factor.degree()), "exponent": exponent}
                    for factor, exponent in factorization
                ],
                "networkCalls": 0,
                "resolvent": {
                    "constructionSeconds": construction_seconds,
                    "degree": int(resolvent.degree()),
                    "factorizationSeconds": factorization_seconds,
                    "sha256": sha256_bytes(polynomial_line(resolvent).encode()),
                },
                "source": preflight["source"],
                "status": "exact_live_hit" if any(row["liveHit"] for row in candidates) else "exact_not_live",
                "submissionCalls": 0,
                "target": {"label": TARGET[0], "r": TARGET[1]},
            }
        )
    except (RuntimeError, ArithmeticError) as exc:
        obstruction = {
            "error": f"{type(exc).__name__}: {exc}",
            "reason": "degree-220 factorization or exact target-label assignment did not resolve",
        }
        print(json.dumps({"event": "k3_18035_obstruction", **obstruction}, sort_keys=True), flush=True)
    connection.close()

    live_candidates = [row for row in candidates if row["liveHit"]]
    staged = []
    if live_candidates:
        staged = [
            min(live_candidates, key=lambda row: ZZ(row["fieldDiscriminantAbs"]))
        ]
    validate_output_paths((results_path, summary_path, manifest_path))
    manifest_text = "".join(f"{row['coefficientLine']}\n" for row in staged)
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(manifest_text, encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    results_sha = write_jsonl(results_path, result_rows)
    summary = {
        "artifactSha256": {
            "manifest": sha256_path(manifest_path),
            "results": results_sha,
        },
        "assignmentPrimeBound": args.assignment_prime_bound,
        "exactCandidates": len(candidates),
        "exactLiveHits": len(live_candidates),
        "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS",
        "inputSha256": input_hashes,
        "mechanism": "k3-joint-archimedean-and-modular-factor-action-assignment",
        "networkCalls": 0,
        "obstruction": obstruction,
        "source": preflight["source"],
        "stagedPairs": [f"{TARGET[0]}/r{TARGET[1]}"] if staged else [],
        "stagedPolynomials": len(staged),
        "status": (
            "exact_hit_staged"
            if staged
            else "blocked_factorization_or_assignment"
            if obstruction
            else "blocked_not_live_or_duplicate"
        ),
        "submissionCalls": 0,
        "target": {"label": TARGET[0], "r": TARGET[1]},
    }
    summary_sha = write_json(summary_path, summary)
    print(
        json.dumps(
            {"summary": str(summary_path), "summarySha256": summary_sha, **summary},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

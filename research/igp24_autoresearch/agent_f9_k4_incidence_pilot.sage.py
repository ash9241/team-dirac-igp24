#!/usr/bin/env sage -python
"""Capped offline k=4 Kummer subset-product incidence pilot.

With no arguments this preserves the original frozen three-source pilot.  An
explicitly allowlisted ``--source`` mode isolates one priority source and fails
closed on its provenance, target pairs, prime bound, and output paths.

For each selected even source q(x^2), factor the degree-495 fourth symmetric
power of q.  Its degree-12 factors correspond to the exact size-12 orbits of
four-subsets in the quotient action.  When there is more than one such orbit,
assign factors to actions by intersecting exact modular Frobenius
factorization profiles.  No network or submission calls are made.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sqlite3
import time
from collections import Counter, defaultdict
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
PLAN = DATA / "agent_f9_k4_incidence_pilot_plan.json"
RESULTS = DATA / "agent_f9_k4_incidence_pilot_results.jsonl"
SUMMARY = DATA / "agent_f9_k4_incidence_pilot_summary.json"
MANIFEST = OUTBOX / "agent_f9_k4_incidence_live.txt"

PRIORITY_SOURCE_PROFILES = {
    "24T10428/r12": {
        "coefficientSha256": "52b7bd77695a5a248926dbbf2eb3628a1ac5bbacaf7e9894bf69b296cd81f5ec",
        "eligibleActionMultiplicities": {"24T3514": 4},
        "eligiblePairs": (("24T3514", 16),),
        "eligibleRouteCount": 4,
        "outputStem": "agent_f9_k4_10428_r12",
        "polynomialIndex": 953,
        "quotientPolynomialSha256": "4a0b158b7c2d01559eccad4c6b832ca58d366d0aab2fb00091f6dbe8774434bc",
        "sourceLabel": "24T10428",
        "sourceR": 12,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
    "24T18462/r0": {
        "coefficientSha256": "9e4689b329af78d893e7d78f31ade6141874ddaf67825f56924aaf692985e9bc",
        "eligiblePairs": (("24T6120", 24),),
        "outputStem": "agent_f9_k4_18462_r0",
        "polynomialIndex": 2,
        "quotientPolynomialSha256": "a51e4e2fc9eff1fd1521fc107e06799134cee4ed6a4c752fb2f45fd9d082009c",
        "sourceLabel": "24T18462",
        "sourceR": 0,
        "submissionId": "sub_a348e46055d34f83b598643c7f83f087",
        "syntheticRoute": True,
    },
    "24T18462/r4": {
        "coefficientSha256": "9feb41587c0e6993fcd150a63ff3fa2e21662db36776384eddee8cc873b97851",
        "eligiblePairs": (("24T6120", 16),),
        "outputStem": "agent_f9_k4_18462_r4",
        "polynomialIndex": 116,
        "quotientPolynomialSha256": "1c906fbd771fade0f33d0c9f5e554a9a6d4d2f93611a8430250d5ae85325495a",
        "sourceLabel": "24T18462",
        "sourceR": 4,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
    "24T18462/r20": {
        "coefficientSha256": "2ea56946883b19fcb1b723435a40061fd8bede6fec9130129b6fcc62fd2604fa",
        "eligiblePairs": (("24T6120", 16),),
        "outputStem": "agent_f9_k4_18462_r20",
        "polynomialIndex": 364,
        "quotientPolynomialSha256": "db7948b8e72ad1cb9b8051b66e67c5e0ddc99c2730089b36e2560e67bee7067e",
        "sourceLabel": "24T18462",
        "sourceR": 20,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
    "24T19712/r16": {
        "authorizedOverlapPairs": (("24T15335", 20),),
        "coefficientSha256": "722d18b5990ef20fee43a92a05630850d1b39aaf921921d352289d5c4d143f13",
        "eligibleActionMultiplicities": {"24T15335": 2},
        "eligiblePairs": (("24T15335", 20),),
        "eligibleRouteCount": 2,
        "outputStem": "agent_f9_k4_19712_r16",
        "polynomialIndex": 873,
        "quotientPolynomialSha256": "d1a9a5e82b034e76ad641692698e4288d96452c6e46470315c3129c0384d2436",
        "sourceLabel": "24T19712",
        "sourceR": 16,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
    "24T22560/r16": {
        "coefficientSha256": "9caa6b020a3bc048a21039dec00e8b941c8fda9d634411fe663bbe8be386d886",
        "eligiblePairs": (("24T20605", 20), ("24T20611", 20)),
        "outputStem": "agent_f9_k4_22560_r16",
        "polynomialIndex": 161,
        "quotientPolynomialSha256": "9775471537cecc05fe4b4c25e7de5e94599fb6c5cf6cbb2f730535f97a497857",
        "sourceLabel": "24T22560",
        "sourceR": 16,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
    "24T22564/r2": {
        "coefficientSha256": "9790986cd5186f4aa9aeb88300e3de3feb81264d8f6cf748e75e7a4b0f2f41e2",
        "eligiblePairs": (("24T20614", 8),),
        "outputStem": "agent_f9_k4_22564_r2",
        "polynomialIndex": 169,
        "quotientPolynomialSha256": "8aeecd32de7e9cd90603d4269ddb9f61d8448d8ef4bc8211b2c2057f7877376a",
        "sourceLabel": "24T22564",
        "sourceR": 2,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
    "24T22564/r14": {
        "coefficientSha256": "5d00385e8f3cc059742af803e7e1429c297552da33c83aa604e45cf1ee0287b6",
        "eligiblePairs": (("24T20614", 8),),
        "outputStem": "agent_f9_k4_22564_r14",
        "polynomialIndex": 170,
        "quotientPolynomialSha256": "0637ed5eef7ea8700a929acd9339639372e0fa8b9acccafc1fc71f60a4615cda",
        "sourceLabel": "24T22564",
        "sourceR": 14,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
    },
}


EXPECTED_SHA256 = {
    ACTIONS: "d191f5d5ed251f2e7af6ede4b930da24a75ca6593b62f7f1e6e01f061abc4d00",
    ROUTES: "487e9d8e8dd83aae279af87740d1b6d0836d7dcff380c524791474571b54c5b2",
    K2_ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN: "e15386893d951acf921d2cce598accf7500e6f306b6ab7dbb73d0e3e5fc7eab2",
    PLAN: "b6676c3d65ba1596b9d86e476a24b038ef61d581775f4828e45472015c0f3959",
}

EXPECTED_MULTIPLICITIES = {
    "24T10428": {"24T2215": 2, "24T3514": 4},
    "24T18461": {"24T2222": 1, "24T6120": 1, "24T6170": 1},
    "24T18462": {"24T2216": 1, "24T6120": 1, "24T6170": 1},
    "24T19712": {"24T980": 1, "24T15335": 2},
    "24T22560": {"24T20601": 1, "24T20605": 1, "24T20611": 1},
    "24T22562": {"24T20605": 2, "24T20608": 1},
    "24T22564": {"24T20610": 2, "24T20614": 1},
}

EXPECTED_NOVEL_PAIRS = {
    ("24T20605", 20),
    ("24T20605", 24),
    ("24T20611", 20),
    ("24T20611", 24),
    ("24T20614", 8),
    ("24T3514", 16),
    ("24T6120", 16),
    ("24T6120", 24),
}


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


def canonical_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def polynomial_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frozen k=4 pilot or one explicitly allowlisted source."
    )
    parser.add_argument(
        "--source",
        choices=sorted(PRIORITY_SOURCE_PROFILES),
        help=(
            "Run only the allowlisted priority source. Without this option, the "
            "original frozen three-source pilot is preserved."
        ),
    )
    parser.add_argument(
        "--assignment-prime-bound",
        choices=(400, 2000, 5000),
        default=400,
        type=int,
        help=(
            "Exclusive upper bound for exact modular assignment primes. "
            "Non-default bounds are allowed only with --source and get isolated outputs."
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate inputs, provenance, target state, and output isolation without constructing a resolvent.",
    )
    args = parser.parse_args()
    if args.source is None and args.assignment_prime_bound != 400:
        parser.error("a non-default --assignment-prime-bound requires --source")
    return args


def output_paths(source_selector: str | None, prime_bound: int) -> tuple[Path, Path, Path]:
    if source_selector is None:
        return RESULTS, SUMMARY, MANIFEST
    if source_selector not in PRIORITY_SOURCE_PROFILES:
        raise ValueError(f"source selector is not allowlisted: {source_selector}")
    profile = PRIORITY_SOURCE_PROFILES[source_selector]
    stem = str(profile["outputStem"])
    if prime_bound != 400:
        stem += f"_pb{prime_bound}"
    return (
        DATA / f"{stem}_results.jsonl",
        DATA / f"{stem}_summary.json",
        OUTBOX / f"{stem}_live.txt",
    )


def validate_output_paths(
    source_selector: str | None, results_path: Path, summary_path: Path, manifest_path: Path
) -> None:
    paths = (results_path.resolve(), summary_path.resolve(), manifest_path.resolve())
    if len(set(paths)) != 3:
        raise ValueError("result, summary, and manifest output paths collide")
    if results_path.parent.resolve() != DATA.resolve() or summary_path.parent.resolve() != DATA.resolve():
        raise ValueError("data outputs escaped the data directory")
    if manifest_path.parent.resolve() != OUTBOX.resolve():
        raise ValueError("manifest output escaped the outbox directory")
    if source_selector is None:
        return
    legacy_paths = {RESULTS.resolve(), SUMMARY.resolve(), MANIFEST.resolve()}
    if any(path in legacy_paths for path in paths):
        raise ValueError("single-source output collides with a legacy pilot output")
    collision_candidates = list(paths) + [
        Path(str(path) + ".tmp").resolve() for path in paths
    ]
    existing = [
        str(path.relative_to(ROOT)) for path in collision_candidates if path.exists()
    ]
    if existing:
        raise FileExistsError(
            "single-source execution will not overwrite existing outputs: " + ", ".join(existing)
        )


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


def build_synthetic_routes(source_plan: dict, action_rows: list[dict]) -> list[dict]:
    """Build a frozen-shape route for a newly verified source absent from old routes."""
    if not source_plan.get("syntheticRoute"):
        return []
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable,
          v.field_disc_abs
        FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (source_plan["submissionId"], int(source_plan["polynomialIndex"])),
    ).fetchone()
    connection.close()
    if row is None:
        raise ValueError("synthetic-route source is absent from the ledger")
    coefficients = [ZZ(value) for value in row["coefficients"].split(",")]
    if (
        row["coefficient_hash"] != source_plan["coefficientSha256"]
        or row["label"] != source_plan["sourceLabel"]
        or int(row["r"]) != int(source_plan["sourceR"])
        or row["status"] != "accepted"
        or int(row["scoreable"]) != 1
        or len(coefficients) != 25
        or coefficients[-1] != 1
        or any(coefficients[index] for index in range(1, 25, 2))
    ):
        raise ValueError("synthetic-route source provenance mismatch")
    quotient_line = ",".join(str(value) for value in coefficients[::2])
    if sha256_bytes(quotient_line.encode()) != source_plan["quotientPolynomialSha256"]:
        raise ValueError("synthetic-route quotient hash mismatch")

    source = {
        "coefficientSha256": str(source_plan["coefficientSha256"]),
        "fieldDiscAbs": str(row["field_disc_abs"]),
        "label": str(source_plan["sourceLabel"]),
        "polynomialIndex": int(source_plan["polynomialIndex"]),
        "r": int(source_plan["sourceR"]),
        "submissionId": str(source_plan["submissionId"]),
        "t": int(str(source_plan["sourceLabel"]).removeprefix("24T")),
    }
    routes = []
    for label, r in source_plan["eligiblePairs"]:
        matching_actions = [
            action
            for action in action_rows
            if int(action["subsetSize"]) == 4 and str(action["targetLabel"]) == str(label)
        ]
        if len(matching_actions) != 1:
            raise ValueError(f"synthetic target label is not a unique k=4 action: {label}")
        routes.append(
            {
                "action": matching_actions[0],
                "goldTarget": {"label": str(label), "r": int(r)},
                "source": source,
                "sourceQuotientLine": quotient_line,
                "sourceQuotientPolynomialSha256": str(
                    source_plan["quotientPolynomialSha256"]
                ),
            }
        )
    return routes


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
    source_t = int(first["sourceT"])
    quotient_t = int(first["quotientT12"])
    blocks = [tuple(sorted(int(value) for value in block)) for block in first["sourceBlockSystem"]]
    if any(row["sourceBlockSystem"] != first["sourceBlockSystem"] for row in action_rows):
        raise ValueError("subset actions disagree on the exact block system")
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
    orbits = []
    for row in action_rows:
        orbit = row.get("subsetOrbit", row.get("pairOrbit"))
        if orbit is None:
            raise ValueError("action has neither subsetOrbit nor pairOrbit")
        orbits.append(
            [tuple(int(value) for value in subset) for subset in orbit]
        )
    subset_sizes = {
        len(subset) for orbit in orbits for subset in orbit
    }
    if len(subset_sizes) != 1:
        raise ValueError("subset actions do not have one common subset size")
    subset_size = next(iter(subset_sizes))
    for orbit in orbits:
        if len(orbit) != 12 or any(len(subset) != subset_size for subset in orbit):
            raise ValueError("invalid size-12 subset orbit")
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
    metadata = {
        "conjugacyClasses": len(profiles),
        "quotientOrder": int(libgap.Size(quotient_group)),
        "quotientT12": observed_t,
        "subsetSize": subset_size,
    }
    return profiles, metadata


def modular_factor_degrees(polynomial, prime: int) -> tuple[int, ...] | None:
    finite_ring = PolynomialRing(GF(prime), "z")
    reduced = finite_ring(polynomial)
    if reduced.degree() != polynomial.degree() or reduced.gcd(reduced.derivative()).degree() != 0:
        return None
    degrees = []
    for factor, exponent in reduced.factor():
        degrees.extend([int(factor.degree())] * int(exponent))
    return tuple(sorted(degrees))


def assign_degree_twelve_factors(
    quotient,
    factors: list,
    action_rows: list[dict],
    source_r: int,
    prime_bound: int = 400,
) -> tuple[dict[int, int], dict]:
    profiles, group_metadata = action_class_profiles(action_rows)
    remaining = set(itertools.permutations(range(len(action_rows))))
    candidate_signatures = [
        int(factor(factor.parent().gen() ** 2).number_of_real_roots()) for factor in factors
    ]
    signature_compatible = {
        assignment
        for assignment in remaining
        if all(
            candidate_signatures[factor_index]
            in {
                int(value)
                for value in action_rows[action_index][
                    "sourceSignatureToPossibleTargetSignatures"
                ][str(source_r)]
            }
            for factor_index, action_index in enumerate(assignment)
        )
    }
    signature_assignments_before = len(remaining)
    remaining.intersection_update(signature_compatible)
    if not remaining:
        raise ValueError("exact candidate signatures eliminated every factor assignment")

    quotient_real_roots = int(quotient.number_of_real_roots())
    source_archimedean_cycle_type = tuple(
        sorted([1] * quotient_real_roots + [2] * ((12 - quotient_real_roots) // 2))
    )
    factor_real_roots = [int(factor.number_of_real_roots()) for factor in factors]
    factor_archimedean_cycle_types = [
        tuple(sorted([1] * count + [2] * ((12 - count) // 2)))
        for count in factor_real_roots
    ]
    matching_archimedean_classes = [
        row for row in profiles if row["sourceCycleType"] == source_archimedean_cycle_type
    ]
    archimedean_compatible = {
        assignment
        for assignment in remaining
        if any(
            all(
                factor_archimedean_cycle_types[factor_index]
                == row["actionCycleTypes"][assignment[factor_index]]
                for factor_index in range(len(factors))
            )
            for row in matching_archimedean_classes
        )
    }
    archimedean_assignments_before = len(remaining)
    remaining.intersection_update(archimedean_compatible)
    if not remaining:
        raise ValueError("archimedean action profiles eliminated every factor assignment")
    archimedean_assignments_after = len(remaining)

    prime_rows = []
    target_label_assignments = {
        tuple(str(action_rows[action_index]["targetLabel"]) for action_index in assignment)
        for assignment in remaining
    }
    primes = prime_range(5, prime_bound) if len(target_label_assignments) != 1 else []
    for prime in primes:
        source_profile = modular_factor_degrees(quotient, int(prime))
        factor_profiles = [modular_factor_degrees(factor, int(prime)) for factor in factors]
        if source_profile is None or any(profile is None for profile in factor_profiles):
            continue
        matching_classes = [
            row for row in profiles if row["sourceCycleType"] == source_profile
        ]
        compatible = set()
        for permutation in remaining:
            if any(
                all(
                    factor_profiles[factor_index]
                    == row["actionCycleTypes"][permutation[factor_index]]
                    for factor_index in range(len(factors))
                )
                for row in matching_classes
            ):
                compatible.add(permutation)
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
            raise ValueError("modular action profiles eliminated every factor assignment")
        target_label_assignments = {
            tuple(str(action_rows[action_index]["targetLabel"]) for action_index in assignment)
            for assignment in remaining
        }
        if len(target_label_assignments) == 1:
            break
    target_label_assignments = {
        tuple(str(action_rows[action_index]["targetLabel"]) for action_index in assignment)
        for assignment in remaining
    }
    factor_action_index_options = {
        factor_index: sorted({assignment[factor_index] for assignment in remaining})
        for factor_index in range(len(factors))
    }
    certificate = {
        "archimedean": {
            "compatibleAssignmentsAfter": archimedean_assignments_after,
            "compatibleAssignmentsBefore": archimedean_assignments_before,
            "factorCycleTypes": [list(value) for value in factor_archimedean_cycle_types],
            "factorRealRoots": factor_real_roots,
            "matchingConjugacyClasses": [
                row["classIndex"] for row in matching_archimedean_classes
            ],
            "sourceCycleType": list(source_archimedean_cycle_type),
            "sourceRealRoots": quotient_real_roots,
        },
        "assignmentCount": len(remaining),
        "candidateSignatureConstraint": {
            "compatibleAssignmentsAfter": len(signature_compatible),
            "compatibleAssignmentsBefore": signature_assignments_before,
            "factorCandidateSignatures": candidate_signatures,
            "sourceR": int(source_r),
        },
        "factorActionIndexOptions": factor_action_index_options,
        "group": group_metadata,
        "primes": prime_rows,
        "proof": (
            "The degree-12 factors and the exact size-12 subset orbits are in "
            "bijection. Exact real-root counts give the complex-conjugation cycle "
            "types at the archimedean place, and factor(x^2) signatures must lie in "
            "the frozen action signature frontier. For each unramified finite prime, "
            "factor degrees are the exact Frobenius cycle types. Intersecting all "
            "compatible bijections leaves the displayed unique factor-to-target-label "
            "assignment; unresolved action indices, if any, have the same target label."
        ),
        "targetLabelAssignmentCount": len(target_label_assignments),
    }
    if len(target_label_assignments) != 1:
        raise RuntimeError(
            f"joint modular profiles leave {len(remaining)} assignments and "
            f"{len(target_label_assignments)} target-label assignments below prime {prime_bound}"
        )
    assignment = min(remaining)
    return {factor_index: assignment[factor_index] for factor_index in range(len(factors))}, certificate


def main() -> int:
    args = parse_args()
    results_path, summary_path, manifest_path = output_paths(
        args.source, args.assignment_prime_bound
    )
    validate_output_paths(args.source, results_path, summary_path, manifest_path)

    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch for {path}")

    plan = json.loads(PLAN.read_text())
    actions = load_jsonl(ACTIONS)
    routes = load_jsonl(ROUTES)
    k2_routes = load_jsonl(K2_ROUTES)
    frozen_rows = load_jsonl(FROZEN)
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}

    actions_by_label = defaultdict(list)
    for action in actions:
        if int(action["subsetSize"]) == 4 and action["sourceLabel"] in EXPECTED_MULTIPLICITIES:
            actions_by_label[str(action["sourceLabel"])].append(action)
    observed_multiplicities = {
        label: dict(sorted(Counter(row["targetLabel"] for row in rows).items()))
        for label, rows in sorted(actions_by_label.items())
    }
    if observed_multiplicities != EXPECTED_MULTIPLICITIES:
        raise ValueError("frozen size-12 k=4 action multiplicities changed")

    k2_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in k2_routes
    }
    k3_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in routes
        if int(row["action"]["subsetSize"]) == 3
    }
    k4_routes = [row for row in routes if int(row["action"]["subsetSize"]) == 4]
    k4_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in k4_routes
    }
    novel_pairs = k4_pairs.difference(k2_pairs).difference(k3_pairs)
    if novel_pairs != EXPECTED_NOVEL_PAIRS:
        raise ValueError("the frozen eight-pair k=4 frontier changed")

    routes_by_source = defaultdict(list)
    for route in k4_routes:
        key = (str(route["source"]["label"]), int(route["source"]["polynomialIndex"]))
        routes_by_source[key].append(route)

    if args.source is None:
        source_plans = plan["pilotSourcesInOrder"]
        pilot_cap = int(plan["pilotCap"])
    else:
        source_plans = [PRIORITY_SOURCE_PROFILES[args.source]]
        pilot_cap = 1

    selected = []
    for source_plan in source_plans:
        key = (str(source_plan["sourceLabel"]), int(source_plan["polynomialIndex"]))
        source_routes = list(routes_by_source[key])
        if not source_routes:
            source_routes = build_synthetic_routes(
                source_plan, actions_by_label[str(source_plan["sourceLabel"])]
            )
        if not source_routes:
            raise ValueError(f"planned source is absent: {key}")
        first = source_routes[0]
        if first["source"]["coefficientSha256"] != source_plan["coefficientSha256"]:
            raise ValueError("planned source hash changed")
        quotient_lines = {str(row["sourceQuotientLine"]) for row in source_routes}
        quotient_hashes = {str(row["sourceQuotientPolynomialSha256"]) for row in source_routes}
        if quotient_hashes != {source_plan["quotientPolynomialSha256"]} or len(quotient_lines) != 1:
            raise ValueError("planned source quotient changed")
        if args.source is not None:
            expected_source = (
                str(source_plan["coefficientSha256"]),
                str(source_plan["sourceLabel"]),
                int(source_plan["polynomialIndex"]),
                int(source_plan["sourceR"]),
                str(source_plan["submissionId"]),
            )
            observed_sources = {
                (
                    str(row["source"]["coefficientSha256"]),
                    str(row["source"]["label"]),
                    int(row["source"]["polynomialIndex"]),
                    int(row["source"]["r"]),
                    str(row["source"]["submissionId"]),
                )
                for row in source_routes
            }
            if observed_sources != {expected_source}:
                raise ValueError("allowlisted source identity or provenance changed")
            observed_pairs = {
                (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
                for row in source_routes
            }
            observed_action_pairs = {
                (str(row["action"]["targetLabel"]), int(row["goldTarget"]["r"]))
                for row in source_routes
            }
            expected_pairs = set(source_plan["eligiblePairs"])
            authorized_overlap_pairs = set(
                source_plan.get("authorizedOverlapPairs", ())
            )
            if (
                not authorized_overlap_pairs.issubset(expected_pairs)
                or not authorized_overlap_pairs.issubset(k2_pairs | k3_pairs)
                or expected_pairs.difference(novel_pairs).difference(
                    authorized_overlap_pairs
                )
            ):
                raise ValueError("allowlisted nonnovel k=4 pair authorization changed")
            expected_route_count = int(
                source_plan.get("eligibleRouteCount", len(expected_pairs))
            )
            expected_action_multiplicities = dict(
                source_plan.get(
                    "eligibleActionMultiplicities",
                    {label: 1 for label, _signature in expected_pairs},
                )
            )
            observed_route_action_multiplicities = dict(
                sorted(Counter(str(row["action"]["targetLabel"]) for row in source_routes).items())
            )
            if (
                len(source_routes) != expected_route_count
                or observed_pairs != expected_pairs
                or observed_action_pairs != expected_pairs
                or observed_route_action_multiplicities
                != dict(sorted(expected_action_multiplicities.items()))
                or any(int(row["action"]["subsetSize"]) != 4 for row in source_routes)
            ):
                raise ValueError("allowlisted source live target routes changed")
        source_actions = sorted(
            actions_by_label[key[0]],
            key=lambda row: (str(row["targetLabel"]), json.dumps(row["subsetOrbit"])),
        )
        live_labels = {str(row["action"]["targetLabel"]) for row in source_routes}
        expected_live_action_multiplicities = dict(
            source_plan.get(
                "eligibleActionMultiplicities",
                {live_label: 1 for live_label in live_labels},
            )
        )
        observed_live_action_multiplicities = {
            live_label: sum(row["targetLabel"] == live_label for row in source_actions)
            for live_label in sorted(live_labels)
        }
        if observed_live_action_multiplicities != dict(
            sorted(expected_live_action_multiplicities.items())
        ):
            raise ValueError("allowlisted live-label action multiplicities changed")
        selected.append(
            {
                "actions": source_actions,
                "authorizedPairs": sorted(
                    set(source_plan["eligiblePairs"])
                    if args.source is not None
                    else {
                        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
                        for row in source_routes
                    }
                ),
                "eligiblePairs": sorted(
                    {
                        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
                        for row in source_routes
                    }
                ),
                "liveLabels": sorted(live_labels),
                "source": first["source"],
                "sourceQuotientLine": next(iter(quotient_lines)),
                "sourceQuotientPolynomialSha256": next(iter(quotient_hashes)),
            }
        )
    if len(selected) > pilot_cap:
        raise ValueError("planned execution exceeds its source cap")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    preflight_rows = []
    for route in selected:
        source = route["source"]
        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("historical source missing from ledger during preflight")
        source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
        if (
            source_row["coefficient_hash"] != source["coefficientSha256"]
            or source_row["label"] != source["label"]
            or int(source_row["r"]) != int(source["r"])
            or source_row["status"] != "accepted"
            or int(source_row["scoreable"]) != 1
            or len(source_coefficients) != 25
            or source_coefficients[-1] != 1
            or any(source_coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("historical source provenance mismatch during preflight")
        quotient_line = route["sourceQuotientLine"]
        quotient_coefficients = [ZZ(value) for value in quotient_line.split(",")]
        if (
            sha256_bytes(quotient_line.encode()) != route["sourceQuotientPolynomialSha256"]
            or len(quotient_coefficients) != 13
            or quotient_coefficients[-1] != 1
            or source_coefficients[::2] != quotient_coefficients
        ):
            raise ValueError("source quotient identity mismatch during preflight")

        pair_states = {}
        for pair in route["eligiblePairs"]:
            if pair not in frozen:
                raise ValueError(f"eligible pair is absent from the frozen frontier: {pair}")
            state = target_state(connection, *pair)
            pair_states[f"{pair[0]}/r{pair[1]}"] = state
            if args.source is not None and (
                state["teamCount"] != 0
                or state["discovered"]
                or state["minimumDiscAbs"] is not None
                or state["baseline"]
                or state["owned"]
            ):
                raise ValueError(f"allowlisted target is no longer locally live gold: {pair}")
        preflight_rows.append(
            {
                "eligibleTargetStates": pair_states,
                "source": {
                    "coefficientSha256": str(source["coefficientSha256"]),
                    "label": str(source["label"]),
                    "polynomialIndex": int(source["polynomialIndex"]),
                    "r": int(source["r"]),
                    "submissionId": str(source["submissionId"]),
                },
                "sourceQuotientPolynomialSha256": route["sourceQuotientPolynomialSha256"],
            }
        )

    if args.preflight_only:
        connection.close()
        print(
            json.dumps(
                {
                    "event": "k4_preflight_ok",
                    "assignmentPrimeBound": args.assignment_prime_bound,
                    "heavyArithmeticCalls": 0,
                    "networkCalls": 0,
                    "outputs": {
                        "manifest": str(manifest_path.relative_to(ROOT)),
                        "results": str(results_path.relative_to(ROOT)),
                        "summary": str(summary_path.relative_to(ROOT)),
                    },
                    "sources": preflight_rows,
                    "submissionCalls": 0,
                },
                sort_keys=True,
            )
        )
        return 0

    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    known_hashes.update(outbox_hashes(manifest_path))
    result_rows = []
    exact_live_hits = []
    obstruction = None

    for position, route in enumerate(selected, start=1):
        source = route["source"]
        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("historical source missing from ledger")
        source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
        if (
            source_row["coefficient_hash"] != source["coefficientSha256"]
            or source_row["label"] != source["label"]
            or int(source_row["r"]) != int(source["r"])
            or source_row["status"] != "accepted"
            or int(source_row["scoreable"]) != 1
            or any(source_coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("historical source provenance mismatch")

        ring_y = PolynomialRing(ZZ, f"y{position}")
        quotient_line = route["sourceQuotientLine"]
        if sha256_bytes(quotient_line.encode()) != route["sourceQuotientPolynomialSha256"]:
            raise ValueError("source quotient hash mismatch")
        quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
        if source_coefficients[::2] != quotient.list():
            raise ValueError("source polynomial does not equal q(x^2)")
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("source quotient is not monic irreducible degree 12")

        try:
            started = time.monotonic()
            resolvent = quotient.symmetric_power(4, monic=True)
            construction_seconds = time.monotonic() - started
            started = time.monotonic()
            factorization = [(factor, int(exponent)) for factor, exponent in resolvent.factor()]
            factorization_seconds = time.monotonic() - started
            degree_twelve = sorted(
                [factor for factor, exponent in factorization if factor.degree() == 12 and exponent == 1],
                key=lambda factor: sha256_bytes(polynomial_line(factor).encode()),
            )
            if len(degree_twelve) != len(route["actions"]):
                raise RuntimeError(
                    f"found {len(degree_twelve)} degree-12 factors for {len(route['actions'])} actions"
                )
            assignment, assignment_certificate = assign_degree_twelve_factors(
                quotient,
                degree_twelve,
                route["actions"],
                source_r=int(source["r"]),
                prime_bound=args.assignment_prime_bound,
            )
        except (RuntimeError, ArithmeticError) as exc:
            obstruction = {
                "error": f"{type(exc).__name__}: {exc}",
                "position": position,
                "reason": "degree-495 factorization or exact joint modular assignment is not executable",
                "source": source,
            }
            print(json.dumps({"event": "k4_obstruction", **obstruction}, sort_keys=True), flush=True)
            break

        factor_rows = []
        for factor_index, factor in enumerate(degree_twelve):
            action_index = assignment[factor_index]
            action = route["actions"][action_index]
            factor_rows.append(
                {
                    "actionIndex": action_index,
                    "factorCoefficientLine": polynomial_line(factor),
                    "factorSha256": sha256_bytes(polynomial_line(factor).encode()),
                    "targetLabel": str(action["targetLabel"]),
                }
            )

        live_candidates = []
        for factor_index, action_index in assignment.items():
            action = route["actions"][action_index]
            if action["targetLabel"] not in route["liveLabels"]:
                continue
            ring_x = PolynomialRing(ZZ, f"x{position}_{factor_index}")
            x = ring_x.gen()
            factor = ring_x(degree_twelve[factor_index])
            candidate = factor(x**2)
            if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
                raise ValueError("assigned live four-subset candidate is not irreducible degree 24")
            signature = int(candidate.number_of_real_roots())
            possible_signatures = {
                int(value)
                for value in action["sourceSignatureToPossibleTargetSignatures"][str(source["r"])]
            }
            if signature not in possible_signatures:
                raise ValueError("candidate signature contradicts exact assigned action")
            target_pair = (str(action["targetLabel"]), signature)
            state = target_state(connection, *target_pair)
            coefficient_line = canonical_line(candidate)
            coefficient_sha = sha256_bytes(coefficient_line.encode())
            fresh_hash = coefficient_sha not in known_hashes
            field_disc = abs(ZZ(NumberField(candidate, f"a{position}_{factor_index}").absolute_discriminant()))
            live_hit = (
                target_pair in set(route["eligiblePairs"])
                and target_pair in set(route["authorizedPairs"])
                and target_pair in frozen
                and state["teamCount"] == 0
                and not state["baseline"]
                and not state["owned"]
                and fresh_hash
            )
            candidate_row = {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha,
                "fieldDiscriminantAbs": str(field_disc),
                "freshHash": fresh_hash,
                "irreducible": True,
                "liveHit": live_hit,
                "localTargetState": state,
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "r": signature,
                "target": {"label": target_pair[0], "r": target_pair[1]},
            }
            live_candidates.append(candidate_row)
            known_hashes.add(coefficient_sha)
            if live_hit:
                exact_live_hits.append(candidate_row)

        result = {
            "assignmentCertificate": assignment_certificate,
            "eligiblePairs": [f"{label}/r{r}" for label, r in route["eligiblePairs"]],
            "factorAssignment": factor_rows,
            "factorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent}
                for factor, exponent in factorization
            ],
            "liveCandidates": live_candidates,
            "networkCalls": 0,
            "position": position,
            "resolvent": {
                "constructionSeconds": construction_seconds,
                "degree": int(resolvent.degree()),
                "factorizationSeconds": factorization_seconds,
                "sha256": sha256_bytes(polynomial_line(resolvent).encode()),
            },
            "source": source,
            "status": "exact_live_hit" if any(row["liveHit"] for row in live_candidates) else "exact_miss_live_signature",
            "submissionCalls": 0,
        }
        result_rows.append(result)
        print(
            json.dumps(
                {
                    "event": "source_complete",
                    "factorizationSeconds": factorization_seconds,
                    "live": [
                        f"{row['target']['label']}/r{row['target']['r']}:{row['liveHit']}"
                        for row in live_candidates
                    ],
                    "position": position,
                    "sourceLabel": source["label"],
                    "sourceR": source["r"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
    connection.close()

    best_by_pair = {}
    for row in exact_live_hits:
        pair = (row["target"]["label"], int(row["target"]["r"]))
        current = best_by_pair.get(pair)
        if current is None or ZZ(row["fieldDiscriminantAbs"]) < ZZ(current["fieldDiscriminantAbs"]):
            best_by_pair[pair] = row
    staged = [best_by_pair[pair] for pair in sorted(best_by_pair)]
    manifest_text = "".join(f"{row['coefficientLine']}\n" for row in staged)
    if args.source is not None:
        # The factorization can run for a long time. Recheck immediately before
        # publishing so a concurrent/stale artifact is never overwritten.
        validate_output_paths(args.source, results_path, summary_path, manifest_path)
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(manifest_text, encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    results_sha = write_jsonl(results_path, result_rows)

    summary = {
        "artifactSha256": {
            "manifest": sha256_path(manifest_path),
            "plan": sha256_path(PLAN),
            "results": results_sha,
        },
        "assignmentPrimeBound": args.assignment_prime_bound,
        "exactLiveHits": len(exact_live_hits),
        "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS",
        "inputSha256": input_hashes,
        "k4NovelPairsBeyondK2AndK3": len(novel_pairs),
        "mechanism": "k4-incidence-with-joint-modular-factor-action-assignment",
        "networkCalls": 0,
        "obstruction": obstruction,
        "pilotSourcesCompleted": len(result_rows),
        "pilotSourcesPlanned": len(selected),
        "realizedLiveLabelPairs": sorted(
            f"{row['target']['label']}/r{row['target']['r']}"
            for result in result_rows
            for row in result["liveCandidates"]
        ),
        "stagedPairs": [f"{label}/r{r}" for label, r in sorted(best_by_pair)],
        "stagedPolynomials": len(staged),
        "status": (
            "exact_hits_staged"
            if staged
            else "blocked_factorization_or_assignment"
            if obstruction
            else "blocked_signature_miss_on_capped_pilot"
        ),
        "submissionCalls": 0,
    }
    if args.source is not None:
        summary["sourceSelector"] = args.source
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

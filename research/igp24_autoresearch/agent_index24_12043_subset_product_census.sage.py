#!/usr/bin/env sage -python
"""Single-source exact lower-Kummer action census for 24T12043 -> 24T12067.

The light ``--preflight-only`` path deliberately imports no Sage modules.  The
isolated ``--census-only`` path is the sole heavy operation: for the certified
two-block action of 24T12043 it enumerates every quotient orbit of k-subsets,
1 <= k <= 11, having length twelve.  For such an orbit O it certifies the
relative invariant

    h_S = product(beta_i for i in S),  S in O,

where the accepted even source is f(x)=q(x^2) and beta_i are the roots of q.
The induced action on +/-sqrt(h_S) is computed exactly from the signed source
action.  A route is executable only if that action is faithful 24T12067 and
its point stabilizer is one of the already certified index-24 subgroup
classes.  The census contains no source or candidate coefficient material,
makes no network/submission calls, and refuses to overwrite its output.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"
FRONTIER_PLAN = DATA / "index24_relative_invariant_frontier_20260722_plan.json"
DEFAULT_OUTPUT = DATA / "agent_index24_12043_subset_product_census.json"

EXPECTED_SHA256 = {
    ACTION_MAP: "c696830fa3ae78431ab2341d80931f52f343673d2d5b6dadf2eaf60796cf464a",
    ISOMORPHISM: "4b52472158c882b23ee585ab409eff9ced4871da41bb00f411c4f4b4af0ac82d",
    FRONTIER_PLAN: "dafcf6519046b40380ed4852063e208b392f8e002bdd09b4e118e3d2a3e3d448",
}
SOURCE = {
    "coefficientBytes": 94,
    "coefficientSha256": "370794c3c02fe547896398c8a535cc434f6af3fae90524cde7a5c7cd149e01c4",
    "label": "24T12043",
    "polynomialIndex": 970,
    "r": 24,
    "submissionId": "sub_f0a13c6c019d45c2a4391c084125d54c",
    "t": 12043,
}
TARGET = {"label": "24T12067", "r": 24, "t": 12067}
EXPECTED_SOURCE_ORDER = 12288
EXPECTED_QUOTIENT_ORDER = 1536
EXPECTED_QUOTIENT_T = 226
EXPECTED_CERTIFICATE_ROW_SHA256 = (
    "c347c7137d6e0ddbb9f46562de229cf40aff8c680487e1caf23b0fdaaf5c7613"
)
EXPECTED_SUBGROUP_IDENTITIES = {
    "0901f56a15000ea7aab46d1b3c5fb3f6f037b9b3d0387c29af138eea2ee0f0f2",
    "0c23ec5a3cbf132dc98241a4e13ff6f423c5d394866c94e7f61a1d48211c4a2a",
    "2e4a604e09787fdd8dc90a85246ea22dad14818c3e21f1df0cc32dd804a56ddf",
    "d820f27e9d9d0fe488517cbe192ff3fd496ee33c3897447c736deef735675a7b",
}
EXPECTED_BLOCKS = [[2 * index + 1, 2 * index + 2] for index in range(12)]
TARGET_MAX_AGE_SECONDS = 6 * 60 * 60


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_timestamp(value: str) -> datetime:
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        raise ValueError("target generation timestamp is not timezone-aware")
    return observed.astimezone(timezone.utc)


def target_state(connection: sqlite3.Connection) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (TARGET["label"], TARGET["r"]),
    ).fetchone()
    if row is None or row[3] is None:
        raise ValueError("target row or generation timestamp is missing")
    generated = parse_timestamp(str(row[3]))
    age = (datetime.now(timezone.utc) - generated).total_seconds()
    state = {
        "ageSeconds": max(0, int(age)),
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "fresh": -300 <= age <= TARGET_MAX_AGE_SECONDS,
        "generatedAt": str(row[3]),
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }
    return state


def require_live_nonbaseline(state: dict) -> None:
    if not (
        state["fresh"]
        and state["teamCount"] == 0
        and not state["discovered"]
        and state["minimumDiscAbs"] is None
        and not state["baseline"]
        and not state["owned"]
    ):
        raise ValueError("24T12067/r24 is not a fresh live nonbaseline unowned target")


def validate_source(connection: sqlite3.Connection) -> dict:
    row = connection.execute(
        """
        SELECT s.created_at,s.updated_at,s.queued_count,s.verified_count,s.failed_count,
          (SELECT COUNT(*) FROM polynomials px WHERE px.submission_id=s.submission_id),
          p.original_line,p.coefficients,p.coefficient_hash,
          v.label,v.t,v.r,v.status,v.scoreable,v.scoring_status
        FROM submissions s
        JOIN polynomials p USING(submission_id)
        JOIN verifications v USING(submission_id,polynomial_index)
        WHERE s.submission_id=? AND p.polynomial_index=?
        """,
        (SOURCE["submissionId"], SOURCE["polynomialIndex"]),
    ).fetchone()
    if row is None:
        raise ValueError("sealed source receipt is missing")
    coefficients = [int(value.strip()) for value in str(row["coefficients"]).split(",")]
    original = [int(value.strip()) for value in str(row["original_line"]).split(",")]
    canonical_line = ",".join(str(value) for value in coefficients)
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients != original
        or any(coefficients[index] for index in range(1, 25, 2))
        or sha256_bytes(canonical_line.encode()) != SOURCE["coefficientSha256"]
        or str(row["coefficient_hash"]) != SOURCE["coefficientSha256"]
        or len(str(row["original_line"]).encode()) != SOURCE["coefficientBytes"]
        or str(row["label"]) != SOURCE["label"]
        or int(row["t"]) != SOURCE["t"]
        or int(row["r"]) != SOURCE["r"]
        or str(row["status"]) != "accepted"
        or int(row["scoreable"]) != 1
        or str(row["scoring_status"]) != "scoreable"
        or int(row["queued_count"]) != 0
        or int(row["failed_count"]) != 0
        or int(row["verified_count"]) != int(row[5])
    ):
        raise ValueError("sealed source receipt or even-polynomial provenance changed")
    return {
        "coefficientBytes": SOURCE["coefficientBytes"],
        "coefficientSha256": SOURCE["coefficientSha256"],
        "createdAt": str(row["created_at"]),
        "fullyAdjudicated": True,
        "label": SOURCE["label"],
        "polynomialIndex": SOURCE["polynomialIndex"],
        "r": SOURCE["r"],
        "submissionId": SOURCE["submissionId"],
        "updatedAt": str(row["updated_at"]),
        "verifiedCount": int(row["verified_count"]),
    }


def mutable_state_recheck() -> tuple[dict, dict]:
    """Refresh the exact source receipt and mutable target with named rows."""
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        source = validate_source(connection)
        state = target_state(connection)
        require_live_nonbaseline(state)
        return source, state
    finally:
        connection.close()


def validate_sealed_inputs() -> tuple[dict, dict, dict]:
    input_hashes = {
        str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256
    }
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"sealed input hash mismatch for {path.name}")

    action_rows = [
        row for row in load_jsonl(ACTION_MAP) if row.get("sourceLabel") == SOURCE["label"]
    ]
    if len(action_rows) != 1:
        raise ValueError("expected exactly one source action-map row")
    action = action_rows[0]
    systems = action.get("systems", [])
    if (
        int(action.get("sourceT", -1)) != SOURCE["t"]
        or int(action.get("sourceOrder", -1)) != EXPECTED_SOURCE_ORDER
        or len(systems) != 1
    ):
        raise ValueError("source action-map anchor changed")
    system = systems[0]
    if (
        system.get("blocks") != EXPECTED_BLOCKS
        or int(system.get("blockActionT12", -1)) != EXPECTED_QUOTIENT_T
        or int(system.get("blockActionOrder", -1)) != EXPECTED_QUOTIENT_ORDER
        or not bool(system.get("flipInSource"))
        or str(system.get("targetLabel")) != SOURCE["label"]
        or int(system.get("targetOrder", -1)) != EXPECTED_SOURCE_ORDER
    ):
        raise ValueError("exact two-block system anchor changed")

    certificates = [
        row
        for row in load_jsonl(ISOMORPHISM)
        if int(row.get("sourceT", -1)) == SOURCE["t"]
        and int(row.get("targetT", -1)) == TARGET["t"]
    ]
    if len(certificates) != 1:
        raise ValueError("expected exactly one index-24 isomorphism certificate")
    certificate = certificates[0]
    identities = {
        str(row["subgroupClassIdentitySha256"])
        for row in certificate.get("subgroupClasses", [])
    }
    if (
        certificate.get("status") != "certified_isomorphic"
        or sha256_bytes(canonical_json(certificate).encode())
        != EXPECTED_CERTIFICATE_ROW_SHA256
        or identities != EXPECTED_SUBGROUP_IDENTITIES
        or any(int(row["actualTargetT"]) != TARGET["t"] for row in certificate["subgroupClasses"])
    ):
        raise ValueError("certified index-24 subgroup frontier changed")

    plan = json.loads(FRONTIER_PLAN.read_text())
    routes = [
        row
        for row in plan.get("routes", [])
        if row["source"]["label"] == SOURCE["label"]
        and int(row["source"]["r"]) == SOURCE["r"]
        and row["target"]["label"] == TARGET["label"]
        and int(row["target"]["r"]) == TARGET["r"]
    ]
    if (
        len(routes) != 1
        or int(routes[0].get("priorityRank", -1)) != 1
        or not routes[0].get("blockedOnExplicitRelativeInvariant")
        or set(routes[0].get("subgroupClassIdentitySha256", []))
        != EXPECTED_SUBGROUP_IDENTITIES
    ):
        raise ValueError("sealed priority route changed")
    return input_hashes, system, certificate


def preflight() -> dict:
    input_hashes, _system, _certificate = validate_sealed_inputs()
    source, state = mutable_state_recheck()
    return {
        "candidateCoefficientMaterialIncluded": False,
        "candidateMechanism": "h_S=product(beta_i for i in S); candidate factor F(x^2)",
        "heavyArithmeticCalls": 0,
        "inputSha256": input_hashes,
        "networkCalls": 0,
        "source": source,
        "submissionCalls": 0,
        "target": {"label": TARGET["label"], "r": TARGET["r"], "state": state},
    }


def subset_orbits(block_generators: list[list[int]], subset_size: int) -> list[list[tuple[int, ...]]]:
    remaining = set(itertools.combinations(range(12), subset_size))
    orbits = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            subset = queue.popleft()
            for permutation in block_generators:
                image = tuple(sorted(permutation[index] for index in subset))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        orbits.append(sorted(orbit))
    return orbits


def block_action_data(libgap, blocks: list[tuple[int, int]], permutation) -> tuple[list[int], list[int]]:
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def induced_permutation(libgap, orbit, block_permutation, sign_vector):
    position = {subset: index for index, subset in enumerate(orbit)}
    images = []
    for subset in orbit:
        image_subset = tuple(sorted(block_permutation[index] for index in subset))
        target = position[image_subset]
        sign = sum(sign_vector[index] for index in subset) % 2
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def orbit_permutation(libgap, orbit, block_permutation):
    position = {subset: index for index, subset in enumerate(orbit)}
    return libgap.PermList(
        [
            position[tuple(sorted(block_permutation[index] for index in subset))] + 1
            for subset in orbit
        ]
    )


def fixed_points(libgap, permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def subgroup_identity(libgap, subgroup) -> str:
    generators = sorted(str(value) for value in libgap.SmallGeneratingSet(subgroup))
    return sha256_bytes(
        canonical_json(
            {
                "sourceLabel": SOURCE["label"],
                "subgroupGenerators": generators,
                "targetLabel": TARGET["label"],
            }
        ).encode()
    )


def census_action_rows(system: dict) -> tuple[list[dict], dict]:
    # Deferred by design: python3 --preflight-only must not initialize Sage/GAP.
    from sage.all import GF, Matrix, libgap

    source_group = libgap.TransitiveGroup(24, SOURCE["t"])
    if (
        int(libgap.Size(source_group)) != EXPECTED_SOURCE_ORDER
        or int(libgap.TransitiveIdentification(source_group)) != SOURCE["t"]
    ):
        raise ArithmeticError("standard source group identity changed")
    generators = list(libgap.GeneratorsOfGroup(source_group))
    blocks = [tuple(int(value) for value in block) for block in system["blocks"]]
    generator_data = [block_action_data(libgap, blocks, generator) for generator in generators]
    block_generators = [row[0] for row in generator_data]
    quotient_group = libgap.Group(
        [libgap.PermList([value + 1 for value in row]) for row in block_generators]
    )
    quotient_order = int(libgap.Size(quotient_group))
    quotient_t = int(libgap.TransitiveIdentification(quotient_group))
    if quotient_order != EXPECTED_QUOTIENT_ORDER or quotient_t != EXPECTED_QUOTIENT_T:
        raise ArithmeticError("exact source block quotient changed")

    relevant_classes = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(source_group)):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) not in (1, 2):
            continue
        relevant_classes.append(
            (
                class_index,
                int(libgap.Size(conjugacy_class)),
                fixed_points(libgap, representative, 24),
                representative,
            )
        )

    rows = []
    orbit_histogram = {}
    for subset_size in range(1, 12):
        orbits = subset_orbits(block_generators, subset_size)
        orbit_histogram[str(subset_size)] = sorted(len(orbit) for orbit in orbits)
        for orbit_index, orbit in enumerate(orbits):
            if len(orbit) != 12:
                continue
            induced_generators = [
                induced_permutation(libgap, orbit, block_permutation, sign_vector)
                for block_permutation, sign_vector in generator_data
            ]
            target_group = libgap.Group(induced_generators)
            target_order = int(libgap.Size(target_group))
            transitive = bool(libgap.IsTransitive(target_group, libgap.eval("[1..24]")))
            induced_quotient = libgap.Group(
                [
                    orbit_permutation(libgap, orbit, block_permutation)
                    for block_permutation, _sign_vector in generator_data
                ]
            )
            quotient_action_order = int(libgap.Size(induced_quotient))
            quotient_action_t = int(libgap.TransitiveIdentification(induced_quotient))
            incidence = Matrix(
                GF(2),
                12,
                12,
                lambda row, column: int(column in orbit[row]),
            )
            signature_map = defaultdict(set)
            profiles = []
            for class_index, class_size, source_r, representative in relevant_classes:
                block_permutation, sign_vector = block_action_data(libgap, blocks, representative)
                induced = induced_permutation(libgap, orbit, block_permutation, sign_vector)
                target_r = fixed_points(libgap, induced, 24)
                signature_map[source_r].add(target_r)
                profiles.append(
                    {
                        "classIndex": class_index,
                        "classSize": class_size,
                        "sourceR": source_r,
                        "targetR": target_r,
                    }
                )
            row = {
                "faithful": False,
                "incidenceMatrixRank": int(incidence.rank()),
                "incidenceRows": [
                    [int(value) for value in incidence.row(index)] for index in range(12)
                ],
                "inducedQuotientOrder": quotient_action_order,
                "inducedQuotientT12": quotient_action_t,
                "mechanismCertificate": (
                    "For f(x)=q(x^2), choose one square root alpha_i of each beta_i. "
                    "For every S in the displayed quotient orbit set h_S=product(beta_i). "
                    "Then sqrt(h_S)=product(alpha_i), and the exact signed source action "
                    "is transported by the displayed GF(2) incidence map."
                ),
                "orbitIndexWithinSubsetSize": orbit_index,
                "profiles": profiles,
                "sourceSignatureToPossibleTargetSignatures": {
                    str(source_r): sorted(values)
                    for source_r, values in sorted(signature_map.items())
                },
                "subsetOrbit": [list(subset) for subset in orbit],
                "subsetSize": subset_size,
                "targetOrder": target_order,
                "transitive": transitive,
            }
            if transitive:
                target_t = int(libgap.TransitiveIdentification(target_group))
                homomorphism = libgap.GroupHomomorphismByImages(
                    source_group, target_group, generators, induced_generators
                )
                if homomorphism == libgap.fail:
                    raise ArithmeticError("induced generator images do not define a homomorphism")
                kernel_order = int(libgap.Size(libgap.Kernel(homomorphism)))
                point_stabilizer = libgap.PreImage(
                    homomorphism, libgap.Stabilizer(target_group, 1)
                )
                index = int(libgap.Index(source_group, point_stabilizer))
                core_order = int(libgap.Size(libgap.Core(source_group, point_stabilizer)))
                identity = subgroup_identity(libgap, point_stabilizer) if target_t == TARGET["t"] else None
                row.update(
                    {
                        "coreOrder": core_order,
                        "faithful": kernel_order == 1,
                        "kernelOrder": kernel_order,
                        "pointStabilizerIndex": index,
                        "pointStabilizerOrder": int(libgap.Size(point_stabilizer)),
                        "subgroupClassIdentitySha256": identity,
                        "targetLabel": f"24T{target_t}",
                        "targetT": target_t,
                    }
                )
            rows.append(row)
    return rows, {
        "generatorCount": len(generators),
        "orbitSizeHistogramBySubsetSize": orbit_histogram,
        "quotientOrder": quotient_order,
        "quotientT12": quotient_t,
        "sourceOrder": int(libgap.Size(source_group)),
        "sourceT": int(libgap.TransitiveIdentification(source_group)),
    }


def publish_no_overwrite(path: Path, payload: bytes) -> str:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    else:
        temporary.unlink()
    return sha256_bytes(payload)


def validate_output_path(path: Path) -> None:
    if path.resolve().parent != DATA.resolve():
        raise ValueError("census output escaped the data directory")
    collisions = [candidate for candidate in (path, Path(str(path) + ".tmp")) if candidate.exists()]
    if collisions:
        raise FileExistsError("refusing to overwrite census output: " + ", ".join(map(str, collisions)))


def run_census(output: Path) -> dict:
    validate_output_path(output)
    pre = preflight()
    _input_hashes, system, _certificate = validate_sealed_inputs()
    rows, group_certificate = census_action_rows(system)

    exact_hits = []
    for row in rows:
        mapped = row["sourceSignatureToPossibleTargetSignatures"].get(str(SOURCE["r"]), [])
        if (
            row.get("targetT") == TARGET["t"]
            and row.get("targetOrder") == EXPECTED_SOURCE_ORDER
            and row.get("faithful")
            and row.get("kernelOrder") == 1
            and row.get("pointStabilizerIndex") == 24
            and row.get("coreOrder") == 1
            and row.get("subgroupClassIdentitySha256") in EXPECTED_SUBGROUP_IDENTITIES
            and mapped == [TARGET["r"]]
        ):
            exact_hits.append(row)
    exact_hits.sort(
        key=lambda row: (
            int(row["subsetSize"]),
            canonical_json(row["subsetOrbit"]),
            str(row["subgroupClassIdentitySha256"]),
        )
    )
    executable = None
    if exact_hits:
        selected = exact_hits[0]
        executable = {
            "candidateConstruction": (
                "factor q.symmetric_power(k) at the certified degree-12 subset orbit, "
                "then substitute y=x^2"
            ),
            "factorDegree": 12,
            "formula": "h_S=product(beta_i for i in S); roots are +/-sqrt(h_S)",
            "incidenceMatrixRank": selected["incidenceMatrixRank"],
            "kind": "lower_kummer_subset_product_relative_invariant",
            "subgroupClassIdentitySha256": selected["subgroupClassIdentitySha256"],
            "subsetOrbit": selected["subsetOrbit"],
            "subsetSize": selected["subsetSize"],
            "target": {"label": TARGET["label"], "r": TARGET["r"]},
        }
    payload = {
        "actionRows": rows,
        "coefficientMaterialIncluded": False,
        "exactExecutableHitCount": len(exact_hits),
        "executableInvariant": executable,
        "family": "INDEX24_LOWER_KUMMER_SUBSET_PRODUCT_RELATIVE_INVARIANT",
        "groupCertificate": group_certificate,
        "heavyArithmeticCalls": 1,
        "inputSha256": pre["inputSha256"],
        "mathematicalCertificate": (
            "Each length-12 subset orbit gives twelve conjugates h_S. The displayed "
            "incidence matrix transports the source Kummer squareclass module, while "
            "the induced signed permutations certify the exact degree-24 action. "
            "Executability additionally requires faithfulness, target identification "
            "24T12067, and membership of the point stabilizer in the sealed subgroup frontier."
        ),
        "networkCalls": 0,
        "schemaVersion": "index24-12043-subset-product-census-v1",
        "source": pre["source"],
        "status": (
            "exact_executable_invariant_certified"
            if executable is not None
            else "certified_no_exact_subset_product_invariant"
        ),
        "submissionCalls": 0,
        "target": pre["target"],
    }
    # Recheck the mutable target immediately before publishing the certificate.
    _final_source, final_state = mutable_state_recheck()
    payload["target"]["stateAtPublish"] = final_state
    rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    artifact_sha = publish_no_overwrite(output, rendered)
    return {
        "artifact": str(output.relative_to(ROOT)),
        "artifactSha256": artifact_sha,
        "coefficientMaterialIncluded": False,
        "event": "index24_12043_subset_product_census_complete",
        "exactExecutableHitCount": len(exact_hits),
        "heavyArithmeticCalls": 1,
        "networkCalls": 0,
        "status": payload["status"],
        "submissionCalls": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--census-only", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preflight_only:
        event = {
            **preflight(),
            "event": "index24_12043_subset_product_preflight_ok",
            "plannedOutput": str(args.output.relative_to(ROOT)),
        }
    else:
        event = run_census(args.output)
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

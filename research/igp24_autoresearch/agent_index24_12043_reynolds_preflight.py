#!/usr/bin/env python3
"""Light, coefficient-free preflight for a 24T12043 Reynolds invariant.

The sealed index-24 certificate proves that four automorphism-orbits of
core-free subgroups H <= 24T12043 have order 512, index 24, and coset action
24T12067.  This module records a universal formal H-relative invariant and the
remaining gates needed before it can be specialized to the accepted source.

No Sage/GAP module is imported here.  In particular, this preflight does not
claim that a formal invariant stays separating after the source roots are
substituted.  An executable candidate remains null until an exact root-action
alignment and an exact noncollision certificate are supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CENSUS = DATA / "agent_index24_12043_subset_product_census.json"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"
FRONTIER_PLAN = DATA / "index24_relative_invariant_frontier_20260722_plan.json"
RUNBOOK = DATA / "agent_index24_12043_reynolds_invariant_runbook.json"

EXPECTED_SHA256 = {
    CENSUS: "07b1c3e1b2276d0692dba10121278bc3686cce14d2fd8a741cf5c59a9e2d5977",
    ACTION_MAP: "c696830fa3ae78431ab2341d80931f52f343673d2d5b6dadf2eaf60796cf464a",
    ISOMORPHISM: "4b52472158c882b23ee585ab409eff9ced4871da41bb00f411c4f4b4af0ac82d",
    FRONTIER_PLAN: "dafcf6519046b40380ed4852063e208b392f8e002bdd09b4e118e3d2a3e3d448",
}
EXPECTED_ISOMORPHISM_ROW_SHA256 = (
    "c347c7137d6e0ddbb9f46562de229cf40aff8c680487e1caf23b0fdaaf5c7613"
)
EXPECTED_SUBGROUP_IDENTITIES = {
    "0901f56a15000ea7aab46d1b3c5fb3f6f037b9b3d0387c29af138eea2ee0f0f2",
    "0c23ec5a3cbf132dc98241a4e13ff6f423c5d394866c94e7f61a1d48211c4a2a",
    "2e4a604e09787fdd8dc90a85246ea22dad14818c3e21f1df0cc32dd804a56ddf",
    "d820f27e9d9d0fe488517cbe192ff3fd496ee33c3897447c736deef735675a7b",
}
EXPECTED_BLOCKS = [[2 * index + 1, 2 * index + 2] for index in range(12)]
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
EXPECTED_SUBGROUP_ORDER = 512
EXPECTED_INDEX = 24
EXPECTED_AUTOMORPHISM_ORDER = 98304
TARGET_MAX_AGE_SECONDS = 6 * 60 * 60


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


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


def validate_sealed_inputs() -> tuple[dict, dict, dict, dict]:
    observed_hashes = {
        str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256
    }
    for path, expected in EXPECTED_SHA256.items():
        if observed_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"sealed input hash mismatch for {path.name}")

    census = json.loads(CENSUS.read_text())
    if (
        census.get("status") != "certified_no_exact_subset_product_invariant"
        or int(census.get("exactExecutableHitCount", -1)) != 0
        or census.get("executableInvariant") is not None
        or census.get("source", {}).get("label") != SOURCE["label"]
        or census.get("target", {}).get("label") != TARGET["label"]
    ):
        raise ValueError("sealed subset-product obstruction changed")

    rows = [
        row
        for row in load_jsonl(ISOMORPHISM)
        if int(row.get("sourceT", -1)) == SOURCE["t"]
        and int(row.get("targetT", -1)) == TARGET["t"]
    ]
    if len(rows) != 1:
        raise ValueError("expected exactly one sealed 12043 -> 12067 certificate")
    certificate = rows[0]
    subgroup_rows = certificate.get("subgroupClasses", [])
    identities = {
        str(row.get("subgroupClassIdentitySha256")) for row in subgroup_rows
    }
    if (
        sha256_bytes(canonical_json(certificate).encode())
        != EXPECTED_ISOMORPHISM_ROW_SHA256
        or certificate.get("status") != "certified_isomorphic"
        or int(certificate.get("order", -1)) != EXPECTED_SOURCE_ORDER
        or int(certificate.get("automorphismGroupOrder", -1))
        != EXPECTED_AUTOMORPHISM_ORDER
        or int(certificate.get("coreFreeIndex24SubgroupClassCountForTarget", -1)) != 4
        or identities != EXPECTED_SUBGROUP_IDENTITIES
    ):
        raise ValueError("sealed index-24 subgroup certificate changed")
    for row in subgroup_rows:
        identity_profiles = [
            profile
            for profile in row.get("profiles", [])
            if int(profile.get("sourceR", -1)) == SOURCE["r"]
        ]
        if (
            int(row.get("actualTargetT", -1)) != TARGET["t"]
            or int(row.get("subgroupOrder", -1)) != EXPECTED_SUBGROUP_ORDER
            or int(row.get("index", -1)) != EXPECTED_INDEX
            or int(row.get("coreOrder", -1)) != 1
            or len(identity_profiles) != 1
            or int(identity_profiles[0].get("targetR", -1)) != TARGET["r"]
        ):
            raise ValueError("one certified subgroup/coset profile changed")

    action_rows = [
        row for row in load_jsonl(ACTION_MAP) if row.get("sourceLabel") == SOURCE["label"]
    ]
    if len(action_rows) != 1 or len(action_rows[0].get("systems", [])) != 1:
        raise ValueError("sealed source block action is missing or ambiguous")
    system = action_rows[0]["systems"][0]
    if (
        int(action_rows[0].get("sourceOrder", -1)) != EXPECTED_SOURCE_ORDER
        or system.get("blocks") != EXPECTED_BLOCKS
        or int(system.get("blockActionOrder", -1)) != 1536
        or int(system.get("blockActionT12", -1)) != 226
        or not system.get("flipInSource")
    ):
        raise ValueError("sealed natural source action changed")

    plan = json.loads(FRONTIER_PLAN.read_text())
    routes = [
        route
        for route in plan.get("routes", [])
        if route.get("source", {}).get("label") == SOURCE["label"]
        and int(route.get("source", {}).get("r", -1)) == SOURCE["r"]
        and route.get("target", {}).get("label") == TARGET["label"]
        and int(route.get("target", {}).get("r", -1)) == TARGET["r"]
    ]
    if (
        len(routes) != 1
        or int(routes[0].get("priorityRank", -1)) != 1
        or not routes[0].get("blockedOnExplicitRelativeInvariant")
        or set(routes[0].get("subgroupClassIdentitySha256", []))
        != EXPECTED_SUBGROUP_IDENTITIES
    ):
        raise ValueError("sealed priority route changed")
    return observed_hashes, census, certificate, routes[0]


def source_receipt_state() -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT s.created_at,s.updated_at,s.queued_count,s.verified_count,s.failed_count,
              (SELECT COUNT(*) FROM polynomials px WHERE px.submission_id=s.submission_id)
                AS polynomial_count,
              p.coefficient_hash,LENGTH(p.original_line) AS coefficient_bytes,
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
        if (
            str(row["coefficient_hash"]) != SOURCE["coefficientSha256"]
            or int(row["coefficient_bytes"]) != SOURCE["coefficientBytes"]
            or str(row["label"]) != SOURCE["label"]
            or int(row["t"]) != SOURCE["t"]
            or int(row["r"]) != SOURCE["r"]
            or str(row["status"]) != "accepted"
            or int(row["scoreable"]) != 1
            or str(row["scoring_status"]) != "scoreable"
            or int(row["queued_count"]) != 0
            or int(row["failed_count"]) != 0
            or int(row["verified_count"]) != int(row["polynomial_count"])
        ):
            raise ValueError("sealed source receipt changed")
        return {
            **SOURCE,
            "createdAt": str(row["created_at"]),
            "fullyAdjudicated": True,
            "updatedAt": str(row["updated_at"]),
            "verifiedCount": int(row["verified_count"]),
        }
    finally:
        connection.close()


def target_state() -> dict:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
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
    finally:
        connection.close()
    if row is None or row[3] is None:
        raise ValueError("target state is missing")
    generated = datetime.fromisoformat(str(row[3]).replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("target timestamp is not timezone-aware")
    age = max(0, int((datetime.now(timezone.utc) - generated).total_seconds()))
    state = {
        "ageSeconds": age,
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "fresh": age <= TARGET_MAX_AGE_SECONDS,
        "generatedAt": str(row[3]),
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }
    state["liveNonbaseline"] = bool(
        state["fresh"]
        and state["teamCount"] == 0
        and not state["discovered"]
        and state["minimumDiscAbs"] is None
        and not state["baseline"]
        and not state["owned"]
    )
    return state


def universal_seed_exponents(degree: int = 24) -> tuple[int, ...]:
    """The minimum-total-degree nonnegative vector with all entries distinct."""
    if degree < 1:
        raise ValueError("degree must be positive")
    return tuple(range(degree))


def universal_seed_certificate() -> dict:
    exponents = universal_seed_exponents(24)
    if len(set(exponents)) != 24 or sum(exponents) != 276:
        raise ArithmeticError("universal asymmetric monomial invariant changed")
    return {
        "exponentCount": len(exponents),
        "exponentVectorSha256": sha256_bytes(canonical_json(exponents).encode()),
        "maximumExponent": max(exponents),
        "minimalityScope": (
            "nonnegative monomial exponent vectors with trivial stabilizer in S24"
        ),
        "minimalityProof": (
            "trivial S24 stabilizer requires 24 distinct nonnegative exponents; "
            "their sum is at least 0+1+...+23=276"
        ),
        "seedStabilizerOrder": 1,
        "totalDegree": sum(exponents),
    }


def apply_permutation(vector: tuple[int, ...], permutation: tuple[int, ...]) -> tuple[int, ...]:
    """Permute exponent positions; permutation[i] is the image of position i."""
    if len(vector) != len(permutation) or sorted(permutation) != list(range(len(vector))):
        raise ValueError("invalid vector/permutation dimensions")
    image = [0] * len(vector)
    for index, exponent in enumerate(vector):
        image[permutation[index]] = exponent
    return tuple(image)


def orbit_support(
    seed: tuple[int, ...], subgroup: set[tuple[int, ...]]
) -> frozenset[tuple[int, ...]]:
    return frozenset(apply_permutation(seed, permutation) for permutation in subgroup)


def support_stabilizer(
    support: frozenset[tuple[int, ...]], group: set[tuple[int, ...]]
) -> set[tuple[int, ...]]:
    return {
        permutation
        for permutation in group
        if frozenset(apply_permutation(seed, permutation) for seed in support) == support
    }


def exact_reynolds_support_certificate(
    seed: tuple[int, ...],
    group: set[tuple[int, ...]],
    subgroup: set[tuple[int, ...]],
) -> dict:
    """Finite exact checker used by tests and the future Sage/GAP adapter."""
    if not subgroup or not subgroup.issubset(group):
        raise ValueError("H must be a nonempty subset of G")
    seed_stabilizer = {
        permutation for permutation in group if apply_permutation(seed, permutation) == seed
    }
    if not seed_stabilizer.issubset(subgroup):
        raise ValueError("seed stabilizer is not contained in H")
    support = orbit_support(seed, subgroup)
    stabilizer = support_stabilizer(support, group)
    if stabilizer != subgroup:
        raise ArithmeticError("Reynolds support stabilizer is not exactly H")
    if len(group) % len(subgroup):
        raise ArithmeticError("H order does not divide G order")
    return {
        "cosetConjugateCount": len(group) // len(subgroup),
        "formalConjugatesDistinct": True,
        "seedStabilizerOrder": len(seed_stabilizer),
        "supportCardinality": len(support),
        "supportStabilizerOrder": len(stabilizer),
    }


def validate_specialization_certificate(payload: dict) -> None:
    """Fail closed unless a future exact arithmetic worker proves every gate."""
    required_true = (
        "exactRootActionAlignment",
        "formalSupportStabilizerExact",
        "resolventCoefficientsExactIntegers",
        "specializedConjugatesDistinct",
        "resolventIrreducible",
        "sourceReceiptRechecked",
        "targetStateRechecked",
    )
    missing = [field for field in required_true if payload.get(field) is not True]
    if missing:
        raise ValueError("specialization certificate missing exact gates: " + ", ".join(missing))
    if (
        int(payload.get("resolventDegree", -1)) != 24
        or int(payload.get("resolventRealRoots", -1)) != 24
        or int(payload.get("resolventGaloisOrder", -1)) != EXPECTED_SOURCE_ORDER
        or str(payload.get("resolventGaloisLabel")) != TARGET["label"]
        or not payload.get("resolventDiscriminantNonzero")
    ):
        raise ValueError("specialized resolvent identity/separation is not exact 24T12067/r24")


def build_runbook() -> dict:
    input_hashes, _census, certificate, route = validate_sealed_inputs()
    subgroup_rows = sorted(
        certificate["subgroupClasses"],
        key=lambda row: str(row["subgroupClassIdentitySha256"]),
    )
    seed = universal_seed_certificate()
    return {
        "abstractActionCertificate": {
            "automorphismGroupOrder": EXPECTED_AUTOMORPHISM_ORDER,
            "cosetActionDegree": EXPECTED_INDEX,
            "cosetActionFaithful": True,
            "cosetActionTarget": TARGET["label"],
            "faithfulnessProof": "Core_G(H)=1 for every sealed subgroup class",
            "sourceGroupOrder": EXPECTED_SOURCE_ORDER,
            "subgroupClassCount": len(subgroup_rows),
            "subgroupClassIdentitySha256": [
                row["subgroupClassIdentitySha256"] for row in subgroup_rows
            ],
            "subgroupIndex": EXPECTED_INDEX,
            "subgroupOrder": EXPECTED_SUBGROUP_ORDER,
        },
        "coefficientMaterialIncluded": False,
        "exactObstruction": {
            "censusStatus": "certified_no_exact_subset_product_invariant",
            "reason": (
                "the exhaustive lower-Kummer subset-product census has no faithful "
                "24T12067 action"
            ),
        },
        "executableInvariant": None,
        "family": "INDEX24_SUBGROUP_REYNOLDS_ORBIT_INVARIANT",
        "formalInvariant": {
            "cosetConjugateCount": EXPECTED_INDEX,
            "formula": "I_H,a=sum_{b in H.a} product_i x_i^b_i",
            "formalConjugatesDistinct": True,
            "formalStabilizer": "H",
            "formalStabilizerProof": (
                "if K=Stab_G(a)<=H, monomial independence identifies the support "
                "with H.a; any g stabilizing H.a satisfies g.a=h.a, hence h^-1 g "
                "lies in K<=H and g lies in H"
            ),
            "supportCardinality": EXPECTED_SUBGROUP_ORDER,
            "universalFallbackSeed": seed,
        },
        "heavyArithmeticCalls": 0,
        "inputSha256": input_hashes,
        "networkCalls": 0,
        "nextExactSteps": [
            "reconstruct all four H inside the standard 24T12043 action and match their sealed identities",
            "search squarefree seeds by increasing degree; accept only when Stab_G(a)<=H and the full support stabilizer equals H",
            "if no smaller seed is certified, use the degree-276 universal asymmetric seed",
            "produce an exact conjugator aligning the accepted polynomial roots with the standard 24T12043 action and its two-block system",
            "specialize all 24 coset conjugates and prove their exact pairwise separation",
            "certify a monic integral irreducible degree-24 resolvent with label 24T12067 and 24 real roots",
            "recheck source receipt, target state, and local hash absence immediately before staging",
        ],
        "priorityRank": int(route["priorityRank"]),
        "schemaVersion": "index24-12043-reynolds-invariant-runbook-v1",
        "source": SOURCE,
        "status": "prepared_formal_invariant_blocked_on_root_alignment_and_specialization",
        "submissionCalls": 0,
        "target": TARGET,
        "unprovedExecutionGates": [
            "exactRootActionAlignment",
            "specializedConjugatesDistinct",
            "exactIntegralResolvent",
            "resolventIs24T12067_r24",
        ],
    }


def preflight() -> dict:
    runbook = build_runbook()
    source = source_receipt_state()
    target = target_state()
    runbook_hash = sha256_path(RUNBOOK) if RUNBOOK.exists() else None
    return {
        "candidateCoefficientMaterialIncluded": False,
        "event": "index24_12043_reynolds_preflight_ok",
        "executableInvariant": None,
        "formalCosetActionFaithful": True,
        "formalTargetLabel": TARGET["label"],
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "runbook": str(RUNBOOK.relative_to(ROOT)),
        "runbookSha256": runbook_hash,
        "source": source,
        "status": runbook["status"],
        "submissionCalls": 0,
        "target": {**TARGET, "state": target},
        "unprovedExecutionGates": runbook["unprovedExecutionGates"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--print-runbook", action="store_true")
    modes.add_argument("--validate-specialization", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preflight_only:
        event = preflight()
    elif args.print_runbook:
        event = build_runbook()
    else:
        payload = json.loads(args.validate_specialization.read_text())
        validate_specialization_certificate(payload)
        event = {
            "event": "index24_12043_reynolds_specialization_certificate_ok",
            "heavyArithmeticCalls": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        }
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

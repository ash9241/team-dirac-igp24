#!/usr/bin/env python3
"""Prepare the coefficient-free F6-A signed-subset action census.

This light planner imports neither Sage nor GAP.  It seals the accepted even
source receipts, the current safe structural routes, and every prior exact
negative before constructing the only tasks a heavy action worker may run.
The plan never contains source or candidate coefficient material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
STRUCTURAL = DATA / "agent_index24_structural_frontier.jsonl"
ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"
PAIR_ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
PAIR_SUMMARY = DATA / "agent_gold_c_lower_kummer_pair_product_summary.json"
SUBSET_ACTIONS_V2 = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
SUBSET_SUMMARY_V2 = DATA / "agent_gold_c_lower_kummer_subset_product_summary_v2.json"
CLOSED_12043 = DATA / "agent_index24_12043_subset_product_census.json"
DEFAULT_PLAN = DATA / "index24_f6a_kummer_action_plan.json"

EXPECTED_SHA256 = {
    STRUCTURAL: "ae7822cc8a9c2d3580b71b1338a34c43b4411631f6fd37266321ec60afcdb46b",
    ISOMORPHISM: "4b52472158c882b23ee585ab409eff9ced4871da41bb00f411c4f4b4af0ac82d",
    PAIR_ACTIONS: "6304b195a109317db89413300a51567c9f7c09c6227616591cf15fd344031f40",
    PAIR_SUMMARY: "efcc5effca0dad97471615e250214d1792c3b56f07c27ca1382ecd7e9638f143",
    SUBSET_ACTIONS_V2: "63e61cb820747f55aadd5145e033de82b4632e5ee82b5e24eb6298a4f34a267e",
    SUBSET_SUMMARY_V2: "24975d8462fc2aefda8e3512141a69099e81a2c32c127ada59bb387d84c1f908",
    CLOSED_12043: "07b1c3e1b2276d0692dba10121278bc3686cce14d2fd8a741cf5c59a9e2d5977",
}

# Every anchor is the shortest sealed accepted even polynomial for its pair.
# Only hashes and receipt coordinates may leave this light validation layer.
SOURCE_ANCHORS = {
    "24T5786": {
        "coefficientBytes": 92,
        "coefficientSha256": "1055df388f7368b63985cef64dd45eab8c40aa2a3c461d1c48b116f72e6b9b0b",
        "label": "24T5786",
        "order": 3072,
        "polynomialIndex": 86,
        "priority": 1,
        "r": 16,
        "submissionId": "sub_b6a0914a23c44e2f8f992a4d9aa09bb8",
        "t": 5786,
    },
    "24T11202": {
        "coefficientBytes": 108,
        "coefficientSha256": "4d8f80623fc0134b15810c8a685fec6bfbbff1f1c3c6c50e97d113ae554b0da3",
        "label": "24T11202",
        "order": 12288,
        "polynomialIndex": 422,
        "priority": 2,
        "r": 16,
        "submissionId": "sub_c983e77dbf4f42c68d72f3c665544ccb",
        "t": 11202,
    },
    "24T6436": {
        "coefficientBytes": 106,
        "coefficientSha256": "e6475e689a1615d23643d52e78cd505588ff509cfe529b74555080a545818079",
        "label": "24T6436",
        "order": 3072,
        "polynomialIndex": 366,
        "priority": 3,
        "r": 16,
        "submissionId": "sub_46223ca3866446a1809bbbd3cd1f1e74",
        "t": 6436,
    },
    "24T14141": {
        "coefficientBytes": 186,
        "coefficientSha256": "f7bd04a573d8a9c8ab7a41aef9c543b60d4ceebf055f4dfec970e32c98f77c5f",
        "label": "24T14141",
        "order": 36864,
        "polynomialIndex": 374,
        "priority": 4,
        "r": 20,
        "submissionId": "sub_9f3534b453e641faba6de187b1b66996",
        "t": 14141,
    },
    "24T14292": {
        "coefficientBytes": 204,
        "coefficientSha256": "c7b51dc9d53e95edc718c1f99cf61aeeedf451d729095f2b72be4cb374413a60",
        "label": "24T14292",
        "order": 36864,
        "polynomialIndex": 22,
        "priority": 5,
        "r": 16,
        "submissionId": "sub_143f4e76c56f4d1a9c53a0fa56e4c9ea",
        "t": 14292,
    },
    "24T10913": {
        "coefficientBytes": 189,
        "coefficientSha256": "9cbaf47180c52050839ab49fb1176bb96c7cda11025f13c0bcc0375a94b97653",
        "label": "24T10913",
        "order": 12288,
        "polynomialIndex": 860,
        "priority": 6,
        "r": 20,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "t": 10913,
    },
}

EXPECTED_SAFE_ROUTES = {
    (5786, 5653): {8, 16},
    (6436, 6910): {16},
    (10913, 10916): {20},
    (11202, 11204): {8, 16},
    (11202, 11566): {16},
    (14141, 14142): {16},
    (14292, 14142): {16},
}

REQUESTED_SUBSET_SIZES = tuple(range(1, 12))
TARGET_MAX_AGE_SECONDS = 6 * 60 * 60
HARD_MAX_SUBSET_UNIVERSE = math.comb(12, 6)  # 924
HARD_MAX_SYMMETRIC_POWER_DEGREE = 924
HARD_MAX_ACTION_WALL_SECONDS = 3600
HARD_MAX_BLOCK_SYSTEMS_PER_SOURCE = 64
HARD_MAX_ACTION_ROWS = 4096
HARD_MAX_PROMOTIONS = 64

FORBIDDEN_SERIALIZED_KEYS = {
    "candidateCoefficients",
    "coefficientLine",
    "coefficients",
    "originalLine",
    "original_line",
    "polynomialCoefficients",
}


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


def assert_coefficient_free(value) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_SERIALIZED_KEYS.intersection(value)
        if forbidden:
            raise ValueError("coefficient-bearing keys escaped: " + ", ".join(sorted(forbidden)))
        for child in value.values():
            assert_coefficient_free(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_coefficient_free(child)


def validate_source(connection: sqlite3.Connection, expected: dict) -> dict:
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
        (expected["submissionId"], expected["polynomialIndex"]),
    ).fetchone()
    if row is None:
        raise ValueError(f"sealed source receipt is missing for {expected['label']}")
    coefficients = [int(value.strip()) for value in str(row["coefficients"]).split(",")]
    original = [int(value.strip()) for value in str(row["original_line"]).split(",")]
    canonical_line = ",".join(str(value) for value in coefficients)
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or coefficients != original
        or any(coefficients[index] for index in range(1, 25, 2))
        or sha256_bytes(canonical_line.encode()) != expected["coefficientSha256"]
        or str(row["coefficient_hash"]) != expected["coefficientSha256"]
        or len(str(row["original_line"]).encode()) != expected["coefficientBytes"]
        or str(row["label"]) != expected["label"]
        or int(row["t"]) != expected["t"]
        or int(row["r"]) != expected["r"]
        or str(row["status"]) != "accepted"
        or int(row["scoreable"]) != 1
        or str(row["scoring_status"]) != "scoreable"
        or int(row["queued_count"]) != 0
        or int(row["failed_count"]) != 0
        or int(row["verified_count"]) != int(row[5])
    ):
        raise ValueError(f"sealed even source receipt changed for {expected['label']}")
    return {
        "coefficientBytes": expected["coefficientBytes"],
        "coefficientSha256": expected["coefficientSha256"],
        "createdAt": str(row["created_at"]),
        "fullyAdjudicated": True,
        "label": expected["label"],
        "order": expected["order"],
        "polynomialIndex": expected["polynomialIndex"],
        "priority": expected["priority"],
        "r": expected["r"],
        "submissionId": expected["submissionId"],
        "t": expected["t"],
        "updatedAt": str(row["updated_at"]),
        "verifiedCount": int(row["verified_count"]),
    }


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
    if row is None or row[3] is None:
        raise ValueError(f"target row or timestamp missing for {label}/r{r}")
    generated = parse_timestamp(str(row[3]))
    age = (datetime.now(timezone.utc) - generated).total_seconds()
    state = {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "fresh": -300 <= age <= TARGET_MAX_AGE_SECONDS,
        "generatedAt": str(row[3]),
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }
    if not (
        state["fresh"]
        and state["teamCount"] == 0
        and not state["discovered"]
        and state["minimumDiscAbs"] is None
        and not state["baseline"]
        and not state["owned"]
    ):
        raise ValueError(f"target is not fresh/live/nonbaseline/unowned: {label}/r{r}")
    return state


def validate_prior_negatives() -> list[dict]:
    closed = json.loads(CLOSED_12043.read_text())
    if (
        closed.get("status") != "certified_no_exact_subset_product_invariant"
        or int(closed.get("exactExecutableHitCount", -1)) != 0
        or closed.get("executableInvariant") is not None
        or set(closed.get("groupCertificate", {}).get("orbitSizeHistogramBySubsetSize", {}))
        != {str(value) for value in REQUESTED_SUBSET_SIZES}
    ):
        raise ValueError("24T12043 k=1..11 closure certificate changed")

    pair_summary = json.loads(PAIR_SUMMARY.read_text())
    pair_rows = [
        row for row in load_jsonl(PAIR_ACTIONS) if row.get("sourceLabel") == "24T10913"
    ]
    if (
        pair_summary.get("actionsSha256") != EXPECTED_SHA256[PAIR_ACTIONS]
        or len(pair_rows) != 1
        or pair_rows[0].get("targetLabel") == "24T10916"
    ):
        raise ValueError("24T10913 k=2 exact-negative certificate changed")

    subset_summary = json.loads(SUBSET_SUMMARY_V2.read_text())
    subset_rows = [
        row for row in load_jsonl(SUBSET_ACTIONS_V2) if row.get("sourceLabel") == "24T10913"
    ]
    if (
        subset_summary.get("actionsSha256") != EXPECTED_SHA256[SUBSET_ACTIONS_V2]
        or subset_summary.get("subsetSizes") != [3, 4, 5, 6]
        or subset_summary.get("failures") != []
        or not subset_rows
        or any(row.get("targetLabel") == "24T10916" for row in subset_rows)
    ):
        raise ValueError("24T10913 k=3..6 exact-negative certificate changed")

    exclusions = []
    for label in SOURCE_ANCHORS:
        exclusions.append(
            {
                "reason": "k=1 signed singleton action is the source action, not a sibling action",
                "sourceLabel": label,
                "subsetSize": 1,
                "type": "theorem_closed_identity",
            }
        )
    for subset_size in range(2, 7):
        exclusions.append(
            {
                "reason": (
                    "saved exhaustive pair-product census"
                    if subset_size == 2
                    else "saved exhaustive k=3..6 subset-product census"
                ),
                "sourceLabel": "24T10913",
                "subsetSize": subset_size,
                "type": "prior_exact_negative",
            }
        )
    for subset_size in REQUESTED_SUBSET_SIZES:
        exclusions.append(
            {
                "reason": "isolated complete k=1..11 certificate has zero exact 24T12067 hits",
                "sourceLabel": "24T12043",
                "subsetSize": subset_size,
                "type": "theorem_closed_source_route",
            }
        )
    return exclusions


def structural_routes(connection: sqlite3.Connection) -> list[dict]:
    structural_rows = load_jsonl(STRUCTURAL)
    isomorphism_rows = load_jsonl(ISOMORPHISM)
    routes = []
    for (source_t, target_t), expected_rs in sorted(EXPECTED_SAFE_ROUTES.items()):
        rows = [
            row
            for row in structural_rows
            if int(row.get("sourceT", -1)) == source_t
            and int(row.get("targetT", -1)) == target_t
            and int(row.get("sourceR", -1)) == SOURCE_ANCHORS[f"24T{source_t}"]["r"]
            and bool(row.get("allCompatibleClassesGold"))
        ]
        if not rows or any(row.get("executableInvariant") is not None for row in rows):
            raise ValueError(f"safe structural frontier changed for 24T{source_t}->24T{target_t}")
        mapped_rs = {int(value) for row in rows for value in row.get("mappedTargetR", [])}
        if mapped_rs != expected_rs:
            raise ValueError(f"mapped signatures changed for 24T{source_t}->24T{target_t}")
        subgroup_ids = sorted(
            {
                str(subgroup["subgroupClassIdentitySha256"])
                for row in rows
                for subgroup in row.get("subgroupClasses", [])
            }
        )

        certificates = [
            row
            for row in isomorphism_rows
            if int(row.get("sourceT", -1)) == source_t
            and int(row.get("targetT", -1)) == target_t
        ]
        if len(certificates) != 1 or certificates[0].get("status") != "certified_isomorphic":
            raise ValueError(f"isomorphism certificate changed for 24T{source_t}->24T{target_t}")
        certificate = certificates[0]
        if int(certificate.get("order", -1)) != SOURCE_ANCHORS[f"24T{source_t}"]["order"]:
            raise ValueError(f"source order changed for 24T{source_t}")
        allowed_profiles = set()
        for subgroup in certificate.get("subgroupClasses", []):
            if str(subgroup.get("subgroupClassIdentitySha256")) not in subgroup_ids:
                continue
            values = sorted(
                {
                    int(profile["targetR"])
                    for profile in subgroup.get("profiles", [])
                    if int(profile["sourceR"]) == SOURCE_ANCHORS[f"24T{source_t}"]["r"]
                }
            )
            if values:
                allowed_profiles.add(tuple(values))
        if not allowed_profiles or any(not set(profile).issubset(expected_rs) for profile in allowed_profiles):
            raise ValueError(f"unsafe target profile in 24T{source_t}->24T{target_t}")

        targets = []
        for target_r in sorted(expected_rs):
            label = f"24T{target_t}"
            targets.append({"label": label, "r": target_r, "state": target_state(connection, label, target_r)})
        routes.append(
            {
                "allowedSourceSignatureProfiles": [list(value) for value in sorted(allowed_profiles)],
                "safeTargetRs": sorted(expected_rs),
                "sealedSubgroupClassIdentitySha256": subgroup_ids,
                "sourceLabel": f"24T{source_t}",
                "sourceR": SOURCE_ANCHORS[f"24T{source_t}"]["r"],
                "sourceT": source_t,
                "targetLabel": f"24T{target_t}",
                "targetT": target_t,
                "targets": targets,
            }
        )
    return routes


def build_plan() -> dict:
    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"sealed input hash mismatch for {path.name}")
    exclusions = validate_prior_negatives()

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        sources = [
            validate_source(connection, source)
            for source in sorted(SOURCE_ANCHORS.values(), key=lambda row: row["priority"])
        ]
        routes = structural_routes(connection)
    finally:
        connection.close()

    excluded = {(row["sourceLabel"], int(row["subsetSize"])) for row in exclusions}
    routes_by_source = {}
    for route in routes:
        routes_by_source.setdefault(route["sourceLabel"], []).append(route)
    tasks = []
    for source in sources:
        for subset_size in REQUESTED_SUBSET_SIZES:
            if (source["label"], subset_size) in excluded:
                continue
            degree = math.comb(12, subset_size)
            if degree > HARD_MAX_SYMMETRIC_POWER_DEGREE:
                raise ValueError("planned symmetric-power degree exceeds the hard gate")
            tasks.append(
                {
                    "factorEnumerationDegree": degree,
                    "inducedActionDegree": 24,
                    "quotientOrbitLengthRequired": 12,
                    "sourceLabel": source["label"],
                    "sourceOrder": source["order"],
                    "sourceR": source["r"],
                    "sourceT": source["t"],
                    "subsetSize": subset_size,
                    "targetActions": sorted(
                        routes_by_source[source["label"]], key=lambda row: row["targetT"]
                    ),
                    "taskId": f"{source['label']}_k{subset_size:02d}",
                }
            )

    plan = {
        "actionOnly": True,
        "arithmeticAuthorizationRule": (
            "No polynomial arithmetic is authorized unless the signed induced action is faithful, "
            "has exact TransitiveIdentification equal to a listed target, and its exact source-r "
            "profile equals a listed safe profile."
        ),
        "coefficientMaterialIncluded": False,
        "dispatcherContractAfterExactActionHit": {
            "factorSelection": (
                "Factor every degree-12 component of q.symmetric_power(k); do not select a factor "
                "from an abstract numbering of roots."
            ),
            "finalExactGates": [
                "symmetric power is squarefree or repeated components are explicitly rejected",
                "selected factor is monic irreducible of degree 12",
                "factor(x^2) is monic irreducible of degree 24",
                "exact Galois label equals the promoted target label",
                "exact real-root signature is one of the promoted live target signatures",
                "mutable target state and source receipt are rechecked immediately before staging",
            ],
            "rootActionAlignment": "exhaustive exact factor enumeration; no unproved root numbering",
            "specializationFailurePolicy": "discard the route; never relax the exact label/signature gates",
        },
        "exclusions": exclusions,
        "family": "F6-A_SIGNED_TWO_BLOCK_KUMMER_ACTION_DISPATCHER",
        "hardResourceGates": {
            "maxActionRows": HARD_MAX_ACTION_ROWS,
            "maxActionWallSeconds": HARD_MAX_ACTION_WALL_SECONDS,
            "maxBlockSystemsPerSource": HARD_MAX_BLOCK_SYSTEMS_PER_SOURCE,
            "maxPromotions": HARD_MAX_PROMOTIONS,
            "maxSubsetUniverse": HARD_MAX_SUBSET_UNIVERSE,
            "maxSymmetricPowerDegree": HARD_MAX_SYMMETRIC_POWER_DEGREE,
            "oneHeavyWorker": True,
        },
        "heavyArithmeticCalls": 0,
        "inputSha256": input_hashes,
        "networkCalls": 0,
        "requestedSubsetSizes": list(REQUESTED_SUBSET_SIZES),
        "schemaVersion": "index24-f6a-kummer-action-plan-v1",
        "sources": sources,
        "structuralRoutes": routes,
        "submissionCalls": 0,
        "tasks": tasks,
    }
    if len(tasks) != 55 or len(exclusions) != 22:
        raise ArithmeticError("fresh-task or exact-exclusion count changed")
    assert_coefficient_free(plan)
    return plan


def publish_no_overwrite(path: Path, payload: bytes) -> str:
    if path.resolve().parent != DATA.resolve():
        raise ValueError("plan output escaped the data directory")
    temporary = Path(str(path) + ".tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite plan output: {path}")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight-only", action="store_true")
    modes.add_argument("--write-plan", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_PLAN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = build_plan()
    rendered = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
    event = {
        "actionOnly": True,
        "coefficientMaterialIncluded": False,
        "event": "index24_f6a_kummer_action_preflight_ok",
        "excludedTasks": len(plan["exclusions"]),
        "freshTasks": len(plan["tasks"]),
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "planSha256": sha256_bytes(rendered),
        "submissionCalls": 0,
        "writePlan": bool(args.write_plan),
    }
    if args.write_plan:
        event["artifact"] = str(args.output.relative_to(ROOT))
        event["artifactSha256"] = publish_no_overwrite(args.output, rendered)
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Seal the exact upstream-lineage audit for the v12 conditional pair routes.

The v12 census exposes thirteen conditional routes.  Every source polynomial
was itself produced by an exact, faithful unordered-pair construction.  This
audit reuses those pinned candidate/Frobenius packets to recover either the
reverse source action or an already-resolved sibling action.  The one
two-generation case (24T3782/r0 -> 24T3518) is resolved by an exact GAP class
incidence calculation; no polynomial arithmetic is repeated.

The script is deliberately offline.  It opens the ledger read-only, never
prints a coefficient line, and writes only its isolated certificate/summary.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
V12 = DATA / "autopilot_pair_delta_20260722_v12"
CENSUS = V12 / "missing_pair_all.jsonl"
INVENTORY = V12 / "exact_source_inventory.json"
CERTIFICATE = DATA / "v12_route_lineage_certificate.json"
SUMMARY = DATA / "v12_route_lineage_summary.json"

FROBENIUS_METHOD = "exact-unramified-frobenius-cycle-type-exclusion-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: dict) -> str:
    return sha256_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def rooted(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def relative(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": sha256_path(path),
    }


def fixed_points(permutation) -> int:
    return 24 - int(libgap.NrMovedPoints(permutation))


# Each entry identifies the v12 route and the exact upstream packet that
# generated its source.  ``sibling`` means that the target assignment is
# already present in the same packet.  ``reverse`` means the target is the
# packet's faithful source action, so its exact signature is sourceR.
ROUTES = [
    {
        "source": ("24T2016", 0),
        "target": "24T2030",
        "kind": "sibling",
        "resultR": 8,
        "candidates": DATA / "agent_index24_ambiguous_pair_multi_packets.jsonl",
        "frobenius": DATA / "agent_index24_ambiguous_pair_multi_frobenius_certificate.json",
        "upstream": ("sub_9ff8dd684fc54c65b2e93099fcad2098", 2, "24T2053", 0),
    },
    {
        "source": ("24T3782", 0),
        "target": "24T3518",
        "kind": "two_generation_lineage",
        "resultR": 4,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v3/ambiguous_low/source_4185_r0_candidates.jsonl",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v3/ambiguous_low/source_4185_r0_frobenius.json",
        "upstream": ("sub_4c9897bb39cb4f6da6077a5b128f0404", 4, "24T4185", 0),
    },
    {
        "source": ("24T3919", 0),
        "target": "24T3647",
        "kind": "sibling",
        "resultR": 4,
        "candidates": DATA / "autopilot_pair_sum_multi_ranked_v3.jsonl",
        "frobenius": DATA / "autopilot_pair_sum_multi_ranked_v3_frobenius.json",
        "upstream": ("sub_52f4f6c690134c53884050f05649efd7", 2, "24T3110", 0),
    },
    {
        "source": ("24T3919", 0),
        "target": "24T3110",
        "kind": "reverse",
        "resultR": 0,
        "candidates": DATA / "autopilot_pair_sum_multi_ranked_v3.jsonl",
        "frobenius": DATA / "autopilot_pair_sum_multi_ranked_v3_frobenius.json",
        "upstream": ("sub_52f4f6c690134c53884050f05649efd7", 2, "24T3110", 0),
    },
    {
        "source": ("24T6640", 8),
        "target": "24T5614",
        "kind": "reverse",
        "resultR": 16,
        "candidates": V12.parent / "autopilot_pair_delta_20260721_v1/ambiguous_multi_results.jsonl",
        "frobenius": V12.parent / "autopilot_pair_delta_20260721_v1/ambiguous_multi_frobenius.json",
        "upstream": ("sub_79fd1b3277b54d5c849f0a624adf535b", 4, "24T5614", 16),
    },
    {
        "source": ("24T8354", 4),
        "target": "24T8893",
        "kind": "sibling",
        "resultR": 8,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v10/ambiguous_8535_multi_candidates.jsonl",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v10/ambiguous_8535_frobenius.json",
        "upstream": ("sub_9a4fa0ffea6e4568bcb57f90f289adda", 3, "24T8535", 0),
    },
    {
        "source": ("24T8525", 0),
        "target": "24T8093",
        "kind": "sibling",
        "resultR": 0,
        "candidates": DATA / "autopilot_pair_sum_multi_ranked_v3.jsonl",
        "frobenius": DATA / "autopilot_pair_sum_multi_ranked_v3_frobenius.json",
        "upstream": ("sub_e7403e62ecc24a44868be58d47603312", 68, "24T8525", 8),
    },
    {
        "source": ("24T11749", 8),
        "target": "24T11216",
        "kind": "reverse",
        "resultR": 8,
        "candidates": DATA / "autopilot_pair_sum_multi_ranked_v3.jsonl",
        "frobenius": DATA / "autopilot_pair_sum_multi_ranked_v3_frobenius.json",
        "upstream": ("sub_5367012c47ca415aa739a1a5076bd721", 29, "24T11216", 8),
    },
    {
        "source": ("24T15250", 8),
        "target": "24T15130",
        "kind": "reverse",
        "resultR": 8,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v4/ambiguous_15130_to_15250/source_15130_r8_candidates.json",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v4/ambiguous_15130_to_15250/source_15130_r8_frobenius.json",
        "upstream": ("sub_a2e634ebb04346b5baf3bc24a7fdcd8f", 11, "24T15130", 8),
    },
    {
        "source": ("24T15756", 8),
        "target": "24T14924",
        "kind": "reverse",
        "resultR": 8,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v5/pilots/source_14924_r8_candidates.json",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v5/pilots/source_14924_r8_frobenius.json",
        "upstream": ("sub_2ad5382afecd45588ee6a6c0718e1180", 0, "24T14924", 8),
    },
    {
        "source": ("24T16947", 8),
        "target": "24T17398",
        "kind": "reverse",
        "resultR": 8,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v4/ambiguous_17398_to_16947/source_17398_r8_candidates.json",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v4/ambiguous_17398_to_16947/source_17398_r8_frobenius.json",
        "upstream": ("sub_a2e634ebb04346b5baf3bc24a7fdcd8f", 18, "24T17398", 8),
    },
    {
        "source": ("24T16965", 8),
        "target": "24T17499",
        "kind": "reverse",
        "resultR": 8,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v4/ambiguous_17499_to_16965/source_17499_r8_candidates.json",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v4/ambiguous_17499_to_16965/source_17499_r8_frobenius.json",
        "upstream": ("sub_a2e634ebb04346b5baf3bc24a7fdcd8f", 19, "24T17499", 8),
    },
    {
        "source": ("24T17244", 16),
        "target": "24T16972",
        "kind": "reverse",
        "resultR": 16,
        "candidates": V12.parent / "autopilot_pair_delta_20260722_v3/ambiguous_16972_to_17704/source_16972_r16_candidates.json",
        "frobenius": V12.parent / "autopilot_pair_delta_20260722_v3/ambiguous_16972_to_17704/source_16972_r16_frobenius.json",
        "upstream": ("sub_a8c78b3c4fd04a3da25433d17efcfd88", 14, "24T16972", 16),
    },
]


def load_candidate_rows(path: Path) -> list[dict]:
    # Some isolated singleton packets use a JSON object rather than JSONL.
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    if text.startswith("{") and "\n" not in text:
        return [json.loads(text)]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def validate_candidate_row(row: dict) -> dict[str, dict]:
    if (
        row.get("status") != "certified_multi"
        or int(row.get("workerExitCode", 0)) != 0
        or int(row.get("networkCalls", 0)) != 0
        or int(row.get("submissionCalls", 0)) != 0
        or int(row.get("ledgerWrites", 0)) != 0
    ):
        raise ValueError("upstream candidate row is not an exact offline packet")
    orbit = row.get("orbitCertificate") or {}
    if (
        orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or not orbit.get("exponents")
        or any(int(value) != 1 for value in orbit["exponents"])
    ):
        raise ValueError("upstream factorization certificate is not squarefree/exact")
    candidates = {}
    for candidate in row.get("candidates") or []:
        line = str(candidate.get("coefficientLine"))
        digest = str(candidate.get("coefficientSha256"))
        if sha256_bytes(line.encode()) != digest:
            raise ValueError("candidate coefficient hash mismatch")
        if digest in candidates:
            raise ValueError("duplicate candidate coefficient hash")
        candidates[digest] = candidate
    if not candidates:
        raise ValueError("candidate packet has no degree-24 candidates")
    return candidates


def find_packet(spec: dict, source_hash: str) -> tuple[dict, dict, dict, dict]:
    candidates_path = Path(spec["candidates"]).resolve()
    frobenius_path = Path(spec["frobenius"]).resolve()
    source_submission, source_index, source_label, source_r = spec["upstream"]
    rows = [
        row
        for row in load_candidate_rows(candidates_path)
        if str(row.get("sourceSubmissionId")) == source_submission
        and int(row.get("sourcePolynomialIndex", -1)) == source_index
        and str(row.get("sourceLabel")) == source_label
        and int(row.get("sourceR", -1)) == source_r
    ]
    if len(rows) != 1:
        raise ValueError(f"candidate source row is not unique: {spec['source']}")
    candidate_row = rows[0]
    candidates = validate_candidate_row(candidate_row)

    proof = read_json(frobenius_path)
    if (
        proof.get("method") != FROBENIUS_METHOD
        or rooted(str(proof.get("input"))) != candidates_path
        or proof.get("inputSha256") != sha256_path(candidates_path)
        or proof.get("selectedInputRowsSha256") != sha256_path(candidates_path)
    ):
        raise ValueError(f"Frobenius envelope mismatch: {frobenius_path}")
    proof_rows = [
        row
        for row in proof.get("rows") or []
        if str(row.get("sourceSubmissionId")) == source_submission
        and int(row.get("sourcePolynomialIndex", -1)) == source_index
        and str(row.get("sourceLabel")) == source_label
        and int(row.get("sourceR", -1)) == source_r
    ]
    if len(proof_rows) != 1 or proof_rows[0].get("status") != "resolved":
        raise ValueError(f"resolved Frobenius row is not unique: {spec['source']}")
    proof_row = proof_rows[0]
    assignments = proof_row.get("assignments") or []
    current = [
        assignment
        for assignment in assignments
        if assignment.get("coefficientSha256") == source_hash
        and assignment.get("targetLabel") == spec["source"][0]
        and int(assignment.get("targetR", -1)) == spec["source"][1]
    ]
    if len(current) != 1 or source_hash not in candidates:
        raise ValueError(f"current v12 source is not pinned by upstream proof: {spec['source']}")
    current_assignment = current[0]
    if int(current_assignment.get("factorIndex", -1)) != int(
        candidates[source_hash].get("factorIndex", -2)
    ):
        raise ValueError("current assignment factor index mismatch")
    return candidate_row, proof_row, candidates, current_assignment


def validate_census(rows: list[dict]) -> dict[tuple[str, int, str, int], tuple[dict, dict]]:
    routes = {}
    for row in rows:
        base = {key: value for key, value in row.items() if key != "exactCertificateSha256"}
        if (
            row.get("status") != "certified"
            or canonical_sha256(base) != row.get("exactCertificateSha256")
        ):
            raise ValueError(f"v12 census row is not exact: {row.get('sourceLabel')}")
        targets = {int(target["orbitIndex"]): target for target in row.get("targets") or []}
        for route in row.get("routes") or []:
            key = (
                str(row["sourceLabel"]),
                int(route["sourceR"]),
                str(route["targetLabel"]),
                int(route["orbitIndex"]),
            )
            if key in routes:
                raise ValueError(f"duplicate v12 route: {key}")
            target = targets.get(key[3])
            if (
                target is None
                or target.get("targetLabel") != key[2]
                or int(target.get("kernelOrder", -1)) != 1
            ):
                raise ValueError(f"v12 route target is not faithful: {key}")
            routes[key] = (row, route)
    if len(routes) != 13:
        raise ValueError(f"expected 13 v12 routes, found {len(routes)}")
    return routes


def lineage_3782(candidate_row: dict, proof_row: dict) -> dict:
    # The 4185/r0 packet has three faithful 3782 children with exact signature
    # multiset {0,4,8}; the v12 source is its r0 child.  Enumerate every
    # compatible involution class and every possible child-orbit alignment.
    exact_child_rs = sorted(
        int(assignment["targetR"])
        for assignment in proof_row.get("assignments") or []
        if assignment.get("targetLabel") == "24T3782"
    )
    if exact_child_rs != [0, 4, 8]:
        raise ValueError("4185/r0 exact child-signature multiset changed")
    orbit_targets = candidate_row.get("orbitTargets") or []
    if (
        len(orbit_targets) != 3
        or any(target.get("targetLabel") != "24T3782" for target in orbit_targets)
        or any(int(target.get("kernelOrder", -1)) != 1 for target in orbit_targets)
    ):
        raise ValueError("4185/r0 child actions are not exactly three faithful 3782 orbits")

    group = libgap.TransitiveGroup(24, 4185)
    pairs = libgap.Combinations(libgap.eval("[1..24]"), 2)
    child_actions = []
    for orbit_index, orbit in enumerate(libgap.Orbits(group, pairs, libgap.OnSets)):
        if int(libgap.Length(orbit)) != 24:
            continue
        action = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
        image = libgap.Image(action)
        child_actions.append(
            {
                "action": action,
                "image": image,
                "kernelOrder": int(libgap.Size(libgap.Kernel(action))),
                "orbitIndex": orbit_index,
                "targetT": int(libgap.TransitiveIdentification(image)),
            }
        )
    if (
        len(child_actions) != 3
        or any(child["targetT"] != 3782 for child in child_actions)
        or any(child["kernelOrder"] != 1 for child in child_actions)
    ):
        raise ValueError("standard 4185 action does not have the pinned faithful children")

    compatible = []
    for class_index, conjugacy_class in enumerate(libgap.ConjugacyClasses(group)):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) not in (1, 2):
            continue
        if fixed_points(representative) != 0:
            continue
        child_rs = [
            fixed_points(libgap.Image(child["action"], representative))
            for child in child_actions
        ]
        if sorted(child_rs) != exact_child_rs:
            continue
        for child, child_r in zip(child_actions, child_rs, strict=True):
            if child_r != 0:
                continue
            image_element = libgap.Image(child["action"], representative)
            grandchildren = []
            for orbit_index, orbit in enumerate(
                libgap.Orbits(child["image"], pairs, libgap.OnSets)
            ):
                if int(libgap.Length(orbit)) != 24:
                    continue
                action = libgap.ActionHomomorphism(
                    child["image"], orbit, libgap.OnSets
                )
                image = libgap.Image(action)
                grandchildren.append(
                    {
                        "kernelOrder": int(libgap.Size(libgap.Kernel(action))),
                        "orbitIndex": orbit_index,
                        "targetR": fixed_points(libgap.Image(action, image_element)),
                        "targetT": int(libgap.TransitiveIdentification(image)),
                    }
                )
            target_3518 = [row for row in grandchildren if row["targetT"] == 3518]
            if (
                Counter(row["targetT"] for row in grandchildren)
                != Counter({3518: 1, 4185: 2})
                or len(target_3518) != 1
                or any(row["kernelOrder"] != 1 for row in grandchildren)
            ):
                raise ValueError("nested 3782 action does not have the pinned faithful targets")
            compatible.append(
                {
                    "classIndex": class_index,
                    "classSize": int(libgap.Size(conjugacy_class)),
                    "source4185R": 0,
                    "child3782OrbitIndex": int(child["orbitIndex"]),
                    "child3782R": 0,
                    "childSignatureMultiset": sorted(child_rs),
                    "target3518OrbitIndex": int(target_3518[0]["orbitIndex"]),
                    "target3518R": int(target_3518[0]["targetR"]),
                }
            )
    if len(compatible) != 3 or {row["target3518R"] for row in compatible} != {4}:
        raise ValueError("two-generation 3782 lineage is not uniquely r4")
    return {
        "method": "exact-faithful-two-generation-class-incidence-v1",
        "upstreamSource": "24T4185/r0",
        "exactChildSignatureMultiset": exact_child_rs,
        "compatibleClassChildAlignments": compatible,
        "compatibleAlignmentCount": len(compatible),
        "forcedTarget": "24T3518/r4",
        "forcedTargetR": 4,
    }


def main() -> int:
    census_rows = read_jsonl(CENSUS)
    census_routes = validate_census(census_rows)
    inventory = read_json(INVENTORY)
    if inventory.get("schemaVersion") != "v12-exact-source-inventory-v1":
        raise ValueError("v12 exact source inventory schema mismatch")
    selected = inventory.get("selectedConstructionRows") or []
    inventory_rows = {
        (str(row["pair"]).split(":")[0], int(str(row["pair"]).split(":")[1])): row
        for row in selected
    }

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    audit_rows = []
    used_route_keys = set()
    lineage_proof = None
    for spec in ROUTES:
        source_label, source_r = spec["source"]
        inventory_row = inventory_rows.get((source_label, source_r))
        if inventory_row is None:
            raise ValueError(f"v12 source absent from exact inventory: {spec['source']}")
        submission_id = str(inventory_row["submissionId"])
        polynomial_index = int(inventory_row["polynomialIndex"])
        source_hash = str(inventory_row["coefficientSha256"])
        ledger = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
            "v.scoring_status FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
            "AND p.polynomial_index=?",
            (submission_id, polynomial_index),
        ).fetchone()
        if ledger != (
            source_hash,
            "accepted",
            source_label,
            source_r,
            1,
            0,
            "scoreable",
        ):
            raise ValueError(f"ledger source anchor mismatch: {spec['source']}")

        matching_route_keys = [
            key
            for key in census_routes
            if key[0] == source_label and key[1] == source_r and key[2] == spec["target"]
        ]
        if len(matching_route_keys) != 1:
            raise ValueError(f"conditional route is not unique: {spec['source']}->{spec['target']}")
        route_key = matching_route_keys[0]
        if route_key in used_route_keys:
            raise ValueError(f"route used twice: {route_key}")
        used_route_keys.add(route_key)
        census_row, route = census_routes[route_key]
        candidate_row, proof_row, candidates, current_assignment = find_packet(
            spec, source_hash
        )

        if spec["kind"] == "sibling":
            target_assignments = [
                assignment
                for assignment in proof_row.get("assignments") or []
                if assignment.get("targetLabel") == spec["target"]
                and int(assignment.get("targetR", -1)) == int(spec["resultR"])
            ]
            if len(target_assignments) != 1:
                raise ValueError(f"sibling target assignment is not unique: {route_key}")
            target_assignment = target_assignments[0]
            target_hash = str(target_assignment.get("coefficientSha256"))
            if (
                target_hash not in candidates
                or int(target_assignment.get("factorIndex", -1))
                != int(candidates[target_hash].get("factorIndex", -2))
            ):
                raise ValueError("sibling target assignment/candidate mismatch")
            exact_evidence = {
                "assignmentFactorIndex": int(target_assignment["factorIndex"]),
                "assignmentCoefficientSha256": target_hash,
            }
        elif spec["kind"] == "reverse":
            if (
                proof_row.get("sourceLabel") != spec["target"]
                or int(proof_row.get("sourceR", -1)) != int(spec["resultR"])
                or int((census_row.get("targetCounts") or {}).get(spec["target"], 0)) != 1
            ):
                raise ValueError(f"reverse action is not unique/exact: {route_key}")
            exact_evidence = {
                "faithfulUpstreamSourceLabel": str(proof_row["sourceLabel"]),
                "faithfulUpstreamSourceR": int(proof_row["sourceR"]),
            }
        elif spec["kind"] == "two_generation_lineage":
            lineage_proof = lineage_3782(candidate_row, proof_row)
            if int(lineage_proof["forcedTargetR"]) != int(spec["resultR"]):
                raise ValueError("two-generation forced signature changed")
            exact_evidence = lineage_proof
        else:
            raise ValueError(f"unknown route proof kind: {spec['kind']}")

        result_r = int(spec["resultR"])
        gold_r = sorted(int(value) for value in route.get("goldR") or [])
        if result_r in gold_r:
            raise ValueError(f"lineage unexpectedly hits a v12 gold route: {route_key}")
        audit_rows.append(
            {
                "source": {
                    "label": source_label,
                    "r": source_r,
                    "submissionId": submission_id,
                    "polynomialIndex": polynomial_index,
                    "coefficientSha256": source_hash,
                    "ledgerStatus": "accepted_scoreable_nonbaseline",
                },
                "route": {
                    "orbitIndex": int(route["orbitIndex"]),
                    "targetLabel": spec["target"],
                    "mappedTargetR": sorted(int(value) for value in route["mappedTargetR"]),
                    "goldR": gold_r,
                },
                "proofKind": spec["kind"],
                "upstream": {
                    "submissionId": str(proof_row["sourceSubmissionId"]),
                    "polynomialIndex": int(proof_row["sourcePolynomialIndex"]),
                    "label": str(proof_row["sourceLabel"]),
                    "r": int(proof_row["sourceR"]),
                    "currentAssignmentFactorIndex": int(current_assignment["factorIndex"]),
                    "candidates": relative(Path(spec["candidates"])),
                    "frobenius": relative(Path(spec["frobenius"])),
                },
                "exactEvidence": exact_evidence,
                "exactTargetR": result_r,
                "exactTargetPair": f"{spec['target']}/r{result_r}",
                "outcome": "miss_not_in_frozen_gold_set",
            }
        )
    connection.close()

    if used_route_keys != set(census_routes):
        missing = sorted(set(census_routes) - used_route_keys)
        raise ValueError(f"not every v12 route was audited: {missing}")
    if len(audit_rows) != 13 or any(row["outcome"] != "miss_not_in_frozen_gold_set" for row in audit_rows):
        raise ValueError("v12 route audit did not close exactly 13 misses")

    certificate = {
        "schemaVersion": "v12-exact-route-lineage-certificate-v1",
        "method": "exact-upstream-pair-frobenius-reverse-sibling-lineage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "all_conditional_routes_closed_no_hit",
        "inputs": {
            "census": relative(CENSUS),
            "exactSourceInventory": relative(INVENTORY),
            "ledger": {"path": str(DB.relative_to(ROOT)), "mode": "read_only"},
            "auditScript": relative(Path(__file__)),
        },
        "counts": {
            "conditionalRoutes": 13,
            "reverseRoutes": sum(row["proofKind"] == "reverse" for row in audit_rows),
            "siblingRoutes": sum(row["proofKind"] == "sibling" for row in audit_rows),
            "twoGenerationLineageRoutes": sum(
                row["proofKind"] == "two_generation_lineage" for row in audit_rows
            ),
            "exactHits": 0,
            "exactMisses": 13,
            "polynomialArithmeticRuns": 0,
            "heavyWorkers": 0,
        },
        "checks": {
            "allCensusCertificatesRehashed": True,
            "allRouteTargetsFaithful": True,
            "allSourceReceiptIndexHashesLedgerPinned": True,
            "allUpstreamCandidatePacketsSquarefree": True,
            "allUpstreamFrobeniusRowsResolved": True,
            "allReverseActionsUnique": True,
            "allSiblingAssignmentsExact": True,
            "twoGenerationLineageForced": True,
            "noFrozenGoldIntersection": True,
        },
        "routes": audit_rows,
        "twoGenerationLineage": lineage_proof,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "polynomialArithmeticRuns": 0,
        },
    }
    atomic_json(CERTIFICATE, certificate)
    summary = {
        "schemaVersion": "v12-exact-route-lineage-summary-v1",
        "status": "closed_no_manifest",
        "certificate": relative(CERTIFICATE),
        "conditionalRoutes": 13,
        "exactHits": 0,
        "exactMisses": 13,
        "manifest": None,
        "heavyWorkerUsed": False,
        "sideEffects": certificate["sideEffects"],
    }
    atomic_json(SUMMARY, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "routes": 13,
                "exactHits": 0,
                "exactMisses": 13,
                "heavyWorkerUsed": False,
                "certificate": str(CERTIFICATE.relative_to(ROOT)),
                "summary": str(SUMMARY.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

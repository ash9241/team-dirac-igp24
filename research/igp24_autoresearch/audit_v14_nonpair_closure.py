#!/usr/bin/env python3
"""Seal the light-only non-pair closure for the 14 v14 source signatures.

The audit only joins exact cached certificates and current local/receipt
exclusions.  It never imports Sage/GAP, performs polynomial factorization,
calls the network, changes the ledger, or submits.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
V14 = DATA / "autopilot_pair_delta_20260722_v14"
INVENTORY = V14 / "exact_source_inventory.json"
CENSUS = V14 / "missing_pair_all.jsonl"
OUTPUT = DATA / "v14_nonpair_closure_certificate.json"
SUMMARY = DATA / "v14_nonpair_closure_summary.json"

REJOIN_CERT = DATA / "v14_postverification_rejoin_11118_r12_certificate.json"
REJOIN_RECEIPT = RECEIPTS / "sub_59f9b8ae82874a40b2e6c1c39d6c85fd.json"
TWIST_CERT = DATA / "v14_negative_twist_15578_r4_certificate.json"
TWIST_RECEIPT = RECEIPTS / "sub_077547629a4e41c490ddebe2d0cf84c4.json"

NEGATIVE_AUDIT = DATA / "agent_f7_negative_twist_signature_audit.jsonl"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
LOWER_HISTORICAL = DATA / "agent_gold_c_lower_kummer_historical_census.jsonl"
LOWER_PAIR_ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
LOWER_PAIR_ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
LOWER_SUBSET_ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
LOWER_SUBSET_ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes_v2.jsonl"
HIGHER_RESULTS = DATA / "agent_f9_higher_kummer_triple_pilot_results.jsonl"
CHARACTER = DATA / "agent_gold_c_character_census_alignment_results.jsonl"
TRIPLES = DATA / "agent_gold_a_triple_current_profiles.jsonl"
ORDERED_SHARDS = tuple(DATA / f"agent_index24_ordered_pair_shard{i}.jsonl" for i in range(6))
ORDERED_CEILING = DATA / "agent_index24_ordered_pair_route_ceiling.json"
COMPOSITA = DATA / "agent_non12_simple_compositum_pilot.json"
ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"

COEFFICIENT_RE = re.compile(r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def artifact(path: Path) -> dict:
    return {"path": display(path), "sha256": sha256_path(path)}


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_new(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    text = payload.decode("utf-8")
    if COEFFICIENT_RE.search(text):
        raise ValueError("coefficient payload leaked into audit output")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite sealed output: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def nested_source_pair(row: dict) -> tuple[str, int] | None:
    for value in (row, row.get("source") or {}, row.get("candidate") or {}):
        label = value.get("sourceLabel", value.get("label"))
        r_value = value.get("sourceR", value.get("r"))
        if isinstance(r_value, int) and isinstance(label, str):
            return label, int(r_value)
    return None


def nested_source_hashes(row: dict) -> set[str]:
    result = set()
    stack = [row]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("sourceCoefficientSha256", "coefficientSha256") and isinstance(child, str):
                    result.add(child)
                elif isinstance(child, (dict, list)):
                    stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)
    return result


def validate_committed_certificate(certificate_path: Path, receipt_path: Path) -> dict:
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest = certificate.get("manifest") or {}
    response = receipt.get("response") or {}
    if (
        certificate.get("coefficientMaterialIncluded") is not False
        or not str(certificate.get("status", "")).startswith("certified_exact_safe")
        or not bool(receipt.get("commit"))
        or int(receipt.get("polynomials", -1)) != int(manifest.get("polynomials", -2))
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or str(receipt.get("manifestHash")) != str(manifest.get("sha256"))
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
        or not str(response.get("submissionId", "")).startswith("sub_")
    ):
        raise ValueError(f"committed exact certificate/receipt mismatch: {certificate_path}")
    return {
        "certificate": artifact(certificate_path),
        "manifestSha256": str(manifest["sha256"]),
        "polynomials": int(manifest["polynomials"]),
        "receipt": artifact(receipt_path),
        "submissionId": str(response["submissionId"]),
    }


def main() -> int:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    delta_pairs = {
        (text.split(":", 1)[0], int(text.split(":", 1)[1]))
        for text in inventory.get("deltaPairs") or []
    }
    anchors_by_pair = inventory.get("acceptedAnchorsByPair") or {}
    anchor_hashes = {
        str(anchor["coefficientSha256"])
        for rows in anchors_by_pair.values()
        for anchor in rows
    }
    census_rows = read_jsonl(CENSUS)
    census_pairs = {
        (str(row["sourceLabel"]), int(r))
        for row in census_rows
        for r in row.get("sourceR") or []
    }
    if (
        inventory.get("status") != "certified"
        or inventory.get("coefficientMaterialIncluded") is not False
        or len(delta_pairs) != 14
        or len(anchor_hashes) != 15
        or census_pairs != delta_pairs
        or len(census_rows) != 13
        or any(row.get("status") != "certified" for row in census_rows)
    ):
        raise ValueError("v14 exact source boundary is incomplete")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for text, anchors in anchors_by_pair.items():
            label, raw_r = text.split(":", 1)
            r_value = int(raw_r)
            for anchor in anchors:
                row = connection.execute(
                    "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,"
                    "v.in_baseline,v.scoring_status FROM polynomials p JOIN verifications v "
                    "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
                    "AND p.polynomial_index=?",
                    (str(anchor["submissionId"]), int(anchor["polynomialIndex"])),
                ).fetchone()
                if row is None or (
                    str(row["coefficient_hash"]) != str(anchor["coefficientSha256"])
                    or str(row["status"]) != "accepted"
                    or str(row["label"]) != label
                    or int(row["r"]) != r_value
                    or int(row["scoreable"] or 0) != 1
                    or int(row["in_baseline"] or 0) != 0
                    or str(row["scoring_status"]) != "scoreable"
                ):
                    raise ValueError(f"v14 anchor is not accepted-scoreable: {text}")

        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        locally_known = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE label IS NOT NULL AND r IS NOT NULL"
            )
        }
        ledger_hashes = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, {}
        )

        rejoin = validate_committed_certificate(REJOIN_CERT, REJOIN_RECEIPT)
        twist = validate_committed_certificate(TWIST_CERT, TWIST_RECEIPT)
        committed_hashes = {rejoin["manifestSha256"], twist["manifestSha256"]}
        if not committed_hashes <= {
            str(json.loads(path.read_text(encoding="utf-8")).get("manifestHash"))
            for path in (REJOIN_RECEIPT, TWIST_RECEIPT)
        }:
            raise ValueError("post-v14 committed manifests are not receipt-pinned")

        # Every other unsubmitted factor in the eight rejoined packets is now
        # exactly forced onto one of these locally owned pairs.
        closed_sibling_pairs = {
            ("24T1559", 12),
            ("24T3052", 12),
            ("24T6885", 16),
            ("24T6703", 16),
            ("24T11084", 8),
            ("24T10495", 12),
            ("24T15712", 8),
        }
        if not closed_sibling_pairs <= owned:
            raise ValueError("one exact rejoin sibling is no longer locally owned")

        negative_rows = read_jsonl(NEGATIVE_AUDIT)
        cached_negative_direct = [
            row
            for row in negative_rows
            if str(row.get("sourceCoefficientSha256")) in anchor_hashes
        ]
        action_rows = read_jsonl(ACTION_MAP)
        action_labels = {str(row["sourceLabel"]) for row in action_rows}
        even_anchor_pairs = set()
        for text, anchors in anchors_by_pair.items():
            label, raw_r = text.split(":", 1)
            for anchor in anchors:
                coefficients = connection.execute(
                    "SELECT coefficients FROM polynomials WHERE submission_id=? "
                    "AND polynomial_index=?",
                    (str(anchor["submissionId"]), int(anchor["polynomialIndex"])),
                ).fetchone()[0]
                values = [int(value) for value in str(coefficients).split(",")]
                if all(values[index] == 0 for index in range(1, 25, 2)):
                    even_anchor_pairs.add((label, int(raw_r)))
        actionable_twist_pairs = {
            pair for pair in even_anchor_pairs if pair[0] in action_labels
        }
        if actionable_twist_pairs != {("24T15578", 12)}:
            raise ValueError(f"unexpected v14 cached twist source set: {actionable_twist_pairs}")

        family_paths = {
            "lowerHistorical": LOWER_HISTORICAL,
            "lowerPairActions": LOWER_PAIR_ACTIONS,
            "lowerPairRoutes": LOWER_PAIR_ROUTES,
            "lowerSubsetActions": LOWER_SUBSET_ACTIONS,
            "lowerSubsetRoutes": LOWER_SUBSET_ROUTES,
            "higherKummer": HIGHER_RESULTS,
            "characterQuotients": CHARACTER,
            "triples": TRIPLES,
        }
        family_matches = {}
        for name, path in family_paths.items():
            rows = read_jsonl(path)
            direct_pairs = {
                pair for row in rows if (pair := nested_source_pair(row)) in delta_pairs
            }
            direct_hashes = {
                digest
                for row in rows
                for digest in nested_source_hashes(row)
                if digest in anchor_hashes
            }
            family_matches[name] = {
                "artifact": artifact(path),
                "cachedRows": len(rows),
                "directAnchorHashMatches": len(direct_hashes),
                "directSourcePairMatches": len(direct_pairs),
            }
            if direct_pairs or direct_hashes:
                raise ValueError(f"unexpected cached direct v14 route in {name}")

        ordered_rows = []
        ordered_artifacts = []
        for path in ORDERED_SHARDS:
            rows = read_jsonl(path)
            ordered_rows.extend(rows)
            ordered_artifacts.append({**artifact(path), "rows": len(rows)})
        ordered_direct = {
            (str(row["sourceLabel"]), int(r))
            for row in ordered_rows
            for r in row.get("sourceR") or []
            if (str(row["sourceLabel"]), int(r)) in delta_pairs
        }
        ceiling = json.loads(ORDERED_CEILING.read_text(encoding="utf-8"))
        if ordered_direct or ceiling.get("status") != "theorem_certified_route_ceiling":
            raise ValueError("ordered-pair theorem closure is not exact")

        composita = json.loads(COMPOSITA.read_text(encoding="utf-8"))
        composite_rows = composita.get("pilot") or []
        composite_dispositions = defaultdict(int)
        for row in composite_rows:
            if row.get("status") != "certified_simple_compositum":
                raise ValueError("compositum pilot contains an uncertified row")
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            if pair in owned:
                composite_dispositions["owned"] += 1
            elif pair in baseline:
                composite_dispositions["baseline"] += 1
            elif pair in locally_known or pair in receipt_pairs:
                composite_dispositions["known_or_receipted"] += 1
            else:
                composite_dispositions["unexcluded"] += 1
        if composite_dispositions["unexcluded"]:
            raise ValueError("one certified simple compositum remains unexcluded")

        isomorphism_rows = read_jsonl(ISOMORPHISM)
        direct_isomorphism_routes = []
        inverse_routes = []
        inverse_pairs = set()
        for row in isomorphism_rows:
            if row.get("status") != "certified_isomorphic":
                continue
            for pair in delta_pairs:
                if str(row.get("sourceLabel")) == pair[0] and pair[1] in {
                    int(value) for value in row.get("sourceR") or []
                }:
                    direct_isomorphism_routes.append((pair, str(row["targetLabel"])))
                if str(row.get("targetLabel")) != pair[0]:
                    continue
                possible = {
                    (str(row["sourceLabel"]), int(profile["sourceR"]))
                    for subgroup in row.get("subgroupClasses") or []
                    for profile in subgroup.get("profiles") or []
                    if int(profile["targetR"]) == pair[1]
                }
                if possible:
                    inverse_routes.append(
                        {
                            "sourcePair": pair_text(pair),
                            "inverseTargetPairs": sorted(pair_text(value) for value in possible),
                        }
                    )
                    inverse_pairs.update(possible)
        if direct_isomorphism_routes or not inverse_pairs <= (owned | baseline | receipt_pairs):
            raise ValueError("isomorphism frontier contains an unexcluded exact route")

        exclusions = {
            "baselinePairs": len(baseline),
            "ledgerCoefficientHashes": len(ledger_hashes),
            "locallyKnownPairs": len(locally_known),
            "locallyOwnedPairs": len(owned),
            "receiptCoefficientHashes": len(receipt_hashes),
            "receiptPairs": len(receipt_pairs),
            "receiptCount": int(receipt_audit["receiptCount"]),
        }
    finally:
        connection.close()

    certificate = {
        "schemaVersion": "v14-light-nonpair-closure-v1",
        "status": "all_cached_nonpair_routes_closed_two_exact_rows_committed",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sourceBoundary": {
            "acceptedScoreableSignatures": len(delta_pairs),
            "acceptedScoreableAnchorHashes": len(anchor_hashes),
            "censusRows": len(census_rows),
            "inventory": artifact(INVENTORY),
            "pairCensus": artifact(CENSUS),
        },
        "exclusionSnapshot": exclusions,
        "families": {
            "receiptSourceRejoins": {
                "exactParentPackets": 8,
                "newExactRowsCommitted": 1,
                "committed": rejoin,
                "otherExactUnsubmittedSiblingRows": 7,
                "otherSiblingDisposition": "all_locally_owned_or_duplicate_pairs",
                "otherSiblingPairs": sorted(pair_text(pair) for pair in closed_sibling_pairs),
                "remainingExactSafeRows": 0,
            },
            "cachedNegativeTwists": {
                "priorExactDirectAnchorHashMatches": len(cached_negative_direct),
                "v14EvenSourcePairs": sorted(pair_text(pair) for pair in even_anchor_pairs),
                "actionableUniqueCachedActionPairs": sorted(
                    pair_text(pair) for pair in actionable_twist_pairs
                ),
                "newExactRowsCommitted": 1,
                "committed": twist,
                "remainingExactSafeRows": 0,
                "artifacts": [artifact(NEGATIVE_AUDIT), artifact(ACTION_MAP)],
            },
            "lowerHigherKummerSubsetProducts": {
                "status": "no_cached_direct_v14_source_or_anchor_match",
                "audits": {
                    key: family_matches[key]
                    for key in (
                        "lowerHistorical",
                        "lowerPairActions",
                        "lowerPairRoutes",
                        "lowerSubsetActions",
                        "lowerSubsetRoutes",
                        "higherKummer",
                    )
                },
            },
            "characterQuotients": {
                "status": "no_cached_direct_v14_source_or_anchor_match",
                "audit": family_matches["characterQuotients"],
            },
            "unorderedTriples": {
                "status": "no_cached_direct_v14_source_or_anchor_match",
                "audit": family_matches["triples"],
            },
            "orderedPairs": {
                "status": "theorem_closed_no_novel_index24_identity",
                "directV14SourcePairMatches": len(ordered_direct),
                "ceiling": artifact(ORDERED_CEILING),
                "shards": ordered_artifacts,
            },
            "simpleComposita": {
                "status": "all_exact_pilot_rows_excluded",
                "artifact": artifact(COMPOSITA),
                "certifiedPilotRows": len(composite_rows),
                "dispositions": dict(sorted(composite_dispositions.items())),
                "remainingExactSafeRows": 0,
            },
            "index24Isomorphisms": {
                "status": "all_exact_inverse_routes_owned_baseline_or_receipted",
                "artifact": artifact(ISOMORPHISM),
                "cachedRows": len(isomorphism_rows),
                "directRoutes": len(direct_isomorphism_routes),
                "inverseRouteRows": len(inverse_routes),
                "inverseDistinctPairs": len(inverse_pairs),
                "inverseRoutes": inverse_routes,
                "remainingExactSafeRows": 0,
            },
        },
        "heldResearchGaps": {
            "status": "not_executable_from_cached_exact_evidence",
            "items": [
                "fresh polynomial-specific Kummer/subset-product modules",
                "fresh character-quotient field alignments",
                "fresh unordered-triple structural profiles",
            ],
            "guardedHeavyRunbooksPrepared": 0,
            "reason": "no cached exact v14 source route reaches an unexcluded pair",
        },
        "closure": {
            "exactSafeRowsFound": 2,
            "exactSafeRowsCommittedWithZeroKnownHashes": 2,
            "remainingAlreadyExactSafeRows": 0,
            "remainingExecutableRoutes": 0,
        },
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "ledgerWrites": 0,
            "networkCalls": 0,
            "submissionCallsByAudit": 0,
        },
    }
    write_new(OUTPUT, certificate)
    summary = {
        "schemaVersion": "v14-light-nonpair-closure-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "acceptedScoreableSignaturesAudited": len(delta_pairs),
        "exactSafeRowsFound": 2,
        "exactSafeRowsCommitted": 2,
        "remainingAlreadyExactSafeRows": 0,
        "remainingExecutableRoutes": 0,
        "heavyWorkersLaunched": 0,
        "certificate": artifact(OUTPUT),
        "committedSubmissionIds": [rejoin["submissionId"], twist["submissionId"]],
    }
    write_new(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

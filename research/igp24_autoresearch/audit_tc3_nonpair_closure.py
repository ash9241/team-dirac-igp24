#!/usr/bin/env python3
"""Light-only non-pair closure audit for the 23 verified tc3 rows.

The audit rejoins the sealed tc3 receipt mapping to the immutable exact
construction artifacts and accepted ledger rows.  It scans only existing
Python-readable proof caches and deterministic metadata.  It never imports
Sage/GAP, calls the network, submits, or writes the ledger.  When no saved
exact row is available it seals a guarded, coefficient-free runbook for a
later single-worker even-twist pass.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

import audit_tc2_nonpair_closure as base
import stage_all_exact_frobenius_unowned as all_exact
import stage_exact_shared_census as shared
import stage_single_exact_census as single
from stage_v14_negative_twist import exact_real_root_count


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"

MAPPING = DATA / "low_contention_tc3_frontier_receipt_mapping.json"
CERTIFICATE = DATA / "low_contention_tc3_nonpair_closure_certificate.json"
SUMMARY = DATA / "low_contention_tc3_nonpair_closure_summary.json"
RUNBOOK = DATA / "low_contention_tc3_even_twist_guarded_runbook.json"

SUBMISSION_IDS = {
    "sub_f7e75a17e20141aaab5e6e2e93fa1eb7",
    "sub_0434bc0351bd4bc3b19a55b1c3d39079",
}

ACTION_OUTPUT = DATA / "low_contention_tc3_even_twist_action_subset.jsonl"
TWIST_OUTPUT = DATA / "low_contention_tc3_even_twist_results.jsonl"
TWIST_MANIFEST = ROOT / "outbox/low_contention_tc3_even_twist_safe.txt"
TWIST_SUMMARY = DATA / "low_contention_tc3_even_twist_summary.json"


def artifact(path: Path) -> dict:
    return base.artifact(path)


def parse_pair(text: str) -> tuple[str, int]:
    return base.parse_pair(text)


def pair_text(pair: tuple[str, int]) -> str:
    return base.pair_text(pair)


def classify_pair(
    pair: tuple[str, int],
    *,
    baseline: set,
    owned: set,
    known: set,
    receipt_pairs: set,
) -> str:
    return base.classify_pair(
        pair,
        baseline=baseline,
        owned=owned,
        known=known,
        receipt_pairs=receipt_pairs,
    )


def validate_boundary(connection: sqlite3.Connection) -> tuple[list[dict], dict]:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    mappings = mapping.get("mappings") or []
    if (
        mapping.get("schemaVersion")
        != "low-contention-deterministic-tc3-frontier-seal-v1"
        or mapping.get("status")
        != "sealed_23_of_23_exact_candidates_receipt_mapped"
        or mapping.get("coefficientMaterialIncluded") is not False
        or mapping.get("credentialMaterialIncluded") is not False
        or int(mapping.get("routeCount", -1)) != 23
        or len(mappings) != 23
        or {str(row.get("receiptSubmissionId")) for row in mappings}
        != SUBMISSION_IDS
    ):
        raise ValueError("tc3 receipt mapping boundary is incomplete")

    receipt_pins = {
        str(row["submissionId"]): row["receipt"]
        for row in mapping.get("receipts") or []
    }
    if set(receipt_pins) != SUBMISSION_IDS:
        raise ValueError("tc3 receipt pins are incomplete")

    sources = []
    seen_hashes = set()
    seen_pairs = set()
    for mapped in mappings:
        route_id = str(mapped["routeId"])
        source_pair = parse_pair(str(mapped["sourcePair"]))
        target_pair = parse_pair(str(mapped["targetPair"]))
        digest = str(mapped["candidateSha256"])
        submission_id = str(mapped["receiptSubmissionId"])
        if digest in seen_hashes or target_pair in seen_pairs:
            raise ValueError("tc3 boundary is not hash/pair distinct")
        seen_hashes.add(digest)
        seen_pairs.add(target_pair)

        result_path = ROOT / str((mapped.get("result") or {})["path"])
        postflight_path = ROOT / str((mapped.get("postflight") or {})["path"])
        stage_path = DATA / f"{route_id}_stage_certificate.json"
        receipt_path = ROOT / str(receipt_pins[submission_id]["path"])
        for path in (result_path, postflight_path, stage_path, receipt_path):
            if not path.is_file():
                raise ValueError(f"missing tc3 provenance artifact: {path}")
        if (
            base.sha256_path(result_path) != str(mapped["result"]["sha256"])
            or base.sha256_path(postflight_path)
            != str(mapped["postflight"]["sha256"])
            or base.sha256_path(receipt_path)
            != str(receipt_pins[submission_id]["sha256"])
        ):
            raise ValueError(f"tc3 provenance pin changed: {route_id}")

        result_rows = base.read_jsonl(result_path)
        stage = json.loads(stage_path.read_text(encoding="utf-8"))
        postflight = json.loads(postflight_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if len(result_rows) != 1:
            raise ValueError(f"tc3 result is not one exact row: {route_id}")
        result = result_rows[0]
        candidate = stage.get("candidate") or {}
        post_target = postflight.get("target") or {}
        response = receipt.get("response") or {}
        if (
            result.get("status") != "certified"
            or str(result.get("coefficientSha256")) != digest
            or (str(result.get("targetLabel")), int(result.get("targetR", -1)))
            != target_pair
            or stage.get("coefficientMaterialIncluded") is not False
            or not str(stage.get("status", "")).startswith(
                "certified_exact_novel_tc3_staged"
            )
            or str(stage.get("routeId")) != route_id
            or str(candidate.get("coefficientSha256")) != digest
            or (
                str(candidate.get("targetLabel")),
                int(candidate.get("targetR", -1)),
            )
            != target_pair
            or str((stage.get("result") or {}).get("sha256"))
            != base.sha256_path(result_path)
            or str((stage.get("postflight") or {}).get("sha256"))
            != base.sha256_path(postflight_path)
            or postflight.get("coefficientMaterialIncluded") is not False
            or str(postflight.get("routeId")) != route_id
            or (str(post_target.get("label")), int(post_target.get("r", -1)))
            != target_pair
            or receipt.get("commit") is not True
            or int(receipt.get("knownLocalHashes", -1)) != 0
            or str(receipt.get("manifestHash"))
            != str(mapped.get("receiptManifestSha256"))
            or str(response.get("submissionId")) != submission_id
            or int(response.get("rejectedCount", -1)) != 0
            or list(response.get("failedPolynomials") or [])
        ):
            raise ValueError(f"tc3 exact provenance envelope mismatch: {route_id}")

        ledger_rows = connection.execute(
            "SELECT p.submission_id,p.polynomial_index,p.coefficients,"
            "p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
            "v.scoring_status FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
            "AND p.coefficient_hash=?",
            (submission_id, digest),
        ).fetchall()
        if len(ledger_rows) != 1:
            raise ValueError(f"tc3 hash does not identify one submitted source: {route_id}")
        ledger = ledger_rows[0]
        if (
            str(ledger["status"]) != "accepted"
            or (str(ledger["label"]), int(ledger["r"])) != target_pair
            or int(ledger["scoreable"] or 0) != 1
            or int(ledger["in_baseline"] or 0) != 0
            or str(ledger["scoring_status"]) != "scoreable"
        ):
            raise ValueError(f"tc3 source is not accepted-scoreable: {route_id}")
        line = str(ledger["coefficients"])
        if hashlib.sha256(line.encode("ascii")).hexdigest() != digest:
            raise ValueError(f"tc3 ledger hash mismatch: {route_id}")
        coefficients = [int(value) for value in line.split(",")]
        if len(coefficients) != 25:
            raise ValueError(f"tc3 source is not degree 24: {route_id}")
        sources.append(
            {
                "coefficientSha256": digest,
                "exactConstruction": artifact(stage_path),
                "exactResult": artifact(result_path),
                "evenModel": all(
                    coefficients[index] == 0 for index in range(1, 25, 2)
                ),
                "label": target_pair[0],
                "polynomialIndex": int(ledger["polynomial_index"]),
                "postflight": artifact(postflight_path),
                "r": target_pair[1],
                "receipt": artifact(receipt_path),
                "routeId": route_id,
                "sourcePair": pair_text(source_pair),
                "submissionId": submission_id,
            }
        )
    sources.sort(key=lambda row: (int(row["label"][3:]), row["r"]))
    return sources, mapping


def make_runbook(ranked_sources: list[dict]) -> dict:
    labels = sorted(
        {str(row["label"]) for row in ranked_sources},
        key=lambda value: int(value[3:]),
    )
    action_command = (
        "test ! -e data/low_contention_tc3_even_twist_action_subset.jsonl && "
        "/usr/local/bin/sage -python build_even_twist_action_subset.sage.py "
        "--db data/ledger.sqlite3 "
        "--output data/low_contention_tc3_even_twist_action_subset.jsonl "
        + " ".join(f"--source-label {label}" for label in labels)
    )
    execute_command = (
        "test ! -e data/low_contention_tc3_even_twist_results.jsonl && "
        "test ! -e outbox/low_contention_tc3_even_twist_safe.txt && "
        "test ! -e data/low_contention_tc3_even_twist_summary.json && "
        "/usr/local/bin/sage -python even_twist_delta_scan.sage.py "
        "data/ledger.sqlite3 data/low_contention_tc3_even_twist_action_subset.jsonl "
        "data/low_contention_tc3_even_twist_results.jsonl "
        "outbox/low_contention_tc3_even_twist_safe.txt "
        "--summary-output data/low_contention_tc3_even_twist_summary.json "
        "--synced-after 1970-01-01T00:00:00Z --max-candidates 16 "
        + " ".join(
            f"--source-hash {row['coefficientSha256']}" for row in ranked_sources
        )
    )
    outputs = (ACTION_OUTPUT, TWIST_OUTPUT, TWIST_MANIFEST, TWIST_SUMMARY)
    return {
        "schemaVersion": "low-contention-tc3-even-twist-guarded-runbook-v1",
        "status": "prepared_execution_held_for_existing_heavy_lease",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "rankedSources": [
            {
                "coefficientSha256": row["coefficientSha256"],
                "exactNegativeR": int(row["exactNegativeR"]),
                "label": row["label"],
                "polynomialIndex": int(row["polynomialIndex"]),
                "priority": index,
                "r": int(row["r"]),
                "submissionId": row["submissionId"],
            }
            for index, row in enumerate(ranked_sources, start=1)
        ],
        "rankingBasis": (
            "Exact negative signature ascending, then transitive label and source "
            "signature. This is a diversity heuristic only; target labels and live "
            "value remain guarded until the exact action map is built."
        ),
        "distinctActionLabels": len(labels),
        "positiveAndNegativeTwistsGeneratedPerSource": True,
        "inputs": {
            "actionBuilder": artifact(base.TWIST_BUILDER),
            "ledger": {"path": str(DB.relative_to(ROOT))},
            "twistWorker": artifact(base.TWIST_WORKER),
        },
        "absentOutputGuard": {
            "allAbsentAtPreparation": all(not path.exists() for path in outputs),
            "paths": [str(path.relative_to(ROOT)) for path in outputs],
        },
        "commands": {
            "buildSevenLabelActionSubset": action_command,
            "executeEightHashPinnedTwists": execute_command,
        },
        "launchGate": (
            "only after the coordinator confirms the sole Sage/GAP heavy slot is free"
        ),
        "postRun": (
            "Refresh ledger and receipts, then pass the live-gold manifest through "
            "the receipt-aware SINGLE exact census; never submit an unresolved or "
            "excluded pair/hash."
        ),
        "sideEffectsAtPreparation": {
            "heavyWorkersLaunched": 0,
            "ledgerWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }


def main() -> int:
    inputs = (
        MAPPING,
        base.NEGATIVE_AUDIT,
        base.TWIST_ACTION,
        base.TWIST_BUILDER,
        base.TWIST_WORKER,
        base.LOWER_HISTORICAL,
        base.LOWER_PAIR_ACTIONS,
        base.LOWER_PAIR_ROUTES,
        base.LOWER_SUBSET_ACTIONS,
        base.LOWER_SUBSET_ROUTES,
        base.HIGHER_RESULTS,
        base.CHARACTER,
        base.TRIPLE_CURRENT,
        base.ORDERED_CEILING,
        base.COMPOSITA,
        base.ISOMORPHISM,
        *base.FULL_PAIR_ACTIONS,
        *base.TRIPLE_ORBITS,
        *base.ORDERED,
    )
    for path in inputs:
        if not path.is_file():
            raise ValueError(f"missing audit input: {path}")
    ast.parse(base.TWIST_BUILDER.read_text(encoding="utf-8"), str(base.TWIST_BUILDER))
    ast.parse(base.TWIST_WORKER.read_text(encoding="utf-8"), str(base.TWIST_WORKER))

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        sources, _mapping = validate_boundary(connection)
        source_pairs = {(row["label"], int(row["r"])) for row in sources}
        source_hashes = {row["coefficientSha256"] for row in sources}
        source_keys = {
            (
                row["submissionId"],
                int(row["polynomialIndex"]),
                row["label"],
                int(row["r"]),
            )
            for row in sources
        }
        source_id_keys = {(row[0], row[1]) for row in source_keys}
        sources_by_pair = {(row["label"], int(row["r"])): row for row in sources}
        if len(source_pairs) != 23 or len(source_hashes) != 23 or len(source_keys) != 23:
            raise ValueError("tc3 accepted source boundary is not 23 distinct rows")

        baseline = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        known_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE label IS NOT NULL AND r IS NOT NULL"
            )
        }
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, {}
        )
        staged_hashes = base.outbox_hashes()

        # Exact saved candidates: the same allowlisted proof families used by
        # the receipt-aware production censuses.
        single_candidates, single_audit = single.scan_candidates(DATA)
        single_hits = []
        for candidate in single_candidates:
            pins = {
                (str(pin["submissionId"]), int(pin["polynomialIndex"]))
                for pin in candidate.get("sourcePins") or []
            }
            if pins & source_id_keys:
                single_hits.append(candidate)

        stable_pool, stable_audit = shared.scan_stable_multi(
            DATA, DATA / "pair_signature_map.jsonl"
        )
        stable_hits = [
            row for row in stable_pool.values() if row["sourceKeys"] & source_keys
        ]
        frobenius_certificates, _ = all_exact.scan_json_artifacts(DATA)
        unresolved_pool, unresolved_audit = shared.unresolved_options(
            frobenius_certificates, ROOT
        )
        unresolved_hits = [
            row
            for row in unresolved_pool.values()
            if row["sourceKey"] in source_keys
        ]
        frobenius_pool, frobenius_certificate_audit, frobenius_audit = (
            all_exact.collect_exact_pool(
                frobenius_certificates, connection, ROOT, None
            )
        )
        frobenius_hits = []
        for row in frobenius_pool.values():
            proofs = {
                (
                    str(proof["sourceSubmissionId"]),
                    int(proof["sourcePolynomialIndex"]),
                    str(proof["sourceLabel"]),
                    int(proof["sourceR"]),
                )
                for proof in row["proofs"]
            }
            if proofs & source_keys:
                frobenius_hits.append(row)
        if single_hits or stable_hits or unresolved_hits or frobenius_hits:
            raise ValueError("an already-exact tc3-derived row requires staging")

        negative_rows = base.read_jsonl(base.NEGATIVE_AUDIT)
        negative_direct_hashes = {
            str(row.get("sourceCoefficientSha256"))
            for row in negative_rows
            if str(row.get("sourceCoefficientSha256")) in source_hashes
        }
        twist_actions = {
            str(row["sourceLabel"]): row
            for row in base.read_jsonl(base.TWIST_ACTION)
        }
        negative_dispositions = []
        ranked_twist_sources = []
        for source in sources:
            if not source["evenModel"]:
                continue
            row = connection.execute(
                "SELECT coefficients FROM polynomials WHERE submission_id=? "
                "AND polynomial_index=?",
                (source["submissionId"], int(source["polynomialIndex"])),
            ).fetchone()
            values = [int(value) for value in str(row[0]).split(",")]
            quotient_roots, sturm_length = exact_real_root_count(values[::2])
            negative_r = 2 * quotient_roots - int(source["r"])
            ranked_twist_sources.append({**source, "exactNegativeR": negative_r})
            action = twist_actions.get(source["label"])
            if action is None:
                negative_dispositions.append(
                    {
                        "sourcePair": pair_text(
                            (source["label"], int(source["r"]))
                        ),
                        "exactNegativeR": negative_r,
                        "quotientRealRoots": quotient_roots,
                        "sturmSequenceLength": sturm_length,
                        "status": "exact_signature_cached_action_missing_runbook_prepared",
                    }
                )
                continue
            labels = {str(value) for value in action.get("targetLabels") or []}
            possible = {
                (label, negative_r) for label in labels
            } | {(label, int(source["r"])) for label in labels}
            negative_dispositions.append(
                {
                    "actionRowSha256": base.canonical_row_sha256(action),
                    "sourcePair": pair_text((source["label"], int(source["r"]))),
                    "possiblePairDispositions": dict(
                        sorted(
                            Counter(
                                classify_pair(
                                    pair,
                                    baseline=baseline,
                                    owned=owned,
                                    known=known_pairs,
                                    receipt_pairs=receipt_pairs,
                                )
                                for pair in possible
                            ).items()
                        )
                    ),
                    "status": "cached_action_requires_hash_pinned_worker",
                }
            )

        ranked_twist_sources.sort(
            key=lambda row: (
                int(row["exactNegativeR"]),
                int(row["label"][3:]),
                int(row["r"]),
                row["coefficientSha256"],
            )
        )
        if len(ranked_twist_sources) != 8:
            raise ValueError("unexpected tc3 even-model frontier size")
        if any(source["label"] in twist_actions for source in ranked_twist_sources):
            raise ValueError("tc3 cached twist action changed; refresh closure logic")

        old_kummer_paths = (
            base.LOWER_HISTORICAL,
            base.LOWER_PAIR_ACTIONS,
            base.LOWER_PAIR_ROUTES,
            base.LOWER_SUBSET_ACTIONS,
            base.LOWER_SUBSET_ROUTES,
            base.HIGHER_RESULTS,
        )
        old_kummer_matches = {}
        for path in old_kummer_paths:
            rows = base.read_jsonl(path)
            pair_matches = {
                pair
                for row in rows
                if (pair := base.direct_source_pair(row)) in source_pairs
            }
            hash_matches = {
                digest
                for row in rows
                for digest in base.recursive_source_hashes(row)
                if digest in source_hashes
            }
            old_kummer_matches[str(path.relative_to(ROOT))] = {
                "artifact": artifact(path),
                "cachedRows": len(rows),
                "directAnchorHashMatches": len(hash_matches),
                "directSourcePairMatches": len(pair_matches),
            }
            if pair_matches or hash_matches:
                raise ValueError(f"unexpected old Kummer direct match: {path}")

        full_pair_rows = []
        full_pair_artifacts = []
        for path in base.FULL_PAIR_ACTIONS:
            rows = base.read_jsonl(path)
            full_pair_rows.extend(rows)
            full_pair_artifacts.append({**artifact(path), "rows": len(rows)})
        full_pair_routes = []
        executable_safe_pair_routes = 0
        for row in full_pair_rows:
            matches = [
                pair
                for pair in source_pairs
                if pair[0] == str(row.get("sourceLabel"))
            ]
            for source_pair in matches:
                possible = {
                    (str(row["targetLabel"]), int(r_value))
                    for r_value in (
                        row.get("sourceSignatureToPossibleTargetSignatures") or {}
                    ).get(str(source_pair[1]), [])
                }
                if not possible:
                    continue
                source = sources_by_pair[source_pair]
                dispositions = Counter(
                    classify_pair(
                        pair,
                        baseline=baseline,
                        owned=owned,
                        known=known_pairs,
                        receipt_pairs=receipt_pairs,
                    )
                    for pair in possible
                )
                literal = bool(source["evenModel"])
                if literal and dispositions.get("safe_at_snapshot", 0):
                    executable_safe_pair_routes += 1
                full_pair_routes.append(
                    {
                        "actionRowSha256": base.canonical_row_sha256(row),
                        "currentAnchorIsLiteralEvenModel": literal,
                        "possiblePairCount": len(possible),
                        "possiblePairDispositions": dict(
                            sorted(dispositions.items())
                        ),
                        "sourcePair": pair_text(source_pair),
                        "targetLabel": str(row["targetLabel"]),
                    }
                )
        if executable_safe_pair_routes:
            raise ValueError("one literal Kummer pair route remains executable")

        character_rows = base.read_jsonl(base.CHARACTER)
        source_labels = {pair[0] for pair in source_pairs}
        character_label_rows = [
            row
            for row in character_rows
            if str(row.get("sourceLabel")) in source_labels
        ]
        character_hash_rows = [
            row
            for row in character_rows
            if str(row.get("coefficientSha256")) in source_hashes
        ]
        character_statuses = Counter(
            str(row.get("status")) for row in character_label_rows
        )
        if character_hash_rows:
            raise ValueError("current tc3 hash has a cached character alignment")

        triple_current_rows = base.read_jsonl(base.TRIPLE_CURRENT)
        triple_current_pairs = {
            pair
            for row in triple_current_rows
            if (pair := base.direct_source_pair(row)) in source_pairs
        }
        triple_orbit_matches = []
        exact_triple_routes = []
        for path in base.TRIPLE_ORBITS:
            for row in base.read_jsonl(path):
                matches = [
                    pair
                    for pair in source_pairs
                    if pair[0] == str(row.get("sourceLabel"))
                ]
                for source_pair in matches:
                    current_routes = [
                        route
                        for route in row.get("signatureRoutes") or []
                        if int(route.get("sourceR", -1)) == source_pair[1]
                    ]
                    route_pairs = {
                        (str(route["targetLabel"]), int(route["targetR"]))
                        for route in current_routes
                    }
                    exact_triple_routes.extend(
                        (source_pair, pair) for pair in route_pairs
                    )
                    triple_orbit_matches.append(
                        {
                            "artifact": str(path.relative_to(ROOT)),
                            "sourcePair": pair_text(source_pair),
                            "sourceSignatureProfiled": source_pair[1]
                            in {int(value) for value in row.get("sourceR") or []},
                            "exactCurrentSignatureRoutes": len(route_pairs),
                            "exactRouteDispositions": dict(
                                sorted(
                                    Counter(
                                        classify_pair(
                                            pair,
                                            baseline=baseline,
                                            owned=owned,
                                            known=known_pairs,
                                            receipt_pairs=receipt_pairs,
                                        )
                                        for pair in route_pairs
                                    ).items()
                                )
                            ),
                            "targetLabels": sorted(
                                {str(value["targetLabel"]) for value in row.get("targets") or []}
                            ),
                        }
                    )
        if triple_current_pairs or exact_triple_routes:
            raise ValueError("triple frontier contains a direct cached tc3 route")

        ceiling = json.loads(base.ORDERED_CEILING.read_text(encoding="utf-8"))
        if ceiling.get("status") != "theorem_certified_route_ceiling":
            raise ValueError("ordered-pair theorem ceiling is not certified")
        ordered_label_rows = 0
        for path in base.ORDERED:
            for row in base.read_jsonl(path):
                if str(row.get("sourceLabel")) in source_labels:
                    ordered_label_rows += 1

        composita = json.loads(base.COMPOSITA.read_text(encoding="utf-8"))
        composite_rows = composita.get("pilot") or []
        composite_direct_hashes = set()
        composite_direct_pairs = set()
        composite_dispositions = Counter()
        for row in composite_rows:
            if row.get("status") != "certified_simple_compositum":
                raise ValueError("compositum pilot contains an uncertified row")
            for side in (row.get("first") or {}, row.get("second") or {}):
                if str(side.get("sourceCoefficientSha256")) in source_hashes:
                    composite_direct_hashes.add(
                        str(side["sourceCoefficientSha256"])
                    )
                pair = single.valid_pair(
                    side.get("sourceLabel"), side.get("sourceR")
                )
                if pair in source_pairs:
                    composite_direct_pairs.add(pair)
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            composite_dispositions[
                classify_pair(
                    pair,
                    baseline=baseline,
                    owned=owned,
                    known=known_pairs,
                    receipt_pairs=receipt_pairs,
                )
            ] += 1
        if composite_direct_hashes or composite_direct_pairs:
            raise ValueError("simple-compositum cache contains a direct tc3 source")

        isomorphism_rows = base.read_jsonl(base.ISOMORPHISM)
        direct_isomorphisms = []
        inverse_routes = []
        inverse_pairs = set()
        for row in isomorphism_rows:
            if row.get("status") != "certified_isomorphic":
                continue
            for source_pair in source_pairs:
                if str(row.get("sourceLabel")) == source_pair[0]:
                    possible = {
                        (str(row["targetLabel"]), int(profile["targetR"]))
                        for subgroup in row.get("subgroupClasses") or []
                        for profile in subgroup.get("profiles") or []
                        if int(profile["sourceR"]) == source_pair[1]
                    }
                    if possible:
                        direct_isomorphisms.append((source_pair, possible))
                if str(row.get("targetLabel")) != source_pair[0]:
                    continue
                possible = {
                    (str(row["sourceLabel"]), int(profile["sourceR"]))
                    for subgroup in row.get("subgroupClasses") or []
                    for profile in subgroup.get("profiles") or []
                    if int(profile["targetR"]) == source_pair[1]
                }
                if possible:
                    inverse_pairs.update(possible)
                    inverse_routes.append(
                        {
                            "sourcePair": pair_text(source_pair),
                            "inversePairs": sorted(
                                pair_text(pair) for pair in possible
                            ),
                        }
                    )
        if direct_isomorphisms or not inverse_pairs <= (
            baseline | owned | known_pairs | receipt_pairs
        ):
            raise ValueError("isomorphism frontier contains an unexcluded route")

        runbook = make_runbook(ranked_twist_sources)
        if runbook["absentOutputGuard"]["allAbsentAtPreparation"] is not True:
            raise ValueError("guarded tc3 twist outputs already exist")
        base.write_new(RUNBOOK, runbook)

        certificate = {
            "schemaVersion": "low-contention-tc3-light-nonpair-closure-v1",
            "status": "zero_already_exact_rows_eight_source_guarded_twist_runbook_prepared",
            "coefficientMaterialIncluded": False,
            "credentialMaterialIncluded": False,
            "sourceBoundary": {
                "acceptedScoreableSignatures": len(source_pairs),
                "acceptedScoreableAnchorHashes": len(source_hashes),
                "evenLiteralModels": sum(bool(row["evenModel"]) for row in sources),
                "mapping": artifact(MAPPING),
                "receiptBatchSplit": "1+22",
                "sources": sources,
            },
            "exclusionSnapshot": {
                "baselinePairs": len(baseline),
                "ledgerCoefficientHashes": len(ledger_hashes),
                "locallyKnownPairs": len(known_pairs),
                "locallyOwnedPairs": len(owned),
                "outboxCoefficientHashes": len(staged_hashes),
                "receiptCoefficientHashes": len(receipt_hashes),
                "receiptPairs": len(receipt_pairs),
                "receiptCount": int(receipt_audit["receiptCount"]),
            },
            "families": {
                "exactSavedCandidateRejoin": {
                    "status": "zero_direct_source_anchor_hits",
                    "single": {
                        "pool": len(single_candidates),
                        "hits": 0,
                        "audit": single_audit,
                    },
                    "stableMulti": {
                        "pool": len(stable_pool),
                        "hits": 0,
                        "audit": stable_audit,
                    },
                    "exactFrobenius": {
                        "pool": len(frobenius_pool),
                        "hits": 0,
                        "audit": frobenius_audit,
                        "certificateAudit": frobenius_certificate_audit,
                    },
                    "unresolvedFrobenius": {
                        "pool": len(unresolved_pool),
                        "hits": 0,
                        "audit": unresolved_audit,
                    },
                },
                "cachedNegativeTwists": {
                    "artifact": artifact(base.NEGATIVE_AUDIT),
                    "actionMap": artifact(base.TWIST_ACTION),
                    "directAnchorHashMatches": len(negative_direct_hashes),
                    "dispositions": negative_dispositions,
                    "guardedRunbook": artifact(RUNBOOK),
                    "remainingAlreadyExactSafeRows": 0,
                },
                "lowerHigherKummerSubsetProducts": {
                    "status": "no_literal_executable_safe_route",
                    "oldCachedAudits": old_kummer_matches,
                    "fullLedgerActionShards": full_pair_artifacts,
                    "directFullLedgerActionRows": full_pair_routes,
                    "literalExecutableSafeRoutes": executable_safe_pair_routes,
                    "remainingAlreadyExactSafeRows": 0,
                },
                "characterQuotients": {
                    "artifact": artifact(base.CHARACTER),
                    "directCurrentAnchorHashRows": len(character_hash_rows),
                    "historicalSameLabelRows": len(character_label_rows),
                    "historicalStatusCounts": dict(
                        sorted(character_statuses.items())
                    ),
                    "status": "no_cached_current_anchor_alignment",
                },
                "unorderedTriples": {
                    "currentProfileDirectPairs": len(triple_current_pairs),
                    "groupOrbitMatches": triple_orbit_matches,
                    "status": "no_exact_current_signature_route",
                    "artifacts": [
                        artifact(base.TRIPLE_CURRENT),
                        *[artifact(path) for path in base.TRIPLE_ORBITS],
                    ],
                },
                "orderedPairs": {
                    "status": "theorem_closed_to_source_pair_identity",
                    "cachedSameLabelRows": ordered_label_rows,
                    "ceiling": artifact(base.ORDERED_CEILING),
                    "shards": [artifact(path) for path in base.ORDERED],
                    "remainingExactSafeRows": 0,
                },
                "simpleComposita": {
                    "status": "no_direct_source_match",
                    "artifact": artifact(base.COMPOSITA),
                    "certifiedPilotRows": len(composite_rows),
                    "targetDispositions": dict(
                        sorted(composite_dispositions.items())
                    ),
                },
                "index24Isomorphisms": {
                    "status": "all_inverse_routes_excluded_no_direct_routes",
                    "artifact": artifact(base.ISOMORPHISM),
                    "directRoutes": len(direct_isomorphisms),
                    "inverseRoutes": inverse_routes,
                    "inverseDistinctPairs": len(inverse_pairs),
                },
            },
            "closure": {
                "alreadyExactSafeRows": 0,
                "manifestsStagedByAudit": 0,
                "offlineDryRunsByAudit": 0,
                "guardedRunbooksPrepared": 1,
                "guardedRunbookSourceHashes": len(ranked_twist_sources),
                "unresolvedExecutableCachedRoutes": 0,
            },
            "sideEffects": {
                "heavyWorkersLaunched": 0,
                "ledgerWrites": 0,
                "networkCalls": 0,
                "submissionCalls": 0,
            },
        }
    finally:
        connection.close()

    base.write_new(CERTIFICATE, certificate)
    summary = {
        "schemaVersion": "low-contention-tc3-light-nonpair-closure-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "acceptedScoreableSignaturesAudited": 23,
        "alreadyExactSafeRows": 0,
        "guardedRunbooksPrepared": 1,
        "guardedRunbookSourceHashes": 8,
        "guardedRunbookDistinctActionLabels": 7,
        "heavyWorkersLaunched": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
        "certificate": artifact(CERTIFICATE),
        "runbook": artifact(RUNBOOK),
    }
    base.write_new(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

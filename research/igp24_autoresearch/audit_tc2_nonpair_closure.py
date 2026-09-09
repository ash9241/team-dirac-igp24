#!/usr/bin/env python3
"""Light-only non-pair/second-hop audit for the 14 verified tc2 rows.

This program rejoins the two receipt batches to their exact construction
artifacts and immutable ledger verifications.  It then scans only cached proof
families, excluding baseline, owned, known, receipt, ledger-hash, receipt-hash,
and outbox-hash collisions.  It never imports Sage/GAP, calls the network,
submits, or writes the ledger.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import stage_all_exact_frobenius_unowned as all_exact
import stage_exact_shared_census as shared
import stage_single_exact_census as single
from stage_v14_negative_twist import exact_real_root_count


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"

MAPPING = DATA / "low_contention_tc2_frontier_receipt_mapping.json"
CERTIFICATE = DATA / "low_contention_tc2_nonpair_closure_certificate.json"
SUMMARY = DATA / "low_contention_tc2_nonpair_closure_summary.json"
RUNBOOK = DATA / "low_contention_tc2_even_twist_guarded_runbook.json"

SUBMISSION_IDS = {
    "sub_b80910ae012d49cf9b5dcfa4d00b6f51",
    "sub_574f9e56ce804797ab49160e2f9838e6",
}

NEGATIVE_AUDIT = DATA / "agent_f7_negative_twist_signature_audit.jsonl"
TWIST_ACTION = DATA / "agent_gold_b_even_twist_action_map.jsonl"
TWIST_BUILDER = ROOT / "build_even_twist_action_subset.sage.py"
TWIST_WORKER = ROOT / "even_twist_delta_scan.sage.py"

LOWER_HISTORICAL = DATA / "agent_gold_c_lower_kummer_historical_census.jsonl"
LOWER_PAIR_ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
LOWER_PAIR_ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
LOWER_SUBSET_ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
LOWER_SUBSET_ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes_v2.jsonl"
FULL_PAIR_ACTIONS = tuple(
    DATA / f"agent_f5_full_ledger_pair_product_actions_shard{index}of4.jsonl"
    for index in range(4)
)
HIGHER_RESULTS = DATA / "agent_f9_higher_kummer_triple_pilot_results.jsonl"
CHARACTER = DATA / "agent_gold_c_character_census_alignment_results.jsonl"
TRIPLE_CURRENT = DATA / "agent_gold_a_triple_current_profiles.jsonl"
TRIPLE_ORBITS = tuple(DATA / f"agent_gold_a_triple_orbit_shard{index}.jsonl" for index in range(4))
ORDERED = tuple(DATA / f"agent_index24_ordered_pair_shard{index}.jsonl" for index in range(6))
ORDERED_CEILING = DATA / "agent_index24_ordered_pair_route_ceiling.json"
COMPOSITA = DATA / "agent_non12_simple_compositum_pilot.json"
ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"

ACTION_OUTPUT = DATA / "low_contention_tc2_even_twist_action_subset.jsonl"
TWIST_OUTPUT = DATA / "low_contention_tc2_even_twist_results.jsonl"
TWIST_MANIFEST = ROOT / "outbox/low_contention_tc2_even_twist_safe.txt"
TWIST_SUMMARY = DATA / "low_contention_tc2_even_twist_summary.json"

COEFFICIENT_RE = re.compile(r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path) -> dict:
    return {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_path(path)}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_new(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if COEFFICIENT_RE.search(payload.decode("utf-8")):
        raise ValueError(f"coefficient payload entered {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite sealed output: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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


def parse_pair(text: str) -> tuple[str, int]:
    label, separator, raw_r = text.partition("/r")
    pair = single.valid_pair(label, raw_r) if separator else None
    if pair is None:
        raise ValueError(f"invalid pair: {text}")
    return pair


def canonical_row_sha256(row: dict) -> str:
    payload = json.dumps(row, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def outbox_hashes() -> set[str]:
    result = set()
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = single.canonical_polynomial_line(raw.strip())
            if line is not None:
                result.add(hashlib.sha256(line.encode("ascii")).hexdigest())
    return result


def classify_pair(
    pair: tuple[str, int],
    *,
    baseline: set,
    owned: set,
    known: set,
    receipt_pairs: set,
) -> str:
    if pair in baseline:
        return "baseline"
    if pair in owned:
        return "owned"
    if pair in known:
        return "known_verification"
    if pair in receipt_pairs:
        return "receipted"
    return "safe_at_snapshot"


def recursive_source_hashes(value) -> set[str]:
    result = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {"sourceCoefficientSha256", "coefficientSha256"} and isinstance(child, str):
                    result.add(child)
                elif isinstance(child, (dict, list)):
                    stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return result


def direct_source_pair(row: dict) -> tuple[str, int] | None:
    values = [row]
    for key in ("source", "candidate"):
        if isinstance(row.get(key), dict):
            values.append(row[key])
    for value in values:
        label = value.get("sourceLabel", value.get("label"))
        r_value = value.get("sourceR", value.get("r"))
        if isinstance(label, str) and isinstance(r_value, int):
            return label, int(r_value)
    return None


def validate_boundary(connection: sqlite3.Connection) -> tuple[list[dict], dict]:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    mappings = mapping.get("mappings") or []
    if (
        mapping.get("schemaVersion") != "low-contention-deterministic-tc2-frontier-seal-v1"
        or mapping.get("status") != "sealed_14_of_14_exact_candidates_receipt_mapped"
        or mapping.get("coefficientMaterialIncluded") is not False
        or mapping.get("credentialMaterialIncluded") is not False
        or int(mapping.get("routeCount", -1)) != 14
        or len(mappings) != 14
        or {str(row.get("receiptSubmissionId")) for row in mappings} != SUBMISSION_IDS
    ):
        raise ValueError("tc2 receipt mapping boundary is incomplete")

    receipt_pins = {
        str(row["submissionId"]): row["receipt"] for row in mapping.get("receipts") or []
    }
    if set(receipt_pins) != SUBMISSION_IDS:
        raise ValueError("tc2 receipt pins are incomplete")

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
            raise ValueError("tc2 boundary is not hash/pair distinct")
        seen_hashes.add(digest)
        seen_pairs.add(target_pair)

        result_path = ROOT / str((mapped.get("result") or {})["path"])
        postflight_path = ROOT / str((mapped.get("postflight") or {})["path"])
        stage_path = DATA / f"{route_id}_stage_certificate.json"
        receipt_path = ROOT / str(receipt_pins[submission_id]["path"])
        for path in (result_path, postflight_path, stage_path, receipt_path):
            if not path.is_file():
                raise ValueError(f"missing tc2 provenance artifact: {path}")
        if (
            sha256_path(result_path) != str(mapped["result"]["sha256"])
            or sha256_path(postflight_path) != str(mapped["postflight"]["sha256"])
            or sha256_path(receipt_path) != str(receipt_pins[submission_id]["sha256"])
        ):
            raise ValueError(f"tc2 provenance pin changed: {route_id}")

        result_rows = read_jsonl(result_path)
        stage = json.loads(stage_path.read_text(encoding="utf-8"))
        postflight = json.loads(postflight_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if len(result_rows) != 1:
            raise ValueError(f"tc2 result is not one exact row: {route_id}")
        result = result_rows[0]
        candidate = stage.get("candidate") or {}
        post_target = postflight.get("target") or {}
        response = receipt.get("response") or {}
        if (
            result.get("status") != "certified"
            or str(result.get("coefficientSha256")) != digest
            or (str(result.get("targetLabel")), int(result.get("targetR", -1))) != target_pair
            or stage.get("coefficientMaterialIncluded") is not False
            or not str(stage.get("status", "")).startswith("certified_exact_novel_tc2_staged")
            or str(stage.get("routeId")) != route_id
            or str(candidate.get("coefficientSha256")) != digest
            or (str(candidate.get("targetLabel")), int(candidate.get("targetR", -1))) != target_pair
            or str((stage.get("result") or {}).get("sha256")) != sha256_path(result_path)
            or str((stage.get("postflight") or {}).get("sha256")) != sha256_path(postflight_path)
            or postflight.get("coefficientMaterialIncluded") is not False
            or str(postflight.get("routeId")) != route_id
            or (str(post_target.get("label")), int(post_target.get("r", -1))) != target_pair
            or receipt.get("commit") is not True
            or int(receipt.get("knownLocalHashes", -1)) != 0
            or str(receipt.get("manifestHash")) != str(mapped.get("receiptManifestSha256"))
            or str(response.get("submissionId")) != submission_id
            or int(response.get("rejectedCount", -1)) != 0
            or list(response.get("failedPolynomials") or [])
        ):
            raise ValueError(f"tc2 exact provenance envelope mismatch: {route_id}")

        ledger_rows = connection.execute(
            "SELECT p.submission_id,p.polynomial_index,p.coefficients,p.coefficient_hash,"
            "v.status,v.label,v.r,v.scoreable,v.in_baseline,v.scoring_status "
            "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? AND p.coefficient_hash=?",
            (submission_id, digest),
        ).fetchall()
        if len(ledger_rows) != 1:
            raise ValueError(f"tc2 hash does not identify one submitted source: {route_id}")
        ledger = ledger_rows[0]
        if (
            str(ledger["status"]) != "accepted"
            or (str(ledger["label"]), int(ledger["r"])) != target_pair
            or int(ledger["scoreable"] or 0) != 1
            or int(ledger["in_baseline"] or 0) != 0
            or str(ledger["scoring_status"]) != "scoreable"
        ):
            raise ValueError(f"tc2 source is not accepted-scoreable: {route_id}")
        line = str(ledger["coefficients"])
        if hashlib.sha256(line.encode("ascii")).hexdigest() != digest:
            raise ValueError(f"tc2 ledger hash mismatch: {route_id}")
        coefficients = [int(value) for value in line.split(",")]
        if len(coefficients) != 25:
            raise ValueError(f"tc2 source is not degree 24: {route_id}")
        is_even = all(coefficients[index] == 0 for index in range(1, 25, 2))
        sources.append({
            "coefficientSha256": digest,
            "exactConstruction": artifact(stage_path),
            "exactResult": artifact(result_path),
            "evenModel": is_even,
            "label": target_pair[0],
            "polynomialIndex": int(ledger["polynomial_index"]),
            "postflight": artifact(postflight_path),
            "r": target_pair[1],
            "receipt": artifact(receipt_path),
            "routeId": route_id,
            "sourcePair": pair_text(source_pair),
            "submissionId": submission_id,
        })
    sources.sort(key=lambda row: (int(row["label"][3:]), row["r"]))
    return sources, mapping


def make_runbook(sources_by_pair: dict[tuple[str, int], dict]) -> dict:
    selected_pairs = [("24T10408", 12), ("24T17207", 8)]
    selected = [sources_by_pair[pair] for pair in selected_pairs]
    action_command = (
        "test ! -e data/low_contention_tc2_even_twist_action_subset.jsonl && "
        "/usr/local/bin/sage -python build_even_twist_action_subset.sage.py "
        "--db data/ledger.sqlite3 "
        "--output data/low_contention_tc2_even_twist_action_subset.jsonl "
        "--source-label 24T10408 --source-label 24T17207"
    )
    execute_command = (
        "test ! -e data/low_contention_tc2_even_twist_results.jsonl && "
        "test ! -e outbox/low_contention_tc2_even_twist_safe.txt && "
        "test ! -e data/low_contention_tc2_even_twist_summary.json && "
        "/usr/local/bin/sage -python even_twist_delta_scan.sage.py "
        "data/ledger.sqlite3 data/low_contention_tc2_even_twist_action_subset.jsonl "
        "data/low_contention_tc2_even_twist_results.jsonl "
        "outbox/low_contention_tc2_even_twist_safe.txt "
        "--summary-output data/low_contention_tc2_even_twist_summary.json "
        "--synced-after 1970-01-01T00:00:00Z --max-candidates 4 "
        + " ".join(f"--source-hash {row['coefficientSha256']}" for row in selected)
    )
    return {
        "schemaVersion": "low-contention-tc2-even-twist-guarded-runbook-v1",
        "status": "prepared_execution_held_for_existing_heavy_lease",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sources": [
            {
                "coefficientSha256": row["coefficientSha256"],
                "exactNegativeR": 12 if row["label"] == "24T10408" else 4,
                "label": row["label"],
                "polynomialIndex": row["polynomialIndex"],
                "r": row["r"],
                "submissionId": row["submissionId"],
            }
            for row in selected
        ],
        "inputs": {
            "actionBuilder": artifact(TWIST_BUILDER),
            "ledger": {"path": str(DB.relative_to(ROOT))},
            "twistWorker": artifact(TWIST_WORKER),
        },
        "absentOutputGuard": {
            "allAbsentAtPreparation": all(
                not path.exists() for path in (ACTION_OUTPUT, TWIST_OUTPUT, TWIST_MANIFEST, TWIST_SUMMARY)
            ),
            "paths": [
                str(path.relative_to(ROOT))
                for path in (ACTION_OUTPUT, TWIST_OUTPUT, TWIST_MANIFEST, TWIST_SUMMARY)
            ],
        },
        "commands": {
            "buildTwoLabelActionSubset": action_command,
            "executeTwoHashPinnedTwists": execute_command,
        },
        "launchGate": "only after the coordinator confirms the sole Sage/GAP heavy slot is free",
        "postRun": (
            "Refresh ledger and receipts, then pass the result through the receipt-aware "
            "SINGLE exact census; never submit an unresolved or excluded pair/hash."
        ),
        "sideEffectsAtPreparation": {
            "heavyWorkersLaunched": 0,
            "ledgerWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }


def main() -> int:
    for path in (
        MAPPING, NEGATIVE_AUDIT, TWIST_ACTION, TWIST_BUILDER, TWIST_WORKER,
        LOWER_HISTORICAL, LOWER_PAIR_ACTIONS, LOWER_PAIR_ROUTES,
        LOWER_SUBSET_ACTIONS, LOWER_SUBSET_ROUTES, HIGHER_RESULTS, CHARACTER,
        TRIPLE_CURRENT, ORDERED_CEILING, COMPOSITA, ISOMORPHISM,
        *FULL_PAIR_ACTIONS, *TRIPLE_ORBITS, *ORDERED,
    ):
        if not path.is_file():
            raise ValueError(f"missing audit input: {path}")
    ast.parse(TWIST_BUILDER.read_text(encoding="utf-8"), str(TWIST_BUILDER))
    ast.parse(TWIST_WORKER.read_text(encoding="utf-8"), str(TWIST_WORKER))

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        sources, mapping = validate_boundary(connection)
        source_pairs = {(row["label"], int(row["r"])) for row in sources}
        source_hashes = {row["coefficientSha256"] for row in sources}
        source_keys = {
            (row["submissionId"], int(row["polynomialIndex"]), row["label"], int(row["r"]))
            for row in sources
        }
        source_id_keys = {(row[0], row[1]) for row in source_keys}
        sources_by_pair = {(row["label"], int(row["r"])): row for row in sources}
        if len(source_pairs) != 14 or len(source_hashes) != 14 or len(source_keys) != 14:
            raise ValueError("tc2 accepted source boundary is not exactly 14 distinct rows")

        baseline = {(str(row[0]), int(row[1])) for row in connection.execute("SELECT label,r FROM baseline_pairs")}
        owned = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT DISTINCT label,r FROM verifications WHERE scoreable=1")
        }
        known_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE label IS NOT NULL AND r IS NOT NULL"
            )
        }
        ledger_hashes = {str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")}
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, {}
        )
        staged_hashes = outbox_hashes()
        targets = {
            (str(row["label"]), int(row["r"])): {
                "discovered": bool(row["discovered"]),
                "teamCount": int(row["team_count"]),
            }
            for row in connection.execute("SELECT label,r,discovered,team_count FROM targets")
        }

        # Allowlisted exact artifact rejoin: SINGLE, stable MULTI, exact
        # Frobenius, and all-compatible unresolved Frobenius packets.
        single_candidates, single_audit = single.scan_candidates(DATA)
        single_hits = []
        for candidate in single_candidates:
            pins = {
                (str(pin["submissionId"]), int(pin["polynomialIndex"]))
                for pin in candidate.get("sourcePins") or []
            }
            if pins & source_id_keys:
                single_hits.append(candidate)

        stable_pool, stable_audit = shared.scan_stable_multi(DATA, DATA / "pair_signature_map.jsonl")
        stable_hits = [row for row in stable_pool.values() if row["sourceKeys"] & source_keys]
        frobenius_certificates, _ = all_exact.scan_json_artifacts(DATA)
        unresolved_pool, unresolved_audit = shared.unresolved_options(frobenius_certificates, ROOT)
        unresolved_hits = [row for row in unresolved_pool.values() if row["sourceKey"] in source_keys]
        frobenius_pool, frobenius_certificate_audit, frobenius_audit = all_exact.collect_exact_pool(
            frobenius_certificates, connection, ROOT, None
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
            raise ValueError("an already-exact tc2-derived row requires staging before closure")

        negative_rows = read_jsonl(NEGATIVE_AUDIT)
        negative_direct_hashes = {
            str(row.get("sourceCoefficientSha256"))
            for row in negative_rows
            if str(row.get("sourceCoefficientSha256")) in source_hashes
        }
        twist_actions = {
            str(row["sourceLabel"]): row for row in read_jsonl(TWIST_ACTION)
        }
        negative_dispositions = []
        missing_twist_action_pairs = []
        for source in sources:
            if not source["evenModel"]:
                continue
            row = connection.execute(
                "SELECT coefficients FROM polynomials WHERE submission_id=? AND polynomial_index=?",
                (source["submissionId"], int(source["polynomialIndex"])),
            ).fetchone()
            values = [int(value) for value in str(row[0]).split(",")]
            quotient_roots, sturm_length = exact_real_root_count(values[::2])
            negative_r = 2 * quotient_roots - int(source["r"])
            action = twist_actions.get(source["label"])
            if action is None:
                missing_twist_action_pairs.append((source["label"], int(source["r"])))
                negative_dispositions.append({
                    "sourcePair": pair_text((source["label"], int(source["r"]))),
                    "exactNegativeR": negative_r,
                    "quotientRealRoots": quotient_roots,
                    "sturmSequenceLength": sturm_length,
                    "status": "exact_signature_cached_action_missing_runbook_prepared",
                })
                continue
            labels = {str(value) for value in action.get("targetLabels") or []}
            if int(action.get("systemCount", -1)) != len(action.get("systems") or []) or len(labels) != 1:
                raise ValueError("cached twist action is not uniquely target-labeled")
            target_pair = (next(iter(labels)), negative_r)
            disposition = classify_pair(
                target_pair, baseline=baseline, owned=owned, known=known_pairs,
                receipt_pairs=receipt_pairs,
            )
            negative_dispositions.append({
                "actionRowSha256": canonical_row_sha256(action),
                "sourcePair": pair_text((source["label"], int(source["r"]))),
                "targetPair": pair_text(target_pair),
                "targetDisposition": disposition,
                "status": "cached_exact_action_closed" if disposition != "safe_at_snapshot" else "cached_exact_action_safe",
            })
            if disposition == "safe_at_snapshot":
                raise ValueError("one cached exact negative twist remains safe and unstaged")
        if set(missing_twist_action_pairs) != {("24T10408", 12), ("24T17207", 8)}:
            raise ValueError("unexpected missing twist-action frontier")

        old_kummer_paths = (
            LOWER_HISTORICAL, LOWER_PAIR_ACTIONS, LOWER_PAIR_ROUTES,
            LOWER_SUBSET_ACTIONS, LOWER_SUBSET_ROUTES, HIGHER_RESULTS,
        )
        old_kummer_matches = {}
        for path in old_kummer_paths:
            rows = read_jsonl(path)
            pair_matches = {pair for row in rows if (pair := direct_source_pair(row)) in source_pairs}
            hash_matches = {
                digest for row in rows for digest in recursive_source_hashes(row) if digest in source_hashes
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
        for path in FULL_PAIR_ACTIONS:
            rows = read_jsonl(path)
            full_pair_rows.extend(rows)
            full_pair_artifacts.append({**artifact(path), "rows": len(rows)})
        full_pair_routes = []
        executable_safe_pair_routes = 0
        for row in full_pair_rows:
            label = str(row.get("sourceLabel"))
            matches = [pair for pair in source_pairs if pair[0] == label]
            for source_pair in matches:
                possible = {
                    (str(row["targetLabel"]), int(r_value))
                    for r_value in (row.get("sourceSignatureToPossibleTargetSignatures") or {}).get(
                        str(source_pair[1]), []
                    )
                }
                source = sources_by_pair[source_pair]
                dispositions = Counter(
                    classify_pair(
                        pair, baseline=baseline, owned=owned, known=known_pairs,
                        receipt_pairs=receipt_pairs,
                    )
                    for pair in possible
                )
                literal = bool(source["evenModel"])
                safe = dispositions.get("safe_at_snapshot", 0)
                if literal and safe:
                    executable_safe_pair_routes += 1
                full_pair_routes.append({
                    "actionRowSha256": canonical_row_sha256(row),
                    "currentAnchorIsLiteralEvenModel": literal,
                    "possiblePairCount": len(possible),
                    "possiblePairDispositions": dict(sorted(dispositions.items())),
                    "sourcePair": pair_text(source_pair),
                    "targetLabel": str(row["targetLabel"]),
                })
        if executable_safe_pair_routes:
            raise ValueError("one literal lower-Kummer pair-product route remains executable")

        character_rows = read_jsonl(CHARACTER)
        character_label_rows = [row for row in character_rows if str(row.get("sourceLabel")) in {p[0] for p in source_pairs}]
        character_hash_rows = [row for row in character_rows if str(row.get("coefficientSha256")) in source_hashes]
        character_statuses = Counter(str(row.get("status")) for row in character_label_rows)
        if character_hash_rows:
            raise ValueError("current tc2 hash unexpectedly has a cached character alignment")

        triple_current_rows = read_jsonl(TRIPLE_CURRENT)
        triple_current_pairs = {
            pair for row in triple_current_rows if (pair := direct_source_pair(row)) in source_pairs
        }
        triple_orbit_matches = []
        for path in TRIPLE_ORBITS:
            for row in read_jsonl(path):
                source_pair_matches = [pair for pair in source_pairs if pair[0] == str(row.get("sourceLabel"))]
                for source_pair in source_pair_matches:
                    targets_for_row = {str(value["targetLabel"]) for value in row.get("targets") or []}
                    disposition = "unprofiled"
                    if source_pair[1] == 24 and targets_for_row == {source_pair[0]}:
                        disposition = "all_real_self_pair_owned"
                    triple_orbit_matches.append({
                        "artifact": str(path.relative_to(ROOT)),
                        "sourcePair": pair_text(source_pair),
                        "targetLabels": sorted(targets_for_row),
                        "disposition": disposition,
                    })
        if triple_current_pairs or any(row["disposition"] != "all_real_self_pair_owned" for row in triple_orbit_matches):
            raise ValueError("triple frontier contains an unresolved cached direct route")

        ceiling = json.loads(ORDERED_CEILING.read_text(encoding="utf-8"))
        if ceiling.get("status") != "theorem_certified_route_ceiling":
            raise ValueError("ordered-pair theorem ceiling is not certified")
        ordered_label_rows = 0
        for path in ORDERED:
            for row in read_jsonl(path):
                if str(row.get("sourceLabel")) in {pair[0] for pair in source_pairs}:
                    ordered_label_rows += 1

        composita = json.loads(COMPOSITA.read_text(encoding="utf-8"))
        composite_rows = composita.get("pilot") or []
        composite_direct_hashes = set()
        composite_direct_pairs = set()
        composite_dispositions = Counter()
        for row in composite_rows:
            if row.get("status") != "certified_simple_compositum":
                raise ValueError("compositum pilot contains an uncertified row")
            for side in (row.get("first") or {}, row.get("second") or {}):
                if str(side.get("sourceCoefficientSha256")) in source_hashes:
                    composite_direct_hashes.add(str(side["sourceCoefficientSha256"]))
                pair = single.valid_pair(side.get("sourceLabel"), side.get("sourceR"))
                if pair in source_pairs:
                    composite_direct_pairs.add(pair)
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            composite_dispositions[classify_pair(
                pair, baseline=baseline, owned=owned, known=known_pairs,
                receipt_pairs=receipt_pairs,
            )] += 1
        if composite_direct_hashes or composite_direct_pairs or composite_dispositions.get("safe_at_snapshot", 0):
            raise ValueError("simple-compositum frontier contains an unclosed route")

        isomorphism_rows = read_jsonl(ISOMORPHISM)
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
                    inverse_routes.append({
                        "sourcePair": pair_text(source_pair),
                        "inversePairs": sorted(pair_text(pair) for pair in possible),
                    })
        if direct_isomorphisms or not inverse_pairs <= (baseline | owned | known_pairs | receipt_pairs):
            raise ValueError("isomorphism frontier contains an unexcluded exact route")

        runbook = make_runbook(sources_by_pair)
        if runbook["absentOutputGuard"]["allAbsentAtPreparation"] is not True:
            raise ValueError("guarded twist outputs already exist")
        write_new(RUNBOOK, runbook)

        certificate = {
            "schemaVersion": "low-contention-tc2-light-nonpair-closure-v1",
            "status": "zero_already_exact_rows_two_source_guarded_twist_runbook_prepared",
            "coefficientMaterialIncluded": False,
            "credentialMaterialIncluded": False,
            "sourceBoundary": {
                "acceptedScoreableSignatures": len(source_pairs),
                "acceptedScoreableAnchorHashes": len(source_hashes),
                "evenLiteralModels": sum(bool(row["evenModel"]) for row in sources),
                "mapping": artifact(MAPPING),
                "receiptBatchSplit": "1+13",
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
                    "single": {"pool": len(single_candidates), "hits": 0, "audit": single_audit},
                    "stableMulti": {"pool": len(stable_pool), "hits": 0, "audit": stable_audit},
                    "exactFrobenius": {
                        "pool": len(frobenius_pool), "hits": 0,
                        "audit": frobenius_audit,
                        "certificateAudit": frobenius_certificate_audit,
                    },
                    "unresolvedFrobenius": {"pool": len(unresolved_pool), "hits": 0, "audit": unresolved_audit},
                },
                "cachedNegativeTwists": {
                    "artifact": artifact(NEGATIVE_AUDIT),
                    "actionMap": artifact(TWIST_ACTION),
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
                    "heldNonliteralStructuralLead": "24T19983/r24 -> 24T15887/r24",
                    "remainingAlreadyExactSafeRows": 0,
                },
                "characterQuotients": {
                    "artifact": artifact(CHARACTER),
                    "directCurrentAnchorHashRows": len(character_hash_rows),
                    "historicalSameLabelRows": len(character_label_rows),
                    "historicalStatusCounts": dict(sorted(character_statuses.items())),
                    "status": "no_cached_current_anchor_alignment",
                },
                "unorderedTriples": {
                    "currentProfileDirectPairs": len(triple_current_pairs),
                    "groupOrbitMatches": triple_orbit_matches,
                    "status": "cached_all_real_self_routes_owned_other_sources_unmatched",
                    "artifacts": [artifact(TRIPLE_CURRENT), *[artifact(path) for path in TRIPLE_ORBITS]],
                },
                "orderedPairs": {
                    "status": "theorem_closed_to_source_pair_identity",
                    "cachedSameLabelRows": ordered_label_rows,
                    "ceiling": artifact(ORDERED_CEILING),
                    "shards": [artifact(path) for path in ORDERED],
                    "remainingExactSafeRows": 0,
                },
                "simpleComposita": {
                    "status": "no_direct_source_match_all_pilot_targets_excluded",
                    "artifact": artifact(COMPOSITA),
                    "certifiedPilotRows": len(composite_rows),
                    "targetDispositions": dict(sorted(composite_dispositions.items())),
                },
                "index24Isomorphisms": {
                    "status": "all_inverse_routes_excluded_no_direct_routes",
                    "artifact": artifact(ISOMORPHISM),
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
                "guardedRunbookSourceHashes": 2,
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

    write_new(CERTIFICATE, certificate)
    summary = {
        "schemaVersion": "low-contention-tc2-light-nonpair-closure-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "acceptedScoreableSignaturesAudited": 14,
        "alreadyExactSafeRows": 0,
        "guardedRunbooksPrepared": 1,
        "guardedRunbookSourceHashes": 2,
        "heavyWorkersLaunched": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
        "certificate": artifact(CERTIFICATE),
        "runbook": artifact(RUNBOOK),
    }
    write_new(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

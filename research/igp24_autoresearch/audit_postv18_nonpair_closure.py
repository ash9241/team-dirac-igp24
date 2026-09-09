#!/usr/bin/env python3
"""Light-only non-pair closure for the 25 post-v17 receipt rows."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import audit_low_contention_tc7_tc9_routes as exclusion_helper
import audit_tc2_nonpair_closure as prior_audit
import stage_all_exact_frobenius_unowned as all_exact
import stage_exact_shared_census as shared
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
OUTBOX = ROOT / "outbox"
CERTIFICATE = DATA / "postv18_nonpair_closure_certificate.json"
SUMMARY = DATA / "postv18_nonpair_closure_summary.json"
MANIFEST = OUTBOX / "postv18_nonpair_exact_hits.txt"
RUNBOOK = DATA / "postv18_even_twist_guarded_runbook.json"

SUBMISSIONS = {
    "sub_6eeaa15d8d994fd19646243de90b4b09": 3,
    "sub_bc7a3dd9294543bd813bed8ba413972b": 14,
    "sub_ebc16254512a411b902ac0212d62f21b": 8,
}

ACTION_OUTPUT = DATA / "postv18_even_twist_action_subset.jsonl"
TWIST_OUTPUT = DATA / "postv18_even_twist_results.jsonl"
TWIST_MANIFEST = OUTBOX / "postv18_even_twist_safe.txt"
TWIST_SUMMARY = DATA / "postv18_even_twist_summary.json"

FAMILY_PATHS = {
    "negative_twist": (DATA / "agent_f7_negative_twist_signature_audit.jsonl",),
    "even_twist_actions": (DATA / "agent_gold_b_even_twist_action_map.jsonl",),
    "lower_kummer_historical": (DATA / "agent_gold_c_lower_kummer_historical_census.jsonl",),
    "lower_kummer_pair": (
        DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl",
        DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl",
    ),
    "lower_kummer_subset": (
        DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl",
        DATA / "agent_gold_c_lower_kummer_subset_product_live_routes_v2.jsonl",
    ),
    "full_pair_alternate_actions": tuple(
        DATA / f"agent_f5_full_ledger_pair_product_actions_shard{i}of4.jsonl"
        for i in range(4)
    ) + (
        DATA / "agent_gold_b_combined_pair_orbit_map.jsonl",
        DATA / "agent_page19_pair_orbit_combined.jsonl",
        DATA / "autopilot_f5_newsource_action_map_v1.jsonl",
    ),
    "higher_kummer_triple": (DATA / "agent_f9_higher_kummer_triple_pilot_results.jsonl",),
    "character": (DATA / "agent_gold_c_character_census_alignment_results.jsonl",),
    "triple": (DATA / "agent_gold_a_triple_current_profiles.jsonl",) + tuple(
        DATA / f"agent_gold_a_triple_orbit_shard{i}.jsonl" for i in range(4)
    ),
    "ordered": tuple(DATA / f"agent_index24_ordered_pair_shard{i}.jsonl" for i in range(6)),
    "compositum": (DATA / "agent_non12_simple_compositum_pilot.json",),
    "isomorphism": (DATA / "agent_index24_isomorphism_complete.jsonl",),
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path) -> dict:
    return {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_path(path)}


def write_new_json(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    text = payload.decode("utf-8")
    if prior_audit.COEFFICIENT_RE.search(text) or "bearer " in text.lower():
        raise ValueError("coefficient or credential material entered audit")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
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


def write_manifest(path: Path, lines: list[str]) -> dict | None:
    if not lines:
        return None
    payload = ("\n".join(lines) + "\n").encode("ascii")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
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
    return {**artifact(path), "polynomials": len(lines), "bytes": len(payload)}


def recursive_objects(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            stack.extend(child for child in item.values() if isinstance(child, (dict, list)))
        elif isinstance(item, list):
            stack.extend(item)


def iter_artifact_objects(path: Path):
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield from recursive_objects(json.loads(line))
    else:
        yield from recursive_objects(json.loads(path.read_text(encoding="utf-8")))


def family_audit(source_pairs: set, source_hashes: set) -> dict:
    result = {}
    for family, paths in FAMILY_PATHS.items():
        pair_objects = 0
        hash_objects = 0
        artifacts = []
        for path in paths:
            if not path.is_file():
                continue
            objects = 0
            for row in iter_artifact_objects(path):
                objects += 1
                pair = prior_audit.direct_source_pair(row)
                hashes = prior_audit.recursive_source_hashes(row)
                if pair in source_pairs:
                    pair_objects += 1
                if hashes & source_hashes:
                    hash_objects += 1
            artifacts.append({**artifact(path), "objects": objects})
        result[family] = {
            "artifacts": artifacts,
            "directSourcePairObjects": pair_objects,
            "directAnchorHashObjects": hash_objects,
        }
    return result


def main() -> int:
    for path in (CERTIFICATE, SUMMARY, MANIFEST, RUNBOOK):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        sources = []
        source_keys = set()
        source_hashes = set()
        source_pairs = set()
        for submission_id, expected in SUBMISSIONS.items():
            submission = connection.execute(
                "SELECT queued_count,verified_count,failed_count FROM submissions WHERE submission_id=?",
                (submission_id,),
            ).fetchone()
            rows = list(connection.execute(
                "SELECT p.submission_id,p.polynomial_index,p.coefficient_hash,p.coefficients,"
                "v.label,v.r,v.status,v.scoreable,v.in_baseline,v.scoring_status "
                "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
                "WHERE p.submission_id=? ORDER BY p.polynomial_index", (submission_id,),
            ))
            if submission is None or tuple(map(int, submission)) != (0, expected, 0) or len(rows) != expected:
                raise ValueError(f"receipt boundary incomplete: {submission_id}")
            for row in rows:
                key = (str(row["submission_id"]), int(row["polynomial_index"]))
                pair = (str(row["label"]), int(row["r"]))
                digest = str(row["coefficient_hash"])
                if (
                    str(row["status"]) != "accepted" or int(row["scoreable"]) != 1
                    or int(row["in_baseline"]) != 0 or str(row["scoring_status"]) != "scoreable"
                    or hashlib.sha256(str(row["coefficients"]).encode("ascii")).hexdigest() != digest
                ):
                    raise ValueError(f"source is not exact accepted-scoreable: {key}")
                coefficients = [int(value) for value in str(row["coefficients"]).split(",")]
                even = len(coefficients) == 25 and all(coefficients[i] == 0 for i in range(1, 25, 2))
                sources.append({"submissionId": key[0], "polynomialIndex": key[1], "coefficientSha256": digest, "label": pair[0], "r": pair[1], "evenModel": even})
                source_keys.add(key)
                source_hashes.add(digest)
                source_pairs.add(pair)
        if len(sources) != 25 or len(source_keys) != 25 or len(source_hashes) != 25 or len(source_pairs) != 25:
            raise ValueError("25-row source boundary is not key/hash/pair distinct")

        baseline = {(str(label), int(r)) for label, r in connection.execute("SELECT label,r FROM baseline_pairs")}
        owned = {(str(label), int(r)) for label, r in connection.execute("SELECT DISTINCT label,r FROM verifications WHERE status='accepted' AND scoreable=1")}
        known = {(str(label), int(r)) for label, r in connection.execute("SELECT DISTINCT label,r FROM verifications WHERE label IS NOT NULL AND r IS NOT NULL")}
        ledger_hashes = {str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")}
        targets = {
            (str(row["label"]), int(row["r"])): {"teamCount": int(row["team_count"]), "discovered": bool(row["discovered"]), "minimumDiscAbs": str(row["minimum_disc_abs"]) if row["minimum_disc_abs"] is not None else None, "generatedAt": str(row["generated_at"])}
            for row in connection.execute("SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at FROM targets")
        }

        single_candidates, single_corpus = single.scan_candidates(DATA)
        certificates, _ = all_exact.scan_json_artifacts(DATA)
        frobenius_pool, frobenius_certificates, frobenius_census = all_exact.collect_exact_pool(certificates, connection, ROOT, None)
        exact_pool, stable_census = shared.scan_stable_multi(DATA, shared.SIGNATURES)
        shared.add_frobenius_exact(exact_pool, frobenius_pool)

        normalized = []
        candidate_pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for candidate in single_candidates:
            digest = str(candidate["coefficientSha256"])
            pair = (str(candidate["targetLabel"]), int(candidate["targetR"]))
            candidate_pair_index[digest].add(pair)
            pins = candidate.get("sourcePins") or []
            if not single.validate_source_pins(connection, candidate):
                continue
            if not any(
                (str(pin["submissionId"]), int(pin["polynomialIndex"])) in source_keys
                and str(pin["coefficientSha256"]) in source_hashes
                for pin in pins
            ):
                continue
            normalized.append({
                "coefficientLine": candidate["coefficientLine"], "coefficientSha256": digest,
                "coefficientBytes": candidate["coefficientBytes"], "pair": pair,
                "fieldDiscriminantAbs": candidate.get("fieldDiscriminantAbs"),
                "polynomialDiscriminantAbs": candidate.get("polynomialDiscriminantAbs"),
                "families": {str((candidate.get("proof") or {}).get("schema"))},
                "proofs": [candidate.get("proof") or {}],
            })
        for digest, row in exact_pool.items():
            candidate_pair_index[digest].add(row["pair"])
            if not any((key[0], key[1]) in source_keys for key in row["sourceKeys"]):
                continue
            normalized.append({**row, "families": set(row["families"])})

        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(RECEIPTS, DATA, connection, candidate_pair_index)
        supplemental, supplemental_artifacts = exclusion_helper.supplemental_exact_pair_index()
        for digest, pairs in supplemental.items():
            candidate_pair_index[digest].update(pairs)
        outbox_hashes = prior_audit.outbox_hashes()
        outbox_pairs = set()
        for digest in outbox_hashes:
            outbox_pairs.update(candidate_pair_index.get(digest, set()))

        skip = Counter()
        eligible = []
        for row in normalized:
            digest = str(row["coefficientSha256"])
            pair = tuple(row["pair"])
            reason = None
            if digest in ledger_hashes: reason = "ledger_hash"
            elif digest in receipt_hashes: reason = "receipt_hash"
            elif digest in outbox_hashes: reason = "outbox_hash"
            elif pair in baseline: reason = "baseline_pair"
            elif pair in owned: reason = "owned_pair"
            elif pair in known: reason = "known_pair"
            elif pair in receipt_pairs: reason = "receipt_pair"
            elif pair in outbox_pairs: reason = "outbox_pair"
            elif pair not in targets: reason = "target_missing"
            if reason:
                skip[reason] += 1
                continue
            eligible.append(row)

        best_by_pair = {}
        for row in sorted(eligible, key=lambda item: (
            targets[item["pair"]]["teamCount"],
            int(item.get("fieldDiscriminantAbs") or 10**1000),
            int(item.get("polynomialDiscriminantAbs") or 10**1000),
            int(item["coefficientBytes"]), str(item["coefficientSha256"]),
        )):
            best_by_pair.setdefault(tuple(row["pair"]), row)
        selected = list(best_by_pair.values())
        if len({row["coefficientSha256"] for row in selected}) != len(selected):
            raise ValueError("selected exact hits are not hash distinct")
        manifest = write_manifest(MANIFEST, [str(row["coefficientLine"]) for row in selected])

        action_labels = {str(row["sourceLabel"]) for row in prior_audit.read_jsonl(prior_audit.TWIST_ACTION)}
        negative_by_hash = {
            str(row.get("sourceCoefficientSha256")): row
            for row in prior_audit.read_jsonl(prior_audit.NEGATIVE_AUDIT)
        }
        twist_sources = []
        for source in sources:
            if not source["evenModel"] or source["label"] not in action_labels:
                continue
            cached = negative_by_hash.get(source["coefficientSha256"])
            twist_sources.append({
                **source,
                "cachedNegativeR": int(cached["negativeTwistRealRootCount"]) if cached else None,
                "priority": 0 if cached else 1,
            })
        twist_sources.sort(key=lambda row: (row["priority"], int(row["label"][3:]), row["r"]))
        labels = sorted({row["label"] for row in twist_sources}, key=lambda value: int(value[3:]))
        action_command = None
        worker_command = None
        if twist_sources:
            action_command = (
                "test ! -e data/postv18_even_twist_action_subset.jsonl && "
                "/usr/local/bin/sage -python build_even_twist_action_subset.sage.py "
                "--db data/ledger.sqlite3 --output data/postv18_even_twist_action_subset.jsonl "
                + " ".join(f"--source-label {label}" for label in labels)
            )
            worker_command = (
                "test ! -e data/postv18_even_twist_results.jsonl && "
                "test ! -e outbox/postv18_even_twist_safe.txt && "
                "test ! -e data/postv18_even_twist_summary.json && "
                "/usr/local/bin/sage -python even_twist_delta_scan.sage.py "
                "data/ledger.sqlite3 data/postv18_even_twist_action_subset.jsonl "
                "data/postv18_even_twist_results.jsonl outbox/postv18_even_twist_safe.txt "
                "--summary-output data/postv18_even_twist_summary.json "
                "--synced-after 1970-01-01T00:00:00Z "
                f"--max-candidates {2 * len(twist_sources)} "
                + " ".join(f"--source-hash {row['coefficientSha256']}" for row in twist_sources)
            )
        runbook = {
            "schemaVersion": "postv18-even-twist-guarded-runbook-v1",
            "status": "ready_waiting_for_root_heavy_clearance" if twist_sources else "certified_no_even_twist_sources",
            "sources": [{key: row[key] for key in ("submissionId", "polynomialIndex", "coefficientSha256", "label", "r", "cachedNegativeR", "priority")} for row in twist_sources],
            "actionCommand": action_command, "workerCommand": worker_command,
            "outputsAbsentAtSeal": all(not path.exists() for path in (ACTION_OUTPUT, TWIST_OUTPUT, TWIST_MANIFEST, TWIST_SUMMARY)),
            "submissionAuthorized": False, "coefficientMaterialIncluded": False,
            "sideEffects": {"sageRuns": 0, "gapRuns": 0, "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
        }
        write_new_json(RUNBOOK, runbook)

        families = family_audit(source_pairs, source_hashes)
    finally:
        connection.close()

    public_selected = [{
        "coefficientSha256": row["coefficientSha256"], "pair": prior_audit.pair_text(row["pair"]),
        "families": sorted(row["families"]), "coefficientBytes": row["coefficientBytes"],
        "teamCountAtSeal": targets[row["pair"]]["teamCount"],
        "projectedMarginalScoreExact": str(Fraction(1, 2 ** targets[row["pair"]]["teamCount"])),
        "proofArtifacts": sorted({str(proof.get("artifact")) for proof in row["proofs"] if proof.get("artifact")}),
    } for row in selected]
    certificate = {
        "schemaVersion": "postv18-combined-nonpair-closure-v1",
        "status": "certified_exact_hits_staged" if selected else "certified_no_cached_exact_hits_guarded_runbooks_ready",
        "sourceBoundary": {"submissionCounts": SUBMISSIONS, "rows": len(sources), "distinctKeys": len(source_keys), "distinctHashes": len(source_hashes), "distinctPairs": len(source_pairs)},
        "exactCacheCensus": {
            "single": single_corpus, "stableMulti": stable_census,
            "exactFrobenius": frobenius_census, "exactFrobeniusCertificates": frobenius_certificates,
            "normalizedDirectSourceCandidates": len(normalized), "eligibleBeforePairDedup": len(eligible),
            "skipCounts": dict(sorted(skip.items())), "selectedExactHits": len(selected),
        },
        "exclusions": {
            "ledgerHashes": len(ledger_hashes), "receiptHashes": len(receipt_hashes), "receiptPairs": len(receipt_pairs),
            "outboxHashes": len(outbox_hashes), "outboxMappedPairs": len(outbox_pairs),
            "receiptAudit": {key: value for key, value in receipt_audit.items() if key != "audit"},
            "supplementalExactPairMaps": supplemental_artifacts,
        },
        "selectedExactHits": public_selected, "manifest": manifest,
        "familyDirectMatchAudit": families, "evenTwistRunbook": artifact(RUNBOOK),
        "checks": {
            "sourceBoundary25ExactAcceptedScoreable": len(sources) == len(source_keys) == len(source_hashes) == len(source_pairs) == 25,
            "everySelectedHashNovel": all(row["coefficientSha256"] not in ledger_hashes | receipt_hashes | outbox_hashes for row in selected),
            "everySelectedPairSafe": all(row["pair"] not in baseline | owned | known | receipt_pairs | outbox_pairs for row in selected),
            "selectedHashesAndPairsDistinct": len(selected) == len({row["coefficientSha256"] for row in selected}) == len({row["pair"] for row in selected}),
            "manifestMatchesSelection": (manifest is None and not selected) or (manifest is not None and manifest["polynomials"] == len(selected)),
            "coefficientMaterialExcludedFromCertificate": True,
        },
        "coefficientMaterialIncluded": False, "credentialMaterialIncluded": False,
        "sideEffects": {"sageRuns": 0, "gapRuns": 0, "networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    if not all(certificate["checks"].values()):
        raise ValueError("combined nonpair audit check failed")
    write_new_json(CERTIFICATE, certificate)
    summary = {
        "schemaVersion": "postv18-combined-nonpair-summary-v1", "status": certificate["status"],
        "certificate": artifact(CERTIFICATE), "sourceRows": 25, "cachedExactHits": len(selected),
        "manifest": manifest, "evenTwistSources": len(twist_sources), "evenTwistRunbook": artifact(RUNBOOK),
        "coefficientMaterialIncluded": False, "heavyWorkerLaunched": False, "networkCalls": 0, "submissionCalls": 0,
    }
    write_new_json(SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

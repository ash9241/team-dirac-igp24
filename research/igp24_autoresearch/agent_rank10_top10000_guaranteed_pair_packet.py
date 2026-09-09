#!/usr/bin/env python3
"""Seal the guaranteed T00134 top-10,000 pair-orbit run packet.

This builder is deliberately light-only.  It validates saved exact action and
complex-conjugation evidence, joins the pinned structural frontier to current
accepted ledger presentations, applies the current target-pair boundary and
actual pair-test evidence, and emits commands without executing them.

It has no Sage/GAP, network, staging, or submission path.
"""

from __future__ import annotations

import hashlib
import json
import math
import shlex
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import analyze_rank12_routes as shared
import fresh_t00134_pair_orbit_census as sole
import fresh_t00134_top10000_pair_orbit_census as top10000_census  # noqa: F401
import fresh_t00134_top5000_pair_orbit_census as census


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
STRUCTURAL = DATA / "rank10_t00134_top10000_structural_frontier_20260730.jsonl"
STRUCTURAL_SUMMARY = (
    DATA / "rank10_t00134_top10000_structural_frontier_20260730_summary.json"
)
PAIR_FRONTIER = DATA / "fresh_t00134_top10000_pair_orbit_frontier.jsonl"
PAIR_SUMMARY = DATA / "fresh_t00134_top10000_pair_orbit_summary.json"
ACTION_MAP = DATA / "pair_orbit_map.jsonl"
PROFILE_CACHE = DATA / "fresh_t00134_top10000_pair_orbit_profiles.jsonl"
RUNTIME_PACKET = DATA / "fresh_current_gold_pair_ranked_packet_20260730.json"
OUTPUT = (
    DATA / "rank10_t00134_top10000_guaranteed_pair_run_packet_20260730.jsonl"
)
SUMMARY = (
    DATA
    / "rank10_t00134_top10000_guaranteed_pair_run_packet_20260730_summary.json"
)

PINNED_SHA256 = {
    STRUCTURAL: "86721b7c11704cc94e5aa49410918ce0091eff7e26d82bc70a3c339fd6b773d5",
    STRUCTURAL_SUMMARY: (
        "c2894bf0f3f664b88e8826641882211123de6d618d2413cdab24b2b0cb6dd759"
    ),
    PAIR_FRONTIER: (
        "ed6d99fab70b39a58066d0d6b2398d0c9b0bfd779874e572a55745f148cf6841"
    ),
    PAIR_SUMMARY: (
        "0e583ec6861b194d5f2223cb7101d822570a13a31e1b0349fa6b0c970e5b8ab2"
    ),
    ACTION_MAP: "53655d70697f0af6ffa501ceedf385c81e874e8b92579ff05fdfebee43237b37",
    PROFILE_CACHE: (
        "8b67a455cc1dde62222b028c19e51ccf6af1ff581235efe967fe7cfa9e2752c8"
    ),
}
EXPECTED_TARGET_COUNT = 8
EXPECTED_PRESENTATION_COUNT = 16
FIXED_OVERHEAD_SECONDS = 1.8935
NEAREST_RUNTIME_SAMPLES = 15


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def canonical_sha256(value: object) -> str:
    rendered = json.dumps(
        value, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def line_sha256(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def validate_pins() -> dict[str, dict]:
    evidence = {}
    for path, expected in PINNED_SHA256.items():
        actual = shared.sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"pinned artifact drift: {relative(path)}={actual}, expected {expected}"
            )
        evidence[relative(path)] = {"path": relative(path), "sha256": actual}
    return evidence


def indexed_jsonl(path: Path, key: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        value = str(row[key])
        if value in indexed:
            raise ValueError(f"duplicate {key}={value} in {relative(path)}")
        indexed[value] = {
            "row": row,
            "line": line_number,
            "rowSha256": line_sha256(line),
        }
    return indexed


def structural_routes() -> tuple[list[dict], dict[tuple[str, int], dict]]:
    routes = []
    targets = {}
    for line_number, line in enumerate(
        STRUCTURAL.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        guaranteed = [
            route
            for route in row.get("pairOrbitRoutes", [])
            if route.get("guaranteed") is True
        ]
        if not guaranteed:
            continue
        target = row["targetPair"]
        pair = (str(target["label"]), int(target["r"]))
        if pair in targets:
            raise ValueError(f"duplicate guaranteed target pair {pair}")
        targets[pair] = {
            "target": target,
            "ratioOneReferenceRelativeSwing": float(
                row["ratioOneReferenceRelativeSwing"]
            ),
            "structuralLine": line_number,
            "structuralRowSha256": line_sha256(line),
        }
        for route in guaranteed:
            if (
                float(route["ratioOneReferenceRelativeSwing"])
                != float(row["ratioOneReferenceRelativeSwing"])
            ):
                raise ValueError("route/target ratio-one swing mismatch")
            routes.append(
                {
                    "targetPair": pair,
                    "target": target,
                    "ratioOneReferenceRelativeSwing": float(
                        row["ratioOneReferenceRelativeSwing"]
                    ),
                    "route": route,
                    "structuralLine": line_number,
                    "structuralRowSha256": line_sha256(line),
                }
            )
    if len(targets) != EXPECTED_TARGET_COUNT:
        raise ValueError(
            f"pinned frontier has {len(targets)} guaranteed target pairs, "
            f"expected {EXPECTED_TARGET_COUNT}"
        )
    if len(routes) != EXPECTED_PRESENTATION_COUNT:
        raise ValueError(
            f"pinned frontier has {len(routes)} guaranteed presentations, "
            f"expected {EXPECTED_PRESENTATION_COUNT}"
        )
    return routes, targets


def full_frontier_index() -> dict[tuple[str, int, str], dict]:
    result = {}
    for row in shared.read_jsonl(PAIR_FRONTIER):
        key = (
            str(row["submissionId"]),
            int(row["polynomialIndex"]),
            str(row["sourceCoefficientSha256"]),
        )
        if key in result:
            raise ValueError(f"duplicate presentation in {relative(PAIR_FRONTIER)}")
        result[key] = row
    return result


def profile_evidence(
    source_label: str,
    source_r: int,
    action: dict,
    action_entry: dict,
    profile_cache_index: dict[str, dict],
    profile_sources: dict[str, str],
) -> tuple[dict, dict]:
    if source_r == 24:
        profile = sole.identity_profile(action)
        return profile, {
            "kind": "identity_complex_conjugation",
            "proof": (
                "A totally real source has identity complex conjugation, so every "
                "unordered-pair orbit has targetR=24."
            ),
            "syntheticProfileSha256": canonical_sha256(profile),
            "actionMapCrossCheck": "exact_match",
        }

    cached_entry = profile_cache_index.get(source_label)
    if cached_entry is None:
        raise ValueError(f"missing consolidated exact profile for {source_label}")
    cached = shared.verified_profile(cached_entry["row"], action)
    origin_name = profile_sources.get(source_label)
    if not origin_name:
        raise ValueError(f"missing profile-source provenance for {source_label}")
    origin_path = ROOT / origin_name
    if not origin_path.exists():
        raise ValueError(f"profile origin is absent: {origin_name}")
    origin_index = indexed_jsonl(origin_path, "sourceLabel")
    origin_entry = origin_index.get(source_label)
    if origin_entry is None:
        raise ValueError(f"profile origin lacks {source_label}: {origin_name}")
    origin = shared.verified_profile(origin_entry["row"], action)
    if origin["profiles"] != cached["profiles"]:
        raise ValueError(f"origin/consolidated profiles differ for {source_label}")
    return cached, {
        "kind": "cached_conjugacy_profile",
        "status": str(cached["status"]),
        "actionMapCrossCheck": str(cached["actionMapCrossCheck"]),
        "consolidated": {
            "path": relative(PROFILE_CACHE),
            "sha256": shared.sha256_file(PROFILE_CACHE),
            "line": int(cached_entry["line"]),
            "rowSha256": str(cached_entry["rowSha256"]),
        },
        "origin": {
            "path": relative(origin_path),
            "sha256": shared.sha256_file(origin_path),
            "line": int(origin_entry["line"]),
            "rowSha256": str(origin_entry["rowSha256"]),
            "crossCheck": "exact_match",
        },
    }


def validate_exact_route(
    item: dict,
    ledger_row: dict,
    action_index: dict[str, dict],
    profile_cache_index: dict[str, dict],
    profile_sources: dict[str, str],
    full_index: dict[tuple[str, int, str], dict],
) -> dict:
    source = item["route"]["source"]
    target_pair = item["targetPair"]
    source_label = str(source["sourceLabel"])
    source_r = int(source["sourceR"])
    action_entry = action_index.get(source_label)
    if action_entry is None:
        raise ValueError(f"action map lacks {source_label}")
    action = action_entry["row"]
    if int(action["sourceT"]) != int(str(source_label).split("T", 1)[1]):
        raise ValueError(f"action source T mismatch for {source_label}")
    if any(int(target["kernelOrder"]) != 1 for target in action["targets"]):
        raise ValueError(f"nonfaithful length-24 action for {source_label}")

    key = (
        str(source["submissionId"]),
        int(source["polynomialIndex"]),
        str(source["sourceCoefficientSha256"]),
    )
    full = full_index.get(key)
    if full is None:
        raise ValueError(f"presentation absent from exact pair frontier: {key}")
    expected_source_fields = {
        "sourceLabel": source_label,
        "sourceR": source_r,
        "sourceCoefficientSha256": str(source["sourceCoefficientSha256"]),
        "sourceFieldDiscAbs": str(source["sourceFieldDiscAbs"]),
    }
    for field, expected in expected_source_fields.items():
        if str(full.get(field)) != str(expected):
            raise ValueError(f"full-frontier {field} mismatch for {key}")

    profile, profile_proof = profile_evidence(
        source_label,
        source_r,
        action,
        action_entry,
        profile_cache_index,
        profile_sources,
    )
    classes = [
        row
        for row in profile["profiles"]
        if int(row["sourceR"]) == source_r
    ]
    class_indexes = sorted(int(row["classIndex"]) for row in classes)
    if class_indexes != sorted(
        int(value) for value in item["route"]["compatibleClassIndexes"]
    ):
        raise ValueError(f"compatible-class mismatch for {key}")

    expected_orbit_evidence: dict[str, list[int]] = defaultdict(list)
    target_r_unique = True
    for class_row in classes:
        exact_matches = [
            signature
            for signature in class_row["orbitSignatures"]
            if (
                str(signature["targetLabel"]),
                int(signature["targetR"]),
            )
            == target_pair
        ]
        if not exact_matches:
            raise ValueError(f"target is not guaranteed in class {class_row}")
        for signature in exact_matches:
            expected_orbit_evidence[str(int(signature["orbitIndex"]))].append(
                int(class_row["classIndex"])
            )
        same_r = [
            signature
            for signature in class_row["orbitSignatures"]
            if int(signature["targetR"]) == target_pair[1]
        ]
        if len(same_r) != len(exact_matches):
            target_r_unique = False
    expected_orbit_evidence = {
        orbit: sorted(classes)
        for orbit, classes in sorted(
            expected_orbit_evidence.items(), key=lambda value: int(value[0])
        )
    }
    structural_evidence = {
        str(orbit): sorted(int(value) for value in classes)
        for orbit, classes in item["route"]["orbitEvidence"].items()
    }
    if expected_orbit_evidence != structural_evidence:
        raise ValueError(f"orbit/class evidence mismatch for {key}")

    mapped_target_orbits = {
        str(int(row["orbitIndex"]))
        for row in action["targets"]
        if str(row["targetLabel"]) == target_pair[0]
    }
    if not set(expected_orbit_evidence).issubset(mapped_target_orbits):
        raise ValueError(f"target orbit is absent from exact action row for {key}")

    ledger_projection = {
        "submissionId": str(ledger_row["submission_id"]),
        "polynomialIndex": int(ledger_row["polynomial_index"]),
        "status": str(ledger_row["status"]),
        "scoreable": int(ledger_row["scoreable"]),
        "label": str(ledger_row["label"]),
        "t": int(ledger_row["t"]),
        "r": int(ledger_row["r"]),
        "fieldDiscAbs": (
            str(ledger_row["field_disc_abs"])
            if ledger_row["field_disc_abs"] is not None
            else None
        ),
        "discSource": (
            str(ledger_row["disc_source"])
            if ledger_row["disc_source"] is not None
            else None
        ),
        "coefficientSha256": str(ledger_row["coefficient_hash"]),
        "coefficientBytes": len(
            str(ledger_row["original_line"]).encode("utf-8")
        ),
    }
    required = {
        "status": "accepted",
        "scoreable": 1,
        "label": source_label,
        "r": source_r,
        "coefficientSha256": str(source["sourceCoefficientSha256"]),
        "fieldDiscAbs": str(source["sourceFieldDiscAbs"]),
        "discSource": "exact_nfdisc",
        "coefficientBytes": int(full["sourceCoefficientBytes"]),
    }
    for field, expected in required.items():
        if ledger_projection[field] != expected:
            raise ValueError(
                f"current ledger {field}={ledger_projection[field]!r}, "
                f"expected {expected!r} for {key}"
            )

    return {
        "action": action,
        "actionEvidence": {
            "path": relative(ACTION_MAP),
            "sha256": shared.sha256_file(ACTION_MAP),
            "line": int(action_entry["line"]),
            "rowSha256": str(action_entry["rowSha256"]),
            "sourceLabel": source_label,
            "sourceT": int(action["sourceT"]),
            "length24OrbitCount": int(action["length24OrbitCount"]),
            "allLength24ActionsFaithful": True,
            "targetOrbits": sorted(int(value) for value in mapped_target_orbits),
            "targetLabelMultiplicity": int(
                action.get("targetCounts", {}).get(target_pair[0], 0)
            ),
        },
        "profileEvidence": profile_proof,
        "compatibleClassIndexes": class_indexes,
        "orbitClassEvidence": expected_orbit_evidence,
        "targetRUniqueAcrossEveryCompatibleClass": target_r_unique,
        "needsFrobeniusAssignment": (
            int(action["length24OrbitCount"]) > 1 and not target_r_unique
        ),
        "ledgerEvidence": {
            **ledger_projection,
            "rowProjectionSha256": canonical_sha256(ledger_projection),
            "coefficientMaterialIncluded": False,
        },
    }


def exact_runtime_samples(
    source_lookup: dict[tuple[str, int], tuple[str, int]]
) -> list[dict]:
    samples = []
    seen = set()
    for path in sorted(DATA.rglob("*")):
        if (
            not path.is_file()
            or path.suffix not in (".json", ".jsonl")
            or not any(
                marker in path.name.lower()
                for marker in ("pair", "resolvent", "live_gold_route")
            )
        ):
            continue
        for row in sole.iter_file_dicts(path):
            if str(row.get("status")) not in sole.TESTED_STATUSES:
                continue
            elapsed = row.get("elapsedSeconds")
            submission = row.get("sourceSubmissionId")
            index = row.get("sourcePolynomialIndex")
            targets = row.get("orbitTargets")
            certificate = row.get("orbitCertificate")
            if (
                not isinstance(elapsed, (int, float))
                or float(elapsed) <= 0
                or submission is None
                or index is None
                or not isinstance(targets, list)
                or not targets
                or not isinstance(certificate, dict)
            ):
                continue
            source = source_lookup.get((str(submission), int(index)))
            if source is None or str(row.get("sourceCoefficientSha256")) != source[0]:
                continue
            actual = sorted(int(value) for value in certificate.get("actualDegrees", []))
            expected = sorted(
                int(value) for value in certificate.get("expectedDegrees", [])
            )
            exponents = [int(value) for value in certificate.get("exponents", [])]
            if (
                not actual
                or actual != expected
                or any(value != 1 for value in exponents)
                or actual.count(24) != len(targets)
            ):
                continue
            identity = canonical_sha256(
                {
                    "sourceCoefficientSha256": source[0],
                    "elapsedSeconds": float(elapsed),
                    "transform": row.get("transform"),
                    "orbitCertificate": certificate,
                    "resolventSha256": [
                        attempt.get("resolventSha256")
                        for attempt in row.get("attempts", [])
                        if isinstance(attempt, dict)
                    ],
                    "candidateSha256": [
                        candidate.get("coefficientSha256")
                        for candidate in row.get("candidates", [])
                        if isinstance(candidate, dict)
                    ]
                    or [row.get("coefficientSha256")],
                }
            )
            if identity in seen:
                continue
            seen.add(identity)
            samples.append(
                {
                    "length24OrbitCount": len(targets),
                    "sourceCoefficientBytes": int(source[1]),
                    "workerElapsedSeconds": float(elapsed),
                }
            )
    return samples


def runtime_estimate(
    source_bytes: int, orbit_count: int, samples: list[dict]
) -> dict:
    matching = [
        sample
        for sample in samples
        if int(sample["length24OrbitCount"]) == orbit_count
    ]
    if not matching:
        matching = samples
    nearest = sorted(
        matching,
        key=lambda row: (
            abs(int(row["sourceCoefficientBytes"]) - source_bytes),
            int(row["sourceCoefficientBytes"]),
            float(row["workerElapsedSeconds"]),
        ),
    )[:NEAREST_RUNTIME_SAMPLES]
    if not nearest:
        raise ValueError("no exact historical pair-worker runtime samples")
    core = statistics.median(
        float(row["workerElapsedSeconds"]) for row in nearest
    )
    return {
        "method": (
            f"k={len(nearest)} nearest deduplicated exact historical pair-worker "
            "results by source coefficient bytes, matched on length-24 orbit count"
        ),
        "empiricalCoreSeconds": round(core, 4),
        "fixedSageStartupAndTeardownSeconds": FIXED_OVERHEAD_SECONDS,
        "estimatedWallSeconds": round(core + FIXED_OVERHEAD_SECONDS, 4),
        "nearestSampleCoefficientByteRange": [
            min(int(row["sourceCoefficientBytes"]) for row in nearest),
            max(int(row["sourceCoefficientBytes"]) for row in nearest),
        ],
    }


def current_ledger_rows(
    connection: sqlite3.Connection, items: list[dict]
) -> dict[tuple[str, int], dict]:
    query = """
        SELECT v.submission_id,v.polynomial_index,v.status,v.scoreable,
               v.label,v.t,v.r,v.field_disc_abs,v.disc_source,
               p.coefficient_hash,p.original_line
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.submission_id=? AND v.polynomial_index=?
    """
    rows = {}
    for item in items:
        source = item["route"]["source"]
        key = (str(source["submissionId"]), int(source["polynomialIndex"]))
        row = connection.execute(query, key).fetchone()
        if row is not None:
            rows[key] = dict(row)
    return rows


def compact_boundary(meta: dict) -> dict:
    receipt = meta["receiptAudit"]
    outbox = meta["outboxAudit"]
    return {
        "boundaryFunction": (
            "fresh_t00134_top5000_pair_orbit_census.exclusion_boundary "
            "with top-10000 wrapper constants"
        ),
        "excludedPairKinds": [
            "baseline",
            "owned",
            "known_verification",
            "receipt",
            "outbox",
        ],
        "prefixPairs": int(meta["prefixPairs"]),
        "eligiblePairs": int(meta["eligiblePairs"]),
        "baselinePairsInPrefix": int(meta["baselinePairs"]),
        "ownedPairsInPrefix": int(meta["ownedPairs"]),
        "knownVerificationPairsInPrefix": int(meta["knownVerificationPairs"]),
        "receiptPairsInPrefix": int(meta["receiptPairs"]),
        "outboxPairsInPrefix": int(meta["outboxPairs"]),
        "receiptAudit": {
            key: receipt.get(key)
            for key in (
                "receiptCount",
                "receiptPolynomialHashes",
                "receiptTargetPairs",
                "queuedPossiblePairMapRows",
            )
        },
        "outboxAudit": {
            key: outbox.get(key)
            for key in (
                "outboxFiles",
                "nonemptyOutboxFiles",
                "canonicalPolynomialRows",
                "distinctCoefficientHashes",
                "distinctPairsExcluded",
            )
        },
    }


def main() -> int:
    pins = validate_pins()
    structural, target_meta = structural_routes()
    action_index = indexed_jsonl(ACTION_MAP, "sourceLabel")
    profile_cache_index = indexed_jsonl(PROFILE_CACHE, "sourceLabel")
    pair_summary = json.loads(PAIR_SUMMARY.read_text(encoding="utf-8"))
    profile_sources = {
        str(key): str(value)
        for key, value in pair_summary.get("profileSources", {}).items()
    }
    full_index = full_frontier_index()
    sealed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    with sqlite3.connect(
        f"file:{DB.resolve()}?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        prefix = census.validated_prefix()
        eligible, boundary_meta = census.exclusion_boundary(
            connection, set(prefix)
        )
        ledger_rows = current_ledger_rows(connection, structural)
        all_source_lookup = {
            (str(submission), int(index)): (
                str(digest),
                len(str(line).encode("utf-8")),
            )
            for submission, index, digest, line in connection.execute(
                """
                SELECT submission_id,polynomial_index,coefficient_hash,original_line
                FROM polynomials
                """
            )
        }
        data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])

    exact = {}
    validation_failures = {}
    for item in structural:
        source = item["route"]["source"]
        source_key = (
            str(source["submissionId"]),
            int(source["polynomialIndex"]),
        )
        route_key = (
            item["targetPair"],
            source_key,
            str(source["sourceCoefficientSha256"]),
        )
        row = ledger_rows.get(source_key)
        if row is None:
            validation_failures[route_key] = ["current_ledger_row_absent"]
            continue
        try:
            exact[route_key] = validate_exact_route(
                item,
                row,
                action_index,
                profile_cache_index,
                profile_sources,
                full_index,
            )
        except (KeyError, TypeError, ValueError) as exc:
            validation_failures[route_key] = [f"exact_evidence_error:{exc}"]

    presentation_hash = {
        source_key: str(row["coefficient_hash"])
        for source_key, row in ledger_rows.items()
    }
    tested_hashes, tested_evidence = sole.tested_source_hashes(
        presentation_hash
    )
    runtime_samples = exact_runtime_samples(all_source_lookup)
    sample_counts = {
        str(orbit_count): sum(
            int(row["length24OrbitCount"]) == orbit_count
            for row in runtime_samples
        )
        for orbit_count in sorted(
            {int(row["length24OrbitCount"]) for row in runtime_samples}
        )
    }

    ready = []
    excluded = []
    for item in structural:
        target_pair = item["targetPair"]
        source = item["route"]["source"]
        source_key = (
            str(source["submissionId"]),
            int(source["polynomialIndex"]),
        )
        digest = str(source["sourceCoefficientSha256"])
        route_key = (target_pair, source_key, digest)
        reasons = list(validation_failures.get(route_key, []))
        if target_pair not in eligible:
            reasons.append("target_pair_fails_current_all_exclusion_boundary")
        if digest in tested_hashes:
            reasons.append("source_hash_has_actual_certified_pair_test_evidence")
        evidence = exact.get(route_key)
        if evidence is None and not reasons:
            reasons.append("exact_evidence_unavailable")

        output = DATA / (
            "rank10_guaranteed_pair_"
            f"{target_pair[0]}_r{target_pair[1]}__"
            f"{source['sourceLabel']}_r{source['sourceR']}__"
            f"{digest[:12]}.jsonl"
        )
        temporary = output.with_suffix(output.suffix + ".tmp")
        if output.exists() or temporary.exists():
            reasons.append("planned_worker_output_or_temporary_already_exists")
        if reasons:
            excluded.append(
                {
                    "target": {
                        "label": target_pair[0],
                        "r": target_pair[1],
                    },
                    "source": {
                        "submissionId": source_key[0],
                        "polynomialIndex": source_key[1],
                        "coefficientSha256": digest,
                    },
                    "reasons": reasons,
                    "testedEvidence": tested_evidence.get(digest, []),
                }
            )
            continue

        target = item["target"]
        prefix_row = prefix[target_pair]
        target_checks = {
            "label": str(prefix_row["label"]) == target_pair[0],
            "r": int(prefix_row["r"]) == target_pair[1],
            "kTeams": int(prefix_row["kTeams"]) == int(target["kTeamsAtCrawl"]),
            "points": float(prefix_row["points"])
            == float(target["holderPointsAtCrawl"]),
            "minimumDisc": str(prefix_row["minScoringDiscAbs"])
            == str(target["minimumDiscAbsAtCrawl"]),
            "holderScoringDisc": str(prefix_row["scoringDiscAbs"])
            == str(target["holderScoringDiscAbsAtCrawl"]),
        }
        if not all(target_checks.values()):
            raise ValueError(f"target structural/prefix mismatch for {target_pair}")

        runtime = runtime_estimate(
            int(evidence["ledgerEvidence"]["coefficientBytes"]),
            int(evidence["actionEvidence"]["length24OrbitCount"]),
            runtime_samples,
        )
        command = [
            "/usr/local/bin/sage",
            "-python",
            "pair_sum_one.sage.py",
            source_key[0],
            str(source_key[1]),
            "--expected-target",
            target_pair[0],
            "--orbit-map",
            relative(ACTION_MAP),
            "--transforms",
            "1,2,3,5,7",
            "--reduce",
            "best",
            "--nfdisc",
            "--all-degree-24",
            "--expected-source-hash",
            digest,
            "--output-jsonl",
            relative(output),
        ]
        swing = float(item["ratioOneReferenceRelativeSwing"])
        ready.append(
            {
                "schemaVersion": (
                    "rank10-t00134-top10000-guaranteed-pair-run-route-v1"
                ),
                "sealedAt": sealed_at,
                "status": "ready_fail_closed",
                "target": {
                    "label": target_pair[0],
                    "r": target_pair[1],
                    "kTeamsAtCrawl": int(target["kTeamsAtCrawl"]),
                    "holderPointsAtCrawl": float(target["holderPointsAtCrawl"]),
                    "minimumDiscAbsAtCrawl": str(
                        target["minimumDiscAbsAtCrawl"]
                    ),
                    "holderScoringDiscAbsAtCrawl": str(
                        target["holderScoringDiscAbsAtCrawl"]
                    ),
                    "passesCurrentAllExclusionBoundary": True,
                    "structuralPrefixCrossCheck": target_checks,
                },
                "scoreImpact": {
                    "ratioOneReferenceRelativeSwing": swing,
                    "ratioOneReferenceFormula": (
                        "2^(-kTeamsAtCrawl) + holderPointsAtCrawl/2"
                    ),
                    "exactRelativeSwingAfterNfdiscFormula": (
                        "2^(-kTeamsAtCrawl) * "
                        "log(minimumDiscAbsAtCrawl)/log(candidateFieldDiscAbs) "
                        "+ holderPointsAtCrawl/2"
                    ),
                    "candidateDiscriminantRatioIsUncapped": True,
                    "structuralTargetHitProbability": 1.0,
                    "novelExactScoreImprovementIsNotGuaranteed": True,
                },
                "source": evidence["ledgerEvidence"],
                "exactActionEvidence": evidence["actionEvidence"],
                "exactProfileEvidence": evidence["profileEvidence"],
                "compatibleClassIndexes": evidence[
                    "compatibleClassIndexes"
                ],
                "orbitClassEvidence": evidence["orbitClassEvidence"],
                "factorAssignment": {
                    "targetRUniqueAcrossEveryCompatibleClass": evidence[
                        "targetRUniqueAcrossEveryCompatibleClass"
                    ],
                    "needsFrobeniusAssignment": evidence[
                        "needsFrobeniusAssignment"
                    ],
                    "policy": (
                        "Do not assign a multi-orbit factor to the target label "
                        "from list position. Use exact targetR only when uniquely "
                        "proved; otherwise require an exact Frobenius assignment."
                    ),
                },
                "structuralEvidence": {
                    "path": relative(STRUCTURAL),
                    "sha256": pins[relative(STRUCTURAL)]["sha256"],
                    "line": int(item["structuralLine"]),
                    "rowSha256": str(item["structuralRowSha256"]),
                },
                "currentExclusionEvidence": {
                    "targetPairPasses": True,
                    "sourceHashPairTested": False,
                    "sourceTestEvidence": [],
                },
                "runtimeEstimate": runtime,
                "ratioOneReferenceSwingPerEstimatedHeavyMinute": round(
                    swing * 60.0 / float(runtime["estimatedWallSeconds"]), 9
                ),
                "output": relative(output),
                "commandArgv": command,
                "guardedShellCommand": (
                    f"test ! -e {shlex.quote(relative(output))} && "
                    f"test ! -e {shlex.quote(relative(temporary))} && "
                    f"{shlex.join(command)}"
                ),
                "postRunFailClosedGates": [
                    "worker status is certified_multi and source hash matches",
                    "orbit certificate exactly matches the pinned action row",
                    "every selected candidate is degree 24, monic, irreducible, and has exact nfdisc",
                    "assign the target factor only by exact targetR uniqueness or exact Frobenius evidence",
                    "candidate coefficient hash is absent from ledger, receipts, and outbox",
                    "target pair still passes baseline/owned/known/receipt/outbox exclusions",
                    "compute exact relative swing from candidate field discriminant",
                    "do not stage or submit from this packet",
                ],
            }
        )

    by_target: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for route in ready:
        by_target[(route["target"]["label"], route["target"]["r"])].append(route)
    target_order = sorted(
        by_target,
        key=lambda pair: (
            -float(
                target_meta[pair]["ratioOneReferenceRelativeSwing"]
            ),
            pair[0],
            pair[1],
        ),
    )
    primaries = []
    alternates = []
    for target_rank, pair in enumerate(target_order, start=1):
        candidates = sorted(
            by_target[pair],
            key=lambda row: (
                float(row["runtimeEstimate"]["estimatedWallSeconds"]),
                int(row["source"]["coefficientBytes"]),
                str(row["source"]["coefficientSha256"]),
            ),
        )
        for source_rank, route in enumerate(candidates, start=1):
            route["targetBreadthRank"] = target_rank
            route["sourceRankWithinTarget"] = source_rank
            route["laneRole"] = (
                "primary_distinct_target" if source_rank == 1 else "alternate_source"
            )
            route["skipPolicy"] = (
                "Run first for this target."
                if source_rank == 1
                else (
                    "Skip after any earlier source for this target yields a novel, "
                    "exactly assigned candidate; use only on failure or duplication."
                )
            )
            (primaries if source_rank == 1 else alternates).append(route)
    ordered = primaries + sorted(
        alternates,
        key=lambda row: (
            int(row["targetBreadthRank"]),
            int(row["sourceRankWithinTarget"]),
        ),
    )
    throughput = sorted(
        ordered,
        key=lambda row: (
            -float(row["ratioOneReferenceSwingPerEstimatedHeavyMinute"]),
            str(row["target"]["label"]),
            int(row["target"]["r"]),
            str(row["source"]["coefficientSha256"]),
        ),
    )
    throughput_rank = {
        (
            row["target"]["label"],
            int(row["target"]["r"]),
            row["source"]["coefficientSha256"],
        ): rank
        for rank, row in enumerate(throughput, start=1)
    }
    for run_rank, route in enumerate(ordered, start=1):
        route["runRank"] = run_rank
        route["throughputRank"] = throughput_rank[
            (
                route["target"]["label"],
                int(route["target"]["r"]),
                route["source"]["coefficientSha256"],
            )
        ]

    shared.write_jsonl_atomic(
        OUTPUT, ordered, sort_key=lambda row: int(row["runRank"])
    )
    primary_targets = {
        (row["target"]["label"], int(row["target"]["r"])) for row in primaries
    }
    runtime_packet = json.loads(RUNTIME_PACKET.read_text(encoding="utf-8"))
    runtime_values = [
        float(row["workerElapsedSeconds"]) for row in runtime_samples
    ]
    summary = {
        "schemaVersion": (
            "rank10-t00134-top10000-guaranteed-pair-run-packet-summary-v1"
        ),
        "createdAt": sealed_at,
        "status": (
            "eight_target_breadth_ready_fail_closed"
            if len(primary_targets) == EXPECTED_TARGET_COUNT
            else "partial_after_current_fail_closed_exclusions"
        ),
        "method": {
            "lightOnly": True,
            "sageGapWorkersStarted": 0,
            "networkCalls": 0,
            "stagingCalls": 0,
            "submissionCalls": 0,
            "coefficientMaterialIncluded": False,
        },
        "pinnedArtifacts": pins,
        "currentLedger": {
            "path": relative(DB),
            "readMode": "sqlite_read_only_snapshot",
            "dataVersion": data_version,
            "acceptedSourceRowsCrossChecked": len(ledger_rows),
        },
        "currentTargetBoundary": compact_boundary(boundary_meta),
        "testedSourceHashPolicy": {
            "function": "fresh_t00134_pair_orbit_census.tested_source_hashes",
            "acceptedStatuses": sorted(sole.TESTED_STATUSES),
            "actualCertifiedPairArithmeticRequired": True,
            "testedPinnedSourceHashesExcluded": len(tested_hashes),
        },
        "hashExclusionSemantics": {
            "acceptedSourceHashes": (
                "Input provenance hashes. They are naturally tied to prior accepted "
                "submissions, so receipt membership is not a reason to discard an "
                "otherwise untested source. Actual certified pair-test evidence is."
            ),
            "generatedCandidateHashes": (
                "Unknown until arithmetic runs. Every generated hash must be absent "
                "from the ledger, receipt manifests, and outbox before staging."
            ),
        },
        "runtimeModel": {
            "deduplicatedExactHistoricalResults": len(runtime_samples),
            "samplesByLength24OrbitCount": sample_counts,
            "workerElapsedMedianSeconds": (
                round(statistics.median(runtime_values), 4)
                if runtime_values
                else None
            ),
            "workerElapsedP90Seconds": (
                round(percentile(runtime_values, 0.9), 4)
                if runtime_values
                else None
            ),
            "fixedSageStartupAndTeardownSeconds": FIXED_OVERHEAD_SECONDS,
            "fixedOverheadEvidence": {
                "path": relative(RUNTIME_PACKET),
                "sha256": shared.sha256_file(RUNTIME_PACKET),
                "observedCommandWallSeconds": runtime_packet["runtimeModel"][
                    "fixedOverheadEvidence"
                ]["observedCommandWallSeconds"],
                "workerElapsedSeconds": runtime_packet["runtimeModel"][
                    "fixedOverheadEvidence"
                ]["workerElapsedSeconds"],
            },
            "scope": (
                "Pair-sum generation through exact nfdisc only; any required "
                "Frobenius assignment runtime is not included."
            ),
        },
        "counts": {
            "pinnedGuaranteedTargetPairs": len(target_meta),
            "pinnedGuaranteedAcceptedPresentations": len(structural),
            "currentEligibleReadyTargetPairs": len(primary_targets),
            "currentUntestedReadyPresentations": len(ordered),
            "recommendedPrimaryPresentations": len(primaries),
            "alternatePresentations": len(alternates),
            "excludedPresentations": len(excluded),
            "routesNeedingFrobeniusAssignment": sum(
                bool(row["factorAssignment"]["needsFrobeniusAssignment"])
                for row in ordered
            ),
        },
        "aggregate": {
            "distinctTargetRatioOneReferenceRelativeSwing": round(
                sum(
                    float(target_meta[pair]["ratioOneReferenceRelativeSwing"])
                    for pair in primary_targets
                ),
                12,
            ),
            "recommendedPrimariesEstimatedSequentialWallSeconds": round(
                sum(
                    float(row["runtimeEstimate"]["estimatedWallSeconds"])
                    for row in primaries
                ),
                4,
            ),
            "allPresentationsEstimatedSequentialWallSeconds": round(
                sum(
                    float(row["runtimeEstimate"]["estimatedWallSeconds"])
                    for row in ordered
                ),
                4,
            ),
            "threeHourPairGenerationBudgetSeconds": 10800,
        },
        "recommendedPolicy": {
            "ordering": (
                "One fastest accepted source per distinct target in descending "
                "ratio-one reference swing, then alternate sources."
            ),
            "stopWithinTarget": (
                "Do not run an alternate after an earlier source yields a novel "
                "candidate with exact target assignment and positive exact swing."
            ),
            "revalidateImmediatelyBeforeEachRun": [
                "planned output and temporary path are absent",
                "source remains accepted, scoreable, exact-nfdisc, hash-identical, and pair-untested",
                "target still passes baseline/owned/known/receipt/outbox exclusions",
            ],
        },
        "excludedPresentations": excluded,
        "outputs": {
            "routes": relative(OUTPUT),
            "summary": relative(SUMMARY),
        },
    }
    shared.write_json_atomic(SUMMARY, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "readyTargets": len(primary_targets),
                "readyPresentations": len(ordered),
                "excludedPresentations": len(excluded),
                "primaryEstimatedSeconds": summary["aggregate"][
                    "recommendedPrimariesEstimatedSequentialWallSeconds"
                ],
                "allEstimatedSeconds": summary["aggregate"][
                    "allPresentationsEstimatedSequentialWallSeconds"
                ],
                "output": relative(OUTPUT),
                "summary": relative(SUMMARY),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prepare a deterministic empirical-transition F5 wave without doing arithmetic.

The selector is intentionally light-only.  It reads the pinned ledger/action/result
corpus, learns signature-transition profiles, and ranks novel unaligned F5 sources.
It never invokes Sage/GAP or the network.  ``--seal`` only publishes a coefficient-
free plan compatible with ``run_f5_untried_compact_plan.sage.py``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Iterable

import prepare_f5_untried_wave as core


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
PREVIEW_ROOT = DATA / "f5_wave_previews"
WORKER = ROOT / "run_f5_untried_compact_plan.sage.py"
BASE_WORKER = ROOT / "run_f5_untried_plan.sage.py"
DB = DATA / "ledger.sqlite3"
DB_WAL = DATA / "ledger.sqlite3-wal"
GOLD = DATA / "live_undiscovered_signatures.jsonl"
LOCK = DATA / ".f5_untried_heavy.lock"

EXPECTED_CORE_SHA256 = "db31a240f7835e0a638e8f91bbb7918890692b4698a129aabdb93599da7c9d2b"
EXPECTED_WORKER_SHA256 = "d43c5cd580f0906a86e06e0b0150c2605926adceec271ad8170aa66bd63a4ff9"
EXPECTED_BASE_WORKER_SHA256 = "a2abb1928f67652d552826aa6936330bcda7c39b2d8340a5d5abd4271a011479"

ALPHA = Fraction(1, 2)
CALIBRATION_BASE = Fraction(3, 65)
SHRINK_STRENGTH = 10
HEIGHT_COST_BASE = 512
REFERENCE = {
    "selectedSources": 50,
    "selectedSourceSignatures": 50,
    "selectedCanonicalSources": 50,
    "distinctPairs": 74,
    "multiGoldSources": 18,
    "minimumHeightBits": 8,
    "medianHeightBits": 34,
    "maximumHeightBits": 150,
    "pairSlotOverlap": 0,
    "coefficientFreeSelectionSha256": "e4ac3458f6981ea3e9a5078f92c404098fc89996a5640ceaa1e5b4398d46275e",
    "rawExpectedHits": {
        "numerator": 9124408157,
        "denominator": 636389208,
    },
    "confidenceShrunkExpectedHits": {
        "numerator": 1586876953,
        "denominator": 176774780,
    },
    "trainingRows": 371,
    "trainingProfiles": 36,
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fraction_payload(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def validate_report_ownership(report_path: Path, wave_id: str) -> None:
    if not report_path.exists():
        return
    existing = core.read_json(report_path)
    if (
        existing.get("schemaVersion") != "f5-unaligned-empirical-preview-v1"
        or existing.get("waveId") != wave_id
    ):
        raise ValueError("refusing to overwrite a report not owned by this wave")


def profile_key(source_r: int, allowed: Iterable[int]) -> tuple[int, tuple[int, ...]]:
    signatures = tuple(sorted({int(value) for value in allowed}))
    if not signatures:
        raise ValueError("transition profile has no allowed target signatures")
    return int(source_r), signatures


def transition_estimate(
    counts: Counter[int], allowed: tuple[int, ...], gold: set[int], height_bits: int
) -> dict[str, Fraction | int]:
    if not gold or not gold <= set(allowed):
        raise ValueError("current-gold signatures must be a nonempty subset of the profile")
    trials = sum(int(value) for value in counts.values())
    successes = sum(int(counts.get(signature, 0)) for signature in gold)
    raw = (Fraction(successes, 1) + ALPHA * len(gold)) / (
        Fraction(trials, 1) + ALPHA * len(allowed)
    )
    posterior = (
        Fraction(trials, 1) * raw + SHRINK_STRENGTH * CALIBRATION_BASE
    ) / (trials + SHRINK_STRENGTH)
    if trials == 0:
        posterior = CALIBRATION_BASE
    cost = Fraction(
        HEIGHT_COST_BASE + max(0, int(height_bits) - 64), HEIGHT_COST_BASE
    )
    return {
        "trials": trials,
        "successes": successes,
        "ratio": Fraction(len(gold), len(allowed)),
        "raw": raw,
        "posterior": posterior,
        "cost": cost,
        "utility": posterior / cost,
    }


def candidate_identity(row: dict) -> tuple[str, int, str]:
    source = row["source"]
    return (
        str(source["label"]),
        int(source["r"]),
        str(row["canonicalQuotientSha256"]),
    )


def candidate_pairs(row: dict) -> set[tuple[str, int]]:
    return {
        (str(pair["label"]), int(pair["r"]))
        for pair in row["possibleGoldPairs"]
    }


def empirical_fraction(row: dict, name: str) -> Fraction:
    value = row["empiricalTransition"][name]
    return Fraction(int(value["numerator"]), int(value["denominator"]))


def representative_rank(row: dict) -> tuple:
    empirical = row["empiricalTransition"]
    source = row["source"]
    return (
        -empirical_fraction(row, "utility"),
        -len(row["possibleGoldPairs"]),
        -int(empirical["profileTrials"]),
        int(row["coefficientHeightBits"]),
        str(row["canonicalQuotientSha256"]),
        str(source["label"]),
        int(source["r"]),
    )


def one_representative_per_source_signature(rows: Iterable[dict]) -> list[dict]:
    best: dict[tuple[str, int], dict] = {}
    for row in rows:
        source = row["source"]
        key = (str(source["label"]), int(source["r"]))
        incumbent = best.get(key)
        if incumbent is None or representative_rank(row) < representative_rank(incumbent):
            best[key] = row
    return sorted(best.values(), key=representative_rank)


def greedy_select(representatives: Iterable[dict], maximum: int) -> list[dict]:
    available = list(representatives)
    selected: list[dict] = []
    covered: set[tuple[str, int]] = set()
    used_canonical: set[str] = set()
    while available and len(selected) < maximum:
        available = [
            row
            for row in available
            if row["canonicalQuotientSha256"] not in used_canonical
        ]
        if not available:
            break
        frequencies: Counter[tuple[str, int]] = Counter()
        for row in available:
            frequencies.update(candidate_pairs(row))
        disjoint = [row for row in available if not (candidate_pairs(row) & covered)]
        pool = disjoint or available

        def rank(row: dict) -> tuple:
            pairs = candidate_pairs(row)
            new_pairs = pairs - covered
            rarity = sum(
                (Fraction(1, frequencies[pair]) for pair in new_pairs),
                Fraction(0, 1),
            )
            empirical = row["empiricalTransition"]
            source = row["source"]
            return (
                -empirical_fraction(row, "utility"),
                -len(new_pairs),
                -rarity,
                -int(empirical["profileTrials"]),
                -empirical_fraction(row, "goldToAllowedRatio"),
                int(row["coefficientHeightBits"]),
                str(source["label"]),
                int(source["r"]),
                str(row["canonicalQuotientSha256"]),
            )

        chosen = min(pool, key=rank)
        chosen = json.loads(json.dumps(chosen, sort_keys=True))
        pairs = candidate_pairs(chosen)
        chosen["selectionRank"] = len(selected) + 1
        chosen["newGoldPairsAtSelection"] = len(pairs - covered)
        chosen["pairOverlapAtSelection"] = len(pairs & covered)
        selected.append(chosen)
        covered.update(pairs)
        used_canonical.add(str(chosen["canonicalQuotientSha256"]))
        chosen_identity = candidate_identity(chosen)
        available = [row for row in available if candidate_identity(row) != chosen_identity]
    return selected


def load_actions() -> tuple[dict[str, dict], dict[str, tuple[str, dict]], list[dict]]:
    artifacts = []
    actions: dict[str, dict] = {}
    for relative_path, expected in core.ACTION_SHARDS.items():
        path = ROOT / relative_path
        if not path.is_file() or sha256_path(path) != expected:
            raise ValueError(f"F5 action shard changed: {relative_path}")
        artifacts.append({"path": relative_path, "sha256": expected})
        for row in core.iter_jsonl(path):
            digest = core.action_key(row)
            incumbent = actions.get(digest)
            if incumbent is not None and core.action_core(incumbent) != core.action_core(row):
                raise ValueError("conflicting deduped F5 action")
            actions[digest] = row
    if len(actions) != 3771:
        raise ValueError("deduped F5 action count changed")
    grouped: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for digest, row in actions.items():
        grouped[str(row["sourceLabel"])].append((digest, row))
    unique = {label: rows[0] for label, rows in grouped.items() if len(rows) == 1}
    return actions, unique, artifacts


def transition_corpus_paths() -> tuple[list[Path], list[Path]]:
    legacy = sorted(DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl"))
    development = [path for path in legacy if "revival_" not in path.name]
    holdout = [path for path in legacy if "revival_" in path.name]
    holdout.extend(
        [
            DATA / "f5_untried_frontier_20260722" / "wave1_results.jsonl",
            DATA / "f5_untried_frontier_20260722_wave2_v3" / "results.jsonl",
        ]
    )
    if any(not path.is_file() for path in [*development, *holdout]):
        raise ValueError("empirical transition corpus is incomplete")
    return development, holdout


def usable_transitions(
    paths: Iterable[Path], unique_actions: dict[str, tuple[str, dict]]
) -> list[dict]:
    rows = []
    for path in paths:
        for result in core.iter_jsonl(path):
            source = result.get("source") or {}
            target = result.get("target") or {}
            label = source.get("label")
            source_r = source.get("r")
            target_label = target.get("label")
            target_r = target.get("r")
            item = unique_actions.get(str(label))
            if (
                item is None
                or source_r is None
                or not isinstance(target_label, str)
                or target_r is None
            ):
                continue
            action_digest, action = item
            allowed = tuple(
                sorted(
                    {
                        int(value)
                        for value in action[
                            "sourceSignatureToPossibleTargetSignatures"
                        ].get(str(int(source_r)), [])
                    }
                )
            )
            if not allowed:
                continue
            if str(action["targetLabel"]) != target_label or int(target_r) not in allowed:
                raise ValueError("resolved F5 transition disagrees with its unique action")
            rows.append(
                {
                    "actionSha256": action_digest,
                    "profile": profile_key(int(source_r), allowed),
                    "sourceLabel": str(label),
                    "targetR": int(target_r),
                }
            )
    return rows


def profile_counts(rows: Iterable[dict]) -> dict[tuple[int, tuple[int, ...]], Counter[int]]:
    counts: dict[tuple[int, tuple[int, ...]], Counter[int]] = defaultdict(Counter)
    for row in rows:
        counts[row["profile"]][int(row["targetR"])] += 1
    return counts


def validation_metrics(development: list[dict], holdout: list[dict]) -> dict:
    counts = profile_counts(development)
    development_labels = {row["sourceLabel"] for row in development}

    def metrics(rows: list[dict]) -> dict:
        assigned = []
        uniform = []
        correct = 0
        for row in rows:
            key = row["profile"]
            allowed = key[1]
            counter = counts.get(key, Counter())
            total = sum(counter.values())
            denominator = Fraction(total, 1) + ALPHA * len(allowed)
            probabilities = {
                signature: (Fraction(counter.get(signature, 0), 1) + ALPHA)
                / denominator
                for signature in allowed
            }
            best_probability = max(probabilities.values())
            # Accuracy is set-valued when the posterior has an exact tie.  This
            # avoids injecting a synthetic signature-order preference into the
            # leakage-safe validation metric; ordering of live candidates still
            # uses the fully deterministic tuples below.
            correct += int(probabilities[int(row["targetR"])] == best_probability)
            assigned.append(float(probabilities[int(row["targetR"])]))
            uniform.append(1.0 / len(allowed))
        return {
            "rows": len(rows),
            "topSignatureAccuracy": correct / len(rows),
            "meanAssignedProbability": statistics.mean(assigned),
            "logLoss": statistics.mean(-math.log(value) for value in assigned),
            "uniformLogLoss": statistics.mean(-math.log(value) for value in uniform),
        }

    unseen = [row for row in holdout if row["sourceLabel"] not in development_labels]
    result = metrics(holdout)
    result["unseenSourceLabels"] = metrics(unseen)
    return result


def fraction_metadata(estimate: dict[str, Fraction | int]) -> dict:
    return {
        "profileTrials": int(estimate["trials"]),
        "profileGoldTransitions": int(estimate["successes"]),
        "goldToAllowedRatio": fraction_payload(estimate["ratio"]),
        "rawPosterior": fraction_payload(estimate["raw"]),
        "confidenceShrunkPosterior": fraction_payload(estimate["posterior"]),
        "heightCost": fraction_payload(estimate["cost"]),
        "utility": fraction_payload(estimate["utility"]),
    }


def build_state(args: argparse.Namespace) -> dict:
    if sha256_path(ROOT / "prepare_f5_untried_wave.py") != EXPECTED_CORE_SHA256:
        raise ValueError("audited core F5 planner changed")
    if sha256_path(WORKER) != EXPECTED_WORKER_SHA256:
        raise ValueError("compact F5 worker changed")
    if sha256_path(BASE_WORKER) != EXPECTED_BASE_WORKER_SHA256:
        raise ValueError("base F5 worker changed")

    destination = core.resolve_under(args.destination, DATA, "destination")
    manifest = core.resolve_under(args.manifest, OUTBOX, "manifest")
    report_path = (
        core.resolve_under(args.report, PREVIEW_ROOT, "report")
        if args.report is not None
        else None
    )
    if report_path is not None:
        validate_report_ownership(report_path, args.wave_id)
    if args.seal and report_path is None:
        raise ValueError("seal requires an owned report path")
    if args.seal and (destination.exists() or manifest.exists()):
        raise ValueError("refusing to overwrite existing wave state")

    results_path = destination / "results.jsonl"
    summary_path = destination / "summary.json"
    index_path = destination / "frontier.jsonl"
    claimed_path = destination / "claimed_pairs_at_seal.json"
    plan_path = destination / "plan.json"
    preflight_path = destination / "preflight.json"
    claims_dir = destination / "claims"

    initial_core = {
        "database": core.artifact(DB),
        "databaseWal": core.artifact(DB_WAL) if DB_WAL.is_file() else None,
        "frozenGold": core.artifact(GOLD),
        "worker": core.artifact(WORKER),
        "baseWorker": core.artifact(BASE_WORKER),
        "selector": core.artifact(Path(__file__).resolve()),
    }
    ignored = {report_path} if report_path is not None else set()
    census_volatile = core.mutable_boundary(
        destination, manifest, ignored_data_paths=ignored
    )
    _, unique_actions, action_artifacts = load_actions()
    plan_paths, result_paths = core.discover_prior_f5_paths(destination)
    rank11_plan_paths = {
        path
        for directory in DATA.glob("rank11_f5_unaligned_*")
        if directory.is_dir() and directory.resolve() != destination.resolve()
        for path in directory.rglob("*plan.json")
        if path.is_file()
    }
    rank11_result_paths = {
        path
        for directory in DATA.glob("rank11_f5_unaligned_*")
        if directory.is_dir() and directory.resolve() != destination.resolve()
        for path in directory.rglob("*results.jsonl")
        if path.is_file()
    }
    plan_paths = sorted(set(plan_paths) | rank11_plan_paths)
    result_paths = sorted(set(result_paths) | rank11_result_paths)
    prior_plans = [core.artifact(path) for path in plan_paths]
    prior_results = [core.artifact(path) for path in result_paths]
    tried, known_candidate_sources, prior_result_rows = core.collect_prior_sources(
        plan_paths, result_paths
    )
    tried_hashes = {row[2] for row in tried}
    prior_source_signatures = set()
    for path in result_paths:
        for row in core.iter_jsonl(path):
            source = row.get("source") or {}
            if isinstance(source.get("label"), str) and source.get("r") is not None:
                prior_source_signatures.add((str(source["label"]), int(source["r"])))

    development_paths, holdout_paths = transition_corpus_paths()
    development = usable_transitions(development_paths, unique_actions)
    holdout = usable_transitions(holdout_paths, unique_actions)
    training = [*development, *holdout]
    counts = profile_counts(training)
    validation = validation_metrics(development, holdout)
    frozen_training_paths = {
        path.resolve() for path in [*development_paths, *holdout_paths]
    }
    post_validation_result_paths = [
        path for path in result_paths if path.resolve() not in frozen_training_paths
    ]
    post_validation_usable = usable_transitions(
        post_validation_result_paths, unique_actions
    )
    training_boundary = [
        core.artifact(path) for path in [*development_paths, *holdout_paths]
    ]

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    target_boundary = core.target_boundary(connection)
    worker_claimed, reserved_pairs, reserved_hashes, claim_artifacts, outbox_artifacts, receipt_artifacts, receipt_health = core.collect_reservations(
        connection, destination
    )
    queued_rows = [
        {
            "submissionId": str(row[0]),
            "queuedCount": int(row[1]),
            "verifiedCount": int(row[2]),
            "failedCount": int(row[3]),
            "updatedAt": row[4],
        }
        for row in connection.execute(
            "SELECT submission_id,queued_count,verified_count,failed_count,updated_at "
            "FROM submissions WHERE queued_count>0 ORDER BY submission_id"
        )
    ]
    frozen_rows = list(core.iter_jsonl(GOLD))
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    owned = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    baseline = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    current_zero = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT label,r FROM targets WHERE team_count=0 AND discovered=0"
        )
    }
    gold = {
        pair
        for pair in frozen
        if pair in current_zero
        and pair not in owned
        and pair not in baseline
        and pair not in reserved_pairs
    }

    candidates: dict[tuple[str, int, str], dict] = {}
    accepted_rows = even_rows = eligible_even_rows = 0
    for db_row in connection.execute(
        "SELECT v.label,v.t,v.r,p.coefficients,p.coefficient_hash,p.submission_id,"
        "p.polynomial_index FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE v.status='accepted' AND v.scoreable=1"
    ):
        accepted_rows += 1
        coefficient_line = str(db_row[3])
        values = coefficient_line.split(",")
        if (
            len(values) != 25
            or values[-1] != "1"
            or any(int(values[index]) for index in range(1, 25, 2))
        ):
            continue
        even_rows += 1
        label = str(db_row[0])
        action_item = unique_actions.get(label)
        if action_item is None:
            continue
        eligible_even_rows += 1
        action_digest, action = action_item
        source_r = int(db_row[2])
        allowed = tuple(
            sorted(
                {
                    int(value)
                    for value in action[
                        "sourceSignatureToPossibleTargetSignatures"
                    ].get(str(source_r), [])
                }
            )
        )
        if not allowed:
            continue
        target_label = str(action["targetLabel"])
        possible = {
            (target_label, signature)
            for signature in allowed
            if (target_label, signature) in gold
        }
        if not possible:
            continue
        quotient_line = ",".join(values[::2])
        canonical = core.canonical_hash(quotient_line)
        source_signature = (label, source_r)
        if (
            canonical in tried_hashes
            or source_signature in prior_source_signatures
            or source_r in {pair[1] for pair in possible}
        ):
            continue
        height_bits = max(abs(int(value)) for value in values[::2]).bit_length()
        if height_bits > args.height_cap_bits:
            continue
        profile = profile_key(source_r, allowed)
        estimate = transition_estimate(
            counts.get(profile, Counter()),
            allowed,
            {pair[1] for pair in possible},
            height_bits,
        )
        proposed = {
            "actionSha256": action_digest,
            "canonicalQuotientSha256": canonical,
            "coefficientHeightBits": height_bits,
            "empiricalTransition": {
                **fraction_metadata(estimate),
                "allowedTargetSignatures": list(allowed),
            },
            "possibleGoldPairs": [
                {"label": pair[0], "r": pair[1]} for pair in sorted(possible)
            ],
            "signatureAligned": False,
            "source": {
                "coefficientSha256": str(db_row[4]),
                "label": label,
                "polynomialIndex": int(db_row[6]),
                "r": source_r,
                "submissionId": str(db_row[5]),
                "t": int(db_row[1]),
            },
        }
        identity = candidate_identity(proposed)
        row_rank = (height_bits, len(coefficient_line.encode()), str(db_row[4]))
        incumbent = candidates.get(identity)
        if incumbent is None or row_rank < incumbent["_rowRank"]:
            proposed["_rowRank"] = row_rank
            candidates[identity] = proposed

    for row in candidates.values():
        row.pop("_rowRank", None)
    representatives = one_representative_per_source_signature(candidates.values())
    selected = greedy_select(representatives, args.max_sources)
    receipt_ids = {Path(row["path"]).stem for row in receipt_artifacts}
    stale_queue = core.certify_stale_queue_exception(
        connection, queued_rows, receipt_ids, selected
    )
    stale_queue["enabled"] = bool(args.allow_pinned_stale_queue)
    queue_boundary = core.queued_boundary(connection, queued_rows)
    connection.close()

    selected_pairs = [pair for row in selected for pair in candidate_pairs(row)]
    pair_counts = Counter(selected_pairs)
    selected_heights = sorted(int(row["coefficientHeightBits"]) for row in selected)
    raw_sum = sum(
        (empirical_fraction(row, "rawPosterior") for row in selected),
        Fraction(0, 1),
    )
    posterior_sum = sum(
        (
            empirical_fraction(row, "confidenceShrunkPosterior")
            for row in selected
        ),
        Fraction(0, 1),
    )
    selection_digest = core.sha256_bytes(
        json.dumps(selected, separators=(",", ":"), sort_keys=True).encode()
    )
    aggregate = {
        "heightCapBits": int(args.height_cap_bits),
        "requestedSources": int(args.max_sources),
        "selectedSources": len(selected),
        "selectedSourceSignatures": len(
            {(row["source"]["label"], int(row["source"]["r"])) for row in selected}
        ),
        "selectedCanonicalSources": len(
            {row["canonicalQuotientSha256"] for row in selected}
        ),
        "priorResultSourceSignatureOverlap": len(
            {
                (str(row["source"]["label"]), int(row["source"]["r"]))
                for row in selected
            }
            & prior_source_signatures
        ),
        "distinctPairs": len(pair_counts),
        "pairSlots": len(selected_pairs),
        "pairSlotOverlap": sum(count - 1 for count in pair_counts.values()),
        "multiGoldSources": sum(
            len(row["possibleGoldPairs"]) > 1 for row in selected
        ),
        "minimumHeightBits": min(selected_heights) if selected_heights else None,
        "medianHeightBits": (
            int(statistics.median(selected_heights)) if selected_heights else None
        ),
        "maximumHeightBits": max(selected_heights) if selected_heights else None,
        "rawExpectedHits": fraction_payload(raw_sum),
        "confidenceShrunkExpectedHits": fraction_payload(posterior_sum),
        "coefficientFreeSelectionSha256": selection_digest,
    }
    reference_match = all(aggregate.get(key) == value for key, value in REFERENCE.items() if key in aggregate)
    reference_match &= len(training) == REFERENCE["trainingRows"]
    reference_match &= len(counts) == REFERENCE["trainingProfiles"]

    blockers = []
    if queued_rows and not (
        args.allow_pinned_stale_queue and stale_queue.get("certified")
    ):
        blockers.append(
            {
                "code": "queued_boundary_not_certified",
                "queuedSubmissions": len(queued_rows),
                "failedChecks": sorted(
                    key
                    for key, value in stale_queue.get("checks", {}).items()
                    if not value
                ),
            }
        )
    if receipt_health["activeReceiptManifestErrors"]:
        blockers.append({"code": "active_receipt_manifest_not_intact"})
    if not receipt_health["reservationArtifactsStableDuringScan"]:
        blockers.append({"code": "reservation_boundary_changed_during_census"})
    if len(selected) != args.max_sources:
        blockers.append(
            {"code": "insufficient_selected_sources", "selected": len(selected)}
        )
    if aggregate["pairSlotOverlap"] != 0:
        blockers.append({"code": "selected_pair_overlap"})
    if not reference_match and not args.accept_current_aggregate:
        blockers.append({"code": "reference_aggregate_diverged"})

    worker_prior_plan_paths = sorted(
        path
        for path in DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_plan.json")
        if path.is_file()
    )
    worker_prior_result_paths = sorted(
        path
        for path in DATA.glob("agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl")
        if path.is_file()
    )
    worker_prior_plans = [core.artifact(path) for path in worker_prior_plan_paths]
    worker_prior_results = [core.artifact(path) for path in worker_prior_result_paths]

    preview = {
        "schemaVersion": "f5-unaligned-empirical-preview-v1",
        "waveId": args.wave_id,
        "status": "blocked_before_seal" if blockers else "ready_for_explicit_seal",
        "sealRequested": bool(args.seal),
        "blockers": blockers,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "training": {
            "modelFreezePolicy": "validated_original_371_only",
            "developmentRows": len(development),
            "holdoutRows": len(holdout),
            "resolvedRows": len(training),
            "profiles": len(counts),
            "validation": validation,
            "artifacts": training_boundary,
            "postValidationEvidenceExcludedFromModel": {
                "artifacts": [
                    core.artifact(path) for path in post_validation_result_paths
                ],
                "resolvedRows": len(post_validation_usable),
                "includedInPriorAndSourceExclusions": True,
            },
        },
        "census": {
            "acceptedScoreableRows": accepted_rows,
            "evenRows": even_rows,
            "eligibleUniqueActionEvenRows": eligible_even_rows,
            "currentGoldPairs": len(gold),
            "novelUnalignedCandidateIdentities": len(candidates),
            "novelSourceSignatureRepresentatives": len(representatives),
            "priorSourceSignatures": len(prior_source_signatures),
            "priorCanonicalSources": len(tried_hashes),
            "priorResultRows": prior_result_rows,
            "knownCandidateSourceHashes": len(known_candidate_sources),
            "workerClaimedPairs": len(worker_claimed),
            "selectionReservedPairs": len(reserved_pairs),
            "reservedCoefficientHashes": len(reserved_hashes),
        },
        "selection": aggregate,
        "selectedSources": selected,
        "reference": {
            **REFERENCE,
            "matches": reference_match,
            "currentAggregateExplicitlyAccepted": bool(
                args.accept_current_aggregate and not reference_match
            ),
        },
        "queueHealth": {
            "activeQueuedSubmissions": len(queued_rows),
            "boundary": queue_boundary,
            "pinnedStaleQueueException": stale_queue,
        },
        "pinnedBoundaries": {
            **initial_core,
            "actions": action_artifacts,
            "allPriorPlans": prior_plans,
            "allPriorResults": prior_results,
            "workerPriorPlans": worker_prior_plans,
            "workerPriorResults": worker_prior_results,
            "claims": claim_artifacts,
            "outboxes": outbox_artifacts,
            "receipts": receipt_artifacts,
            "targets": target_boundary,
            "volatileWithoutOwnedReport": census_volatile,
        },
        "plannedOutputs": {
            "destination": core.relative(destination),
            "frontier": core.relative(index_path),
            "claimedPairIndex": core.relative(claimed_path),
            "plan": core.relative(plan_path),
            "preflight": core.relative(preflight_path),
            "manifest": core.relative(manifest),
        },
        "runtimeEstimate": {
            "arithmeticMinutesLow": 4,
            "arithmeticMinutesHigh": 6,
            "operationalExactHitsLow": 5,
            "operationalExactHitsHigh": 9,
            "unstratifiedFloorHits": 2,
        },
        "safeLaunchPrerequisites": [
            (
                "reference aggregate and selection digest independently reproduced"
                if reference_match
                else "current aggregate deviation explicitly accepted and pinned"
            ),
            "all pinned DB/WAL/frozen/action/prior/claim/outbox/receipt boundaries unchanged",
            "one shared heavy-worker lock free",
            "exact coefficient-free selected rows are a subset of the sealed frontier",
            "fresh live targets and queue immediately before launch",
            "submission remains unauthorized until post-worker live target/known-hash/claim refresh",
        ],
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    return {
        "preview": preview,
        "destination": destination,
        "manifest": manifest,
        "report": report_path,
        "paths": {
            "index": index_path,
            "claimed": claimed_path,
            "plan": plan_path,
            "preflight": preflight_path,
            "results": results_path,
            "summary": summary_path,
            "claims": claims_dir,
        },
        "selected": selected,
        "representatives": representatives,
        "workerClaimed": worker_claimed,
        "actionArtifacts": action_artifacts,
        "priorPlans": prior_plans,
        "priorResults": prior_results,
        "workerPriorPlans": worker_prior_plans,
        "workerPriorResults": worker_prior_results,
        "initialCore": initial_core,
        "censusVolatile": census_volatile,
    }


def assert_stable(state: dict) -> None:
    report = state["report"]
    ignored = {report} if report is not None else set()
    current_core = {
        "database": core.artifact(DB),
        "databaseWal": core.artifact(DB_WAL) if DB_WAL.is_file() else None,
        "frozenGold": core.artifact(GOLD),
        "worker": core.artifact(WORKER),
        "baseWorker": core.artifact(BASE_WORKER),
        "selector": core.artifact(Path(__file__).resolve()),
    }
    if current_core != state["initialCore"]:
        raise ValueError("core boundary changed during empirical census")
    plans, results = core.discover_prior_f5_paths(state["destination"])
    rank11_plans = {
        path
        for directory in DATA.glob("rank11_f5_unaligned_*")
        if directory.is_dir()
        and directory.resolve() != state["destination"].resolve()
        for path in directory.rglob("*plan.json")
        if path.is_file()
    }
    rank11_results = {
        path
        for directory in DATA.glob("rank11_f5_unaligned_*")
        if directory.is_dir()
        and directory.resolve() != state["destination"].resolve()
        for path in directory.rglob("*results.jsonl")
        if path.is_file()
    }
    plans = sorted(set(plans) | rank11_plans)
    results = sorted(set(results) | rank11_results)
    if [core.artifact(path) for path in plans] != state["priorPlans"]:
        raise ValueError("prior F5 plan boundary changed")
    if [core.artifact(path) for path in results] != state["priorResults"]:
        raise ValueError("prior F5 result boundary changed")
    if core.mutable_boundary(
        state["destination"], state["manifest"], ignored_data_paths=ignored
    ) != state["censusVolatile"]:
        raise ValueError("volatile boundary changed during empirical census")


def seal(state: dict) -> tuple[str, str]:
    preview = state["preview"]
    if preview["blockers"]:
        raise ValueError("refusing to seal blocked empirical F5 wave")
    assert_stable(state)
    destination = state["destination"]
    manifest = state["manifest"]
    paths = state["paths"]
    selected = state["selected"]
    frontier_rows = sorted(
        state["representatives"],
        key=lambda row: (
            int(row["source"]["t"]),
            int(row["source"]["r"]),
            row["canonicalQuotientSha256"],
        ),
    )
    selected_by_identity = {candidate_identity(row): row for row in selected}
    frontier_rows = [
        selected_by_identity.get(candidate_identity(row), row) for row in frontier_rows
    ]
    selected_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True) for row in selected
    }
    frontier_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True)
        for row in frontier_rows
    }
    if len(selected_exact) != len(selected) or not selected_exact <= frontier_exact:
        raise ValueError("selected rows are not an exact frontier subset")
    index_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in frontier_rows
    )
    claimed_text = json.dumps(
        {
            "pairs": [
                {"label": label, "r": signature}
                for label, signature in sorted(state["workerClaimed"])
            ],
            "sourceArtifacts": state["preview"]["pinnedBoundaries"]["claims"],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    volatile = core.mutable_boundary(destination, manifest)
    command = (
        "/usr/bin/caffeinate -i /usr/local/bin/sage -python "
        f"{WORKER.name} --plan {core.relative(paths['plan'])}"
    )
    pinned_inputs = [
        state["initialCore"]["frozenGold"],
        state["initialCore"]["database"],
        state["initialCore"]["baseWorker"],
        state["initialCore"]["selector"],
        *state["preview"]["pinnedBoundaries"]["claims"],
        *state["priorPlans"],
        *state["priorResults"],
    ]
    plan = {
        "schemaVersion": "f5-untried-frontier-plan-v1",
        "waveId": preview["waveId"],
        "status": "ready_for_one_heavy_worker",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "frontier": preview["selection"],
        "ledgerInventory": preview["census"],
        "selectedSources": selected,
        "selector": {
            "schemaVersion": preview["schemaVersion"],
            "training": preview["training"],
            "reference": preview["reference"],
        },
        "artifacts": {
            "actionShards": state["actionArtifacts"],
            "databaseSidecars": {"wal": state["initialCore"]["databaseWal"]},
            "frozenGold": state["initialCore"]["frozenGold"],
            "frontierIndex": core.payload_artifact(paths["index"], index_text),
            "claimedPairIndex": core.payload_artifact(paths["claimed"], claimed_text),
            "pinnedInputs": pinned_inputs,
            "priorPlans": state["workerPriorPlans"],
            "priorResults": state["workerPriorResults"],
            "allPriorPlans": state["priorPlans"],
            "allPriorResults": state["priorResults"],
            "worker": state["initialCore"]["worker"],
            "results": core.relative(paths["results"]),
            "summary": core.relative(paths["summary"]),
            "manifest": core.relative(manifest),
            "claimsDirectory": core.relative(paths["claims"]),
        },
        "targetBoundary": preview["pinnedBoundaries"]["targets"],
        "staleQueueException": preview["queueHealth"]["pinnedStaleQueueException"],
        "volatileBoundary": volatile,
        "execution": {
            "commandTemplate": command + " --expected-plan-sha256 <PLAN_SHA256>",
            "heavyWorkerLaunched": False,
            "oneWorkerAtATimeLockRequired": True,
            "submissionAuthorized": False,
            "preSubmissionRefreshRequired": True,
            "preSubmissionRefreshChecks": [
                "liveTargets",
                "queueBoundary",
                "nonbaseline",
                "locallyUnowned",
                "knownCoefficientHashes",
                "externalClaims",
            ],
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    plan_sha = core.sha256_bytes(plan_text.encode())
    checks = {
        "selectedSourceCountMatchesRequest": len(selected)
        == int(preview["selection"]["requestedSources"]),
        "sourceSignaturesUnique": len(selected)
        == len({(row["source"]["label"], row["source"]["r"]) for row in selected}),
        "canonicalSourcesUnique": len(selected)
        == len({row["canonicalQuotientSha256"] for row in selected}),
        "allSourcesUnaligned": all(
            int(row["source"]["r"])
            not in {int(pair["r"]) for pair in row["possibleGoldPairs"]}
            for row in selected
        ),
        "pairSetsDisjoint": preview["selection"]["pairSlotOverlap"] == 0,
        "heightCapEnforced": all(
            int(row["coefficientHeightBits"])
            <= int(preview["selection"]["heightCapBits"])
            for row in selected
        ),
        "zeroPriorTrialsPerSourceSignature": preview["selection"][
            "priorResultSourceSignatureOverlap"
        ]
        == 0,
        "selectedRowsExactFrontierSubset": selected_exact <= frontier_exact,
        "referenceAggregateMatchedOrExplicitlyAccepted": (
            preview["reference"]["matches"]
            or preview["reference"]["currentAggregateExplicitlyAccepted"]
        ),
        "coefficientFreePlan": "coefficientLine" not in plan_text
        and "quotientLine" not in plan_text,
        "pinnedStaleQueueGate": not preview["queueHealth"]["activeQueuedSubmissions"]
        or (
            preview["queueHealth"]["pinnedStaleQueueException"]["enabled"]
            and preview["queueHealth"]["pinnedStaleQueueException"]["certified"]
        ),
    }
    if not all(checks.values()):
        raise ValueError("empirical F5 preflight checks failed")
    launch = command + f" --expected-plan-sha256 {plan_sha}"
    preflight = {
        "schemaVersion": "f5-unaligned-empirical-preflight-v1",
        "status": "certified_light_only_wave_ready",
        "checks": checks,
        "plan": core.payload_artifact(paths["plan"], plan_text),
        "launchCommand": launch,
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }
    preflight_text = json.dumps(preflight, indent=2, sort_keys=True) + "\n"
    assert_stable(state)
    if core.mutable_boundary(destination, manifest) != volatile:
        raise ValueError("volatile boundary changed before empirical seal")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        core.atomic_text(staging / paths["index"].name, index_text)
        core.atomic_text(staging / paths["claimed"].name, claimed_text)
        core.atomic_text(staging / paths["plan"].name, plan_text)
        core.atomic_text(staging / paths["preflight"].name, preflight_text)
        if destination.exists():
            raise ValueError("destination appeared during empirical seal")
        os.rename(staging, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if staging.exists():
            for child in staging.iterdir():
                if child.is_file():
                    child.unlink()
            staging.rmdir()
    return plan_sha, core.sha256_bytes(preflight_text.encode())


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-sources", type=int, default=50)
    parser.add_argument("--height-cap-bits", type=int, default=256)
    parser.add_argument("--allow-pinned-stale-queue", action="store_true")
    parser.add_argument(
        "--accept-current-aggregate",
        action="store_true",
        help=(
            "Explicitly seal the current deterministic aggregate when the "
            "evolving ledger/reservation boundary no longer matches the "
            "historical reference aggregate."
        ),
    )
    parser.add_argument("--stdout-only", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--seal", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if not core.SAFE_WAVE_ID.fullmatch(args.wave_id):
        raise ValueError("wave-id must be lowercase alphanumeric/underscore")
    if not 1 <= args.max_sources <= 50:
        raise ValueError("max-sources must be within 1..50")
    if not 1 <= args.height_cap_bits <= 256:
        raise ValueError("height cap must be within 1..256")
    if args.stdout_only and args.seal:
        raise ValueError("stdout-only mode cannot seal")
    if not args.stdout_only and args.report is None:
        raise ValueError("report is required unless stdout-only is used")
    state = build_state(args)
    preview = state["preview"]
    if args.aggregate_only:
        print(
            json.dumps(
                {
                    "status": preview["status"],
                    "blockers": preview["blockers"],
                    "training": {
                        key: preview["training"][key]
                        for key in ("developmentRows", "holdoutRows", "resolvedRows", "profiles")
                    },
                    "census": preview["census"],
                    "selection": preview["selection"],
                    "reference": preview["reference"],
                    "runtimeEstimate": preview["runtimeEstimate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif args.stdout_only:
        print(json.dumps(preview, indent=2, sort_keys=True))
    else:
        assert_stable(state)
        core.atomic_text(state["report"], json.dumps(preview, indent=2, sort_keys=True) + "\n")
        if args.seal:
            plan_sha, preflight_sha = seal(state)
            print(json.dumps({"planSha256": plan_sha, "preflightSha256": preflight_sha}, sort_keys=True))
        else:
            print(json.dumps({"report": core.relative(state["report"]), "status": preview["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Expected-value portfolio scheduler for fresh IGP24 target snapshots."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import candidate_hash, canonical_coefficients


DEFAULT_ALLOCATIONS = {
    "gold": 0.45,
    "raid": 0.25,
    "exploitation": 0.15,
    "exploration": 0.10,
    "calibration": 0.05,
}


@dataclass(frozen=True)
class TargetState:
    t: int
    r: int
    team_count: int
    baseline: bool = False
    snapshot_id: str | None = None
    captured_at: str | None = None
    minimum_disc_abs: int | None = None
    staleness_hazard: float = 0.0
    dilution_hazard: float = 0.0

    @property
    def immediate_value(self) -> float:
        return 2.0 ** (-self.team_count)


@dataclass(frozen=True)
class CandidateOption:
    coefficients: str
    target_t: int
    target_r: int
    label_probability: float
    recipe_family: str
    recipe_lineage: str
    local_root_count: int
    cpu_ms: float = 0.0
    valid_probability: float = 1.0
    hold_hours: float = 0.0
    retention_hours: float = 24.0 * 7
    information_value: float = 0.0
    preferred_bucket: str | None = None
    estimated_disc_abs: int | None = None
    submission_ready: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def candidate_hash(self) -> str:
        return candidate_hash(self.coefficients)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateOption":
        line = value.get("coefficients") or value.get("line")
        if line is None:
            raise ValueError("candidate record has no coefficients/line")
        target_t = value.get("target_t", value.get("tgt"))
        target_r = value.get("target_r", value.get("r"))
        if target_t is None or target_r is None:
            raise ValueError("candidate record has no target pair")
        family = str(value.get("recipe_family") or value.get("fam") or "unknown")
        lineage = str(
            value.get("recipe_lineage")
            or value.get("recipe_id")
            or value.get("parent_recipe_id")
            or family
        )
        probability = value.get("label_probability", value.get("predicted_probability", 1.0))
        estimated_disc_abs = _candidate_disc_abs(value)
        hold_hours = value.get("hold_hours")
        if hold_hours is None and value.get("selected_at"):
            selected_at = datetime.fromisoformat(str(value["selected_at"]).replace("Z", "+00:00"))
            if selected_at.tzinfo is None:
                selected_at = selected_at.replace(tzinfo=timezone.utc)
            hold_hours = max(0.0, (datetime.now(timezone.utc) - selected_at).total_seconds() / 3600.0)
        return cls(
            coefficients=canonical_coefficients(line),
            target_t=int(target_t),
            target_r=int(target_r),
            label_probability=float(probability),
            recipe_family=family,
            recipe_lineage=lineage,
            local_root_count=int(value.get("local_root_count", value.get("r"))),
            cpu_ms=float(value.get("cpu_ms", 0.0) or 0.0),
            valid_probability=float(value.get("valid_probability", 1.0)),
            hold_hours=float(hold_hours or 0.0),
            retention_hours=float(value.get("retention_hours", 24.0 * 7)),
            information_value=float(value.get("information_value", 0.0)),
            preferred_bucket=value.get("bucket"),
            estimated_disc_abs=estimated_disc_abs,
            submission_ready=candidate_submission_ready(value),
            metadata=dict(value),
        )


@dataclass(frozen=True)
class RankedCandidate:
    candidate: CandidateOption
    target: TargetState
    expected_value: float
    bucket: str
    success_probability: float
    discriminant_ratio: float


class PortfolioScheduler:
    def __init__(
        self,
        allocations: Mapping[str, float] = DEFAULT_ALLOCATIONS,
        *,
        max_per_target: int = 2,
        cpu_cost_per_second: float = 0.0,
        server_slot_cost: float = 0.0,
        min_expected_value: float = 0.0,
        unknown_discriminant_ratio: float = 0.5,
    ):
        if max_per_target < 1:
            raise ValueError("max_per_target must be positive")
        total = sum(float(value) for value in allocations.values())
        if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("allocation weights must sum to 1")
        self.allocations = dict(allocations)
        self.max_per_target = max_per_target
        self.cpu_cost_per_second = cpu_cost_per_second
        self.server_slot_cost = server_slot_cost
        self.min_expected_value = min_expected_value
        if not 0.0 <= unknown_discriminant_ratio <= 1.0:
            raise ValueError("unknown_discriminant_ratio must be in [0, 1]")
        self.unknown_discriminant_ratio = unknown_discriminant_ratio

    def rank(
        self,
        candidates: Iterable[CandidateOption],
        targets: Mapping[tuple[int, int], TargetState],
        owned_pairs: set[tuple[int, int]] | None = None,
    ) -> list[RankedCandidate]:
        owned_pairs = owned_pairs or set()
        ranked = []
        for candidate in candidates:
            if not candidate.submission_ready:
                continue
            pair = (candidate.target_t, candidate.target_r)
            target = targets.get(pair)
            if not target or target.baseline or pair in owned_pairs:
                continue
            if candidate.local_root_count != candidate.target_r:
                continue
            if not (0.0 <= candidate.label_probability <= 1.0):
                continue
            survival = math.exp(-target.staleness_hazard * candidate.hold_hours)
            retention = math.exp(
                -target.dilution_hazard * candidate.retention_hours / 2.0
            )
            success_probability = (
                candidate.valid_probability
                * candidate.label_probability
                * survival
                * retention
            )
            disc_ratio = expected_discriminant_ratio(
                target,
                candidate.estimated_disc_abs,
                unknown=self.unknown_discriminant_ratio,
            )
            raw = success_probability * target.immediate_value * disc_ratio
            cost = self.cpu_cost_per_second * candidate.cpu_ms / 1000.0 + self.server_slot_cost
            value = raw + candidate.information_value - cost
            if value < self.min_expected_value:
                continue
            ranked.append(
                RankedCandidate(
                    candidate,
                    target,
                    value,
                    _bucket(candidate, target),
                    success_probability,
                    disc_ratio,
                )
            )
        return sorted(
            ranked,
            key=lambda item: (
                -item.expected_value,
                item.candidate.target_t,
                item.candidate.target_r,
                item.candidate.candidate_hash,
            ),
        )

    def select(
        self,
        candidates: Iterable[CandidateOption],
        targets: Mapping[tuple[int, int], TargetState],
        limit: int,
        owned_pairs: set[tuple[int, int]] | None = None,
    ) -> list[RankedCandidate]:
        if limit <= 0:
            return []
        ranked = self.rank(candidates, targets, owned_pairs)
        by_bucket: dict[str, list[RankedCandidate]] = defaultdict(list)
        for item in ranked:
            by_bucket[item.bucket].append(item)

        quotas = _integer_quotas(limit, self.allocations)
        selected: list[RankedCandidate] = []
        selected_hashes: set[str] = set()
        target_counts: Counter[tuple[int, int]] = Counter()
        target_lineages: dict[tuple[int, int], set[str]] = defaultdict(set)

        def consider(item: RankedCandidate) -> bool:
            candidate = item.candidate
            pair = (candidate.target_t, candidate.target_r)
            if candidate.candidate_hash in selected_hashes:
                return False
            if target_counts[pair] >= self.max_per_target:
                return False
            if candidate.recipe_lineage in target_lineages[pair]:
                return False
            selected.append(item)
            selected_hashes.add(candidate.candidate_hash)
            target_counts[pair] += 1
            target_lineages[pair].add(candidate.recipe_lineage)
            return True

        for bucket, quota in quotas.items():
            for item in by_bucket.get(bucket, []):
                if sum(1 for chosen in selected if chosen.bucket == bucket) >= quota:
                    break
                consider(item)

        # Unused allocation is reserve, not permission to add low-EV candidates.
        remaining = [item for item in ranked if item.candidate.candidate_hash not in selected_hashes]
        for item in remaining:
            if len(selected) >= limit:
                break
            consider(item)
        return sorted(selected, key=lambda item: -item.expected_value)


def load_candidates(path: str | Path) -> list[CandidateOption]:
    out = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                expires_at = value.get("expires_at")
                if expires_at:
                    expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires <= datetime.now(timezone.utc):
                        continue
                out.append(CandidateOption.from_mapping(value))
            except Exception as exc:
                raise ValueError(f"invalid candidate shard line {number}: {exc}") from exc
    return out


def candidate_submission_ready(value: Mapping[str, Any]) -> bool:
    """Return whether a candidate carries sufficient exact-label evidence.

    Older exact-construction shards predate calibration metadata and remain
    eligible.  Structural-oracle shards explicitly carry
    ``family_profile_support``; zero support is therefore an affirmative
    uncalibrated marker, not missing data.  A reproducible prospective audit
    or an exact compatibility proof may override it.
    """

    status = str(value.get("calibration_status") or "").strip().lower()
    if status in {
        "blocked",
        "failed",
        "invalidated",
        "needs_calibration",
        "needs_prospective_calibration",
    }:
        return False
    if value.get("prospective_calibration_passed") is False:
        return False
    if value.get("submission_ready") is not None:
        return bool(value["submission_ready"])
    if value.get("prospective_calibration_passed") is True:
        return True
    if bool(value.get("exact_compatibility_proven")):
        return True
    support = value.get("family_profile_support")
    if support is not None:
        try:
            return int(support) > 0
        except (TypeError, ValueError):
            return False
    return True


def targets_from_ledger(rows: Mapping[tuple[int, int], Mapping[str, Any]]) -> dict[tuple[int, int], TargetState]:
    return {
        pair: TargetState(
            t=int(row["t"]),
            r=int(row["r"]),
            team_count=int(row["team_count"]),
            baseline=bool(row["baseline"]),
            snapshot_id=row["snapshot_id"],
            captured_at=row["captured_at"],
            minimum_disc_abs=_positive_int(row["minimum_disc_abs"]),
        )
        for pair, row in rows.items()
    }


def load_owned_pairs(project: str | Path | None = None) -> set[tuple[int, int]]:
    project = Path(project) if project else Path(__file__).resolve().parent.parent
    owned: set[tuple[int, int]] = set()
    state_path = project / "daemon" / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        owned.update((int(t), int(r)) for t, r in state.get("team_pairs", []))
    for knowledge_path in (project / "daemon" / "knowledge.jsonl", project / "routeA" / "knowledge.jsonl"):
        if not knowledge_path.exists():
            continue
        with knowledge_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    owned.add((int(row["t"]), int(row["r"])))
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
    return owned


def effective_sample_size(count: int, intraclass_correlation: float) -> float:
    if count <= 0:
        return 0.0
    if not (0.0 <= intraclass_correlation <= 1.0):
        raise ValueError("intraclass correlation must be in [0, 1]")
    return count / (1.0 + (count - 1) * intraclass_correlation)


def expected_discriminant_ratio(
    target: TargetState,
    candidate_disc_abs: int | None,
    *,
    unknown: float = 0.5,
) -> float:
    """Estimate log(D0)/log(D), capped at one as in the official score."""

    if target.team_count == 0:
        return 1.0
    if target.minimum_disc_abs is None or candidate_disc_abs is None:
        return unknown
    return discriminant_score_ratio(target.minimum_disc_abs, candidate_disc_abs)


def discriminant_score_ratio(reference_disc_abs: int, candidate_disc_abs: int) -> float:
    reference = abs(int(reference_disc_abs))
    candidate = abs(int(candidate_disc_abs))
    if reference <= 1 or candidate <= 1:
        return 0.0
    return min(1.0, max(0.0, _log_integer(reference) / _log_integer(candidate)))


def _log_integer(value: int) -> float:
    """Compute log(value) without overflowing on very large Python integers."""

    shift = max(0, value.bit_length() - 53)
    return math.log(value >> shift) + shift * math.log(2.0)


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = abs(int(value))
    except (TypeError, ValueError):
        return None
    return number if number > 1 else None


def _candidate_disc_abs(value: Mapping[str, Any]) -> int | None:
    keys = (
        "estimated_nfdisc_abs",
        "predicted_nfdisc_abs",
        "estimated_discriminant_abs",
        "predicted_discriminant_abs",
        "nfdisc_abs",
    )
    for key in keys:
        parsed = _positive_int(value.get(key))
        if parsed is not None:
            return parsed
    features = value.get("discriminant_features")
    if isinstance(features, Mapping):
        for key in keys:
            parsed = _positive_int(features.get(key))
            if parsed is not None:
                return parsed
    return None


def _bucket(candidate: CandidateOption, target: TargetState) -> str:
    if candidate.preferred_bucket in DEFAULT_ALLOCATIONS:
        return str(candidate.preferred_bucket)
    if target.team_count == 0:
        return "gold"
    if target.team_count == 1:
        return "raid"
    if candidate.information_value > 0:
        return "exploration"
    return "exploitation"


def _integer_quotas(limit: int, allocations: Mapping[str, float]) -> dict[str, int]:
    exact = {key: limit * value for key, value in allocations.items()}
    quotas = {key: int(value) for key, value in exact.items()}
    remainder = limit - sum(quotas.values())
    order = sorted(exact, key=lambda key: exact[key] - quotas[key], reverse=True)
    for key in order[:remainder]:
        quotas[key] += 1
    return quotas

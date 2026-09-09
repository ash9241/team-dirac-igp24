#!/usr/bin/env python3
"""Dry-run-first staged submission controller for targeted IGP24 campaigns."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from routeA.api_client import APIClient, AmbiguousSubmissionError  # noqa: E402
from routeA.ledger import DEFAULT_DB, Ledger, candidate_hash, utc_now  # noqa: E402
from routeA.progress import DEFAULT_ARCHIVE, DEFAULT_DATA, refresh_progress  # noqa: E402
from routeA.scheduler import (  # noqa: E402
    CandidateOption,
    PortfolioScheduler,
    RankedCandidate,
    load_candidates,
    load_owned_pairs,
    targets_from_ledger,
)


DEFAULT_REPORTS = Path(__file__).resolve().parent / "data" / "reports"


@dataclass
class WaveReport:
    wave_id: str
    dry_run: bool
    started_at: str
    completed_at: str | None
    candidate_pool: int
    selected_candidates: int
    submitted_batches: int
    forecast_score: float
    realized_score: float
    accepted: int
    exact_label_hits: int
    exact_pair_hits: int
    stale_or_owned_candidates: int
    duplicate_candidates: int
    uncalibrated_candidates: int
    unresolved_submissions: int
    passed_gate: bool
    gate_reasons: list[str]
    batch_uuids: list[str]
    submission_ids: list[str]


class StagedController:
    def __init__(
        self,
        ledger: Ledger,
        client: APIClient,
        scheduler: PortfolioScheduler | None = None,
        *,
        batch_size: int = 1000,
        reports_dir: str | os.PathLike[str] = DEFAULT_REPORTS,
    ):
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be in [1, 1000]")
        self.ledger = ledger
        self.client = client
        self.scheduler = scheduler or PortfolioScheduler()
        self.batch_size = batch_size
        self.reports_dir = Path(reports_dir)

    def refresh_targets(self) -> str:
        result = refresh_progress(
            self.client,
            self.ledger,
            data_dir=DEFAULT_DATA,
            archive_dir=DEFAULT_ARCHIVE,
        )
        return result.snapshot_id

    def plan_wave(
        self,
        candidates: Sequence[CandidateOption],
        candidate_limit: int,
    ) -> tuple[list[RankedCandidate], int, int, int]:
        targets = targets_from_ledger(self.ledger.latest_targets())
        if not targets:
            raise RuntimeError("no target snapshot is available; refresh progress first")
        owned = load_owned_pairs(PROJECT) | self.ledger.owned_pairs()
        unique: dict[str, CandidateOption] = {}
        duplicates = 0
        stale_or_owned = 0
        uncalibrated = 0
        for candidate in candidates:
            key = candidate.candidate_hash
            if key in unique or self.ledger.candidate_committed(key):
                duplicates += 1
                continue
            if not candidate.submission_ready:
                uncalibrated += 1
                continue
            if (candidate.target_t, candidate.target_r) in owned:
                stale_or_owned += 1
                continue
            unique[key] = candidate
        selected = self.scheduler.select(unique.values(), targets, candidate_limit, owned)
        stale_or_owned += max(0, len(unique) - len(self.scheduler.rank(unique.values(), targets, owned)))
        return selected, stale_or_owned, duplicates, uncalibrated

    def run_wave(
        self,
        candidates: Sequence[CandidateOption],
        *,
        candidate_limit: int,
        dry_run: bool = True,
        refresh: bool = True,
        poll_timeout: float = 3600,
        submit_spacing: float = 2.0,
    ) -> WaveReport:
        if not dry_run and not refresh:
            raise ValueError("executed waves must refresh targets immediately before selection")
        if refresh:
            self.refresh_targets()
        wave_id = str(uuid.uuid4())
        started = utc_now()
        selected, stale, duplicates, uncalibrated = self.plan_wave(
            candidates, candidate_limit
        )
        forecast = marginal_forecast(selected)
        batches = [selected[index:index + self.batch_size] for index in range(0, len(selected), self.batch_size)]
        if not dry_run and len(batches) > 1:
            raise ValueError(
                "an executed wave may contain only one server submission; "
                "reduce candidate_limit or increase batch_size"
            )

        batch_uuids: list[str] = []
        submission_ids: list[str] = []
        receipts = []
        for batch_index, batch in enumerate(batches):
            for ranked in batch:
                self._persist_candidate(ranked)
            lines = [ranked.candidate.coefficients for ranked in batch]
            batch_uuid = f"{wave_id}:{batch_index:04d}"
            receipt = self.client.submit_batch(
                lines, self.ledger, batch_uuid=batch_uuid, dry_run=dry_run
            )
            receipts.append((receipt, batch))
            batch_uuids.append(batch_uuid)
            if receipt.submission_id:
                submission_ids.append(receipt.submission_id)
            if not dry_run and batch_index + 1 < len(batches):
                time.sleep(submit_spacing)

        accepted = exact_labels = exact_pairs = 0
        realized = 0.0
        credited_pairs: set[tuple[int, int]] = set()
        if not dry_run:
            target_rows = self.ledger.latest_targets()
            for receipt, batch in receipts:
                if not receipt.submission_id:
                    continue
                item = self.client.poll_submission(
                    receipt.submission_id, timeout=poll_timeout
                )
                self.client.ingest_completed(receipt.batch_uuid, item, self.ledger)
                metrics = self._score_batch(batch, item, target_rows, credited_pairs)
                accepted += metrics[0]
                exact_labels += metrics[1]
                exact_pairs += metrics[2]
                realized += metrics[3]

        unresolved = len(self.ledger.unresolved_submissions()) if not dry_run else 0
        passed, reasons = evaluate_gate(
            dry_run=dry_run,
            selected=len(selected),
            candidate_pool=len(candidates),
            forecast=forecast,
            realized=realized,
            accepted=accepted,
            exact_labels=exact_labels,
            stale=stale,
            duplicates=duplicates,
            uncalibrated=uncalibrated,
            unresolved=unresolved,
        )
        report = WaveReport(
            wave_id=wave_id,
            dry_run=dry_run,
            started_at=started,
            completed_at=utc_now(),
            candidate_pool=len(candidates),
            selected_candidates=len(selected),
            submitted_batches=len(batches),
            forecast_score=forecast,
            realized_score=realized,
            accepted=accepted,
            exact_label_hits=exact_labels,
            exact_pair_hits=exact_pairs,
            stale_or_owned_candidates=stale,
            duplicate_candidates=duplicates,
            uncalibrated_candidates=uncalibrated,
            unresolved_submissions=unresolved,
            passed_gate=passed,
            gate_reasons=reasons,
            batch_uuids=batch_uuids,
            submission_ids=submission_ids,
        )
        self._write_report(report)
        return report

    def run_campaign(
        self,
        candidates: Sequence[CandidateOption],
        wave_candidate_counts: Sequence[int] = (25, 50, 100, 200),
        *,
        dry_run: bool = True,
        poll_timeout: float = 3600,
    ) -> list[WaveReport]:
        reports = []
        remaining = list(candidates)
        for candidate_count in wave_candidate_counts:
            if candidate_count <= 0:
                raise ValueError("wave candidate counts must be positive")
            report = self.run_wave(
                remaining,
                candidate_limit=candidate_count,
                dry_run=dry_run,
                refresh=True,
                poll_timeout=poll_timeout,
            )
            reports.append(report)
            used = {
                row["candidate_hash"]
                for batch_uuid in report.batch_uuids
                for row in self.ledger.batch_items(batch_uuid)
            }
            remaining = [candidate for candidate in remaining if candidate.candidate_hash not in used]
            if not report.passed_gate:
                break
        return reports

    def _persist_candidate(self, ranked: RankedCandidate) -> None:
        candidate = ranked.candidate
        recipe_id = self.ledger.upsert_recipe(
            {
                "family": candidate.recipe_family,
                "parameters": dict(candidate.metadata),
                "recipe_id": candidate.metadata.get("recipe_id"),
                "parent_recipe_id": candidate.metadata.get("parent_recipe_id"),
                "construction_overgroup": candidate.metadata.get("construction_overgroup"),
                "structural_fingerprint": candidate.metadata.get("structural_fingerprint"),
            }
        )
        key = self.ledger.upsert_candidate(
            {
                "coefficients": candidate.coefficients,
                "recipe_id": recipe_id,
                "local_irreducible": candidate.metadata.get("local_irreducible", True),
                "local_root_count": candidate.local_root_count,
                "discriminant_features": candidate.metadata.get("discriminant_features"),
                "feature_version": candidate.metadata.get("feature_version"),
                "cpu_ms": candidate.cpu_ms,
                "source_host": candidate.metadata.get("source_host", "worker"),
            }
        )
        self.ledger.record_prediction(
            {
                "candidate_hash": key,
                "model_version": candidate.metadata.get("model_version", "construction-v1"),
                "target_snapshot_id": ranked.target.snapshot_id,
                "label_probabilities": {str(candidate.target_t): candidate.label_probability},
                "prediction_set": [candidate.target_t],
                "expected_value": ranked.expected_value,
                "selected_t": candidate.target_t,
                "selected_r": candidate.target_r,
            }
        )

    def _score_batch(
        self,
        batch: Sequence[RankedCandidate],
        item: dict[str, Any],
        target_rows: dict[tuple[int, int], Any],
        credited_pairs: set[tuple[int, int]],
    ) -> tuple[int, int, int, float]:
        accepted = exact_labels = exact_pairs = 0
        score = 0.0
        for result in item.get("verifiedPolynomials") or []:
            position = result.get("polynomialIndex")
            if not isinstance(position, int) or not 0 <= position < len(batch):
                continue
            ranked = batch[position]
            candidate = ranked.candidate
            if result.get("status") != "accepted":
                continue
            accepted += 1
            if result.get("t") == candidate.target_t:
                exact_labels += 1
            pair = (result.get("t"), result.get("r"))
            intended = (candidate.target_t, candidate.target_r)
            if pair == intended:
                exact_pairs += 1
            row = target_rows.get(pair)
            if pair != intended or not row or pair in credited_pairs or bool(row["baseline"]):
                continue
            credited_pairs.add(pair)
            team_count = int(row["team_count"])
            reference_disc = _positive_int(row["minimum_disc_abs"])
            result_disc = _result_disc_abs(result)
            disc_ratio = 1.0 if team_count == 0 else _realized_disc_ratio(
                reference_disc, result_disc
            )
            points = 2.0 ** (-team_count) * disc_ratio
            score += points
            event_type = "gold" if int(row["team_count"]) == 0 else "raid" if int(row["team_count"]) == 1 else "shared"
            self.ledger.record_score_event(
                {
                    "candidate_hash": candidate.candidate_hash,
                    "t": pair[0],
                    "r": pair[1],
                    "pre_team_count": team_count,
                    "event_type": event_type,
                    "immediate_score": points,
                    "estimated_retained_score": points,
                    "snapshot_id": row["snapshot_id"],
                }
            )
        return accepted, exact_labels, exact_pairs, score

    def _write_report(self, report: WaveReport) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        target = self.reports_dir / f"wave_{report.wave_id}.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(report), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, target)


def marginal_forecast(selected: Iterable[RankedCandidate]) -> float:
    by_pair: dict[tuple[int, int], list[RankedCandidate]] = {}
    for item in selected:
        pair = (item.candidate.target_t, item.candidate.target_r)
        by_pair.setdefault(pair, []).append(item)
    total = 0.0
    for items in by_pair.values():
        failure_probability = 1.0
        pair_value = items[0].target.immediate_value
        for item in sorted(items, key=lambda value: value.expected_value, reverse=True):
            probability = item.success_probability
            total += (
                failure_probability
                * probability
                * pair_value
                * item.discriminant_ratio
            )
            failure_probability *= max(0.0, 1.0 - probability)
    return total


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = abs(int(value))
    except (TypeError, ValueError):
        return None
    return number if number > 1 else None


def _result_disc_abs(result: Mapping[str, Any]) -> int | None:
    for key in ("fieldDiscAbs", "nfdisc", "nfdiscAbs", "discriminant"):
        value = _positive_int(result.get(key))
        if value is not None:
            return value
    return None


def _realized_disc_ratio(reference: int | None, candidate: int | None) -> float:
    if reference is None or candidate is None:
        return 0.5
    from routeA.scheduler import discriminant_score_ratio

    return discriminant_score_ratio(reference, candidate)


def evaluate_gate(
    *,
    dry_run: bool,
    selected: int,
    candidate_pool: int,
    forecast: float,
    realized: float,
    accepted: int,
    exact_labels: int,
    stale: int,
    duplicates: int,
    uncalibrated: int,
    unresolved: int,
) -> tuple[bool, list[str]]:
    reasons = []
    if selected == 0:
        reasons.append("no positive-EV candidates selected")
    denominator = max(1, candidate_pool)
    if (stale + duplicates) / denominator > 0.05:
        reasons.append("stale/duplicate candidate rate exceeds 5%")
    if uncalibrated:
        reasons.append("one or more candidates lack exact-label calibration")
    if unresolved:
        reasons.append("one or more submissions remain unresolved")
    if not dry_run:
        if accepted >= 200 and exact_labels / accepted < 0.85:
            reasons.append("exact-label agreement is below 85%")
        if forecast > 0 and realized < 0.5 * forecast:
            reasons.append("realized score is below 50% of forecast")
    return not reasons, reasons


def _already_verified(ledger: Ledger, key: str) -> bool:
    return ledger.connection.execute(
        "SELECT 1 FROM verification WHERE candidate_hash=?", (key,)
    ).fetchone() is not None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_shard")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--candidate-limit", type=int, default=25)
    parser.add_argument(
        "--wave-schedule",
        help="comma-separated candidate counts, for example 25,50,100,200",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--no-refresh", action="store_true")
    parser.add_argument("--execute", action="store_true", help="perform real submissions")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS))
    args = parser.parse_args()
    candidates = load_candidates(args.candidate_shard)
    with Ledger(args.db) as ledger:
        controller = StagedController(
            ledger,
            APIClient(),
            batch_size=args.batch_size,
            reports_dir=args.reports_dir,
        )
        if args.wave_schedule:
            schedule = [int(value) for value in args.wave_schedule.split(",") if value]
            reports = controller.run_campaign(
                candidates, schedule, dry_run=not args.execute
            )
        else:
            reports = [controller.run_wave(
                candidates,
                candidate_limit=args.candidate_limit,
                dry_run=not args.execute,
                refresh=not args.no_refresh,
            )]
    print(json.dumps([asdict(report) for report in reports], indent=2, sort_keys=True))
    if not reports[-1].passed_gate:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

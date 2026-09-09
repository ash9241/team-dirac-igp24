#!/usr/bin/env python3
"""Build and locally certify the character-engineered reciprocal campaign."""

from __future__ import annotations

import argparse
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Union

from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import discriminant_score_ratio, load_owned_pairs
from routeA.constructions.cubic_tower_reciprocal import (
    CubicTowerReciprocalSpec,
    build_fiber_cubic_tower,
    build_product_cubic_tower,
    build_within_cubic_tower,
    certify_cubic_tower,
)
from routeA.constructions.twisted_reciprocal import (
    CriticalTwistedReciprocalSpec,
    TwistedReciprocalSpec,
    build_critical_twisted_reciprocal,
    build_twisted_reciprocal,
    certify_twisted_reciprocal,
)


PROJECT = Path(__file__).resolve().parent.parent


ReciprocalSpec = Union[
    TwistedReciprocalSpec,
    CriticalTwistedReciprocalSpec,
    CubicTowerReciprocalSpec,
]


@dataclass(frozen=True)
class CampaignEntry:
    spec: ReciprocalSpec
    target_r: int

    @property
    def pair(self) -> tuple[int, int]:
        return self.spec.target_t, self.target_r


def campaign_entries() -> list[CampaignEntry]:
    """Return the 26 deterministic candidates in the July 14 campaign."""

    entries: list[CampaignEntry] = []

    # 12T299 product character -> 24T24869.
    entries.append(CampaignEntry(build_twisted_reciprocal(-5, 0, "product"), 0))
    product_299 = [
        ((-6, -3, 4), -3, -1404, 75816, 4),
        ((-10, -5, 0), -10, -63750, 239062500, 8),
        ((-9, -6, 0), -9, -2727, 73629, 10),
        ((-10, -5, 0), -10, -43750, 38281250, 12),
        ((-12, -8, 0), 0, -20736, 214990848, 16),
        ((-18, -11, 4), 4, -14162, 100281122, 18),
        ((-6, -5, -4), -4, -4, 8, 20),
    ]
    for roots, mark, value, d, target_r in product_299:
        entries.append(CampaignEntry(
            build_critical_twisted_reciprocal(roots, mark, value, d, "product"),
            target_r,
        ))

    # 12T299 within character -> 24T24871.
    within_299 = [
        ((-9, -6, 0), -9, 526, 273760, 0),
        ((-5, -4, 4), -5, 1645, 2703424, 2),
        ((-6, -3, 4), -3, -960, 920304, 4),
        ((-6, 0, 1), 1, 4119, 16966017, 6),
        ((-9, -6, 0), -9, -954, 907200, 8),
        ((-6, 0, 1), 0, 3168, 10035200, 10),
        ((-9, -6, 0), -9, -1830, 3348576, 12),
        ((-11, -8, 4), -11, -14773, 218239504, 16),
        ((-6, -5, -4), -5, 11, 85, 20),
    ]
    for roots, mark, value, d, target_r in within_299:
        entries.append(CampaignEntry(
            build_critical_twisted_reciprocal(roots, mark, value, d, "within"),
            target_r,
        ))

    # 12T274 product character -> 24T24155.
    product_274 = [
        (-1, 1, 64, 6),
        (-1, 2, 10, 8),
        (1, 3, -432, 10),
        (1, 3, -200, 12),
        (3, 12, 200, 16),
        (-10, 57, 720, 18),
        (1, 1, 1, 20),
    ]
    for m, s, k, target_r in product_274:
        entries.append(CampaignEntry(build_product_cubic_tower(m, s, k), target_r))

    # 12T274 within character -> 24T24157.
    entries.append(CampaignEntry(build_within_cubic_tower(1, 1, 2), 20))

    # Aggregate cubic-fiber character -> 24T24151.
    entries.append(CampaignEntry(build_fiber_cubic_tower(1, 14790), 20))

    lines = [entry.spec.line for entry in entries]
    pairs = [entry.pair for entry in entries]
    if len(entries) != 26 or len(set(lines)) != 26 or len(set(pairs)) != 26:
        raise AssertionError("campaign must contain 26 unique polynomials and target pairs")
    return entries


def forecast_campaign(
    entries: Sequence[CampaignEntry],
    progress: Sequence[Mapping[str, Any]],
    owned_pairs: Iterable[tuple[int, int]] = (),
    candidate_discriminants: Mapping[tuple[int, int], int] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Score the campaign against sharing and discriminant competition.

    Without ``candidate_discriminants`` the result is the sharing-factor
    ceiling.  Certified field discriminants produce the official
    ``log(D0)/log(D)`` adjustment for already discovered signatures.
    """

    owned = {(int(t), int(r)) for t, r in owned_pairs}
    labels = {
        int(row.get("t") or str(row["label"]).replace("24T", "")): row
        for row in progress
    }
    rows: list[dict[str, Any]] = []
    for entry in entries:
        label = labels[entry.spec.target_t]
        signature = next(
            item for item in label["signatures"] if int(item["r"]) == entry.target_r
        )
        team_count = int(signature["teamCount"])
        ceiling = 2.0 ** (-team_count)
        candidate_disc = (
            None if candidate_discriminants is None
            else int(candidate_discriminants[entry.pair])
        )
        reference_disc = signature.get("minimumDiscAbs")
        disc_ratio = 1.0
        if candidate_disc is not None and reference_disc is not None:
            disc_ratio = discriminant_score_ratio(
                int(reference_disc), candidate_disc
            )
        forecast = 0.0 if entry.pair in owned else ceiling * disc_ratio
        rows.append({
            "target_t": entry.spec.target_t,
            "target_r": entry.target_r,
            "team_count": team_count,
            "score_ceiling": ceiling,
            "candidate_disc_abs": candidate_disc,
            "reference_disc_abs": reference_disc,
            "discriminant_ratio": disc_ratio,
            "forecast_points": forecast,
            "gold": team_count == 0,
            "already_owned": entry.pair in owned,
            "coefficients": entry.spec.line,
            "parameters": entry.spec.parameters,
        })
    return rows, sum(float(row["forecast_points"]) for row in rows)


def filter_owned_entries(
    entries: Sequence[CampaignEntry],
    owned_pairs: Iterable[tuple[int, int]],
) -> tuple[list[CampaignEntry], list[CampaignEntry]]:
    """Split a campaign into actionable and already-owned target rows."""

    owned = {(int(t), int(r)) for t, r in owned_pairs}
    actionable = [entry for entry in entries if entry.pair not in owned]
    excluded = [entry for entry in entries if entry.pair in owned]
    return actionable, excluded


def known_owned_pairs(
    db_path: str | Path = DEFAULT_DB,
    project: str | Path = PROJECT,
) -> set[tuple[int, int]]:
    """Combine legacy ownership files with authoritative reconciled history."""

    with Ledger(db_path) as ledger:
        return load_owned_pairs(project) | ledger.owned_pairs()


def certify_entries(entries: Sequence[CampaignEntry]) -> list[dict[str, Any]]:
    records = []
    for entry in entries:
        if isinstance(entry.spec, CubicTowerReciprocalSpec):
            candidate = certify_cubic_tower(
                entry.spec, expected_root_count=entry.target_r
            )
        else:
            candidate = certify_twisted_reciprocal(
                entry.spec, expected_root_count=entry.target_r
            )
        records.append(candidate.to_json())
    return records


def _read_progress(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    return list(value.get("labels", value) if isinstance(value, dict) else value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progress")
    parser.add_argument("--payload")
    parser.add_argument("--manifest")
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--include-owned",
        action="store_true",
        help="include target rows already proven owned (unsafe for a live payload)",
    )
    args = parser.parse_args()
    all_entries = campaign_entries()
    owned = set() if args.include_owned else known_owned_pairs(args.db)
    entries, excluded = filter_owned_entries(all_entries, owned)
    if args.certify:
        manifest = certify_entries(entries)
    else:
        manifest = [
            {
                "target_t": entry.spec.target_t,
                "target_r": entry.target_r,
                "coefficients": entry.spec.line,
                "parameters": entry.spec.parameters,
            }
            for entry in entries
        ]
    if args.progress:
        candidate_discriminants = None
        if args.certify:
            candidate_discriminants = {
                (int(row["target_t"]), int(row["target_r"])):
                    int(row["field_disc_abs"])
                for row in manifest
            }
        forecast, total = forecast_campaign(
            entries,
            _read_progress(args.progress),
            owned_pairs=owned,
            candidate_discriminants=candidate_discriminants,
        )
        by_pair = {(row["target_t"], row["target_r"]): row for row in forecast}
        for row in manifest:
            row.update(by_pair[(int(row["target_t"]), int(row["target_r"]))])
        print(json.dumps({
            "campaign_candidates": len(all_entries),
            "actionable_candidates": len(entries),
            "excluded_owned": [entry.pair for entry in excluded],
            "forecast_kind": (
                "discriminant_adjusted" if args.certify else "sharing_ceiling"
            ),
            "forecast_points": total,
        }, sort_keys=True))
    if args.payload:
        Path(args.payload).write_text(
            "\n".join(entry.spec.line for entry in entries) + "\n", encoding="utf-8"
        )
    if args.manifest:
        Path(args.manifest).write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in manifest) + "\n",
            encoding="utf-8",
        )
    if not args.payload and not args.manifest:
        print("\n".join(entry.spec.line for entry in entries))


if __name__ == "__main__":
    main()

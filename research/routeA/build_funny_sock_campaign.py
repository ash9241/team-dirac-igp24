#!/usr/bin/env python3
"""Build and certify the 25-row campaign aimed at overtaking Funny Sock."""

from __future__ import annotations

import argparse
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.constructions.nested_character_lift import (
    NestedCharacterLiftCandidate,
    NestedCharacterLiftSpec,
    build_nested_character_lift,
    certify_nested_character_lift,
)
from routeA.ledger import DEFAULT_DB, Ledger
from routeA.scheduler import discriminant_score_ratio, load_owned_pairs


PROJECT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CampaignEntry:
    spec: NestedCharacterLiftSpec
    target_r: int

    @property
    def pair(self) -> tuple[int, int]:
        return self.spec.target_t, self.target_r


def _entry(
    target_r: int,
    *,
    family: str,
    base_t: int,
    target_t: int,
    q: Sequence[int],
    e: int,
    d: int,
    norm: int,
    roots: Sequence[int],
    quadratics: Sequence[tuple[int, int]] = (),
    sign: int = 1,
) -> CampaignEntry:
    return CampaignEntry(
        build_nested_character_lift(
            family=family,
            base_t=base_t,
            target_t=target_t,
            q_coefficients=q,
            e=e,
            d=d,
            expected_norm_squareclass=norm,
            h_linear_roots=roots,
            h_positive_quadratics=quadratics,
            h_sign=sign,
        ),
        int(target_r),
    )


def campaign_entries() -> list[CampaignEntry]:
    entries: list[CampaignEntry] = []

    # 12T249 product character (squareclass 34) -> 24T23311.
    product_249 = [
        (0, 1, (4,), ((4, 6),)),
        (4, -1, (2, -2, 4, -1, 3), ()),
        (8, -1, (2,), ()),
        (12, -1, (2, -1, 3), ()),
        (16, 1, (-2,), ()),
        (20, 1, (-2, -4, 2, -3, 1), ()),
        (24, -1, (-4,), ((-4, 6),)),
    ]
    for target_r, c, roots, quadratics in product_249:
        entries.append(_entry(
            target_r,
            family="nested_12T249_product_character",
            base_t=249,
            target_t=23311,
            q=(c, -12, 0, 1),
            e=105,
            d=7200,
            norm=34,
            roots=roots,
            quadratics=quadratics,
        ))

    # 12T249 top character (squareclass 2) -> 24T23312.
    # The endpoint multipliers have square norm and break the smaller
    # 24T12524 specialization while staying positive on every real root.
    top_249 = [
        (0, -1, (4,), ((-14, 24),)),
        (4, 1, (-3, -2, 1, 2, 4), ()),
        (8, 1, (2,), ()),
        (12, -1, (-4, -1, 2, 3, 4), ()),
        (16, -1, (-2,), ()),
        (20, -1, (-4, -2, -1, 2, 3), ()),
        (24, 1, (-4,), ((-14, 24),)),
    ]
    for target_r, c, roots, quadratics in top_249:
        entries.append(_entry(
            target_r,
            family="nested_12T249_top_character",
            base_t=249,
            target_t=23312,
            q=(c, -12, 0, 1),
            e=105,
            d=7200,
            norm=2,
            roots=roots,
            quadratics=quadratics,
        ))

    # 12T210 d*kappa character (squareclass 10) -> 24T22543.
    d_kappa_210 = [
        (0, (3,), ((3, 9),)),
        (4, (-6, -4, 3), ()),
        (8, (0,), ()),
        (12, (-4,), ()),
        (16, (-6,), ()),
        (20, (-9, -6, -4), ()),
        (24, (-9,), ((-9, 9),)),
    ]
    for target_r, roots, quadratics in d_kappa_210:
        entries.append(_entry(
            target_r,
            family="nested_12T210_d_kappa_character",
            base_t=210,
            target_t=22543,
            q=(-74, 0, 9, 1),
            e=181,
            d=14265,
            norm=10,
            roots=roots,
            quadratics=quadratics,
        ))

    # A second 12T210 quotient realizes the mu character (squareclass 2).
    for target_r, root in ((0, 0), (8, -2), (16, -6), (24, -8)):
        entries.append(_entry(
            target_r,
            family="nested_12T210_mu_character",
            base_t=210,
            target_t=22546,
            q=(14, 36, 12, 1),
            e=89,
            d=2737,
            norm=2,
            roots=(root,),
            quadratics=((-8, 6),),
        ))

    if len(entries) != 25:
        raise AssertionError("Funny Sock campaign must contain exactly 25 rows")
    if len({entry.pair for entry in entries}) != len(entries):
        raise AssertionError("campaign target rows are not unique")
    if len({entry.spec.line for entry in entries}) != len(entries):
        raise AssertionError("campaign polynomials are not unique")
    return entries


def known_owned_pairs(
    db_path: str | Path = DEFAULT_DB,
    project: str | Path = PROJECT,
) -> set[tuple[int, int]]:
    with Ledger(db_path) as ledger:
        return load_owned_pairs(project) | ledger.owned_pairs()


def filter_owned_entries(
    entries: Sequence[CampaignEntry],
    owned_pairs: Iterable[tuple[int, int]],
) -> tuple[list[CampaignEntry], list[CampaignEntry]]:
    owned = {(int(t), int(r)) for t, r in owned_pairs}
    return (
        [entry for entry in entries if entry.pair not in owned],
        [entry for entry in entries if entry.pair in owned],
    )


def certify_entries(
    entries: Sequence[CampaignEntry],
    *,
    gp: str | None = None,
    gap: str | None = None,
) -> list[NestedCharacterLiftCandidate]:
    candidates = []
    for entry in entries:
        kwargs: dict[str, Any] = {}
        if gp:
            kwargs["gp"] = gp
        if gap:
            kwargs["gap"] = gap
        candidates.append(certify_nested_character_lift(
            entry.spec,
            expected_root_count=entry.target_r,
            **kwargs,
        ))
    return candidates


def forecast_campaign(
    entries: Sequence[CampaignEntry],
    progress: Sequence[Mapping[str, Any]],
    *,
    owned_pairs: Iterable[tuple[int, int]] = (),
    candidate_discriminants: Mapping[tuple[int, int], int] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    labels = {
        int(row.get("t") or str(row["label"]).replace("24T", "")): row
        for row in progress
    }
    owned = {(int(t), int(r)) for t, r in owned_pairs}
    rows: list[dict[str, Any]] = []
    for entry in entries:
        signature = next(
            row for row in labels[entry.spec.target_t]["signatures"]
            if int(row["r"]) == entry.target_r
        )
        count = int(signature["teamCount"])
        reference = signature.get("minimumDiscAbs")
        candidate_disc = None if candidate_discriminants is None else int(
            candidate_discriminants[entry.pair]
        )
        ratio = 1.0
        if reference is not None and candidate_disc is not None:
            ratio = discriminant_score_ratio(int(reference), candidate_disc)
        points = 0.0 if entry.pair in owned else 2.0 ** (-count) * ratio
        rows.append({
            "target_t": entry.spec.target_t,
            "target_r": entry.target_r,
            "team_count": count,
            "gold": count == 0,
            "candidate_disc_abs": candidate_disc,
            "reference_disc_abs": reference,
            "discriminant_ratio": ratio,
            "forecast_points": points,
        })
    return rows, sum(float(row["forecast_points"]) for row in rows)


def _read_progress(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    return list(value.get("labels", value) if isinstance(value, dict) else value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progress")
    parser.add_argument("--payload")
    parser.add_argument("--manifest")
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--include-owned", action="store_true")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--gp")
    parser.add_argument("--gap")
    args = parser.parse_args()

    all_entries = campaign_entries()
    owned = set() if args.include_owned else known_owned_pairs(args.db)
    entries, excluded = filter_owned_entries(all_entries, owned)
    certified = certify_entries(entries, gp=args.gp, gap=args.gap) if args.certify else []
    if certified:
        manifest = [candidate.to_json() for candidate in certified]
    else:
        manifest = [{
            "coefficients": entry.spec.line,
            "target_t": entry.spec.target_t,
            "target_r": entry.target_r,
            "local_root_count": entry.target_r,
            "recipe_family": entry.spec.family,
            "parameters": entry.spec.parameters,
        } for entry in entries]

    summary: dict[str, Any] = {
        "campaign_candidates": len(all_entries),
        "actionable_candidates": len(entries),
        "excluded_owned": [entry.pair for entry in excluded],
    }
    if args.progress:
        discriminants = None
        if certified:
            discriminants = {
                (candidate.target_t, candidate.target_r): candidate.field_disc_abs
                for candidate in certified
            }
        rows, total = forecast_campaign(
            entries,
            _read_progress(args.progress),
            owned_pairs=owned,
            candidate_discriminants=discriminants,
        )
        by_pair = {(row["target_t"], row["target_r"]): row for row in rows}
        for row in manifest:
            row.update(by_pair[(int(row["target_t"]), int(row["target_r"]))])
        summary["forecast_points"] = total
        summary["forecast_kind"] = (
            "discriminant_adjusted" if certified else "sharing_ceiling"
        )

    if args.payload:
        Path(args.payload).write_text(
            "\n".join(entry.spec.line for entry in entries) + "\n",
            encoding="utf-8",
        )
    if args.manifest:
        Path(args.manifest).write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in manifest) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, sort_keys=True))
    if not args.payload and not args.manifest:
        print("\n".join(entry.spec.line for entry in entries))


if __name__ == "__main__":
    main()

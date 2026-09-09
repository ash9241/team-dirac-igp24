#!/usr/bin/env python3
"""Harvest exact subgroups from arbitrary linear Kummer seeds.

For every known degree-12 field ``K = Q(alpha)`` and integer shift ``c``,
the lift ``x^2-(alpha-c)`` lies in the exact full wreath overgroup
``C2 wr Gal(K^gal/Q)``.  Unit squareclasses steer the real signature.  We
then descend through transitive maximal subgroups using exact Frobenius
witnesses and retain only uniquely identified, currently valuable terminals.

Unlike character-kernel campaigns, this route needs no prior identification
of the rational norm character: full-wreath containment is automatic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from routeA.build_catalog_character_campaign import (
    DEFAULT_CATALOG,
    DEFAULT_MANIFEST,
    DEFAULT_PAYLOAD,
    RankedCatalogFamily,
    load_jsonl,
    recover_catalog_subgroups,
)
from routeA.build_funny_sock_campaign import known_owned_pairs
from routeA.build_t00035_campaign import _squarefree_part, forecast, write_payload
from routeA.constructions.integral_basis_character_lift import (
    family_from_integral_basis_seed,
)
from routeA.gap_full_wreath_map import DEFAULT_OUTPUT as DEFAULT_WREATH_MAP
from routeA.ledger import DEFAULT_DB, Ledger


def full_wreath_targets(path: str | Path = DEFAULT_WREATH_MAP) -> dict[int, int]:
    rows = load_jsonl(path)
    targets = {int(row["base_t"]): int(row["target_t"]) for row in rows}
    if set(targets) != set(range(1, 302)):
        raise ValueError("full-wreath map must cover all 301 degree-12 groups")
    return targets


def linear_wreath_families(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    wreath_map_path: str | Path = DEFAULT_WREATH_MAP,
    db_path: str | Path = DEFAULT_DB,
    shifts: range = range(-3, 4),
    bases: list[int] | None = None,
) -> list[RankedCatalogFamily]:
    catalog = load_jsonl(catalog_path)
    if not catalog:
        raise ValueError("degree-12 field catalog is empty")
    wreath_targets = full_wreath_targets(wreath_map_path)
    with Ledger(db_path) as ledger:
        live_targets = ledger.latest_targets()

    base_order = {base: index for index, base in enumerate(bases or [])}
    if bases:
        wanted = set(bases)
        catalog = [row for row in catalog if int(row["base_t"]) in wanted]
        catalog.sort(key=lambda row: (
            base_order[int(row["base_t"])],
            int(row["disc_abs"]),
        ))

    ranked: list[RankedCatalogFamily] = []
    for row in catalog:
        base_t = int(row["base_t"])
        target_t = wreath_targets[base_t]
        coefficients = tuple(int(value) for value in row["coefficients"])
        label_tag = str(row["label"]).replace(".", "_")
        live_ceiling = sum(
            2.0 ** (-int(target["team_count"]))
            for root in range(0, 25, 4)
            for target in [live_targets.get((target_t, root))]
            if target is not None and not bool(target["baseline"])
        )
        for shift in shifts:
            norm = sum(value * int(shift) ** power for power, value in enumerate(coefficients))
            if norm == 0:
                continue
            # Every requested signature has a positive total norm.  When the
            # seed itself has negative norm, the enumerated unit necessarily
            # contributes norm -1, so the realized squareclass is |core|.
            norm_class = abs(_squarefree_part(norm))
            shift_tag = f"m{abs(shift)}" if shift < 0 else f"p{shift}"
            family = family_from_integral_basis_seed(
                family=(
                    f"score700_wreath_linear_12t{base_t}_{target_t}_"
                    f"{label_tag}_c{shift_tag}"
                ),
                base_t=base_t,
                target_t=target_t,
                expected_norm_squareclass=norm_class,
                base_coefficients=coefficients,
                seed_coefficients=(-int(shift), 1),
                target_root_counts=tuple(range(0, 25, 4)),
                construction_overgroup=f"full_C2_wreath_over_12T{base_t}",
                character_name=f"arbitrary_linear_norm_squareclass_{norm_class}",
                base_root_count=12,
            )
            ranked.append(RankedCatalogFamily(
                family=family,
                live_ceiling=live_ceiling,
                catalog_label=str(row["label"]),
            ))
    return ranked


def seed_worker_targets(
    db_path: str | Path,
    snapshot_path: str | Path,
) -> int:
    """Seed a credential-free worker ledger with a captured live target table."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rows = load_jsonl(snapshot_path)
    normalized = [
        {
            "t": int(row["t"]),
            "r": int(row["r"]),
            "team_count": int(row.get("team_count", 0)),
            "discovered": bool(row.get("discovered", False)),
            "baseline": bool(row.get("baseline", False)),
            "immediate_value": float(
                row.get("immediate_value", 2.0 ** (-int(row.get("team_count", 0))))
            ),
            "minimum_disc_abs": row.get("minimum_disc_abs"),
        }
        for row in rows
    ]
    with Ledger(db_path) as ledger:
        ledger.record_target_snapshot(normalized)
    return len(normalized)


def load_owned_pair_snapshot(path: str | Path | None) -> set[tuple[int, int]]:
    if path is None:
        return set()
    return {
        (int(row["t"]), int(row["r"]))
        for row in load_jsonl(path)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--wreath-map", type=Path, default=DEFAULT_WREATH_MAP)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--target-snapshot",
        type=Path,
        help="credential-free JSONL target snapshot used to seed a worker DB",
    )
    parser.add_argument(
        "--owned-pairs",
        type=Path,
        help="credential-free JSONL snapshot of pairs already owned by the team",
    )
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--shift-min", type=int, default=-3)
    parser.add_argument("--shift-max", type=int, default=3)
    parser.add_argument("--limit-families", type=int)
    parser.add_argument("--start-family", type=int, default=1)
    parser.add_argument("--options-per-signature", type=int, default=4)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--minimum-value", type=float, default=0.125)
    args = parser.parse_args()

    if args.shift_min > args.shift_max:
        parser.error("--shift-min must not exceed --shift-max")
    if args.target_snapshot is not None:
        seed_worker_targets(args.db, args.target_snapshot)
    families = linear_wreath_families(
        catalog_path=args.catalog,
        wreath_map_path=args.wreath_map,
        db_path=args.db,
        shifts=range(args.shift_min, args.shift_max + 1),
        bases=args.base or None,
    )
    if args.limit_families is not None:
        families = families[: max(0, args.limit_families)]
    families = families[max(0, args.start_family - 1):]
    owned = known_owned_pairs(args.db) | load_owned_pair_snapshot(args.owned_pairs)
    manifest = recover_catalog_subgroups(
        families,
        manifest_path=args.manifest,
        db_path=args.db,
        owned_pairs=owned,
        options_per_signature=args.options_per_signature,
        coefficient_limit=args.coefficient_limit,
        prime_limit=args.prime_limit,
        all_roots=True,
        minimum_raw_value=max(0.0, args.minimum_value),
    )
    payload_rows = write_payload(manifest, args.payload, owned)
    rows, ceiling, adjusted = forecast(
        manifest,
        db_path=args.db,
        owned_pairs=owned,
    )
    print(json.dumps({
        "families": len(families),
        "manifest_rows": len(manifest),
        "payload_rows": payload_rows,
        "forecast_rows": len(rows),
        "sharing_ceiling": ceiling,
        "discriminant_adjusted_forecast": adjusted,
        "manifest": str(args.manifest),
        "payload": str(args.payload),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Certify and harvest uniquely calibrated linear character discoveries."""

from __future__ import annotations

import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from routeA.build_catalog_character_campaign import (
    DEFAULT_MANIFEST,
    DEFAULT_PAYLOAD,
    RankedCatalogFamily,
    certify_ranked_catalog,
    load_jsonl,
    recover_catalog_subgroups,
)
from routeA.build_funny_sock_campaign import known_owned_pairs
from routeA.build_t00035_campaign import forecast, write_payload
from routeA.constructions.integral_basis_character_lift import (
    family_from_integral_basis_seed,
)
from routeA.discover_linear_characters import DEFAULT_OUTPUT as DEFAULT_DISCOVERIES
from routeA.ledger import DEFAULT_DB, Ledger


def discovered_families(
    *,
    discoveries_path: str | Path = DEFAULT_DISCOVERIES,
    db_path: str | Path = DEFAULT_DB,
    pilot_only: bool = False,
) -> list[RankedCatalogFamily]:
    with Ledger(db_path) as ledger:
        targets = ledger.latest_targets()
    families: list[RankedCatalogFamily] = []
    for row in load_jsonl(discoveries_path):
        if row.get("server_calibrated") is False and not pilot_only:
            continue
        target_t = int(row["starting_target_t"])
        norm_squareclass = int(row["norm_squareclass"])
        target_roots = tuple(
            range(2 if norm_squareclass < 0 else 0, 25, 4)
        )
        if pilot_only:
            raw_pilot = row.get("pilot_target_r")
            target_roots = (
                (int(raw_pilot),)
                if raw_pilot is not None
                else _pilot_roots(norm_squareclass)
            )
            if target_roots[0] not in range(
                2 if norm_squareclass < 0 else 0,
                25,
                4,
            ):
                raise ValueError(
                    f"pilot root {target_roots[0]} has the wrong norm-sign parity"
                )
        ceiling = sum(
            2.0 ** (-int(target["team_count"]))
            for root in target_roots
            for target in [targets.get((target_t, root))]
            if target is not None and not bool(target["baseline"])
        )
        label_tag = str(row["label"]).replace(".", "_")
        seed, seed_tag = _discovery_seed(row)
        family = family_from_integral_basis_seed(
            family=(
                f"score700_discovered_linear_12t{row['base_t']}_{target_t}_"
                f"{label_tag}_{seed_tag}"
            ),
            base_t=int(row["base_t"]),
            target_t=target_t,
            expected_norm_squareclass=norm_squareclass,
            base_coefficients=row["base_coefficients"],
            seed_coefficients=seed,
            target_root_counts=target_roots,
            construction_overgroup=(
                f"uniquely_calibrated_character_kernel_over_12T{row['base_t']}"
            ),
            character_name=f"linear_norm_squareclass_{row['norm_squareclass']}",
            base_root_count=12,
        )
        families.append(RankedCatalogFamily(
            family=family,
            live_ceiling=ceiling,
            catalog_label=str(row["label"]),
        ))
    return sorted(
        families,
        key=lambda item: (-item.live_ceiling, item.family.base_t, item.family.target_t),
    )


def _pilot_roots(norm_squareclass: int) -> tuple[int, ...]:
    roots = tuple(range(2 if int(norm_squareclass) < 0 else 0, 25, 4))
    return (min(roots, key=lambda root: (abs(root - 12), root)),)


def _discovery_seed(row: dict[str, Any]) -> tuple[tuple[Fraction, ...], str]:
    """Load both legacy linear rows and product-character discovery rows."""

    raw_seed = row.get("seed_coefficients")
    if raw_seed is not None:
        seed = tuple(Fraction(value) for value in raw_seed)
        explicit_tag = str(row.get("seed_tag") or "").strip()
        if explicit_tag:
            safe_tag = "".join(
                character if character.isalnum() else "_"
                for character in explicit_tag
            ).strip("_")
            if safe_tag:
                return seed, safe_tag
        raw_factors = row.get("factor_shifts", ())
        factors = tuple(int(value) for value in raw_factors)
        rendered = "_".join(
            f"m{abs(value)}" if value < 0 else f"p{value}"
            for value in factors
        )
        digest = hashlib.sha256(
            ",".join(str(value) for value in seed).encode("utf-8")
        ).hexdigest()[:12]
        return seed, (
            f"product_{rendered}"
            if rendered
            else f"norm{int(row['norm_squareclass'])}_{digest}"
        )
    shift = int(row["shift"])
    shift_tag = f"m{abs(shift)}" if shift < 0 else f"p{shift}"
    return (Fraction(-shift), Fraction(1)), f"c{shift_tag}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discoveries", type=Path, default=DEFAULT_DISCOVERIES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--certify", action="store_true")
    parser.add_argument(
        "--pilot-only",
        action="store_true",
        help="certify only the central real signature for each discovered family",
    )
    parser.add_argument("--recover-subgroups", action="store_true")
    parser.add_argument("--recovery-all-roots", action="store_true")
    parser.add_argument("--options-per-signature", type=int, default=32)
    parser.add_argument("--recovery-options-per-signature", type=int, default=8)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--minimum-recovery-value", type=float, default=0.0)
    args = parser.parse_args()

    families = discovered_families(
        discoveries_path=args.discoveries,
        db_path=args.db,
        pilot_only=args.pilot_only,
    )
    owned = known_owned_pairs(args.db)
    if args.certify:
        manifest = certify_ranked_catalog(
            families,
            manifest_path=args.manifest,
            owned_pairs=owned,
            options_per_signature=args.options_per_signature,
            coefficient_limit=args.coefficient_limit,
            prime_limit=args.prime_limit,
        )
    else:
        manifest = load_jsonl(args.manifest)
    if args.recover_subgroups or args.recovery_all_roots:
        manifest = recover_catalog_subgroups(
            families,
            manifest_path=args.manifest,
            db_path=args.db,
            owned_pairs=owned,
            options_per_signature=args.recovery_options_per_signature,
            coefficient_limit=args.coefficient_limit,
            prime_limit=args.prime_limit,
            all_roots=args.recovery_all_roots,
            minimum_raw_value=max(0.0, args.minimum_recovery_value),
        )
    payload_rows = write_payload(manifest, args.payload, owned) if manifest else 0
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
    }, sort_keys=True))


if __name__ == "__main__":
    main()

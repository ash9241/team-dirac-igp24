#!/usr/bin/env python3
"""Build resumable sign and trivial character lifts from the degree-12 catalog.

For every totally real degree-12 field ``K = Q(a)`` in the local LMFDB
catalog, two Kummer generators are available without solving a norm equation:

* ``f'(a)`` has norm squareclass equal to the permutation-sign character;
* ``1`` has trivial norm squareclass.

Multiplication by units steers the number of positive embeddings and therefore
the real-root count of the degree-24 lift.  PARI's ``rnfequation`` keeps the
true integral-basis arithmetic, which is important for catalog polynomials
whose power basis has a large index.  Every retained row is then proved equal
to its claimed 24T group by excluding all transitive maximal subgroups.

The manifest is appended after each successful row, making a long catalog run
safe to interrupt and resume.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Literal

from routeA.build_funny_sock_campaign import PROJECT, known_owned_pairs
from routeA.build_t00035_campaign import _squarefree_part, forecast, write_payload
from routeA.constructions.integral_basis_character_lift import (
    IntegralBasisCharacterFamily,
    build_integral_basis_lift,
    certify_integral_basis_lift,
    enumerate_integral_basis_generators,
    family_from_integral_basis_seed,
)
from routeA.constructions.nested_character_lift import classify_transitive_subgroup
from routeA.ledger import DEFAULT_DB, Ledger


DATA = PROJECT / "routeA" / "data"
DEFAULT_CATALOG = DATA / "degree12_lmfdb_catalog.jsonl"
DEFAULT_MAP = DATA / "character_kernel_map.jsonl"
DEFAULT_MANIFEST = DATA / "score700_campaign.jsonl"
DEFAULT_PAYLOAD = PROJECT / "routeA" / "score700_campaign.txt"
CharacterMode = Literal["sign", "trivial", "both"]


@dataclass(frozen=True)
class RankedCatalogFamily:
    family: IntegralBasisCharacterFamily
    live_ceiling: float
    catalog_label: str


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    return [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line
    ]


def character_targets(
    map_rows: Iterable[dict[str, Any]],
) -> dict[int, dict[str, int]]:
    """Return the exact sign/trivial target for each degree-12 group."""

    targets: dict[int, dict[str, int]] = {}
    for row in map_rows:
        base_t = int(row["base_t"])
        entry = targets.setdefault(base_t, {})
        if bool(row.get("includes_permutation_sign")):
            entry["sign"] = int(row["target_t"])
        if bool(row.get("includes_trivial_character")):
            entry["trivial"] = int(row["target_t"])
    missing = [
        base_t
        for base_t in range(1, 302)
        if set(targets.get(base_t, {})) != {"sign", "trivial"}
    ]
    if missing:
        raise ValueError(f"character map is incomplete for bases {missing}")
    return targets


def _derivative(coefficients: Iterable[int]) -> tuple[int, ...]:
    values = tuple(int(value) for value in coefficients)
    return tuple(power * values[power] for power in range(1, len(values)))


def _candidate_roots(
    target_t: int,
    targets: dict[tuple[int, int], Any],
    owned_pairs: set[tuple[int, int]],
) -> tuple[tuple[int, ...], float]:
    roots: list[int] = []
    ceiling = 0.0
    for root in range(0, 25, 4):
        pair = int(target_t), root
        row = targets.get(pair)
        if row is None or pair in owned_pairs or bool(row["baseline"]):
            continue
        roots.append(root)
        ceiling += 2.0 ** (-int(row["team_count"]))
    return tuple(roots), ceiling


def ranked_catalog_families(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    map_path: str | Path = DEFAULT_MAP,
    db_path: str | Path = DEFAULT_DB,
    owned_pairs: set[tuple[int, int]] | None = None,
    character: CharacterMode = "sign",
    allow_repeated_fields: bool = False,
) -> list[RankedCatalogFamily]:
    """Materialize score-ranked catalog families for the requested characters."""

    if character not in {"sign", "trivial", "both"}:
        raise ValueError(f"unsupported character mode {character!r}")
    catalog = load_jsonl(catalog_path)
    if not allow_repeated_fields and len(catalog) != 301:
        raise ValueError(f"degree-12 catalog has {len(catalog)} rows, expected 301")
    if allow_repeated_fields and not catalog:
        raise ValueError("repeated-field catalog is empty")
    exact_targets = character_targets(load_jsonl(map_path))
    owned = set(owned_pairs or ())
    with Ledger(db_path) as ledger:
        live_targets = ledger.latest_targets()

    modes = ("sign", "trivial") if character == "both" else (character,)
    ranked: list[RankedCatalogFamily] = []
    seen_targets: set[tuple[Any, ...]] = set()
    for row in catalog:
        base_t = int(row["base_t"])
        coefficients = tuple(int(value) for value in row["coefficients"])
        for mode in modes:
            target_t = exact_targets[base_t][mode]
            # In an even permutation group the sign and trivial characters
            # coincide.  Prefer the derivative seed because it is typically
            # less Kummer-degenerate than the rational seed 1.
            identity = (
                (base_t, target_t, str(row["label"]))
                if allow_repeated_fields
                else (base_t, target_t)
            )
            if identity in seen_targets:
                continue
            seen_targets.add(identity)
            roots, ceiling = _candidate_roots(target_t, live_targets, owned)
            if not roots or ceiling <= 0:
                continue
            if mode == "sign":
                norm_class = _squarefree_part(int(row["disc_abs"]))
                seed = _derivative(coefficients)
                character_name = "permutation_sign"
            else:
                norm_class = 1
                seed = (1,)
                character_name = "trivial"
            label_tag = str(row["label"]).replace(".", "_")
            family = family_from_integral_basis_seed(
                family=(
                    f"score700_catalog_{mode}_12t{base_t}_{target_t}_{label_tag}"
                    if allow_repeated_fields
                    else f"score700_catalog_{mode}_12t{base_t}_{target_t}"
                ),
                base_t=base_t,
                target_t=target_t,
                expected_norm_squareclass=norm_class,
                base_coefficients=coefficients,
                seed_coefficients=seed,
                target_root_counts=roots,
                construction_overgroup=(
                    f"C2^11_{mode}_character_kernel_over_12T{base_t}"
                ),
                character_name=character_name,
                base_root_count=12,
            )
            ranked.append(RankedCatalogFamily(
                family=family,
                live_ceiling=ceiling,
                catalog_label=str(row["label"]),
            ))
    return sorted(
        ranked,
        key=lambda item: (
            -item.live_ceiling,
            item.family.base_t,
            item.family.target_t,
        ),
    )


def _append_candidate(path: Path, candidate: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(candidate.to_json(), sort_keys=True) + "\n")
        handle.flush()


def certify_ranked_catalog(
    ranked: Iterable[RankedCatalogFamily],
    *,
    manifest_path: Path,
    owned_pairs: set[tuple[int, int]],
    options_per_signature: int = 64,
    coefficient_limit: int = 10**120,
    prime_limit: int = 10000,
) -> list[dict[str, Any]]:
    existing = load_jsonl(manifest_path)
    completed = {
        (int(row["target_t"]), int(row["target_r"]))
        for row in existing
        if row.get("exact_compatibility_proven") and row.get("submission_ready")
    }
    materialized = list(ranked)
    for index, ranked_family in enumerate(materialized, 1):
        family = ranked_family.family
        roots = tuple(
            root
            for root in family.target_root_counts
            if (family.target_t, root) not in owned_pairs
            and (family.target_t, root) not in completed
        )
        if not roots:
            continue
        active = replace(family, target_root_counts=roots)
        print(json.dumps({
            "event": "catalog_family",
            "index": index,
            "total_families": len(materialized),
            "family": active.family,
            "base_t": active.base_t,
            "target_t": active.target_t,
            "roots": list(roots),
            "live_ceiling": ranked_family.live_ceiling,
            "catalog_label": ranked_family.catalog_label,
        }, sort_keys=True), flush=True)
        try:
            options = enumerate_integral_basis_generators(
                active,
                options_per_signature=options_per_signature,
                allow_missing_signatures=True,
            )
        except Exception as exc:
            print(json.dumps({
                "event": "catalog_family_excluded",
                "family": active.family,
                "error": str(exc),
            }, sort_keys=True), flush=True)
            continue

        for root in roots:
            if not options[root]:
                print(json.dumps({
                    "event": "catalog_signature_unavailable",
                    "family": active.family,
                    "target_t": active.target_t,
                    "target_r": root,
                }, sort_keys=True), flush=True)
                continue
            failures: list[str] = []
            for option in options[root]:
                try:
                    spec = build_integral_basis_lift(
                        active,
                        option,
                        coefficient_limit=coefficient_limit,
                    )
                    candidate = certify_integral_basis_lift(
                        spec,
                        prime_limit=prime_limit,
                    )
                except Exception as exc:
                    failures.append(str(exc))
                    continue
                _append_candidate(manifest_path, candidate)
                completed.add((candidate.target_t, candidate.target_r))
                print(json.dumps({
                    "event": "catalog_row_certified",
                    "family": active.family,
                    "target_t": candidate.target_t,
                    "target_r": candidate.target_r,
                    "coefficient_digits": max(
                        len(part.lstrip("-"))
                        for part in candidate.coefficients.split(",")
                    ),
                    "field_disc_abs": candidate.field_disc_abs,
                }, sort_keys=True), flush=True)
                break
            else:
                print(json.dumps({
                    "event": "catalog_row_excluded",
                    "family": active.family,
                    "target_t": active.target_t,
                    "target_r": root,
                    "errors": failures[-3:],
                }, sort_keys=True), flush=True)
    return load_jsonl(manifest_path)


def recover_catalog_subgroups(
    ranked: Iterable[RankedCatalogFamily],
    *,
    manifest_path: Path,
    db_path: str | Path,
    owned_pairs: set[tuple[int, int]],
    options_per_signature: int = 8,
    coefficient_limit: int = 10**120,
    prime_limit: int = 10000,
    all_roots: bool = False,
    minimum_raw_value: float = 0.0,
) -> list[dict[str, Any]]:
    """Recover exact proper Kummer subgroups from catalog rows.

    The ordinary mode revisits only signatures that failed to realize the
    starting character-kernel target.  ``all_roots`` deliberately revisits
    every attainable signature: a unit twist can land in a valuable proper
    subgroup even when the corresponding starting-target pair is already
    owned or certified.
    """

    existing = load_jsonl(manifest_path)
    completed = {
        (int(row["target_t"]), int(row["target_r"]))
        for row in existing
        if row.get("exact_compatibility_proven") and row.get("submission_ready")
    }
    with Ledger(db_path) as ledger:
        live_targets = ledger.latest_targets()
    materialized = list(ranked)
    for index, ranked_family in enumerate(materialized, 1):
        family = ranked_family.family
        if all_roots:
            unresolved = tuple(range(0, 25, 4))
        else:
            unresolved = tuple(
                root
                for root in family.target_root_counts
                if (family.target_t, root) not in owned_pairs
                and (family.target_t, root) not in completed
            )
        if not unresolved:
            continue
        active = replace(family, target_root_counts=unresolved)
        print(json.dumps({
            "event": "catalog_subgroup_family",
            "index": index,
            "total_families": len(materialized),
            "family": active.family,
            "starting_target_t": active.target_t,
            "roots": list(unresolved),
        }, sort_keys=True), flush=True)
        try:
            options = enumerate_integral_basis_generators(
                active,
                options_per_signature=options_per_signature,
                allow_missing_signatures=True,
            )
        except Exception as exc:
            print(json.dumps({
                "event": "catalog_subgroup_family_excluded",
                "family": active.family,
                "error": str(exc),
            }, sort_keys=True), flush=True)
            continue

        for root in unresolved:
            seen_terminals: set[int] = set()
            for option_index, option in enumerate(options[root]):
                try:
                    spec = build_integral_basis_lift(
                        active,
                        option,
                        coefficient_limit=coefficient_limit,
                    )
                    terminal_t, _, _, chain = classify_transitive_subgroup(
                        spec.coefficients,
                        active.target_t,
                        prime_limit=prime_limit,
                    )
                except Exception as exc:
                    print(json.dumps({
                        "event": "catalog_subgroup_option_excluded",
                        "family": active.family,
                        "target_r": root,
                        "option_index": option_index,
                        "error": str(exc),
                    }, sort_keys=True), flush=True)
                    continue
                if terminal_t in seen_terminals:
                    continue
                seen_terminals.add(terminal_t)
                pair = terminal_t, root
                target = live_targets.get(pair)
                if (
                    target is None
                    or pair in owned_pairs
                    or pair in completed
                    or bool(target["baseline"])
                    or 2.0 ** (-int(target["team_count"])) < minimum_raw_value
                ):
                    continue
                rendered_chain = ">".join(f"24T{value}" for value in chain)
                terminal_family = replace(
                    active,
                    family=(
                        f"{active.family}_subgroup_"
                        f"{terminal_t}"
                    ),
                    target_t=terminal_t,
                    target_root_counts=(root,),
                    construction_overgroup=(
                        f"exact_transitive_subgroup_descent:{rendered_chain}"
                    ),
                    character_name=(
                        f"{active.character_name}_proper_kummer_submodule"
                    ),
                )
                terminal_spec = replace(spec, family=terminal_family)
                try:
                    candidate = certify_integral_basis_lift(
                        terminal_spec,
                        prime_limit=prime_limit,
                    )
                except Exception as exc:
                    print(json.dumps({
                        "event": "catalog_subgroup_certificate_rejected",
                        "family": active.family,
                        "target_t": terminal_t,
                        "target_r": root,
                        "chain": chain,
                        "error": str(exc),
                    }, sort_keys=True), flush=True)
                    continue
                _append_candidate(manifest_path, candidate)
                completed.add(pair)
                print(json.dumps({
                    "event": "catalog_subgroup_certified",
                    "family": candidate.recipe_family,
                    "target_t": terminal_t,
                    "target_r": root,
                    "team_count": int(target["team_count"]),
                    "raw_value": 2.0 ** (-int(target["team_count"])),
                    "chain": chain,
                }, sort_keys=True), flush=True)
    return load_jsonl(manifest_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--character", choices=("sign", "trivial", "both"), default="sign")
    parser.add_argument(
        "--allow-repeated-fields",
        action="store_true",
        help="allow multiple number fields with the same degree-12 group",
    )
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--exclude-base", action="append", type=int, default=[])
    parser.add_argument("--limit-families", type=int)
    parser.add_argument("--start-rank", type=int, default=1)
    parser.add_argument("--minimum-ceiling", type=float, default=0.0)
    parser.add_argument("--options-per-signature", type=int, default=64)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    parser.add_argument("--certify", action="store_true")
    parser.add_argument("--recover-subgroups", action="store_true")
    parser.add_argument(
        "--recovery-all-roots",
        action="store_true",
        help=(
            "inspect every real-root signature for valuable proper subgroups; "
            "this also enables subgroup recovery"
        ),
    )
    parser.add_argument("--recovery-options-per-signature", type=int, default=8)
    parser.add_argument("--minimum-recovery-value", type=float, default=0.0)
    args = parser.parse_args()

    owned = known_owned_pairs(args.db)
    ranked = ranked_catalog_families(
        catalog_path=args.catalog,
        map_path=args.map,
        db_path=args.db,
        owned_pairs=owned,
        character=args.character,
        allow_repeated_fields=args.allow_repeated_fields,
    )
    if args.base:
        wanted = set(args.base)
        ranked = [item for item in ranked if item.family.base_t in wanted]
    if args.exclude_base:
        excluded = set(args.exclude_base)
        ranked = [item for item in ranked if item.family.base_t not in excluded]
    ranked = [item for item in ranked if item.live_ceiling >= args.minimum_ceiling]
    if args.limit_families is not None:
        ranked = ranked[: max(0, args.limit_families)]
    ranked = ranked[max(0, args.start_rank - 1):]

    if args.certify:
        manifest = certify_ranked_catalog(
            ranked,
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
            ranked,
            manifest_path=args.manifest,
            db_path=args.db,
            owned_pairs=owned,
            options_per_signature=args.recovery_options_per_signature,
            coefficient_limit=args.coefficient_limit,
            prime_limit=args.prime_limit,
            all_roots=args.recovery_all_roots,
            minimum_raw_value=args.minimum_recovery_value,
        )
    payload_rows = write_payload(manifest, args.payload, owned) if manifest else 0
    rows, ceiling, adjusted = forecast(
        manifest,
        db_path=args.db,
        owned_pairs=owned,
    )
    print(json.dumps({
        "selected_families": len(ranked),
        "selected_live_ceiling": sum(item.live_ceiling for item in ranked),
        "certified_manifest_rows": len(manifest),
        "payload_rows": payload_rows,
        "forecast_rows": len(rows),
        "sharing_ceiling": ceiling,
        "discriminant_adjusted_forecast": adjusted,
        "manifest": str(args.manifest),
        "payload": str(args.payload),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

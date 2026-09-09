#!/usr/bin/env python3
"""Export exact sign/trivial character families in discovery-compatible form.

The character-kernel map already identifies these two canonical Kummer
characters for every degree-12 transitive group.  Exporting them as ordinary
discovery rows lets the credential-free certification planner shard them by
24T target and root signature without shipping the control-plane database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable, Literal

from routeA.build_catalog_character_campaign import (
    DEFAULT_CATALOG,
    DEFAULT_MAP,
    _derivative,
    character_targets,
    load_jsonl,
)
from routeA.build_t00035_campaign import _squarefree_part


CharacterMode = Literal["sign", "trivial", "both"]


def standard_character_discoveries(
    *,
    catalog_path: str | Path | Iterable[str | Path] = DEFAULT_CATALOG,
    map_path: str | Path = DEFAULT_MAP,
    character: CharacterMode = "trivial",
    bases: Iterable[int] = (),
    allow_repeated_fields: bool = False,
    exclude_candidate_paths: Iterable[str | Path] = (),
) -> list[dict[str, Any]]:
    if character not in {"sign", "trivial", "both"}:
        raise ValueError(f"unsupported character mode {character!r}")
    catalog_paths = (
        [catalog_path]
        if isinstance(catalog_path, (str, Path))
        else list(catalog_path)
    )
    if not catalog_paths:
        raise ValueError("at least one degree-12 catalog is required")
    catalog = [
        row
        for path in catalog_paths
        for row in load_jsonl(path)
    ]
    if not catalog:
        raise ValueError("degree-12 catalog is empty")
    if not allow_repeated_fields and len(catalog) != 301:
        raise ValueError(f"degree-12 catalog has {len(catalog)} rows, expected 301")
    targets = character_targets(load_jsonl(map_path))
    wanted = {int(value) for value in bases}
    excluded_fields = _excluded_field_keys(exclude_candidate_paths)
    modes = ("sign", "trivial") if character == "both" else (character,)
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for field in catalog:
        base_t = int(field["base_t"])
        if wanted and base_t not in wanted:
            continue
        coefficients = tuple(int(value) for value in field["coefficients"])
        if (base_t, coefficients) in excluded_fields:
            continue
        for mode in modes:
            target_t = int(targets[base_t][mode])
            identity = (
                (base_t, target_t, mode, str(field["label"]), coefficients)
                if allow_repeated_fields
                else (base_t, target_t, mode)
            )
            if identity in seen:
                continue
            seen.add(identity)
            if mode == "sign":
                norm_class = _squarefree_part(int(field["disc_abs"]))
                seed = list(_derivative(coefficients))
            else:
                norm_class = 1
                seed = [1]
            output.append({
                "base_t": base_t,
                "starting_target_t": target_t,
                "pilot_terminal_t": target_t,
                "label": str(field["label"]),
                "disc_abs": int(field["disc_abs"]),
                "base_coefficients": list(coefficients),
                "seed_coefficients": seed,
                "norm_squareclass": int(norm_class),
                "standard_character": mode,
                "calibration_kind": f"standard_{mode}_character_overgroup_map",
                "server_calibrated": False,
            })
    return sorted(
        output,
        key=lambda row: (
            int(row["starting_target_t"]),
            int(row["base_t"]),
            str(row["standard_character"]),
            int(row["disc_abs"]),
            str(row["label"]),
        ),
    )


def _excluded_field_keys(paths: Iterable[str | Path]) -> set[tuple[int, tuple[int, ...]]]:
    """Return base-field identities already represented by candidate shards."""

    excluded: set[tuple[int, tuple[int, ...]]] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise ValueError(f"excluded candidate shard does not exist: {path}")
        for row in load_jsonl(path):
            parameters = row.get("parameters")
            base_t = row.get("base_t")
            if base_t is None and isinstance(parameters, dict):
                base_t = parameters.get("base_t")
            coefficients = row.get("base_coefficients")
            if base_t is None or not isinstance(coefficients, (list, tuple)):
                continue
            excluded.add((
                int(base_t),
                tuple(int(value) for value in coefficients),
            ))
    return excluded


def export_standard_discoveries(
    output_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    output_path = Path(output_path)
    rows = standard_character_discoveries(**kwargs)
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output_path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(output_path)
    summary = {
        "output": str(output_path),
        "rows": len(rows),
        "bases": len({int(row["base_t"]) for row in rows}),
        "targets": len({int(row["starting_target_t"]) for row in rows}),
        "characters": sorted({str(row["standard_character"]) for row in rows}),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    report = output_path.with_suffix(output_path.suffix + ".report.json")
    report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--catalog",
        action="append",
        type=Path,
        help="degree-12 catalog JSONL (repeatable; defaults to the canonical catalog)",
    )
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--character", choices=("sign", "trivial", "both"), default="trivial")
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument(
        "--allow-repeated-fields",
        action="store_true",
        help="accept alternate catalogs containing multiple fields per base group",
    )
    parser.add_argument(
        "--exclude-candidates",
        action="append",
        type=Path,
        default=[],
        help="candidate JSONL whose represented base fields should be skipped",
    )
    args = parser.parse_args()
    summary = export_standard_discoveries(
        args.output,
        catalog_path=args.catalog or DEFAULT_CATALOG,
        map_path=args.map,
        character=args.character,
        bases=args.base,
        allow_repeated_fields=args.allow_repeated_fields,
        exclude_candidate_paths=args.exclude_candidates,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate exact degree-12 fields from pair-sum resolvents.

For a degree-12 polynomial with roots ``r_i``, the pair-sum resolvent has
roots ``r_i + r_j`` for ``i < j`` and degree 66.  Its irreducible factors are
the Galois-group orbits on unordered pairs.  GAP determines the exact 12T
action for every orbit of length twelve.  When all such actions have the same
12T label and the factor multiplicities match the orbit census, every
degree-12 factor is therefore an exact new presentation of that group.

These related fields often have unit arithmetic unlike the primary LMFDB
presentation and are useful for harvesting different Kummer subgroups.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence

from sympy import Poly, factor_list, symbols

from routeA.build_catalog_character_campaign import DEFAULT_CATALOG, load_jsonl
from routeA.pair_sum_resolvent import pair_sum_resolvent


DATA = Path(__file__).resolve().parent / "data"
DEFAULT_OUTPUT = DATA / "degree12_pair_resolvents.jsonl"
DEFAULT_CHECKED = DATA / "degree12_pair_resolvents_checked.json"
DEFAULT_GAP = Path("/tmp/igp24-gap/gap-4.16.0/gap")
_X = symbols("x")


def build_gap_script() -> str:
    return r'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
for t in [1..301] do
  H := TransitiveGroup(12,t);;
  orbits := Filtered(
    OrbitsDomain(H,Combinations([1..12],2),OnSets),
    orbit -> Length(orbit)=12
  );;
  if Length(orbits)>0 then
    Print("PAIR12|",t,"|");
    for orbit in orbits do
      Print(TransitiveIdentification(Action(H,orbit,OnSets)),",");
    od;
    Print("\n");
  fi;
od;
QUIT;
'''


def parse_pair_orbits(lines: Iterable[str]) -> dict[int, tuple[int, ...]]:
    output: dict[int, tuple[int, ...]] = {}
    for raw in lines:
        line = raw.strip()
        if not line.startswith("PAIR12|"):
            continue
        _, raw_base, raw_targets = line.split("|", 2)
        targets = tuple(
            int(value) for value in raw_targets.split(",") if value
        )
        if targets:
            output[int(raw_base)] = targets
    return output


def pair_orbit_map(
    *,
    gap: str | Path = DEFAULT_GAP,
    timeout: float = 120,
) -> dict[int, tuple[int, ...]]:
    result = subprocess.run(
        [str(gap), "-q", "-A"],
        input=build_gap_script(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return parse_pair_orbits(result.stdout.splitlines())


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--checked", type=Path, default=DEFAULT_CHECKED)
    parser.add_argument("--gap", default=str(DEFAULT_GAP))
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--limit-bases", type=int)
    parser.add_argument(
        "--check-labels",
        action="store_true",
        help="checkpoint repeated-field inputs by label instead of base T-number",
    )
    parser.add_argument(
        "--dedupe-catalog",
        action="append",
        type=Path,
        default=[],
        help="additional catalogs whose exact defining polynomials are skipped",
    )
    args = parser.parse_args()

    orbit_map = pair_orbit_map(gap=args.gap)
    eligible = {
        base_t: targets
        for base_t, targets in orbit_map.items()
        if len(set(targets)) == 1
    }
    fields = [
        row for row in load_jsonl(args.catalog)
        if int(row["base_t"]) in eligible
    ]
    if args.base:
        wanted = set(args.base)
        fields = [row for row in fields if int(row["base_t"]) in wanted]
    raw_checked = (
        json.loads(args.checked.read_text(encoding="utf-8"))
        if args.checked.exists()
        else []
    )
    checked = (
        {str(value) for value in raw_checked}
        if args.check_labels
        else {int(value) for value in raw_checked}
    )
    fields = [
        row for row in fields
        if (
            str(row["label"])
            if args.check_labels
            else int(row["base_t"])
        ) not in checked
    ]
    if args.limit_bases is not None:
        fields = fields[: max(0, args.limit_bases)]
    output = load_jsonl(args.output)
    seen = {
        tuple(int(value) for value in row["coefficients"])
        for row in output + fields + [
            extra
            for catalog in args.dedupe_catalog
            for extra in load_jsonl(catalog)
        ]
    }

    for index, field in enumerate(fields, 1):
        source_t = int(field["base_t"])
        targets = eligible[source_t]
        error: str | None = None
        added = 0
        factor_summary: list[tuple[int, int]] = []
        try:
            coefficients = pair_sum_resolvent(field["coefficients"])
            polynomial = Poly(
                sum(value * _X**power for power, value in enumerate(coefficients)),
                _X,
                domain="ZZ",
            )
            _, factors = factor_list(polynomial)
            factor_summary = [(int(factor.degree()), int(power)) for factor, power in factors]
            degree_twelve = [
                (factor.monic(), int(power))
                for factor, power in factors
                if factor.degree() == 12
            ]
            multiplicity = sum(power for _, power in degree_twelve)
            if multiplicity != len(targets):
                raise ValueError(
                    f"degree-12 factor multiplicity {multiplicity} does not match "
                    f"GAP orbit count {len(targets)}"
                )
            target_t = int(targets[0])
            for factor_index, (factor, power) in enumerate(degree_twelve, 1):
                factor_coefficients = tuple(
                    int(value) for value in reversed(factor.all_coeffs())
                )
                if factor_coefficients in seen:
                    continue
                disc_abs = abs(int(factor.discriminant()))
                if not disc_abs:
                    raise ValueError("pair-resolvent factor has zero discriminant")
                output.append({
                    "base_t": target_t,
                    "label": (
                        f"pair-sum.12T{source_t}.12T{target_t}."
                        f"{field['label']}.{factor_index}"
                    ),
                    "disc_abs": disc_abs,
                    "coefficients": list(factor_coefficients),
                    "source": "exact pair-sum resolvent orbit",
                    "source_base_t": source_t,
                    "source_label": str(field["label"]),
                    "resolvent_factor_multiplicity": power,
                    "gap_orbit_count": len(targets),
                })
                seen.add(factor_coefficients)
                added += 1
            checked.add(str(field["label"]) if args.check_labels else source_t)
            _write_jsonl(args.output, output)
            _write_json(args.checked, sorted(checked))
        except Exception as exc:
            error = str(exc)
        print(json.dumps({
            "event": "pair_resolvent_catalog_base",
            "index": index,
            "total": len(fields),
            "source_base_t": source_t,
            "target_base_t": int(targets[0]),
            "gap_orbits": len(targets),
            "factor_summary": factor_summary,
            "added": added,
            "catalog_rows": len(output),
            "error": error,
        }, sort_keys=True), flush=True)

    print(json.dumps({
        "eligible_bases": len(eligible),
        "checked": len(checked),
        "catalog_rows": len(output),
        "output": str(args.output),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

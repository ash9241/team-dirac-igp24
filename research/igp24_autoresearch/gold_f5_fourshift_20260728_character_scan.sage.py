#!/usr/bin/env sage -python
"""Exact small-grid norm-character scan for four-shift F5 derivatives."""

from __future__ import annotations

import importlib.util
import json
import time
from itertools import combinations
from pathlib import Path

import gmpy2
from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = DATA / "gold_f5_fourshift_20260728_character_scan.json"
PARAMETERS = (-3, -2, -1, 1, 2, 3)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE = load_module(
    "gold_f5_fourshift_single_character_scan",
    ROOT / "gold_f5_f6_20260728_all_character_derivative_scan.sage.py",
)


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")

    started = time.monotonic()
    sources = SINGLE.load_sources(SINGLE.load_current_tc0())
    ring = PolynomialRing(ZZ, "y")
    hits = []
    exact_checks = 0
    for position, source in enumerate(sources, 1):
        coefficients = tuple(
            int(value) for value in source["coefficientLine"].split(",")
        )
        q = ring([ZZ(value) for value in coefficients])
        discriminant = int(q.discriminant())
        q0 = int(coefficients[0])
        source_core = int(ZZ(q0).squarefree_part())
        characters = (
            ("trivial", 1, discriminant),
            ("source_norm", q0, source_core * discriminant),
            ("discriminant", discriminant, 1),
            (
                "source_norm_times_discriminant",
                q0 * discriminant,
                source_core,
            ),
        )
        values = {
            shift: SINGLE.exact_value(coefficients, shift)
            for shift in PARAMETERS
        }
        seen = set()
        for shifts in combinations(PARAMETERS, 4):
            product_values = gmpy2.mpz(1)
            for shift in shifts:
                product_values *= values[shift]
            if product_values == 0:
                continue
            for kind, multiplier, derivative_core in characters:
                exact_checks += 1
                product = product_values * gmpy2.mpz(multiplier)
                if product <= 0 or not gmpy2.is_square(product):
                    continue
                key = (*shifts, int(derivative_core))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "shifts": list(shifts),
                        "characterKind": kind,
                        "characterMultiplier": str(multiplier),
                        "derivativeNormCoreRepresentative": str(
                            derivative_core
                        ),
                        "qAtShifts": {
                            str(shift): str(values[shift])
                            for shift in shifts
                        },
                        "source": source,
                        "squareRootOfProduct": str(gmpy2.isqrt(product)),
                    }
                )
        if position % 25 == 0 or position == len(sources):
            print(
                json.dumps(
                    {
                        "event": "progress",
                        "hits": len(hits),
                        "position": position,
                        "sources": len(sources),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    payload = {
        "schemaVersion": "gold-f5-fourshift-20260728-character-scan-v1",
        "characterSpan": ["1", "q(0)", "disc(q)", "q(0)*disc(q)"],
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactChecks": exact_checks,
        "hits": hits,
        "networkCalls": 0,
        "parameters": list(PARAMETERS),
        "sourceCount": len(sources),
        "submissionCalls": 0,
    }
    OUTPUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "event": "complete",
                "hits": len(hits),
                "output": str(OUTPUT),
                "sourceCount": len(sources),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

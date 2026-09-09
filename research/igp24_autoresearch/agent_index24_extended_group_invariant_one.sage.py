#!/usr/bin/env sage -python
"""Exact characteristic-subgroup invariant for one degree-24 group."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from sage.all import libgap


def abelian_invariants(group):
    return sorted(int(value) for value in libgap.AbelianInvariants(group))


def series_orders(series):
    return [int(libgap.Size(group)) for group in list(series)]


def factor_orders(series):
    orders = series_orders(series)
    return sorted(orders[index] // orders[index + 1] for index in range(len(orders) - 1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--t", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    group = libgap.TransitiveGroup(24, args.t)
    fitting = libgap.FittingSubgroup(group)
    socle = libgap.Socle(group)
    frattini = libgap.FrattiniSubgroup(group)
    payload = {
        "compositionFactorOrders": factor_orders(libgap.CompositionSeries(group)),
        "derivedSeriesOrders": series_orders(libgap.DerivedSeriesOfGroup(group)),
        "exponent": int(libgap.Exponent(group)),
        "fittingAbelianInvariants": abelian_invariants(fitting),
        "fittingCenterOrder": int(libgap.Size(libgap.Center(fitting))),
        "fittingDerivedOrder": int(libgap.Size(libgap.DerivedSubgroup(fitting))),
        "fittingFrattiniOrder": int(libgap.Size(libgap.FrattiniSubgroup(fitting))),
        "fittingOrder": int(libgap.Size(fitting)),
        "frattiniOrder": int(libgap.Size(frattini)),
        "order": int(libgap.Size(group)),
        "socleAbelianInvariants": abelian_invariants(socle),
        "socleOrder": int(libgap.Size(socle)),
        "solvableRadicalOrder": int(libgap.Size(libgap.SolvableRadical(group))),
    }
    row = {
        **payload,
        "extendedFingerprintSha256": hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "status": "certified",
        "t": args.t,
    }
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(json.dumps({"status": "certified", "t": args.t}), flush=True)
    os._exit(0)


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        os._exit(2)
    os._exit(code)

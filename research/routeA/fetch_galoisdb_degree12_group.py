#!/usr/bin/env python3
"""Fetch defining polynomials for one degree-12 group from GaloisDB."""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


BASE = "https://galoisdb.math.uni-paderborn.de/groups/view/polynomials"
POLYNOMIAL = re.compile(r'name="polynomial" type="text" value="([^"]+)"')


def coefficients(expression: str, degree: int) -> list[int]:
    compact = html.unescape(expression).replace(" ", "")
    terms = compact.replace("-", "+-").split("+")
    result = [0] * (degree + 1)
    for term in terms:
        if not term:
            continue
        if "x" not in term:
            result[0] += int(term)
            continue
        raw_coefficient, _, raw_power = term.partition("*x")
        if not _:
            raw_coefficient, raw_power = term.split("x", 1)
        if raw_coefficient in ("", "+"):
            value = 1
        elif raw_coefficient == "-":
            value = -1
        else:
            value = int(raw_coefficient.rstrip("*"))
        exponent = 1
        if raw_power.startswith("^"):
            exponent = int(raw_power[1:])
        result[exponent] += value
    if result[-1] != 1:
        raise ValueError(f"not a monic degree-{degree} polynomial: {expression}")
    return result


def fetch_page(degree: int, group: int, signature: int, timeout: float) -> list[list[int]]:
    query = urllib.parse.urlencode({
        "deg": int(degree),
        "num": int(group),
        "sig": int(signature),
        "sort": "disc",
    })
    request = urllib.request.Request(
        BASE + "?" + query,
        headers={"User-Agent": "Team-Dirac-IGP24/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        page = response.read().decode("utf-8")
    return [coefficients(match, degree) for match in POLYNOMIAL.findall(page)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--group", type=int, default=61)
    parser.add_argument("--signature", action="append", type=int, default=[])
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    signatures = args.signature or [2, 4, 6, 12]
    unique: dict[tuple[int, ...], dict[str, object]] = {}
    counts: dict[int, int] = {}
    for signature in signatures:
        rows = fetch_page(12, args.group, signature, args.timeout)
        counts[int(signature)] = len(rows)
        for index, values in enumerate(rows, 1):
            key = tuple(values)
            unique.setdefault(key, {
                "base_t": int(args.group),
                "label": f"galoisdb:12T{args.group}:r{signature}:{index}",
                "coefficients": values,
                "source_signature": int(signature),
                "subfield_degrees": [3, 6],
                "source": "Kluners-Malle GaloisDB",
            })
    output = sorted(
        unique.values(),
        key=lambda row: (int(row["source_signature"]), str(row["label"])),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in output),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "group": args.group,
        "page_counts": counts,
        "unique": len(output),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

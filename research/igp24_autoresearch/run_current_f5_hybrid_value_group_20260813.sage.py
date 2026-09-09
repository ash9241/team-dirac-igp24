#!/usr/bin/env sage -python
"""Resolve one exact unique-action F5 group from a sealed hybrid plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--group-ordinal", type=int, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    plan = json.loads((DATA / Path(args.plan).name).read_text(encoding="utf-8"))
    if plan.get("schemaVersion") != "current-f5-hybrid-value-plan-v1":
        raise ValueError("unexpected F5 hybrid plan schema")
    group = next(
        row for row in plan["groups"]
        if int(row["groupOrdinal"]) == args.group_ordinal
    )
    output = DATA / f"current_f5_hybrid_value_{args.tag}_group{args.group_ordinal}_20260813.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    quotient_line = str(group["quotientLine"])
    if hashlib.sha256(quotient_line.encode("ascii")).hexdigest() != group["quotientSha256"]:
        raise ValueError("quotient hash mismatch")
    ring_y = PolynomialRing(ZZ, "y")
    quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("quotient failed exact checks")
    factors = [
        (factor, int(exponent))
        for factor, exponent in quotient.symmetric_power(2, monic=True).factor()
    ]
    degree_twelve = [
        factor for factor, exponent in factors
        if factor.degree() == 12 and exponent == 1
    ]
    if len(degree_twelve) != 1:
        raise ValueError(f"unique degree-12 factor gate failed: {len(degree_twelve)}")
    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    candidate = ring_x(degree_twelve[0])(x**2)
    candidate = ring_x(pari(candidate).polredbest())
    if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
        raise ValueError("F5 candidate failed exact polynomial checks")
    signature = int(candidate.number_of_real_roots())
    if signature not in {int(value) for value in group["possibleR"]}:
        raise ValueError("real signature contradicts exact action atlas")
    values = [ZZ(value) for value in candidate.list()]
    values += [ZZ(0)] * (25 - len(values))
    line = ",".join(str(value) for value in values)
    candidate_row = {
        "coefficientLine": line,
        "coefficientSha256": hashlib.sha256(line.encode("ascii")).hexdigest(),
        "fieldDiscriminantAbs": str(abs(int(pari(candidate).nfdisc()))),
        "r": signature,
        "targetLabel": str(group["targetLabel"]),
        "targetT": int(group["targetT"]),
    }
    payload = {
        "actionSha256": group["actionSha256"],
        "candidates": [candidate_row],
        "desiredTeamCounts": group["desiredTeamCounts"],
        "factorDegrees": [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ],
        "groupOrdinal": args.group_ordinal,
        "source": group["source"],
        "sourceCanonicalQuotientSha256": group["canonicalQuotientSha256"],
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"event": "complete", "group": args.group_ordinal, "r": signature, "targetLabel": group["targetLabel"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

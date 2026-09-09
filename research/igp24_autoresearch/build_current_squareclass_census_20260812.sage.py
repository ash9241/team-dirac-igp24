#!/usr/bin/env sage -python
"""Canonicalize a current squareclass source bank and exclude prior fields."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def prior_hashes(quotient_t: int) -> set[str]:
    hashes: set[str] = set()
    for path in DATA.glob(f"*q{quotient_t}*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("quotientT12") not in (None, quotient_t):
            continue
        for key in ("fields", "freshFields", "freshTotallyRealFields"):
            for row in payload.get(key, []):
                digest = row.get("fieldCanonicalSha256")
                if digest:
                    hashes.add(str(digest))
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--quotient-t", type=int, required=True)
    parser.add_argument("--prior-hashes", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")

    payload = json.loads(args.sources.read_text(encoding="utf-8"))
    matches = [
        lane for lane in payload.get("lanes", [])
        if int(lane.get("quotientT12", -1)) == args.quotient_t
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one q{args.quotient_t} lane, found {len(matches)}")
    lane = matches[0]
    sources = lane.get("selectedSourcePresentations", [])
    ring = PolynomialRing(QQ, "y")
    fields: dict[str, dict] = {}
    rejected = 0
    for source in sources:
        quotient = ring([ZZ(value) for value in source["quotientPolynomial"].split(",")])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            rejected += 1
            continue
        canonical = ring(pari(quotient).polredabs())
        line = ",".join(str(ZZ(value)) for value in canonical)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        row = fields.setdefault(
            digest,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": digest,
                "quotientT12ClaimedByStructuralSource": args.quotient_t,
                "sourceRows": [],
            },
        )
        row["sourceRows"].append(
            {
                "coefficientSha256": str(source["sourceCoefficientSha256"]),
                "label": str(source["sourceLabel"]),
                "polynomialIndex": int(source["sourcePolynomialIndex"]),
                "r": int(source["sourceR"]),
                "scoreable": True,
                "submissionId": str(source["sourceSubmissionId"]),
            }
        )

    prior = prior_hashes(args.quotient_t)
    if args.prior_hashes:
        prior.update(
            line.strip()
            for line in args.prior_hashes.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    rows = []
    for digest in sorted(fields):
        row = fields[digest]
        row["previouslyAudited"] = digest in prior
        row["sourceRows"].sort(
            key=lambda value: (
                value["label"], value["r"], value["submissionId"],
                value["polynomialIndex"],
            )
        )
        rows.append(row)
    fresh = [row for row in rows if not row["previouslyAudited"]]
    result = {
        "audit": {"networkCalls": 0, "submissionCalls": 0},
        "fields": rows,
        "freshFields": fresh,
        "quotientT12": args.quotient_t,
        "summary": {
            "canonicalFields": len(rows),
            "freshCanonicalTotallyRealFields": len(fresh),
            "priorHashes": len(prior),
            "rejectedSources": rejected,
            "sourcePresentations": len(sources),
        },
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **result["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

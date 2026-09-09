#!/usr/bin/env sage -python
"""Build a canonical field census from a selected squareclass lane manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari


def canonical_polynomial(polynomial):
    ring = polynomial.parent()
    return ring(pari(polynomial).polredabs())


def coefficient_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--quotient-t", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    matching = [
        lane
        for lane in manifest.get("lanes", [])
        if int(lane.get("quotientT12", -1)) == args.quotient_t
    ]
    if len(matching) != 1:
        raise ValueError("manifest does not contain exactly one requested lane")
    lane = matching[0]
    ring = PolynomialRing(QQ, "y")
    fields: dict[str, dict] = {}
    counts = defaultdict(int)
    for source in lane.get("selectedSourcePresentations", []):
        counts["selectedSourcePresentations"] += 1
        polynomial = ring(
            [ZZ(value) for value in str(source["quotientPolynomial"]).split(",")]
        )
        if polynomial.degree() != 12 or polynomial[12] != 1:
            counts["invalidDegreeOrLeadingCoefficient"] += 1
            continue
        if not polynomial.is_irreducible():
            counts["reducible"] += 1
            continue
        if int(polynomial.number_of_real_roots()) != 12:
            counts["notTotallyReal"] += 1
            continue
        reduced = canonical_polynomial(polynomial)
        line = coefficient_line(reduced)
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        row = fields.setdefault(
            digest,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": digest,
                "previouslyAudited": False,
                "quotientT12ClaimedByStructuralSource": args.quotient_t,
                "sourceRows": [],
            },
        )
        source_row = {
            "coefficientSha256": str(source["sourceCoefficientSha256"]),
            "label": str(source["sourceLabel"]),
            "polynomialIndex": int(source["sourcePolynomialIndex"]),
            "r": int(source["sourceR"]),
            "scoreable": True,
            "submissionId": str(source["sourceSubmissionId"]),
        }
        if source_row not in row["sourceRows"]:
            row["sourceRows"].append(source_row)
        counts["totallyRealSourcePresentations"] += 1

    field_rows = sorted(fields.values(), key=lambda row: row["fieldCanonicalSha256"])
    gold_targets = list(lane.get("currentGoldTargets", []))
    payload = {
        "audit": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "warning": (
                "selected structural source membership is not an independent "
                "exact quotient Galois-group certificate"
            ),
        },
        "fields": field_rows,
        "freshFields": field_rows,
        "fullRankTargetLabels": sorted(
            {str(row["label"]) for row in gold_targets}
        ),
        "livePairsBeforeQueuedSubmissionExclusion": gold_targets,
        "quotientT12": args.quotient_t,
        "summary": {
            **dict(sorted(counts.items())),
            "canonicalTotallyRealFields": len(field_rows),
            "freshCanonicalTotallyRealFields": len(field_rows),
            "livePairs": len(gold_targets),
        },
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "fields": len(field_rows),
        "livePairs": len(gold_targets),
        "output": str(args.output.resolve()),
        "quotientT12": args.quotient_t,
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "sourceCounts": dict(sorted(counts.items())),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

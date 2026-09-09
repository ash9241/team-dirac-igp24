#!/usr/bin/env sage -python
"""Stage every exact degree-12 subset-product factor when action assignment is ambiguous."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PLAN = DATA / "current_lower_kummer_subset_wave_20260812.json"


def line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    values += [ZZ(0)] * (25 - len(values))
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("not monic degree 24")
    return ",".join(str(value) for value in values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-ordinal", type=int, required=True)
    parser.add_argument("--plan", default=PLAN.name)
    parser.add_argument("--tag", default="base")
    args = parser.parse_args()
    plan_path = DATA / Path(args.plan).name
    result_path = DATA / f"current_lower_kummer_subset_all_factors_{args.tag}_group{args.group_ordinal}_20260812.json"
    manifest_path = DATA / f"current_lower_kummer_subset_all_factors_{args.tag}_group{args.group_ordinal}_20260812.txt"
    if result_path.exists() or manifest_path.exists():
        raise FileExistsError("refusing to overwrite all-factor outputs")
    plan = json.loads(plan_path.read_text())
    group = next(item for item in plan["groups"] if int(item["groupOrdinal"]) == args.group_ordinal)
    source = group["source"]
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
        FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (source["submissionId"], int(source["polynomialIndex"])),
    ).fetchone()
    if row is None or str(row["coefficient_hash"]) != source["coefficientSha256"]:
        raise ValueError("source provenance mismatch")
    coefficients = [ZZ(value) for value in str(row["coefficients"]).split(",")]
    ring_q = PolynomialRing(ZZ, "q")
    quotient = ring_q(coefficients[::2])
    factorization = list(quotient.symmetric_power(int(group["subsetSize"]), monic=True).factor())
    factors = sorted(
        [factor for factor, exponent in factorization if factor.degree() == 12 and int(exponent) == 1],
        key=lambda value: ",".join(str(ZZ(item)) for item in value.list()),
    )
    if len(factors) != len(group["actions"]):
        raise ValueError("degree-12 factors do not match sealed action count")
    known = {str(item[0]) for item in connection.execute("SELECT coefficient_hash FROM polynomials")}
    candidates = []
    for index, factor in enumerate(factors):
        ring_x = PolynomialRing(ZZ, f"x{index}")
        x = ring_x.gen()
        candidate = ring_x(factor)(x**2)
        candidate = ring_x(pari(candidate).polredbest())
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("candidate failed exact polynomial checks")
        coefficient_line = line(candidate)
        digest = hashlib.sha256(coefficient_line.encode()).hexdigest()
        candidates.append(
            {
                "coefficientLine": coefficient_line,
                "coefficientSha256": digest,
                "fieldDiscriminantAbs": str(abs(int(pari(candidate).nfdisc()))),
                "freshHash": digest not in known,
                "r": int(candidate.number_of_real_roots()),
            }
        )
        known.add(digest)
    connection.close()
    fresh = [item for item in candidates if item["freshHash"]]
    manifest_path.write_text("".join(item["coefficientLine"] + "\n" for item in fresh))
    payload = {
        "actionCount": len(group["actions"]),
        "candidates": candidates,
        "freshCandidates": len(fresh),
        "groupOrdinal": args.group_ordinal,
        "routes": group["routes"],
        "source": source,
        "subsetSize": int(group["subsetSize"]),
    }
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"event": "complete", "fresh": len(fresh), "group": args.group_ordinal, "signatures": [item["r"] for item in candidates]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

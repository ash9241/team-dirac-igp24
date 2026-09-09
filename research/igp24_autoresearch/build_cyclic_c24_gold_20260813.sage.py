#!/usr/bin/env sage -python
"""Build the two exact cyclic C24 fields at signatures 0 and 24."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"


def main() -> int:
    ring = PolynomialRing(QQ, "x")
    rows = []
    for conductor, expected_r in ((73, 0), (97, 24)):
        polynomial = ring(pari(f"polsubcyclo({conductor},24)"))
        polynomial = ring(pari(polynomial).polredbest())
        if polynomial.degree() != 24 or not polynomial.is_monic() or not polynomial.is_irreducible():
            raise ValueError("cyclic subfield polynomial failed exact checks")
        signature = int(polynomial.number_of_real_roots())
        if signature != expected_r:
            raise ValueError(f"unexpected signature for conductor {conductor}: {signature}")
        coefficients = [int(value) for value in polynomial.list()]
        coefficients += [0] * (25 - len(coefficients))
        line = ",".join(map(str, coefficients))
        rows.append(
            {
                "coefficientLine": line,
                "coefficientSha256": hashlib.sha256(line.encode("ascii")).hexdigest(),
                "conductor": conductor,
                "fieldDiscriminantAbs": str(abs(int(pari(polynomial).nfdisc()))),
                "label": "24T1",
                "proof": (
                    f"The unique index-24 subfield of Q(zeta_{conductor}) is Galois "
                    "with cyclic quotient C24, hence its regular action is 24T1."
                ),
                "r": signature,
            }
        )
    manifest = "".join(row["coefficientLine"] + "\n" for row in rows)
    output = OUTBOX / "cyclic_c24_two_gold_20260813.txt"
    summary = DATA / "cyclic_c24_two_gold_20260813.json"
    if output.exists() or summary.exists():
        raise FileExistsError("refusing to overwrite cyclic C24 gold artifacts")
    output.write_text(manifest, encoding="utf-8")
    payload = {
        "authority": "exact cyclotomic Galois quotient and exact Sage/PARI polynomial checks",
        "manifest": str(output.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode("ascii")).hexdigest(),
        "rows": rows,
    }
    summary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

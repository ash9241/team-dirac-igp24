#!/usr/bin/env sage -python
"""Stage exact unowned shared pairs from a completed unique-action F5 wave."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()

    results_path = args.results.expanduser().resolve()
    manifest = args.manifest.expanduser().resolve()
    audit = args.audit.expanduser().resolve()
    if manifest.parent != (ROOT / "outbox").resolve():
        raise ValueError("manifest must be a direct child of outbox/")
    if audit.parent != (ROOT / "data").resolve():
        raise ValueError("audit must be a direct child of data/")
    if manifest.exists() or audit.exists():
        raise FileExistsError("refusing to overwrite staged artifacts")

    result_rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ring = PolynomialRing(ZZ, "x")
    selected = []
    seen_pairs = set()
    with sqlite3.connect(DB) as connection:
        connection.row_factory = sqlite3.Row
        for row in result_rows:
            target = row["target"]
            pair = (str(target["label"]), int(target["r"]))
            state = connection.execute(
                """
                SELECT t.*,
                       EXISTS(
                         SELECT 1 FROM baseline_pairs b
                         WHERE b.label=t.label AND b.r=t.r
                       ) AS baseline,
                       EXISTS(
                         SELECT 1 FROM verifications v
                         WHERE v.label=t.label AND v.r=t.r
                           AND v.status='accepted' AND v.scoreable=1
                       ) AS owned
                FROM targets t
                WHERE t.label=? AND t.r=?
                """,
                pair,
            ).fetchone()
            candidate = row["candidate"]
            digest = str(candidate["coefficientSha256"])
            if (
                pair in seen_pairs
                or state is None
                or bool(state["baseline"])
                or bool(state["owned"])
                or connection.execute(
                    "SELECT EXISTS("
                    "SELECT 1 FROM polynomials WHERE coefficient_hash=?"
                    ")",
                    (digest,),
                ).fetchone()[0]
            ):
                continue
            line = str(candidate["coefficientLine"])
            if digest_text(line) != digest:
                raise ValueError("candidate coefficient hash mismatch")
            coefficients = [ZZ(value) for value in line.split(",")]
            polynomial = ring(coefficients)
            if (
                len(coefficients) != 25
                or coefficients[-1] != 1
                or coefficients[0] == 0
                or math.gcd(*(int(value) for value in coefficients)) != 1
                or polynomial.degree() != 24
                or not polynomial.is_monic()
                or not polynomial.is_irreducible()
                or int(polynomial.number_of_real_roots()) != pair[1]
            ):
                raise ValueError("candidate failed exact local polynomial gates")
            selected.append(
                {
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "exactTarget": {"label": pair[0], "r": pair[1]},
                    "fieldDiscriminantAbs": str(abs(int(pari(polynomial).nfdisc()))),
                    "factorDegrees": row["factorDegrees"],
                    "liveStateAtStage": {
                        "discovered": bool(state["discovered"]),
                        "generatedAt": state["generated_at"],
                        "minimumDiscAbs": state["minimum_disc_abs"],
                        "teamCount": int(state["team_count"]),
                    },
                    "source": row["source"],
                    "sourceCanonicalQuotientSha256": row[
                        "sourceCanonicalQuotientSha256"
                    ],
                }
            )
            seen_pairs.add(pair)

    if not selected:
        raise ValueError("no exact unowned pairs survived staging")
    manifest_text = "".join(row["coefficientLine"] + "\n" for row in selected)
    audit_value = {
        "schemaVersion": "f5-unaligned-shared-stage-v1",
        "results": str(results_path.relative_to(ROOT)),
        "resultsSha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        "manifest": str(manifest.relative_to(ROOT)),
        "manifestSha256": digest_text(manifest_text),
        "selectedCount": len(selected),
        "selected": selected,
    }
    manifest.write_text(manifest_text, encoding="utf-8")
    audit.write_text(
        json.dumps(audit_value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "audit": str(audit),
                "manifest": str(manifest),
                "manifestSha256": audit_value["manifestSha256"],
                "selectedCount": len(selected),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

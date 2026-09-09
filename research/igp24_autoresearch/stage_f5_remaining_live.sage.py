#!/usr/bin/env sage -python
"""Stage one best exact field for each currently unowned F5 result pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"


def sha256_text(value: str) -> str:
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

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_pair = defaultdict(list)
    for row in rows:
        for candidate in row["candidateResults"]:
            target = candidate["exactTarget"]
            pair = (str(target["label"]), int(target["r"]))
            by_pair[pair].append((row, candidate))

    ring = PolynomialRing(ZZ, "x")
    selected = []
    with sqlite3.connect(DB) as connection:
        connection.row_factory = sqlite3.Row
        for pair in sorted(by_pair):
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
            if (
                state is None
                or bool(state["baseline"])
                or bool(state["owned"])
            ):
                continue

            choices = []
            for source_row, candidate in by_pair[pair]:
                line = str(candidate["coefficientLine"])
                digest = sha256_text(line)
                if digest != str(candidate["coefficientSha256"]):
                    raise ValueError("candidate coefficient hash mismatch")
                if connection.execute(
                    "SELECT EXISTS("
                    "SELECT 1 FROM polynomials WHERE coefficient_hash=?"
                    ")",
                    (digest,),
                ).fetchone()[0]:
                    continue
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
                field_disc = abs(int(pari(polynomial).nfdisc()))
                choices.append(
                    (
                        field_disc,
                        len(line.encode("utf-8")),
                        digest,
                        source_row,
                        candidate,
                    )
                )
            if not choices:
                continue
            field_disc, _, digest, source_row, candidate = min(
                choices, key=lambda value: value[:3]
            )
            selected.append(
                {
                    "coefficientLine": candidate["coefficientLine"],
                    "coefficientSha256": digest,
                    "exactTarget": candidate["exactTarget"],
                    "fieldDiscriminantAbs": str(field_disc),
                    "liveStateAtStage": {
                        "discovered": bool(state["discovered"]),
                        "generatedAt": state["generated_at"],
                        "minimumDiscAbs": state["minimum_disc_abs"],
                        "teamCount": int(state["team_count"]),
                    },
                    "source": source_row["source"],
                    "sourcePosition": int(source_row["sourcePosition"]),
                    "factorIndex": int(candidate["factorIndex"]),
                    "assignmentCertificate": source_row["assignmentCertificate"],
                    "exactActionSha256s": source_row["exactActionSha256s"],
                    "pairResolventSha256": source_row["pairResolventSha256"],
                }
            )

    if not selected:
        raise ValueError("no currently unowned exact pairs survived staging")
    lines = [str(row["coefficientLine"]) for row in selected]
    if len(lines) != len(set(lines)):
        raise ValueError("staged manifest contains duplicate coefficient lines")

    manifest_text = "".join(line + "\n" for line in lines)
    audit_value = {
        "schemaVersion": "f5-remaining-live-stage-v1",
        "results": str(results_path.relative_to(ROOT)),
        "resultsSha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        "manifest": str(manifest.relative_to(ROOT)),
        "manifestSha256": sha256_text(manifest_text),
        "selectedCount": len(selected),
        "distinctPairs": len(
            {
                (row["exactTarget"]["label"], int(row["exactTarget"]["r"]))
                for row in selected
            }
        ),
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
                "distinctPairs": audit_value["distinctPairs"],
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

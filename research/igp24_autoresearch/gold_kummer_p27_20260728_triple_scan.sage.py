#!/usr/bin/env sage -python
"""Exact offline k=3 Kummer scan over untested accepted even sources.

The two source groups below each have one exact size-12 orbit of triples in
the preserved natural 12-block action.  Thus the unique degree-12 factor of
the third symmetric power is canonically assigned to the stored induced
24-point action.  This script is read-only with respect to the ledger and
performs no network or submission calls.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
PRIOR_RESULTS = DATA / "agent_f9_higher_kummer_triple_pilot_results.jsonl"
RESULTS = DATA / "gold_kummer_p27_20260728_triple_results.jsonl"
SUMMARY = DATA / "gold_kummer_p27_20260728_triple_summary.json"
MANIFEST = OUTBOX / "gold_kummer_p27_20260728_triple_gold.txt"

SOURCE_LABELS = {"24T19325", "24T20436"}
ELIGIBLE_SOURCE_SIGNATURES = {
    "24T19325": {12},
    "24T20436": {8, 12, 20},
}
STAGED_PAIRS = {
    ("24T10482", 8),
    ("24T14293", 16),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T15337", 20),
    ("24T15043", 4),
    ("24T11787", 12),
    ("24T24877", 14),
}


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def coefficient_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ArithmeticError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def write_atomic(path: Path, text: str) -> str:
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return sha256(text)


def target_state(connection, label: str, r: int) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b
                 WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        raise ArithmeticError(f"missing target row {label}/r{r}")
    return {
        "teamCount": int(row[0]),
        "discovered": bool(row[1]),
        "minimumDiscAbs": None if row[2] is None else str(row[2]),
        "generatedAt": row[3],
        "baseline": bool(row[4]),
        "owned": bool(row[5]),
    }


def main() -> int:
    for path in (RESULTS, SUMMARY, MANIFEST):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    action_rows = [
        row
        for row in jsonl(ACTIONS)
        if int(row["subsetSize"]) == 3 and row["sourceLabel"] in SOURCE_LABELS
    ]
    actions = {}
    for label in SOURCE_LABELS:
        rows = [row for row in action_rows if row["sourceLabel"] == label]
        if len(rows) != 1:
            raise ArithmeticError(f"{label} does not have one exact triple action")
        actions[label] = rows[0]

    prior_source_hashes = {
        row["source"]["coefficientSha256"] for row in jsonl(PRIOR_RESULTS)
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source_rows = list(
        connection.execute(
            """
            SELECT p.submission_id,p.polynomial_index,p.coefficient_hash,
                   p.coefficients,v.label,v.r,v.field_disc_abs
            FROM polynomials p JOIN verifications v
              USING(submission_id,polynomial_index)
            WHERE v.label IN ('24T19325','24T20436')
              AND v.status='accepted' AND v.scoreable=1
            ORDER BY v.label,v.r,p.submission_id,p.polynomial_index
            """
        )
    )
    known_hashes = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT coefficient_hash FROM polynomials"
        )
    }
    for path in OUTBOX.glob("*.txt"):
        if path == MANIFEST:
            continue
        for line in path.read_text(errors="ignore").splitlines():
            values = line.split(",")
            if len(values) == 25:
                try:
                    normalized = ",".join(str(ZZ(value)) for value in values)
                except Exception:
                    continue
                known_hashes.add(sha256(normalized))

    selected = []
    for row in source_rows:
        label = str(row["label"])
        source_r = int(row["r"])
        coefficients = [ZZ(value) for value in row["coefficients"].split(",")]
        if (
            source_r not in ELIGIBLE_SOURCE_SIGNATURES[label]
            or row["coefficient_hash"] in prior_source_hashes
            or len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        selected.append((row, coefficients))
    if len(selected) != 7:
        raise ArithmeticError(f"expected seven untested even sources, got {len(selected)}")

    started = time.monotonic()
    results = []
    hits = []
    for position, (source, coefficients) in enumerate(selected, start=1):
        label = str(source["label"])
        action = actions[label]
        ring_y = PolynomialRing(ZZ, f"y{position}")
        quotient = ring_y(coefficients[::2])
        if (
            quotient.degree() != 12
            or not quotient.is_monic()
            or not quotient.is_irreducible()
        ):
            raise ArithmeticError("source quotient is not monic irreducible degree 12")

        resolvent = quotient.symmetric_power(3, monic=True)
        factorization = [
            (factor, int(exponent)) for factor, exponent in resolvent.factor()
        ]
        factors = [
            factor
            for factor, exponent in factorization
            if factor.degree() == 12 and exponent == 1
        ]
        if len(factors) != 1:
            raise ArithmeticError("triple resolvent lacks a unique degree-12 factor")
        factor = factors[0]
        ring_x = PolynomialRing(ZZ, f"x{position}")
        x = ring_x.gen()
        candidate = ring_x(factor)(x**2)
        if not candidate.is_monic() or not candidate.is_irreducible():
            raise ArithmeticError("derived degree-24 polynomial is not monic irreducible")
        r = int(candidate.number_of_real_roots())
        possible = {
            int(value)
            for value in action["sourceSignatureToPossibleTargetSignatures"][
                str(int(source["r"]))
            ]
        }
        if r not in possible:
            raise ArithmeticError("candidate signature contradicts exact action")

        target = (str(action["targetLabel"]), r)
        state = target_state(connection, *target)
        line = coefficient_line(candidate)
        digest = sha256(line)
        fresh = digest not in known_hashes
        live = bool(
            state["teamCount"] == 0
            and not state["discovered"]
            and not state["baseline"]
            and not state["owned"]
            and target not in STAGED_PAIRS
            and fresh
        )
        polynomial_disc = abs(ZZ(candidate.discriminant()))
        field_disc = abs(
            ZZ(NumberField(candidate, f"a{position}").absolute_discriminant())
        )
        result = {
            "position": position,
            "status": "exact_new_tc0_hit" if live else "exact_miss_current_tc0",
            "source": {
                "submissionId": source["submission_id"],
                "polynomialIndex": int(source["polynomial_index"]),
                "coefficientSha256": source["coefficient_hash"],
                "label": label,
                "r": int(source["r"]),
                "acceptedScoreable": True,
                "naturalQuotientIrreducible": True,
            },
            "candidate": {
                "coefficientLine": line,
                "coefficientSha256": digest,
                "degree": 24,
                "monic": True,
                "primitive": candidate.content() == 1,
                "irreducible": True,
                "r": r,
                "polynomialDiscriminantAbs": str(polynomial_disc),
                "fieldDiscriminantAbs": str(field_disc),
                "freshHash": fresh,
            },
            "target": {"label": target[0], "r": target[1]},
            "localTargetState": state,
            "exactGroupCertificate": {
                "conclusion": target[0],
                "uniqueSize12TripleOrbit": True,
                "uniqueDegree12ResolventFactor": True,
                "sourceExactAcceptedLabel": label,
                "sourceNaturalQuotientT12": int(action["quotientT12"]),
                "sourceKummerRank": int(action["sourceKummerRank"]),
                "targetKummerRank": int(action["targetKummerRank"]),
                "incidenceMatrixRank": int(action["incidenceMatrixRank"]),
                "targetOrder": int(action["targetOrder"]),
                "subsetOrbit": action["subsetOrbit"],
                "factorDegrees": [
                    {"degree": int(item.degree()), "exponent": exponent}
                    for item, exponent in factorization
                ],
                "proof": (
                    "The accepted exact source has the preserved natural "
                    "12-block Kummer action. Its unique size-12 orbit of "
                    "triples gives the unique irreducible degree-12 factor "
                    "of Sym^3(q); the stored exact GF(2) incidence action "
                    "therefore identifies the derived 24-point group."
                ),
            },
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        results.append(result)
        known_hashes.add(digest)
        if live:
            hits.append(result)
        print(
            json.dumps(
                {
                    "position": position,
                    "source": f"{label}/r{int(source['r'])}",
                    "target": f"{target[0]}/r{target[1]}",
                    "live": live,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    connection.close()
    result_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in results
    )
    manifest_text = "".join(row["candidate"]["coefficientLine"] + "\n" for row in hits)
    result_sha = write_atomic(RESULTS, result_text)
    manifest_sha = write_atomic(MANIFEST, manifest_text)
    summary = {
        "schemaVersion": "gold-kummer-p27-20260728-triple-v1",
        "status": "complete",
        "sourcesTested": len(results),
        "exactNewTc0Polynomials": len(hits),
        "exactNewTc0Pairs": sorted(
            {
                f"{row['target']['label']}/r{row['target']['r']}"
                for row in hits
            }
        ),
        "realizedPairs": [
            f"{row['target']['label']}/r{row['target']['r']}"
            for row in results
        ],
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": result_sha,
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": manifest_sha,
        "elapsedSeconds": time.monotonic() - started,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    write_atomic(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

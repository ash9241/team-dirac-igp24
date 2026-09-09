#!/usr/bin/env sage -python
"""Census signature-steered shifted Kummer lifts against current tc0 pairs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
OUTPUT = DATA / "shifted_kummer_gold_census.json"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def full_wreath_label(quotient_t: int) -> tuple[str, int]:
    fiber = libgap.Group(libgap.PermList([2, 1]))
    quotient = libgap.TransitiveGroup(12, quotient_t)
    wreath = libgap.WreathProduct(fiber, quotient)
    if int(libgap.NrMovedPoints(wreath)) != 24 or not bool(libgap.IsTransitive(wreath)):
        raise ArithmeticError("full quadratic wreath action is not transitive degree 24")
    expected_order = (2**12) * int(libgap.Size(quotient))
    if int(libgap.Size(wreath)) != expected_order:
        raise ArithmeticError("full quadratic wreath order mismatch")
    target_t = int(libgap.TransitiveIdentification(wreath))
    return f"24T{target_t}", expected_order


def main() -> int:
    if OUTPUT.exists():
        raise ValueError(f"refusing to overwrite {OUTPUT}")
    action_rows = [
        json.loads(line)
        for line in ACTION_MAP.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    quotient_by_source: dict[str, int] = {}
    ambiguous_sources = {}
    for row in action_rows:
        values = {
            int(system["blockActionT12"])
            for system in row.get("systems") or []
            if bool(system.get("flipInSource"))
        }
        if len(values) == 1:
            quotient_by_source[str(row["sourceLabel"])] = next(iter(values))
        elif values:
            ambiguous_sources[str(row["sourceLabel"])] = sorted(values)

    wreath_by_t = {
        quotient_t: full_wreath_label(quotient_t)
        for quotient_t in sorted(set(quotient_by_source.values()))
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    accepted_even = defaultdict(list)
    for row in connection.execute(
        """
        SELECT p.submission_id,p.polynomial_index,p.coefficients,p.coefficient_hash,
               v.label,v.r,v.field_disc_abs
        FROM polynomials p JOIN verifications v
          USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1 AND v.in_baseline=0
        """
    ):
        values = str(row["coefficients"]).split(",")
        if (
            str(row["label"]) in quotient_by_source
            and len(values) == 25
            and values[-1] == "1"
            and all(int(values[index]) == 0 for index in range(1, 25, 2))
        ):
            accepted_even[str(row["label"])].append(row)

    current_tc0 = defaultdict(list)
    for row in connection.execute(
        """
        SELECT t.label,t.r
        FROM targets t
        WHERE t.team_count=0 AND t.discovered=0
          AND NOT EXISTS(
            SELECT 1 FROM baseline_pairs b
            WHERE b.label=t.label AND b.r=t.r
          )
          AND NOT EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
          )
        """
    ):
        current_tc0[str(row["label"])].append(int(row["r"]))
    generated_at = connection.execute(
        "SELECT MAX(generated_at) FROM targets"
    ).fetchone()[0]
    connection.close()

    rows = []
    for source_label, sources in accepted_even.items():
        quotient_t = quotient_by_source[source_label]
        target_label, target_order = wreath_by_t[quotient_t]
        target_signatures = sorted(current_tc0.get(target_label, []))
        if not target_signatures:
            continue
        source = min(
            sources,
            key=lambda row: (
                len(str(row["coefficients"]).encode("utf-8")),
                str(row["coefficient_hash"]),
            ),
        )
        quotient_line = ",".join(str(source["coefficients"]).split(",")[::2])
        rows.append(
            {
                "fullWreathLabel": target_label,
                "fullWreathOrder": target_order,
                "liveTc0Signatures": target_signatures,
                "quotientCoefficientLine": quotient_line,
                "quotientT12": quotient_t,
                "source": {
                    "coefficientSha256": str(source["coefficient_hash"]),
                    "fieldDiscAbs": source["field_disc_abs"],
                    "label": source_label,
                    "polynomialIndex": int(source["polynomial_index"]),
                    "r": int(source["r"]),
                    "submissionId": str(source["submission_id"]),
                },
            }
        )
    rows.sort(
        key=lambda row: (
            -len(row["liveTc0Signatures"]),
            len(row["quotientCoefficientLine"].encode("utf-8")),
            int(row["fullWreathLabel"][3:]),
        )
    )
    result = {
        "schemaVersion": "shifted-kummer-gold-census-v1",
        "status": "light_group_census_complete",
        "method": "generic full C2-wreath labels of exact degree-12 quotient actions",
        "targetsGeneratedAt": generated_at,
        "candidateSourceLabels": len(rows),
        "candidateTc0Pairs": sum(len(row["liveTc0Signatures"]) for row in rows),
        "ambiguousSourceLabelsExcluded": len(ambiguous_sources),
        "artifacts": {
            "actionMap": str(ACTION_MAP.relative_to(ROOT)),
            "actionMapSha256": sha256_path(ACTION_MAP),
            "ledger": str(DB.relative_to(ROOT)),
        },
        "rows": rows,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "candidatePolynomialWrites": 0,
        },
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "candidateSourceLabels": result["candidateSourceLabels"],
                "candidateTc0Pairs": result["candidateTc0Pairs"],
                "output": str(OUTPUT.relative_to(ROOT)),
                "top": [
                    {
                        "source": row["source"]["label"],
                        "target": row["fullWreathLabel"],
                        "r": row["liveTc0Signatures"],
                    }
                    for row in rows[:10]
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

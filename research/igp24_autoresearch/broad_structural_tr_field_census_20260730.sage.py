#!/usr/bin/env sage
"""Find unused totally-real quotient fields in a 12x2 character family.

This deliberately broadens the source census to every accepted degree-24
label having at least one exact 12x2 block quotient ``12Tq``.  An even source
polynomial ``Q(x^2)`` supplies a degree-12 field directly.  The result is only
a field inventory: an exact quotient Galois-group check is still mandatory
before any Selmer lift is treated as belonging to the requested family.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from cysignals.alarm import AlarmInterrupt, alarm, cancel_alarm
from sage.all import PolynomialRing, QQ, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def canonical_line(polynomial) -> tuple[str, str]:
    reduced = polynomial.parent()(pari(polynomial).polredabs())
    line = ",".join(str(ZZ(value)) for value in reduced)
    return line, hashlib.sha256(line.encode("ascii")).hexdigest()


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def structural_labels(
    structures: Path,
    quotient_t: int,
    require_unique_12x2_quotient: bool = False,
) -> tuple[set[str], set[str]]:
    wanted = f"12T{quotient_t}"
    labels = set()
    full_rank_labels = set()
    with structures.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            matching = [
                system
                for system in row.get("blockSystems", [])
                if (
                system.get("shape") == "12x2"
                and system.get("quotientActionLabel") == wanted
                )
            ]
            quotient_labels = {
                str(system.get("quotientActionLabel"))
                for system in row.get("blockSystems", [])
                if system.get("shape") == "12x2"
            }
            if (
                require_unique_12x2_quotient
                and matching
                and quotient_labels != {wanted}
            ):
                matching = []
            if matching:
                labels.add(str(row["label"]))
            if any(
                int(system.get("blockKernelOrder", 0)) == 2048
                for system in matching
            ):
                full_rank_labels.add(str(row["label"]))
    return labels, full_rank_labels


def prior_hashes(quotient_t: int) -> set[str]:
    hashes = set()
    patterns = (
        f"character_field_gate_q{quotient_t}*local*.json",
        f"character_field_gate_q{quotient_t}_*.json",
    )
    paths = set()
    for pattern in patterns:
        paths.update(DATA.glob(pattern))
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for field in payload.get("fields", []):
            digest = field.get("fieldCanonicalSha256")
            if digest:
                hashes.add(str(digest))
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotient-t", required=True, type=int)
    parser.add_argument("--db", type=Path, default=DATA / "ledger.sqlite3")
    parser.add_argument(
        "--structures",
        type=Path,
        default=DATA / "agent_non12_tower_structures.jsonl",
    )
    parser.add_argument(
        "--accepted-row-start",
        type=int,
        default=0,
        help="zero-based offset into the deterministically ordered accepted rows",
    )
    parser.add_argument(
        "--accepted-row-count",
        type=int,
        default=0,
        help="process at most this many accepted rows; zero means all remaining",
    )
    parser.add_argument(
        "--polredabs-timeout-seconds",
        type=int,
        default=0,
        help="skip a quotient if canonical reduction exceeds this bound",
    )
    parser.add_argument(
        "--require-unique-12x2-quotient",
        action="store_true",
        help=(
            "retain only source labels whose every exact 12x2 block-system "
            "quotient has the requested transitive label"
        ),
    )
    parser.add_argument(
        "--required-real-roots",
        type=int,
        default=12,
        help=(
            "retain irreducible degree-12 quotients with exactly this many "
            "real roots (default: 12)"
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")

    label_set, full_rank_labels = structural_labels(
        args.structures,
        args.quotient_t,
        args.require_unique_12x2_quotient,
    )
    labels = sorted(label_set)
    if not labels:
        raise ValueError(f"no 12x2/12T{args.quotient_t} structural labels")
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.scoreable,v.submission_id,v.polynomial_index,
                   p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash
            """,
            tuple(labels),
        ).fetchall()
        target_placeholders = ",".join("?" for _label in full_rank_labels)
        live_pairs = connection.execute(
            f"""
            SELECT t.label,t.r,t.team_count,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label IN ({target_placeholders})
              AND t.team_count=0 AND t.discovered=0
              AND b.label IS NULL AND owned.label IS NULL
            ORDER BY CAST(substr(t.label,4) AS INTEGER),t.r
            """,
            tuple(sorted(full_rank_labels)),
        ).fetchall()
    finally:
        connection.close()

    total_accepted_rows = len(rows)
    if args.accepted_row_start < 0 or args.accepted_row_start > total_accepted_rows:
        raise ValueError("--accepted-row-start is outside the accepted-row census")
    accepted_row_stop = (
        min(
            total_accepted_rows,
            args.accepted_row_start + args.accepted_row_count,
        )
        if args.accepted_row_count
        else total_accepted_rows
    )
    rows = rows[args.accepted_row_start:accepted_row_stop]

    ring = PolynomialRing(QQ, "y")
    fields = {}
    counts = {
        "acceptedRows": 0,
        "evenRows": 0,
        "irreducibleQuotients": 0,
        "polredabsTimeoutRows": 0,
        "totallyRealRows": 0,
    }
    timed_out_rows = []
    for (
        label,
        source_r,
        scoreable,
        submission_id,
        polynomial_index,
        source_hash,
        text,
    ) in rows:
        counts["acceptedRows"] += 1
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        counts["evenRows"] += 1
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        counts["irreducibleQuotients"] += 1
        if (
            int(quotient.number_of_real_roots())
            != args.required_real_roots
        ):
            continue
        counts["totallyRealRows"] += 1
        try:
            if args.polredabs_timeout_seconds:
                alarm(args.polredabs_timeout_seconds)
            line, digest = canonical_line(quotient)
        except AlarmInterrupt:
            counts["polredabsTimeoutRows"] += 1
            timed_out_rows.append(
                {
                    "coefficientSha256": str(source_hash),
                    "label": str(label),
                    "polynomialIndex": int(polynomial_index),
                    "submissionId": str(submission_id),
                }
            )
            continue
        finally:
            if args.polredabs_timeout_seconds:
                cancel_alarm()
        item = fields.setdefault(
            digest,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": digest,
                "quotientT12ClaimedByStructuralSource": args.quotient_t,
                "sourceRows": [],
            },
        )
        item["sourceRows"].append(
            {
                "coefficientSha256": str(source_hash),
                "label": str(label),
                "polynomialIndex": int(polynomial_index),
                "r": int(source_r),
                "scoreable": bool(scoreable),
                "submissionId": str(submission_id),
            }
        )

    audited = prior_hashes(args.quotient_t)
    field_rows = []
    for digest in sorted(fields):
        item = fields[digest]
        item["previouslyAudited"] = digest in audited
        item["sourceRows"].sort(
            key=lambda row: (
                row["label"],
                row["r"],
                row["submissionId"],
                row["polynomialIndex"],
            )
        )
        field_rows.append(item)
    fresh = [row for row in field_rows if not row["previouslyAudited"]]
    payload = {
        "audit": {
            "acceptedRowSlice": {
                "start": args.accepted_row_start,
                "stop": accepted_row_stop,
                "total": total_accepted_rows,
            },
            "polredabsTimeoutSeconds": args.polredabs_timeout_seconds,
            "polredabsTimedOutRows": timed_out_rows,
            "requireUnique12x2Quotient": bool(
                args.require_unique_12x2_quotient
            ),
            "requiredRealRoots": int(args.required_real_roots),
            "networkCalls": 0,
            "submissionCalls": 0,
            "warning": (
                "structural source membership is not an exact Galois-group "
                "certificate for the extracted even quotient"
            ),
        },
        "fields": field_rows,
        "freshFields": fresh,
        "fullRankTargetLabels": sorted(full_rank_labels),
        "livePairsBeforeQueuedSubmissionExclusion": [
            {
                "generatedAt": str(generated_at) if generated_at else None,
                "label": str(label),
                "r": int(r),
                "teamCount": int(team_count),
            }
            for label, r, team_count, generated_at in live_pairs
        ],
        "quotientT12": args.quotient_t,
        "summary": {
            **counts,
            "acceptedRowSliceStart": args.accepted_row_start,
            "acceptedRowSliceStop": accepted_row_stop,
            "acceptedRowsInFullCensus": total_accepted_rows,
            "canonicalTotallyRealFields": len(field_rows),
            "freshCanonicalTotallyRealFields": len(fresh),
            "previouslyAuditedHashes": len(audited),
            "fullRankTargetLabels": len(full_rank_labels),
            "livePairsBeforeQueuedSubmissionExclusion": len(live_pairs),
            "structuralSourceLabels": len(labels),
        },
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

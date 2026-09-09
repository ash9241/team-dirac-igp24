#!/usr/bin/env python3
"""Select short accepted totally-real quotient presentations by 12T lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_b_even_twist_action_map.jsonl"


def jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def root_counts(quotients: list[tuple[int, ...]]) -> list[int]:
    result = []
    for start in range(0, len(quotients), 1000):
        chunk = quotients[start : start + 1000]
        commands = ['default(parisizemax,"4G");']
        commands.extend(
            "print(polsturm(Polrev([" + ",".join(map(str, row)) + "]))) ;"
            for row in chunk
        )
        commands.append("quit;")
        process = subprocess.run(
            ["gp", "-q"],
            input="\n".join(commands) + "\n",
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(f"PARI root-count batch failed: {process.stderr[-2000:]}")
        values = [int(value) for value in process.stdout.split()]
        if len(values) != len(chunk):
            raise RuntimeError("PARI root-count batch returned the wrong row count")
        result.extend(values)
    return result


def source_rows(
    connection: sqlite3.Connection,
    labels: set[str],
    scan_limit: int,
) -> list[dict]:
    rows = {}
    label_list = sorted(labels)
    for start in range(0, len(label_list), 700):
        chunk = label_list[start : start + 700]
        placeholders = ",".join("?" for _ in chunk)
        query = f"""
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,
                   v.submission_id,v.polynomial_index,v.field_disc_abs
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted'
              AND v.label IN ({placeholders})
        """
        for raw in connection.execute(query, chunk):
            line = str(raw[0])
            try:
                coefficients = tuple(int(value) for value in line.split(","))
            except ValueError:
                continue
            if (
                len(coefficients) != 25
                or coefficients[-1] != 1
                or any(coefficients[index] for index in range(1, 25, 2))
            ):
                continue
            digest = str(raw[1])
            value = {
                "coefficients": coefficients,
                "sourceCoefficientSha256": digest,
                "sourceFieldDiscriminantAbs": (
                    str(raw[6]) if raw[6] is not None else None
                ),
                "sourceLabel": str(raw[2]),
                "sourcePolynomialIndex": int(raw[5]),
                "sourceR": int(raw[3]),
                "sourceSubmissionId": str(raw[4]),
            }
            incumbent = rows.get(digest)
            if incumbent is None or (
                len(line),
                value["sourceSubmissionId"],
                value["sourcePolynomialIndex"],
            ) < (
                len(",".join(map(str, incumbent["coefficients"]))),
                incumbent["sourceSubmissionId"],
                incumbent["sourcePolynomialIndex"],
            ):
                rows[digest] = value
    ordered = sorted(
        rows.values(),
        key=lambda row: (
            len(",".join(map(str, row["coefficients"]))),
            row["sourceCoefficientSha256"],
        ),
    )
    return ordered[:scan_limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quotient-t", type=int, action="append", required=True)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="skip this many totally-real presentations per quotient lane",
    )
    parser.add_argument("--scan-limit", type=int, default=5000)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--actions", type=Path, default=ACTIONS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    if args.offset < 0:
        parser.error("--offset must be nonnegative")

    label_q: dict[str, set[int]] = {}
    for row in jsonl(args.actions):
        label = str(row["sourceLabel"])
        label_q[label] = {
            int(system["blockActionT12"])
            for system in row.get("systems", [])
        }
    q_labels = {
        q: {label for label, values in label_q.items() if q in values}
        for q in sorted(set(args.quotient_t))
    }

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    lanes = []
    try:
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE status='accepted'"
            )
        }
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        for q in sorted(q_labels):
            sources = source_rows(connection, q_labels[q], args.scan_limit)
            quotient_by_presentation = {}
            for source in sources:
                quotient = tuple(source["coefficients"][::2])
                presentation = ",".join(map(str, quotient))
                digest = hashlib.sha256(presentation.encode("ascii")).hexdigest()
                quotient_by_presentation.setdefault(digest, (quotient, source))
            presentations = list(quotient_by_presentation.values())
            counts = root_counts([row[0] for row in presentations])
            selected = []
            totally_real_seen = 0
            for (quotient, source), count in zip(presentations, counts):
                if count != 12:
                    continue
                if totally_real_seen < args.offset:
                    totally_real_seen += 1
                    continue
                totally_real_seen += 1
                presentation = ",".join(map(str, quotient))
                selected.append(
                    {
                        key: value
                        for key, value in source.items()
                        if key != "coefficients"
                    }
                    | {
                        "quotientPolynomial": presentation,
                        "quotientPresentationSha256": hashlib.sha256(
                            presentation.encode("ascii")
                        ).hexdigest(),
                    }
                )
                if len(selected) == args.limit:
                    break
            gold_targets = []
            for label in sorted(q_labels[q]):
                for target in connection.execute(
                    "SELECT label,r,team_count,discovered,generated_at "
                    "FROM targets WHERE label=? AND team_count=0 AND discovered=0",
                    (label,),
                ):
                    pair = (str(target["label"]), int(target["r"]))
                    if pair in owned or pair in baseline:
                        continue
                    gold_targets.append(
                        {
                            "generatedAt": str(target["generated_at"]),
                            "label": pair[0],
                            "r": pair[1],
                            "teamCount": int(target["team_count"]),
                        }
                    )
            lanes.append(
                {
                    "quotientT12": q,
                    "selectedSourcePresentations": selected,
                    "currentGoldTargets": gold_targets,
                    "audit": {
                        "acceptedEvenSourcesScanned": len(sources),
                        "uniqueQuotientPresentations": len(presentations),
                        "totallyRealPresentationsSkipped": min(
                            args.offset, totally_real_seen
                        ),
                        "totallyRealPresentationsSelected": len(selected),
                    },
                }
            )
    finally:
        connection.close()

    payload = {
        "schemaVersion": "current-squareclass-tr-source-manifest-v1",
        "lanes": lanes,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "lanes": [
                    {
                        "q": lane["quotientT12"],
                        "sources": len(lane["selectedSourcePresentations"]),
                        "golds": len(lane["currentGoldTargets"]),
                        **lane["audit"],
                    }
                    for lane in lanes
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

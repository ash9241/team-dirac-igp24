#!/usr/bin/env python3
"""Stage Gold Hunter B's exact, positive shared candidates without submitting.

The script consumes only agent_gold_b Frobenius certificates, rechecks the
latest local target/ownership ledger, and writes an auditable manifest plus a
certificate.  It makes no network or submission calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB_PATH = DATA / "ledger.sqlite3"
DEFAULT_MANIFEST = OUTBOX / "agent_gold_b_positive_shared.txt"
DEFAULT_CERTIFICATE = DATA / "agent_gold_b_positive_shared_certificate.json"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def packet_rows(path: Path) -> dict[tuple[str, int], dict]:
    return {
        (str(row["sourceSubmissionId"]), int(row["sourcePolynomialIndex"])): row
        for row in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-team-count", type=int, default=4)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--certificate", type=Path, default=DEFAULT_CERTIFICATE)
    args = parser.parse_args()
    if args.max_team_count < 1:
        parser.error("--max-team-count must be positive")

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")
    }

    eligible = []
    certificate_paths = sorted(DATA.glob("agent_gold_b*frobenius_certificate.json"))
    for certificate_path in certificate_paths:
        certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
        input_path = Path(certificate.get("input", ""))
        if not input_path.is_file():
            continue
        packets = packet_rows(input_path)
        for row in certificate.get("rows", []):
            if row.get("status") != "resolved":
                continue
            source_key = (
                str(row["sourceSubmissionId"]),
                int(row["sourcePolynomialIndex"]),
            )
            packet = packets[source_key]
            factors = {
                int(factor["factorIndex"]): factor
                for factor in packet.get("candidates", [])
            }
            for assignment in row.get("assignments", []):
                coefficient_hash = str(assignment["coefficientSha256"])
                if coefficient_hash in known_hashes:
                    continue
                target = connection.execute(
                    """
                    SELECT
                        t.team_count,
                        t.minimum_disc_abs,
                        t.generated_at,
                        EXISTS(
                            SELECT 1 FROM baseline_pairs AS b
                            WHERE b.label=t.label AND b.r=t.r
                        ) AS baseline,
                        EXISTS(
                            SELECT 1 FROM verifications AS v
                            WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
                        ) AS owned
                    FROM targets AS t
                    WHERE t.label=? AND t.r=?
                    """,
                    (assignment["targetLabel"], int(assignment["targetR"])),
                ).fetchone()
                if (
                    target is None
                    or bool(target["baseline"])
                    or bool(target["owned"])
                    or int(target["team_count"]) < 1
                    or int(target["team_count"]) > args.max_team_count
                ):
                    continue
                factor = factors[int(assignment["factorIndex"])]
                coefficient_line = str(factor["coefficientLine"])
                if sha256_text(coefficient_line) != coefficient_hash:
                    raise ValueError(f"coefficient hash mismatch for {coefficient_hash}")
                eligible.append(
                    {
                        "certificatePath": str(certificate_path.relative_to(ROOT)),
                        "certificateSha256": hashlib.sha256(
                            certificate_path.read_bytes()
                        ).hexdigest(),
                        "coefficientLine": coefficient_line,
                        "coefficientSha256": coefficient_hash,
                        "factorIndex": int(assignment["factorIndex"]),
                        "polynomialDiscriminantAbs": str(
                            factor["polynomialDiscriminantAbs"]
                        ),
                        "projectedMarginalValue": 1.0
                        / (2 ** int(target["team_count"])),
                        "sourceLabel": str(row["sourceLabel"]),
                        "sourcePolynomialIndex": int(row["sourcePolynomialIndex"]),
                        "sourceR": int(row["sourceR"]),
                        "sourceSubmissionId": str(row["sourceSubmissionId"]),
                        "targetGeneratedAt": str(target["generated_at"]),
                        "targetLabel": str(assignment["targetLabel"]),
                        "targetMinimumDiscAbs": target["minimum_disc_abs"],
                        "targetR": int(assignment["targetR"]),
                        "teamCount": int(target["team_count"]),
                    }
                )

    connection.close()

    best = {}
    for row in eligible:
        key = (row["targetLabel"], row["targetR"])
        incumbent = best.get(key)
        if incumbent is None or int(row["polynomialDiscriminantAbs"]) < int(
            incumbent["polynomialDiscriminantAbs"]
        ):
            best[key] = row
    selected = sorted(
        best.values(), key=lambda row: (row["teamCount"], row["targetLabel"], row["targetR"])
    )

    manifest_text = "".join(f"{row['coefficientLine']}\n" for row in selected)
    write_atomic(args.manifest, manifest_text)
    result = {
        "method": "exact-pair-resolvent-plus-frobenius-shared-stage-v1",
        "ledger": str(DB_PATH.relative_to(ROOT)),
        "manifest": str(args.manifest.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
        "maxTeamCount": args.max_team_count,
        "networkCalls": 0,
        "polynomials": len(selected),
        "projectedMarginalValue": sum(
            row["projectedMarginalValue"] for row in selected
        ),
        "selected": [
            {key: value for key, value in row.items() if key != "coefficientLine"}
            for row in selected
        ],
        "submissionCalls": 0,
    }
    write_atomic(args.certificate, json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

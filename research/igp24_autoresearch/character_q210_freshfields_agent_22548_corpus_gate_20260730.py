#!/usr/bin/env python3
"""Seal the fresh-field corpus audit for q210 / sibling 24T22548.

The exact algebraic extraction lives in
``character_q210_freshfields_agent_source_census_20260730.sage.py`` and its
immutable JSON result.  This companion audit verifies that result, freezes the
current six live 24T22548 signatures, and records the remaining local corpus
searches (archived ledger, candidate database, JSON/JSONL artifacts, and the
queued q210 manifest).  Since the algebraic census has no fresh totally-real
field, both the local character gate and the exact Selmer/Frobenius phase have
empty input and are explicitly skipped.

There are no network calls, coefficient searches, staging operations, or
submissions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_ARCHIVE_DB = (
    DATA
    / "gpt56ultra_multiorbit_reseal_20260726_a"
    / "ledger_snapshot.sqlite3"
)
DEFAULT_CANDIDATE_DB = Path(
    "/path/to/igp24/routeA/igp24_authoritative.db"
)
DEFAULT_SOURCE_AUDIT = (
    DATA / "character_q210_freshfields_agent_source_census_20260730.json"
)
DEFAULT_OUTPUT = (
    DATA / "character_q210_freshfields_agent_22548_corpus_gate_20260730.json"
)
SOURCE_AUDIT_SHA256 = (
    "967e6e1b46beeb7f9014524edb63ff402bf7a73c07f4718104b53cdfa3f1ed05"
)
TARGET_LABEL = "24T22548"
EXPECTED_LIVE = [4, 8, 12, 16, 20, 24]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def source_labels(source: dict) -> list[str]:
    labels = list(source["sourceCorpus"]["structuralCatalog"]["labels"])
    if len(labels) != 33:
        raise ValueError(f"expected 33 q210 parent labels, got {len(labels)}")
    return labels


def current_live(db: Path) -> list[dict]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.r,t.team_count,t.discovered,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=?
              AND t.team_count=0
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.r
            """,
            (TARGET_LABEL,),
        ).fetchall()
    finally:
        connection.close()
    actual = [int(row[0]) for row in rows]
    if actual != EXPECTED_LIVE:
        raise ValueError(
            f"24T22548 strict live set changed: {actual}, "
            f"expected {EXPECTED_LIVE}"
        )
    return [
        {
            "baseline": False,
            "discovered": bool(discovered),
            "generatedAt": str(generated_at),
            "label": TARGET_LABEL,
            "locallyOwned": False,
            "r": int(r),
            "teamCount": int(team_count),
        }
        for r, team_count, discovered, generated_at in rows
    ]


def main_coefficient_hashes(db: Path) -> set[str]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
    finally:
        connection.close()


def archive_census(
    archive: Path, main_hashes: set[str], labels: list[str]
) -> dict:
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(
        f"file:{archive.resolve()}?mode=ro", uri=True
    )
    try:
        rows = connection.execute(
            f"""
            SELECT p.coefficient_hash
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            """,
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()
    hashes = {str(row[0]) for row in rows}
    novel = sorted(hashes - main_hashes)
    if novel:
        raise ValueError(f"archived ledger has q210 hashes absent from main: {novel}")
    return {
        "acceptedScoreableRows": len(rows),
        "distinctCoefficientHashes": len(hashes),
        "hashesAbsentFromMainLedger": novel,
        "path": str(archive.resolve()),
    }


def candidate_db_census(path: Path, labels: list[str]) -> dict:
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        candidates = int(
            connection.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]
        )
        verified = int(
            connection.execute("SELECT COUNT(*) FROM verification").fetchone()[0]
        )
        q210 = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM verification
                WHERE verified_label IN ({placeholders})
                """,
                tuple(labels),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    return {
        "candidateRows": candidates,
        "path": str(path.resolve()),
        "q210ParentVerificationRows": q210,
        "verificationRows": verified,
    }


def integer_sequence(value, length: int):
    if (
        isinstance(value, list)
        and len(value) == length
        and all(
            isinstance(item, (int, float)) and int(item) == item
            for item in value
        )
    ):
        return tuple(int(item) for item in value)
    if isinstance(value, str):
        text = value.strip().strip("[]()")
        if not re.fullmatch(r"[-+0-9, ]+", text):
            return None
        try:
            sequence = tuple(
                int(item.strip()) for item in text.split(",")
            )
        except ValueError:
            return None
        if len(sequence) == length:
            return sequence
    return None


def collect_sequences(value, length: int, output: set[str]) -> None:
    sequence = integer_sequence(value, length)
    if sequence is not None:
        line = ",".join(str(item) for item in sequence)
        output.add(hashlib.sha256(line.encode("ascii")).hexdigest())
    if isinstance(value, dict):
        for item in value.values():
            collect_sequences(item, length, output)
    elif isinstance(value, list) and sequence is None:
        for item in value:
            collect_sequences(item, length, output)


def relevant_json_paths(labels: list[str]) -> list[Path]:
    expression = "|".join(re.escape(label) for label in labels)
    process = subprocess.run(
        [
            "rg",
            "-l",
            expression,
            "data",
            "--glob",
            "*.json",
            "--glob",
            "*.jsonl",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in process.stdout.splitlines() if line]


def json_artifact_census(
    labels: list[str], main_hashes: set[str]
) -> dict:
    paths = relevant_json_paths(labels)
    hashes = set()
    parse_errors = []
    for path in paths:
        try:
            if path.suffix == ".jsonl":
                with path.open(errors="ignore", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip() or not any(
                            label in line for label in labels
                        ):
                            continue
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError as error:
                            parse_errors.append(
                                f"{path}:{line_number}:{error}"
                            )
                            continue
                        collect_sequences(value, 25, hashes)
            else:
                value = json.loads(path.read_text(errors="ignore"))
                collect_sequences(value, 25, hashes)
        except Exception as error:
            parse_errors.append(f"{path}:{error}")
    novel = sorted(hashes - main_hashes)
    if parse_errors or novel:
        raise ValueError(
            f"artifact corpus failed: errors={parse_errors}, novel={novel}"
        )
    return {
        "filesMentioningExactParentLabels": len(paths),
        "parseErrors": parse_errors,
        "uniqueDegree24CoefficientHashes": len(hashes),
        "hashesAlreadyInMainLedger": len(hashes & main_hashes),
        "hashesAbsentFromMainLedger": novel,
    }


def queued_q210_census(db: Path) -> dict:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        submissions = connection.execute(
            """
            SELECT submission_id,created_at,queued_count,verified_count,
                   failed_count,description
            FROM submissions
            WHERE lower(description) LIKE '%q210%'
            ORDER BY created_at,submission_id
            """
        ).fetchall()
        polynomial_rows = []
        for row in submissions:
            polynomial_rows.extend(
                connection.execute(
                    """
                    SELECT p.coefficient_hash,
                           CASE WHEN v.submission_id IS NULL THEN 0 ELSE 1 END
                    FROM polynomials AS p
                    LEFT JOIN verifications AS v
                      USING(submission_id,polynomial_index)
                    WHERE p.submission_id=?
                    ORDER BY p.polynomial_index
                    """,
                    (row[0],),
                ).fetchall()
            )
    finally:
        connection.close()
    stage_path = DATA / "q210_five_gold_stage_20260730.json"
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    stage_hashes = {
        str(item["sha256"]) for item in stage.get("selected", [])
    }
    database_hashes = {str(row[0]) for row in polynomial_rows}
    if stage_hashes != database_hashes:
        raise ValueError("queued q210 database rows differ from sealed manifest")
    submitted_field_hashes = sorted(
        {str(item["fieldCanonicalSha256"]) for item in stage["selected"]}
    )
    return {
        "degree24CoefficientHashes": sorted(database_hashes),
        "fieldCanonicalHashes": submitted_field_hashes,
        "manifest": {
            "path": str((OUTBOX / "q210_five_gold_20260730.txt").resolve()),
            "sha256": str(stage["manifest"]["sha256"]),
        },
        "polynomialRows": len(polynomial_rows),
        "rowsWithoutVerification": sum(not bool(row[1]) for row in polynomial_rows),
        "stageArtifact": {
            "path": str(stage_path.resolve()),
            "sha256": sha256_file(stage_path),
        },
        "submissions": [
            {
                "createdAt": str(created_at),
                "description": str(description),
                "failedCount": int(failed_count),
                "queuedCount": int(queued_count),
                "submissionId": str(submission_id),
                "verifiedCount": int(verified_count),
            }
            for (
                submission_id,
                created_at,
                queued_count,
                verified_count,
                failed_count,
                description,
            ) in submissions
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--archive-db", type=Path, default=DEFAULT_ARCHIVE_DB)
    parser.add_argument(
        "--candidate-db", type=Path, default=DEFAULT_CANDIDATE_DB
    )
    parser.add_argument(
        "--source-audit", type=Path, default=DEFAULT_SOURCE_AUDIT
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite q210 corpus-gate artifact")

    actual_source_sha = sha256_file(args.source_audit)
    if actual_source_sha != SOURCE_AUDIT_SHA256:
        raise ValueError(
            f"source audit hash mismatch: {actual_source_sha}, "
            f"expected {SOURCE_AUDIT_SHA256}"
        )
    source = json.loads(args.source_audit.read_text(encoding="utf-8"))
    labels = source_labels(source)
    if source["summary"]["freshTotallyRealCanonicalFields"] != 0:
        raise ValueError("source audit now contains a fresh totally-real field")
    if source["freshTotallyRealFields"]:
        raise ValueError("fresh field list is unexpectedly nonempty")

    main_hashes = main_coefficient_hashes(args.db)
    live = current_live(args.db)
    queued = queued_q210_census(args.db)
    audited_hashes = set(source["audit"]["priorAuditedFieldHashes"])
    submitted_hashes = set(queued["fieldCanonicalHashes"])
    if not submitted_hashes.issubset(audited_hashes):
        raise ValueError(
            "q210 submitted field hashes are absent from prior-audit exclusion"
        )

    corpus = {
        "archivedLedger": archive_census(
            args.archive_db, main_hashes, labels
        ),
        "candidateDatabase": candidate_db_census(
            args.candidate_db, labels
        ),
        "jsonArtifacts": json_artifact_census(labels, main_hashes),
        "queuedQ210Submission": queued,
        "sourceAudit": {
            "path": str(args.source_audit.resolve()),
            "sha256": actual_source_sha,
            "summary": source["summary"],
        },
    }
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "constructionAlternatives": 0,
            "networkCalls": 0,
            "priorAuditedFieldHashes": sorted(audited_hashes),
            "submissionCalls": 0,
            "submittedFieldHashes": sorted(submitted_hashes),
        },
        "certifiedPairs": [],
        "exactSelmerAndFrobeniusGate": {
            "inputFieldCount": 0,
            "pairs": [],
            "status": "skipped_no_fresh_totally_real_fields",
        },
        "freshTotallyRealFields": [],
        "livePairs": live,
        "localAllAmbiguousCharacterGate": {
            "inputFieldCount": 0,
            "pairFieldRoutes": [],
            "status": "skipped_no_fresh_totally_real_fields",
        },
        "sourceCorpus": corpus,
        "summary": {
            "certifiedPairs": 0,
            "freshTotallyRealCanonicalFields": 0,
            "livePairs": len(live),
            "localPassingPairFieldRoutes": 0,
            "targetLabel": TARGET_LABEL,
        },
        "targetLabel": TARGET_LABEL,
    }
    rendered = rendered_json(payload)
    write_atomic(args.output.resolve(), rendered)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

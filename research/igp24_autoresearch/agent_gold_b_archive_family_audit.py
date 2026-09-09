#!/usr/bin/env python3
"""Join archived IGP24 generator batches exactly to the current local ledger.

The audit is offline and read-only with respect to the archive and ledger.  It
hashes canonical coefficient lines, resolves repeated submissions to the
earliest scoreable verification, and reports current gold/solo yield using the
ledger's target snapshot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import zlib
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent
DEFAULT_ARCHIVE_ROOT = Path("/path/to/private-file")


FAMILY_PATTERNS = {
    "dynamic_v1": re.compile(r"^dynamic_batch_v1_(\d+)\.txt$"),
    "k1_mixed": re.compile(r"^k1_batch_(\d+)\.txt$"),
    "rank_breaker_sum_4x6": re.compile(r"^rank_breaker_(\d+)\.txt$"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_coefficients(line: str) -> tuple[str, list[int]]:
    values = [int(value.strip()) for value in line.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("batch line is not a monic degree-24 integer polynomial")
    return ",".join(map(str, values)), values


def k1_shape(values: list[int]) -> str:
    """Recover a high-confidence structural class from unreduced K1 output."""
    nonzero = [index for index, value in enumerate(values) if value]
    if (
        len(nonzero) == 3
        and nonzero[-1] == 24
        and values[0] in (-2, -1, 1, 2)
        and 1 <= nonzero[1] <= 23
        and values[nonzero[1]] in (-3, -2, -1, 1, 2, 3)
    ):
        return "extreme_sparse_exact_shape"
    for prime in (3, 5, 7, 11, 13):
        if (
            values[0] % prime == 0
            and values[0] % (prime * prime) != 0
            and all(value % prime == 0 for value in values[:24])
        ):
            return f"eisenstein_p{prime}_exact_shape"
    if (
        values[0] in (-1, 1)
        and all(values[24 - index] == values[index] * values[0] for index in range(1, 12))
    ):
        return "reciprocal_skewed_exact_shape"
    for extension_degree in (12, 8, 6, 4, 3, 2):
        if all(index % extension_degree == 0 for index in nonzero):
            return f"cyclic_x_pow_{extension_degree}_compatible_shape"
    return "asymmetric_composition_residual_shape"


def archive_comparison(archive_root: Path) -> list[dict]:
    rows = []
    for name in ("IGP24_Dirac_Data.zip", "igp.zip"):
        path = archive_root / name
        if not path.exists():
            continue
        archived_batches = 0
        identical_local_batches = 0
        missing_local_batches = 0
        with ZipFile(path) as archive:
            for info in archive.infolist():
                base = Path(info.filename).name
                if not re.match(r"^k1_batch_\d+\.txt$", base):
                    continue
                archived_batches += 1
                local = archive_root / base
                if not local.exists():
                    missing_local_batches += 1
                    continue
                checksum = 0
                with local.open("rb") as handle:
                    for block in iter(lambda: handle.read(1 << 20), b""):
                        checksum = zlib.crc32(block, checksum)
                if local.stat().st_size == info.file_size and checksum & 0xFFFFFFFF == info.CRC:
                    identical_local_batches += 1
        rows.append(
            {
                "archive": str(path),
                "archiveBytes": path.stat().st_size,
                "archiveSha256": sha256_file(path),
                "archivedK1Batches": archived_batches,
                "identicalCurrentDirectoryBatches": identical_local_batches,
                "missingCurrentDirectoryBatches": missing_local_batches,
            }
        )
    return rows


def dict_rows(cursor) -> list[dict]:
    names = [description[0] for description in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "agent_gold_b_archive_family_audit.json"
    )
    parser.add_argument(
        "--matches", type=Path, default=ROOT / "data" / "agent_gold_b_archive_family_matches.jsonl"
    )
    args = parser.parse_args()

    batch_files = []
    for path in args.archive_root.iterdir():
        if not path.is_file():
            continue
        for family, pattern in FAMILY_PATTERNS.items():
            match = pattern.match(path.name)
            if match:
                batch_files.append((family, int(match.group(1)), path))
                break
    batch_files.sort(key=lambda row: (row[0], row[1]))

    connection = sqlite3.connect(f"file:{args.db}?immutable=1", uri=True)
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute(
        """
        CREATE TEMP TABLE archive_lines (
            line_id INTEGER PRIMARY KEY,
            family TEXT NOT NULL,
            shape_class TEXT NOT NULL,
            batch_name TEXT NOT NULL,
            batch_index INTEGER NOT NULL,
            line_index INTEGER NOT NULL,
            generated_at_local TEXT NOT NULL,
            coefficient_hash TEXT NOT NULL,
            coefficients TEXT NOT NULL
        )
        """
    )

    invalid_rows = []
    canonical_changes = 0
    insert_rows = []
    line_id = 0
    family_files = defaultdict(int)
    family_bytes = defaultdict(int)
    family_first = {}
    family_last = {}
    for family, batch_index, path in batch_files:
        timestamp = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        generated = timestamp.isoformat(timespec="seconds")
        family_files[family] += 1
        family_bytes[family] += path.stat().st_size
        family_first[family] = min(family_first.get(family, generated), generated)
        family_last[family] = max(family_last.get(family, generated), generated)
        with path.open("r", encoding="utf-8") as handle:
            for line_index, raw_line in enumerate(handle, 1):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    coefficients, values = canonical_coefficients(stripped)
                except Exception as exc:
                    invalid_rows.append(
                        {
                            "batch": path.name,
                            "error": str(exc),
                            "lineIndex": line_index,
                        }
                    )
                    continue
                canonical_changes += int(coefficients != stripped)
                shape = k1_shape(values) if family == "k1_mixed" else family
                line_id += 1
                insert_rows.append(
                    (
                        line_id,
                        family,
                        shape,
                        path.name,
                        batch_index,
                        line_index,
                        generated,
                        hashlib.sha256(coefficients.encode()).hexdigest(),
                        coefficients,
                    )
                )
                if len(insert_rows) >= 10000:
                    connection.executemany(
                        "INSERT INTO archive_lines VALUES (?,?,?,?,?,?,?,?,?)", insert_rows
                    )
                    insert_rows.clear()
    if insert_rows:
        connection.executemany(
            "INSERT INTO archive_lines VALUES (?,?,?,?,?,?,?,?,?)", insert_rows
        )
    connection.execute(
        "CREATE INDEX archive_lines_hash ON archive_lines(coefficient_hash)"
    )
    connection.execute(
        "CREATE INDEX archive_lines_family ON archive_lines(family,shape_class)"
    )

    connection.execute(
        """
        CREATE TEMP TABLE matched_ranked AS
        SELECT a.*,
               p.submission_id,p.polynomial_index,s.created_at AS submitted_at,
               v.status,v.label,v.t,v.r,v.scoring_status,v.scoreable,
               v.in_baseline,v.field_disc_abs,v.poly_disc_abs,
               target.team_count AS current_team_count,
               target.minimum_disc_abs AS current_minimum_disc_abs,
               ROW_NUMBER() OVER (
                   PARTITION BY a.line_id
                   ORDER BY COALESCE(v.scoreable,0) DESC,
                            COALESCE(s.created_at,'9999') ASC,
                            p.submission_id,p.polynomial_index
               ) AS match_rank,
               COUNT(*) OVER (PARTITION BY a.line_id) AS ledger_occurrences
        FROM archive_lines AS a
        JOIN polynomials AS p
          ON p.coefficient_hash=a.coefficient_hash
         AND p.coefficients=a.coefficients
        LEFT JOIN submissions AS s USING (submission_id)
        LEFT JOIN verifications AS v USING (submission_id,polynomial_index)
        LEFT JOIN targets AS target ON target.label=v.label AND target.r=v.r
        """
    )
    connection.execute(
        "CREATE TEMP TABLE matched_best AS SELECT * FROM matched_ranked WHERE match_rank=1"
    )
    connection.execute("CREATE INDEX matched_best_line ON matched_best(line_id)")

    aggregate_sql = """
        SELECT a.family{shape_select},
               COUNT(*) AS lineCount,
               COUNT(DISTINCT a.coefficient_hash) AS uniqueCoefficientCount,
               COUNT(*)-COUNT(DISTINCT a.coefficient_hash) AS duplicateLineCount,
               SUM(m.line_id IS NOT NULL) AS ledgerMatchedLineCount,
               COUNT(DISTINCT CASE WHEN m.line_id IS NOT NULL THEN a.coefficient_hash END)
                   AS ledgerMatchedUniqueCoefficientCount,
               SUM(COALESCE(m.scoreable,0)=1) AS scoreableLineCount,
               COUNT(DISTINCT CASE WHEN m.scoreable=1 THEN a.coefficient_hash END)
                   AS scoreableUniqueCoefficientCount,
               COUNT(DISTINCT CASE WHEN m.scoreable=1 THEN m.label||'/r'||m.r END)
                   AS uniqueScoreablePairCount,
               SUM(m.scoreable=1 AND COALESCE(m.in_baseline,0)=0)
                   AS nonbaselineScoreableLineCount,
               SUM(m.scoreable=1 AND COALESCE(m.in_baseline,0)=0
                   AND m.current_team_count=0) AS currentGoldLineCount,
               COUNT(DISTINCT CASE WHEN m.scoreable=1 AND COALESCE(m.in_baseline,0)=0
                   AND m.current_team_count=0 THEN m.label||'/r'||m.r END)
                   AS currentGoldUniquePairCount,
               SUM(m.scoreable=1 AND COALESCE(m.in_baseline,0)=0
                   AND m.current_team_count=1) AS currentSoloLineCount,
               COUNT(DISTINCT CASE WHEN m.scoreable=1 AND COALESCE(m.in_baseline,0)=0
                   AND m.current_team_count<=1 THEN m.label||'/r'||m.r END)
                   AS currentRareUniquePairCount,
               MIN(m.submitted_at) AS earliestMatchedSubmission,
               MAX(m.submitted_at) AS latestMatchedSubmission
        FROM archive_lines AS a
        LEFT JOIN matched_best AS m USING (line_id)
        GROUP BY a.family{shape_group}
        ORDER BY currentGoldUniquePairCount DESC,currentRareUniquePairCount DESC,
                 scoreableUniqueCoefficientCount DESC
    """
    family_summary = dict_rows(
        connection.execute(
            aggregate_sql.format(shape_select="", shape_group="")
        )
    )
    shape_summary = dict_rows(
        connection.execute(
            aggregate_sql.format(
                shape_select=",a.shape_class", shape_group=",a.shape_class"
            )
        )
    )

    time_summary = dict_rows(
        connection.execute(
            """
            SELECT a.family,
                   substr(a.generated_at_local,1,13)||':00' AS generatedHourLocal,
                   COUNT(*) AS lineCount,
                   SUM(m.line_id IS NOT NULL) AS ledgerMatchedLineCount,
                   SUM(COALESCE(m.scoreable,0)=1) AS scoreableLineCount,
                   SUM(m.scoreable=1 AND COALESCE(m.in_baseline,0)=0
                       AND m.current_team_count=0) AS currentGoldLineCount,
                   COUNT(DISTINCT CASE WHEN m.scoreable=1
                       AND COALESCE(m.in_baseline,0)=0 AND m.current_team_count=0
                       THEN m.label||'/r'||m.r END) AS currentGoldUniquePairCount,
                   MIN(m.submitted_at) AS earliestMatchedSubmission,
                   MAX(m.submitted_at) AS latestMatchedSubmission
            FROM archive_lines AS a
            LEFT JOIN matched_best AS m USING (line_id)
            GROUP BY a.family,generatedHourLocal
            ORDER BY generatedHourLocal,a.family
            """
        )
    )

    rare_pairs = dict_rows(
        connection.execute(
            """
            SELECT m.family,m.shape_class,m.label,m.t,m.r,m.current_team_count,
                   COUNT(*) AS matchedLineCount,
                   COUNT(DISTINCT m.coefficient_hash) AS uniqueCoefficientCount,
                   MIN(m.submitted_at) AS earliestSubmission,
                   MIN(m.batch_index) AS earliestBatchIndex,
                   MIN(m.batch_name) AS exampleBatch,
                   MIN(m.line_index) AS exampleLineIndex,
                   MIN(m.coefficient_hash) AS exampleCoefficientSha256,
                   MIN(m.field_disc_abs) AS exampleFieldDiscAbs
            FROM matched_best AS m
            WHERE m.scoreable=1 AND COALESCE(m.in_baseline,0)=0
              AND m.current_team_count<=1
            GROUP BY m.family,m.shape_class,m.label,m.t,m.r,m.current_team_count
            ORDER BY m.current_team_count,m.t,m.r,m.family,m.shape_class
            """
        )
    )

    match_cursor = connection.execute(
        """
        SELECT family,shape_class,batch_name,batch_index,line_index,
               generated_at_local,coefficient_hash,submission_id,
               polynomial_index,submitted_at,status,label,t,r,scoring_status,
               scoreable,in_baseline,field_disc_abs,poly_disc_abs,
               current_team_count,current_minimum_disc_abs,ledger_occurrences
        FROM matched_best
        ORDER BY family,batch_index,line_index
        """
    )
    match_names = [description[0] for description in match_cursor.description]
    match_count = 0
    with args.matches.resolve().open("w", encoding="utf-8") as handle:
        for row in match_cursor:
            handle.write(json.dumps(dict(zip(match_names, row)), sort_keys=True) + "\n")
            match_count += 1

    source_scripts = []
    for name in (
        "igp24_k1_hunter.sage",
        "igp24_rank_breaker.sage",
        "igp24_dynamic_sieve_1.sage",
    ):
        path = args.archive_root / name
        if path.exists():
            source_scripts.append(
                {
                    "bytes": path.stat().st_size,
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
            )

    report = {
        "archiveComparison": archive_comparison(args.archive_root),
        "batchInventory": [
            {
                "bytes": family_bytes[family],
                "family": family,
                "fileCount": family_files[family],
                "firstGeneratedAtLocal": family_first[family],
                "lastGeneratedAtLocal": family_last[family],
            }
            for family in sorted(family_files)
        ],
        "familySummary": family_summary,
        "invalidRows": invalid_rows,
        "matchesArtifact": str(args.matches.resolve()),
        "matchesArtifactBytes": args.matches.resolve().stat().st_size,
        "matchesArtifactSha256": sha256_file(args.matches.resolve()),
        "matchedLineCount": match_count,
        "provenance": {
            "archiveRoot": str(args.archive_root.resolve()),
            "canonicalCoefficientHash": "sha256(','.join(decimal integer coefficients))",
            "canonicalChanges": canonical_changes,
            "ledger": str(args.db.resolve()),
            "ledgerBytes": args.db.stat().st_size,
            "networkCalls": 0,
            "sourceScripts": source_scripts,
            "submissionCalls": 0,
        },
        "rarePairDefinition": (
            "scoreable, nonbaseline ledger verification whose current target "
            "team_count is 0 (gold) or 1 (solo)"
        ),
        "rarePairs": rare_pairs,
        "shapeSummary": shape_summary,
        "timeSummary": time_summary,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.resolve().write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "families": len(family_summary),
                "matchedLines": match_count,
                "output": str(args.output.resolve()),
                "outputBytes": len(rendered.encode()),
                "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "rarePairs": len(rare_pairs),
            },
            sort_keys=True,
        )
    )
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

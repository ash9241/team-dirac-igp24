#!/usr/bin/env sage -python
"""Build a broad exact degree-12 base-field census for 51 live gold pairs.

The historical Cross-1500 bank retained a compact two-base set cover even
though its discovery engine used 216 bases.  This supervisor audits every
locally verified scoreable even degree-24 row, derives all compatible
degree-12 quotient candidates for the current shallow-absent gold pairs,
reuses locked exact alignments, and expands them with six process-isolated
exact alignment workers.  Quotient fields are deduplicated by their PARI
``polredabs`` defining polynomial, not by the original generator polynomial.

No generated polynomial search, network call, submission, or staging occurs
in this program.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import glob
import hashlib
import json
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_AUDIT = DATA / "agent_gold_b_character_all_r_gold_audit.json"
DEFAULT_LOCKED_BANK = DATA / "agent_gold_a_cross1500_character_bank.jsonl"
DEFAULT_SHALLOW_RESULTS = DATA / "agent_gold_a_cross1500_results"
DEFAULT_CENSUS = DATA / "agent_gold_c_character_field_census.jsonl"
DEFAULT_RESULTS = DATA / "agent_gold_c_character_census_alignment_results.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_c_character_census_summary.json"
DEFAULT_ARTIFACT_DIR = DATA / "agent_gold_c_character_census_alignments"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
WORKER = ROOT / "agent_gold_c_character_census_worker.sage.py"
EXCLUDED_PAIR = ("24T18497", 0)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def pair_from_payload(payload: dict) -> tuple[str, int]:
    label, r_text = str(payload["audit"]["liveTarget"]["pair"]).split("/r", 1)
    return label, int(r_text)


def current_gold_pairs(
    connection: sqlite3.Connection, shallow_results: Path
) -> tuple[list[tuple[str, int]], dict]:
    absent = set()
    result_paths = sorted(shallow_results.glob("*.json"))
    statuses = Counter()
    provenance = []
    for path in result_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = str(payload["search"]["status"])
        statuses[status] += 1
        if status == "target_signature_absent_from_s_unit_space":
            absent.add(pair_from_payload(payload))
        provenance.append(f"{path.relative_to(ROOT)}\t{sha256_path(path)}\n")

    live = []
    snapshots = {}
    for pair in sorted(absent):
        target = connection.execute(
            "SELECT team_count,minimum_disc_abs,generated_at FROM targets "
            "WHERE label=? AND r=?",
            pair,
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
        ).fetchone()
        owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
            pair,
        ).fetchone()
        snapshot = {
            "baseline": baseline is not None,
            "generatedAt": str(target[2]) if target and target[2] else None,
            "locallyOwned": owned is not None,
            "minimumDiscAbs": str(target[1]) if target and target[1] else None,
            "teamCount": int(target[0]) if target else None,
        }
        snapshots[f"{pair[0]}/r{pair[1]}"] = snapshot
        if (
            pair != EXCLUDED_PAIR
            and target is not None
            and int(target[0]) == 0
            and baseline is None
            and owned is None
        ):
            live.append(pair)
    return live, {
        "completedShallowOutputs": len(result_paths),
        "liveTargetSnapshots": snapshots,
        "resultSetSha256": hashlib.sha256("".join(provenance).encode()).hexdigest(),
        "searchStatusCounts": dict(sorted(statuses.items())),
    }


def current_rank11_campaign(
    connection: sqlite3.Connection, structures_path: Path
) -> tuple[list[tuple[str, int]], dict, list[dict], dict[int, set[str]]]:
    """Read every live no-baseline rank-11 character-kernel pair directly.

    The July campaign intentionally restricted itself to a historical set of
    51 shallow-search failures.  Emergency recovery needs the entire current
    reservoir, so this mode joins today's target snapshot to the exact GAP
    block-system census instead of depending on old search-result files.
    """
    structures = {}
    for row in load_jsonl(structures_path):
        systems = [
            system
            for system in row.get("blockSystems", [])
            if system.get("shape") == "12x2"
            and int(system.get("blockKernelOrder", 0)) == 2**11
        ]
        if not systems:
            continue
        quotient_labels = {str(system["quotientActionLabel"]) for system in systems}
        if len(quotient_labels) != 1:
            raise ValueError(
                f"rank-11 label {row['label']} has ambiguous quotient actions: "
                f"{sorted(quotient_labels)}"
            )
        quotient_label = next(iter(quotient_labels))
        if not quotient_label.startswith("12T"):
            raise ValueError(f"unexpected quotient label {quotient_label}")
        structures[str(row["label"])] = {
            "quotientT12": int(quotient_label[3:]),
            "t": int(row["t"]),
        }

    query = """
        SELECT t.label,t.t,t.r,t.team_count,t.discovered,
               t.minimum_disc_abs,t.generated_at
        FROM targets AS t
        LEFT JOIN baseline_pairs AS b
          ON b.label=t.label AND b.r=t.r
        LEFT JOIN (
            SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
        ) AS owned
          ON owned.label=t.label AND owned.r=t.r
        WHERE t.team_count=0 AND b.label IS NULL AND owned.label IS NULL
        ORDER BY t.t,t.r
    """
    live = []
    targets = []
    labels_by_q: dict[int, set[str]] = defaultdict(set)
    snapshots = {}
    for label, target_t, target_r, team_count, discovered, minimum, generated in connection.execute(query):
        label = str(label)
        structure = structures.get(label)
        if structure is None:
            continue
        pair = (label, int(target_r))
        quotient_t = int(structure["quotientT12"])
        live.append(pair)
        targets.append(
            {
                "label": label,
                "quotientT12": quotient_t,
                "r": int(target_r),
                "t": int(target_t),
            }
        )
        labels_by_q[quotient_t].add(label)
        snapshots[f"{label}/r{int(target_r)}"] = {
            "baseline": False,
            "discovered": int(discovered),
            "generatedAt": str(generated) if generated else None,
            "locallyOwned": False,
            "minimumDiscAbs": str(minimum) if minimum else None,
            "teamCount": int(team_count),
        }
    audit = {
        "campaignMode": "current_rank11",
        "liveTargetSnapshots": snapshots,
        "structureRows": len(structures),
        "structuresPath": str(structures_path.resolve()),
        "structuresSha256": sha256_path(structures_path),
    }
    return live, audit, targets, labels_by_q


def target_metadata(audit: Path, live_pairs: list[tuple[str, int]]):
    payload = json.loads(audit.read_text(encoding="utf-8"))
    by_label = {str(row["label"]): row for row in payload["rows"]}
    targets = []
    labels_by_q: dict[int, set[str]] = defaultdict(set)
    for label, target_r in live_pairs:
        row = by_label[label]
        quotient_t = int(row["quotientT12"])
        targets.append(
            {
                "label": label,
                "quotientT12": quotient_t,
                "r": target_r,
                "t": int(row["t"]),
            }
        )
        labels_by_q[quotient_t].add(label)
    return targets, labels_by_q


def compatible_source_labels(action_map: Path, quotient_ts: set[int]):
    labels_by_q = {value: set() for value in quotient_ts}
    system_counts = {}
    for line in action_map.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        label = str(row["sourceLabel"])
        count = int(row["systemCount"])
        for system in row["systems"]:
            quotient_t = int(system["blockActionT12"])
            if quotient_t not in labels_by_q:
                continue
            labels_by_q[quotient_t].add(label)
            key = (quotient_t, label)
            system_counts[key] = min(count, system_counts.get(key, count))
    label_to_qs: dict[str, set[int]] = defaultdict(set)
    for quotient_t, labels in labels_by_q.items():
        for label in labels:
            label_to_qs[label].add(quotient_t)
    return labels_by_q, label_to_qs, system_counts


def even_quotient(coefficients: str):
    try:
        values = [int(value) for value in coefficients.split(",")]
    except ValueError:
        return None
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] <= 0
        or any(values[index] for index in range(1, 25, 2))
    ):
        return None
    line = ",".join(str(value) for value in values[::2])
    return line, hashlib.sha256(line.encode()).hexdigest()


def exhaustive_candidate_census(
    connection: sqlite3.Connection,
    label_to_qs: dict[str, set[int]],
    system_counts: dict,
):
    all_even_rows = 0
    all_even_coefficients = set()
    all_quotients = set()
    candidates: dict[int, list[dict]] = defaultdict(list)
    query = """
        SELECT v.label,v.r,v.field_disc_abs,v.submission_id,
               v.polynomial_index,p.coefficients,p.coefficient_hash
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
    """
    scanned = 0
    for row in connection.execute(query):
        scanned += 1
        quotient = even_quotient(str(row[5]))
        if quotient is None:
            continue
        quotient_line, quotient_hash = quotient
        all_even_rows += 1
        all_even_coefficients.add(str(row[6]))
        all_quotients.add(quotient_hash)
        label = str(row[0])
        for quotient_t in label_to_qs.get(label, ()):
            candidates[quotient_t].append(
                {
                    "blockSystemCount": int(system_counts[(quotient_t, label)]),
                    "coefficientBytes": len(str(row[5]).encode()),
                    "coefficientSha256": str(row[6]),
                    "fieldDiscAbs": str(row[2]) if row[2] else None,
                    "label": label,
                    "polynomialIndex": int(row[4]),
                    "quotientLine": quotient_line,
                    "quotientPolynomialSha256": quotient_hash,
                    "quotientT": quotient_t,
                    "r": int(row[1]),
                    "submissionId": str(row[3]),
                }
            )

    unique_by_q = {}
    raw_counts = {}
    for quotient_t, rows in candidates.items():
        raw_counts[str(quotient_t)] = len(rows)
        rows.sort(
            key=lambda row: (
                row["blockSystemCount"] != 1,
                row["coefficientBytes"],
                len(row["fieldDiscAbs"] or "9" * 100000),
                row["fieldDiscAbs"] or "9" * 100000,
                row["coefficientSha256"],
            )
        )
        unique = {}
        for row in rows:
            unique.setdefault(row["quotientPolynomialSha256"], row)
        # Interleave source-label/submission buckets so the initial bounded
        # exact scan does not repeatedly rediscover one quotient field.
        buckets: dict[tuple[bool, str, str], list[dict]] = defaultdict(list)
        for row in unique.values():
            buckets[
                (
                    row["blockSystemCount"] != 1,
                    row["label"],
                    row["submissionId"],
                )
            ].append(row)
        for values in buckets.values():
            values.sort(
                key=lambda row: (
                    row["blockSystemCount"] != 1,
                    row["coefficientBytes"],
                    row["coefficientSha256"],
                )
            )
        ordered = []
        for ambiguous_system in (False, True):
            keys = sorted(key for key in buckets if key[0] == ambiguous_system)
            while keys:
                next_keys = []
                for key in keys:
                    ordered.append(buckets[key].pop(0))
                    if buckets[key]:
                        next_keys.append(key)
                keys = next_keys
        unique_by_q[quotient_t] = ordered
    return unique_by_q, {
        "acceptedScoreableRowsScanned": scanned,
        "compatibleCandidateRowsByQuotient": raw_counts,
        "compatibleCandidateRowsTotal": sum(raw_counts.values()),
        "uniqueCompatibleQuotientPolynomialsByQuotient": {
            str(key): len(value) for key, value in sorted(unique_by_q.items())
        },
        "verifiedEvenDegree24Rows": all_even_rows,
        "uniqueVerifiedEvenCoefficientHashes": len(all_even_coefficients),
        "uniqueVerifiedExactQuotientPolynomials": len(all_quotients),
    }


def polynomial_details(
    connection: sqlite3.Connection, submission_id: str, polynomial_index: int
):
    row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.field_disc_abs
        FROM polynomials AS p JOIN verifications AS v
        USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (submission_id, polynomial_index),
    ).fetchone()
    if row is None:
        raise ValueError("missing exact source row")
    quotient = even_quotient(str(row[0]))
    if quotient is None:
        raise ValueError("reused source is not monic even degree 24")
    quotient_line, quotient_hash = quotient
    ring = PolynomialRing(ZZ, "y")
    polynomial = ring([ZZ(value) for value in quotient_line.split(",")])
    field = NumberField(polynomial.change_ring(QQ), "a")
    reduced = PolynomialRing(ZZ, "z")(field.pari_polynomial("z").polredabs())
    reduced_line = ",".join(str(value) for value in reduced)
    return {
        "fieldCanonicalPolynomial": reduced_line,
        "fieldCanonicalSha256": hashlib.sha256(reduced_line.encode()).hexdigest(),
        "quotientFieldDiscAbs": str(abs(ZZ(field.discriminant()))),
        "quotientPolynomialSha256": quotient_hash,
        "source": {
            "coefficientSha256": str(row[1]),
            "fieldDiscAbs": str(row[4]) if row[4] else None,
            "label": str(row[2]),
            "polynomialIndex": polynomial_index,
            "r": int(row[3]),
            "submissionId": submission_id,
        },
    }


def merge_field(fields: dict, record: dict) -> bool:
    key = (int(record["quotientT"]), str(record["fieldCanonicalSha256"]))
    existing = fields.get(key)
    if existing is None:
        record["representatives"] = record.get("representatives", [])
        fields[key] = record
        return True
    for label, cores in record.get("targetNormCores", {}).items():
        merged = set(int(value) for value in existing["targetNormCores"].get(label, []))
        merged.update(int(value) for value in cores)
        existing["targetNormCores"][label] = sorted(merged)
    for representative in record.get("representatives", []):
        identity = (
            representative["source"]["submissionId"],
            int(representative["source"]["polynomialIndex"]),
        )
        old = {
            (value["source"]["submissionId"], int(value["source"]["polynomialIndex"]))
            for value in existing["representatives"]
        }
        if identity not in old:
            existing["representatives"].append(representative)
    return False


def field_record_from_artifact(artifact: Path, reused: bool = False) -> dict:
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    return {
        "alignmentCertificate": payload["alignment"],
        "fieldCanonicalPolynomial": payload["fieldCanonicalPolynomial"],
        "fieldCanonicalSha256": payload["fieldCanonicalSha256"],
        "fieldDiscAbs": payload["source"]["quotientFieldDiscAbs"],
        "quotientT": int(payload["alignment"]["quotientT"]),
        "representatives": [
            {
                "provenance": [
                    {
                        "kind": "gold_c_exact_census_alignment",
                        "path": str(artifact.relative_to(ROOT)),
                        "sha256": sha256_path(artifact),
                    }
                ],
                "quotientPolynomialSha256": payload[
                    "quotientPolynomialSha256"
                ],
                "source": payload["source"],
            }
        ],
        "reusedExactAlignment": reused,
        "targetNormCores": payload["targetNormCores"],
    }


def reusable_exact_fields(
    connection: sqlite3.Connection,
    locked_bank: Path,
    target_labels_by_q: dict[int, set[str]],
):
    source_records: dict[tuple[int, str, int], dict] = {}
    locked_sha = sha256_path(locked_bank)
    for row in load_jsonl(locked_bank):
        quotient_t = int(row["target"]["quotientT12"])
        if quotient_t not in target_labels_by_q:
            continue
        for base in row.get("bases", []):
            key = (quotient_t, str(base["submissionId"]), int(base["polynomialIndex"]))
            record = source_records.setdefault(
                key,
                {
                    "alignment": base["alignment"],
                    "provenance": [],
                    "targetNormCores": {},
                },
            )
            record["provenance"].append(
                {
                    "kind": "sha_locked_bank_inline_alignment",
                    "path": str(locked_bank.relative_to(ROOT)),
                    "sha256": locked_sha,
                }
            )
            for label, cores in base["alignment"]["targetNormCores"].items():
                if label in target_labels_by_q[quotient_t]:
                    record["targetNormCores"][label] = sorted(
                        set(record["targetNormCores"].get(label, []))
                        | {int(value) for value in cores}
                    )

    for name in glob.glob(str(DATA / "*character_alignment_q*_base*.json")):
        path = Path(name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            alignment = payload["alignment"]
            source = payload["source"]
            quotient_t = int(alignment["quotientT"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if quotient_t not in target_labels_by_q:
            continue
        key = (
            quotient_t,
            str(source["submissionId"]),
            int(source["polynomialIndex"]),
        )
        record = source_records.setdefault(
            key,
            {"alignment": alignment, "provenance": [], "targetNormCores": {}},
        )
        record["alignment"] = alignment
        record["provenance"].append(
            {
                "kind": "standalone_exact_alignment",
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
            }
        )
        exact = alignment["labelToUnambiguousSquarefreeNormCores"]
        for label in target_labels_by_q[quotient_t]:
            cores = [int(value) for value in exact.get(label, [])]
            if cores:
                record["targetNormCores"][label] = cores

    fields = {}
    for (quotient_t, submission_id, polynomial_index), record in sorted(
        source_records.items()
    ):
        if not record["targetNormCores"]:
            continue
        details = polynomial_details(connection, submission_id, polynomial_index)
        field_record = {
            "alignmentCertificate": record["alignment"],
            "fieldCanonicalPolynomial": details["fieldCanonicalPolynomial"],
            "fieldCanonicalSha256": details["fieldCanonicalSha256"],
            "fieldDiscAbs": details["quotientFieldDiscAbs"],
            "quotientT": quotient_t,
            "representatives": [
                {
                    "provenance": record["provenance"],
                    "quotientPolynomialSha256": details[
                        "quotientPolynomialSha256"
                    ],
                    "source": details["source"],
                }
            ],
            "reusedExactAlignment": True,
            "targetNormCores": record["targetNormCores"],
        }
        merge_field(fields, field_record)
    return fields, len(source_records)


def execute(task: dict, args, target_labels: list[str]) -> dict:
    output = args.artifact_dir / (
        f"q{task['quotientT']}__{task['submissionId']}__p{task['polynomialIndex']}__"
        f"{task['quotientPolynomialSha256'][:12]}.json"
    )
    command = [
        "sage",
        "-python",
        str(WORKER),
        "--db",
        str(args.db),
        "--submission-id",
        task["submissionId"],
        "--polynomial-index",
        str(task["polynomialIndex"]),
        "--quotient-t",
        str(task["quotientT"]),
        "--output",
        str(output),
    ]
    for label in target_labels:
        command.extend(["--target-label", label])
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            **{key: value for key, value in task.items() if key != "quotientLine"},
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "status": "timeout",
            "stderr": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
    stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    parsed = None
    if stdout_lines:
        try:
            parsed = json.loads(stdout_lines[-1])
        except json.JSONDecodeError:
            parsed = None
    if parsed is None:
        parsed = {"status": "worker_error"}
    return {
        **{key: value for key, value in task.items() if key != "quotientLine"},
        **parsed,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "returnCode": completed.returncode,
        "stderr": completed.stderr[-1000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--locked-bank", type=Path, default=DEFAULT_LOCKED_BANK)
    parser.add_argument("--shallow-results", type=Path, default=DEFAULT_SHALLOW_RESULTS)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument(
        "--current-rank11",
        action="store_true",
        help="use every current no-baseline rank-11 12x2 target pair",
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--target-fields", type=int, default=216)
    parser.add_argument("--minimum-fields-per-quotient", type=int, default=8)
    parser.add_argument("--maximum-new-alignments", type=int, default=1200)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 14:
        raise ValueError("the exact census requires between one and fourteen workers")
    if not args.resume and (
        args.results.exists() or args.census.exists() or args.summary.exists()
    ):
        raise ValueError("refusing to overwrite an existing gold-c census run")
    if args.resume and (args.census.exists() or args.summary.exists()):
        raise ValueError("refusing to resume a census that already has a final summary")

    started = time.monotonic()
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        if args.current_rank11:
            (
                live_pairs,
                shallow_audit,
                targets,
                target_labels_by_q,
            ) = current_rank11_campaign(connection, args.structures)
            if not live_pairs:
                raise ValueError("current rank-11 campaign has no live gold pairs")
        else:
            live_pairs, shallow_audit = current_gold_pairs(
                connection, args.shallow_results
            )
            if len(live_pairs) != 51:
                raise ValueError(f"expected 51 current shallow-absent gold pairs, got {len(live_pairs)}")
            targets, target_labels_by_q = target_metadata(args.audit, live_pairs)
        quotient_ts = set(target_labels_by_q)
        _labels_by_q, label_to_qs, system_counts = compatible_source_labels(
            args.action_map, quotient_ts
        )
        candidates_by_q, inventory = exhaustive_candidate_census(
            connection, label_to_qs, system_counts
        )
        fields, reused_sources = reusable_exact_fields(
            connection, args.locked_bank, target_labels_by_q
        )
    finally:
        connection.close()

    resumed_rows = []
    if args.resume and args.results.exists():
        resumed_rows = load_jsonl(args.results)
    completed_quotient_hashes: dict[int, set[str]] = defaultdict(set)
    for row in resumed_rows:
        quotient_hash = row.get("quotientPolynomialSha256")
        if quotient_hash:
            completed_quotient_hashes[int(row["quotientT"])].add(
                str(quotient_hash)
            )
        if row.get("status") == "aligned_target" and row.get("artifact"):
            artifact = Path(row["artifact"])
            if artifact.exists():
                merge_field(fields, field_record_from_artifact(artifact, reused=True))

    # Recover fully written worker artifacts whose supervisor result append was
    # interrupted.  The artifact itself contains the exact status and source
    # identity, so recording it here is deterministic and avoids recomputation.
    if args.resume and args.artifact_dir.exists():
        for artifact in sorted(args.artifact_dir.glob("*.json")):
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                quotient_t = int(payload["alignment"]["quotientT"])
                quotient_hash = str(payload["quotientPolynomialSha256"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if quotient_hash in completed_quotient_hashes[quotient_t]:
                continue
            status = str(payload.get("status", "worker_error"))
            recovered = {
                "alignedTargetLabels": sorted(payload.get("targetNormCores", {})),
                "artifact": str(artifact.resolve()),
                "artifactSha256": sha256_path(artifact),
                "fieldCanonicalSha256": payload.get("fieldCanonicalSha256"),
                "fieldDiscAbs": payload.get("source", {}).get("quotientFieldDiscAbs"),
                "newCanonicalField": False,
                "polynomialIndex": int(payload["source"]["polynomialIndex"]),
                "quotientPolynomialSha256": quotient_hash,
                "quotientT": quotient_t,
                "recoveredInterruptedArtifact": True,
                "returnCode": 0 if status == "aligned_target" else 2,
                "sourceLabel": str(payload["source"]["label"]),
                "status": status,
                "stderr": "",
                "submissionId": str(payload["source"]["submissionId"]),
            }
            if status == "aligned_target":
                recovered["newCanonicalField"] = merge_field(
                    fields, field_record_from_artifact(artifact, reused=True)
                )
            recovered["uniqueCanonicalFieldsAfter"] = len(fields)
            append_jsonl(args.results, recovered)
            resumed_rows.append(recovered)
            completed_quotient_hashes[quotient_t].add(quotient_hash)

    prior_quotient_hashes: dict[int, set[str]] = defaultdict(set)
    for record in fields.values():
        for representative in record["representatives"]:
            prior_quotient_hashes[int(record["quotientT"])].add(
                representative["quotientPolynomialSha256"]
            )
    for quotient_t, hashes in completed_quotient_hashes.items():
        prior_quotient_hashes[quotient_t].update(hashes)
    pending_by_q = {}
    for quotient_t in sorted(quotient_ts):
        pending_by_q[quotient_t] = [
            row
            for row in candidates_by_q.get(quotient_t, [])
            if row["quotientPolynomialSha256"] not in prior_quotient_hashes[quotient_t]
        ]

    launched = 0
    completed_count = 0
    resumed_count = len(resumed_rows)
    aligned_count = sum(
        row.get("status") == "aligned_target" for row in resumed_rows
    )
    duplicate_fields = sum(
        row.get("status") == "aligned_target"
        and not row.get("newCanonicalField", False)
        for row in resumed_rows
    )
    indexes = {quotient_t: 0 for quotient_t in quotient_ts}
    active = {}
    active_by_q = Counter()

    def field_counts():
        counts = Counter(int(key[0]) for key in fields)
        return {quotient_t: counts[quotient_t] for quotient_t in sorted(quotient_ts)}

    def label_coverage():
        covered = set()
        for record in fields.values():
            covered.update(record["targetNormCores"])
        return covered

    def target_reached():
        counts = field_counts()
        return (
            len(fields) >= args.target_fields
            and all(
                counts[quotient_t] >= args.minimum_fields_per_quotient
                for quotient_t in quotient_ts
            )
            and label_coverage()
            >= {label for labels in target_labels_by_q.values() for label in labels}
        )

    def choose_quotient():
        counts = field_counts()
        available = [
            quotient_t
            for quotient_t in quotient_ts
            if indexes[quotient_t] < len(pending_by_q[quotient_t])
        ]
        if not available:
            return None
        below_minimum = [
            quotient_t
            for quotient_t in available
            if counts[quotient_t] + active_by_q[quotient_t]
            < args.minimum_fields_per_quotient
        ]
        pool = below_minimum or available
        return min(
            pool,
            key=lambda quotient_t: (
                counts[quotient_t] + active_by_q[quotient_t],
                indexes[quotient_t] / max(1, len(pending_by_q[quotient_t])),
                quotient_t,
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        while active or (
            launched < args.maximum_new_alignments and not target_reached()
        ):
            while (
                len(active) < args.workers
                and launched < args.maximum_new_alignments
                and not target_reached()
            ):
                quotient_t = choose_quotient()
                if quotient_t is None:
                    break
                task = pending_by_q[quotient_t][indexes[quotient_t]]
                indexes[quotient_t] += 1
                launched += 1
                active_by_q[quotient_t] += 1
                future = pool.submit(
                    execute,
                    task,
                    args,
                    sorted(target_labels_by_q[quotient_t]),
                )
                active[future] = task
            if not active:
                break
            done, _pending = concurrent.futures.wait(
                active, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                task = active.pop(future)
                active_by_q[int(task["quotientT"])] -= 1
                row = future.result()
                completed_count += 1
                is_new = False
                if row.get("status") == "aligned_target" and row.get("artifact"):
                    aligned_count += 1
                    artifact = Path(row["artifact"])
                    record = field_record_from_artifact(artifact)
                    is_new = merge_field(fields, record)
                    duplicate_fields += int(not is_new)
                row["newCanonicalField"] = is_new
                row["uniqueCanonicalFieldsAfter"] = len(fields)
                append_jsonl(args.results, row)
                if completed_count % 12 == 0 or is_new:
                    print(
                        json.dumps(
                            {
                                "aligned": aligned_count,
                                "completed": resumed_count + completed_count,
                                "event": "exact_census_progress",
                                "launched": launched,
                                "newField": is_new,
                                "quotientT": task["quotientT"],
                                "status": row.get("status"),
                                "uniqueFields": len(fields),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )

    census_rows = sorted(
        fields.values(),
        key=lambda row: (int(row["quotientT"]), row["fieldCanonicalSha256"]),
    )
    census_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in census_rows
    )
    write_atomic(args.census, census_text)
    counts = field_counts()
    coverage = label_coverage()
    all_target_labels = {
        label for labels in target_labels_by_q.values() for label in labels
    }
    if target_reached():
        status = "target_field_census_reached"
    elif launched >= args.maximum_new_alignments:
        status = "bounded_alignment_limit"
    else:
        status = "candidate_exhaustion"
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256_path(args.action_map),
        "alignmentStatusCounts": dict(
            sorted(
                Counter(
                    json.loads(line).get("status", "unknown")
                    for line in args.results.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ).items()
            )
        ),
        "allTargetLabelsCovered": coverage >= all_target_labels,
        "canonicalFieldCountsByQuotient": {
            str(key): value for key, value in sorted(counts.items())
        },
        "canonicalQuotientFields": len(fields),
        "census": str(args.census.resolve()),
        "censusSha256": hashlib.sha256(census_text.encode()).hexdigest(),
        "completedAlignmentsTotal": resumed_count + completed_count,
        "completedNewAlignments": completed_count,
        "database": str(args.db.resolve()),
        "databaseLogicalCounts": {},
        "duplicateCanonicalFields": duplicate_fields,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "exactAlignedSourcesTotal": aligned_count,
        "inventory": inventory,
        "launchedNewAlignments": launched,
        "liveGoldTargets": targets,
        "liveGoldTargetCount": len(targets),
        "minimumFieldsPerQuotient": args.minimum_fields_per_quotient,
        "networkCalls": 0,
        "quotientTs": sorted(quotient_ts),
        "results": str(args.results.resolve()),
        "resultsSha256": sha256_path(args.results),
        "resumedAlignmentResults": resumed_count,
        "reusedExactSourceAlignments": reused_sources,
        "shallowAudit": shallow_audit,
        "status": status,
        "submissionCalls": 0,
        "targetFieldCount": args.target_fields,
        "targetLabels": sorted(all_target_labels),
        "targetLabelsCovered": sorted(coverage & all_target_labels),
        "workers": args.workers,
    }
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        summary["databaseLogicalCounts"] = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("submissions", "polynomials", "verifications", "targets")
        }
    finally:
        connection.close()
    write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "allTargetLabelsCovered",
                    "canonicalFieldCountsByQuotient",
                    "canonicalQuotientFields",
                    "completedNewAlignments",
                    "elapsedSeconds",
                    "exactAlignedSourcesTotal",
                    "status",
                )
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if status == "target_field_census_reached" else 2


if __name__ == "__main__":
    raise SystemExit(main())

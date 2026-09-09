#!/usr/bin/env python3
"""Build a bounded disjoint pilot from newly exact-aligned census bases.

Only source alignments produced by the gold-c census are eligible.  Commands
are deduplicated against the immutable Cross-1500 bank and all existing
character-search outputs, and the selection first maximizes source-base and
live-pair diversity.  Every command remains in the direct character-kernel
stage and is revalidated by ``character_kernel_gold_pilot.sage.py``.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_LOCKED_BANK = DATA / "agent_gold_a_cross1500_character_bank.jsonl"
DEFAULT_ALIGNMENT_RESULTS = (
    DATA / "agent_gold_c_character_census_alignment_results.jsonl"
)
DEFAULT_CENSUS = DATA / "agent_gold_c_character_field_census.jsonl"
DEFAULT_CENSUS_SUMMARY = DATA / "agent_gold_c_character_census_summary.json"
DEFAULT_SHALLOW_RESULTS = DATA / "agent_gold_a_cross1500_results"
DEFAULT_BANK = DATA / "agent_gold_c_character_census_pilot_bank.jsonl"
DEFAULT_SUMMARY = DATA / "agent_gold_c_character_census_pilot_bank_summary.json"
DEFAULT_OUTPUTS = DATA / "agent_gold_c_character_census_pilot_outputs"
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


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_command_identity(command: list[str]) -> tuple | None:
    def option(name: str):
        try:
            return command[command.index(name) + 1]
        except (ValueError, IndexError):
            return None

    label = option("--target-label")
    target_r = option("--target-r")
    submission_id = option("--submission-id")
    polynomial_index = option("--polynomial-index")
    norm_core = option("--norm-core")
    if None in (label, target_r, submission_id, polynomial_index, norm_core):
        return None
    auxiliary = option("--aux-primes")
    return (
        str(label),
        int(target_r),
        str(submission_id),
        int(polynomial_index),
        int(norm_core),
        tuple(int(value) for value in auxiliary.split(",") if value)
        if auxiliary
        else (),
    )


def locked_identities(path: Path) -> set[tuple]:
    identities = set()
    for row in load_jsonl(path):
        for attempt in row.get("attempts", []):
            identity = parse_command_identity(
                [str(value) for value in attempt["command"]]
            )
            if identity is not None:
                identities.add(identity)
    return identities


def output_identities() -> tuple[set[tuple], list[dict]]:
    identities = set()
    provenance = []
    for name in glob.glob(str(DATA / "**" / "*.json"), recursive=True):
        path = Path(name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        audit = payload.get("audit")
        search = payload.get("search")
        if not isinstance(audit, dict) or not isinstance(search, dict):
            continue
        source = audit.get("source")
        live = audit.get("liveTarget")
        if not isinstance(source, dict) or not isinstance(live, dict):
            continue
        pair = str(live.get("pair", ""))
        if "/r" not in pair:
            continue
        core_primes = search.get("corePrimes", [])
        auxiliary = search.get("auxiliaryPrimes", [])
        if not isinstance(core_primes, list) or not isinstance(auxiliary, list):
            continue
        try:
            label, r_text = pair.rsplit("/r", 1)
            identity = (
                label,
                int(r_text),
                str(source["submissionId"]),
                int(source["polynomialIndex"]),
                int(math.prod(int(value) for value in core_primes)),
                tuple(int(value) for value in auxiliary),
            )
        except (KeyError, TypeError, ValueError):
            continue
        identities.add(identity)
        provenance.append(
            {
                "identity": [*identity[:5], list(identity[5])],
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
            }
        )
    return identities, provenance


def shallow_absent_pairs(
    connection: sqlite3.Connection, shallow_results: Path
) -> list[tuple[str, int]]:
    absent = set()
    for path in shallow_results.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["search"]["status"] != "target_signature_absent_from_s_unit_space":
            continue
        label, r_text = str(payload["audit"]["liveTarget"]["pair"]).split(
            "/r", 1
        )
        absent.add((label, int(r_text)))
    live = []
    for pair in sorted(absent):
        target = connection.execute(
            "SELECT team_count FROM targets WHERE label=? AND r=?", pair
        ).fetchone()
        baseline = connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=? LIMIT 1", pair
        ).fetchone()
        owned = connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
            pair,
        ).fetchone()
        if (
            pair != EXCLUDED_PAIR
            and target is not None
            and int(target[0]) == 0
            and baseline is None
            and owned is None
        ):
            live.append(pair)
    return live


def current_snapshot(
    connection: sqlite3.Connection, pair: tuple[str, int]
) -> dict:
    target = connection.execute(
        "SELECT team_count,minimum_disc_abs,generated_at FROM targets "
        "WHERE label=? AND r=?",
        pair,
    ).fetchone()
    return {
        "generatedAt": str(target[2]) if target and target[2] else None,
        "minimumDiscAbs": str(target[1]) if target and target[1] else None,
        "teamCount": int(target[0]) if target else None,
    }


def load_new_alignments(results: Path) -> list[dict]:
    records = {}
    for row in load_jsonl(results):
        if row.get("status") != "aligned_target" or not row.get("artifact"):
            continue
        artifact = Path(row["artifact"])
        if not artifact.exists():
            continue
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        source = payload["source"]
        key = (str(source["submissionId"]), int(source["polynomialIndex"]))
        records[key] = {
            "alignment": payload["alignment"],
            "alignmentArtifact": str(artifact.relative_to(ROOT)),
            "alignmentArtifactSha256": sha256_path(artifact),
            "fieldCanonicalSha256": payload["fieldCanonicalSha256"],
            "quotientPolynomialSha256": payload["quotientPolynomialSha256"],
            "quotientT": int(payload["alignment"]["quotientT"]),
            "source": source,
            "targetNormCores": {
                label: [int(value) for value in cores]
                for label, cores in payload["targetNormCores"].items()
            },
        }
    return sorted(
        records.values(),
        key=lambda row: (
            row["quotientT"],
            row["fieldCanonicalSha256"],
            row["source"]["coefficientSha256"],
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--locked-bank", type=Path, default=DEFAULT_LOCKED_BANK)
    parser.add_argument(
        "--alignment-results", type=Path, default=DEFAULT_ALIGNMENT_RESULTS
    )
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--census-summary", type=Path, default=DEFAULT_CENSUS_SUMMARY)
    parser.add_argument("--shallow-results", type=Path, default=DEFAULT_SHALLOW_RESULTS)
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--outputs", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument(
        "--exclude-bank",
        action="append",
        type=Path,
        default=[],
        help="prior pilot bank whose sources, fields, pairs, and commands are excluded/prioritized",
    )
    args = parser.parse_args()
    if args.bank.exists() or args.summary.exists():
        raise ValueError("refusing to overwrite an existing gold-c pilot bank")
    if not args.census.exists() or not args.census_summary.exists():
        raise ValueError("the exact census must finish before the pilot bank is built")

    new_alignments = load_new_alignments(args.alignment_results)
    if not new_alignments:
        raise ValueError("no newly exact-aligned census sources are available")
    prior_locked = locked_identities(args.locked_bank)
    prior_outputs, output_provenance = output_identities()
    prior = prior_locked | prior_outputs
    excluded_sources = set()
    excluded_fields = set()
    prior_pilot_pairs = set()
    for bank_path in args.exclude_bank:
        for row in load_jsonl(bank_path):
            pair = (str(row["target"]["label"]), int(row["target"]["r"]))
            prior_pilot_pairs.add(pair)
            for attempt in row.get("attempts", []):
                identity = attempt.get("identity")
                if identity:
                    excluded_sources.add((str(identity[2]), int(identity[3])))
                    prior.add(
                        (
                            str(identity[0]), int(identity[1]), str(identity[2]),
                            int(identity[3]), int(identity[4]),
                            tuple(int(value) for value in identity[5]),
                        )
                    )
                if attempt.get("fieldCanonicalSha256"):
                    excluded_fields.add(str(attempt["fieldCanonicalSha256"]))
    new_alignments = [
        row for row in new_alignments
        if (str(row["source"]["submissionId"]), int(row["source"]["polynomialIndex"]))
        not in excluded_sources
    ]

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        live_pairs = shallow_absent_pairs(connection, args.shallow_results)
        if len(live_pairs) != 51:
            raise ValueError(f"expected 51 current shallow-absent gold pairs, got {len(live_pairs)}")
        live_pairs.sort(key=lambda pair: (pair in prior_pilot_pairs, pair))
        snapshots = {pair: current_snapshot(connection, pair) for pair in live_pairs}
    finally:
        connection.close()

    options = []
    excluded = []
    for alignment in new_alignments:
        source = alignment["source"]
        for pair in live_pairs:
            label, target_r = pair
            cores = alignment["targetNormCores"].get(label, [])
            for core in cores:
                identity = (
                    label,
                    target_r,
                    str(source["submissionId"]),
                    int(source["polynomialIndex"]),
                    int(core),
                    (),
                )
                if identity in prior:
                    excluded.append(
                        {
                            "identity": [*identity[:5], []],
                            "reason": (
                                "already_in_locked_bank"
                                if identity in prior_locked
                                else "existing_result_identity"
                            ),
                        }
                    )
                    continue
                options.append(
                    {
                        "alignment": alignment,
                        "identity": identity,
                        "pair": pair,
                    }
                )

    # Phase 1: cover every reachable live pair once, preferring unused source
    # bases and canonical fields.
    selected = []
    selected_identities = set()
    used_pairs = Counter()
    used_sources = Counter()
    used_fields = Counter()

    def select(option):
        identity = option["identity"]
        if identity in selected_identities:
            return
        selected.append(option)
        selected_identities.add(identity)
        used_pairs[option["pair"]] += 1
        source = option["alignment"]["source"]
        used_sources[(source["submissionId"], int(source["polynomialIndex"]))] += 1
        used_fields[option["alignment"]["fieldCanonicalSha256"]] += 1

    for pair in live_pairs:
        if len(selected) >= args.limit:
            break
        pool = [option for option in options if option["pair"] == pair]
        if not pool:
            continue
        best = min(
            pool,
            key=lambda option: (
                used_sources[
                    (
                        option["alignment"]["source"]["submissionId"],
                        int(option["alignment"]["source"]["polynomialIndex"]),
                    )
                ],
                used_fields[option["alignment"]["fieldCanonicalSha256"]],
                option["identity"][4],
            ),
        )
        select(best)

    # Phase 2: give every newly aligned source one pilot command where room
    # remains, choosing its least-used reachable pair.
    by_source: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for option in options:
        source = option["alignment"]["source"]
        by_source[(source["submissionId"], int(source["polynomialIndex"]))].append(
            option
        )
    for source_key in sorted(by_source):
        if len(selected) >= args.limit:
            break
        if used_sources[source_key]:
            continue
        best = min(
            by_source[source_key],
            key=lambda option: (
                used_pairs[option["pair"]],
                used_fields[option["alignment"]["fieldCanonicalSha256"]],
                option["identity"][4],
                option["pair"],
            ),
        )
        select(best)

    # Phase 3: bounded extra source/pair combinations, always no-auxiliary.
    while len(selected) < args.limit:
        pool = [
            option
            for option in options
            if option["identity"] not in selected_identities
        ]
        if not pool:
            break
        best = min(
            pool,
            key=lambda option: (
                used_pairs[option["pair"]],
                used_sources[
                    (
                        option["alignment"]["source"]["submissionId"],
                        int(option["alignment"]["source"]["polynomialIndex"]),
                    )
                ],
                used_fields[option["alignment"]["fieldCanonicalSha256"]],
                option["identity"][4],
                option["pair"],
            ),
        )
        select(best)

    rows_by_pair = {}
    for option in selected:
        alignment = option["alignment"]
        source = alignment["source"]
        label, target_r = option["pair"]
        core = int(option["identity"][4])
        identity_text = "|".join(str(value) for value in option["identity"][:5])
        identity_text += "|aux="
        command_id = hashlib.sha256(identity_text.encode()).hexdigest()
        output = args.outputs.resolve() / (
            f"{label}_r{target_r}__q{alignment['quotientT']}__"
            f"{source['coefficientSha256'][:12]}__core{core}__"
            f"{command_id[:10]}.json"
        )
        seed = 1 + int(command_id[:8], 16) % 2_000_000_000
        command = [
            "sage",
            "-python",
            "character_kernel_gold_pilot.sage.py",
            "--db",
            str(args.db.resolve()),
            "--submission-id",
            str(source["submissionId"]),
            "--polynomial-index",
            str(source["polynomialIndex"]),
            "--target-label",
            label,
            "--target-r",
            str(target_r),
            "--norm-core",
            str(core),
            "--max-candidates",
            "64",
            "--witness-primes",
            "1000",
            "--seed",
            str(seed),
            "--output",
            str(output.relative_to(ROOT)),
        ]
        row = rows_by_pair.setdefault(
            option["pair"],
            {
                "attempts": [],
                "logicalTaskId": f"{label}_r{target_r}",
                "status": "executable_new_census_base_pilot",
                "target": {
                    "generatedAt": snapshots[option["pair"]]["generatedAt"],
                    "label": label,
                    "minimumDiscAbs": snapshots[option["pair"]]["minimumDiscAbs"],
                    "r": target_r,
                    "teamCount": snapshots[option["pair"]]["teamCount"],
                },
            },
        )
        row["attempts"].append(
            {
                "alignmentArtifact": alignment["alignmentArtifact"],
                "alignmentArtifactSha256": alignment[
                    "alignmentArtifactSha256"
                ],
                "auxiliaryPrimes": [],
                "command": command,
                "commandId": command_id,
                "fieldCanonicalSha256": alignment["fieldCanonicalSha256"],
                "identity": [*option["identity"][:5], []],
                "output": str(output.resolve()),
                "quotientPolynomialSha256": alignment[
                    "quotientPolynomialSha256"
                ],
                "sourceCoefficientSha256": source["coefficientSha256"],
                "targetNormCore": core,
                "targetNormCoreCertificate": {
                    "allUnambiguousCores": alignment["targetNormCores"][label],
                    "label": label,
                    "quotientT": alignment["quotientT"],
                },
            }
        )

    bank_rows = [rows_by_pair[pair] for pair in sorted(rows_by_pair)]
    bank_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in bank_rows
    )
    write_atomic(args.bank, bank_text)
    summary = {
        "alignmentResults": str(args.alignment_results.resolve()),
        "alignmentResultsSha256": sha256_path(args.alignment_results),
        "bank": str(args.bank.resolve()),
        "bankSha256": hashlib.sha256(bank_text.encode()).hexdigest(),
        "census": str(args.census.resolve()),
        "censusSha256": sha256_path(args.census),
        "censusSummary": str(args.census_summary.resolve()),
        "censusSummarySha256": sha256_path(args.census_summary),
        "commandCount": len(selected),
        "distinctCanonicalFields": len(
            {option["alignment"]["fieldCanonicalSha256"] for option in selected}
        ),
        "distinctNewSources": len(
            {
                (
                    option["alignment"]["source"]["submissionId"],
                    int(option["alignment"]["source"]["polynomialIndex"]),
                )
                for option in selected
            }
        ),
        "excludedExistingCommandIdentities": len(excluded),
        "existingResultArtifactsAudited": len(output_provenance),
        "liveGoldPairsInPilot": len(rows_by_pair),
        "liveGoldShallowAbsentPairs": len(live_pairs),
        "networkCalls": 0,
        "newExactAlignmentSources": len(new_alignments),
        "requestedLimit": args.limit,
        "submissionCalls": 0,
        "pairsWithNoNewExactBase": [
            f"{label}/r{target_r}"
            for label, target_r in live_pairs
            if not any(option["pair"] == (label, target_r) for option in options)
        ],
        "unselectedReachablePairsAtLimit": [
            f"{label}/r{target_r}"
            for label, target_r in live_pairs
            if (label, target_r) not in rows_by_pair
            and any(option["pair"] == (label, target_r) for option in options)
        ],
    }
    write_atomic(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

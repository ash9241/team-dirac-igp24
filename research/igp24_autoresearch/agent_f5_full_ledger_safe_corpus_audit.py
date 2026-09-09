#!/usr/bin/env python3
"""Audit the safe source corpus for the reopened F5 pair-product lane.

This audit is intentionally independent of the generated F5 action/route shard
artifacts.  It derives the admissible source labels directly from the raw
degree-24 block-system map and derives source-polynomial coverage directly from
the verification ledger.  A label is called safe here only when the standard
24T representative has exactly one enumerated two-block system.  In
particular, equality of coarse block profiles is not treated as proof that two
different block systems give the same unordered-pair action.

The audit is structural and offline.  It makes no network or submission calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
DEFAULT_GOLD = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_OUTPUT = DATA / "agent_f5_full_ledger_safe_corpus_audit.json"


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def profile_key(system: dict, source_order: int) -> tuple:
    block_order = int(system["blockActionOrder"])
    target_order = int(system["targetOrder"])
    if source_order % block_order or target_order % block_order:
        return ()
    return (
        int(system["blockActionT12"]),
        source_order // block_order,
        bool(system["flipInSource"]),
        str(system["targetLabel"]),
        target_order // block_order,
    )


def quotient_if_even(coefficients: str) -> str | None:
    values = coefficients.split(",")
    if (
        len(values) != 25
        or values[-1] != "1"
        or any(int(values[index]) != 0 for index in range(1, 25, 2))
    ):
        return None
    return ",".join(values[::2])


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--action-map", type=Path, default=DEFAULT_ACTION_MAP)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--comparison-cap", type=int, default=3)
    args = parser.parse_args()
    if args.comparison_cap <= 0:
        raise ValueError("comparison cap must be positive")

    action_rows = load_jsonl(args.action_map)
    safe_labels = set()
    coarse_profile_multi_labels = set()
    ambiguous_profile_labels = set()
    invalid_system_labels = set()
    system_count_histogram = Counter()
    for row in action_rows:
        label = str(row["sourceLabel"])
        systems = list(row["systems"])
        system_count_histogram[len(systems)] += 1
        keys = {
            profile_key(system, int(row["sourceOrder"]))
            for system in systems
            if profile_key(system, int(row["sourceOrder"]))
        }
        if not keys:
            invalid_system_labels.add(label)
        elif len(systems) == 1 and len(keys) == 1:
            safe_labels.add(label)
        elif len(keys) == 1:
            coarse_profile_multi_labels.add(label)
        else:
            ambiguous_profile_labels.add(label)

    accepted_scoreable_rows = 0
    all_even_rows = 0
    category_even_rows = Counter()
    category_quotients: dict[str, dict[tuple[str, int], set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        cursor = connection.execute(
            """
            SELECT v.label,v.r,p.coefficients
            FROM polynomials AS p JOIN verifications AS v
            USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
            """
        )
        for label_value, signature_value, coefficients_value in cursor:
            accepted_scoreable_rows += 1
            quotient = quotient_if_even(str(coefficients_value))
            if quotient is None:
                continue
            all_even_rows += 1
            label = str(label_value)
            signature = int(signature_value)
            if label in safe_labels:
                category = "single_block_system_safe"
            elif label in coarse_profile_multi_labels:
                category = "coarse_profile_only_unsafe_multi_system"
            elif label in ambiguous_profile_labels:
                category = "ambiguous_profile_excluded"
            else:
                category = "no_valid_action_map_profile"
            category_even_rows[category] += 1
            category_quotients[category][(label, signature)].add(quotient)
    finally:
        connection.close()

    categories = {}
    for category in sorted(category_quotients):
        by_label_signature = category_quotients[category]
        distinct = sum(len(values) for values in by_label_signature.values())
        retained = sum(min(args.comparison_cap, len(values)) for values in by_label_signature.values())
        categories[category] = {
            "distinctLabelSignatures": len(by_label_signature),
            "distinctLabels": len({label for label, _ in by_label_signature}),
            "distinctQuotientPolynomials": distinct,
            "evenRows": int(category_even_rows[category]),
            "omittedByComparisonCap": distinct - retained,
            "retainedByComparisonCap": retained,
            "retentionPercent": 0.0 if not distinct else round(100.0 * retained / distinct, 9),
        }

    gold_rows = load_jsonl(args.gold)
    db_stat = args.db.stat()
    result = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapSha256": sha256_path(args.action_map),
        "actionMapSourceLabels": len(action_rows),
        "acceptedScoreableLedgerRows": accepted_scoreable_rows,
        "allVerifiedEvenRows": all_even_rows,
        "categories": categories,
        "comparisonCapPerLabelSignature": args.comparison_cap,
        "coverageConclusion": (
            "Only single-block-system labels are safe without an additional block-system "
            "assignment certificate.  A cap on quotient representatives is a bounded pilot, "
            "not a complete-ledger exhaustion."
        ),
        "frozenGold": str(args.gold.resolve()),
        "frozenGoldPairs": len(gold_rows),
        "frozenGoldSha256": sha256_path(args.gold),
        "labelClassification": {
            "ambiguousProfileExcluded": len(ambiguous_profile_labels),
            "coarseProfileOnlyUnsafeMultiSystem": len(coarse_profile_multi_labels),
            "invalidSystemLabels": len(invalid_system_labels),
            "singleBlockSystemSafe": len(safe_labels),
            "systemCountHistogram": {
                str(key): value for key, value in sorted(system_count_histogram.items())
            },
        },
        "ledger": {
            "modifiedTimeNanoseconds": db_stat.st_mtime_ns,
            "path": str(args.db.resolve()),
            "sizeBytes": db_stat.st_size,
        },
        "mechanism": "F5 full-ledger safe-corpus audit v1",
        "networkCalls": 0,
        "rootCensusActionOrRouteArtifactsUsed": False,
        "submissionCalls": 0,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_write(args.output, rendered)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "outputSha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "safe": categories.get("single_block_system_safe", {}),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

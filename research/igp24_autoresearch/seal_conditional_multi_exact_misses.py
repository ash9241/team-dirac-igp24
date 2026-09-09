#!/usr/bin/env python3
"""Seal exact conditional MULTI resolutions whose current live yield is zero."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import seal_uncertified_multi_zero_frontier as frontier
import stage_frobenius_gold as stage


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATABASE = DATA / "ledger.sqlite3"
OUTPUT = DATA / "conditional_multi_exact_misses_v2_20260722_certificate.json"
SUMMARY = DATA / "conditional_multi_exact_misses_v2_20260722_summary.json"
METHOD = "conditional-multi-exact-zero-live-closure-v2"

CERTIFICATES = (
    "conditional_multi_3235_r0_frobenius_certificate.json",
    "conditional_multi_12906_r8_frobenius_certificate.json",
    "conditional_multi_12947_r4_frobenius_certificate.json",
    "conditional_multi_14803_r16_frobenius_certificate.json",
    "conditional_multi_14981_r4_frobenius_certificate.json",
    "conditional_multi_15152_r8_frobenius_certificate.json",
    "conditional_multi_16867_r16_frobenius_certificate.json",
    "conditional_multi_18582_r16_frobenius_certificate.json",
)


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display_path(path: Path) -> str:
    return str(path.expanduser().resolve().relative_to(ROOT))


def build_certificate(database: Path = DATABASE) -> dict:
    rows = []
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        target_snapshot = connection.execute(
            "SELECT COUNT(*),MAX(generated_at) FROM targets"
        ).fetchone()
        for name in CERTIFICATES:
            certificate_path = DATA / name
            certificate = stage.read_json(certificate_path)
            input_path = Path(str(certificate["input"])).expanduser().resolve()
            candidates = stage.read_jsonl(input_path)
            joined = stage.join_resolved_assignments(
                certificate, candidates, input_path, allow_unresolved=False
            )
            certificate_rows = certificate.get("rows") or []
            if len(certificate_rows) != 1 or certificate_rows[0].get("status") != "resolved":
                raise ValueError(f"{name} is not a single exact resolution")
            source = stage.source_key(certificate_rows[0])
            frontier.source_provenance(connection, source)
            selected, skips = stage.filter_live_gold(
                joined, database, include_shared=True
            )
            if selected:
                raise ValueError(f"{name} now has {len(selected)} live rows")
            runbook_path = DATA / name.replace(
                "_frobenius_certificate.json", "_runbook.json"
            )
            runbook = json.loads(runbook_path.read_text(encoding="utf-8"))
            manifest_path = ROOT / str(runbook["outputs"]["manifest"])
            if manifest_path.exists():
                raise ValueError(f"zero-branch manifest unexpectedly exists: {manifest_path}")
            rows.append(
                {
                    "certificate": display_path(certificate_path),
                    "certificateSha256": sha256_path(certificate_path),
                    "joinedAssignments": len(joined),
                    "manifestAbsent": True,
                    "runbook": display_path(runbook_path),
                    "runbookSha256": sha256_path(runbook_path),
                    "skipCounts": dict(sorted(skips.items())),
                    "sourceLabel": source[2],
                    "sourcePolynomialIndex": source[1],
                    "sourceR": source[3],
                    "sourceSubmissionId": source[0],
                    "status": "exact_resolved_zero_current_live_yield",
                }
            )
    rows.sort(key=lambda row: (stage.label_t(row["sourceLabel"]), row["sourceR"]))
    result = {
        "checks": {
            "allCertificatesExactAndResolved": True,
            "allCoefficientHashesRejoinedToOriginalCandidates": True,
            "allManifestsAbsent": True,
            "allSourcesAcceptedAndScoreable": True,
            "certificateContainsNoCoefficientPayload": True,
            "currentGoldAndSharedSelectionEmpty": True,
        },
        "database": {
            "path": display_path(database),
            "sha256": sha256_path(database),
        },
        "method": METHOD,
        "networkCalls": 0,
        "rootSignatureInference": {
            "distinctGeometryFallbackMisses": 1,
            "duplicateLabelExceptionalRootMisses": 6,
            "duplicateLabelExceptionalRootTrials": 6,
            "observation": (
                "In all six exact duplicate-label packets with two equal candidate "
                "real-root counts and one exceptional count, the exceptional-root "
                "factor received the singleton target label. Every optimistic live "
                "branch required the repeated target label at that exceptional root "
                "count, so all six were structurally collision-covered. A seventh "
                "repeated-root packet with three distinct labels likewise placed its "
                "exceptional-root factor on the blocked branch. The all-distinct-root "
                "24T3235 fallback also resolved to its blocked anchored swap."
            ),
            "scope": (
                "This invalidates a uniform-permutation yield estimate for the sealed "
                "repeated-root subtype; it is not a probabilistic claim about unrelated "
                "packet geometries."
            ),
            "targetedPositiveBranchesRealized": 0,
            "trials": 8
        },
        "rows": rows,
        "status": "eight_exact_conditional_misses_sealed",
        "submissionCalls": 0,
        "summary": {
            "exactMisses": len(rows),
            "liveRows": 0,
            "manifestsWritten": 0,
        },
        "targetSnapshot": {
            "generatedAtMaximum": str(target_snapshot[1]),
            "pairs": int(target_snapshot[0]),
        },
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if "coefficientLine" in payload or "coefficients" in payload:
        raise ValueError("coefficient payload leaked into miss certificate")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    args = parser.parse_args()
    try:
        certificate = build_certificate(args.database.expanduser().resolve())
        payload = frontier.json_bytes(certificate)
        summary = {
            "certificate": display_path(args.output),
            "certificateSha256": hashlib.sha256(payload).hexdigest(),
            "exactMisses": certificate["summary"]["exactMisses"],
            "method": METHOD,
            "status": certificate["status"],
            "submissionCalls": 0,
        }
        frontier.write_new(args.output, payload)
        frontier.write_new(args.summary, frontier.json_bytes(summary))
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

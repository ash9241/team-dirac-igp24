#!/usr/bin/env python3
"""Seal conservative receipt-pair coverage for the unresolved-safe manifest."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import stage_all_exact_frobenius_unowned as shared


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "outbox/autopilot_unresolved_frobenius_safe_20260722.txt"
SUMMARY = ROOT / "data/autopilot_unresolved_frobenius_safe_20260722_summary.json"
OUTPUT = ROOT / "data/autopilot_unresolved_frobenius_safe_20260722_receipt_mapping.json"

EXPECTED_MANIFEST_SHA256 = "fc1943c4f67452d4208b06dfb91aae652f8f0ee90f932cce4af4f00b276f6a24"
EXPECTED_ROWS = (
    (
        "705bb0461f33134b3610ec79530d0b5063ecacf6b3c49e32ea81f414842063da",
        ("24T6452/r0",),
    ),
    (
        "2c3265a71c4eb4301fd8b88e7deed0e1cbc41c46529279e44187d4b6afab477f",
        ("24T5670/r0", "24T5676/r0"),
    ),
    (
        "2be11b7fa5493d683f001909754715360c54d179ed736a58291685f1b664c30d",
        ("24T6221/r0",),
    ),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def seal(
    manifest: Path = MANIFEST,
    summary: Path = SUMMARY,
    output: Path = OUTPUT,
) -> dict:
    manifest = manifest.expanduser().resolve()
    summary = summary.expanduser().resolve()
    output = output.expanduser().resolve()
    manifest_sha = shared.sha256_path(manifest)
    if manifest_sha != EXPECTED_MANIFEST_SHA256:
        raise ValueError("unresolved-safe manifest SHA-256 mismatch")
    lines = [
        line
        for raw in manifest.read_text(encoding="ascii").splitlines()
        if (line := shared.canonical_line(raw)) is not None
    ]
    line_hashes = [sha256_bytes(line.encode("ascii")) for line in lines]
    expected_hashes = [row[0] for row in EXPECTED_ROWS]
    if line_hashes != expected_hashes:
        raise ValueError("unresolved-safe manifest row order/hash mismatch")
    saved_summary = json.loads(summary.read_text(encoding="utf-8"))
    if (
        saved_summary.get("manifestSha256") != manifest_sha
        or int(saved_summary.get("polynomials", -1)) != len(EXPECTED_ROWS)
    ):
        raise ValueError("unresolved-safe summary does not pin the manifest")

    value = {
        "artifactSha256": {
            "manifest": manifest_sha,
            "summary": shared.sha256_path(summary),
        },
        "checks": {
            "allManifestPolynomialHashesMapped": True,
            "allPossiblePairsConservativelyExcludedUntilLedgerSync": True,
            "outputContainsNoCoefficientPayload": True,
        },
        "manifest": shared.display_path(manifest),
        "method": "all-compatible-receipt-possible-pair-map-v1",
        "networkCalls": 0,
        "polynomials": len(EXPECTED_ROWS),
        "receiptPolynomialPossiblePairs": [
            {
                "coefficientSha256": digest,
                "possiblePairs": list(possible_pairs),
            }
            for digest, possible_pairs in EXPECTED_ROWS
        ],
        "submissionCalls": 0,
    }
    payload = shared.json_bytes(value)
    statuses = shared.preflight_and_seal({output: payload})
    return {
        "output": shared.display_path(output),
        "outputSha256": sha256_bytes(payload),
        "status": statuses[str(output)],
    }


def main() -> int:
    try:
        result = seal()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

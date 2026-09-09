#!/usr/bin/env python3
"""Stage every certified, submission-ready score700 archive row not yet sent."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def canonical(raw: str) -> str:
    return ",".join(str(int(value)) for value in raw.split(","))


def receipt_hashes(receipts: Path) -> set[str]:
    hashes: set[str] = set()
    for path in receipts.glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_file():
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != str(receipt["manifestHash"]):
                continue
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    line = canonical(line)
                    hashes.add(hashlib.sha256(line.encode("ascii")).hexdigest())
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT.parent / "routeA" / "data" / "score700_campaign.jsonl",
    )
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.output, args.certificate):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    finally:
        connection.close()
    known.update(receipt_hashes(args.receipts))

    selected: list[str] = []
    evidence: list[dict] = []
    seen: set[str] = set()
    skipped = {
        "notCertifiedExact": 0,
        "notSubmissionReady": 0,
        "hashMismatch": 0,
        "known": 0,
        "duplicateArchive": 0,
    }
    for line_number, raw in enumerate(args.archive.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if row.get("submission_ready") is not True:
            skipped["notSubmissionReady"] += 1
            continue
        if row.get("exact_compatibility_proven") is not True:
            skipped["notCertifiedExact"] += 1
            continue
        line = canonical(str(row["coefficients"]))
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if digest != str(row.get("candidate_hash")):
            skipped["hashMismatch"] += 1
            continue
        if digest in seen:
            skipped["duplicateArchive"] += 1
            continue
        seen.add(digest)
        if digest in known:
            skipped["known"] += 1
            continue
        selected.append(line)
        evidence.append(
            {
                "archiveLine": line_number,
                "candidateHash": digest,
                "constructionOvergroup": row.get("construction_overgroup"),
                "exactCompatibilityProven": True,
                "localRootCount": row.get("local_root_count"),
                "offlineTargetT": row.get("target_t"),
                "submissionReady": True,
            }
        )

    if not selected:
        raise ValueError("no unseen certified rows remain")
    rendered = "".join(line + "\n" for line in selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.certificate.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    certificate = {
        "archive": str(args.archive.resolve()),
        "archiveSha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "checks": {
            "allCandidateHashesMatched": skipped["hashMismatch"] == 0,
            "allSelectedExactCompatibilityProven": True,
            "allSelectedSubmissionReady": True,
            "excludedLedgerAndReceiptHashes": True,
        },
        "manifest": str(args.output.resolve()),
        "manifestRows": len(selected),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "selected": evidence,
        "skipped": skipped,
    }
    args.certificate.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: certificate[key] for key in ("manifest", "manifestRows", "manifestSha256", "skipped")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

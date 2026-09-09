#!/usr/bin/env python3
"""Stage one unseen certified F5 result for each currently unowned target pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
OUT = ROOT / "outbox" / "current_f5_archive_delta_20260813.txt"
CERT = DATA / "current_f5_archive_delta_20260813_certificate.json"


def receipt_hashes() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "receipts").glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text())
            manifest = Path(receipt["manifest"])
            if not manifest.is_file():
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]:
                continue
            for raw in manifest.read_text().splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    canonical = ",".join(str(int(v)) for v in line.split(","))
                    found.add(hashlib.sha256(canonical.encode("ascii")).hexdigest())
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag-contains", default="")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--certificate", type=Path, default=CERT)
    args = parser.parse_args()
    output = args.output.resolve()
    certificate_path = args.certificate.resolve()
    if output.exists() or certificate_path.exists():
        raise FileExistsError("refusing to overwrite staged F5 artifacts")
    candidates: dict[str, dict] = {}
    for path in sorted(DATA.glob("current_f5_hybrid_value_*group*_20260813.json")):
        if args.tag_contains and args.tag_contains not in path.name:
            continue
        payload = json.loads(path.read_text())
        for candidate in payload.get("candidates", []):
            digest = candidate["coefficientSha256"]
            candidates.setdefault(digest, {
                "coefficientLine": candidate["coefficientLine"],
                "coefficientSha256": digest,
                "label": candidate["targetLabel"],
                "r": int(candidate["r"]),
                "resultArtifact": str(path.relative_to(ROOT)),
                "actionSha256": payload["actionSha256"],
                "sourceCanonicalQuotientSha256": payload["sourceCanonicalQuotientSha256"],
            })
    pending = receipt_hashes()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    selected: dict[tuple[str, int], dict] = {}
    skipped = {"knownLedger": 0, "knownReceipt": 0, "notCurrentTarget": 0, "ownedPair": 0, "duplicatePair": 0}
    try:
        owned = set(connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE status='accepted' AND label IS NOT NULL AND r IS NOT NULL"
        ))
        targets = {(label, int(r)): int(team_count) for label, r, team_count in connection.execute(
            "SELECT label,r,team_count FROM targets"
        )}
        for digest, row in candidates.items():
            pair = (row["label"], row["r"])
            if digest in pending:
                skipped["knownReceipt"] += 1
            elif connection.execute("SELECT 1 FROM polynomials WHERE coefficient_hash=?", (digest,)).fetchone():
                skipped["knownLedger"] += 1
            elif pair not in targets:
                skipped["notCurrentTarget"] += 1
            elif pair in owned:
                skipped["ownedPair"] += 1
            elif pair in selected:
                skipped["duplicatePair"] += 1
            else:
                row["currentTeamCount"] = targets[pair]
                selected[pair] = row
    finally:
        connection.close()
    ordered = sorted(selected.values(), key=lambda row: (row["currentTeamCount"], row["label"], row["r"]))
    rendered = "".join(row["coefficientLine"] + "\n" for row in ordered)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="ascii")
    certificate = {
        "schemaVersion": 1,
        "method": "exact F5 hybrid-value action outputs; one unseen row per current unowned pair",
        "manifest": str(output.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "rows": len(ordered),
        "pairs": len(ordered),
        "projectedMarginalScore": sum(2.0 ** (-row["currentTeamCount"]) for row in ordered),
        "teamCountDistribution": {str(k): sum(row["currentTeamCount"] == k for row in ordered) for k in sorted({row["currentTeamCount"] for row in ordered})},
        "skipped": skipped,
        "entries": ordered,
    }
    certificate_path.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: certificate[k] for k in ("rows", "pairs", "projectedMarginalScore", "teamCountDistribution", "manifestSha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

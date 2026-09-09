#!/usr/bin/env python3
"""Stage every fresh, current, proof-complete non-baseline archive pair."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from build_global_certified_novelty_portfolio_20260813 import (
    canonical,
    certified,
    digest,
    height,
    receipt_hashes,
)


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    if args.tag and not args.tag.replace("-", "").replace("_", "").isalnum():
        raise ValueError("tag must contain only letters, digits, underscores, or hyphens")
    suffix = f"_{args.tag}" if args.tag else ""
    out = ROOT / "outbox" / f"universal_exact_archive_delta{suffix}_20260813.txt"
    cert = ROOT / "data" / f"universal_exact_archive_delta{suffix}_20260813_certificate.json"
    if out.exists() or cert.exists():
        raise FileExistsError("refusing to overwrite universal exact delta")
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    targets = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute("SELECT * FROM targets")
    }
    owned = {
        (str(label), int(r))
        for label, r in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    baseline = {
        (str(label), int(r))
        for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    known = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    known |= receipt_hashes(ROOT / "receipts")
    connection.close()

    files = sorted(
        set(glob.glob(str(ROOT / "data" / "**" / "*.jsonl"), recursive=True))
        | set(glob.glob(str(ROOT.parent / "routeA" / "data" / "**" / "*.jsonl"), recursive=True))
    )
    by_pair: dict[tuple[str, int], list[dict]] = {}
    skips = Counter()
    for raw_path in files:
        path = Path(raw_path)
        if not path.is_file():
            continue
        if any(token in path.name.lower() for token in ("lmfdb", "baseline", "degree24_exact")):
            skips["frozenBaselineOrCatalog"] += 1
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or not certified(row):
                    continue
                exact = row.get("exact_compatibility_proven") is True or row.get("submission_ready") is True
                try:
                    exact = exact or float(row.get("label_probability", 0.0) or 0.0) >= 1.0
                except (TypeError, ValueError):
                    pass
                if not exact:
                    continue
                line = canonical(row)
                if line is None:
                    skips["notCanonicalDegree24"] += 1
                    continue
                candidate_hash = digest(line)
                claimed_hash = row.get("candidate_hash") or row.get("coefficientSha256")
                if claimed_hash and str(claimed_hash) != candidate_hash:
                    skips["hashMismatch"] += 1
                    continue
                try:
                    t = int(row.get("target_t", row.get("parameters", {}).get("target_t")))
                    r = int(row.get("target_r", row.get("local_root_count")))
                except (TypeError, ValueError):
                    skips["missingExactPair"] += 1
                    continue
                pair = (f"24T{t}", r)
                if pair not in targets:
                    skips["absentFromCurrentTargets"] += 1
                    continue
                if pair in owned:
                    skips["ownedPair"] += 1
                    continue
                if pair in baseline:
                    skips["baselinePair"] += 1
                    continue
                if candidate_hash in known:
                    skips["knownOrReceiptedPolynomial"] += 1
                    continue
                normalized = {
                    "label": pair[0],
                    "r": pair[1],
                    "currentTeamCount": int(targets[pair]["team_count"]),
                    "coefficientLine": line,
                    "coefficientSha256": candidate_hash,
                    "source": str(path.relative_to(ROOT.parent)),
                    "family": str(row.get("recipe_family") or row.get("construction_overgroup") or path.stem),
                }
                by_pair.setdefault(pair, []).append(normalized)

    selected = []
    for pair, rows in by_pair.items():
        rows.sort(key=lambda item: (height(item["coefficientLine"]), item["coefficientSha256"]))
        selected.append(rows[0] | {"availableRowsForPair": len(rows)})
    selected.sort(key=lambda item: (item["currentTeamCount"], item["r"], int(item["label"][3:])))
    rendered = "".join(item["coefficientLine"] + "\n" for item in selected)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="ascii")
    certificate = {
        "schemaVersion": 1,
        "method": "universal exact non-baseline archive delta against current targets, ownership, ledger, and receipts",
        "filesScanned": len(files),
        "manifest": str(out.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "rows": len(selected),
        "pairs": len(selected),
        "projectedMarginalScore": sum(2.0 ** (-item["currentTeamCount"]) for item in selected),
        "teamCountDistribution": dict(sorted(Counter(item["currentTeamCount"] for item in selected).items())),
        "skips": dict(sorted(skips.items())),
        "entries": selected,
    }
    cert.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: certificate[key] for key in ("rows", "pairs", "projectedMarginalScore", "teamCountDistribution", "manifestSha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

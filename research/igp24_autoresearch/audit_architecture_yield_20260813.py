#!/usr/bin/env python3
"""Summarize classified yield of the unclassified architecture candidate bank."""

from __future__ import annotations

import glob
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT.parent / "routeA" / "data"
DB = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "architecture_classified_yield_20260813.json"


def canonical(value: object) -> str | None:
    try:
        values = [int(item) for item in (value if isinstance(value, list) else str(value).split(","))]
    except (TypeError, ValueError):
        return None
    if len(values) != 25 or values[-1] != 1:
        return None
    return ",".join(map(str, values))


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUTPUT}")
    patterns = (
        "cross2000_empirical_*.jsonl",
        "cross2000_tower_*.jsonl",
        "cross1500_primitive_masked_tower_*.jsonl",
        "cross1500_masked_tower_*.jsonl",
        "cross1500_quartic_root_lift_*.jsonl",
        "cross1500_q12t61_focus_*.jsonl",
        "cross1500_s3_cubic_*.jsonl",
        "gq96_*.jsonl",
        "tower_*248.jsonl",
        "candidates_fiber_*.jsonl",
    )
    paths = sorted({Path(item) for p in patterns for item in glob.glob(str(ARCHIVE / p))})
    candidates: dict[str, dict] = {}
    for path in paths:
        for raw in path.open(encoding="utf-8", errors="replace"):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            line = canonical(row.get("coefficients"))
            if line is None:
                continue
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            params = row.get("parameters") if isinstance(row.get("parameters"), dict) else {}
            candidates.setdefault(
                digest,
                {
                    "family": str(row.get("recipe_family") or row.get("construction_overgroup") or path.stem),
                    "form": str(params.get("form", "")),
                    "root": int(row.get("local_root_count", row.get("target_r", -1))),
                },
            )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    targets = {
        (str(row["label"]), int(row["r"])): int(row["team_count"])
        for row in connection.execute("SELECT label,r,team_count FROM targets")
    }
    hits = []
    hashes = list(candidates)
    for offset in range(0, len(hashes), 800):
        batch = hashes[offset : offset + 800]
        marks = ",".join("?" for _ in batch)
        for row in connection.execute(
            "SELECT p.coefficient_hash,v.label,v.r,v.scoreable "
            "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
            f"WHERE p.coefficient_hash IN ({marks}) AND v.status='accepted' AND v.label IS NOT NULL",
            batch,
        ):
            meta = candidates[str(row["coefficient_hash"])]
            label, root = str(row["label"]), int(row["r"])
            hits.append(meta | {"label": label, "r": root, "teamCount": targets.get((label, root))})
    connection.close()

    def summarize(key_fields: tuple[str, ...]) -> list[dict]:
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for hit in hits:
            grouped[tuple(hit[key] for key in key_fields)].append(hit)
        output = []
        for key, rows in grouped.items():
            pairs = Counter((row["label"], row["r"], row["teamCount"]) for row in rows)
            output.append(
                {
                    **dict(zip(key_fields, key)),
                    "acceptedOccurrences": len(rows),
                    "distinctLabels": len({row["label"] for row in rows}),
                    "distinctPairs": len(pairs),
                    "minimumTeamCount": min((row["teamCount"] for row in rows if row["teamCount"] is not None), default=None),
                    "pairs": [
                        {"label": pair[0], "r": pair[1], "teamCount": pair[2], "occurrences": count}
                        for pair, count in sorted(pairs.items(), key=lambda item: ((999 if item[0][2] is None else item[0][2]), item[0][0], item[0][1]))
                    ],
                }
            )
        return sorted(output, key=lambda row: (row["minimumTeamCount"] if row["minimumTeamCount"] is not None else 999, -row["distinctLabels"], *(str(row[k]) for k in key_fields)))

    report = {
        "filesScanned": len(paths),
        "candidateHashes": len(candidates),
        "classifiedOccurrences": len(hits),
        "byFamily": summarize(("family",)),
        "byFamilyForm": summarize(("family", "form")),
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT.relative_to(ROOT)), **{k: report[k] for k in ("filesScanned", "candidateHashes", "classifiedOccurrences")}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

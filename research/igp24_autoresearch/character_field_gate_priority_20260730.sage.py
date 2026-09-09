#!/usr/bin/env sage -python
"""Configured exact character gates for ranked field-ready clusters."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_field_gate_q138_20260730.sage.py"
PRIORITY_INVENTORY = (
    ROOT / "data" / "character_fullrank_tr_priority_inventory_20260730.json"
)
CONFIGS = {
    41: {
        "canonicalFields": 2,
        "live": {
            "24T18026": [4, 8, 12, 16, 20, 24],
        },
        "sourceLabels": {
            "24T18026",
            "24T18027",
            "24T18028",
        },
        "targetLabels": ["24T18026"],
    },
    70: {
        "canonicalFields": 1,
        "live": {
            "24T19171": [4, 20, 24],
            "24T19172": [4, 20, 24],
        },
        "sourceLabels": {
            "24T19171",
            "24T19172",
            "24T19173",
        },
        "targetLabels": ["24T19171", "24T19172"],
    },
    71: {
        "canonicalFields": 1,
        "live": {
            "24T19174": [4, 12, 20, 24],
        },
        "sourceLabels": {
            "24T19174",
            "24T19175",
        },
        "targetLabels": ["24T19174"],
    },
    116: {
        "canonicalFields": 4,
        "live": {
            "24T20311": [2, 6, 10],
        },
        "sourceLabels": {
            "24T20309",
            "24T20310",
            "24T20311",
            "24T20312",
        },
        "targetLabels": ["24T20311"],
    },
    136: {
        "canonicalFields": 14,
        "live": {
            "24T20753": [4, 12, 20],
            "24T20757": [20, 24],
        },
        "sourceLabels": {
            "24T20752",
            "24T20753",
            "24T20754",
            "24T20755",
            "24T20756",
            "24T20757",
        },
        "targetLabels": ["24T20753", "24T20757"],
    },
    153: {
        "canonicalFields": 2,
        "live": {
            "24T20825": [24],
            "24T20827": [24],
        },
        "sourceLabels": {
            "24T20825",
            "24T20827",
            "24T20828",
        },
        "targetLabels": ["24T20825", "24T20827"],
    },
    155: {
        "canonicalFields": 7,
        "live": {
            "24T20834": [20, 24],
        },
        "sourceLabels": {
            "24T20833",
            "24T20834",
            "24T20835",
            "24T20836",
        },
        "targetLabels": ["24T20834"],
    },
    193: {
        "canonicalFields": 9,
        "live": {
            "24T21913": [14, 18, 20, 24],
        },
        "sourceLabels": {
            "24T21906",
            "24T21908",
            "24T21909",
            "24T21910",
            "24T21911",
            "24T21912",
            "24T21913",
        },
        "targetLabels": ["24T21913"],
    },
    195: {
        "canonicalFields": 3,
        "live": {
            "24T22364": [12, 20, 24],
        },
        "sourceLabels": {
            "24T22361",
            "24T22363",
            "24T22364",
            "24T22365",
        },
        "targetLabels": ["24T22364"],
    },
    213: {
        "canonicalFields": 2,
        "live": {
            "24T22557": [12, 20, 24],
            "24T22559": [20, 24],
        },
        "sourceLabels": {
            "24T22556",
            "24T22557",
            "24T22558",
            "24T22559",
        },
        "targetLabels": ["24T22557", "24T22559"],
    },
    217: {
        "canonicalFields": 2,
        "live": {
            "24T22574": [20, 24],
            "24T22577": [20, 24],
        },
        "sourceLabels": {
            "24T22573",
            "24T22574",
            "24T22575",
            "24T22576",
            "24T22577",
            "24T22578",
        },
        "targetLabels": ["24T22574", "24T22577"],
    },
    161: {
        "canonicalFields": 2,
        "live": {
            "24T21462": [20, 24],
            "24T21463": [20],
        },
        "sourceLabels": {
            "24T21462",
            "24T21463",
            "24T21464",
        },
        "targetLabels": ["24T21462", "24T21463"],
    },
    185: {
        "canonicalFields": 2,
        "live": {
            "24T21879": [24],
            "24T21880": [24],
        },
        "sourceLabels": {
            "24T21878",
            "24T21879",
            "24T21880",
            "24T21881",
            "24T21882",
            "24T21883",
            "24T21884",
            "24T21885",
        },
        "targetLabels": ["24T21879", "24T21880"],
    },
    234: {
        "canonicalFields": 1,
        "live": {
            "24T23121": [4, 8, 12, 16, 20, 24],
        },
        "sourceLabels": {
            "24T23120",
            "24T23121",
        },
        "targetLabels": ["24T23121"],
    },
    236: {
        "canonicalFields": 2,
        "live": {
            "24T23199": [12, 16, 24],
        },
        "sourceLabels": {
            "24T23196",
            "24T23197",
            "24T23198",
            "24T23199",
            "24T23200",
            "24T23201",
        },
        "targetLabels": ["24T23199"],
    },
    247: {
        "canonicalFields": 1,
        "live": {
            "24T23301": [24],
            "24T23304": [24],
        },
        "sourceLabels": {
            "24T23301",
            "24T23302",
            "24T23304",
        },
        "targetLabels": ["24T23301", "24T23304"],
    },
    260: {
        "canonicalFields": 29,
        "live": {
            "24T23756": [18],
            "24T23760": [24],
        },
        "sourceLabels": {
            "24T23755",
            "24T23756",
            "24T23757",
            "24T23758",
            "24T23759",
            "24T23760",
            "24T23761",
            "24T23762",
        },
        "targetLabels": ["24T23756", "24T23760"],
    },
}


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "character_gate_priority", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory_config(quotient_t: int) -> dict:
    payload = json.loads(PRIORITY_INVENTORY.read_text(encoding="utf-8"))
    matches = [
        row
        for row in payload["clusters"]
        if int(row["quotientT12"]) == quotient_t
    ]
    if len(matches) != 1:
        raise ValueError(
            f"q{quotient_t} selected {len(matches)} priority clusters"
        )
    cluster = matches[0]
    live = defaultdict(list)
    for pair in cluster["livePairs"]:
        live[str(pair["label"])].append(int(pair["r"]))
    return {
        "canonicalFields": int(
            cluster["canonicalTotallyRealFieldCount"]
        ),
        "live": {
            label: sorted(signatures)
            for label, signatures in sorted(live.items())
        },
        "sourceLabels": set(cluster["sourceStructuralLabels"]),
        "targetLabels": list(cluster["targetLabels"]),
    }


def current_live_subset(config: dict, db: Path) -> dict[str, list[int]]:
    labels = sorted(config["targetLabels"])
    placeholders = ",".join("?" for _label in labels)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT t.label,t.r
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label IN ({placeholders})
              AND t.team_count=0
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.label,t.r
            """,
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()
    live = defaultdict(list)
    for label, r in rows:
        live[str(label)].append(int(r))
    return {
        label: sorted(signatures)
        for label, signatures in sorted(live.items())
    }


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--quotient-t", type=int, required=True)
    parser.add_argument(
        "--ambiguous-target-label",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--use-current-live-subset",
        action="store_true",
        help=(
            "replace the frozen inventory live-pair set with its strict "
            "currently-live subset from the local ledger"
        ),
    )
    known, remaining = parser.parse_known_args()
    quotient_t = known.quotient_t
    derived_config = inventory_config(quotient_t)
    if quotient_t in CONFIGS and CONFIGS[quotient_t] != derived_config:
        raise ValueError(
            f"static/inventory config mismatch for q{quotient_t}"
        )
    config = CONFIGS.get(quotient_t, derived_config)
    ambiguous_target_labels = sorted(
        set(str(label) for label in known.ambiguous_target_label)
    )
    unknown_ambiguous_labels = (
        set(ambiguous_target_labels) - set(config["targetLabels"])
    )
    if unknown_ambiguous_labels:
        raise ValueError(
            "ambiguous target labels are outside this quotient cluster: "
            f"{sorted(unknown_ambiguous_labels)}"
        )
    driver = load_driver()
    driver.DEFAULT_LOCAL_OUTPUT = (
        ROOT
        / "data"
        / f"character_field_gate_q{quotient_t}_local_20260730.json"
    )
    driver.QUOTIENT_T = quotient_t
    driver.FAMILIES = {
        quotient_t: {
            "ambiguousTargetLabels": ambiguous_target_labels,
            "targetLabels": config["targetLabels"],
        }
    }
    driver.EXPECTED_LIVE = (
        current_live_subset(config, ROOT / "data" / "ledger.sqlite3")
        if known.use_current_live_subset
        else config["live"]
    )
    driver.EXPECTED_SOURCE_LABELS = config["sourceLabels"]
    driver.EXPECTED_CANONICAL_FIELDS = config["canonicalFields"]
    driver.BASE.FAMILIES = driver.FAMILIES
    sys.argv = [sys.argv[0], *remaining]
    return int(driver.main())


if __name__ == "__main__":
    raise SystemExit(main())

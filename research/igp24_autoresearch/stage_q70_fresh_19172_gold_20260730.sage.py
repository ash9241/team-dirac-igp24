#!/usr/bin/env sage -python
"""Revalidate and seal the three exact q70 character-lift witnesses."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_q136_q193_character_packet_20260730.sage.py"
LIVE_REFRESH_MIN = "2026-07-30T13:38:14Z"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "q70_fresh_character_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT
    / "data"
    / "character_field_gate_q70_fresh_24T19172_exact_958f8101_coset16_20260730.json":
        "73cba54ac0f5c350e34e0e072597a6abe16217093400223ddab7df00b801465e",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19172", 4):
        "b4d3f289c47553b3990f73189e8a55f63203c24e763023669ac0de67a6de5884",
    ("24T19172", 20):
        "28837a2c39c9ab9cef59ec54639ef507ea6d7963f4586a660bca730ae9ec52dc",
    ("24T19172", 24):
        "94876723abe405172a087605ecc18f31cf5451e3e0de82d9140ea102c6b482e2",
}
STAGER.MANIFEST = ROOT / "outbox" / "q70_fresh_19172_three_gold_20260730.txt"
STAGER.CERTIFICATE = (
    ROOT / "data" / "q70_fresh_19172_three_gold_stage_20260730.json"
)


def validate_fresh_targets(candidates: list[dict]) -> list[dict]:
    database = ROOT / "data" / "ledger.sqlite3"
    receipts = {path.stem for path in (ROOT / "receipts").glob("sub_*.json")}
    connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro",
        uri=True,
    )
    try:
        ledger_submissions = {
            str(row[0])
            for row in connection.execute("SELECT submission_id FROM submissions")
        }
        STAGER.require(
            receipts <= ledger_submissions,
            "one or more local submission receipts are not synchronized",
        )
        rows = []
        for candidate in candidates:
            pair = (candidate["label"], candidate["r"])
            target = connection.execute(
                """
                SELECT team_count,discovered,generated_at
                FROM targets WHERE label=? AND r=?
                """,
                pair,
            ).fetchone()
            STAGER.require(target is not None, f"target pair missing: {pair}")
            STAGER.require(
                target[0:2] == (0, 0),
                f"pair is no longer live tc0: {pair}: {target}",
            )
            STAGER.require(
                str(target[2]) >= LIVE_REFRESH_MIN,
                f"target row predates the exact audit: {pair}: {target[2]}",
            )
            baseline = connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                pair,
            ).fetchone()[0]
            owned = connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND scoreable=1
                """,
                pair,
            ).fetchone()[0]
            known_hash = connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate["sha256"],),
            ).fetchone()[0]
            STAGER.require(baseline == 0, f"baseline pair: {pair}")
            STAGER.require(owned == 0, f"already-owned pair: {pair}")
            STAGER.require(known_hash == 0, f"candidate already submitted: {pair}")
            rows.append(
                {
                    "discovered": False,
                    "generatedAt": str(target[2]),
                    "label": pair[0],
                    "locallyOwned": False,
                    "r": pair[1],
                    "teamCount": 0,
                }
            )
        STAGER.require(
            len({candidate["sha256"] for candidate in candidates})
            == len(candidates),
            "duplicate candidate hashes",
        )
        return rows
    finally:
        connection.close()


STAGER.validate_targets_and_outbox = validate_fresh_targets


if __name__ == "__main__":
    raise SystemExit(STAGER.main())

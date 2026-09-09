#!/usr/bin/env sage -python
"""Revalidate and seal the 13 exact character witnesses still live at 10:50Z."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "stage_q136_q193_character_packet_20260730.sage.py"
LIVE_REFRESH_MIN = "2026-07-30T10:49:53Z"


def load_stager():
    spec = importlib.util.spec_from_file_location(
        "character_live_gold_stager", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGER = load_stager()
STAGER.ARTIFACTS = {
    ROOT / "data" / "character_field_gate_q138_exact_517a67f30ace_20260730.json":
        "9fa9ccbdd6422fd2541d95da0745e2677eb9d8dfae4e55a5ff401e980be2b54d",
    ROOT / "data" / "character_field_gate_q138_exact_6d2504bfd0ae_20260730.json":
        "fe9ea07cbb5febcc53accda212d5c97d8b3de6d806ad3c4aa4399276af67721d",
    ROOT / "data" / "character_field_gate_q138_exact_7ec789b697f3_20260730.json":
        "a16ff4b6a7548eaba8c6fd28b57f0683194c20aa66a2facb124484c285f9ab33",
    ROOT / "data" / "character_field_gate_q136_exact_317c9398_multi16_20260730.json":
        "7f8cc2f215068f4c6fb4de1b9cb97c8d4d4b33bcb2dab4daf6882596947d172a",
    ROOT / "data" / "character_field_gate_q193_exact_57768ed9_coset16_20260730.json":
        "dd12919ec0e8a560431ee333f3ea38e3b1370bb0d08d7e0fe1be38d836e435b5",
    ROOT / "data" / "character_field_gate_q88_exact_feb5a65c_coset16_20260730.json":
        "cf8f53d4e398ae4a636e659bbfcfe5960644ac036bc48b18abc63bf15f93d1cc",
    ROOT / "data" / "character_field_gate_q125_exact_3a92b9fc_coset16_20260730.json":
        "70df3542d84e44a41916bd7f83186fb4efa4a4846961e52e34875f12f380723a",
}
STAGER.EXPECTED_SELECTION = {
    ("24T19669", 24):
        "5c8333f2362901e942043ddc2521a0bdd58b43bb4f44217a76e9e8322d27e6c7",
    ("24T20436", 24):
        "e2d0e017a5fa20d2f3f0c73d8c7561e589ad087bdf17591785396b58d9cfc6dd",
    ("24T20753", 4):
        "377cb7d304125de9e771e624f965227621eae4862c6741b7663843720eed9007",
    ("24T20753", 12):
        "69acab7a27e3fd155d3c6c6370d80a97a7e829cb9173b103e8f16b1da6b618c7",
    ("24T20753", 20):
        "6dd8da1d96906d1149214423afbc40a30ab6f7d7f0d8e9a3e3c7a94f8f17adc6",
    ("24T20767", 4):
        "52d15a01ea2f03ff506c0a54af6ac91069102988cb365c9c3a5472e8fedc85c7",
    ("24T20767", 8):
        "7e662037e0595d60de4cc2c9319eb2b1d4e2ff3d951431b878cb9876e322dfae",
    ("24T20767", 12):
        "a4ff0f27101081d96bdf0f4adcacbb548311dcdc64e20e1028487a776a5aefb5",
    ("24T20767", 16):
        "851227f8530f5252cb39fd0df972da090cd865ab9095e7b1a2b0aaa01f30ccb3",
    ("24T20767", 20):
        "5087111960f88d3873753ccfe7010780b7d657a92b087bfc48a022307fd05354",
    ("24T20767", 24):
        "4d31376424d7b75f4719dafa28395898cebeaa3aeded6d3fbbb3098c2cd8e03c",
    ("24T21913", 20):
        "14497e47fec1c4d5b42e9f63298131ef017baaf3c18d1c6934d7b462e659b669",
    ("24T21913", 24):
        "bcf8f90797d4c23ae8e5c01dab34afcae96cddaaa73312e5d303fcb3b5f130d5",
}
STAGER.MANIFEST = ROOT / "outbox" / "character_live_gold_13_20260730.txt"
STAGER.CERTIFICATE = (
    ROOT / "data" / "character_live_gold_13_stage_20260730.json"
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
                f"target row predates the pinned live refresh: {pair}: {target[2]}",
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

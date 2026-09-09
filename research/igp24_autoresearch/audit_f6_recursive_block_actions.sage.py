#!/usr/bin/env sage -python
"""Seal two-point block-system closure for local F6 sources 10482/14293."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CAMPAIGN = DATA / "campaign_20260727_f627"
CERTIFICATE = CAMPAIGN / "f6_post22_unique_wave_cumulative_certificate.json"
PAIR_CENSUS = CAMPAIGN / "f6_recursive_10482_14293_pair_census.jsonl"
OUTPUT = CAMPAIGN / "f6_recursive_10482_14293_block_action_audit.json"
SOURCES = [("24T10482", 10482, 8), ("24T14293", 14293, 16)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_new(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    twist = runpy.run_path(str(ROOT / "build_even_twist_action_map.sage.py"))
    f5 = runpy.run_path(
        str(ROOT / "agent_f5_full_ledger_pair_product_census.sage.py")
    )
    bundle = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
    lines = {
        (str(hit["targetGate"]["label"]), int(hit["targetGate"]["r"])): str(
            hit["candidateCoefficientLine"]
        )
        for hit in bundle["hits"]
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        gold = {
            (str(label), int(r))
            for label, r, team_count, discovered in connection.execute(
                "SELECT label,r,team_count,discovered FROM targets"
            )
            if int(team_count) == 0
            and int(discovered) == 0
            and (str(label), int(r)) not in baseline
            and (str(label), int(r)) not in owned
        }
    finally:
        connection.close()
    rows = []
    for label, t, source_r in SOURCES:
        line = lines[(label, source_r)]
        coefficients = [int(value) for value in line.split(",")]
        even = all(coefficients[index] == 0 for index in range(1, 25, 2))
        structure = twist["action_row"](label, t)
        systems = []
        structural_actions = []
        for system in structure["systems"]:
            systems.append(system)
            structural_actions.extend(f5["actions_for_system"](t, system))
        routes = []
        for action in structural_actions:
            for target_r in action[
                "sourceSignatureToPossibleTargetSignatures"
            ].get(str(source_r), []):
                if (str(action["targetLabel"]), int(target_r)) in gold:
                    routes.append(
                        {
                            "targetLabel": str(action["targetLabel"]),
                            "targetR": int(target_r),
                            "pairOrbit": action["pairOrbit"],
                        }
                    )
        rows.append(
            {
                "sourceLabel": label,
                "sourceT": t,
                "sourceR": source_r,
                "sourceCoefficientSha256": hashlib.sha256(
                    line.encode("ascii")
                ).hexdigest(),
                "coefficientEven": even,
                "twoPointBlockSystemCount": int(structure["systemCount"]),
                "twoPointBlockSystems": systems,
                "allBlockSystemsFlipAlreadyInSource": all(
                    bool(system["flipInSource"]) for system in systems
                ),
                "structuralSize12QuotientPairActionCount": len(
                    structural_actions
                ),
                "coefficientExecutableActionCount": (
                    len(structural_actions) if even else 0
                ),
                "currentTc0Routes": routes if even else [],
                "nonEvenStructuralActionsExcluded": (
                    len(structural_actions) if not even else 0
                ),
            }
        )
    report = {
        "schemaVersion": "f6-recursive-10482-14293-block-action-audit-v1",
        "method": (
            "exact-centralizer-two-point-block-systems-plus-"
            "size12-quotient-pair-orbit-census"
        ),
        "pairCensusPath": str(PAIR_CENSUS.relative_to(ROOT)),
        "pairCensusSha256": sha256(PAIR_CENSUS),
        "sourceCertificatePath": str(CERTIFICATE.relative_to(ROOT)),
        "sourceCertificateSha256": sha256(CERTIFICATE),
        "rows": rows,
        "totalStructuralBlockActions": sum(
            row["structuralSize12QuotientPairActionCount"] for row in rows
        ),
        "totalCoefficientExecutableBlockActions": sum(
            row["coefficientExecutableActionCount"] for row in rows
        ),
        "totalCurrentTc0Routes": sum(
            len(row["currentTc0Routes"]) for row in rows
        ),
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    atomic_new(OUTPUT, payload)
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "outputSha256": hashlib.sha256(payload).hexdigest(),
                "totalCoefficientExecutableBlockActions": report[
                    "totalCoefficientExecutableBlockActions"
                ],
                "totalCurrentTc0Routes": report["totalCurrentTc0Routes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal the capped five-route F6 multi-orbit Frobenius outcome."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT / "data" / "campaign_20260727_f627"
CANDIDATES = CAMPAIGN / "f6_post22_multi_wave_candidates.jsonl"
OUTPUT = CAMPAIGN / "f6_post22_multi_wave_frobenius_summary.json"
CERTIFICATES = [
    CAMPAIGN / "f6_post22_multi_frob_01_3698_r8.json",
    CAMPAIGN / "f6_post22_multi_frob_02_3721_r8.json",
    CAMPAIGN / "f6_post22_multi_frob_03_3818_r8.json",
    CAMPAIGN / "f6_post22_multi_frob_04_8353_r0.json",
    CAMPAIGN / "f6_post22_multi_frob_05_15342_r16.json",
]


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
    rows = []
    for path in CERTIFICATES:
        certificate = json.loads(path.read_text(encoding="utf-8"))
        if (
            certificate.get("method")
            != "exact-unramified-frobenius-cycle-type-exclusion-v1"
            or int(certificate.get("primeCount", -1)) != 4
            or int(certificate.get("primeBoundExclusive", -1)) != 11
            or certificate.get("summary")
            != {"rows": 1, "resolved": 0, "unresolved": 1, "contradiction": 0}
            or len(certificate.get("rows") or []) != 1
            or certificate["rows"][0].get("status") != "unresolved"
            or certificate["rows"][0].get("assignments") is not None
        ):
            raise ValueError(f"unexpected capped Frobenius result in {path}")
        row = certificate["rows"][0]
        if (
            int(row["marginalProof"]["primesExamined"]) != 4
            or int(row["jointProof"]["primesExamined"]) != 4
        ):
            raise ValueError(f"four-prime stop rule not evidenced by {path}")
        rows.append(
            {
                "certificatePath": str(path.relative_to(ROOT)),
                "certificateSha256": sha256(path),
                "sourceLabel": str(row["sourceLabel"]),
                "sourceR": int(row["sourceR"]),
                "status": "unresolved",
                "marginalPrimesExamined": 4,
                "jointProfilesCheckedAtSamePrimes": 4,
                "remainingSlotAssignmentCount": int(
                    row["remainingSlotAssignmentCount"]
                ),
                "remainingLabelAssignmentCount": len(
                    row["remainingLabelAssignments"]
                ),
            }
        )
    summary = {
        "schemaVersion": "f6-post22-multi-wave-frobenius-summary-v1",
        "status": "capped_all_unresolved",
        "primeCap": [2, 3, 5, 7],
        "routeCount": 5,
        "checkpointCount": len(rows),
        "resolved": 0,
        "unresolved": 5,
        "contradiction": 0,
        "exactCurrentTc0Hits": [],
        "coefficientLinesRetained": 0,
        "candidatePackets": {
            "path": str(CANDIDATES.relative_to(ROOT)),
            "sha256": sha256(CANDIDATES),
            "certifiedMultiPackets": 6,
            "degree24Factors": 18,
            "unusedFallbackPacket": {"sourceLabel": "24T8353", "sourceR": 4},
        },
        "routes": rows,
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    atomic_new(OUTPUT, payload)
    print(
        json.dumps(
            {
                "output": str(OUTPUT.relative_to(ROOT)),
                "outputSha256": hashlib.sha256(payload).hexdigest(),
                "status": summary["status"],
                "exactCurrentTc0Hits": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

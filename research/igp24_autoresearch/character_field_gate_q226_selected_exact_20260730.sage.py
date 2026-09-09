#!/usr/bin/env sage -python
"""Exact Selmer/Frobenius gate for one preselected q226 local survivor.

This driver consumes the fixed local-gate artifact and independent exact
12T226 identification.  It does not enumerate fields or source coefficients.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, QQ, pari, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
LOCAL = DATA / "character_field_gate_q226_allambiguous_local_20260730.json"
LOCAL_SHA256 = (
    "b93f42ef2e705aacf639812228dd2953144cb8f07615ee7d4fbdcef7100fb2e9"
)
EXACT_ID = DATA / "q226_22818r24_selected_exact_id_992a2f44_20260730.json"
EXACT_ID_SHA256 = (
    "f4955bf6a497f20146913fb1a17d6901f7b8325b59e94a195a57265282428696"
)
FIELD_SHA256 = (
    "992a2f4467dcaa0683de8ac8f1eb6efed8b29addea8ab0e39ff0b64fdadd6c56"
)
TARGET_LABEL = "24T22818"
TARGET_R = 24
FAMILIES = {
    226: {
        "ambiguousTargetLabels": [TARGET_LABEL],
        "targetLabels": [TARGET_LABEL],
    }
}


def load_base():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("q226_selected_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character gate base from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAMILIES = FAMILIES
    return module


BASE = load_base()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def strict_live_pair() -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.r,t.team_count,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=?
              AND t.team_count=0
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.r
            """,
            (TARGET_LABEL,),
        ).fetchall()
    finally:
        connection.close()
    if [(str(label), int(r)) for label, r, _tc, _at in rows] != [
        (TARGET_LABEL, TARGET_R)
    ]:
        raise ValueError(f"strict live-pair state changed: {rows}")
    label, r, team_count, generated_at = rows[0]
    return {
        str(label): [
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": (
                    str(generated_at) if generated_at else None
                ),
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
        ]
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--max-reconstructions", type=int, default=64)
    parser.add_argument(
        "--max-coset-reconstructions-per-sign",
        type=int,
        default=64,
    )
    parser.add_argument("--pari-stack-bytes", type=int, default=0)
    parser.add_argument("--pari-stack-max-bytes", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if sha256_path(LOCAL) != LOCAL_SHA256:
        raise ValueError("q226 local artifact byte digest changed")
    if sha256_path(EXACT_ID) != EXACT_ID_SHA256:
        raise ValueError("q226 exact-ID artifact byte digest changed")
    exact_id = json.loads(EXACT_ID.read_text(encoding="utf-8"))
    if (
        exact_id.get("status") != "exact_12T226_certified"
        or exact_id.get("exactQuotientMatch") is not True
        or exact_id.get("fieldCanonicalSha256") != FIELD_SHA256
    ):
        raise ValueError("independent exact q226 identification is absent")

    local = json.loads(LOCAL.read_text(encoding="utf-8"))
    fields = [
        row
        for row in local["fields"]
        if row["fieldCanonicalSha256"] == FIELD_SHA256
    ]
    if len(fields) != 1:
        raise ValueError("selected local field is absent or duplicated")
    source = fields[0]
    if not any(
        pair.get("localPass") is True
        and pair.get("label") == TARGET_LABEL
        and int(pair.get("r", -1)) == TARGET_R
        for pair in source["localPairGates"]
    ):
        raise ValueError("selected q226/r24 route did not pass the local gate")

    proof.number_field(False)
    if args.pari_stack_bytes:
        pari.allocatemem(
            int(args.pari_stack_bytes),
            int(args.pari_stack_max_bytes),
        )
    live = strict_live_pair()
    ring = PolynomialRing(QQ, "y")
    result = BASE.audit_field(
        source,
        ring,
        live,
        False,
        True,
        int(args.witness_primes),
        int(args.max_reconstructions),
        int(args.max_coset_reconstructions_per_sign),
        0,
        1,
    )
    exact_gate = result["exactSelmerSignGate"]
    if exact_gate.get("status") != "skipped_no_local_passers":
        exact_gate["conditionalOnGRH"] = True
    exact_pairs = exact_gate.get("pairs", [])
    exact_routes = [
        pair
        for pair in exact_pairs
        if pair["status"] == "exact_selmer_sign_pass"
    ]
    certified = [
        pair["certifiedExactSelmerReconstruction"]
        for pair in exact_routes
        if pair.get("certifiedExactSelmerReconstruction")
    ]
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "conditionalOnGRH": True,
            "fieldEnumerationCalls": 0,
            "inputExactIdSha256": EXACT_ID_SHA256,
            "inputLocalGateSha256": LOCAL_SHA256,
            "networkCalls": 0,
            "phase": "selected_exact",
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": [
                pair["generatedAt"]
                for pair in live[TARGET_LABEL]
                if pair["generatedAt"]
            ],
            "witnessPrimeCount": int(args.witness_primes),
        },
        "family": FAMILIES[226],
        "fields": [result],
        "livePairs": live,
        "summary": {
            "auditedFields": 1,
            "certifiedCandidates": len(certified),
            "exactPassingPairFieldRoutes": len(exact_routes),
            "localPassingPairFieldRoutes": sum(
                pair["localPass"] for pair in result["localPairGates"]
            ),
        },
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    output = args.output.resolve()
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "certifiedCandidates": len(certified),
                "event": "artifact_written",
                "exactPassingPairFieldRoutes": len(exact_routes),
                "output": str(output),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

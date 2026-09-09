#!/usr/bin/env sage -python
"""Finite audit of the three q210 cores ambiguous between 22544 and 22548.

Each route is first checked for rational norm-valuation and signature parity.
Exact K(S,2) work is run only for local passers.  Any reconstructed candidate
must then have a complete maximal-subgroup Frobenius certificate for 24T22548.

This script is offline and performs no coefficient search, staging, submission,
or network operation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TARGET_LABEL = "24T22548"
AMBIGUOUS_ROUTES = {
    "34eaa879946800fa03b996b115ad50639ce8e4a4ca751e79bfcf0175277ea58f": 5389,
    "d2eef78bf4effdd7f4bf25aeb7553f40b21f2ea8a5a6ed70a8d4715979e1ff0f": 5474,
    "ee1adf66ce8bd877acd946c6b920562634876d47d75f2efe09229a82d5b744ac": 221,
}


def load_gate():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("q156_q210_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import gate from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_gate()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATA / "ledger.sqlite3")
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DATA / "character_tr_field_inventory_20260730.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA / "character_ambiguous_q210_22548_gate_20260730.json",
    )
    parser.add_argument("--witness-primes", type=int, default=1000)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite the ambiguous-core artifact")

    ring = PolynomialRing(QQ, "y")
    live = GATE.live_gold_pairs(args.db, {TARGET_LABEL})[TARGET_LABEL]
    inventory = {
        row["fieldCanonicalSha256"]: row
        for row in GATE.load_totally_real_inventory(args.inventory)
        if int(row["quotientT12"]) == 210
    }
    if set(inventory) != set(AMBIGUOUS_ROUTES):
        raise ValueError("q210 inventory does not match the three audited routes")

    rows = []
    for field_hash, core in AMBIGUOUS_ROUTES.items():
        field_row = inventory[field_hash]
        quotient = ring(
            [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
        )
        alignment = GATE.ALIGNMENT.exact_linear_character_alignment(quotient, 210)
        possible_labels = alignment["coreToPossibleLabels"].get(str(core), [])
        if possible_labels != ["24T22544", "24T22548"]:
            raise ValueError(
                f"unexpected character ambiguity for {field_hash}/{core}: "
                f"{possible_labels}"
            )
        field = NumberField(quotient, "a")
        signature = tuple(int(value) for value in field.signature())
        if signature != (12, 0):
            raise ValueError(f"route field is not totally real: {field_hash}")
        core_gate = GATE.local_core_gate(field, core)

        local_pairs = []
        exact_work = []
        for live_pair in live:
            requested_r = int(live_pair["r"])
            negative_count = 12 - requested_r // 2
            signature_bound = 0 <= negative_count <= 12
            signed_norm_parity = (
                signature_bound
                and ((negative_count & 1) == int(core < 0))
            )
            local_pass = (
                core_gate["rationalNormParityLocallyPossible"]
                and signature_bound
                and signed_norm_parity
            )
            pair = {
                "characterClassificationRequired": True,
                "core": core,
                "label": TARGET_LABEL,
                "localPass": bool(local_pass),
                "negativeRealEmbeddingsRequired": negative_count,
                "possibleLabelsForCore": possible_labels,
                "r": requested_r,
                "realSignatureBoundPass": signature_bound,
                "signedNormParityPass": signed_norm_parity,
                "teamCount": int(live_pair["teamCount"]),
            }
            local_pairs.append(pair)
            if local_pass:
                exact_work.append(pair)

        if exact_work:
            exact = GATE.exact_selmer_sign_gate(
                field,
                quotient,
                {core: exact_work},
                True,
                args.witness_primes,
            )
        else:
            exact = {
                "pairs": [],
                "status": "skipped_no_local_passers",
            }
        rows.append(
            {
                **field_row,
                "ambiguousCore": core,
                "characterAmbiguity": {
                    "possibleLabels": possible_labels,
                    "targetLabel": TARGET_LABEL,
                },
                "localCoreGate": core_gate,
                "localPairGates": local_pairs,
                "exactSelmerAndClassificationGate": exact,
            }
        )
        print(
            json.dumps(
                {
                    "core": core,
                    "event": "ambiguous_route_audited",
                    "fieldCanonicalSha256": field_hash,
                    "localPassers": len(exact_work),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    exact_pairs = [
        pair
        for row in rows
        for pair in row["exactSelmerAndClassificationGate"].get("pairs", [])
    ]
    payload = {
        "audit": {
            "candidateClassificationRule": (
                "A reconstructed candidate advances only with a complete "
                "24T22548 maximal-subgroup Frobenius certificate."
            ),
            "coefficientSearches": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    row["generatedAt"]
                    for row in live
                    if row["generatedAt"]
                }
            ),
        },
        "fields": rows,
        "livePairs": live,
        "summary": {
            "ambiguousRoutesAudited": len(rows),
            "certified22548Candidates": sum(
                bool(
                    (pair.get("firstExactSelmerReconstruction") or {})
                    .get("maximalSubgroupCertificate", {})
                    .get("complete")
                )
                for pair in exact_pairs
            ),
            "exactSelmerPassingRoutes": sum(
                pair.get("status") == "exact_selmer_sign_pass"
                for pair in exact_pairs
            ),
            "localPassingPairFieldRoutes": sum(
                pair["localPass"]
                for row in rows
                for pair in row["localPairGates"]
            ),
        },
        "targetLabel": TARGET_LABEL,
    }
    rendered = rendered_json(payload)
    write_atomic(args.output.resolve(), rendered)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Exact q168 gate restricted to the singleton core-2 live routes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, proof
from sage.rings.number_field import selmer_group


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
LOCAL = DATA / "broad_structural_character_gate_q168_unique_local_20260731.json"
LOCAL_SHA256 = "2a7409128065443eba917609f0d0bb4e5b6c545c54d5d2285d264b4bb923d0af"
FIELD_SHA256 = "fa683e68a842979cf158a5d9671c24350e05aba4dfe4ce3ce8342ab671a0648d"
QUOTIENT_T = 168
TARGET_CORE = 2
TARGET_LABEL = "24T21573"
TARGET_RS = (20, 24)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_atomic(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"refusing to overwrite {temporary}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def strict_live_pairs() -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.r,t.team_count,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=? AND t.team_count=0 AND t.discovered=0
              AND b.label IS NULL AND owned.label IS NULL
            ORDER BY t.r
            """,
            (TARGET_LABEL,),
        ).fetchall()
    finally:
        connection.close()
    actual = [(str(label), int(r)) for label, r, _tc, _at in rows]
    expected = [(TARGET_LABEL, r) for r in TARGET_RS]
    if actual != expected:
        raise ValueError(
            f"strict q168 live-pair state changed: {actual}, expected {expected}"
        )
    return {
        TARGET_LABEL: [
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": str(generated_at) if generated_at else None,
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
            for _label, r, team_count, generated_at in rows
        ]
    }


def selected_source() -> tuple[dict, dict]:
    if sha256_path(LOCAL) != LOCAL_SHA256:
        raise ValueError("q168 local artifact byte digest changed")
    payload = json.loads(LOCAL.read_text(encoding="utf-8"))
    if (
        int(payload.get("quotientT12", -1)) != QUOTIENT_T
        or payload.get("targetLabels") != [TARGET_LABEL]
        or payload.get("audit", {}).get("phase") != "local"
    ):
        raise ValueError("q168 local artifact identity changed")
    fields = [
        row
        for row in payload.get("fields", [])
        if row.get("fieldCanonicalSha256") == FIELD_SHA256
    ]
    if len(fields) != 1:
        raise ValueError("selected q168 field is absent or duplicated")
    source = copy.deepcopy(fields[0])
    if (
        hashlib.sha256(source["canonicalPolynomial"].encode()).hexdigest()
        != FIELD_SHA256
    ):
        raise ValueError("selected q168 canonical polynomial hash changed")
    if (
        source.get("alignment", {})
        .get("coreToPossibleLabels", {})
        .get(str(TARGET_CORE))
        != [TARGET_LABEL]
    ):
        raise ValueError("q168 singleton core alignment changed")
    selected = [
        copy.deepcopy(pair)
        for pair in source.get("localPairGates", [])
        if (
            int(pair.get("core", -1)) == TARGET_CORE
            and pair.get("possibleLabelsForCore") == [TARGET_LABEL]
            and pair.get("finalGroupDisambiguationRequired") is False
            and pair.get("label") == TARGET_LABEL
            and int(pair.get("r", -1)) in TARGET_RS
        )
    ]
    selected.sort(key=lambda pair: int(pair["r"]))
    if [int(pair["r"]) for pair in selected] != list(TARGET_RS):
        raise ValueError("q168 core-2 live route set changed")
    if not all(pair.get("localPass") is True for pair in selected):
        raise ValueError("q168 selected route no longer passes locally")
    source["localCoreGates"] = {
        str(TARGET_CORE): source["localCoreGates"][str(TARGET_CORE)]
    }
    source["localPairGates"] = selected
    source["exactSelmerSignGate"] = {
        "locallyPassingCores": [TARGET_CORE],
        "pairs": [],
        "status": "selected_for_exact",
    }
    source["selection"] = {
        "core": TARGET_CORE,
        "possibleLabelsForCore": [TARGET_LABEL],
        "rValues": list(TARGET_RS),
        "rule": "singleton_character_core_only",
    }
    return source, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--max-reconstructions", type=int, default=16)
    parser.add_argument(
        "--max-coset-reconstructions-per-sign", type=int, default=16
    )
    parser.add_argument("--coset-mask-start", type=int, default=0)
    parser.add_argument("--coset-mask-stride", type=int, default=1)
    parser.add_argument("--coset-mask-list")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    schedule = (
        [
            int(value.strip(), 0)
            for value in args.coset_mask_list.split(",")
            if value.strip()
        ]
        if args.coset_mask_list
        else None
    )

    source, local_payload = selected_source()
    live = strict_live_pairs()
    broad = load_module(
        "q168_selected_broad",
        ROOT / "broad_structural_character_gate_20260730.sage.py",
    )
    compact = load_module(
        "q168_squareclass_compact",
        ROOT / "broad_structural_character_gate_squareclass_compact_20260731.sage.py",
    )
    selmer_group._ideal_generator = compact.squareclass_compact_ideal_generator
    base = broad.load_base(QUOTIENT_T, [TARGET_LABEL])
    ring = PolynomialRing(QQ, "y")
    quotient = ring(
        [ZZ(value) for value in source["canonicalPolynomial"].split(",")]
    )
    quotient_certificate = broad.exact_galois_certificate(
        source, QUOTIENT_T, ring
    )
    proof.number_field(False)
    field = NumberField(quotient, "a")
    print(
        json.dumps(
            {
                "event": "q168_core2_exact_start",
                "fieldCanonicalSha256": FIELD_SHA256,
                "rValues": list(TARGET_RS),
                "selectedCore": TARGET_CORE,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    exact_gate = base.exact_selmer_sign_gate(
        field,
        quotient,
        {TARGET_CORE: source["localPairGates"]},
        True,
        int(args.witness_primes),
        int(args.max_reconstructions),
        int(args.max_coset_reconstructions_per_sign),
        int(args.coset_mask_start),
        int(args.coset_mask_stride),
        schedule,
    )
    exact_pairs = exact_gate.get("pairs", [])
    actual = sorted(
        (int(pair["core"]), str(pair["label"]), int(pair["r"]))
        for pair in exact_pairs
    )
    expected = [(TARGET_CORE, TARGET_LABEL, r) for r in TARGET_RS]
    if actual != expected:
        raise ValueError(f"q168 exact route set changed: {actual}")
    source["exactSelmerSignGate"] = exact_gate
    source["quotientGaloisCertificate"] = quotient_certificate
    routes = broad.certified_routes([source])
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "conditionalOnGRH": True,
            "fieldEnumerationCalls": 0,
            "inputLocalGateSha256": LOCAL_SHA256,
            "networkCalls": 0,
            "phase": "selected_exact_core2",
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    pair["generatedAt"]
                    for pair in live[TARGET_LABEL]
                    if pair["generatedAt"]
                }
            ),
            "witnessPrimeCount": int(args.witness_primes),
        },
        "fields": [source],
        "livePairs": live,
        "quotientT12": QUOTIENT_T,
        "selection": source["selection"],
        "sourceCensusSummary": local_payload.get("sourceCensusSummary"),
        "summary": {
            "auditedFields": 1,
            "certifiedExactRouteCount": len(routes),
            "certifiedExactRoutes": routes,
            "exactSelmerPassingRouteCount": sum(
                pair.get("status") == "exact_selmer_sign_pass"
                for pair in exact_pairs
            ),
            "localPassingPairFieldRoutes": len(source["localPairGates"]),
        },
        "targetLabels": [TARGET_LABEL],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(output),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

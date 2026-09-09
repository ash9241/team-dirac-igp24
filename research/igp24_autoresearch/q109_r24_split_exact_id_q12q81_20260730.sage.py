#!/usr/bin/env sage -python
"""Exact degree-12 action ID for one assigned fresh q109/r24 survivor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, libgap, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CENSUS = (
    DATA
    / "broad_structural_tr_field_census_q109_19727r24_q210freshfields_agent_20260730.json"
)
LOCAL = (
    DATA
    / "broad_structural_character_gate_q109_19727r24_q210freshfields_agent_local_20260730.json"
)
EXPECTED_CENSUS_SHA256 = (
    "d0fcf9185662b7421727bf6dc7ac862d619252fb92428a8110be1309ecc6eb7e"
)
EXPECTED_LOCAL_SHA256 = (
    "12ba2f2ccb92e6327704f86efee204b69c3ce79499e6e9c8e96de6c360e03bb5"
)
ASSIGNED_HASHES = {
    "b202e4df2e1a178249b42641bd97a5df06e98136cb33613b18afa77f5e48cb92",
    "b88909dcad8fd756ad86652cbab0776690f625bf23ff324db68d114145650724",
    "c1fcedd93a6789a71bd4d4fc9f5fb9d02abaa750a12f9f642a6db90581f9d942",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, value: object) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field-hash", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    matches = [
        digest for digest in ASSIGNED_HASHES
        if digest.startswith(args.field_hash)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"field prefix selected {len(matches)} assigned hashes"
        )
    digest = matches[0]
    if sha256_path(CENSUS) != EXPECTED_CENSUS_SHA256:
        raise ValueError("q109 broad census byte digest changed")
    if sha256_path(LOCAL) != EXPECTED_LOCAL_SHA256:
        raise ValueError("q109 local-gate byte digest changed")

    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    local = json.loads(LOCAL.read_text(encoding="utf-8"))
    source = next(
        row for row in census["freshFields"]
        if row["fieldCanonicalSha256"] == digest
    )
    field = next(
        row for row in local["fields"]
        if row["fieldCanonicalSha256"] == digest
    )
    passers = [
        row for row in field["localPairGates"]
        if (
            row.get("localPass") is True
            and row.get("label") == "24T19727"
            and int(row.get("r", -1)) == 24
        )
    ]
    if not passers:
        raise ValueError("assigned field is not a q109/r24 local survivor")

    ring = PolynomialRing(QQ, "x")
    polynomial = ring(
        [ZZ(value) for value in field["canonicalPolynomial"].split(",")]
    )
    group = polynomial.galois_group(algorithm="gap")
    gap_group = libgap(group)
    sage_t = int(group.transitive_number())
    gap_t = int(libgap.TransitiveIdentification(gap_group))
    if sage_t != gap_t:
        raise ArithmeticError("Sage/GAP transitive-ID mismatch")
    certificate = {
        "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
        "degree": int(group.degree()),
        "gapIdGroup": str(libgap.IdGroup(gap_group)),
        "gapTransitiveIdentification": gap_t,
        "generatorsCycleNotation": [
            str(generator) for generator in group.gens()
        ],
        "isTransitive": bool(libgap.IsTransitive(gap_group)),
        "order": int(group.order()),
        "sageTransitiveNumber": sage_t,
        "structureDescription": str(
            libgap.StructureDescription(gap_group)
        ),
        "transitiveLabel": f"12T{gap_t}",
    }
    exact_match = (
        certificate["degree"] == 12
        and certificate["isTransitive"]
        and gap_t == 109
    )
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "inputCensusSha256": EXPECTED_CENSUS_SHA256,
            "inputLocalGateSha256": EXPECTED_LOCAL_SHA256,
            "networkCalls": 0,
            "selmerComputations": 0,
            "submissionCalls": 0,
        },
        "canonicalPolynomial": field["canonicalPolynomial"],
        "exactGaloisAction": certificate,
        "exactQuotientMatch": exact_match,
        "fieldCanonicalSha256": digest,
        "localR24Passers": passers,
        "sourceRows": source["sourceRows"],
        "status": (
            "exact_12T109_certified"
            if exact_match
            else "rejected_wrong_exact_12T_action"
        ),
        "target": "24T19727/r24",
    }
    output = args.output.resolve()
    sha256 = write_atomic(output, payload)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "fieldCanonicalSha256": digest,
                "output": str(output),
                "sha256": sha256,
                "status": payload["status"],
                "transitiveLabel": certificate["transitiveLabel"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    proof.all(True)
    raise SystemExit(main())

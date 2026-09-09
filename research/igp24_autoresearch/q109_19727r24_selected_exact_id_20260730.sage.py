#!/usr/bin/env sage -python
"""Independently identify one of two assigned q109/r24 local survivors."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, libgap, proof


ROOT = Path(__file__).resolve().parent
LOCAL = (
    ROOT
    / "data"
    / "broad_structural_character_gate_q109_19727r24_q210freshfields_agent_local_20260730.json"
)
LOCAL_SHA256 = (
    "12ba2f2ccb92e6327704f86efee204b69c3ce79499e6e9c8e96de6c360e03bb5"
)
ALLOWED_HASHES = {
    "18b29be3651c2d21c6015dca8e0d4f6ff27d3d63f787c23f74cbe6e80e3f23aa",
    "981736366a74f4e548d117e3c8b3e04cb989a2e88a7436fc2fd7d76ebf53f2b4",
}


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field-hash", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sha256_path(LOCAL) != LOCAL_SHA256:
        raise ValueError("q109 local artifact byte digest changed")
    selected = [
        digest
        for digest in sorted(ALLOWED_HASHES)
        if digest.startswith(args.field_hash)
    ]
    if len(selected) != 1:
        raise ValueError(
            f"field prefix selected {len(selected)} assigned hashes"
        )
    digest = selected[0]
    payload = json.loads(LOCAL.read_text(encoding="utf-8"))
    fields = [
        row
        for row in payload["fields"]
        if row["fieldCanonicalSha256"] == digest
    ]
    if len(fields) != 1:
        raise ValueError("assigned field is absent or duplicated")
    field = fields[0]
    r24_passers = [
        row
        for row in field["localPairGates"]
        if (
            row.get("localPass") is True
            and row.get("label") == "24T19727"
            and int(row.get("r", -1)) == 24
        )
    ]
    if not r24_passers:
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
        raise ArithmeticError(
            f"Sage/GAP transitive-ID mismatch: {sage_t} versus {gap_t}"
        )
    exact_match = sage_t == 109
    result = {
        "audit": {
            "coefficientSearches": 0,
            "inputLocalGateSha256": LOCAL_SHA256,
            "networkCalls": 0,
            "selmerComputations": 0,
            "submissionCalls": 0,
        },
        "canonicalPolynomial": field["canonicalPolynomial"],
        "exactGaloisAction": {
            "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
            "degree": int(group.degree()),
            "gapIdGroup": str(libgap.IdGroup(gap_group)),
            "gapTransitiveIdentification": gap_t,
            "isTransitive": bool(libgap.IsTransitive(gap_group)),
            "order": int(group.order()),
            "sageTransitiveNumber": sage_t,
            "structureDescription": str(
                libgap.StructureDescription(gap_group)
            ),
            "transitiveLabel": f"12T{sage_t}",
        },
        "exactQuotientMatch": exact_match,
        "fieldCanonicalSha256": digest,
        "localR24Passers": r24_passers,
        "status": (
            "exact_12T109_certified"
            if exact_match
            else "rejected_wrong_exact_12T_action"
        ),
        "target": "24T19727/r24",
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    write_atomic(args.output.resolve(), rendered)
    print(
        json.dumps(
            {
                "artifact": str(args.output.resolve()),
                "artifactSha256": hashlib.sha256(
                    rendered.encode()
                ).hexdigest(),
                "fieldCanonicalSha256": digest,
                "status": result["status"],
                "transitiveLabel": result[
                    "exactGaloisAction"
                ]["transitiveLabel"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    proof.all(True)
    raise SystemExit(main())

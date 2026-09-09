#!/usr/bin/env sage -python
"""Independently identify the selected q226/r24 local survivor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, libgap, proof
from sage.libs.gap.util import GAPError


ROOT = Path(__file__).resolve().parent
LOCAL = (
    ROOT
    / "data"
    / "character_field_gate_q226_allambiguous_local_20260730.json"
)
LOCAL_SHA256 = (
    "b93f42ef2e705aacf639812228dd2953144cb8f07615ee7d4fbdcef7100fb2e9"
)
FIELD_SHA256 = (
    "992a2f4467dcaa0683de8ac8f1eb6efed8b29addea8ab0e39ff0b64fdadd6c56"
)


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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sha256_path(LOCAL) != LOCAL_SHA256:
        raise ValueError("q226 local artifact byte digest changed")

    payload = json.loads(LOCAL.read_text(encoding="utf-8"))
    fields = [
        row
        for row in payload["fields"]
        if row["fieldCanonicalSha256"] == FIELD_SHA256
    ]
    if len(fields) != 1:
        raise ValueError("selected field is absent or duplicated")
    field = fields[0]
    r24_passers = [
        row
        for row in field["localPairGates"]
        if (
            row.get("localPass") is True
            and row.get("label") == "24T22818"
            and int(row.get("r", -1)) == 24
        )
    ]
    if not r24_passers:
        raise ValueError("selected field is not a q226/r24 local survivor")

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
    try:
        gap_id_group = str(libgap.IdGroup(gap_group))
        gap_id_group_status = "available"
    except GAPError:
        gap_id_group = None
        gap_id_group_status = (
            "unavailable_for_group_order_in_installed_gap_catalog"
        )
    exact_match = sage_t == 226
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
            "gapIdGroup": gap_id_group,
            "gapIdGroupStatus": gap_id_group_status,
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
        "fieldCanonicalSha256": FIELD_SHA256,
        "localR24Passers": r24_passers,
        "status": (
            "exact_12T226_certified"
            if exact_match
            else "rejected_wrong_exact_12T_action"
        ),
        "target": "24T22818/r24",
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
                "fieldCanonicalSha256": FIELD_SHA256,
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

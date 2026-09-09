#!/usr/bin/env sage -python
"""Gate the three broad-census fresh fields for live 24T18026 routes.

The local phase is only the character/norm screen.  Before the exact phase
enters K(S,2), it independently computes the degree-12 polynomial Galois group
with Sage/GAP and requires exact transitive identification 12T41.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from sage.all import ZZ


ROOT = Path(__file__).resolve().parent
BASE_PATH = ROOT / "character_field_gate_q138_20260730.sage.py"
INVENTORY = (
    ROOT
    / "data"
    / "broad_structural_tr_field_census_q41_live_20260730.json"
)


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "character_gate_q41_fresh_24T18026", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()
DRIVER.DEFAULT_LOCAL_OUTPUT = (
    ROOT
    / "data"
    / "character_field_gate_q41_fresh_24T18026_local_20260730.json"
)
DRIVER.QUOTIENT_T = 41
DRIVER.FAMILIES = {
    41: {
        "targetLabels": ["24T18026"],
    }
}
DRIVER.EXPECTED_LIVE = {
    "24T18026": [4, 8, 12, 16, 20, 24],
}
DRIVER.BASE.FAMILIES = DRIVER.FAMILIES


def fresh_fields(_db: Path, _structures: Path, _ring) -> list[dict]:
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    rows = payload["freshFields"]
    if len(rows) != 3:
        raise ValueError(f"expected three fresh q41 claims, found {len(rows)}")
    output = []
    for row in rows:
        if row.get("previouslyAudited"):
            raise ValueError("fresh q41 census contains an audited field")
        if int(row["quotientT12ClaimedByStructuralSource"]) != 41:
            raise ValueError("fresh census contains a non-q41 structural claim")
        output.append(
            {
                "canonicalPolynomial": str(row["canonicalPolynomial"]),
                "fieldCanonicalSha256": str(
                    row["fieldCanonicalSha256"]
                ),
                "family": "12T41",
                "provenance": (
                    "fresh totally-real canonical degree-12 field from the "
                    "broad structural q41 source census; independent exact "
                    "Sage/GAP TransitiveIdentification is mandatory before "
                    "the exact Selmer phase"
                ),
                "quotientT12": 41,
                "sourceRows": list(row["sourceRows"]),
            }
        )
    return sorted(output, key=lambda row: row["fieldCanonicalSha256"])


DRIVER.EXPECTED_CANONICAL_FIELDS = 3
DRIVER.EXPECTED_SOURCE_LABELS = {
    str(source["label"])
    for field in fresh_fields(None, None, None)
    for source in field["sourceRows"]
}
DRIVER.canonical_fields = fresh_fields

ORIGINAL_AUDIT_FIELD = DRIVER.BASE.audit_field


def audit_field_with_exact_id(
    field_row,
    ring,
    live,
    local_only,
    certify_passers,
    witness_primes,
    max_reconstructions_per_pair=1,
    max_coset_reconstructions_per_sign=1,
    coset_mask_start=0,
    coset_mask_stride=1,
):
    certificate = None
    if certify_passers:
        quotient = ring(
            [
                ZZ(value)
                for value in field_row["canonicalPolynomial"].split(",")
            ]
        )
        group = quotient.galois_group(algorithm="gap")
        transitive_number = int(group.transitive_number())
        certificate = {
            "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
            "degree": int(group.degree()),
            "order": int(group.order()),
            "status": (
                "exact_quotient_group_certified"
                if transitive_number == 41
                else "wrong_exact_quotient_group"
            ),
            "transitiveLabel": f"12T{transitive_number}",
            "transitiveNumber": transitive_number,
        }
        if transitive_number != 41:
            raise ValueError(
                f"independent quotient group is 12T{transitive_number}, "
                "not 12T41"
            )
    result = ORIGINAL_AUDIT_FIELD(
        field_row,
        ring,
        live,
        local_only,
        certify_passers,
        witness_primes,
        max_reconstructions_per_pair,
        max_coset_reconstructions_per_sign,
        coset_mask_start,
        coset_mask_stride,
    )
    if certificate is not None:
        result["exactQuotientGaloisCertificate"] = certificate
    return result


DRIVER.BASE.audit_field = audit_field_with_exact_id


if __name__ == "__main__":
    raise SystemExit(DRIVER.main())

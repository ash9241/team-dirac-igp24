#!/usr/bin/env sage -python
"""Extract every degree-12 fixed field from accepted q70 12x2 parents."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari


ROOT = Path(__file__).resolve().parent
BASE_PATH = (
    ROOT
    / "character_fresh_field_inventory_q70_root_q12q81_20260730.sage.py"
)
STRUCTURES = ROOT / "data" / "agent_non12_tower_structures.jsonl"
DIRECT = (
    ROOT
    / "data"
    / "character_fresh_field_inventory_q70_root_q12q81_direct_20260730.json"
)
OUTPUT = (
    ROOT
    / "data"
    / "character_fresh_field_inventory_q70_root_q12q81_subfields_20260730.json"
)


def load_wrapper():
    spec = importlib.util.spec_from_file_location(
        "fresh_q70_inventory_wrapper", BASE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRAPPER = load_wrapper()
DRIVER = WRAPPER.DRIVER


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def canonical(polynomial, ring) -> tuple[str, str]:
    reduced = ring(pari(polynomial).polredabs())
    line = ",".join(str(ZZ(value)) for value in reduced.list())
    return line, hashlib.sha256(line.encode("ascii")).hexdigest()


def assert_unique_q70_actions(labels: set[str]) -> None:
    observed = {}
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            label = str(row["label"])
            if label not in labels:
                continue
            actions = sorted(
                {
                    str(system["quotientActionLabel"])
                    for system in row.get("blockSystems", [])
                    if system.get("shape") == "12x2"
                }
            )
            observed[label] = actions
    require(set(observed) == labels, "missing q70 structural source label")
    bad = {
        label: actions
        for label, actions in observed.items()
        if actions != ["12T70"]
    }
    require(not bad, f"q70 parent has alternate 12x2 action: {bad}")


def main() -> int:
    require(not OUTPUT.exists(), f"refusing to overwrite {OUTPUT}")
    structures = DRIVER.structural_sources()
    labels = set(structures)
    assert_unique_q70_actions(labels)
    rows = DRIVER.accepted_rows(sorted(labels))
    prior = DRIVER.prior_hashes()
    direct = json.loads(DIRECT.read_text(encoding="utf-8"))
    direct_hashes = {
        str(field["fieldCanonicalSha256"])
        for field in direct["canonicalTotallyRealFields"]
    }
    ring = PolynomialRing(QQ, "y")

    representatives = {}
    for row in rows:
        representatives.setdefault(row["coefficientSha256"], row)
    fields = {}
    parent_audits = []
    for index, row in enumerate(
        sorted(
            representatives.values(),
            key=lambda item: (
                len(item["coefficients"]),
                item["coefficientSha256"],
            ),
        ),
        start=1,
    ):
        polynomial = ring(
            [ZZ(value) for value in row["coefficients"].split(",")]
        )
        require(
            polynomial.degree() == 24 and polynomial.is_irreducible(),
            "accepted parent is not irreducible degree 24",
        )
        print(
            json.dumps(
                {
                    "event": "parent_start",
                    "index": index,
                    "label": row["label"],
                    "sourceSha256": row["coefficientSha256"],
                    "total": len(representatives),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        parent = NumberField(polynomial, f"a{index}")
        subfields = parent.subfields(12)
        audit = {
            "fieldDiscriminantAbs": row["fieldDiscriminantAbs"],
            "label": row["label"],
            "parentSignature": [
                int(value) for value in parent.signature()
            ],
            "polynomialIndex": row["polynomialIndex"],
            "r": row["r"],
            "sourceSha256": row["coefficientSha256"],
            "submissionId": row["submissionId"],
            "subfieldCount": len(subfields),
            "subfields": [],
        }
        for subfield, _embedding, _inverse in subfields:
            line, field_hash = canonical(subfield.polynomial(), ring)
            reduced = ring([ZZ(value) for value in line.split(",")])
            real_roots = int(reduced.number_of_real_roots())
            audit["subfields"].append(
                {
                    "canonicalPolynomial": line,
                    "fieldCanonicalSha256": field_hash,
                    "realRoots": real_roots,
                }
            )
            if real_roots != 12:
                continue
            item = fields.setdefault(
                field_hash,
                {
                    "alreadyAudited": field_hash in prior,
                    "alreadyInDirectCensus": field_hash in direct_hashes,
                    "canonicalPolynomial": line,
                    "fieldCanonicalSha256": field_hash,
                    "provenance": (
                        "degree-12 fixed field extracted by exact Sage/PARI "
                        "subfields(12) from an accepted scoreable degree-24 "
                        "parent whose every 12x2 quotient action is 12T70; "
                        "totally real; PARI polredabs canonicalization"
                    ),
                    "quotientT12": 70,
                    "sourceParents": [],
                },
            )
            item["sourceParents"].append(
                {
                    key: value
                    for key, value in audit.items()
                    if key not in {"subfields"}
                }
            )
        parent_audits.append(audit)
        print(
            json.dumps(
                {
                    "event": "parent_done",
                    "index": index,
                    "subfieldCount": len(subfields),
                    "total": len(representatives),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    canonical_fields = [fields[key] for key in sorted(fields)]
    fresh = [
        field
        for field in canonical_fields
        if not field["alreadyAudited"]
    ]
    payload = {
        "audit": {
            "acceptedScoreableRows": len(rows),
            "coefficientSearches": 0,
            "distinctParentCoefficientHashes": len(representatives),
            "networkCalls": 0,
            "priorCanonicalFieldHashes": sorted(prior),
            "submissionCalls": 0,
        },
        "canonicalTotallyRealSubfields": canonical_fields,
        "freshCanonicalTotallyRealSubfields": fresh,
        "parentAudits": parent_audits,
        "summary": {
            "allCanonicalTotallyRealSubfields": len(canonical_fields),
            "freshCanonicalTotallyRealSubfields": len(fresh),
            "freshFieldHashes": [
                field["fieldCanonicalSha256"] for field in fresh
            ],
            "parentsAudited": len(representatives),
        },
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(OUTPUT, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(OUTPUT.resolve()),
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

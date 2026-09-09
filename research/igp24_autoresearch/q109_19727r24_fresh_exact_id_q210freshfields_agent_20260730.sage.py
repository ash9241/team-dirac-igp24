#!/usr/bin/env sage -python
"""Exact polynomial-action IDs for fresh q109/r24 local survivors.

The broad structural census is deliberately only a source-family claim:
some degree-24 parents have several inequivalent 12x2 block systems.  This
audit independently identifies the Galois action of every fresh totally-real
degree-12 polynomial that passes the 24T19727/r24 local character gate.

There are no Selmer computations, network calls, staging operations, or
submissions in this audit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, libgap, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_CENSUS = (
    DATA
    / "broad_structural_tr_field_census_q109_19727r24_q210freshfields_agent_20260730.json"
)
DEFAULT_LOCAL = (
    DATA
    / "broad_structural_character_gate_q109_19727r24_q210freshfields_agent_local_20260730.json"
)
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_OUTPUT = (
    DATA
    / "q109_19727r24_fresh_exact_id_q210freshfields_agent_20260730.json"
)
CENSUS_SHA256 = (
    "d0fcf9185662b7421727bf6dc7ac862d619252fb92428a8110be1309ecc6eb7e"
)
LOCAL_SHA256 = (
    "12ba2f2ccb92e6327704f86efee204b69c3ce79499e6e9c8e96de6c360e03bb5"
)
TARGET_QUOTIENT_T = 109
TARGET_LABEL = "24T19727"
TARGET_R = 24


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def structural_systems(path: Path, labels: set[str]) -> dict[str, list[dict]]:
    output = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            label = str(row.get("label"))
            if label not in labels:
                continue
            output[label] = [
                {
                    "blockKernelOrder": int(system["blockKernelOrder"]),
                    "quotientActionLabel": str(
                        system["quotientActionLabel"]
                    ),
                    "quotientActionOrder": int(
                        system["quotientActionOrder"]
                    ),
                    "seedBlock": [
                        int(value) for value in system["seedBlock"]
                    ],
                    "systemSha256": str(system["systemSha256"]),
                }
                for system in row.get("blockSystems", [])
                if system.get("shape") == "12x2"
            ]
    missing = labels - set(output)
    if missing:
        raise ValueError(f"missing structural labels: {sorted(missing)}")
    return output


def exact_group_certificate(polynomial) -> dict:
    group = polynomial.galois_group(algorithm="gap")
    gap_group = libgap(group)
    sage_t = int(group.transitive_number())
    gap_t = int(libgap.TransitiveIdentification(gap_group))
    if sage_t != gap_t:
        raise ArithmeticError(
            f"Sage/GAP transitive-ID mismatch: {sage_t} versus {gap_t}"
        )
    degree = int(group.degree())
    if degree != 12 or not bool(libgap.IsTransitive(gap_group)):
        raise ArithmeticError("Galois action is not transitive degree 12")
    return {
        "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
        "degree": degree,
        "gapIdGroup": str(libgap.IdGroup(gap_group)),
        "gapTransitiveIdentification": gap_t,
        "generatorsCycleNotation": [
            str(generator) for generator in group.gens()
        ],
        "isTransitive": True,
        "order": int(group.order()),
        "sageTransitiveNumber": sage_t,
        "structureDescription": str(
            libgap.StructureDescription(gap_group)
        ),
        "transitiveLabel": f"12T{gap_t}",
    }


def main() -> int:
    if sha256_path(DEFAULT_CENSUS) != CENSUS_SHA256:
        raise ValueError("q109 broad census byte digest changed")
    if sha256_path(DEFAULT_LOCAL) != LOCAL_SHA256:
        raise ValueError("q109 local-gate byte digest changed")

    census = json.loads(DEFAULT_CENSUS.read_text(encoding="utf-8"))
    local = json.loads(DEFAULT_LOCAL.read_text(encoding="utf-8"))
    fresh_by_hash = {
        str(row["fieldCanonicalSha256"]): row
        for row in census["freshFields"]
    }
    survivors = []
    for field in local["fields"]:
        passers = [
            row
            for row in field.get("localPairGates", [])
            if (
                row.get("localPass") is True
                and str(row.get("label")) == TARGET_LABEL
                and int(row.get("r", -1)) == TARGET_R
            )
        ]
        if not passers:
            continue
        digest = str(field["fieldCanonicalSha256"])
        source = fresh_by_hash.get(digest)
        if source is None:
            raise ValueError(f"local survivor is not fresh: {digest}")
        survivors.append((field, source, passers))
    if len(survivors) != 8:
        raise ValueError(
            f"fresh q109/r24 local survivor count changed: {len(survivors)}"
        )

    source_labels = {
        str(row["label"])
        for _field, source, _passers in survivors
        for row in source["sourceRows"]
    }
    systems = structural_systems(DEFAULT_STRUCTURES, source_labels)
    ring = PolynomialRing(QQ, "x")
    rows = []
    for index, (field, source, passers) in enumerate(survivors, start=1):
        digest = str(field["fieldCanonicalSha256"])
        print(
            json.dumps(
                {
                    "event": "exact_id_start",
                    "fieldCanonicalSha256": digest,
                    "fieldIndex": index,
                    "fieldTotal": len(survivors),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        polynomial = ring(
            [
                ZZ(value)
                for value in str(field["canonicalPolynomial"]).split(",")
            ]
        )
        certificate = exact_group_certificate(polynomial)
        exact_match = (
            int(certificate["gapTransitiveIdentification"])
            == TARGET_QUOTIENT_T
        )
        source_rows = []
        for row in source["sourceRows"]:
            label = str(row["label"])
            source_rows.append(
                {
                    **row,
                    "allParent12x2Systems": systems[label],
                    "parentHasMultiple12x2Systems": (
                        len(systems[label]) > 1
                    ),
                }
            )
        rows.append(
            {
                "canonicalPolynomial": str(field["canonicalPolynomial"]),
                "exactGaloisAction": certificate,
                "exactQuotientMatch": exact_match,
                "fieldCanonicalSha256": digest,
                "localR24Passers": passers,
                "sourceRows": source_rows,
                "status": (
                    "exact_12T109_certified"
                    if exact_match
                    else "rejected_wrong_exact_12T_action"
                ),
            }
        )
        print(
            json.dumps(
                {
                    "event": "exact_id_done",
                    "fieldCanonicalSha256": digest,
                    "transitiveLabel": certificate[
                        "transitiveLabel"
                    ],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    exact = [
        row for row in rows if row["status"] == "exact_12T109_certified"
    ]
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "inputCensusSha256": CENSUS_SHA256,
            "inputLocalGateSha256": LOCAL_SHA256,
            "networkCalls": 0,
            "selmerComputations": 0,
            "submissionCalls": 0,
        },
        "fields": rows,
        "summary": {
            "exact12T109Fields": len(exact),
            "exact12T109Hashes": [
                row["fieldCanonicalSha256"] for row in exact
            ],
            "freshLocalR24Survivors": len(rows),
            "rejectedWrongExactAction": len(rows) - len(exact),
            "target": f"{TARGET_LABEL}/r{TARGET_R}",
        },
        "targetQuotientAction": "12T109",
    }
    rendered = rendered_json(payload)
    write_atomic(DEFAULT_OUTPUT, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(DEFAULT_OUTPUT),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    proof.all(True)
    raise SystemExit(main())

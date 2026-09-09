#!/usr/bin/env sage -python
"""Exact polynomial-action IDs for the three broad-census q41 claims.

The broad structural census intentionally warns that a degree-24 parent having
*some* 12x2/12T41 block system does not identify the particular fixed field
extracted from an even presentation q(x^2).  This audit therefore computes the
Galois action of each canonical degree-12 polynomial, and of every original
even quotient q, with Sage/GAP and applies GAP ``TransitiveIdentification`` to
the resulting degree-12 permutation group.

No Selmer computation is allowed here.  There are no coefficient searches,
network calls, staging operations, or submissions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from sage.all import (
    NumberField,
    PolynomialRing,
    QQ,
    ZZ,
    libgap,
    pari,
    proof,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_INPUT = DATA / "broad_structural_tr_field_census_q41_live_20260730.json"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_OUTPUT = (
    DATA
    / "q41_fresh_exact_id_certificate_q210freshfields_agent_20260730.json"
)
INPUT_SHA256 = (
    "01b95b66db12c15c734b7a920d022f6190bc6339bb0e4bea7ceffd95af7d9387"
)
EXPECTED_FRESH_HASHES = {
    "04a8b71a5fc16e8a5c13196574e8c48fdf45528888949ce54022a6c24c8701e8",
    "32383187790254ae9b67d72aa55fe2bf9cf7ba61665f25c3d03333a0a0ddf06b",
    "afdd36c97045fe42ce01e212cb6fd2325e9f8f37d035587f1bb20efec284bbd3",
}
TARGET_T = 41


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def coefficient_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def group_certificate(polynomial) -> dict:
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
        raise ArithmeticError("computed Galois action is not transitive degree 12")
    return {
        "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
        "degree": degree,
        "gapIdGroup": str(libgap.IdGroup(gap_group)),
        "gapTransitiveIdentification": gap_t,
        "generatorsCycleNotation": [str(generator) for generator in group.gens()],
        "isTransitive": True,
        "order": int(group.order()),
        "sageTransitiveNumber": sage_t,
        "structureDescription": str(libgap.StructureDescription(gap_group)),
        "transitiveLabel": f"12T{gap_t}",
    }


def target_action_certificate() -> dict:
    group = libgap.TransitiveGroup(12, TARGET_T)
    return {
        "degree": 12,
        "gapIdGroup": str(libgap.IdGroup(group)),
        "gapTransitiveIdentification": int(
            libgap.TransitiveIdentification(group)
        ),
        "generatorsCycleNotation": str(libgap.GeneratorsOfGroup(group)),
        "isTransitive": bool(libgap.IsTransitive(group)),
        "order": int(libgap.Size(group)),
        "structureDescription": str(libgap.StructureDescription(group)),
        "transitiveLabel": f"12T{TARGET_T}",
    }


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
                    "seedBlock": [int(value) for value in system["seedBlock"]],
                    "systemSha256": str(system["systemSha256"]),
                }
                for system in row.get("blockSystems", [])
                if system.get("shape") == "12x2"
            ]
    missing = labels - set(output)
    if missing:
        raise ValueError(f"structural source labels are missing: {sorted(missing)}")
    return output


def source_quotient_certificates(
    connection: sqlite3.Connection,
    source_rows: list[dict],
    canonical_line: str,
    canonical_hash: str,
    ring,
    systems: dict[str, list[dict]],
) -> list[dict]:
    output = []
    for source in source_rows:
        row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,
                   v.label,v.r,v.scoreable,v.status
            FROM polynomials AS p
            JOIN verifications AS v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (
                str(source["submissionId"]),
                int(source["polynomialIndex"]),
            ),
        ).fetchone()
        if row is None:
            raise ValueError(f"source row is absent: {source}")
        (
            coefficient_text,
            coefficient_hash,
            label,
            source_r,
            scoreable,
            status,
        ) = row
        expected_identity = (
            str(source["coefficientSha256"]),
            str(source["label"]),
            int(source["r"]),
            bool(source["scoreable"]),
        )
        actual_identity = (
            str(coefficient_hash),
            str(label),
            int(source_r),
            bool(scoreable),
        )
        if actual_identity != expected_identity or str(status) != "accepted":
            raise ValueError(
                f"source identity/status mismatch: {actual_identity}, "
                f"expected {expected_identity}, status={status}"
            )
        coefficients = [ZZ(value) for value in str(coefficient_text).split(",")]
        source_line = ",".join(str(value) for value in coefficients)
        if hashlib.sha256(source_line.encode("ascii")).hexdigest() != str(
            coefficient_hash
        ):
            raise ValueError("source coefficient hash mismatch")
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("source is not a monic even degree-24 polynomial")
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            raise ArithmeticError("source quotient is not irreducible degree 12")
        if int(quotient.number_of_real_roots()) != 12:
            raise ArithmeticError("source quotient is not totally real")
        reduced = ring(pari(quotient).polredabs())
        reduced_line = coefficient_line(reduced)
        reduced_hash = hashlib.sha256(
            reduced_line.encode("ascii")
        ).hexdigest()
        if reduced_line != canonical_line or reduced_hash != canonical_hash:
            raise ArithmeticError(
                "source quotient does not canonicalize to the census field"
            )
        exact_group = group_certificate(quotient)
        matching_structural_systems = [
            system
            for system in systems[str(label)]
            if system["quotientActionLabel"]
            == exact_group["transitiveLabel"]
        ]
        if not matching_structural_systems:
            raise ArithmeticError(
                f"{label} has no structural system matching "
                f"{exact_group['transitiveLabel']}"
            )
        q41_systems = [
            system
            for system in systems[str(label)]
            if system["quotientActionLabel"] == "12T41"
        ]
        if not q41_systems:
            raise ArithmeticError(f"{label} unexpectedly has no 12T41 system")
        output.append(
            {
                "coefficientSha256": str(coefficient_hash),
                "exactQuotientGaloisAction": exact_group,
                "label": str(label),
                "matchingStructuralSystems": matching_structural_systems,
                "parentHasMultiple12x2Systems": len(systems[str(label)]) > 1,
                "parentQ41Systems": q41_systems,
                "polynomialIndex": int(source["polynomialIndex"]),
                "quotientCanonicalSha256": reduced_hash,
                "quotientIrreducible": True,
                "quotientRealRootCount": 12,
                "r": int(source_r),
                "scoreable": bool(scoreable),
                "submissionId": str(source["submissionId"]),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite q41 exact-ID certificate")

    proof.all(True)
    input_hash = sha256_file(args.input)
    if input_hash != INPUT_SHA256:
        raise ValueError(
            f"input census hash mismatch: {input_hash}, expected {INPUT_SHA256}"
        )
    census = json.loads(args.input.read_text(encoding="utf-8"))
    fresh = list(census["freshFields"])
    actual_hashes = {str(row["fieldCanonicalSha256"]) for row in fresh}
    if len(fresh) != 3 or actual_hashes != EXPECTED_FRESH_HASHES:
        raise ValueError(
            f"fresh q41 input changed: count={len(fresh)}, "
            f"hashes={sorted(actual_hashes)}"
        )
    source_labels = {
        str(source["label"])
        for field in fresh
        for source in field["sourceRows"]
    }
    systems = structural_systems(args.structures, source_labels)
    target = target_action_certificate()
    if target["gapTransitiveIdentification"] != TARGET_T:
        raise ArithmeticError("standard target action failed exact GAP ID")

    ring = PolynomialRing(QQ, "x")
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    try:
        results = []
        for field_row in sorted(
            fresh, key=lambda row: row["fieldCanonicalSha256"]
        ):
            claimed_hash = str(field_row["fieldCanonicalSha256"])
            polynomial = ring(
                [
                    ZZ(value)
                    for value in field_row["canonicalPolynomial"].split(",")
                ]
            )
            canonical_line = coefficient_line(polynomial)
            actual_hash = hashlib.sha256(
                canonical_line.encode("ascii")
            ).hexdigest()
            if actual_hash != claimed_hash:
                raise ValueError(
                    f"canonical hash mismatch: {actual_hash}, "
                    f"expected {claimed_hash}"
                )
            if (
                polynomial.degree() != 12
                or not polynomial.is_monic()
                or not polynomial.is_irreducible()
            ):
                raise ArithmeticError("canonical polynomial failed exact checks")
            reduced = ring(pari(polynomial).polredabs())
            if coefficient_line(reduced) != canonical_line:
                raise ArithmeticError("canonical polynomial is not PARI polredabs")
            field = NumberField(polynomial, "a")
            signature = [int(value) for value in field.signature()]
            real_roots = int(polynomial.number_of_real_roots())
            if signature != [12, 0] or real_roots != 12:
                raise ArithmeticError("canonical field is not totally real")
            exact_group = group_certificate(polynomial)
            source_certificates = source_quotient_certificates(
                connection,
                field_row["sourceRows"],
                canonical_line,
                claimed_hash,
                ring,
                systems,
            )
            source_labels_observed = {
                row["exactQuotientGaloisAction"]["transitiveLabel"]
                for row in source_certificates
            }
            if source_labels_observed != {exact_group["transitiveLabel"]}:
                raise ArithmeticError(
                    "canonical/source quotient exact actions disagree"
                )
            matches_target = (
                exact_group["gapTransitiveIdentification"] == TARGET_T
            )
            results.append(
                {
                    "canonicalPolynomial": canonical_line,
                    "exactGaloisAction": exact_group,
                    "fieldCanonicalSha256": claimed_hash,
                    "fieldDiscriminantAbs": str(
                        abs(int(pari(polynomial).nfdisc()))
                    ),
                    "irreducible": True,
                    "matches12T41": matches_target,
                    "polredabsStable": True,
                    "polynomialDiscriminantAbs": str(
                        abs(int(polynomial.discriminant()))
                    ),
                    "realRootCount": real_roots,
                    "signature": signature,
                    "sourceQuotientCertificates": source_certificates,
                    "status": (
                        "exact_12T41_certified"
                        if matches_target
                        else "rejected_not_12T41"
                    ),
                }
            )
            print(
                json.dumps(
                    {
                        "event": "field_exact_id",
                        "fieldCanonicalSha256": claimed_hash,
                        "matches12T41": matches_target,
                        "observed": exact_group["transitiveLabel"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        connection.close()

    observed = Counter(
        row["exactGaloisAction"]["transitiveLabel"] for row in results
    )
    certified = sum(row["matches12T41"] for row in results)
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "input": str(args.input.resolve()),
            "inputSha256": input_hash,
            "networkCalls": 0,
            "selmerRuns": 0,
            "submissionCalls": 0,
            "transitiveIdentificationMeaning": (
                "GAP TransitiveIdentification is the exact conjugacy-class "
                "identifier of the computed transitive subgroup of S_12; "
                "unequal IDs certify unequal degree-12 actions."
            ),
        },
        "fields": results,
        "summary": {
            "exact12T41Fields": certified,
            "freshFieldsAudited": len(results),
            "observedExactActions": dict(sorted(observed.items())),
            "rejectedNot12T41": len(results) - certified,
            "selmerRuns": 0,
        },
        "targetAction": target,
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

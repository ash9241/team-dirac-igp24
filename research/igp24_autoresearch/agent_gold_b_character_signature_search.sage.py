#!/usr/bin/env sage -python
"""Offline exact character-kernel search for an arbitrary real signature.

This extends ``character_kernel_gold_pilot.sage.py`` from the totally positive
case to any positive-norm signature.  The target norm squareclass is recovered
by the shared exact quotient-character alignment; the S-unit linear system is
then solved over every sign vector with the requested number of real roots.
No network calls or submissions are made.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import sqlite3
from pathlib import Path

from sage.all import GF, Matrix, NumberField, PolynomialRing, QQ, RealField, ZZ, prod, vector


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_kernel_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()
SHARED = HELPER.SHARED


def load_live_gold(db: Path, label: str, r: int) -> dict:
    connection = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    try:
        row = connection.execute(
            """
            SELECT t.team_count,t.minimum_disc_abs,t.generated_at,
                   CASE WHEN b.label IS NULL THEN 0 ELSE 1 END,
                   CASE WHEN owned.label IS NULL THEN 0 ELSE 1 END
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=? AND t.r=?
            """,
            (label, r),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"missing target row for {label}/r{r}")
    result = {
        "baseline": bool(row[3]),
        "generatedAt": str(row[2]) if row[2] else None,
        "locallyOwned": bool(row[4]),
        "minimumDiscAbs": str(row[1]) if row[1] else None,
        "pair": f"{label}/r{r}",
        "teamCount": int(row[0]),
    }
    if result["teamCount"] != 0 or result["baseline"] or result["locallyOwned"]:
        raise ValueError(f"target is not live unowned nonbaseline gold: {result}")
    return result


def state_bits(sign_mask: int, norm, rational_primes: list[int]) -> list[int]:
    bits = [(sign_mask >> index) & 1 for index in range(12)]
    norm = QQ(norm)
    bits.extend(int(norm.valuation(prime)) & 1 for prime in rational_primes)
    return bits


def search(source: dict, target_t: int, target_r: int, core: ZZ,
           auxiliary_primes: list[int], maximum: int,
           solutions_per_sign: int, witnesses: int, seed: int) -> dict:
    quotient = source["quotient"]
    core = ZZ(core)
    core_primes = [int(value) for value in core.prime_divisors()]
    rational_primes = core_primes + auxiliary_primes
    field = NumberField(quotient.change_ring(QQ), "a")
    s_units = field.S_unit_group(proof=False, S=prod(rational_primes))
    generators = list(s_units.gens_values())
    embeddings = field.embeddings(RealField(160))
    columns = []
    for unit in generators:
        sign_mask = sum(
            1 << index
            for index, embedding in enumerate(embeddings)
            if embedding(unit) < 0
        )
        columns.append(state_bits(sign_mask, unit.norm(), rational_primes))
    matrix = Matrix(
        GF(2),
        12 + len(rational_primes),
        len(generators),
        lambda row, column: columns[column][row],
    )

    negative_embeddings = 12 - target_r // 2
    if target_r % 4 or negative_embeddings < 0 or negative_embeddings > 12:
        raise ValueError(
            "a character-kernel lift over a totally real quotient requires "
            "r divisible by 4 and between 0 and 24"
        )

    SHARED.TARGET_T = target_t
    SHARED.TARGET_LABEL = f"24T{target_t}"
    profiles, identities = SHARED.maximal_joint_profiles()
    ring = PolynomialRing(ZZ, "x")
    x = ring.gen()
    rows = []
    solvable_sign_masks = 0
    attempted_elements = 0
    seen_hashes = set()

    for sign_indexes in itertools.combinations(range(12), negative_embeddings):
        sign_mask = sum(1 << index for index in sign_indexes)
        target = vector(
            GF(2),
            [(sign_mask >> index) & 1 for index in range(12)]
            + [int(core.valuation(prime)) & 1 for prime in core_primes]
            + [0] * len(auxiliary_primes),
        )
        try:
            solutions = list(
                SHARED.affine_solutions(
                    matrix,
                    target,
                    solutions_per_sign,
                    seed ^ sign_mask,
                )
            )
        except ValueError:
            continue
        solvable_sign_masks += 1
        for solution_mask, kernel_dimension in solutions:
            attempted_elements += 1
            element = field.one()
            selected = []
            for index, unit in enumerate(generators):
                if (solution_mask >> index) & 1:
                    element *= unit
                    selected.append(index)
            scaled, square_scale = SHARED.integral_square_scale(element)
            norm = QQ(scaled.norm())
            row = {
                "affineKernelDimension": kernel_dimension,
                "norm": str(norm),
                "normOverCoreIsSquare": SHARED.rational_is_square(norm / core),
                "selectedSUnitGeneratorIndexes": selected,
                "signMask": sign_mask,
                "squareScale": str(square_scale),
            }
            if not row["normOverCoreIsSquare"]:
                row["status"] = "failed_exact_norm_check"
                rows.append(row)
                continue
            minimal = scaled.minpoly().change_ring(QQ)
            if minimal.degree() != 12 or any(
                QQ(value).denominator() != 1 for value in minimal
            ):
                row["status"] = "nonintegral_or_nonprimitive_minpoly"
                rows.append(row)
                continue
            minimal_zz = PolynomialRing(ZZ, "z")(
                [ZZ(value) for value in minimal.list()]
            )
            candidate = ring(minimal_zz(x**2))
            line = SHARED.coefficient_line(candidate)
            digest = hashlib.sha256(line.encode()).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            row.update(
                {
                    "candidateCoefficientLine": line,
                    "candidateSha256": digest,
                    "containmentProof": {
                        "sameDegree12Field": True,
                        "targetSquarefreeNormCore": int(core),
                        "theorem": (
                            "the Frobenius-aligned rational norm character "
                            "implies containment in the target character-kernel action"
                        ),
                    },
                    "irreducible": bool(candidate.is_irreducible()),
                    "minimalPolynomial": str(minimal_zz),
                    "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                    "realRoots": int(candidate.number_of_real_roots()),
                }
            )
            if not row["irreducible"] or row["realRoots"] != target_r:
                row["status"] = "failed_degree24_checks"
                rows.append(row)
                continue
            row["maximalSubgroupCertificate"] = SHARED.frobenius_maximal_certificate(
                candidate, quotient, profiles, identities, witnesses
            )
            row["status"] = (
                f"certified_24T{target_t}_r{target_r}"
                if row["maximalSubgroupCertificate"]["complete"]
                else "contained_candidate_incomplete_maximal_certificate"
            )
            if row["status"].startswith("certified_"):
                row["fieldDiscriminantAbs"] = str(
                    abs(ZZ(NumberField(candidate.change_ring(QQ), "b").discriminant()))
                )
            rows.append(row)
            if row["status"].startswith("certified_"):
                return {
                    "attemptedElements": attempted_elements,
                    "auxiliaryPrimes": auxiliary_primes,
                    "candidateResults": rows,
                    "corePrimes": core_primes,
                    "sUnitGeneratorCount": len(generators),
                    "sUnitPrimeIdealCount": len(s_units.primes()),
                    "solvableSignMasks": solvable_sign_masks,
                    "status": "completed",
                }
            if attempted_elements >= maximum:
                break
        if attempted_elements >= maximum:
            break
    return {
        "attemptedElements": attempted_elements,
        "auxiliaryPrimes": auxiliary_primes,
        "candidateResults": rows,
        "corePrimes": core_primes,
        "sUnitGeneratorCount": len(generators),
        "sUnitPrimeIdealCount": len(s_units.primes()),
        "solvableSignMasks": solvable_sign_masks,
        "status": (
            "no_solvable_sign_mask"
            if solvable_sign_masks == 0
            else "completed_without_certificate"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--target-r", required=True, type=int)
    parser.add_argument("--aux-primes", default="")
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--solutions-per-sign", type=int, default=2)
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--norm-core", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    target_t = HELPER.parse_label(args.target_label)
    auxiliary = [int(value) for value in args.aux_primes.split(",") if value.strip()]
    source = HELPER.load_source(args.db, args.submission_id, args.polynomial_index)
    live_gold = load_live_gold(args.db, args.target_label, args.target_r)
    structure = HELPER.target_structure(target_t)
    alignment = HELPER.character_alignment(source["quotient"], structure["quotientT"])
    source_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(
        source["label"], []
    )
    if source["squarefreeNormCore"] not in source_cores:
        raise ValueError("character alignment does not recover the source")
    target_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(
        args.target_label, []
    )
    if not target_cores:
        raise ValueError("target label is absent from aligned character actions")
    if args.norm_core is None:
        norm_core = min(target_cores)
    elif args.norm_core not in target_cores:
        raise ValueError(f"norm core is not target-aligned: {target_cores}")
    else:
        norm_core = args.norm_core

    result = {
        "audit": {
            "characterAlignment": alignment,
            "liveTarget": live_gold,
            "networkCalls": 0,
            "source": {key: value for key, value in source.items() if key != "quotient"},
            "submissionCalls": 0,
            "targetStructure": structure,
        },
        "search": search(
            source,
            target_t,
            args.target_r,
            ZZ(norm_core),
            auxiliary,
            args.max_candidates,
            args.solutions_per_sign,
            args.witness_primes,
            args.seed,
        ),
    }
    certified = [
        row
        for row in result["search"].get("candidateResults", [])
        if str(row.get("status", "")).startswith("certified_")
    ]
    if args.manifest is not None:
        if len(certified) != 1:
            raise ValueError(f"refusing to stage: certified count={len(certified)}")
        text = certified[0]["candidateCoefficientLine"] + "\n"
        args.manifest.resolve().write_text(text, encoding="utf-8")
        result["staging"] = {
            "bytes": len(text.encode()),
            "candidateSha256": certified[0]["candidateSha256"],
            "manifest": str(args.manifest.resolve()),
            "manifestSha256": hashlib.sha256(text.encode()).hexdigest(),
            "polynomials": 1,
            "submissionCalls": 0,
        }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.resolve().write_text(rendered, encoding="utf-8")
        print(json.dumps({"output": str(args.output.resolve()), "status": result["search"]["status"]}))
    else:
        print(rendered, end="")
    return 0 if certified else 2


if __name__ == "__main__":
    raise SystemExit(main())

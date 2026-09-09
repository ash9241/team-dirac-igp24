#!/usr/bin/env sage -python
"""Reproduce and certify the F5 derivative-radicand 24T15337/r20 hit."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sage.all import GF, Matrix, PolynomialRing, ZZ, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
MANIFEST = ROOT / "outbox" / "agent_f5_28_derivative_24T15337_r20.txt"
OUTPUT = DATA / "f5_28_derivative_24T15337_r20_certificate.json"
SOURCE_SUBMISSION = "sub_2f3b2b7af1fd4e4a84c4cca84797c201"
SOURCE_INDEX = 8
SOURCE_LABEL = "24T19738"
SOURCE_R = 24
QUOTIENT_LINE = (
    "14565989,-2114295053,11853787534,-18764977906,13941011872,"
    "-5699782331,1370959475,-199518023,17549862,-900974,24660,-293,1"
)
TARGET_LABEL = "24T15337"
TARGET_R = 20


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode())


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cycle_type_mod(polynomial, prime: int) -> tuple[int, ...]:
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in polynomial.change_ring(GF(prime)).factor()
            for _ in range(int(exponent))
        )
    )


def cycle_type_gap(permutation) -> tuple[int, ...]:
    return tuple(
        sorted(
            int(value)
            for value in libgap.CycleLengths(
                permutation, libgap.eval("[1..24]")
            )
        )
    )


def exhaustive_quotient_112_catalog() -> list[dict]:
    """Enumerate every degree-24 action with a two-block quotient 12T112."""
    catalog = []
    quotient_order = int(libgap.Size(libgap.TransitiveGroup(12, 112)))
    for transitive_index in range(1, int(libgap.NrTransitiveGroups(24)) + 1):
        group = libgap.TransitiveGroup(24, transitive_index)
        group_order = int(libgap.Size(group))
        if group_order % quotient_order:
            continue
        kernel_order = group_order // quotient_order
        if (
            kernel_order <= 0
            or kernel_order > 2**12
            or kernel_order & (kernel_order - 1)
        ):
            continue
        systems = []
        for block in libgap.AllBlocks(group):
            if int(libgap.Size(block)) != 2:
                continue
            blocks = libgap.Orbit(group, block, libgap.OnSets)
            if int(libgap.Size(blocks)) != 12:
                continue
            block_action = libgap.Action(group, blocks, libgap.OnSets)
            if int(libgap.TransitiveIdentification(block_action)) != 112:
                continue
            images = list(range(1, 25))
            rendered_blocks = []
            for pair in blocks:
                first, second = sorted(int(value) for value in pair)
                images[first - 1] = second
                images[second - 1] = first
                rendered_blocks.append([first, second])
            full_flip = libgap.PermList(images)
            systems.append(
                {
                    "blocks": sorted(rendered_blocks),
                    "flipInGroup": bool(full_flip in group),
                }
            )
        if systems:
            catalog.append(
                {
                    "kernelOrder": kernel_order,
                    "label": f"24T{transitive_index}",
                    "order": group_order,
                    "systems": systems,
                    "t": transitive_index,
                }
            )
    return catalog


def accepted_source_kummer_relation() -> dict:
    """Recover the exact sign-kernel relation in the accepted 24T19738 action."""
    group = libgap.TransitiveGroup(24, 19738)
    selected_blocks = None
    selected_homomorphism = None
    for block in libgap.AllBlocks(group):
        if int(libgap.Size(block)) != 2:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        if int(libgap.Size(blocks)) != 12:
            continue
        homomorphism = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
        quotient = libgap.Image(homomorphism)
        if int(libgap.TransitiveIdentification(quotient)) == 112:
            selected_blocks = [
                sorted(int(value) for value in pair) for pair in blocks
            ]
            selected_homomorphism = homomorphism
            break
    if selected_blocks is None or selected_homomorphism is None:
        raise ValueError("accepted source lacks its certified 12T112 block action")
    kernel = libgap.Kernel(selected_homomorphism)
    sign_vectors = []
    for generator in libgap.GeneratorsOfGroup(kernel):
        vector = []
        for first, second in selected_blocks:
            image = int(libgap.OnPoints(first, generator))
            if image == first:
                vector.append(0)
            elif image == second:
                vector.append(1)
            else:
                raise ValueError("kernel generator does not preserve a source block")
        sign_vectors.append(vector)
    sign_matrix = Matrix(GF(2), sign_vectors)
    relation_basis = [
        [int(value) for value in vector]
        for vector in sign_matrix.right_kernel().basis()
    ]
    if (
        int(libgap.Size(libgap.Image(selected_homomorphism))) != 192
        or int(libgap.Size(kernel)) != 2**11
        or sign_matrix.rank() != 11
        or relation_basis != [[1] * 12]
    ):
        raise ValueError("accepted source does not have the expected all-product relation")
    return {
        "blockKernelOrder": int(libgap.Size(kernel)),
        "blockQuotientOrder": int(
            libgap.Size(libgap.Image(selected_homomorphism))
        ),
        "relationBasis": relation_basis,
        "signKernelRank": sign_matrix.rank(),
    }


def main() -> int:
    if OUTPUT.exists():
        raise ValueError(f"refusing to overwrite {OUTPUT}")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.t,v.status,v.scoreable
        FROM polynomials AS p JOIN verifications AS v
        USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (SOURCE_SUBMISSION, SOURCE_INDEX),
    ).fetchone()
    if source is None:
        raise ValueError("accepted source pin is absent")
    source_values = str(source["coefficients"]).split(",")
    if (
        str(source["label"]) != SOURCE_LABEL
        or int(source["r"]) != SOURCE_R
        or str(source["status"]) != "accepted"
        or int(source["scoreable"]) != 1
        or len(source_values) != 25
        or any(int(source_values[index]) for index in range(1, 25, 2))
        or ",".join(source_values[::2]) != QUOTIENT_LINE
    ):
        raise ValueError("accepted source provenance mismatch")

    ring_u = PolynomialRing(ZZ, "u")
    u = ring_u.gen()
    q = ring_u([ZZ(value) for value in QUOTIENT_LINE.split(",")])
    radicand = u * q.derivative()
    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    q_y = sum(q[index] * y**index for index in range(q.degree() + 1))
    radicand_y = sum(
        radicand[index] * y**index
        for index in range(radicand.degree() + 1)
    )
    resultant_quotient = ring_z(q_y.resultant(z - radicand_y, y))
    if resultant_quotient.leading_coefficient() == -1:
        resultant_quotient = -resultant_quotient
    source_polynomial = ring_x(resultant_quotient(x**2))
    if (
        resultant_quotient.degree() != 12
        or not resultant_quotient.is_monic()
        or not resultant_quotient.is_irreducible()
        or not source_polynomial.is_irreducible()
        or int(source_polynomial.number_of_real_roots()) != 12
    ):
        raise ValueError("derivative-resultant source failed exact gates")

    compatible = exhaustive_quotient_112_catalog()
    if len(compatible) != 108:
        raise ValueError("unexpected exhaustive 12T112 quotient catalog size")
    profiles = {}
    orders = {}
    for row in compatible:
        group = libgap.TransitiveGroup(24, int(row["t"]))
        label = str(row["label"])
        orders[label] = int(row["order"])
        profiles[label] = {
            cycle_type_gap(libgap.Representative(conjugacy_class))
            for conjugacy_class in libgap.ConjugacyClasses(group)
        }

    archimedean_type = tuple([1] * 12 + [2] * 6)
    remaining = {
        label for label, types in profiles.items() if archimedean_type in types
    }
    elimination = [
        {
            "after": sorted(remaining),
            "afterCount": len(remaining),
            "evidence": "archimedean",
            "sourceR": 12,
            "type": list(archimedean_type),
        }
    ]
    source_discriminant = source_polynomial.discriminant()
    for prime in (23, 47):
        if source_discriminant % prime == 0:
            raise ValueError("classification prime is not squarefree")
        observed = cycle_type_mod(source_polynomial, prime)
        before = sorted(remaining)
        remaining = {
            label for label in remaining if observed in profiles[label]
        }
        elimination.append(
            {
                "after": sorted(remaining),
                "afterCount": len(remaining),
                "before": before,
                "beforeCount": len(before),
                "evidence": "squarefree modular factorization",
                "prime": prime,
                "type": list(observed),
            }
        )
    if remaining != {SOURCE_LABEL, "24T20740"}:
        raise ValueError("profile elimination did not leave the exact maximal pair")

    source_relation = accepted_source_kummer_relation()
    quotient_discriminant = q.discriminant()
    if not quotient_discriminant.is_square():
        raise ValueError("q discriminant is not the required exact rational square")
    maximum_derivative_source_order = (
        int(source_relation["blockQuotientOrder"])
        * int(source_relation["blockKernelOrder"])
    )
    before_order_bound = sorted(remaining)
    remaining = {
        label
        for label in remaining
        if int(orders[label]) <= maximum_derivative_source_order
    }
    elimination.append(
        {
            "after": sorted(remaining),
            "afterCount": len(remaining),
            "before": before_order_bound,
            "beforeCount": len(before_order_bound),
            "evidence": "exact Kummer all-product relation and order bound",
            "explanation": (
                "The accepted 24T19738 source sign kernel has rank 11 with "
                "unique relation (1,...,1).  Since disc(q) is a rational "
                "square, product_i(t_i*q'(t_i)) has the same square class "
                "as product_i(t_i), so the derivative-resultant sign kernel "
                "has rank at most 11."
            ),
            "maximumOrder": maximum_derivative_source_order,
            "quotientDiscriminantIsSquare": True,
            "sourceKummerRelation": source_relation,
        }
    )
    if remaining != {SOURCE_LABEL}:
        raise ValueError("source label did not classify uniquely")

    action_rows = []
    for path in sorted(
        DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
    ):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if str(row["sourceLabel"]) == SOURCE_LABEL:
                    action_rows.append(row)
    if (
        len(action_rows) != 1
        or str(action_rows[0]["targetLabel"]) != TARGET_LABEL
    ):
        raise ValueError("exact source action is not unique")
    exact_action = action_rows[0]

    pair_resolvent = resultant_quotient.symmetric_power(2, monic=True)
    factors = [
        (factor, int(exponent)) for factor, exponent in pair_resolvent.factor()
    ]
    selected = [
        (index, factor)
        for index, (factor, exponent) in enumerate(factors)
        if factor.degree() == 12 and exponent == 1
    ]
    if len(selected) != 1:
        raise ValueError("pair resolvent lacks a unique degree-12 factor")
    factor_index, selected_factor = selected[0]
    candidate = ring_x(selected_factor(x**2))
    candidate_line = ",".join(str(value) for value in candidate.list())
    candidate_hash = sha_text(candidate_line)
    manifest_bytes = MANIFEST.read_bytes()
    if (
        candidate.degree() != 24
        or not candidate.is_monic()
        or not candidate.is_irreducible()
        or int(candidate.number_of_real_roots()) != TARGET_R
        or manifest_bytes.decode().strip() != candidate_line
    ):
        raise ValueError("candidate or manifest mismatch")

    target = connection.execute(
        """
        SELECT t.*,
          EXISTS(
            SELECT 1 FROM baseline_pairs AS b
            WHERE b.label=t.label AND b.r=t.r
          ) AS baseline,
          EXISTS(
            SELECT 1 FROM verifications AS v
            WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
          ) AS owned
        FROM targets AS t WHERE t.label=? AND t.r=?
        """,
        (TARGET_LABEL, TARGET_R),
    ).fetchone()
    duplicate_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
            (candidate_hash,),
        ).fetchone()[0]
    )
    if (
        target is None
        or int(target["team_count"]) != 0
        or int(target["discovered"]) != 0
        or int(target["baseline"]) != 0
        or int(target["owned"]) != 0
        or duplicate_count != 0
    ):
        raise ValueError("candidate failed current live or duplicate gates")

    resultant_line = ",".join(
        str(value) for value in resultant_quotient.list()
    )
    source_polynomial_line = ",".join(
        str(value) for value in source_polynomial.list()
    )
    selected_factor_line = ",".join(
        str(value) for value in selected_factor.list()
    )
    script_path = Path(__file__).resolve()
    certificate = {
        "candidate": {
            "coefficientLine": candidate_line,
            "coefficientSha256": candidate_hash,
            "degree": 24,
            "irreducible": True,
            "monic": True,
            "r": TARGET_R,
        },
        "exactAction": exact_action,
        "factorization": {
            "factorDegrees": [
                {
                    "degree": int(factor.degree()),
                    "exponent": exponent,
                    "index": index,
                }
                for index, (factor, exponent) in enumerate(factors)
            ],
            "pairResolventCoefficientSha256": sha_text(
                ",".join(str(value) for value in pair_resolvent.list())
            ),
            "selectedDegree12FactorIndex": factor_index,
            "selectedDegree12FactorLine": selected_factor_line,
            "selectedDegree12FactorSha256": sha_text(selected_factor_line),
        },
        "liveAndNoveltyGate": {
            "baseline": False,
            "discovered": False,
            "ledgerCoefficientOccurrences": duplicate_count,
            "owned": False,
            "targetGeneratedAt": str(target["generated_at"]),
            "teamCount": 0,
        },
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha_bytes(manifest_bytes),
        "method": (
            "derivative-radicand F5: g(t)=t*q'(t), "
            "h(z)=Res_t(q(t),z-g(t)), unique pair-orbit factor"
        ),
        "networkCalls": 0,
        "reproduction": {
            "command": "sage -python certify_f5_28_derivative_15337.sage.py",
            "script": str(script_path.relative_to(ROOT)),
            "scriptSha256": sha_bytes(script_path.read_bytes()),
        },
        "resultantLift": {
            "quotientGaloisAction": "12T112",
            "resultantQuotientLine": resultant_line,
            "resultantQuotientSha256": sha_text(resultant_line),
            "sourcePolynomialLine": source_polynomial_line,
            "sourcePolynomialSha256": sha_text(source_polynomial_line),
            "sourcePolynomialIrreducible": True,
            "sourcePolynomialR": 12,
            "transform": "g(t)=t*q'(t)",
        },
        "sourceClassification": {
            "candidateCatalog": "exhaustive GAP TransitiveGroup(24,1..25000)",
            "compatible12T112Actions": compatible,
            "compatible12T112Count": len(compatible),
            "elimination": elimination,
            "exactLabel": SOURCE_LABEL,
            "quotientActionJustification": (
                "The irreducible degree-12 resultant is a separating "
                "Tschirnhausen image of q, hence has the same exact 12T112 "
                "root action."
            ),
        },
        "sourcePin": {
            "acceptedCoefficientSha256": str(source["coefficient_hash"]),
            "label": SOURCE_LABEL,
            "polynomialIndex": SOURCE_INDEX,
            "quotientLine": QUOTIENT_LINE,
            "quotientSha256": sha_text(QUOTIENT_LINE),
            "r": SOURCE_R,
            "status": "accepted",
            "submissionId": SOURCE_SUBMISSION,
            "t": int(source["t"]),
        },
        "submissionCalls": 0,
        "target": {"label": TARGET_LABEL, "r": TARGET_R, "t": 15337},
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    atomic_write(OUTPUT, rendered)
    print(
        json.dumps(
            {
                "candidateSha256": candidate_hash,
                "certificate": str(OUTPUT.relative_to(ROOT)),
                "certificateSha256": sha_text(rendered),
                "exactLabel": SOURCE_LABEL,
                "target": f"{TARGET_LABEL}/r{TARGET_R}",
            },
            sort_keys=True,
        )
    )
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

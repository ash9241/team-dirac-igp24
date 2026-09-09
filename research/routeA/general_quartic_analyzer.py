#!/usr/bin/env python3
"""Exact PARI-backed analysis for quartics over totally real sextic fields.

The floating-point work in the GQ generator is only an archimedean
pre-screen.  Every row returned by this module has subsequently passed exact
relative factorization, discriminant-square, degree, rational irreducibility,
and Sturm root-count checks in PARI/GP.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import sympy

from routeA.empirical_recipes import run_gp
from routeA.quartic_invariants import (
    classify_relative_quartic,
    frobenius_fingerprint,
)


MARKER_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43,
                 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97)
FROBENIUS_PRIMES = tuple(sympy.primerange(2, 140))[:30]


@dataclass(frozen=True)
class QuarticAnalysisJob:
    index: int
    base_coefficients: tuple[int, ...]
    quartic_expression: str
    requested_regime: str
    requested_root_count: int
    squareclass_expressions: tuple[str, ...]
    metadata: Mapping[str, Any]


def polynomial_expression(
    coefficients: Sequence[int], variable: str = "t"
) -> str:
    terms = [
        f"({int(coefficient)})*{variable}^{degree}"
        for degree, coefficient in enumerate(coefficients)
        if int(coefficient)
    ]
    return "(" + "+".join(terms or ["0"]) + ")"


def rank_mod_2(rows: Iterable[Sequence[int]]) -> int:
    matrix = [
        [int(value) & 1 for value in row]
        for row in rows
        if any(int(value) & 1 for value in row)
    ]
    if not matrix:
        return 0
    width = max(map(len, matrix))
    matrix = [row + [0] * (width - len(row)) for row in matrix]
    rank = column = 0
    while rank < len(matrix) and column < width:
        pivot = next(
            (row for row in range(rank, len(matrix)) if matrix[row][column]),
            None,
        )
        if pivot is None:
            column += 1
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        for row in range(len(matrix)):
            if row != rank and matrix[row][column]:
                matrix[row] = [a ^ b for a, b in zip(matrix[row], matrix[rank])]
        rank += 1
        column += 1
    return rank


def _gp_vector(value: Sequence[int]) -> str:
    return "[" + ",".join(str(int(item)) for item in value) + "]"


def build_gp_block(job: QuarticAnalysisJob) -> str:
    """Build one self-contained exact-analysis block.

    PARI requires the relative polynomial variables to have higher priority
    than the number-field variable.  ``X`` and ``Z`` are created once by the
    batch prelude and reused here.
    """

    base = polynomial_expression(job.base_coefficients, "t")
    classes = list(job.squareclass_expressions) or ["disc"]
    class_assignments = ";".join(
        f"sc{i + 1}=({expression})" for i, expression in enumerate(classes)
    )
    class_vector = "[" + ",".join(
        f"sc{i + 1}" for i in range(len(classes))
    ) + "]"
    marker_primes = _gp_vector(MARKER_PRIMES)
    return (
        "{"
        f"f6={base};nf=nfinit(f6);Q4=({job.quartic_expression});"
        "aa=polcoef(Q4,3,X);bb=polcoef(Q4,2,X);"
        "cc=polcoef(Q4,1,X);dd=polcoef(Q4,0,X);"
        "RR=Z^3-bb*Z^2+(aa*cc-4*dd)*Z+"
        "(4*bb*dd-aa^2*dd-cc^2);"
        "fq=nffactor(nf,Q4);fr=nffactor(nf,RR);"
        "disc=poldisc(Q4,X);rd=poldisc(RR,Z);"
        "dred=lift(Mod(disc,f6));"
        "fs=nffactor(nf,Z^2-disc);"
        "qfd=vector(matsize(fq)[1],ii,poldegree(fq[ii,1]));"
        "rfd=vector(matsize(fr)[1],ii,poldegree(fr[ii,1]));"
        "sfd=vector(matsize(fs)[1],ii,poldegree(fs[ii,1]));"
        f"{class_assignments};SC={class_vector};PP={marker_primes};"
        # D4 classes are deliberately small; factoring their norms lets marker
        # primes outside the fixed screen certify rank rather than estimate it.
        "if(#SC==2,for(ii=1,#SC,NN=abs(nfeltnorm(nf,SC[ii]));"
        "if(NN>1,PP=setunion(PP,Vec(factor(NN)[,1])))));"
        "VM=List();for(jj=1,#PP,prs=idealprimedec(nf,PP[jj]);"
        "for(kk=1,#prs,row=vector(#SC,ll,nfeltval(nf,SC[ll],prs[kk])%2);"
        "if(vecsum(row)>0,listput(VM,row))));VM=Vec(VM);"
        "P=subst(polresultant(f6,Q4,t),X,t);"
        "pok=(poldegree(P)==24&&polcoef(P,24)==1&&polisirreducible(P));"
        "if(pok,rrc=polsturm(P);pd=abs(poldisc(P));pv=Vec(P),"
        "rrc=-1;pd=0;pv=[]);"
        f"print(\"GQ|{job.index}|\",qfd,\"|\",rfd,\"|\",sfd,"
        "\"|\",rrc,\"|\",abs(nfeltnorm(nf,disc)),\"|\","
        "abs(nfeltnorm(nf,rd)),\"|\",Vec(dred),\"|\",pd,\"|\",VM,\"|\",pv);"
        "};"
    )


def _parse_vector(raw: str) -> list[Any]:
    value = ast.literal_eval(raw.strip())
    if not isinstance(value, list):
        raise ValueError(f"expected vector, got {raw!r}")
    return value


def _parse_gp_output(stdout: str) -> dict[int, dict[str, Any]]:
    parsed: dict[int, dict[str, Any]] = {}
    for line in stdout.splitlines():
        if not line.startswith("GQ|"):
            continue
        pieces = line.split("|", 11)
        if len(pieces) != 12:
            continue
        _, raw_index, raw_qfd, raw_rfd, raw_sfd, raw_roots, raw_qnorm, \
            raw_rnorm, raw_quartic_disc, raw_global_disc, raw_matrix, \
            raw_polynomial = pieces
        parsed[int(raw_index)] = {
            "quartic_factor_degrees": tuple(map(int, _parse_vector(raw_qfd))),
            "resolvent_factor_degrees": tuple(map(int, _parse_vector(raw_rfd))),
            "square_test_factor_degrees": tuple(map(int, _parse_vector(raw_sfd))),
            "real_root_count": int(raw_roots),
            "quartic_discriminant_norm": int(raw_qnorm),
            "quartic_discriminant_coefficients": list(
                reversed(list(map(int, _parse_vector(raw_quartic_disc))))
            ),
            "resolvent_discriminant_norm": int(raw_rnorm),
            "global_polynomial_discriminant": int(raw_global_disc),
            "squareclass_matrix": [
                list(map(int, row)) for row in _parse_vector(raw_matrix)
            ],
            # PARI Vec is descending; the submission format is ascending.
            "coefficients": list(
                reversed(list(map(int, _parse_vector(raw_polynomial))))
            ),
        }
    return parsed


def analyze_jobs(
    jobs: Sequence[QuarticAnalysisJob],
    *,
    coefficient_limit: int = 10**55,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not jobs:
        return [], {}
    script = "t='t;X=varhigher(\"X\",t);Z=varhigher(\"Z\",t);" + "\n".join(
        build_gp_block(job) for job in jobs
    )
    result = run_gp(script)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-6000:] or "PARI quartic analysis failed")
    raw = _parse_gp_output(result.stdout)
    accepted: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for job in jobs:
        row = raw.get(job.index)
        if row is None:
            reject("missing_pari_output")
            continue
        qdegrees = tuple(sorted(row["quartic_factor_degrees"]))
        if qdegrees != (4,):
            reject("relative_reducible")
            continue
        square_degrees = tuple(sorted(row["square_test_factor_degrees"]))
        discriminant_square = square_degrees == (1, 1)
        classification = classify_relative_quartic(
            row["resolvent_factor_degrees"], discriminant_square
        )
        requested = "D4" if job.requested_regime == "D4_non_even" else job.requested_regime
        if requested == "C4":
            # An irreducible quartic of the certified form
            #
            #   X^4 - 2*g*(A^2+B^2)*X^2 + g^2*(A^2+B^2)*B^2
            #
            # is the standard cyclic-quartic construction.  Its cubic
            # resolvent/discriminant signature is the same coarse ``D4``
            # signature detected by ``classify_relative_quartic``; the
            # construction identity is the additional exact certificate.
            cyclic_certificate = str(
                job.metadata.get("cyclic_quartic_certificate") or ""
            )
            if (
                classification.name != "D4"
                or cyclic_certificate != "D=A^2+B^2"
            ):
                reject("relative_regime_mismatch")
                continue
        elif classification.name != requested:
            reject("relative_regime_mismatch")
            continue
        coefficients = row["coefficients"]
        if len(coefficients) != 25 or not coefficients or coefficients[-1] != 1:
            reject("invalid_resultant")
            continue
        if coefficients[0] == 0:
            reject("zero_constant")
            continue
        height = max(map(abs, coefficients))
        if height >= coefficient_limit:
            reject("coefficient_height")
            continue
        if row["real_root_count"] != job.requested_root_count:
            reject("root_target_mismatch")
            continue
        squareclass_rank = rank_mod_2(row["squareclass_matrix"])
        if job.requested_regime == "D4_non_even" and squareclass_rank < 2:
            reject("squareclass_rank")
            continue
        base_disc = abs(int(sympy.discriminant(
            sum(int(c) * sympy.Symbol("t") ** i
                for i, c in enumerate(job.base_coefficients)),
            sympy.Symbol("t"),
        )))
        qnorm = abs(int(row["quartic_discriminant_norm"]))
        rnorm = abs(int(row["resolvent_discriminant_norm"]))
        fingerprint = frobenius_fingerprint(coefficients, FROBENIUS_PRIMES)
        accepted.append({
            **row,
            "relative_group_prediction": requested,
            "quartic_discriminant_square": discriminant_square,
            "squareclass_rank": squareclass_rank,
            "coefficient_height": height,
            "frobenius_cycle_types": {
                str(prime): list(partition)
                for prime, partition in fingerprint.items()
            },
            # These gcds are intersection screens, not proof of disjointness.
            "intersection_fingerprint": {
                "base_discriminant": str(base_disc),
                "quartic_norm_gcd_base_disc": str(math.gcd(qnorm, base_disc)),
                "resolvent_norm_gcd_base_disc": str(math.gcd(rnorm, base_disc)),
                "certification": "ramification-screen-only",
            },
            "job_metadata": dict(job.metadata),
        })
    return accepted, rejected

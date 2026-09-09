#!/usr/bin/env sage -python
"""Finite character/norm feasibility gate for the q156 and q210 gold clusters.

This audit is deliberately field-first and finite:

* all locally owned even rows whose exact block quotient is 12T156 or 12T210
  are filtered to genuinely totally-real degree-12 quotients and deduplicated
  by PARI polredabs;
* each field gets its own full ramified-prime character alignment (cores are
  not assumed to be universal across field realizations);
* a cheap local residue-degree/signature parity gate runs first;
* exact K(S,2) Selmer/sign images are computed only for locally passing
  field/core/target triples.

There are no coefficient boxes, random choices, network calls, submissions,
candidate staging operations, or manifest writes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import (
    GF,
    Matrix,
    NumberField,
    PolynomialRing,
    QQ,
    RealIntervalField,
    ZZ,
    proof,
    vector,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_INVENTORY = DATA / "character_tr_field_inventory_20260730.jsonl"
DEFAULT_OUTPUT = DATA / "character_field_gate_q156_q210_tr_20260730.json"

FAMILIES = {
    156: {
        "targetLabels": ["24T21371", "24T21375"],
        "priorCoreEvidence": [3, 435],
    },
    210: {
        "targetLabels": ["24T22544", "24T22548"],
        "priorSignedCoreEvidenceOnNonTotallyRealField": [-799, -1598],
        "priorCoreEvidenceProvenance": (
            "agent_f8_historical_jump_lineage exact trace-zero q(x^2) "
            "signed norm cores"
        ),
    },
}


def load_character_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("character_gate_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_character_helper()


def load_alignment_worker():
    path = ROOT / "agent_gold_c_character_census_worker.sage.py"
    spec = importlib.util.spec_from_file_location("character_gate_alignment", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import alignment worker from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ALIGNMENT = load_alignment_worker()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def coefficient_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def live_gold_pairs(db: Path, target_labels: set[str]) -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _label in target_labels)
        rows = connection.execute(
            f"""
            SELECT t.label,t.r,t.team_count,t.generated_at,
                   CASE WHEN b.label IS NULL THEN 0 ELSE 1 END,
                   CASE WHEN owned.label IS NULL THEN 0 ELSE 1 END
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label IN ({placeholders})
              AND t.team_count=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.label,t.r
            """,
            tuple(sorted(target_labels)),
        ).fetchall()
    finally:
        connection.close()
    output: dict[str, list[dict]] = defaultdict(list)
    for label, r, team_count, generated_at, baseline, owned in rows:
        output[str(label)].append(
            {
                "baseline": bool(baseline),
                "generatedAt": str(generated_at) if generated_at else None,
                "locallyOwned": bool(owned),
                "r": int(r),
                "teamCount": int(team_count),
            }
        )
    return dict(output)


def load_totally_real_inventory(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    output = [
        {
            **row,
            "family": f"12T{int(row['quotientT12'])}",
            "provenance": (
                "data/character_tr_field_inventory_20260730.jsonl: accepted "
                "scoreable even q(x^2), exact 12x2 action, q real-root count "
                "12, PARI polredabs deduplication"
            ),
        }
        for row in rows
        if int(row["quotientT12"]) in FAMILIES
    ]
    counts = {
        quotient_t: sum(row["quotientT12"] == quotient_t for row in output)
        for quotient_t in FAMILIES
    }
    expected = {156: 3, 210: 3}
    if counts != expected:
        raise ValueError(
            f"totally-real canonical inventory mismatch: {counts}, expected {expected}"
        )
    return sorted(
        output,
        key=lambda row: (row["quotientT12"], row["fieldCanonicalSha256"]),
    )


def sign_mask(field, element, embeddings) -> int:
    precision = 160
    while precision <= 1280:
        current = embeddings
        if precision != 160:
            interval_field = RealIntervalField(precision)
            current = field.embeddings(interval_field)
        values = [embedding(element) for embedding in current]
        if all(not value.contains_zero() for value in values):
            return sum(
                1 << index for index, value in enumerate(values) if value < 0
            )
        precision *= 2
    raise ArithmeticError("could not certify the sign of a nonzero Selmer element")


def state_matrix(field, representatives, embeddings, rational_primes):
    columns = []
    for representative in representatives:
        mask = sign_mask(field, representative, embeddings)
        norm = QQ(representative.norm())
        columns.append(
            [
                *[(mask >> index) & 1 for index in range(len(embeddings))],
                *[
                    int(norm.valuation(prime)) & 1
                    for prime in rational_primes
                ],
            ]
        )
    return Matrix(
        GF(2),
        len(embeddings) + len(rational_primes),
        len(representatives),
        lambda row, column: columns[column][row],
    )


def local_core_gate(field, core: int) -> dict:
    core = ZZ(core)
    rational_primes = [int(value) for value in abs(core).prime_divisors()]
    prime_rows = []
    norm_parity_possible = True
    for prime in rational_primes:
        ideals = list(field.primes_above(prime))
        residue_degrees = [int(ideal.residue_class_degree()) for ideal in ideals]
        desired = int(core.valuation(prime)) & 1
        possible = desired == 0 or any(value & 1 for value in residue_degrees)
        norm_parity_possible &= possible
        prime_rows.append(
            {
                "desiredRationalNormValuationParity": desired,
                "prime": prime,
                "primeIdealCount": len(ideals),
                "residueDegrees": residue_degrees,
                "valuationParityLocallyPossible": possible,
            }
        )
    return {
        "normSign": -1 if core < 0 else 1,
        "rationalNormParityLocallyPossible": bool(norm_parity_possible),
        "rationalPrimeSupport": rational_primes,
        "rationalPrimeTests": prime_rows,
    }


def target_vector(real_count, rational_primes, core: int, sign_indexes):
    sign_indexes = set(sign_indexes)
    return vector(
        GF(2),
        [
            *[int(index in sign_indexes) for index in range(real_count)],
            *[
                int(ZZ(core).valuation(prime)) & 1
                for prime in rational_primes
            ],
        ],
    )


def exact_selmer_sign_gate(
    field,
    quotient,
    cores_to_pairs: dict[int, list[dict]],
    certify_passers: bool,
    witness_primes: int,
    max_reconstructions_per_pair: int = 1,
    max_coset_reconstructions_per_sign: int = 1,
    coset_mask_start: int = 0,
    coset_mask_stride: int = 1,
    coset_mask_schedule: list[int] | None = None,
) -> dict:
    rational_primes = sorted(
        {
            int(prime)
            for core in cores_to_pairs
            for prime in abs(ZZ(core)).prime_divisors()
        }
    )
    prime_ideals = [
        ideal
        for prime in rational_primes
        for ideal in field.primes_above(prime)
    ]
    print(
        json.dumps(
            {
                "event": "selmer_start",
                "rationalPrimes": rational_primes,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    # Sage's principal-ideal reduction otherwise restores the global
    # proof=True default inside selmer_space and can enter an unbounded
    # bnfcertify call despite the explicit proof=False below.  This gate uses
    # PARI's GRH-conditional class-group result and records that status.
    proof.number_field(False)
    selmer, _generators, from_selmer, _to_selmer = field.selmer_space(
        prime_ideals, ZZ(2), proof=False
    )
    selmer_representatives = [
        from_selmer(basis_vector) for basis_vector in selmer.basis()
    ]
    embeddings = field.embeddings(RealIntervalField(160))
    selmer_matrix = state_matrix(
        field, selmer_representatives, embeddings, rational_primes
    )
    selmer_image = selmer_matrix.column_space()
    selmer_state_kernel_basis = list(
        selmer_matrix.right_kernel().basis()
    )
    left_kernel = [
        [int(value) for value in relation]
        for relation in selmer_matrix.left_kernel().basis()
    ]
    print(
        json.dumps(
            {
                "event": "selmer_ready",
                "selmerDimension": int(selmer.dimension()),
                "stateImageDimension": int(selmer_matrix.rank()),
                "stateImageLeftKernel": left_kernel,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    certificate_profiles = {}
    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()

    def reconstruct(core: int, pair: dict, target, indexes, solution=None) -> dict:
        if solution is None:
            solution = selmer_matrix.solve_right(target)
        selected = [
            index for index, bit in enumerate(solution) if int(bit)
        ]
        alpha = field.one()
        for index in selected:
            alpha *= selmer_representatives[index]
        scaled, square_scale = HELPER.SHARED.integral_square_scale(alpha)
        exact_norm = QQ(scaled.norm())
        mask = sign_mask(field, scaled, embeddings)
        negative_count = mask.bit_count()
        result = {
            "alphaCoordinatesInCanonicalPowerBasis": [
                str(value) for value in alpha.list()
            ],
            "alphaExactNorm": str(QQ(alpha.norm())),
            "alphaRepresentative": str(alpha),
            "integralScaledAlphaCoordinatesInCanonicalPowerBasis": [
                str(value) for value in scaled.list()
            ],
            "integralScaledAlphaExactNorm": str(exact_norm),
            "integralSquareScale": str(square_scale),
            "negativeRealEmbeddings": negative_count,
            "normOverCoreIsSquare": bool(
                HELPER.SHARED.rational_is_square(exact_norm / ZZ(core))
            ),
            "selectedSelmerBasisIndexes": selected,
            "selmerSolutionVector": [int(value) for value in solution],
            "signMask": mask,
            "targetSignIndexes": [int(value) for value in indexes],
        }
        minimal = scaled.minpoly().change_ring(QQ)
        integral_minpoly = (
            minimal.degree() == 12
            and all(QQ(value).denominator() == 1 for value in minimal)
        )
        result["primitiveIntegralDegree12Minpoly"] = bool(integral_minpoly)
        if not integral_minpoly:
            result["candidateStatus"] = "nonprimitive_or_nonintegral_minpoly"
            return result
        minimal_zz = candidate_ring([ZZ(value) for value in minimal.list()])
        candidate = candidate_ring(minimal_zz(x**2))
        candidate_line = coefficient_line(candidate)
        result.update(
            {
                "candidateCoefficientLine": candidate_line,
                "candidateIrreducible": bool(candidate.is_irreducible()),
                "candidateRealRoots": int(candidate.number_of_real_roots()),
                "candidateSha256": hashlib.sha256(
                    candidate_line.encode()
                ).hexdigest(),
                "minimalPolynomial": str(minimal_zz),
            }
        )
        exact_candidate = (
            result["normOverCoreIsSquare"]
            and negative_count == len(embeddings) - int(pair["r"]) // 2
            and result["candidateIrreducible"]
            and result["candidateRealRoots"] == int(pair["r"])
        )
        if not exact_candidate:
            result["candidateStatus"] = "failed_exact_candidate_checks"
            return result
        result["candidateStatus"] = "exact_contained_candidate"
        if not certify_passers:
            return result
        target_label = str(pair["label"])
        target_t = int(target_label[3:])
        if target_label not in certificate_profiles:
            HELPER.SHARED.TARGET_T = target_t
            HELPER.SHARED.TARGET_LABEL = target_label
            certificate_profiles[target_label] = (
                HELPER.SHARED.maximal_joint_profiles()
            )
        profiles, identities = certificate_profiles[target_label]
        certificate = HELPER.SHARED.frobenius_maximal_certificate(
            candidate,
            quotient,
            profiles,
            identities,
            witness_primes,
        )
        result["maximalSubgroupCertificate"] = certificate
        result["candidateStatus"] = (
            f"certified_{target_label}_r{int(pair['r'])}"
            if certificate["complete"]
            else "contained_candidate_incomplete_maximal_certificate"
        )
        return result

    rows = []
    for core, pairs in sorted(cores_to_pairs.items()):
        for pair in sorted(pairs, key=lambda row: (row["label"], row["r"])):
            requested_r = int(pair["r"])
            negative_count = len(embeddings) - requested_r // 2
            sign_count = 0
            sample_masks = []
            reconstructions = []
            certified_reconstruction = None
            if 0 <= negative_count <= len(embeddings):
                for indexes in itertools.combinations(
                    range(len(embeddings)), negative_count
                ):
                    target = target_vector(
                        len(embeddings), rational_primes, core, indexes
                    )
                    if target in selmer_image:
                        sign_count += 1
                        if len(sample_masks) < 8:
                            sample_masks.append(
                                sum(1 << index for index in indexes)
                            )
                        if certified_reconstruction is None:
                            base_solution = selmer_matrix.solve_right(target)
                            kernel_dimension = len(
                                selmer_state_kernel_basis
                            )
                            total_cosets = 1 << kernel_dimension
                            coset_start = min(
                                max(0, int(coset_mask_start)),
                                total_cosets,
                            )
                            coset_stride = max(
                                1,
                                int(coset_mask_stride),
                            )
                            if coset_mask_schedule is None:
                                candidate_coset_masks = range(
                                    coset_start,
                                    total_cosets,
                                    coset_stride,
                                )
                            else:
                                candidate_coset_masks = (
                                    mask
                                    for mask in dict.fromkeys(
                                        int(value)
                                        for value in coset_mask_schedule
                                    )
                                    if 0 <= mask < total_cosets
                                )
                            coset_masks = itertools.islice(
                                candidate_coset_masks,
                                max_coset_reconstructions_per_sign,
                            )
                            for coset_mask in coset_masks:
                                if (
                                    len(reconstructions)
                                    >= max_reconstructions_per_pair
                                ):
                                    break
                                solution = vector(GF(2), base_solution)
                                for kernel_index, kernel_vector in enumerate(
                                    selmer_state_kernel_basis
                                ):
                                    if (coset_mask >> kernel_index) & 1:
                                        solution += kernel_vector
                                try:
                                    reconstruction = reconstruct(
                                        core,
                                        pair,
                                        target,
                                        indexes,
                                        solution,
                                    )
                                except Exception as exc:
                                    reconstruction = {
                                        "candidateStatus":
                                            "reconstruction_error",
                                        "error":
                                            f"{type(exc).__name__}: {exc}",
                                        "selmerCosetMask": coset_mask,
                                        "targetSignIndexes": [
                                            int(value) for value in indexes
                                        ],
                                    }
                                reconstruction["selmerCosetMask"] = (
                                    coset_mask
                                )
                                reconstructions.append(reconstruction)
                                if str(
                                    reconstruction.get(
                                        "candidateStatus", ""
                                    )
                                ).startswith("certified_"):
                                    certified_reconstruction = (
                                        reconstruction
                                    )
                                    break
            rows.append(
                {
                    **pair,
                    "certifiedExactSelmerReconstruction":
                        certified_reconstruction,
                    "exactSelmerSolvableSignMasks": sign_count,
                    "firstExactSelmerReconstruction": (
                        reconstructions[0] if reconstructions else None
                    ),
                    "sampleExactSelmerSignMasks": sample_masks,
                    "status": (
                        "exact_selmer_sign_pass"
                        if sign_count
                        else "exact_selmer_sign_obstruction"
                    ),
                    "testedExactSelmerReconstructions": reconstructions,
                }
            )
    return {
        "conditionalOnGRH": True,
        "matrix": {
            "realEmbeddings": len(embeddings),
            "rationalPrimes": rational_primes,
            "rows": int(selmer_matrix.nrows()),
            "selmerColumns": int(selmer_matrix.ncols()),
            "selmerStateImageDimension": int(selmer_matrix.rank()),
            "selmerStateKernelDimension": len(
                selmer_state_kernel_basis
            ),
            "stateImageLeftKernel": left_kernel,
        },
        "pairs": rows,
        "reconstruction": {
            "cosetMaskSchedule": (
                [int(value) for value in coset_mask_schedule]
                if coset_mask_schedule is not None
                else None
            ),
            "cosetMaskStart": int(coset_mask_start),
            "cosetMaskStride": int(coset_mask_stride),
            "maxCosetReconstructionsPerSign":
                int(max_coset_reconstructions_per_sign),
            "maxReconstructionsPerPair":
                int(max_reconstructions_per_pair),
        },
        "selmer": {
            "dimension": int(selmer.dimension()),
            "primeIdealSupportCount": len(prime_ideals),
        },
    }


def audit_field(
    field_row: dict,
    ring,
    live: dict[str, list[dict]],
    local_only: bool,
    certify_passers: bool,
    witness_primes: int,
    max_reconstructions_per_pair: int = 1,
    max_coset_reconstructions_per_sign: int = 1,
    coset_mask_start: int = 0,
    coset_mask_stride: int = 1,
    coset_mask_schedule: list[int] | None = None,
) -> dict:
    quotient_t = int(field_row["quotientT12"])
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    if quotient.degree() != 12 or not quotient.is_irreducible():
        raise ValueError("canonical quotient is not irreducible degree 12")
    field = NumberField(quotient.change_ring(QQ), "a")
    signature = tuple(int(value) for value in field.signature())
    real_count = signature[0]
    family = FAMILIES[quotient_t]
    alignment = ALIGNMENT.exact_linear_character_alignment(
        quotient, quotient_t
    )
    ambiguous_target_labels = set(
        family.get("ambiguousTargetLabels", [])
    )
    target_cores = {
        label: sorted(
            {
                int(core)
                for core, possible_labels in alignment[
                    "coreToPossibleLabels"
                ].items()
                if (
                    label in ambiguous_target_labels
                    and label in possible_labels
                )
            }
            if label in ambiguous_target_labels
            else {
                int(value)
                for value in alignment[
                    "labelToUnambiguousSquarefreeNormCores"
                ].get(label, [])
            }
        )
        for label in family["targetLabels"]
    }

    local_rows = []
    selmer_work: dict[int, list[dict]] = defaultdict(list)
    core_gates = {}
    for target_label, cores in target_cores.items():
        for core in cores:
            if core not in core_gates:
                core_gates[core] = local_core_gate(field, core)
            possible_labels = alignment["coreToPossibleLabels"].get(str(core), [])
            exact_character_match = (
                possible_labels == [target_label]
                or (
                    target_label in ambiguous_target_labels
                    and target_label in possible_labels
                )
            )
            for live_pair in live.get(target_label, []):
                requested_r = int(live_pair["r"])
                negative_count = real_count - requested_r // 2
                signature_bound = 0 <= negative_count <= real_count
                sign_matches_core = (
                    signature_bound
                    and ((negative_count & 1) == int(core < 0))
                )
                local_pass = (
                    exact_character_match
                    and core_gates[core]["rationalNormParityLocallyPossible"]
                    and signature_bound
                    and sign_matches_core
                )
                row = {
                    "core": int(core),
                    "exactCharacterMatch": exact_character_match,
                    "finalGroupDisambiguationRequired":
                        len(possible_labels) > 1,
                    "label": target_label,
                    "localPass": local_pass,
                    "negativeRealEmbeddingsRequired": negative_count,
                    "possibleLabelsForCore": possible_labels,
                    "r": requested_r,
                    "realSignatureBoundPass": signature_bound,
                    "signedNormParityPass": sign_matches_core,
                    "teamCount": int(live_pair["teamCount"]),
                }
                if local_pass:
                    selmer_work[int(core)].append(row)
                local_rows.append(row)

    result = {
        **field_row,
        "alignment": {
            "coreToPossibleLabels": alignment["coreToPossibleLabels"],
            "method": alignment["method"],
            "quotientOrder": alignment["quotientOrder"],
            "quotientT": alignment["quotientT"],
            "ramifiedPrimes": alignment["ramifiedPrimes"],
            "targetNormCores": target_cores,
        },
        "degree": int(field.degree()),
        "discriminantAbs": str(abs(ZZ(field.discriminant()))),
        "localCoreGates": {
            str(core): row for core, row in sorted(core_gates.items())
        },
        "localPairGates": local_rows,
        "realSignature": {
            "complexPlaces": signature[1],
            "realEmbeddings": real_count,
        },
    }
    if selmer_work and not local_only:
        result["exactSelmerSignGate"] = exact_selmer_sign_gate(
            field,
            quotient,
            dict(selmer_work),
            certify_passers,
            witness_primes,
            max_reconstructions_per_pair,
            max_coset_reconstructions_per_sign,
            coset_mask_start,
            coset_mask_stride,
            coset_mask_schedule,
        )
    elif selmer_work:
        result["exactSelmerSignGate"] = {
            "locallyPassingCores": sorted(selmer_work),
            "pairs": [],
            "status": "deferred_by_local_only",
        }
    else:
        result["exactSelmerSignGate"] = {
            "pairs": [],
            "status": "skipped_no_local_passers",
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--certify-passers", action="store_true")
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--quotient-t", type=int, choices=sorted(FAMILIES))
    parser.add_argument("--field-hash", action="append", default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite the character field gate artifact")

    ring = PolynomialRing(QQ, "y")
    target_labels = {
        label
        for family in FAMILIES.values()
        for label in family["targetLabels"]
    }
    live = live_gold_pairs(args.db, target_labels)
    fields = load_totally_real_inventory(args.inventory)
    inventory_counts = {
        quotient_t: sum(field["quotientT12"] == quotient_t for field in fields)
        for quotient_t in FAMILIES
    }
    if args.quotient_t is not None:
        fields = [
            field for field in fields
            if field["quotientT12"] == args.quotient_t
        ]
    requested_hashes = set(args.field_hash)
    if requested_hashes:
        fields = [
            field for field in fields
            if field["fieldCanonicalSha256"] in requested_hashes
        ]
        missing = requested_hashes - {
            field["fieldCanonicalSha256"] for field in fields
        }
        if missing:
            raise ValueError(f"requested canonical fields are absent: {sorted(missing)}")
    rows = []
    for field_row in fields:
        result = audit_field(
            field_row,
            ring,
            live,
            args.local_only,
            args.certify_passers,
            args.witness_primes,
        )
        rows.append(result)
        exact_pairs = result["exactSelmerSignGate"].get("pairs", [])
        print(
            json.dumps(
                {
                    "event": "field_audited",
                    "exactPassers": sum(
                        row["status"] == "exact_selmer_sign_pass"
                        for row in exact_pairs
                    ),
                    "fieldCanonicalSha256": result["fieldCanonicalSha256"],
                    "localPassers": sum(
                        row["localPass"] for row in result["localPairGates"]
                    ),
                    "quotientT12": result["quotientT12"],
                    "realSignature": result["realSignature"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    exact_passing_pairs = [
        {
            "core": pair["core"],
            "fieldCanonicalSha256": field["fieldCanonicalSha256"],
            "label": pair["label"],
            "r": pair["r"],
            "solvableSignMasks": pair["exactSelmerSolvableSignMasks"],
        }
        for field in rows
        for pair in field["exactSelmerSignGate"].get("pairs", [])
        if pair["status"] == "exact_selmer_sign_pass"
    ]
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "certifyPassers": bool(args.certify_passers),
            "inventoryCanonicalFields": inventory_counts,
            "localOnly": bool(args.local_only),
            "networkCalls": 0,
            "submissionCalls": 0,
            "witnessPrimeCount": int(args.witness_primes),
            "targetSnapshotGeneratedAt": sorted(
                {
                    row["generatedAt"]
                    for values in live.values()
                    for row in values
                    if row["generatedAt"]
                }
            ),
        },
        "families": {
            str(quotient_t): family for quotient_t, family in FAMILIES.items()
        },
        "fields": rows,
        "livePairs": live,
        "summary": {
            "canonicalFields": len(rows),
            "exactPassingPairFieldRoutes": len(exact_passing_pairs),
            "exactPassingPairs": exact_passing_pairs,
            "localPassingPairFieldRoutes": sum(
                pair["localPass"]
                for field in rows
                for pair in field["localPairGates"]
            ),
            "q156AuditedFields": sum(
                field["quotientT12"] == 156 for field in rows
            ),
            "q210AuditedFields": sum(
                field["quotientT12"] == 210 for field in rows
            ),
        },
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

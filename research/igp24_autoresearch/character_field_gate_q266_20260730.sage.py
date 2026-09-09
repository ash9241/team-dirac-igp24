#!/usr/bin/env sage -python
"""Exact field-first character gate for live q266 targets 23815 and 23818.

The accepted ledger is reduced to its two canonical totally-real q266 quotient
fields.  Every target-associated norm core, including cores shared by multiple
24T labels, is locally screened before a GRH-conditional K(S,2) computation.
Any reconstructed candidate must pass exact irreducibility/sign checks,
Frobenius ambiguity classification, and a maximal-subgroup certificate for its
claimed target label.

No coefficient grid, random search, network call, staging, or submission is
performed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import (
    NumberField,
    PolynomialRing,
    QQ,
    ZZ,
    libgap,
    pari,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
QUOTIENT_T = 266
TARGET_LABELS = ("24T23815", "24T23818")
EXPECTED_LIVE_R = (8, 12, 16, 20, 24)


def load_gate():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("q266_shared_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import shared gate from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_gate()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def q266_source_labels(action_map: Path) -> list[str]:
    labels = set()
    for line in action_map.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if any(
            int(system.get("blockActionT12", -1)) == QUOTIENT_T
            for system in row.get("systems", [])
        ):
            labels.add(str(row["sourceLabel"]))
    if not labels:
        raise ValueError("the exact action map contains no q266 source labels")
    return sorted(labels)


def recover_inventory(db: Path, action_map: Path, ring) -> tuple[list[dict], dict]:
    source_labels = q266_source_labels(action_map)
    placeholders = ",".join("?" for _label in source_labels)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT v.label,v.r,v.submission_id,v.polynomial_index,
                   p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.status='accepted' AND v.scoreable=1
              AND v.label IN ({placeholders})
            ORDER BY length(p.coefficients),p.coefficient_hash
            """,
            tuple(source_labels),
        ).fetchall()
    finally:
        connection.close()

    fields = {}
    accepted_even_irreducible = 0
    totally_real_rows = 0
    for label, r, submission_id, polynomial_index, source_hash, text in rows:
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        accepted_even_irreducible += 1
        if int(quotient.number_of_real_roots()) != 12:
            continue
        totally_real_rows += 1
        reduced = ring(pari(quotient).polredabs())
        line = GATE.coefficient_line(reduced)
        field_hash = hashlib.sha256(line.encode()).hexdigest()
        item = fields.setdefault(
            field_hash,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": field_hash,
                "quotientT12": QUOTIENT_T,
                "sourceRows": [],
            },
        )
        item["sourceRows"].append(
            {
                "coefficientSha256": str(source_hash),
                "label": str(label),
                "polynomialIndex": int(polynomial_index),
                "r": int(r),
                "submissionId": str(submission_id),
            }
        )

    output = [fields[key] for key in sorted(fields)]
    if len(output) != 2:
        raise ValueError(
            f"q266 totally-real canonical inventory has {len(output)} fields, "
            "expected 2"
        )
    for row in output:
        row["sourceLabels"] = sorted(
            {source["label"] for source in row["sourceRows"]}
        )
        row["sourceSignatures"] = sorted(
            {source["r"] for source in row["sourceRows"]}
        )
    return output, {
        "acceptedEvenIrreducibleRows": accepted_even_irreducible,
        "sourceLabels": source_labels,
        "totallyRealSourceRows": totally_real_rows,
    }


def joint_profiles_for_label(label: str) -> set[tuple[tuple[int, ...], tuple[int, ...]]]:
    target_t = int(label[3:])
    group = libgap.TransitiveGroup(24, target_t)
    points_24 = libgap.eval("[1..24]")
    points_12 = libgap.eval("[1..12]")
    quotient_action = None
    for block in libgap.AllBlocks(group):
        if int(libgap.Length(block)) != 2:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
        quotient = libgap.Image(action)
        if (
            int(libgap.DegreeAction(quotient)) == 12
            and int(libgap.TransitiveIdentification(quotient)) == QUOTIENT_T
        ):
            quotient_action = action
            break
    if quotient_action is None:
        raise ValueError(f"{label} has no exact 12T266 block action")
    profiles = set()
    for conjugacy_class in libgap.ConjugacyClasses(group):
        representative = libgap.Representative(conjugacy_class)
        action_24 = tuple(
            sorted(
                int(value)
                for value in libgap.CycleLengths(representative, points_24)
            )
        )
        action_12 = tuple(
            sorted(
                int(value)
                for value in libgap.CycleLengths(
                    libgap.Image(quotient_action, representative),
                    points_12,
                )
            )
        )
        profiles.add((action_24, action_12))
    return profiles


def classify_ambiguous_candidate(
    candidate,
    quotient,
    possible_labels: list[str],
    profile_cache: dict[str, set],
    witness_primes: int,
) -> dict:
    if len(possible_labels) == 1:
        return {
            "checkedSquarefreePrimes": 0,
            "complete": True,
            "exclusionWitnesses": {possible_labels[0]: None},
            "method": "unambiguous_exact_norm_core_alignment",
            "possibleLabels": possible_labels,
            "survivingLabels": possible_labels,
        }
    for label in possible_labels:
        if label not in profile_cache:
            profile_cache[label] = joint_profiles_for_label(label)
    excluded = {label: None for label in possible_labels}
    checked = 0
    for prime_value in GATE.HELPER.SHARED.primes_first_n(witness_primes):
        prime = int(prime_value)
        candidate_type = GATE.HELPER.SHARED.factor_degrees(candidate, prime)
        quotient_type = GATE.HELPER.SHARED.factor_degrees(quotient, prime)
        if candidate_type is None or quotient_type is None:
            continue
        checked += 1
        joint_profile = (candidate_type, quotient_type)
        for label in possible_labels:
            if (
                excluded[label] is None
                and joint_profile not in profile_cache[label]
            ):
                excluded[label] = {
                    "candidateCycleType": list(candidate_type),
                    "prime": prime,
                    "quotientCycleType": list(quotient_type),
                }
        survivors = [
            label for label in possible_labels if excluded[label] is None
        ]
        if len(survivors) <= 1:
            break
    survivors = [label for label in possible_labels if excluded[label] is None]
    return {
        "checkedSquarefreePrimes": checked,
        "complete": len(survivors) == 1,
        "exclusionWitnesses": excluded,
        "method": "joint_frobenius_profile_exclusion",
        "possibleLabels": possible_labels,
        "survivingLabels": survivors,
    }


def certify_reconstructions(
    exact: dict,
    quotient,
    core_to_possible_labels: dict[int, list[str]],
    witness_primes: int,
) -> None:
    polynomial_ring = PolynomialRing(ZZ, "x")
    profile_cache = {}
    maximal_cache = {}
    for pair in exact.get("pairs", []):
        reconstruction = pair.get("firstExactSelmerReconstruction")
        if (
            not reconstruction
            or reconstruction.get("candidateStatus")
            != "exact_contained_candidate"
        ):
            continue
        candidate = polynomial_ring(
            [
                ZZ(value)
                for value in reconstruction["candidateCoefficientLine"].split(",")
            ]
        )
        target_label = str(pair["label"])
        possible_labels = core_to_possible_labels[int(pair["core"])]
        classification = classify_ambiguous_candidate(
            candidate,
            quotient,
            possible_labels,
            profile_cache,
            witness_primes,
        )
        reconstruction["characterAmbiguityClassification"] = classification
        if classification["survivingLabels"] != [target_label]:
            reconstruction["candidateStatus"] = (
                "classified_other_label"
                if classification["complete"]
                else "character_classification_incomplete"
            )
            continue
        if target_label not in maximal_cache:
            GATE.HELPER.SHARED.TARGET_T = int(target_label[3:])
            GATE.HELPER.SHARED.TARGET_LABEL = target_label
            maximal_cache[target_label] = (
                GATE.HELPER.SHARED.maximal_joint_profiles()
            )
        profiles, identities = maximal_cache[target_label]
        certificate = GATE.HELPER.SHARED.frobenius_maximal_certificate(
            candidate,
            quotient,
            profiles,
            identities,
            witness_primes,
        )
        reconstruction["maximalSubgroupCertificate"] = certificate
        reconstruction["candidateStatus"] = (
            f"certified_{target_label}_r{int(pair['r'])}"
            if certificate["complete"]
            else "contained_candidate_incomplete_maximal_certificate"
        )


def audit_field(
    field_row: dict,
    ring,
    live: dict[str, list[dict]],
    witness_primes: int,
    local_only: bool,
) -> dict:
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    field = NumberField(quotient, "a")
    if tuple(int(value) for value in field.signature()) != (12, 0):
        raise ValueError("q266 canonical field is not totally real")
    alignment = GATE.ALIGNMENT.exact_linear_character_alignment(
        quotient, QUOTIENT_T
    )
    target_cores = {
        label: [
            int(core)
            for core in alignment["labelToSquarefreeNormCores"].get(label, [])
        ]
        for label in TARGET_LABELS
    }

    local_core_gates = {}
    local_pairs = []
    exact_work = defaultdict(list)
    core_to_possible_labels = {}
    for target_label, cores in target_cores.items():
        for core in cores:
            possible_labels = alignment["coreToPossibleLabels"][str(core)]
            core_to_possible_labels[core] = possible_labels
            if core not in local_core_gates:
                local_core_gates[core] = GATE.local_core_gate(field, core)
            unambiguous = possible_labels == [target_label]
            for live_pair in live[target_label]:
                requested_r = int(live_pair["r"])
                negative_count = 12 - requested_r // 2
                signature_bound = 0 <= negative_count <= 12
                signed_norm_parity = (
                    signature_bound
                    and ((negative_count & 1) == int(core < 0))
                )
                local_pass = (
                    local_core_gates[core][
                        "rationalNormParityLocallyPossible"
                    ]
                    and signature_bound
                    and signed_norm_parity
                )
                row = {
                    "characterClassificationRequired": not unambiguous,
                    "core": core,
                    "exactCharacterMatch": unambiguous,
                    "label": target_label,
                    "localPass": bool(local_pass),
                    "negativeRealEmbeddingsRequired": negative_count,
                    "possibleLabelsForCore": possible_labels,
                    "r": requested_r,
                    "realSignatureBoundPass": signature_bound,
                    "signedNormParityPass": signed_norm_parity,
                    "teamCount": int(live_pair["teamCount"]),
                }
                local_pairs.append(row)
                if local_pass:
                    exact_work[core].append(row)

    if exact_work and not local_only:
        exact = GATE.exact_selmer_sign_gate(
            field,
            quotient,
            dict(exact_work),
            False,
            witness_primes,
        )
        certify_reconstructions(
            exact,
            quotient,
            core_to_possible_labels,
            witness_primes,
        )
    elif exact_work:
        exact = {
            "locallyPassingCores": sorted(exact_work),
            "pairs": [],
            "status": "deferred_by_local_only",
        }
    else:
        exact = {"pairs": [], "status": "skipped_no_local_passers"}
    return {
        **field_row,
        "alignment": {
            "coreToPossibleLabels": alignment["coreToPossibleLabels"],
            "labelToSquarefreeNormCores": {
                label: alignment["labelToSquarefreeNormCores"].get(label, [])
                for label in TARGET_LABELS
            },
            "labelToUnambiguousSquarefreeNormCores": {
                label: alignment[
                    "labelToUnambiguousSquarefreeNormCores"
                ].get(label, [])
                for label in TARGET_LABELS
            },
            "method": alignment["method"],
            "quotientOrder": alignment["quotientOrder"],
            "quotientT": alignment["quotientT"],
            "ramifiedPrimes": alignment["ramifiedPrimes"],
        },
        "exactSelmerAndClassificationGate": exact,
        "localCoreGates": {
            str(core): gate for core, gate in sorted(local_core_gates.items())
        },
        "localPairGates": local_pairs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATA / "ledger.sqlite3")
    parser.add_argument(
        "--action-map",
        type=Path,
        default=DATA / "agent_gold_b_even_twist_action_map.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA / "character_field_gate_q266_20260730.json",
    )
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--witness-primes", type=int, default=1000)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to overwrite the q266 gate artifact")

    ring = PolynomialRing(QQ, "y")
    inventory, inventory_audit = recover_inventory(
        args.db, args.action_map, ring
    )
    live = GATE.live_gold_pairs(args.db, set(TARGET_LABELS))
    for label in TARGET_LABELS:
        live_r = tuple(row["r"] for row in live.get(label, []))
        if live_r != EXPECTED_LIVE_R:
            raise ValueError(f"unexpected live list for {label}: {live_r}")

    fields = []
    for field_row in inventory:
        result = audit_field(
            field_row,
            ring,
            live,
            args.witness_primes,
            args.local_only,
        )
        fields.append(result)
        exact_pairs = result["exactSelmerAndClassificationGate"].get(
            "pairs", []
        )
        print(
            json.dumps(
                {
                    "event": "q266_field_audited",
                    "exactSelmerPassers": sum(
                        pair["status"] == "exact_selmer_sign_pass"
                        for pair in exact_pairs
                    ),
                    "fieldCanonicalSha256": result[
                        "fieldCanonicalSha256"
                    ],
                    "localPassers": sum(
                        pair["localPass"]
                        for pair in result["localPairGates"]
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    exact_pairs = [
        pair
        for field in fields
        for pair in field["exactSelmerAndClassificationGate"].get("pairs", [])
    ]
    reconstructions = [
        pair["firstExactSelmerReconstruction"]
        for pair in exact_pairs
        if pair.get("firstExactSelmerReconstruction")
    ]
    certified = [
        reconstruction
        for reconstruction in reconstructions
        if str(reconstruction.get("candidateStatus", "")).startswith(
            "certified_"
        )
    ]
    payload = {
        "audit": {
            **inventory_audit,
            "coefficientSearches": 0,
            "localOnly": bool(args.local_only),
            "networkCalls": 0,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    row["generatedAt"]
                    for values in live.values()
                    for row in values
                    if row["generatedAt"]
                }
            ),
            "witnessPrimeCount": args.witness_primes,
        },
        "fields": fields,
        "livePairs": live,
        "summary": {
            "canonicalFields": len(fields),
            "certifiedCandidates": len(certified),
            "exactSelmerPassingPairFieldRoutes": sum(
                pair["status"] == "exact_selmer_sign_pass"
                for pair in exact_pairs
            ),
            "localPassingPairFieldRoutes": sum(
                pair["localPass"]
                for field in fields
                for pair in field["localPairGates"]
            ),
            "targetCoreRoutes": sum(
                len(field["alignment"]["labelToSquarefreeNormCores"][label])
                for field in fields
                for label in TARGET_LABELS
            ),
        },
        "targets": list(TARGET_LABELS),
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

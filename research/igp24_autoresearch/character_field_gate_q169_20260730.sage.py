#!/usr/bin/env sage -python
"""Finite exact character gate for the full-rank q12T169 cluster.

This driver inventories the single accepted totally-real q169 quotient field,
retains the complete GF(2) core-to-character incidence (including shared
cores), applies cheap rational-prime valuation and real-sign parity gates, and
runs exact K(S,2) only for unambiguously aligned local passers.  Exact passers
are reconstructed as P_alpha(x) and certified by irreducibility, exact real
root count, and a complete maximal-subgroup/Frobenius certificate.

There are no coefficient grids, random searches, network calls, staging
actions, manifests, or submissions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_LOCAL_OUTPUT = DATA / "character_field_gate_q169_local_20260730.json"
DEFAULT_EXACT_OUTPUT = DATA / "character_field_gate_q169_exact_20260730.json"

QUOTIENT_T = 169
TARGET_LABELS = ["24T21577", "24T21578"]
FAMILIES = {QUOTIENT_T: {"targetLabels": TARGET_LABELS}}
EXPECTED_LIVE = {
    "24T21577": [12, 20, 24],
    "24T21578": [0, 4, 8, 12, 16, 20, 24],
}
EXPECTED_SOURCE_LABELS = {
    "24T21576",
    "24T21577",
    "24T21578",
    "24T21579",
}
EXPECTED_FIELD_HASH = (
    "0d9294141ad980c6678ee5d22f0f3727f970f7f7031aa36588044eaeece16e86"
)


def load_base():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location("q169_gate_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character gate base from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAMILIES = FAMILIES
    return module


BASE = load_base()


def rendered_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def canonical_polynomial(polynomial, ring):
    reduced = ring(pari(polynomial).polredabs())
    line = ",".join(str(ZZ(value)) for value in reduced.list())
    return reduced, line, hashlib.sha256(line.encode()).hexdigest()


def full_rank_source_labels(structures: Path) -> set[str]:
    labels = set()
    with structures.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            for system in row.get("blockSystems", []):
                if (
                    system.get("shape") == "12x2"
                    and int(system.get("blockKernelOrder", 0)) == 2**11
                    and system.get("quotientActionLabel") == "12T169"
                ):
                    labels.add(str(row["label"]))
                    break
    if labels != EXPECTED_SOURCE_LABELS:
        raise ValueError(
            "full-rank q169 structural label mismatch: "
            f"{sorted(labels)}, expected {sorted(EXPECTED_SOURCE_LABELS)}"
        )
    return labels


def strict_live_pairs(db: Path) -> dict[str, list[dict]]:
    placeholders = ",".join("?" for _label in TARGET_LABELS)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"""
            SELECT t.label,t.r,t.team_count,t.generated_at
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
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.label,t.r
            """,
            tuple(TARGET_LABELS),
        ).fetchall()
    finally:
        connection.close()
    actual = defaultdict(list)
    output = defaultdict(list)
    for label, r, team_count, generated_at in rows:
        actual[str(label)].append(int(r))
        output[str(label)].append(
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": str(generated_at) if generated_at else None,
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
        )
    if dict(actual) != EXPECTED_LIVE:
        raise ValueError(
            f"strict q169 live-pair mismatch: {dict(actual)}, "
            f"expected {EXPECTED_LIVE}"
        )
    return dict(output)


def canonical_field(db: Path, structures: Path, ring) -> dict:
    labels = sorted(full_rank_source_labels(structures))
    placeholders = ",".join("?" for _label in labels)
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
            tuple(labels),
        ).fetchall()
    finally:
        connection.close()

    counts = defaultdict(int)
    fields = {}
    for label, source_r, submission_id, polynomial_index, source_hash, text in rows:
        counts["acceptedRows"] += 1
        coefficients = [ZZ(value) for value in str(text).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        counts["acceptedEvenRows"] += 1
        quotient = ring(coefficients[::2])
        if quotient.degree() != 12 or not quotient.is_irreducible():
            continue
        counts["acceptedEvenIrreducibleRows"] += 1
        if int(quotient.number_of_real_roots()) != 12:
            continue
        counts["totallyRealSourceRows"] += 1
        _reduced, line, field_hash = canonical_polynomial(quotient, ring)
        item = fields.setdefault(
            field_hash,
            {
                "canonicalPolynomial": line,
                "fieldCanonicalSha256": field_hash,
                "family": "12T169",
                "provenance": (
                    "accepted scoreable even q(x^2) from an exact full-rank "
                    "12x2/block-kernel-2^11 q12T169 action; q has 12 real "
                    "roots; PARI polredabs field deduplication"
                ),
                "quotientT12": QUOTIENT_T,
                "sourceRows": [],
            },
        )
        item["sourceRows"].append(
            {
                "coefficientSha256": str(source_hash),
                "label": str(label),
                "polynomialIndex": int(polynomial_index),
                "r": int(source_r),
                "submissionId": str(submission_id),
            }
        )
    if set(fields) != {EXPECTED_FIELD_HASH}:
        raise ValueError(
            f"q169 canonical field mismatch: {sorted(fields)}, "
            f"expected {[EXPECTED_FIELD_HASH]}"
        )
    field = fields[EXPECTED_FIELD_HASH]
    field["sourceRows"].sort(
        key=lambda source: (
            source["label"],
            source["r"],
            source["submissionId"],
            source["polynomialIndex"],
        )
    )
    print(
        json.dumps(
            {
                "acceptedEvenIrreducibleRows": counts[
                    "acceptedEvenIrreducibleRows"
                ],
                "acceptedEvenRows": counts["acceptedEvenRows"],
                "acceptedRows": counts["acceptedRows"],
                "canonicalFields": 1,
                "event": "field_census",
                "fieldCanonicalSha256": EXPECTED_FIELD_HASH,
                "sourceLabels": labels,
                "totallyRealSourceRows": counts["totallyRealSourceRows"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return field


def audit_field(
    field_row: dict,
    ring,
    live: dict[str, list[dict]],
    local_only: bool,
    witness_primes: int,
) -> dict:
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    if quotient.degree() != 12 or not quotient.is_irreducible():
        raise ValueError("canonical quotient is not irreducible degree 12")
    field = NumberField(quotient.change_ring(QQ), "a")
    signature = tuple(int(value) for value in field.signature())
    if signature != (12, 0):
        raise ValueError(f"canonical q169 field is not totally real: {signature}")
    alignment = BASE.ALIGNMENT.exact_linear_character_alignment(
        quotient, QUOTIENT_T
    )
    core_to_labels = {
        str(core): list(labels)
        for core, labels in alignment["coreToPossibleLabels"].items()
    }
    all_target_cores = {
        label: [
            int(value)
            for value in alignment[
                "labelToSquarefreeNormCores"
            ].get(label, [])
        ]
        for label in TARGET_LABELS
    }
    unambiguous_target_cores = {
        label: [
            int(value)
            for value in alignment[
                "labelToUnambiguousSquarefreeNormCores"
            ].get(label, [])
        ]
        for label in TARGET_LABELS
    }
    shared_or_ambiguous = {
        core: labels
        for core, labels in core_to_labels.items()
        if len(labels) > 1
    }

    local_rows = []
    core_gates = {}
    selmer_work = defaultdict(list)
    for target_label, cores in all_target_cores.items():
        for core in cores:
            possible_labels = core_to_labels.get(str(core), [])
            character_compatible = target_label in possible_labels
            exact_character_match = possible_labels == [target_label]
            if core not in core_gates:
                core_gates[core] = BASE.local_core_gate(field, core)
            for live_pair in live[target_label]:
                requested_r = int(live_pair["r"])
                negative_count = 12 - requested_r // 2
                signature_bound = 0 <= negative_count <= 12
                signed_norm_parity_pass = (
                    signature_bound
                    and ((negative_count & 1) == int(core < 0))
                )
                prime_and_sign_feasible = (
                    character_compatible
                    and core_gates[core][
                        "rationalNormParityLocallyPossible"
                    ]
                    and signature_bound
                    and signed_norm_parity_pass
                )
                local_pass = prime_and_sign_feasible and exact_character_match
                if not character_compatible:
                    blocked_reason = "core_not_character_compatible"
                elif not exact_character_match:
                    blocked_reason = "shared_or_ambiguous_character_core"
                elif not core_gates[core][
                    "rationalNormParityLocallyPossible"
                ]:
                    blocked_reason = "rational_prime_norm_parity"
                elif not signature_bound:
                    blocked_reason = "real_signature_bound"
                elif not signed_norm_parity_pass:
                    blocked_reason = "signed_norm_parity"
                else:
                    blocked_reason = None
                row = {
                    "blockedReason": blocked_reason,
                    "characterCompatible": character_compatible,
                    "core": int(core),
                    "exactCharacterMatch": exact_character_match,
                    "label": target_label,
                    "localPass": bool(local_pass),
                    "negativeRealEmbeddingsRequired": negative_count,
                    "possibleLabelsForCore": possible_labels,
                    "primeAndSignFeasibleIgnoringAmbiguity": bool(
                        prime_and_sign_feasible
                    ),
                    "r": requested_r,
                    "realSignatureBoundPass": signature_bound,
                    "signedNormParityPass": signed_norm_parity_pass,
                    "teamCount": int(live_pair["teamCount"]),
                }
                local_rows.append(row)
                if local_pass:
                    selmer_work[int(core)].append(row)

    result = {
        **field_row,
        "alignment": {
            "characters": alignment["characters"],
            "coreToPossibleLabels": core_to_labels,
            "labelToSquarefreeNormCores": alignment[
                "labelToSquarefreeNormCores"
            ],
            "labelToUnambiguousSquarefreeNormCores": alignment[
                "labelToUnambiguousSquarefreeNormCores"
            ],
            "method": alignment["method"],
            "quotientOrder": alignment["quotientOrder"],
            "quotientT": alignment["quotientT"],
            "ramifiedPrimes": alignment["ramifiedPrimes"],
            "sharedOrAmbiguousCores": shared_or_ambiguous,
            "targetAllNormCores": all_target_cores,
            "targetUnambiguousNormCores": unambiguous_target_cores,
        },
        "degree": int(field.degree()),
        "discriminantAbs": str(abs(ZZ(field.discriminant()))),
        "localCoreGates": {
            str(core): row for core, row in sorted(core_gates.items())
        },
        "localPairGates": local_rows,
        "realSignature": {
            "complexPlaces": signature[1],
            "realEmbeddings": signature[0],
        },
    }
    if selmer_work and not local_only:
        result["exactSelmerSignGate"] = BASE.exact_selmer_sign_gate(
            field,
            quotient,
            dict(selmer_work),
            True,
            witness_primes,
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


def certified_routes(field: dict) -> list[dict]:
    routes = []
    for pair in field["exactSelmerSignGate"].get("pairs", []):
        reconstruction = pair.get("firstExactSelmerReconstruction") or {}
        status = str(reconstruction.get("candidateStatus", ""))
        if not status.startswith("certified_"):
            continue
        line = reconstruction["candidateCoefficientLine"]
        routes.append(
            {
                "candidateCoefficientBytes": len(line.encode()),
                "candidateSha256": reconstruction["candidateSha256"],
                "candidateStatus": status,
                "core": int(pair["core"]),
                "fieldCanonicalSha256": field["fieldCanonicalSha256"],
                "label": pair["label"],
                "r": int(pair["r"]),
                "solvableSignMasks": int(
                    pair["exactSelmerSolvableSignMasks"]
                ),
            }
        )
    return routes


def shortest_by_live_pair(routes: list[dict]) -> list[dict]:
    best = {}
    for route in routes:
        key = (route["label"], route["r"])
        if key not in best or (
            route["candidateCoefficientBytes"],
            route["candidateSha256"],
        ) < (
            best[key]["candidateCoefficientBytes"],
            best[key]["candidateSha256"],
        ):
            best[key] = route
    return [best[key] for key in sorted(best)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--phase", choices=("local", "exact"), default="local")
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        args.output = (
            DEFAULT_LOCAL_OUTPUT
            if args.phase == "local"
            else DEFAULT_EXACT_OUTPUT
        )
    output = args.output.resolve()
    if output.exists():
        raise ValueError("refusing to overwrite the q169 field-gate artifact")

    ring = PolynomialRing(QQ, "y")
    live = strict_live_pairs(args.db)
    field_row = canonical_field(args.db, args.structures, ring)
    if args.phase == "exact":
        proof.number_field(False)
    print(
        json.dumps(
            {
                "event": f"field_{args.phase}_start",
                "fieldCanonicalSha256": field_row[
                    "fieldCanonicalSha256"
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    field = audit_field(
        field_row,
        ring,
        live,
        args.phase == "local",
        args.witness_primes,
    )
    exact_selmer_was_run = "matrix" in field["exactSelmerSignGate"]
    routes = certified_routes(field)
    exact_pairs = field["exactSelmerSignGate"].get("pairs", [])
    print(
        json.dumps(
            {
                "certifiedCandidates": len(routes),
                "event": f"field_{args.phase}_done",
                "exactPassers": sum(
                    pair["status"] == "exact_selmer_sign_pass"
                    for pair in exact_pairs
                ),
                "fieldCanonicalSha256": field[
                    "fieldCanonicalSha256"
                ],
                "localPassers": sum(
                    pair["localPass"] for pair in field["localPairGates"]
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    shortest = shortest_by_live_pair(routes)
    payload = {
        "audit": {
            "coefficientGridSearches": 0,
            "conditionalOnGRH": exact_selmer_was_run,
            "networkCalls": 0,
            "phase": args.phase,
            "sourceCanonicalFields": 1,
            "sourceFullRankLabels": sorted(EXPECTED_SOURCE_LABELS),
            "stagingActions": 0,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    pair["generatedAt"]
                    for pairs in live.values()
                    for pair in pairs
                    if pair["generatedAt"]
                }
            ),
            "witnessPrimeCount": int(args.witness_primes),
        },
        "family": FAMILIES[QUOTIENT_T],
        "field": field,
        "livePairs": live,
        "summary": {
            "certifiedCandidates": len(routes),
            "certifiedDistinctLivePairs": len(shortest),
            "exactSelmerStatus": field["exactSelmerSignGate"].get(
                "status",
                "completed" if exact_selmer_was_run else "not_run",
            ),
            "exactPassingPairRoutes": sum(
                pair["status"] == "exact_selmer_sign_pass"
                for pair in exact_pairs
            ),
            "localPassingPairRoutes": sum(
                pair["localPass"] for pair in field["localPairGates"]
            ),
            "sharedOrAmbiguousCoreCount": len(
                field["alignment"]["sharedOrAmbiguousCores"]
            ),
            "shortestCertifiedCandidateByLivePair": shortest,
        },
    }
    rendered = rendered_json(payload)
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(output),
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

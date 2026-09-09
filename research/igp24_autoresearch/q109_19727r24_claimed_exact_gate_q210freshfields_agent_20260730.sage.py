#!/usr/bin/env sage -python
"""Exact q109 character lift for the three claimed 24T19727/r24 fields.

Each run selects one fresh broad-census field, independently identifies its
degree-12 polynomial action as 12T109, checks that 24T19727/r24 is currently
live and unowned, and only then runs the exact Selmer/sign reconstruction and
maximal-subgroup Frobenius certificate.

The three claimed hashes are disjoint from the other agents' work.  This
script performs no searches, network calls, staging operations, or
submissions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import (
    PolynomialRing,
    QQ,
    ZZ,
    libgap,
    pari,
    proof,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_CENSUS = (
    DATA
    / "broad_structural_tr_field_census_q109_19727r24_q210freshfields_agent_20260730.json"
)
DEFAULT_LOCAL = (
    DATA
    / "broad_structural_character_gate_q109_19727r24_q210freshfields_agent_local_20260730.json"
)
CENSUS_SHA256 = (
    "d0fcf9185662b7421727bf6dc7ac862d619252fb92428a8110be1309ecc6eb7e"
)
LOCAL_SHA256 = (
    "12ba2f2ccb92e6327704f86efee204b69c3ce79499e6e9c8e96de6c360e03bb5"
)
QUOTIENT_T = 109
TARGET_LABEL = "24T19727"
TARGET_R = 24
CLAIMED_HASHES = {
    "43be7262318f5595023fc96061299de06a90fed77326e161c9f5f0b3541f8117",
    "62934cc69e91ffdac06cfe34b393ba9e8c98a945b3e7fde995f2fd377779cddc",
    "a945b19054950a97b104943dcf35a261a30a2fb26c800a62088feb2f82912316",
}


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


def load_base():
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location(
        "q109_19727r24_claimed_base", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character gate base from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAMILIES = {
        QUOTIENT_T: {
            "ambiguousTargetLabels": [TARGET_LABEL],
            "targetLabels": [TARGET_LABEL],
        }
    }
    return module


def select_field(prefix: str) -> tuple[dict, list[dict]]:
    if sha256_path(DEFAULT_CENSUS) != CENSUS_SHA256:
        raise ValueError("q109 broad census byte digest changed")
    if sha256_path(DEFAULT_LOCAL) != LOCAL_SHA256:
        raise ValueError("q109 local-gate byte digest changed")
    census = json.loads(DEFAULT_CENSUS.read_text(encoding="utf-8"))
    local = json.loads(DEFAULT_LOCAL.read_text(encoding="utf-8"))
    matches = [
        row
        for row in census["freshFields"]
        if str(row["fieldCanonicalSha256"]).startswith(prefix)
    ]
    if len(matches) != 1:
        raise ValueError(f"field prefix selected {len(matches)} fields")
    source = dict(matches[0])
    digest = str(source["fieldCanonicalSha256"])
    if digest not in CLAIMED_HASHES:
        raise ValueError(f"field is not assigned to this agent: {digest}")
    local_rows = [
        row
        for field in local["fields"]
        if str(field["fieldCanonicalSha256"]) == digest
        for row in field.get("localPairGates", [])
        if (
            row.get("localPass") is True
            and str(row.get("label")) == TARGET_LABEL
            and int(row.get("r", -1)) == TARGET_R
        )
    ]
    if not local_rows:
        raise ValueError("selected field has no pinned r24 local passer")
    source.update(
        {
            "family": "12T109",
            "provenance": (
                "fresh broad accepted-even q(x^2) census; independent "
                "polynomial-action identification is performed before "
                "the exact Selmer lift"
            ),
            "quotientT12": QUOTIENT_T,
        }
    )
    return source, local_rows


def exact_galois_certificate(polynomial) -> dict:
    group = polynomial.galois_group(algorithm="gap")
    gap_group = libgap(group)
    sage_t = int(group.transitive_number())
    gap_t = int(libgap.TransitiveIdentification(gap_group))
    if sage_t != gap_t:
        raise ArithmeticError(
            f"Sage/GAP transitive-ID mismatch: {sage_t} versus {gap_t}"
        )
    if int(group.degree()) != 12 or not bool(
        libgap.IsTransitive(gap_group)
    ):
        raise ArithmeticError("Galois action is not transitive degree 12")
    certificate = {
        "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
        "degree": int(group.degree()),
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
    if gap_t != QUOTIENT_T:
        raise ValueError(
            f"independent quotient action is 12T{gap_t}, not 12T109"
        )
    certificate["status"] = "exact_12T109_certified"
    return certificate


def strict_live_r24(db: Path) -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.r,t.team_count,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=? AND t.r=?
              AND t.team_count=0 AND t.discovered=0
              AND b.label IS NULL AND owned.label IS NULL
            """,
            (TARGET_LABEL, TARGET_R),
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise ValueError(
            f"{TARGET_LABEL}/r{TARGET_R} is not uniquely live/unowned"
        )
    label, rank, team_count, generated_at = rows[0]
    return {
        str(label): [
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": (
                    str(generated_at) if generated_at else None
                ),
                "locallyOwned": False,
                "r": int(rank),
                "teamCount": int(team_count),
            }
        ]
    }


def certified_routes(field_result: dict) -> list[dict]:
    routes = []
    for pair in field_result["exactSelmerSignGate"].get("pairs", []):
        reconstruction = pair.get("certifiedExactSelmerReconstruction")
        if (
            int(pair.get("r", -1)) == TARGET_R
            and pair.get("status") == "exact_selmer_sign_pass"
            and reconstruction
            and str(reconstruction.get("candidateStatus", "")).startswith(
                "certified_"
            )
        ):
            routes.append(
                {
                    "candidateCoefficientLine": reconstruction[
                        "candidateCoefficientLine"
                    ],
                    "candidateSha256": reconstruction["candidateSha256"],
                    "candidateStatus": reconstruction["candidateStatus"],
                    "core": int(pair["core"]),
                    "fieldCanonicalSha256": field_result[
                        "fieldCanonicalSha256"
                    ],
                    "label": str(pair["label"]),
                    "r": int(pair["r"]),
                }
            )
    return routes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field-hash", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--max-reconstructions", type=int, default=1)
    parser.add_argument(
        "--max-coset-reconstructions-per-sign", type=int, default=16
    )
    parser.add_argument("--coset-mask-start", type=int, default=0)
    parser.add_argument("--coset-mask-stride", type=int, default=1)
    parser.add_argument(
        "--coset-mask-list",
        help="comma-separated explicit Selmer-kernel coset masks",
    )
    parser.add_argument("--pari-stack-bytes", type=int, default=0)
    parser.add_argument("--pari-stack-max-bytes", type=int, default=0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    coset_mask_schedule = (
        [
            int(value.strip(), 0)
            for value in args.coset_mask_list.split(",")
            if value.strip()
        ]
        if args.coset_mask_list
        else None
    )
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    proof.number_field(False)
    if args.pari_stack_bytes:
        pari.allocatemem(
            int(args.pari_stack_bytes),
            int(args.pari_stack_max_bytes),
        )
    field_row, pinned_local_passers = select_field(args.field_hash)
    live = strict_live_r24(args.db)
    ring = PolynomialRing(QQ, "y")
    quotient = ring(
        [
            ZZ(value)
            for value in field_row["canonicalPolynomial"].split(",")
        ]
    )
    print(
        json.dumps(
            {
                "event": "exact_quotient_id_start",
                "fieldCanonicalSha256": field_row[
                    "fieldCanonicalSha256"
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    quotient_certificate = exact_galois_certificate(quotient)
    print(
        json.dumps(
            {
                "event": "exact_quotient_id_done",
                "fieldCanonicalSha256": field_row[
                    "fieldCanonicalSha256"
                ],
                "transitiveLabel": quotient_certificate[
                    "transitiveLabel"
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )

    base = load_base()
    result = base.audit_field(
        field_row,
        ring,
        live,
        False,
        True,
        args.witness_primes,
        args.max_reconstructions,
        args.max_coset_reconstructions_per_sign,
        args.coset_mask_start,
        args.coset_mask_stride,
        coset_mask_schedule,
    )
    result["quotientGaloisCertificate"] = quotient_certificate
    routes = certified_routes(result)
    payload = {
        "audit": {
            "conditionalOnGRH": True,
            "inputCensusSha256": CENSUS_SHA256,
            "inputLocalGateSha256": LOCAL_SHA256,
            "networkCalls": 0,
            "phase": "exact_r24_only",
            "submissionCalls": 0,
            "targetedCosetMasks": coset_mask_schedule,
            "witnessPrimeCount": int(args.witness_primes),
        },
        "field": result,
        "livePairs": live,
        "pinnedLocalR24Passers": pinned_local_passers,
        "summary": {
            "certifiedExactRouteCount": len(routes),
            "certifiedExactRoutes": routes,
            "exact12T109Certified": True,
            "fieldCanonicalSha256": result[
                "fieldCanonicalSha256"
            ],
            "target": f"{TARGET_LABEL}/r{TARGET_R}",
        },
    }
    rendered = rendered_json(payload)
    write_atomic(args.output.resolve(), rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(args.output.resolve()),
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

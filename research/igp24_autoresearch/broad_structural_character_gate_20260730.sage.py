#!/usr/bin/env sage
"""Run the exact 12x2 Selmer lift on broadly sourced quotient fields.

The companion census may include false family assignments because an even
degree-24 source can have several inequivalent 12x2 block systems.  Therefore
the exact phase independently computes the degree-12 Galois group and refuses
to run the Selmer lift unless its transitive number is exactly the requested
``12Tq``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def load_base(quotient_t: int, target_labels: list[str]):
    path = ROOT / "character_field_gate_q156_q210_20260730.sage.py"
    spec = importlib.util.spec_from_file_location(
        "broad_structural_character_base", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import character gate base from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAMILIES = {
        quotient_t: {
            "ambiguousTargetLabels": target_labels,
            "targetLabels": target_labels,
        }
    }
    return module


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def strict_live_pairs(db: Path, target_labels: list[str]) -> dict[str, list[dict]]:
    placeholders = ",".join("?" for _label in target_labels)
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
                FROM verifications WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label IN ({placeholders})
              AND t.team_count=0 AND t.discovered=0
              AND b.label IS NULL AND owned.label IS NULL
            ORDER BY CAST(substr(t.label,4) AS INTEGER),t.r
            """,
            tuple(target_labels),
        ).fetchall()
    finally:
        connection.close()
    result = {label: [] for label in target_labels}
    for label, r, team_count, generated_at in rows:
        result[str(label)].append(
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": str(generated_at) if generated_at else None,
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
        )
    return {label: pairs for label, pairs in result.items() if pairs}


def load_fresh_fields(
    census: Path, quotient_t: int, field_hash: str | None
) -> tuple[list[dict], dict]:
    payload = json.loads(census.read_text(encoding="utf-8"))
    payload_quotient_t = payload.get("quotientT12")
    if payload_quotient_t is None:
        field_quotients = {
            int(row["quotientT12"]) for row in payload.get("fields", [])
        }
        if len(field_quotients) == 1:
            payload_quotient_t = field_quotients.pop()
    if int(payload_quotient_t) != quotient_t:
        raise ValueError("census quotient does not match --quotient-t")
    fields = []
    source_fields = payload.get(
        "freshFields", payload.get("freshTotallyRealFields", [])
    )
    for source in source_fields:
        row = dict(source)
        row["family"] = f"12T{quotient_t}"
        row["provenance"] = (
            "broad accepted even q(x^2) source whose certified degree-24 "
            f"label has a 12x2/12T{quotient_t} block system; independent "
            "exact quotient identity deferred to exact phase"
        )
        row["quotientT12"] = quotient_t
        fields.append(row)
    if field_hash:
        fields = [
            row
            for row in fields
            if row["fieldCanonicalSha256"].startswith(field_hash)
        ]
        if len(fields) != 1:
            raise ValueError(
                f"field prefix selected {len(fields)} fresh canonical fields"
            )
    return fields, payload["summary"]


def exact_galois_certificate(field_row: dict, quotient_t: int, ring) -> dict:
    quotient = ring(
        [ZZ(value) for value in field_row["canonicalPolynomial"].split(",")]
    )
    group = quotient.galois_group(algorithm="gap")
    transitive_number = int(group.transitive_number())
    if transitive_number != quotient_t:
        raise ValueError(
            f"independent quotient group is 12T{transitive_number}, "
            f"not 12T{quotient_t}"
        )
    return {
        "algorithm": "Sage polynomial.galois_group(algorithm='gap')",
        "degree": int(group.degree()),
        "order": int(group.order()),
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{transitive_number}",
        "transitiveNumber": transitive_number,
    }


def certified_routes(rows: list[dict]) -> list[dict]:
    routes = []
    for field in rows:
        for pair in field["exactSelmerSignGate"].get("pairs", []):
            reconstruction = pair.get("certifiedExactSelmerReconstruction")
            if (
                pair.get("status") == "exact_selmer_sign_pass"
                and reconstruction
                and str(reconstruction.get("candidateStatus", "")).startswith(
                    "certified_"
                )
            ):
                routes.append(
                    {
                        "candidateSha256": reconstruction["candidateSha256"],
                        "candidateStatus": reconstruction["candidateStatus"],
                        "core": pair["core"],
                        "fieldCanonicalSha256": field[
                            "fieldCanonicalSha256"
                        ],
                        "label": pair["label"],
                        "r": pair["r"],
                    }
                )
    return routes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quotient-t", type=int, required=True)
    parser.add_argument(
        "--target-label", action="append", required=True, dest="target_labels"
    )
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DATA / "ledger.sqlite3")
    parser.add_argument("--phase", choices=("local", "exact"), default="local")
    parser.add_argument("--field-hash")
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
    parser.add_argument(
        "--stop-after-local-passers",
        type=int,
        default=0,
        help=(
            "during local phase, stop after this many passing field/pair "
            "routes; zero audits the complete census"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
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

    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if args.phase == "exact" and not args.field_hash:
        raise ValueError("--field-hash is required for exact phase")
    if args.phase == "local" and args.field_hash:
        raise ValueError("local phase audits the complete fresh census")

    target_labels = sorted(set(args.target_labels))
    base = load_base(args.quotient_t, target_labels)
    ring = PolynomialRing(QQ, "y")
    fields, source_summary = load_fresh_fields(
        args.census.resolve(), args.quotient_t, args.field_hash
    )
    live = strict_live_pairs(args.db, target_labels)
    if not live:
        raise ValueError("no current live unowned target pairs")

    if args.phase == "exact":
        proof.number_field(False)
        if args.pari_stack_bytes:
            pari.allocatemem(
                int(args.pari_stack_bytes),
                int(args.pari_stack_max_bytes),
            )

    audited = []
    for index, field_row in enumerate(fields, start=1):
        print(
            json.dumps(
                {
                    "event": f"field_{args.phase}_start",
                    "fieldCanonicalSha256": field_row[
                        "fieldCanonicalSha256"
                    ],
                    "fieldIndex": index,
                    "fieldTotal": len(fields),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if (
            args.phase == "local"
            and args.stop_after_local_passers
            and sum(
                row["localPass"]
                for audited_field in audited
                for row in audited_field["localPairGates"]
            )
            >= args.stop_after_local_passers
        ):
            break
        certificate = (
            exact_galois_certificate(field_row, args.quotient_t, ring)
            if args.phase == "exact"
            else {
                "status": "deferred_until_exact_survivor",
                "structuralSourceCertificate": (
                    f"accepted degree-24 label with a 12x2/12T"
                    f"{args.quotient_t} block structure"
                ),
            }
        )
        try:
            result = base.audit_field(
                field_row,
                ring,
                live,
                args.phase == "local",
                args.phase == "exact",
                args.witness_primes,
                args.max_reconstructions,
                args.max_coset_reconstructions_per_sign,
                args.coset_mask_start,
                args.coset_mask_stride,
                coset_mask_schedule,
            )
        except ValueError as exc:
            if args.phase != "local":
                raise
            result = {
                **field_row,
                "alignment": {
                    "error": f"{type(exc).__name__}: {exc}",
                    "status": "rejected_structural_source_misalignment",
                },
                "exactSelmerSignGate": {
                    "pairs": [],
                    "status": "skipped_quotient_identity_rejection",
                },
                "localCoreGates": {},
                "localPairGates": [],
            }
        result["quotientGaloisCertificate"] = certificate
        audited.append(result)
        print(
            json.dumps(
                {
                    "event": f"field_{args.phase}_done",
                    "fieldCanonicalSha256": result[
                        "fieldCanonicalSha256"
                    ],
                    "localPassers": sum(
                        row["localPass"] for row in result["localPairGates"]
                    ),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    routes = certified_routes(audited)
    payload = {
        "audit": {
            "conditionalOnGRH": args.phase == "exact",
            "networkCalls": 0,
            "phase": args.phase,
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    str(pair["generatedAt"])
                    for pairs in live.values()
                    for pair in pairs
                    if pair.get("generatedAt")
                }
            ),
            "witnessPrimeCount": args.witness_primes,
        },
        "fields": audited,
        "livePairs": live,
        "quotientT12": args.quotient_t,
        "sourceCensusSummary": source_summary,
        "summary": {
            "auditedFields": len(audited),
            "certifiedExactRouteCount": len(routes),
            "certifiedExactRoutes": routes,
            "localPassingPairFieldRoutes": sum(
                row["localPass"]
                for field in audited
                for row in field["localPairGates"]
            ),
        },
        "targetLabels": target_labels,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
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

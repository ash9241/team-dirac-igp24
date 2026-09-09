#!/usr/bin/env sage -python
"""Arithmetic, exact-group, pair-action, and live-target screen.

Input rows come from gold_nonderivative_20260728_norm_scan_shard*.jsonl.
Only a live predicted hit triggers the expensive maximal-subgroup and field
discriminant certificates.  No network or submission operation is present.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RESULTS = DATA / "gold_nonderivative_20260728_screen.jsonl"
CERTIFICATE = DATA / "gold_nonderivative_20260728_exact_certificate.json"
SUMMARY = DATA / "gold_nonderivative_20260728_summary.json"
MANIFEST = ROOT / "outbox" / "gold_nonderivative_20260728_exact.txt"
RESERVED = {
    ("24T10482", 8),
    ("24T11787", 12),
    ("24T14293", 16),
    ("24T15043", 4),
    ("24T15273", 20),
    ("24T15337", 20),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T24877", 14),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scan-glob",
        default="gold_nonderivative_20260728_norm_scan*.jsonl",
        help="glob below data/ containing norm-scan checkpoints",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="screen a deduplicated partial checkpoint instead of requiring 505 rows",
    )
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--certificate", type=Path, default=CERTIFICATE)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    return parser.parse_args()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHARACTER = load_module(
    "gold_nonderivative_exact_character",
    ROOT / "character_kernel_gold_pilot.sage.py",
)
SHARED = CHARACTER.SHARED


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coefficient_line(polynomial) -> str:
    return ",".join(str(value) for value in polynomial.list())


def atomic_new(path: Path, value: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
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


def load_actions() -> dict[str, list[dict]]:
    actions = defaultdict(list)
    for name in sorted(
        glob.glob(
            str(
                DATA
                / "agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl"
            )
        )
    ):
        for raw in Path(name).read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if int(row["sourceBlockKernelOrder"]) == 2**11:
                actions[str(row["sourceLabel"])].append(row)
    return actions


def transform(quotient_line: str, a: int, b: int) -> dict:
    started = time.monotonic()
    ring_u = PolynomialRing(ZZ, "u")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    u = ring_u.gen()
    x = ring_x.gen()
    q = ring_u([ZZ(value) for value in quotient_line.split(",")])
    if q.degree() != 12 or not q.is_monic() or not q.is_irreducible():
        raise ValueError("bad quotient")

    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    radicand_y = (y - ZZ(a)) * (y - ZZ(b))
    h = ring_z(q_y.resultant(z - radicand_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    source_polynomial = ring_x(h(x**2))
    base = {
        "a": a,
        "b": b,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "transformedQuotientDegree": int(h.degree()),
    }
    if (
        h.degree() != 12
        or not h.is_monic()
        or not h.is_irreducible()
        or not source_polynomial.is_irreducible()
    ):
        return {**base, "status": "source_irreducibility_miss"}

    h_line = coefficient_line(h)
    source_line = coefficient_line(source_polynomial)
    factors = [
        (factor, int(exponent))
        for factor, exponent in h.symmetric_power(2, monic=True).factor()
    ]
    selected = [
        (index, factor)
        for index, (factor, exponent) in enumerate(factors)
        if factor.degree() == 12 and exponent == 1
    ]
    result = {
        **base,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "factorDegrees": [
            {
                "degree": int(factor.degree()),
                "exponent": exponent,
                "index": index,
            }
            for index, (factor, exponent) in enumerate(factors)
        ],
        "sourcePolynomialLine": source_line,
        "sourcePolynomialR": int(source_polynomial.number_of_real_roots()),
        "sourcePolynomialSha256": sha_text(source_line),
        "status": (
            "unique_degree12_pair_factor"
            if len(selected) == 1
            else "pair_factor_pattern_miss"
        ),
        "transformedNorm": str(h[0]),
        "transformedNormCore": int(ZZ(h[0]).squarefree_part()),
        "transformedQuotientLine": h_line,
        "transformedQuotientSha256": sha_text(h_line),
    }
    if len(selected) == 1:
        factor_index, selected_factor = selected[0]
        candidate = ring_x(selected_factor(x**2))
        if (
            candidate.degree() != 24
            or not candidate.is_monic()
            or not candidate.is_irreducible()
        ):
            raise ValueError("pair candidate failed exact arithmetic gates")
        candidate_line = coefficient_line(candidate)
        result["candidate"] = {
            "coefficientLine": candidate_line,
            "coefficientSha256": sha_text(candidate_line),
            "factorIndex": factor_index,
            "r": int(candidate.number_of_real_roots()),
            "selectedDegree12FactorLine": coefficient_line(selected_factor),
            "selectedDegree12FactorSha256": sha_text(
                coefficient_line(selected_factor)
            ),
        }
    return result


def main() -> int:
    args = parse_args()
    output_paths = (
        args.results.resolve(),
        args.certificate.resolve(),
        args.summary.resolve(),
        args.manifest.resolve(),
    )
    if len(set(output_paths)) != len(output_paths):
        raise ValueError("results, certificate, summary, and manifest must be distinct")
    for path in output_paths:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    scan_paths = sorted(DATA.glob(args.scan_glob))
    if not scan_paths:
        raise ValueError(f"no norm-scan checkpoints matched {args.scan_glob!r}")
    source_by_hash = {}
    for path in scan_paths:
        for line_number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not raw.strip():
                continue
            row = json.loads(raw)
            if row.get("status") != "certified" or not isinstance(
                row.get("source"), dict
            ):
                raise ValueError(f"invalid scan row at {path}:{line_number}")
            digest = str(row["source"].get("coefficientSha256", ""))
            incumbent = source_by_hash.get(digest)
            if incumbent is not None and canonical_json(incumbent) != canonical_json(row):
                raise ValueError(
                    f"conflicting duplicate source hash at {path}:{line_number}"
                )
            source_by_hash[digest] = row
    source_rows = [source_by_hash[digest] for digest in sorted(source_by_hash)]
    if not args.allow_partial and len(source_rows) != 505:
        raise ValueError(f"incomplete norm scan: {len(source_rows)} != 505")
    cases = [
        {
            "alignment": row["alignment"],
            "case": case,
            "source": row["source"],
        }
        for row in source_rows
        for case in row["cases"]
    ]

    connection = sqlite3.connect(
        f"file:{DB.resolve()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    current_tc0 = {
        (str(label), int(r))
        for label, r in connection.execute(
            """
            SELECT t.label,t.r
            FROM targets AS t
            WHERE t.team_count=0 AND t.discovered=0
              AND NOT EXISTS(
                SELECT 1 FROM baseline_pairs AS b
                WHERE b.label=t.label AND b.r=t.r
              )
              AND NOT EXISTS(
                SELECT 1 FROM verifications AS v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }
    known_hashes = {
        str(value)
        for (value,) in connection.execute(
            "SELECT DISTINCT coefficient_hash FROM polynomials"
        )
    }
    actions = load_actions()

    results = []
    preliminary_hits = []
    started = time.monotonic()
    for ordinal, item in enumerate(
        sorted(
            cases,
            key=lambda row: (
                row["source"]["coefficientSha256"],
                int(row["case"]["a"]),
                int(row["case"]["b"]),
            ),
        ),
        1,
    ):
        case = item["case"]
        source = item["source"]
        possible_labels = sorted(set(case["possibleSourceLabels"]))
        exact_source_label = (
            possible_labels[0] if len(possible_labels) == 1 else None
        )
        try:
            arithmetic = transform(
                str(source["coefficientLine"]),
                int(case["a"]),
                int(case["b"]),
            )
        except Exception as exc:
            arithmetic = {
                "error": f"{type(exc).__name__}: {exc}",
                "status": "transform_error",
            }
        predictions = []
        if (
            exact_source_label is not None
            and arithmetic.get("transformedNormCore")
            == int(case["squarefreeNormCore"])
            and "candidate" in arithmetic
        ):
            candidate = arithmetic["candidate"]
            for action in actions.get(exact_source_label, []):
                pair = (str(action["targetLabel"]), int(candidate["r"]))
                live = (
                    pair in current_tc0
                    and pair not in RESERVED
                    and str(candidate["coefficientSha256"]) not in known_hashes
                )
                prediction = {
                    "action": action,
                    "currentTc0Unreserved": live,
                    "label": pair[0],
                    "r": pair[1],
                }
                predictions.append(prediction)
                if live:
                    preliminary_hits.append(
                        {
                            "action": action,
                            "arithmetic": arithmetic,
                            "case": case,
                            "exactSourceLabel": exact_source_label,
                            "source": source,
                        }
                    )
        row = {
            "a": int(case["a"]),
            "arithmetic": arithmetic,
            "b": int(case["b"]),
            "exactRankElevenSourceLabel": exact_source_label,
            "norm": str(case["norm"]),
            "ordinal": ordinal,
            "possibleSourceLabels": possible_labels,
            "predictions": predictions,
            "preliminaryHit": any(
                prediction["currentTc0Unreserved"]
                for prediction in predictions
            ),
            "quotientSha256": str(source["coefficientSha256"]),
            "quotientT12": int(source["quotientT12"]),
            "source": source,
            "squarefreeNormCore": int(case["squarefreeNormCore"]),
        }
        results.append(row)
        args.results.write_text(
            "".join(canonical_json(value) + "\n" for value in results),
            encoding="utf-8",
        )
        if row["preliminaryHit"] or ordinal % 25 == 0:
            print(
                canonical_json(
                    {
                        "candidateR": arithmetic.get("candidate", {}).get("r"),
                        "event": "case",
                        "ordinal": ordinal,
                        "preliminaryHit": row["preliminaryHit"],
                        "total": len(cases),
                    }
                ),
                flush=True,
            )

    # Deduplicate identical candidate/action/source triples before exact proof.
    unique_preliminary = {}
    for hit in preliminary_hits:
        key = (
            str(hit["arithmetic"]["candidate"]["coefficientSha256"]),
            str(hit["action"]["targetLabel"]),
            int(hit["arithmetic"]["candidate"]["r"]),
            str(hit["exactSourceLabel"]),
        )
        unique_preliminary.setdefault(key, hit)

    exact_hits = []
    ring_x = PolynomialRing(ZZ, "x")
    ring_z = PolynomialRing(ZZ, "z")
    for key, hit in sorted(unique_preliminary.items()):
        arithmetic = hit["arithmetic"]
        source_label = str(hit["exactSourceLabel"])
        h = ring_z(
            [ZZ(value) for value in arithmetic["transformedQuotientLine"].split(",")]
        )
        transformed_source = ring_x(
            [ZZ(value) for value in arithmetic["sourcePolynomialLine"].split(",")]
        )
        core = int(arithmetic["transformedNormCore"])
        alignment = CHARACTER.character_alignment(
            h,
            int(hit["source"]["quotientT12"]),
            probe_cores=[core],
        )
        aligned = alignment[
            "labelToUnambiguousSquarefreeNormCores"
        ].get(source_label, [])
        if core not in [int(value) for value in aligned]:
            continue

        SHARED.TARGET_T = int(source_label[3:])
        SHARED.TARGET_LABEL = source_label
        maximal_profiles, maximal_identities = SHARED.maximal_joint_profiles()
        maximal_certificate = SHARED.frobenius_maximal_certificate(
            transformed_source,
            h,
            maximal_profiles,
            maximal_identities,
            1000,
        )
        if not maximal_certificate["complete"]:
            continue

        candidate = ring_x(
            [ZZ(value) for value in arithmetic["candidate"]["coefficientLine"].split(",")]
        )
        target_label = str(hit["action"]["targetLabel"])
        target_r = int(arithmetic["candidate"]["r"])
        target = connection.execute(
            """
            SELECT label,r,team_count,discovered,generated_at
            FROM targets WHERE label=? AND r=?
            """,
            (target_label, target_r),
        ).fetchone()
        candidate_hash = str(arithmetic["candidate"]["coefficientSha256"])
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
            or duplicate_count != 0
        ):
            continue
        field_disc = abs(
            ZZ(NumberField(candidate.change_ring(QQ), "alpha").discriminant())
        )
        exact_hits.append(
            {
                "action": hit["action"],
                "candidate": {
                    **arithmetic["candidate"],
                    "fieldDiscriminantAbs": str(field_disc),
                    "polynomialDiscriminantAbs": str(
                        abs(ZZ(candidate.discriminant()))
                    ),
                },
                "characterAlignment": alignment,
                "maximalSubgroupCertificate": maximal_certificate,
                "method": "g(u)=(u-a)(u-b), no derivative factor",
                "networkCalls": 0,
                "source": hit["source"],
                "sourceLabel": source_label,
                "submissionCalls": 0,
                "target": {
                    "generatedAt": str(target["generated_at"]),
                    "label": target_label,
                    "r": target_r,
                    "teamCount": 0,
                },
                "transform": {
                    "a": int(hit["case"]["a"]),
                    "b": int(hit["case"]["b"]),
                    "norm": str(hit["case"]["norm"]),
                    "squarefreeNormCore": core,
                    "transformedQuotientLine": arithmetic[
                        "transformedQuotientLine"
                    ],
                    "transformedQuotientSha256": arithmetic[
                        "transformedQuotientSha256"
                    ],
                },
            }
        )

    manifest_lines = []
    seen = set()
    for hit in exact_hits:
        digest = str(hit["candidate"]["coefficientSha256"])
        if digest not in seen:
            seen.add(digest)
            manifest_lines.append(str(hit["candidate"]["coefficientLine"]))
    manifest_text = "".join(line + "\n" for line in manifest_lines)
    atomic_new(args.manifest, manifest_text)

    certificate = {
        "exactHitCount": len(exact_hits),
        "hits": exact_hits,
        "manifest": str(args.manifest.resolve().relative_to(ROOT)),
        "manifestSha256": sha_text(manifest_text),
        "networkCalls": 0,
        "partialScan": bool(args.allow_partial),
        "scanPaths": [
            {
                "path": str(path.resolve().relative_to(ROOT)),
                "sha256": sha_file(path),
            }
            for path in scan_paths
        ],
        "schemaVersion": "gold-nonderivative-20260728-exact-v1",
        "submissionCalls": 0,
    }
    certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    atomic_new(args.certificate, certificate_text)
    summary = {
        "exactHitCount": len(exact_hits),
        "exactPairs": [
            {
                "coefficientSha256": hit["candidate"]["coefficientSha256"],
                "label": hit["target"]["label"],
                "r": hit["target"]["r"],
            }
            for hit in exact_hits
        ],
        "manifest": str(args.manifest.resolve().relative_to(ROOT)),
        "manifestSha256": sha_text(manifest_text),
        "networkCalls": 0,
        "normCharacterCaseCount": len(cases),
        "partialScan": bool(args.allow_partial),
        "preliminaryHitCount": len(unique_preliminary),
        "results": str(args.results.resolve().relative_to(ROOT)),
        "resultsSha256": sha_file(args.results),
        "scanFileCount": len(scan_paths),
        "schemaVersion": "gold-nonderivative-20260728-summary-v1",
        "sourceCount": len(source_rows),
        "statusCounts": dict(
            sorted(
                Counter(
                    row["arithmetic"]["status"]
                    for row in results
                ).items()
            )
        ),
        "submissionCalls": 0,
        "wallSeconds": round(time.monotonic() - started, 3),
    }
    atomic_new(
        args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(canonical_json(summary), flush=True)
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal fresh certified factors from the post-archive index-24 closure wave."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from stage_live_index24_pair_results_20260813 import receipt_hashes


ROOT = Path(__file__).resolve().parent


def canonical(value: object) -> str:
    coefficients = [int(item) for item in str(value).split(",")]
    if (
        len(coefficients) != 25
        or coefficients[0] == 0
        or coefficients[-1] != 1
        or math.gcd(*coefficients) != 1
    ):
        raise ValueError("not a primitive monic degree-24 polynomial")
    return ",".join(map(str, coefficients))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--receipts", type=Path, default=ROOT / "receipts")
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--max-lines", type=int, default=900)
    parser.add_argument("--max-bytes", type=int, default=900_000)
    parser.add_argument(
        "--include-hash-certificate",
        action="append",
        type=Path,
        default=[],
        help="exclude hashes already selected by an earlier sealed wave certificate",
    )
    parser.add_argument(
        "--exclude-pair-certificate",
        action="append",
        type=Path,
        default=[],
        help="exclude exact live pairs already sealed by another construction family",
    )
    args = parser.parse_args()
    if args.certificate.exists():
        raise FileExistsError(args.certificate)

    paths = []
    for pattern in args.input:
        paths.extend(Path(value) for value in sorted(glob.glob(pattern)))
    paths = sorted(set(path.resolve() for path in paths))
    if not paths:
        raise FileNotFoundError("no result artifacts matched")

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    known = {
        str(value)
        for (value,) in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    connection.close()
    received = receipt_hashes(args.receipts)
    previously_selected = set()
    previously_sealed_pairs: set[tuple[str, int]] = set()

    def retain_sealed_pairs(certificate: dict) -> None:
        # Pair-closure certificates carry one or more compatible pairs per
        # candidate.  Only a singleton assignment is exact enough for a
        # pair-level exclusion.
        for row in certificate.get("entries") or []:
            compatible = row.get("compatibleLivePairs") or []
            if len(compatible) == 1:
                previously_sealed_pairs.add(
                    (str(compatible[0]["label"]), int(compatible[0]["r"]))
                )
                continue
            label = row.get("label", row.get("targetLabel"))
            root = row.get("r", row.get("targetR"))
            if label is not None and root is not None:
                previously_sealed_pairs.add((str(label), int(root)))
        # Character/C3 stage certificates use ``selectedPairs``.
        for row in certificate.get("selectedPairs") or []:
            label = row.get("label", row.get("targetLabel"))
            root = row.get("r", row.get("targetR"))
            if label is not None and root is not None:
                previously_sealed_pairs.add((str(label), int(root)))

    for certificate_path in args.include_hash_certificate:
        certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
        previously_selected.update(
            str(row["coefficientSha256"])
            for row in certificate.get("entries") or []
        )
        retain_sealed_pairs(certificate)
    for certificate_path in args.exclude_pair_certificate:
        certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
        retain_sealed_pairs(certificate)
    skips = Counter()
    selected: dict[str, dict] = {}
    source_keys = set()
    safe_pairs = {}
    possible_pairs = set()
    for path in paths:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != 1:
            raise ValueError(f"expected one row in {path}")
        payload = rows[0]
        if payload.get("status") != "certified_multi":
            skips["notCertifiedMulti"] += 1
            continue
        job = payload.get("job") or {}
        source_key = (
            str(payload.get("sourceSubmissionId")),
            int(payload.get("sourcePolynomialIndex", -1)),
        )
        if source_key != (
            str(job.get("sourceSubmissionId")),
            int(job.get("sourcePolynomialIndex", -2)),
        ):
            raise ValueError(f"source provenance mismatch in {path}")
        source_keys.add(source_key)
        targets_by_root = {}
        for target in job.get("targets") or []:
            pair = (str(target["label"]), int(target["r"]))
            targets_by_root.setdefault(int(target["r"]), []).append(target)
            possible_pairs.add(pair)
        candidates = payload.get("candidates") or []
        if len(candidates) != int(job.get("length24OrbitCount", -1)):
            raise ValueError(f"degree-24 factor count mismatch in {path}")
        if len(payload.get("attempt", {}).get("factorDegrees") or []) == 0:
            raise ValueError(f"empty exact factor certificate in {path}")
        for candidate in candidates:
            line = canonical(candidate["coefficientLine"])
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            if digest != str(candidate["coefficientSha256"]):
                raise ValueError(f"candidate hash mismatch in {path}")
            if digest in known:
                skips["knownHash"] += 1
                continue
            if digest in received:
                skips["receiptHash"] += 1
                continue
            if digest in previously_selected:
                skips["earlierSealedWaveHash"] += 1
                continue
            root = int(candidate["targetR"])
            compatible = targets_by_root.get(root, [])
            if not compatible:
                skips["noCurrentOpenPairAtCandidateSignature"] += 1
                continue
            if len(compatible) == 1:
                pair = (str(compatible[0]["label"]), root)
                if pair in previously_sealed_pairs:
                    skips["earlierSealedWavePair"] += 1
                    continue
                safe_pairs[pair] = int(compatible[0]["teamCount"])
            selected.setdefault(
                digest,
                {
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "sourceSubmissionId": source_key[0],
                    "sourcePolynomialIndex": source_key[1],
                    "sourceLabel": str(payload["sourceLabel"]),
                    "sourceR": int(payload["sourceR"]),
                    "candidateR": root,
                    "compatibleLivePairs": [
                        {
                            "label": str(target["label"]),
                            "r": int(target["r"]),
                            "teamCount": int(target["teamCount"]),
                        }
                        for target in compatible
                    ],
                    "artifact": str(path),
                    "jobOrdinal": int(job["jobOrdinal"]),
                    "rootBundleSize": sum(
                        int(other["targetR"]) == root for other in candidates
                    ),
                    "orbitCertificateSha256": str(payload["orbitCertificateSha256"]),
                },
            )

    rows = sorted(
        selected.values(),
        key=lambda row: (
            -sum(
                2.0 ** (-int(target["teamCount"]))
                for target in row["compatibleLivePairs"]
            )
            / int(row["rootBundleSize"]),
            min(int(target["teamCount"]) for target in row["compatibleLivePairs"]),
            len(row["compatibleLivePairs"]) != 1,
            row["sourceLabel"],
            row["candidateR"],
            row["coefficientSha256"],
        ),
    )
    chunks = []
    current = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        path = args.output_prefix.with_name(
            f"{args.output_prefix.name}_{len(chunks):03d}.txt"
        )
        if path.exists():
            raise FileExistsError(path)
        rendered = "".join(row["coefficientLine"] + "\n" for row in current)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="ascii")
        chunks.append(
            {
                "path": str(path.resolve()),
                "rows": len(current),
                "bytes": len(rendered.encode("ascii")),
                "sha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
            }
        )
        current = []
        current_bytes = 0

    for row in rows:
        size = len(row["coefficientLine"].encode("ascii")) + 1
        if current and (len(current) >= args.max_lines or current_bytes + size > args.max_bytes):
            flush()
        current.append(row)
        current_bytes += size
    flush()

    payload = {
        "schemaVersion": "postarchive-index24-pair-wave-stage-v1",
        "method": "exact orbit-factor closure from newly accepted source polynomials",
        "inputArtifacts": len(paths),
        "sourcePolynomials": len(source_keys),
        "earlierSealedHashesExcluded": len(previously_selected),
        "earlierSealedPairsExcluded": len(previously_sealed_pairs),
        "selectedRows": len(rows),
        "safeUniquePairCount": len(safe_pairs),
        "safeUniquePairContentionCeiling": sum(2.0 ** (-value) for value in safe_pairs.values()),
        "safeUniquePairTeamCountDistribution": dict(sorted(Counter(safe_pairs.values()).items())),
        "possibleLivePairUnion": len(possible_pairs),
        "skipCounts": dict(sorted(skips.items())),
        "chunks": chunks,
        "entries": rows,
        "checks": {
            "allWorkerRowsCertifiedMulti": True,
            "allSourceProvenanceSelfConsistent": True,
            "allDegree24FactorCountsMatchOrbitCertificates": True,
            "allPolynomialHashesVerified": True,
            "ledgerAndReceiptsExcluded": True,
        },
    }
    args.certificate.parent.mkdir(parents=True, exist_ok=True)
    args.certificate.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: payload[key] for key in payload if key not in {"entries", "chunks"}} | {"chunkCount": len(chunks)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

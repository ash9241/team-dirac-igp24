#!/usr/bin/env sage -python
"""Deterministic, offline, nonduplicate pilot of the archived K1 hit family.

The archived ledger matches show that asymmetric functional compositions were
the most diverse scoreable K1 subfamily, and that every matched hit had r=8.
This worker generates new r=8 members of the same exact family and excludes
every coefficient hash present in either the archived batches or the current
ledger.  It never submits or stages to an outbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
from collections import Counter
from pathlib import Path

from sage.all import PolynomialRing, QQ


ROOT = Path(__file__).resolve().parent
ARCHIVE_ROOT = Path("/path/to/private-file")
SOURCE_SCRIPT = ARCHIVE_ROOT / "igp24_k1_hunter.sage"
SHAPES = [(3, 8), (4, 6), (6, 4), (8, 3), (2, 12)]
BATCH_PATTERNS = (
    re.compile(r"^k1_batch_\d+\.txt$"),
    re.compile(r"^rank_breaker_\d+\.txt$"),
    re.compile(r"^dynamic_batch_v1_\d+\.txt$"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_archive_hashes(archive_root: Path) -> tuple[set[str], dict]:
    hashes = set()
    line_count = 0
    files = 0
    for path in sorted(archive_root.iterdir()):
        if not path.is_file() or not any(pattern.match(path.name) for pattern in BATCH_PATTERNS):
            continue
        files += 1
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                values = [int(value.strip()) for value in raw_line.strip().split(",")]
                if len(values) != 25 or values[-1] != 1:
                    continue
                line = ",".join(map(str, values))
                hashes.add(hashlib.sha256(line.encode()).hexdigest())
                line_count += 1
    return hashes, {
        "batchFiles": files,
        "batchLines": line_count,
        "uniqueCoefficientHashes": len(hashes),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, default=ARCHIVE_ROOT)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--seed", type=int, default=240721)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "agent_gold_b_asymmetric_composition_pilot.txt",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=ROOT / "data" / "agent_gold_b_asymmetric_composition_pilot.json",
    )
    args = parser.parse_args()

    archive_hashes, archive_inventory = load_archive_hashes(args.archive_root)
    connection = sqlite3.connect(f"file:{args.db}?immutable=1", uri=True)
    rng = random.Random(args.seed)
    ring = PolynomialRing(QQ, "x")
    x = ring.gen()
    candidates = []
    pilot_hashes = set()
    rejections = Counter()
    attempts_by_shape = Counter()
    shape_cursor = 0

    while len(candidates) < args.count:
        outer_degree, inner_degree = SHAPES[shape_cursor % len(SHAPES)]
        shape_cursor += 1
        shape_name = f"{outer_degree}x{inner_degree}"
        attempts_by_shape[shape_name] += 1
        outer_coefficients = [rng.randint(-3, 3) for _ in range(outer_degree)] + [1]
        inner_coefficients = [rng.randint(-3, 3) for _ in range(inner_degree)] + [1]
        outer = ring(outer_coefficients)
        inner = ring(inner_coefficients)
        if not outer.is_irreducible():
            rejections["reducibleOuter"] += 1
            continue
        if not inner.is_irreducible():
            rejections["reducibleInner"] += 1
            continue
        polynomial = outer(inner)
        if polynomial.degree() != 24 or not polynomial.is_irreducible():
            rejections["reducibleComposition"] += 1
            continue
        real_roots = int(polynomial.number_of_real_roots())
        if real_roots != 8:
            rejections[f"signatureR{real_roots}"] += 1
            continue
        coefficients = [int(value) for value in polynomial.list()]
        coefficients.extend([0] * (25 - len(coefficients)))
        line = ",".join(map(str, coefficients))
        digest = hashlib.sha256(line.encode()).hexdigest()
        if digest in archive_hashes:
            rejections["archiveDuplicateHash"] += 1
            continue
        if digest in pilot_hashes:
            rejections["pilotDuplicateHash"] += 1
            continue
        ledger_row = connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? AND coefficients=? LIMIT 1",
            (digest, line),
        ).fetchone()
        if ledger_row is not None:
            rejections["ledgerDuplicateExact"] += 1
            continue
        pilot_hashes.add(digest)
        candidates.append(
            {
                "candidateCoefficientLine": line,
                "candidateSha256": digest,
                "compositionIrreducible": True,
                "exactConstruction": {
                    "innerCoefficients": inner_coefficients,
                    "innerDegree": inner_degree,
                    "innerIrreducible": True,
                    "outerCoefficients": outer_coefficients,
                    "outerDegree": outer_degree,
                    "outerIrreducible": True,
                    "relation": "candidate(x) = outer(inner(x))",
                },
                "index": len(candidates),
                "nonduplicateCertificate": {
                    "absentFromAllArchivedBatchHashes": True,
                    "absentFromCurrentLedgerExactCoefficients": True,
                    "distinctWithinPilot": True,
                },
                "realRoots": real_roots,
                "shape": shape_name,
            }
        )

    connection.close()
    manifest = "".join(row["candidateCoefficientLine"] + "\n" for row in candidates)
    args.output.resolve().write_text(manifest, encoding="utf-8")
    source_script = args.archive_root / SOURCE_SCRIPT.name
    audit = {
        "archiveHashInventory": archive_inventory,
        "candidateCount": len(candidates),
        "candidates": candidates,
        "certifiedLiveTargetHits": 0,
        "exactChecks": {
            "allCompositionIrreducible": all(row["compositionIrreducible"] for row in candidates),
            "allDegree24MonicInteger": True,
            "allPairwiseDistinct": len(pilot_hashes) == len(candidates),
            "allRealRoots8": all(row["realRoots"] == 8 for row in candidates),
            "allVerifiedAbsentFromArchiveAndLedger": all(
                all(value for value in row["nonduplicateCertificate"].values())
                for row in candidates
            ),
        },
        "familyEvidence": {
            "observedMatchedUniqueCoefficients": 7,
            "observedScoreableUniqueCoefficients": 7,
            "observedUniqueScoreablePairs": 6,
            "observedRealRootCount": 8,
            "sourceAudit": "data/agent_gold_b_archive_family_audit.json",
        },
        "ledger": str(args.db.resolve()),
        "manifest": str(args.output.resolve()),
        "manifestBytes": len(manifest.encode()),
        "manifestSha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "networkCalls": 0,
        "rejections": dict(sorted(rejections.items())),
        "seed": args.seed,
        "shapeAttempts": dict(sorted(attempts_by_shape.items())),
        "sourceScript": str(source_script.resolve()),
        "sourceScriptSha256": sha256_file(source_script),
        "submissionCalls": 0,
    }
    rendered = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    args.audit.resolve().write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "audit": str(args.audit.resolve()),
                "candidates": len(candidates),
                "manifest": str(args.output.resolve()),
                "manifestSha256": audit["manifestSha256"],
                "rejections": audit["rejections"],
                "status": "completed_offline_no_submission",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

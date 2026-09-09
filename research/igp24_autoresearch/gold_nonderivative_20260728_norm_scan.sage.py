#!/usr/bin/env sage -python
"""Exact norm-character sieve for g(u)=(u-a)(u-b), without q'(u).

The source universe is exactly the current-relevant 505 F5 quotient sources.
For every distinct a,b in [-12,12], this worker computes the signed
squarefree core of q(a)q(b) and retains it only when exact Frobenius character
alignment places that core in an index-two quotient character.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BOUND = 12


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE_HELPER = load_module(
    "gold_nonderivative_source_helper",
    ROOT / "gold_f5_f6_20260728_all_character_derivative_scan.sage.py",
)
CHARACTER = load_module(
    "gold_nonderivative_character_helper",
    ROOT / "character_kernel_gold_pilot.sage.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def read_rows(path: Path) -> list[dict]:
    rows = []
    seen = set()
    if not path.is_file():
        return rows
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if row.get("status") != "certified" or not isinstance(row.get("source"), dict):
            raise ValueError(f"invalid checkpoint row at {path}:{line_number}")
        digest = str(row["source"].get("coefficientSha256", ""))
        if len(digest) != 64 or digest in seen:
            raise ValueError(f"invalid or repeated checkpoint hash at {path}:{line_number}")
        seen.add(digest)
        rows.append(row)
    return rows


def atomic_write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="validate and continue an existing output checkpoint",
    )
    parser.add_argument(
        "--exclude-existing",
        action="append",
        default=[],
        type=Path,
        help="additional prior JSONL checkpoint whose source hashes must be skipped",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="maximum number of new sources to process in this invocation",
    )
    parser.add_argument(
        "--priority",
        choices=("coverage", "hash"),
        default="coverage",
        help="order fresh sources by current tc0 coverage or coefficient hash",
    )
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("invalid shard index")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.output.exists() and not args.resume:
        raise FileExistsError(f"refusing to overwrite {args.output}")

    current_tc0 = SOURCE_HELPER.load_current_tc0()
    all_sources = SOURCE_HELPER.load_sources(current_tc0)
    if not all_sources:
        raise ValueError("fresh current-relevant source universe is empty")

    checkpoint_rows = read_rows(args.output) if args.output.exists() else []
    completed_hashes = {
        str(row["source"]["coefficientSha256"]) for row in checkpoint_rows
    }
    prior_paths = set(
        DATA.glob("gold_nonderivative_20260728_norm_scan_shard*.jsonl")
    )
    prior_paths.update(path.resolve() for path in args.exclude_existing)
    for path in sorted(prior_paths):
        if path.resolve() == args.output.resolve():
            continue
        completed_hashes.update(
            str(row["source"]["coefficientSha256"]) for row in read_rows(path)
        )

    target_rs = {}
    for label, r in current_tc0:
        target_rs.setdefault(str(label), set()).add(int(r))

    def coverage_key(source: dict):
        pairs = {
            (str(row["action"]["targetLabel"]), r)
            for row in source["rows"]
            for r in target_rs.get(str(row["action"]["targetLabel"]), ())
        }
        labels = {
            str(row["action"]["targetLabel"]) for row in source["rows"]
        }
        return (-len(pairs), -len(labels), -len(source["rows"]), source["coefficientSha256"])

    remaining = [
        source
        for source in all_sources
        if source["coefficientSha256"] not in completed_hashes
    ]
    if args.priority == "coverage":
        remaining.sort(key=coverage_key)
    else:
        remaining.sort(key=lambda row: row["coefficientSha256"])
    sources = remaining[args.shard_index :: args.shard_count]
    if args.limit is not None:
        sources = sources[: args.limit]

    ring = PolynomialRing(ZZ, "u")
    parameters = tuple(range(-BOUND, BOUND + 1))
    rows = list(checkpoint_rows)
    started = time.monotonic()
    for position, source in enumerate(sources, 1):
        quotient_line = str(source["coefficientLine"])
        quotient_hash = hashlib.sha256(quotient_line.encode()).hexdigest()
        if quotient_hash != str(source["coefficientSha256"]):
            raise ValueError("source quotient hash mismatch")
        q = ring([ZZ(value) for value in quotient_line.split(",")])
        if q.degree() != 12 or not q.is_monic() or not q.is_irreducible():
            raise ValueError("source quotient failed exact gates")
        values = {parameter: ZZ(q(parameter)) for parameter in parameters}
        raw_cases = []
        probe_cores = set()
        for first_index, a in enumerate(parameters):
            if values[a] == 0:
                continue
            for b in parameters[first_index + 1 :]:
                if values[b] == 0:
                    continue
                norm = ZZ(values[a] * values[b])
                core = ZZ(norm).squarefree_part()
                raw_cases.append((a, b, norm, core))
                probe_cores.add(int(core))

        alignment = CHARACTER.character_alignment(
            q,
            int(source["quotientT12"]),
            probe_cores=sorted(probe_cores),
        )
        cases = []
        for a, b, norm, core in raw_cases:
            possible_labels = alignment["coreToPossibleLabels"].get(
                str(int(core)), []
            )
            if not possible_labels:
                continue
            ratio = ZZ(norm // core)
            if norm != core * ratio or ratio < 0 or not ratio.is_square():
                raise ArithmeticError("signed squarefree-core identity failed")
            cases.append(
                {
                    "a": int(a),
                    "b": int(b),
                    "norm": str(norm),
                    "possibleSourceLabels": sorted(possible_labels),
                    "squareFactorAbs": str(abs(ZZ(ratio).sqrt())),
                    "squarefreeNormCore": int(core),
                }
            )
        row = {
            "alignment": alignment,
            "caseCount": len(cases),
            "cases": cases,
            "quotientSha256": quotient_hash,
            "source": source,
            "status": "certified",
        }
        rows.append(row)
        atomic_write_rows(args.output, rows)
        if position % 10 == 0 or position == len(sources):
            print(
                canonical_json(
                    {
                        "checkpointRows": len(rows),
                        "completedBefore": len(completed_hashes),
                        "currentSourceUniverse": len(all_sources),
                        "event": "progress",
                        "newCases": sum(
                            value["caseCount"] for value in rows[len(checkpoint_rows) :]
                        ),
                        "position": position,
                        "shard": args.shard_index,
                        "sources": len(sources),
                    }
                ),
                flush=True,
            )

    print(
        canonical_json(
            {
                "caseCount": sum(value["caseCount"] for value in rows),
                "completedBefore": len(completed_hashes),
                "currentSourceUniverse": len(all_sources),
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "newSourceCount": len(sources),
                "output": str(args.output),
                "shard": args.shard_index,
                "sourceCount": len(rows),
                "status": "complete",
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

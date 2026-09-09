#!/usr/bin/env sage -python
"""Exact one-base alignment worker for blocked Cross1500 quotients.

This worker stays inside the direct character-kernel construction.  It reads
one locally verified even polynomial, aligns its degree-12 quotient against a
specified transitive quotient action, and writes an alignment artifact only
when at least one requested target label has an unambiguous rational norm
squareclass.  It performs no network or submission operation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("blocked_base_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--quotient-t", required=True, type=int)
    parser.add_argument("--target-label", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    helper = load_helper()
    source = helper.load_source(args.db, args.submission_id, args.polynomial_index)
    quotient_line = ",".join(str(value) for value in source["quotient"])
    quotient_sha256 = hashlib.sha256(quotient_line.encode()).hexdigest()
    alignment = helper.character_alignment(source["quotient"], args.quotient_t)
    source_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(
        source["label"], []
    )
    if source["squarefreeNormCore"] not in source_cores:
        print(
            json.dumps(
                {
                    "alignedTargetLabels": [],
                    "coefficientSha256": source["coefficientSha256"],
                    "polynomialIndex": args.polynomial_index,
                    "quotientSha256": quotient_sha256,
                    "quotientT": args.quotient_t,
                    "sourceLabel": source["label"],
                    "status": "source_alignment_mismatch",
                    "submissionId": args.submission_id,
                },
                sort_keys=True,
            )
        )
        return 2

    requested = sorted(set(args.target_label))
    aligned_targets = {
        label: alignment["labelToUnambiguousSquarefreeNormCores"].get(label, [])
        for label in requested
    }
    aligned_targets = {
        label: values for label, values in aligned_targets.items() if values
    }
    if not aligned_targets:
        print(
            json.dumps(
                {
                    "alignedTargetLabels": [],
                    "coefficientSha256": source["coefficientSha256"],
                    "polynomialIndex": args.polynomial_index,
                    "quotientSha256": quotient_sha256,
                    "quotientT": args.quotient_t,
                    "sourceLabel": source["label"],
                    "status": "no_unambiguous_target_core",
                    "submissionId": args.submission_id,
                },
                sort_keys=True,
            )
        )
        return 2

    result = {
        "alignment": alignment,
        "networkCalls": 0,
        "requestedTargetLabels": requested,
        "source": {key: value for key, value in source.items() if key != "quotient"},
        "submissionCalls": 0,
        "targetNormCores": aligned_targets,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    output = args.output.resolve()
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "alignedTargetLabels": sorted(aligned_targets),
                "artifact": str(output),
                "artifactSha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "coefficientSha256": source["coefficientSha256"],
                "polynomialIndex": args.polynomial_index,
                "quotientSha256": quotient_sha256,
                "quotientT": args.quotient_t,
                "sourceLabel": source["label"],
                "status": "aligned_target",
                "submissionId": args.submission_id,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

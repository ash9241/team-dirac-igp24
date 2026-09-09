"""Refine tower candidates with the exact ordered-affine degree-132 resolvent."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from routeA.oracle import unramified_cycle_patterns
from routeA.tower_quotient_screen import (
    compatible_degree24_labels,
    support_compatible_labels,
)


def refine(
    candidates,
    resolvent_lines,
    atlas,
    census,
    workers=3,
    primes=100,
):
    patterns = unramified_cycle_patterns(
        resolvent_lines, workers=workers, prime_count=primes
    )
    out = []
    for row, pats in zip(candidates, patterns):
        evidence = dict(row.get("quotient_evidence", {}))
        base = set(evidence.get("possible_quotient12", []))
        support = set(support_compatible_labels(pats, atlas))
        keep = sorted(base & support)
        if not keep:
            continue
        compatible = compatible_degree24_labels(
            census,
            quotient3=int(evidence["quotient3"]),
            quotient6=evidence["possible_quotient6"],
            quotient12=keep,
        )
        compatible = sorted(set(row.get("compatible_labels", [])) & set(compatible))
        if not compatible:
            continue
        rec = dict(row)
        evidence.update(
            {
                "possible_quotient12": keep,
                "ordered_affine_observed_patterns": len(pats),
                "ordered_affine_supported_quotient12": len(keep),
                "compatible_degree24_labels": len(compatible),
            }
        )
        rec["quotient_evidence"] = evidence
        rec["compatible_labels"] = compatible
        out.append(rec)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("candidates")
    p.add_argument("resolvent")
    p.add_argument("atlas")
    p.add_argument("census")
    p.add_argument("output")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--primes", type=int, default=100)
    a = p.parse_args()
    candidates = [
        json.loads(line)
        for line in Path(a.candidates).read_text().splitlines()
        if line.strip()
    ]
    resolvents = {
        row["candidate_hash"]: row["coefficients"]
        for row in (
            json.loads(line)
            for line in Path(a.resolvent).read_text().splitlines()
            if line.strip()
        )
    }
    missing = [row["candidate_hash"] for row in candidates if row["candidate_hash"] not in resolvents]
    if missing:
        raise ValueError(f"missing {len(missing)} candidate resolvents")
    census = [
        json.loads(line)
        for line in Path(a.census).read_text().splitlines()
        if line.strip()
    ]
    out = refine(
        candidates,
        [resolvents[row["candidate_hash"]] for row in candidates],
        json.loads(Path(a.atlas).read_text()),
        census,
        a.workers,
        a.primes,
    )
    Path(a.output).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in out)
        + ("\n" if out else "")
    )
    print(json.dumps({"input": len(candidates), "output": len(out)}))


if __name__ == "__main__":
    main()

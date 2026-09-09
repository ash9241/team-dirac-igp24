#!/usr/bin/env python3
"""Build one audited manual-fallback manifest from the pending exact registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "data" / "pending_exact_live_batches.json"
OUTPUT = ROOT / "outbox" / "pending_exact_live_combined.txt"
AUDIT = ROOT / "data" / "pending_exact_live_combined_audit.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def coefficient_hash(line: str) -> str:
    coefficients = line.split("#", 1)[0].strip()
    values = [part.strip() for part in coefficients.split(",")]
    if len(values) != 25 or values[-1] != "1":
        raise ValueError(f"not a monic degree-24 coefficient line: {line[:120]}")
    normalized = ",".join(str(int(value)) for value in values)
    return hashlib.sha256(normalized.encode()).hexdigest()


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    lines = []
    pairs = []
    manifest_rows = []
    seen_hashes = set()
    seen_pairs = set()
    for batch in registry["batches"]:
        path = ROOT / batch["manifest"]
        actual_sha = sha256_path(path)
        if actual_sha != batch["manifestSha256"]:
            raise ValueError(f"manifest hash mismatch: {path}")
        batch_lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(batch_lines) != len(batch["pairs"]):
            raise ValueError(f"line/pair count mismatch: {path}")
        for line, pair_row in zip(batch_lines, batch["pairs"], strict=True):
            digest = coefficient_hash(line)
            pair = (str(pair_row[0]), int(pair_row[1]))
            if digest in seen_hashes:
                raise ValueError(f"duplicate coefficient hash: {digest}")
            if pair in seen_pairs:
                raise ValueError(f"duplicate target pair: {pair}")
            seen_hashes.add(digest)
            seen_pairs.add(pair)
            lines.append(line)
            pairs.append({"coefficientSha256": digest, "label": pair[0], "r": pair[1]})
        manifest_rows.append(
            {
                "lines": len(batch_lines),
                "manifest": batch["manifest"],
                "sha256": actual_sha,
            }
        )
    rendered = ("\n".join(lines) + "\n").encode()
    expected_lines = sum(len(batch["pairs"]) for batch in registry["batches"])
    if len(lines) != expected_lines:
        raise ValueError(
            f"expected {expected_lines} pending exact lines, found {len(lines)}"
        )
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_bytes(rendered)
    temporary.replace(OUTPUT)
    audit = {
        "combinedManifest": str(OUTPUT.relative_to(ROOT)),
        "combinedManifestSha256": sha256_bytes(rendered),
        "distinctCoefficientHashes": len(seen_hashes),
        "distinctPairs": len(seen_pairs),
        "manifests": manifest_rows,
        "networkCalls": 0,
        "pairs": pairs,
        "registry": str(REGISTRY.relative_to(ROOT)),
        "registrySha256": sha256_path(REGISTRY),
        "submissionCalls": 0,
    }
    audit_rendered = (json.dumps(audit, indent=2, sort_keys=True) + "\n").encode()
    audit_temporary = AUDIT.with_suffix(AUDIT.suffix + ".tmp")
    audit_temporary.write_bytes(audit_rendered)
    audit_temporary.replace(AUDIT)
    print(json.dumps(audit, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

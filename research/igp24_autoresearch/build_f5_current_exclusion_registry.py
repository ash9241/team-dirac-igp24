#!/usr/bin/env python3
"""Seal current F5 exclusion inputs without arithmetic, staging, or network use."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
LEDGER = DATA / "ledger.sqlite3"
PAIR_RE = re.compile(r"^(24T[1-9][0-9]*)/r(0|2|4|6|8|10|12|14|16|18|20|22|24)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_line(raw: str) -> str | None:
    """Return one primitive monic degree-24 line, ignoring a # annotation."""
    payload = raw.split("#", 1)[0].strip()
    if not payload:
        return None
    try:
        values = [int(value.strip()) for value in payload.split(",")]
    except ValueError:
        return None
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] == 0
        or math.gcd(*values) != 1
    ):
        return None
    return ",".join(str(value) for value in values)


def valid_pair(label: object, r: object) -> tuple[str, int] | None:
    label = str(label)
    if re.fullmatch(r"24T[1-9][0-9]*", label) is None:
        return None
    try:
        signature = int(r)
    except (TypeError, ValueError):
        return None
    if signature < 0 or signature > 24 or signature % 2:
        return None
    return label, signature


def pair_from_text(value: object) -> tuple[str, int]:
    match = PAIR_RE.fullmatch(str(value))
    if match is None:
        raise ValueError(f"malformed mapped target pair: {value!r}")
    return match.group(1), int(match.group(2))


def pair_from_row(row: dict[str, Any]) -> tuple[str, int] | None:
    for label_key, r_key in (("targetLabel", "targetR"), ("label", "r")):
        if label_key in row and r_key in row:
            pair = valid_pair(row[label_key], row[r_key])
            if pair is not None:
                return pair
    for key in ("exactTarget", "target", "frozenGoldTarget", "liveTarget"):
        nested = row.get(key)
        if isinstance(nested, dict):
            pair = valid_pair(
                nested.get("label", nested.get("targetLabel")),
                nested.get("r", nested.get("targetR")),
            )
            if pair is not None:
                return pair
    return None


def roots(path: Path) -> Iterator[tuple[int, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                yield number, json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{number}") from exc
        return
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at {path}") from exc
    if isinstance(value, list):
        yield from enumerate(value, 1)
    else:
        yield 1, value


def objects(value: Any, ancestors: tuple[dict[str, Any], ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[dict[str, Any], ...]]]:
    if isinstance(value, list):
        for child in value:
            yield from objects(child, ancestors)
    elif isinstance(value, dict):
        yield value, ancestors
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from objects(child, ancestors + (value,))


def candidate_hash(row: dict[str, Any]) -> str | None:
    line = None
    for key in ("coefficientLine", "candidateCoefficientLine"):
        if isinstance(row.get(key), str):
            line = canonical_line(row[key])
            if line is not None:
                break
    declared = [
        str(row[key])
        for key in ("coefficientSha256", "candidateSha256")
        if row.get(key) is not None
    ]
    if any(SHA_RE.fullmatch(value) is None for value in declared):
        raise ValueError("malformed mapped candidate SHA-256")
    if line is None:
        # ``coefficientSha256`` alone is also used by source provenance rows.
        # A payload-free candidate map must say explicitly that it is a candidate.
        explicit = row.get("candidateSha256")
        return str(explicit) if explicit is not None else None
    digest = sha256_bytes(line.encode("utf-8"))
    if declared and any(value != digest for value in declared):
        raise ValueError("mapped candidate line/hash disagreement")
    return digest


def inherited_pair(ancestors: tuple[dict[str, Any], ...]) -> tuple[str, int] | None:
    for row in reversed(ancestors):
        pair = pair_from_row(row)
        if pair is not None:
            return pair
    return None


def is_f5_artifact(path: Path) -> bool:
    return "f5" in str(path.relative_to(ROOT)).lower()


def scan_exact_artifacts() -> tuple[dict[str, set[tuple[str, int]]], set[str], set[tuple[str, int]], list[dict[str, str]]]:
    """Map exact artifact candidate hashes to target pairs; reject malformed maps."""
    mapping: dict[str, set[tuple[str, int]]] = defaultdict(set)
    direct_hashes: set[str] = set()
    direct_pairs: set[tuple[str, int]] = set()
    boundary: list[dict[str, str]] = []
    paths = sorted(
        path for path in DATA.rglob("*") if path.is_file() and path.suffix in {".json", ".jsonl"}
    )
    for path in paths:
        mapped_here = False
        direct = "agent_f5_direct_" in path.name
        try:
            entries = list(roots(path))
        except ValueError:
            if direct or is_f5_artifact(path):
                raise
            continue
        for _number, root in entries:
            for row, ancestors in objects(root):
                digest = candidate_hash(row)
                pair = pair_from_row(row) or inherited_pair(ancestors)
                # Candidate hashes only become pair maps with an explicit target.
                if digest is not None and pair is not None:
                    mapping[digest].add(pair)
                    mapped_here = True
                if direct:
                    # A direct certificate/result must not conceal malformed target/hash data.
                    looks_direct = any(
                        key in row for key in ("routeIdentity", "action", "actions", "candidate", "candidates")
                    )
                    # The coefficient-bearing candidate is normally nested
                    # below the direct-result envelope, so path membership is
                    # the identity gate, not the current object shape.
                    if digest is not None:
                        direct_hashes.add(digest)
                    if pair is not None and (digest is not None or looks_direct):
                        direct_pairs.add(pair)
                    route = row.get("routeIdentity")
                    if isinstance(route, dict):
                        if "targetPairs" in route:
                            if not isinstance(route["targetPairs"], list):
                                raise ValueError("malformed direct route target pair list")
                            for item in route["targetPairs"]:
                                if not isinstance(item, dict):
                                    raise ValueError("malformed direct route target pair")
                                parsed = valid_pair(item.get("label"), item.get("r"))
                                if parsed is None:
                                    raise ValueError("malformed direct route target pair")
                                direct_pairs.add(parsed)
                        elif "targetLabel" in route or "targetR" in route:
                            parsed = valid_pair(route.get("targetLabel"), route.get("targetR"))
                            if parsed is None:
                                raise ValueError("malformed direct route target pair")
                            direct_pairs.add(parsed)
        if mapped_here or direct:
            boundary.append(
                {"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)}
            )
    return mapping, direct_hashes, direct_pairs, boundary


def mapped_receipt_pairs(active_hashes: set[str], mapping: dict[str, set[tuple[str, int]]]) -> set[tuple[str, int]]:
    pairs = set()
    for digest in active_hashes:
        pairs.update(mapping.get(digest, set()))
    for path in sorted(DATA.rglob("*receipt_mapping*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value.get("receiptPolynomialPossiblePairs")
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError(f"malformed receipt mapping rows: {path}")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"malformed receipt mapping row: {path}")
            digest = str(row.get("coefficientSha256", ""))
            if SHA_RE.fullmatch(digest) is None:
                raise ValueError(f"malformed receipt mapping digest: {path}")
            possible = row.get("possiblePairs")
            if not isinstance(possible, list):
                raise ValueError(f"malformed receipt mapping pairs: {path}")
            parsed = {pair_from_text(item) for item in possible}
            if digest in active_hashes:
                pairs.update(parsed)
    return pairs


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
            __import__("os").fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_registry() -> dict[str, Any]:
    """Recompute the complete current exclusion boundary without writing."""
    mapping, direct_hashes, direct_pairs, exact_boundary = scan_exact_artifacts()
    connection = sqlite3.connect(f"file:{LEDGER.resolve()}?mode=ro", uri=True)
    try:
        baseline_pairs = {(str(label), int(r)) for label, r in connection.execute("SELECT label,r FROM baseline_pairs")}
        accepted_pairs = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE status='accepted' AND label IS NOT NULL AND r IS NOT NULL"
            )
        }
    finally:
        connection.close()

    receipt_hashes: set[str] = set()
    receipt_boundary: list[dict[str, object]] = []
    for path in sorted(RECEIPTS.glob("sub_*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError(f"receipt is not an object: {path}")
        manifest_value = receipt.get("manifest")
        recorded = receipt.get("manifestHash")
        status = "missing_or_unpinned"
        used = 0
        if isinstance(manifest_value, str) and isinstance(recorded, str):
            manifest = Path(manifest_value).expanduser().resolve()
            if manifest.is_file() and sha256_path(manifest) == recorded:
                status = "intact"
                for raw in manifest.read_text(encoding="utf-8").splitlines():
                    line = canonical_line(raw)
                    if line is not None:
                        receipt_hashes.add(sha256_bytes(line.encode("utf-8")))
                        used += 1
            else:
                status = "missing_or_changed"
        receipt_boundary.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
                "manifestHash": recorded if isinstance(recorded, str) else None,
                "manifestStatus": status,
                "canonicalHashCount": used,
            }
        )

    outbox_hashes: set[str] = set()
    outbox_boundary: list[dict[str, object]] = []
    for path in sorted(OUTBOX.glob("*.txt")):
        count = 0
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = canonical_line(raw)
            if line is not None:
                outbox_hashes.add(sha256_bytes(line.encode("utf-8")))
                count += 1
        outbox_boundary.append(
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_path(path),
                "canonicalHashCount": count,
            }
        )

    active_hashes = receipt_hashes | outbox_hashes
    blocked_pairs = (
        baseline_pairs
        | accepted_pairs
        | mapped_receipt_pairs(active_hashes, mapping)
        | direct_pairs
    )
    blocked_hashes = active_hashes | direct_hashes
    return {
        "schemaVersion": "f5-current-exclusion-registry-v1",
        "status": "sealed_current_exclusions",
        "blockedPairs": [
            {"label": label, "r": signature}
            for label, signature in sorted(blocked_pairs, key=lambda pair: (int(pair[0][3:]), pair[1]))
        ],
        "blockedHashes": sorted(blocked_hashes),
        "counts": {
            "acceptedPairs": len(accepted_pairs),
            "baselinePairs": len(baseline_pairs),
            "blockedHashes": len(blocked_hashes),
            "blockedPairs": len(blocked_pairs),
            "directCandidateHashes": len(direct_hashes),
            "directTargetPairs": len(direct_pairs),
            "intactReceiptHashes": len(receipt_hashes),
            "outboxHashes": len(outbox_hashes),
        },
        "sourceBoundary": {
            "ledger": {
                "path": str(LEDGER.relative_to(ROOT)),
                "acceptedPairSetSha256": sha256_bytes(
                    json.dumps(
                        sorted(accepted_pairs, key=lambda pair: (int(pair[0][3:]), pair[1])),
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
                "baselinePairSetSha256": sha256_bytes(
                    json.dumps(
                        sorted(baseline_pairs, key=lambda pair: (int(pair[0][3:]), pair[1])),
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
            },
            "receipts": receipt_boundary,
            "outboxes": outbox_boundary,
            "exactArtifactsUsedForHashPairMaps": exact_boundary,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA / "f5_current_exclusion_registry.json",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="Recompute the boundary and require byte-equivalent JSON content",
    )
    args = parser.parse_args()
    result = build_registry()
    if args.verify is not None:
        expected_path = args.verify.expanduser().resolve()
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        if result != expected:
            raise ValueError("current exclusions differ from the sealed registry")
        print(
            json.dumps(
                {
                    "status": "verified_current_exclusions",
                    "registry": str(expected_path),
                    "sha256": sha256_path(expected_path),
                },
                sort_keys=True,
            )
        )
        return 0
    output = args.output.expanduser().resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_write(output, text)
    print(json.dumps({"output": str(output), "sha256": sha256_bytes(text.encode("utf-8"))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

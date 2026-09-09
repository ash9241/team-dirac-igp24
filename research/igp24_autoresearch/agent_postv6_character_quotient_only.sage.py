#!/usr/bin/env sage -python
"""Offline character search from a verified lower-Kummer even source.

The established character workers require the degree-24 source label itself
to be a full ``2^11`` character-kernel action.  A lower-Kummer source can
still provide an exact degree-12 quotient field, but it must not be used as a
character-label witness.  This narrow adapter keeps those two claims
separate:

* the immutable ledger proves the pinned, accepted, scoreable even source;
* the source transitive group proves the pinned 12-block quotient action;
* Frobenius character alignment proves the requested target norm core; and
* the existing S-unit/maximal-subgroup worker proves any produced candidate.

There are no network or submission calls.  Standard output is always
coefficient-safe.  Candidate payloads are written only to new, explicitly
named output files with non-overwrite semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import ZZ, libgap


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import helper from {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


HELPER = load_module(
    "postv6_character_kernel_shared",
    ROOT / "character_kernel_gold_pilot.sage.py",
)
SIGNATURE = load_module(
    "postv6_character_signature_shared",
    ROOT / "agent_gold_b_character_signature_search.sage.py",
)


def coefficient_sha256(values) -> str:
    canonical = ",".join(str(ZZ(value)) for value in values)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def source_quotient_structures(source_t: int) -> list[dict]:
    """Return every distinct 12-by-2 block quotient of the source action."""
    group = libgap.TransitiveGroup(24, source_t)
    structures = {}
    for block in libgap.AllBlocks(group):
        if int(libgap.Length(block)) != 2:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        if int(libgap.Length(blocks)) != 12:
            continue
        action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
        quotient = libgap.Image(action)
        row = {
            "blockCount": 12,
            "blockKernelOrder": int(libgap.Size(libgap.Kernel(action))),
            "quotientOrder": int(libgap.Size(quotient)),
            "quotientT": int(libgap.TransitiveIdentification(quotient)),
        }
        key = (
            row["blockKernelOrder"],
            row["quotientOrder"],
            row["quotientT"],
        )
        structures[key] = row
    return [structures[key] for key in sorted(structures)]


def require_new_file(path: Path, label: str) -> Path:
    destination = path.resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite {label}: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"parent directory for {label} does not exist: {destination.parent}"
        )
    return destination


def write_new_text(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()


def unique_certified_rows(result: dict) -> list[dict]:
    by_hash = {}
    for row in result.get("candidateResults", []):
        if not str(row.get("status", "")).startswith("certified_"):
            continue
        digest = str(row.get("candidateSha256", ""))
        line = row.get("candidateCoefficientLine")
        if len(digest) != 64 or not isinstance(line, str):
            raise ArithmeticError("certified row is missing its coefficient proof")
        if hashlib.sha256(line.encode("ascii")).hexdigest() != digest:
            raise ArithmeticError("certified row coefficient hash changed")
        by_hash[digest] = row
    return [by_hash[key] for key in sorted(by_hash)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", required=True, type=int)
    parser.add_argument("--expected-source-label", required=True)
    parser.add_argument("--expected-source-r", required=True, type=int)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--quotient-sha256", required=True)
    parser.add_argument("--quotient-t", required=True, type=int)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--target-r", required=True, type=int)
    parser.add_argument("--norm-core", required=True, type=int)
    parser.add_argument("--aux-primes", default="")
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--solutions-per-sign", type=int, default=2)
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    if args.norm_core <= 0:
        raise ValueError("--norm-core must be positive")
    if args.target_r < 0 or args.target_r > 24 or args.target_r % 4:
        raise ValueError("character target r must be divisible by 4 in [0,24]")
    if len(args.source_sha256) != 64 or len(args.quotient_sha256) != 64:
        raise ValueError("pinned SHA-256 values must contain 64 hex characters")
    int(args.source_sha256, 16)
    int(args.quotient_sha256, 16)

    source = HELPER.load_source(
        args.db, args.submission_id, args.polynomial_index
    )
    if source["status"] != "accepted" or not source["scoreable"]:
        raise ValueError("source is not an accepted scoreable verification")
    if source["label"] != args.expected_source_label:
        raise ValueError("verified source label does not match its pin")
    if int(source["r"]) != args.expected_source_r:
        raise ValueError("verified source signature does not match its pin")
    if source["coefficientSha256"] != args.source_sha256:
        raise ValueError("verified source coefficient hash does not match its pin")

    quotient_sha256 = coefficient_sha256(source["quotient"])
    if quotient_sha256 != args.quotient_sha256:
        raise ValueError("exact quotient coefficient hash does not match its pin")

    source_t = HELPER.parse_label(source["label"])
    structures = source_quotient_structures(source_t)
    matching_structures = [
        row for row in structures if row["quotientT"] == args.quotient_t
    ]
    if len(matching_structures) != 1:
        raise ValueError(
            "verified source does not have one uniquely pinned quotient action"
        )
    source_structure = matching_structures[0]

    target_t = HELPER.parse_label(args.target_label)
    target_structure = HELPER.target_structure(target_t)
    if int(target_structure["quotientT"]) != args.quotient_t:
        raise ValueError("target character action has the wrong quotient T-number")

    live_target = SIGNATURE.load_live_gold(
        args.db, args.target_label, args.target_r
    )
    alignment = HELPER.character_alignment(
        source["quotient"], args.quotient_t, [args.norm_core]
    )
    target_cores = alignment[
        "labelToUnambiguousSquarefreeNormCores"
    ].get(args.target_label, [])
    if target_cores != [args.norm_core]:
        raise ValueError(
            "pinned norm core is not uniquely Frobenius-aligned to the target"
        )

    audit = {
        "characterAlignment": alignment,
        "liveTarget": live_target,
        "networkCalls": 0,
        "provenanceMode": "verified_even_quotient_only",
        "source": {
            key: value for key, value in source.items() if key != "quotient"
        },
        "sourceQuotientSha256": quotient_sha256,
        "sourceQuotientStructure": source_structure,
        "submissionCalls": 0,
        "targetStructure": target_structure,
    }
    safe_preflight = {
        "liveTarget": live_target["pair"],
        "networkCalls": 0,
        "normCore": args.norm_core,
        "provenanceMode": audit["provenanceMode"],
        "quotientSha256": quotient_sha256,
        "quotientT": args.quotient_t,
        "sourceLabel": source["label"],
        "sourceSha256": source["coefficientSha256"],
        "status": "preflight_passed",
        "submissionCalls": 0,
        "targetLabel": args.target_label,
        "targetR": args.target_r,
    }
    if args.preflight_only:
        if args.output is not None or args.manifest is not None:
            raise ValueError("preflight-only mode does not write output files")
        print(json.dumps(safe_preflight, sort_keys=True))
        return 0

    if args.output is None or args.manifest is None:
        raise ValueError("full search requires isolated --output and --manifest paths")
    output = require_new_file(args.output, "result output")
    manifest = require_new_file(args.manifest, "candidate manifest")
    if output == manifest:
        raise ValueError("result output and candidate manifest must be distinct")

    auxiliary = [
        int(value) for value in args.aux_primes.split(",") if value.strip()
    ]
    search_result = SIGNATURE.search(
        source,
        target_t,
        args.target_r,
        ZZ(args.norm_core),
        auxiliary,
        args.max_candidates,
        args.solutions_per_sign,
        args.witness_primes,
        args.seed,
    )
    result = {"audit": audit, "search": search_result}
    certified = unique_certified_rows(search_result)
    if len(certified) > 1:
        raise ValueError(
            f"refusing to stage multiple distinct certified rows: {len(certified)}"
        )
    if certified:
        manifest_text = certified[0]["candidateCoefficientLine"] + "\n"
        write_new_text(manifest, manifest_text)
        result["staging"] = {
            "bytes": len(manifest_text.encode("ascii")),
            "candidateSha256": certified[0]["candidateSha256"],
            "manifest": str(manifest),
            "manifestSha256": hashlib.sha256(
                manifest_text.encode("ascii")
            ).hexdigest(),
            "polynomials": 1,
            "submissionCalls": 0,
        }
    write_new_text(output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "certified": len(certified),
                "manifest": str(manifest) if certified else None,
                "output": str(output),
                "status": search_result["status"],
                "submissionCalls": 0,
            },
            sort_keys=True,
        )
    )
    return 0 if certified else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal staged T00134 twists in the repository's generic exact-corpus schema.

The staged manifest is the coefficient source of truth.  This worker rejoins
each line to its coefficient-free candidate proof, verifies every hash and
source/action field, and emits the exact schema consumed by
``stage_single_exact_census``.  That makes text-outbox pair reservations
recoverable by all later audits before any submission receipt exists.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import fresh_t00134_twist_route_audit as base
import stage_single_exact_census as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST = ROOT / "outbox/fresh_t00134_twist_top10000_43.txt"
CANDIDATES = DATA / "fresh_t00134_twist_top10000_candidates_20260730.jsonl"
STAGE_CERTIFICATE = (
    DATA / "fresh_t00134_twist_top10000_stage_certificate_20260730.json"
)
EXACT_ROWS = (
    DATA / "fresh_t00134_twist_top10000_exact_index_20260730.jsonl"
)
CERTIFICATE = (
    DATA / "fresh_t00134_twist_top10000_exact_index_certificate_20260730.json"
)

EXPECTED_SHA256 = {
    MANIFEST: "ec6f85c5bbab47881896702aeb4b0e06fd6eeb4dee51828e7d52ec538b641440",
    CANDIDATES: "150a4a366e7cf335e6fb91f12086003fe032212da87a18203d9c664b5a3b5ce4",
    STAGE_CERTIFICATE: "7d0aec06ecb1640da573a6fa16267caf668abd742787af1611d04874aaf03cc5",
}


def main() -> int:
    if EXACT_ROWS.exists() or CERTIFICATE.exists():
        raise FileExistsError("refusing to overwrite exact twist index")
    for path, expected in EXPECTED_SHA256.items():
        actual = base.sha256_path(path)
        if actual != expected:
            raise ValueError(f"staged input changed: {path}: {actual}")

    lines = [
        exact.canonical_polynomial_line(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 43 or any(line is None for line in lines):
        raise ValueError("staged manifest is not 43 canonical polynomials")
    candidates = base.read_jsonl(CANDIDATES)
    if len(candidates) != len(lines):
        raise ValueError("candidate/manifest row counts disagree")

    actions = {
        str(row["sourceLabel"]): row
        for row in base.read_jsonl(base.ACTION_MAP)
    }
    exact_rows = []
    pairs = set()
    hashes = set()
    for index, (line, candidate) in enumerate(zip(lines, candidates)):
        if int(candidate["manifestIndex"]) != index:
            raise ValueError("candidate manifest index is not contiguous")
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        if digest != str(candidate["coefficientSha256"]):
            raise ValueError("candidate hash does not match staged line")
        action = actions[str(candidate["sourceLabel"])]
        target_label = base.unanimous_target(action)
        if target_label != str(candidate["targetLabel"]):
            raise ValueError("candidate action target is no longer unanimous")
        systems = action.get("systems") or []
        pair = (target_label, int(candidate["targetR"]))
        row = {
            "status": "certified_generic_quadratic_twist_staged",
            "coefficientLine": line,
            "coefficientSha256": digest,
            "coefficientBytes": len(line.encode("ascii")),
            "polynomialDiscriminantAbs": str(
                candidate["polynomialDiscriminantAbs"]
            ),
            "fieldDiscriminantAbs": candidate.get("fieldDiscriminantAbs"),
            "sourceSubmissionId": str(candidate["sourceSubmissionId"]),
            "sourcePolynomialIndex": int(
                candidate["sourcePolynomialIndex"]
            ),
            "sourceCoefficientSha256": str(
                candidate["sourceCoefficientSha256"]
            ),
            "sourceLabel": str(candidate["sourceLabel"]),
            "sourceR": int(candidate["sourceR"]),
            "sourceFieldDiscAbs": str(
                candidate["sourceFieldDiscriminantAbs"]
            ),
            "targetLabel": pair[0],
            "targetT": int(pair[0][3:]),
            "targetR": pair[1],
            "twistSign": str(candidate["sign"]),
            "twistD": int(candidate["twistD"]),
            "ramificationPrime": int(candidate["ramificationPrime"]),
            "twistDirectRealRootCount": pair[1],
            "twistIrreducible": True,
            "sourceSquarefreeModRamificationPrime": True,
            "actionResolutionMethod": "all-block-systems-same-target",
            "actionSystemCount": len(systems),
            "allBlockSystemsTargetLabels": [
                str(system["targetLabel"]) for system in systems
            ],
            "genericActionProof": (
                "The source is squarefree modulo the displayed odd prime, "
                "so its splitting field is unramified there; "
                "Q(sqrt(twistD)) is ramified and linearly disjoint. Every "
                "exact source 12x2 block system has the same displayed "
                "global-flip target label, whose action is transitive."
            ),
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        # Exercise the exact validator directly before sealing.
        accepted = exact.validate_twist(
            row,
            (),
            EXACT_ROWS,
            index + 1,
            "/",
        )
        if accepted is None:
            raise ValueError(f"generic exact-corpus validator rejected row {index}")
        if (
            str(accepted["coefficientSha256"]) != digest
            or (str(accepted["targetLabel"]), int(accepted["targetR"]))
            != pair
        ):
            raise ValueError("validator changed the staged hash/pair")
        exact_rows.append(row)
        hashes.add(digest)
        pairs.add(pair)
    if len(hashes) != 43 or len(pairs) != 43:
        raise ValueError("exact index is not hash/pair one-to-one")

    payload = "".join(base.canonical_json(row) + "\n" for row in exact_rows)
    certificate = {
        "schemaVersion": "fresh-t00134-twist-exact-index-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_43_generic_twists_indexed_for_outbox_pair_exclusion",
        "rows": len(exact_rows),
        "distinctHashes": len(hashes),
        "distinctPairs": len(pairs),
        "validator": "stage_single_exact_census.validate_twist",
        "exactIndex": {
            "path": str(EXACT_ROWS.relative_to(ROOT)),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "coefficientMaterialIncluded": True,
        },
        "stagedInputs": {
            str(path.relative_to(ROOT)): {
                "sha256": expected,
            }
            for path, expected in EXPECTED_SHA256.items()
        },
        "sideEffects": {
            "outboxWrites": 0,
            "ledgerWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
    }
    base.exclusive_text(EXACT_ROWS, payload)
    base.exclusive_text(
        CERTIFICATE,
        json.dumps(certificate, indent=2, sort_keys=True) + "\n",
    )
    print(
        json.dumps(
            {
                "status": certificate["status"],
                "exactIndex": str(EXACT_ROWS.relative_to(ROOT)),
                "exactIndexSha256": base.sha256_path(EXACT_ROWS),
                "certificate": str(CERTIFICATE.relative_to(ROOT)),
                "certificateSha256": base.sha256_path(CERTIFICATE),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

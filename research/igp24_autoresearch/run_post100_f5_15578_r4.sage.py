#!/usr/bin/env sage -python
"""Resolve the isolated post-100 24T15578/r4 pair-product route.

The source must be the verifier-accepted negative twist committed in
``sub_077547...``.  A cached exact group-action certificate proves that its
literal degree-12 quotient has one size-12 unordered-pair orbit and that the
associated signed degree-24 action is 24T9490.  The execution path constructs
and factors that one quotient pair resolvent, determines the exact real
signature, and stages one row only when the realized pair and coefficient hash
remain locally novel.

The default preflight performs no resolvent construction.  ``--execute`` is
the only heavy path.  The worker is offline and never submits or writes the
ledger.  Its certificate and terminal summary contain no coefficients.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from fractions import Fraction
from pathlib import Path

from sage.all import PolynomialRing, ZZ

import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
ACTION_SHARDS = tuple(
    DATA / f"agent_f5_full_ledger_pair_product_actions_shard{index}of4.jsonl"
    for index in range(4)
)
SOURCE_CERTIFICATE = DATA / "v14_negative_twist_15578_r4_certificate.json"
SOURCE_RECEIPT = RECEIPTS / "sub_077547629a4e41c490ddebe2d0cf84c4.json"
MANIFEST = OUTBOX / "post100_f5_15578_r4_to_9490.txt"
CERTIFICATE = DATA / "post100_f5_15578_r4_to_9490_certificate.json"
SUMMARY = DATA / "post100_f5_15578_r4_to_9490_summary.json"

SOURCE = {
    "submissionId": "sub_077547629a4e41c490ddebe2d0cf84c4",
    "polynomialIndex": 0,
    "coefficientSha256": "41111e9201a3a90ef1d6e87525e3e6ee2ed3de7aadce69a01b6f9e492750a051",
    "label": "24T15578",
    "r": 4,
}
TARGET_LABEL = "24T9490"
EXPECTED_POSSIBLE_R = {0, 4, 8, 12, 16}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def canonical_json_sha256(value: dict) -> str:
    return sha256_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite sealed output: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def outbox_hashes() -> set[str]:
    result = set()
    for path in sorted(OUTBOX.glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = single.canonical_polynomial_line(raw.strip())
            if line is not None:
                result.add(sha256_bytes(line.encode("ascii")))
    return result


def validate_source_envelope(connection: sqlite3.Connection) -> tuple[sqlite3.Row, list[int]]:
    construction = json.loads(SOURCE_CERTIFICATE.read_text(encoding="utf-8"))
    receipt = json.loads(SOURCE_RECEIPT.read_text(encoding="utf-8"))
    target = construction.get("target") or {}
    manifest = construction.get("manifest") or {}
    response = receipt.get("response") or {}
    if (
        construction.get("coefficientMaterialIncluded") is not False
        or not str(construction.get("status", "")).startswith("certified_exact_safe")
        or str(target.get("coefficientSha256")) != SOURCE["coefficientSha256"]
        or str(target.get("label")) != SOURCE["label"]
        or int(target.get("r", -1)) != SOURCE["r"]
        or receipt.get("commit") is not True
        or int(receipt.get("knownLocalHashes", -1)) != 0
        or str(receipt.get("manifestHash")) != str(manifest.get("sha256"))
        or str(response.get("submissionId")) != SOURCE["submissionId"]
        or int(response.get("rejectedCount", -1)) != 0
        or list(response.get("failedPolynomials") or [])
    ):
        raise ValueError("source construction/receipt envelope is inconsistent")
    row = connection.execute(
        "SELECT p.coefficients,p.coefficient_hash,v.status,v.label,v.r,v.scoreable,"
        "v.in_baseline,v.scoring_status FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
        "AND p.polynomial_index=?",
        (SOURCE["submissionId"], SOURCE["polynomialIndex"]),
    ).fetchone()
    if row is None or (
        str(row["coefficient_hash"]) != SOURCE["coefficientSha256"]
        or str(row["status"]) != "accepted"
        or str(row["label"]) != SOURCE["label"]
        or int(row["r"]) != SOURCE["r"]
        or int(row["scoreable"] or 0) != 1
        or int(row["in_baseline"] or 0) != 0
        or str(row["scoring_status"]) != "scoreable"
    ):
        raise ValueError("source is not the accepted-scoreable verifier anchor")
    line = str(row["coefficients"])
    if sha256_bytes(line.encode("ascii")) != SOURCE["coefficientSha256"]:
        raise ValueError("ledger source coefficient hash mismatch")
    values = [int(value) for value in line.split(",")]
    if (
        len(values) != 25
        or values[-1] != 1
        or values[0] == 0
        or math.gcd(*values) != 1
        or any(values[index] for index in range(1, 25, 2))
    ):
        raise ValueError("source is not a primitive monic even degree-24 polynomial")
    return row, values


def validate_action() -> tuple[dict, dict]:
    twist_rows = [
        row
        for row in read_jsonl(ACTION_MAP)
        if str(row.get("sourceLabel")) == SOURCE["label"]
    ]
    if len(twist_rows) != 1:
        raise ValueError("source does not have one cached literal block system row")
    twist = twist_rows[0]
    if int(twist.get("systemCount", -1)) != 1 or len(twist.get("systems") or []) != 1:
        raise ValueError("source block system is not structurally unique")

    actions = []
    for path in ACTION_SHARDS:
        actions.extend(
            row
            for row in read_jsonl(path)
            if str(row.get("sourceLabel")) == SOURCE["label"]
        )
    if len(actions) != 1:
        raise ValueError("source does not have one cached size-12 pair-product action")
    action = actions[0]
    possible = {
        int(value)
        for value in (action.get("sourceSignatureToPossibleTargetSignatures") or {}).get(
            str(SOURCE["r"]), []
        )
    }
    if (
        str(action.get("targetLabel")) != TARGET_LABEL
        or int(action.get("targetT", -1)) != int(TARGET_LABEL[3:])
        or len(action.get("pairOrbit") or []) != 12
        or possible != EXPECTED_POSSIBLE_R
    ):
        raise ValueError("cached pair-product action differs from the sealed route")
    return twist, action


def pair_state(connection: sqlite3.Connection, pair: tuple[str, int], receipt_pairs: set) -> dict:
    target = connection.execute(
        "SELECT discovered,team_count FROM targets WHERE label=? AND r=?", pair
    ).fetchone()
    if target is None:
        raise ValueError(f"target catalogue is missing {pair}")
    return {
        "baseline": bool(
            connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()
        ),
        "discovered": bool(target["discovered"]),
        "locallyKnown": bool(
            connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
            ).fetchone()
        ),
        "locallyOwned": bool(
            connection.execute(
                "SELECT 1 FROM verifications WHERE scoreable=1 AND label=? AND r=? LIMIT 1",
                pair,
            ).fetchone()
        ),
        "receiptPair": pair in receipt_pairs,
        "teamCount": int(target["team_count"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="construct and factor the exact pair resolvent; otherwise preflight only",
    )
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        _, source_values = validate_source_envelope(connection)
        twist, action = validate_action()
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, {}
        )
        possible_states = {
            f"{TARGET_LABEL}/r{signature}": pair_state(
                connection, (TARGET_LABEL, signature), receipt_pairs
            )
            for signature in sorted(EXPECTED_POSSIBLE_R)
        }
        if any(
            state["baseline"]
            or state["locallyKnown"]
            or state["locallyOwned"]
            or state["receiptPair"]
            for state in possible_states.values()
        ):
            raise ValueError("one possible realized target is no longer locally novel")

        preflight = {
            "schemaVersion": "post100-f5-15578-r4-preflight-v1",
            "status": "ready_for_one_isolated_heavy_resolvent" if not args.execute else "executing",
            "coefficientMaterialIncluded": False,
            "source": {**SOURCE, "ledgerStatus": "accepted_scoreable_nonbaseline"},
            "action": {
                "sourceLabel": SOURCE["label"],
                "targetLabel": TARGET_LABEL,
                "possibleTargetR": sorted(EXPECTED_POSSIBLE_R),
                "uniqueLiteralBlockSystems": 1,
                "uniqueSize12PairProductOrbits": 1,
                "rowSha256": canonical_json_sha256(action),
            },
            "possibleTargetState": possible_states,
            "sideEffects": {
                "heavyWorkersLaunched": 0 if not args.execute else 1,
                "ledgerWrites": 0,
                "networkCalls": 0,
                "submissionCalls": 0,
            },
        }
        if not args.execute:
            print(json.dumps(preflight, indent=2, sort_keys=True))
            return 0

        for path in (MANIFEST, CERTIFICATE, SUMMARY):
            if path.exists():
                raise ValueError(f"refusing to overwrite isolated output: {path}")

        ring_y = PolynomialRing(ZZ, "y")
        quotient = ring_y([ZZ(value) for value in source_values[::2]])
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("source quotient is not monic irreducible degree 12")
        pair_resolvent = quotient.symmetric_power(2, monic=True)
        factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
        factor_degrees = [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ]
        if any(row["exponent"] != 1 for row in factor_degrees):
            raise ValueError("pair resolvent is not squarefree")
        selected = [factor for factor, exponent in factors if factor.degree() == 12]
        if len(selected) != 1:
            raise ValueError("pair resolvent does not have one unique degree-12 factor")

        ring_x = PolynomialRing(ZZ, "x")
        x = ring_x.gen()
        candidate = ring_x(selected[0])(x**2)
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("signed pair-product candidate is not monic irreducible degree 24")
        signature = int(candidate.number_of_real_roots())
        if signature not in EXPECTED_POSSIBLE_R:
            raise ValueError("realized signature is outside the exact action profile")
        target_pair = (TARGET_LABEL, signature)
        target_state = pair_state(connection, target_pair, receipt_pairs)
        if any(
            target_state[key]
            for key in ("baseline", "locallyKnown", "locallyOwned", "receiptPair")
        ):
            raise ValueError("realized target pair is no longer locally novel")

        line = ",".join(str(value) for value in candidate.list())
        if len(line.split(",")) != 25:
            raise ValueError("candidate coefficient serialization has the wrong degree")
        digest = sha256_bytes(line.encode("ascii"))
        ledger_hash = bool(
            connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
            ).fetchone()
        )
        other_outbox = digest in outbox_hashes()
        receipt_hash = digest in receipt_hashes
        if ledger_hash or other_outbox or receipt_hash:
            raise ValueError("realized candidate coefficient hash is already known")

        score = Fraction(1, 2 ** int(target_state["teamCount"]))
        manifest_payload = (line + "\n").encode("ascii")
        write_new(MANIFEST, manifest_payload)
        certificate = {
            "schemaVersion": "post100-f5-15578-r4-exact-v1",
            "status": "certified_exact_safe_staged_not_submitted",
            "coefficientMaterialIncluded": False,
            "method": "unique-literal-block-system-unique-size12-quotient-pair-product",
            "source": {**SOURCE, "ledgerStatus": "accepted_scoreable_nonbaseline"},
            "inputs": {
                "actionMap": {"path": display(ACTION_MAP), "sha256": sha256_path(ACTION_MAP)},
                "actionShards": [
                    {"path": display(path), "sha256": sha256_path(path)}
                    for path in ACTION_SHARDS
                ],
                "sourceCertificate": {
                    "path": display(SOURCE_CERTIFICATE),
                    "sha256": sha256_path(SOURCE_CERTIFICATE),
                },
                "sourceReceipt": {
                    "path": display(SOURCE_RECEIPT),
                    "sha256": sha256_path(SOURCE_RECEIPT),
                },
            },
            "actionCertificate": {
                "rowSha256": canonical_json_sha256(action),
                "sourceLabel": SOURCE["label"],
                "targetLabel": TARGET_LABEL,
                "possibleTargetR": sorted(EXPECTED_POSSIBLE_R),
                "uniqueLiteralBlockSystems": 1,
                "uniqueSize12PairProductOrbits": 1,
            },
            "factorCertificate": {
                "factorDegrees": factor_degrees,
                "pairResolventSha256": sha256_bytes(
                    ",".join(str(value) for value in pair_resolvent.list()).encode("ascii")
                ),
                "squarefree": True,
                "uniqueDegree12Factors": 1,
            },
            "target": {
                "coefficientSha256": digest,
                "label": TARGET_LABEL,
                "projectedMarginalScoreExact": str(score),
                "r": signature,
                "teamCount": int(target_state["teamCount"]),
            },
            "exclusions": {
                "baselinePair": target_state["baseline"],
                "knownLedgerHash": ledger_hash,
                "locallyKnownPair": target_state["locallyKnown"],
                "locallyOwnedPair": target_state["locallyOwned"],
                "otherOutboxHash": other_outbox,
                "receiptHash": receipt_hash,
                "receiptPair": target_state["receiptPair"],
            },
            "receiptExclusionSnapshot": {
                key: value for key, value in receipt_audit.items() if key != "audit"
            },
            "manifest": {
                "bytes": len(manifest_payload),
                "path": display(MANIFEST),
                "polynomials": 1,
                "sha256": sha256_bytes(manifest_payload),
            },
            "sideEffects": {
                "heavyWorkersLaunched": 1,
                "ledgerWrites": 0,
                "networkCalls": 0,
                "submissionCalls": 0,
            },
        }
        certificate_payload = (
            json.dumps(certificate, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        write_new(CERTIFICATE, certificate_payload)
        summary = {
            "schemaVersion": "post100-f5-15578-r4-summary-v1",
            "status": certificate["status"],
            "coefficientMaterialIncluded": False,
            "exactSafeRows": 1,
            "projectedMarginalScoreExact": str(score),
            "target": {"label": TARGET_LABEL, "r": signature},
            "certificate": {"path": display(CERTIFICATE), "sha256": sha256_path(CERTIFICATE)},
            "manifest": certificate["manifest"],
            "submissionCalls": 0,
        }
        write_new(
            SUMMARY, (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())

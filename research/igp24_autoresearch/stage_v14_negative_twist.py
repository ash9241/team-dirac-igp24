#!/usr/bin/env python3
"""Stage one exact post-v14 negative quadratic twist without Sage/GAP.

The source is an accepted even polynomial P(x)=Q(x^2).  An exact cached
two-block action certificate says that its unique generic quadratic-twist
action is the same transitive label and that the global block flip already
lies in the source group.  Exact rational Sturm arithmetic determines the
negative-twist signature.  A prime at which P is squarefree makes the new
quadratic field ramified outside the source splitting field, proving linear
disjointness and hence the certified generic action.

The script is offline, refuses excluded hashes/pairs, and never submits.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from fractions import Fraction
from pathlib import Path

import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
INVENTORY = DATA / "autopilot_pair_delta_20260722_v14/exact_source_inventory.json"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
MANIFEST = ROOT / "outbox/v14_negative_twist_15578_r4.txt"
CERTIFICATE = DATA / "v14_negative_twist_15578_r4_certificate.json"
SUMMARY = DATA / "v14_negative_twist_15578_r4_summary.json"
SOURCE_PAIR = ("24T15578", 12)
TARGET_PAIR = ("24T15578", 4)
PRIME_START = 10009


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


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


def trim(values: list) -> list:
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return values


def rational_remainder(first: list[Fraction], second: list[Fraction]) -> list[Fraction]:
    dividend = trim(list(first))
    divisor = trim(list(second))
    while len(dividend) >= len(divisor) and any(dividend):
        shift = len(dividend) - len(divisor)
        factor = dividend[-1] / divisor[-1]
        for index, value in enumerate(divisor):
            dividend[index + shift] -= factor * value
        trim(dividend)
    return dividend


def exact_real_root_count(coefficients: list[int]) -> tuple[int, int]:
    """Return the Sturm root count and sequence length over Q."""
    polynomial = trim([Fraction(value) for value in coefficients])
    derivative = trim(
        [Fraction(index) * polynomial[index] for index in range(1, len(polynomial))]
    )
    if len(polynomial) <= 1 or not any(derivative):
        raise ValueError("Sturm input must be a nonconstant squarefree polynomial")
    sequence = [polynomial, derivative]
    while len(sequence[-1]) > 1:
        remainder = rational_remainder(sequence[-2], sequence[-1])
        if not any(remainder):
            raise ValueError("Sturm input is not squarefree")
        sequence.append(trim([-value for value in remainder]))

    def variations(positive_infinity: bool) -> int:
        signs = []
        for row in sequence:
            sign = 1 if row[-1] > 0 else -1
            if not positive_infinity and (len(row) - 1) % 2:
                sign = -sign
            signs.append(sign)
        return sum(left != right for left, right in zip(signs, signs[1:]))

    return variations(False) - variations(True), len(sequence)


def is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def modular_trim(values: list[int], prime: int) -> list[int]:
    values = [value % prime for value in values]
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return values


def modular_remainder(first: list[int], second: list[int], prime: int) -> list[int]:
    dividend = modular_trim(first, prime)
    divisor = modular_trim(second, prime)
    inverse = pow(divisor[-1], -1, prime)
    while len(dividend) >= len(divisor) and not (
        len(dividend) == 1 and dividend[0] == 0
    ):
        shift = len(dividend) - len(divisor)
        factor = dividend[-1] * inverse % prime
        for index, value in enumerate(divisor):
            dividend[index + shift] = (
                dividend[index + shift] - factor * value
            ) % prime
        dividend = modular_trim(dividend, prime)
    return dividend


def squarefree_mod_prime(coefficients: list[int], prime: int) -> bool:
    first = modular_trim(coefficients, prime)
    second = modular_trim(
        [index * coefficients[index] for index in range(1, len(coefficients))],
        prime,
    )
    while not (len(second) == 1 and second[0] == 0):
        first, second = second, modular_remainder(first, second, prime)
    return len(first) == 1


def outbox_hashes() -> set[str]:
    result = set()
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = single.canonical_polynomial_line(raw.strip())
            if line is not None:
                result.add(hashlib.sha256(line.encode("ascii")).hexdigest())
    return result


def return_existing_seal() -> int | None:
    present = [path.exists() for path in (MANIFEST, CERTIFICATE, SUMMARY)]
    if not any(present):
        return None
    if not all(present):
        raise ValueError("negative-twist outputs are only partially present")
    certificate = json.loads(CERTIFICATE.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    manifest = certificate.get("manifest") or {}
    if (
        certificate.get("coefficientMaterialIncluded") is not False
        or summary.get("coefficientMaterialIncluded") is not False
        or str(manifest.get("sha256")) != sha256_path(MANIFEST)
        or str((summary.get("certificate") or {}).get("sha256")) != sha256_path(CERTIFICATE)
    ):
        raise ValueError("existing negative-twist seal is inconsistent")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main() -> int:
    if (existing := return_existing_seal()) is not None:
        return existing
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    anchors = (inventory.get("acceptedAnchorsByPair") or {}).get(
        f"{SOURCE_PAIR[0]}:{SOURCE_PAIR[1]}"
    ) or []
    if len(anchors) != 1:
        raise ValueError("v14 source inventory does not have one exact source anchor")
    anchor = anchors[0]

    action_matches = []
    for line in ACTION_MAP.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if str(row.get("sourceLabel")) == SOURCE_PAIR[0]:
            action_matches.append(row)
    if len(action_matches) != 1:
        raise ValueError("cached twist action is missing or nonunique")
    action = action_matches[0]
    systems = action.get("systems") or []
    if (
        int(action.get("systemCount", -1)) != 1
        or list(action.get("targetLabels") or []) != [TARGET_PAIR[0]]
        or len(systems) != 1
        or not bool(systems[0].get("flipInSource"))
        or str(systems[0].get("targetLabel")) != TARGET_PAIR[0]
        or int(systems[0].get("targetT", -1)) != int(TARGET_PAIR[0][3:])
    ):
        raise ValueError("cached action does not prove a unique same-label generic twist")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        source = connection.execute(
            "SELECT p.coefficients,p.coefficient_hash,v.status,v.label,v.r,"
            "v.scoreable,v.in_baseline,v.scoring_status FROM polynomials p "
            "JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? AND p.polynomial_index=?",
            (str(anchor["submissionId"]), int(anchor["polynomialIndex"])),
        ).fetchone()
        if source is None or (
            str(source["coefficient_hash"]) != str(anchor["coefficientSha256"])
            or str(source["status"]) != "accepted"
            or str(source["label"]) != SOURCE_PAIR[0]
            or int(source["r"]) != SOURCE_PAIR[1]
            or int(source["scoreable"] or 0) != 1
            or int(source["in_baseline"] or 0) != 0
            or str(source["scoring_status"]) != "scoreable"
        ):
            raise ValueError("source anchor is not accepted-scoreable and exact")
        values = [int(value) for value in str(source["coefficients"]).split(",")]
        if (
            len(values) != 25
            or values[-1] != 1
            or values[0] == 0
            or math.gcd(*values) != 1
            or any(values[index] != 0 for index in range(1, 25, 2))
        ):
            raise ValueError("source is not a primitive monic even degree-24 polynomial")

        quotient_roots, sturm_length = exact_real_root_count(values[::2])
        negative_roots = 2 * quotient_roots - SOURCE_PAIR[1]
        if negative_roots != TARGET_PAIR[1]:
            raise ValueError(f"negative twist has unexpected signature r={negative_roots}")

        ledger_hashes = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        other_outbox_hashes = outbox_hashes()
        prime = PRIME_START
        while True:
            while not is_prime(prime):
                prime += 1
            if not squarefree_mod_prime(values, prime):
                prime += 1
                continue
            twist_d = -prime
            twisted = [0] * 25
            for index in range(13):
                twisted[2 * index] = values[2 * index] * twist_d ** (12 - index)
            line = ",".join(str(value) for value in twisted)
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            if digest not in ledger_hashes and digest not in other_outbox_hashes:
                break
            prime += 1

        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        locally_known = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE label IS NOT NULL AND r IS NOT NULL"
            )
        }
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, {digest: {TARGET_PAIR}}
        )
        target = connection.execute(
            "SELECT discovered,team_count,generated_at FROM targets "
            "WHERE label=? AND r=?",
            TARGET_PAIR,
        ).fetchone()
        if target is None:
            raise ValueError("twist target is absent from the target catalogue")
        exclusions = {
            "baselinePair": TARGET_PAIR in baseline,
            "knownLedgerHash": digest in ledger_hashes,
            "locallyKnownPair": TARGET_PAIR in locally_known,
            "locallyOwnedPair": TARGET_PAIR in owned,
            "otherOutboxHash": digest in other_outbox_hashes,
            "receiptHash": digest in receipt_hashes,
            "receiptPair": TARGET_PAIR in receipt_pairs,
        }
        if any(exclusions.values()):
            raise ValueError(f"negative twist is excluded: {exclusions}")
    finally:
        connection.close()

    manifest_payload = (line + "\n").encode("ascii")
    write_new(MANIFEST, manifest_payload)
    score = Fraction(1, 2 ** int(target["team_count"]))
    action_payload = json.dumps(action, separators=(",", ":"), sort_keys=True).encode()
    certificate = {
        "schemaVersion": "v14-exact-negative-quadratic-twist-v1",
        "status": "certified_exact_safe_staged_not_submitted",
        "coefficientMaterialIncluded": False,
        "source": {
            "coefficientSha256": str(source["coefficient_hash"]),
            "label": SOURCE_PAIR[0],
            "polynomialIndex": int(anchor["polynomialIndex"]),
            "r": SOURCE_PAIR[1],
            "submissionId": str(anchor["submissionId"]),
        },
        "actionCertificate": {
            "artifact": display(ACTION_MAP),
            "artifactSha256": sha256_path(ACTION_MAP),
            "rowSha256": hashlib.sha256(action_payload).hexdigest(),
            "flipInSource": True,
            "systemCount": 1,
            "uniqueTargetLabel": TARGET_PAIR[0],
        },
        "signatureCertificate": {
            "method": "exact-rational-Sturm-sequence",
            "quotientDegree": 12,
            "quotientRealRootCount": quotient_roots,
            "sourceRealRootCount": SOURCE_PAIR[1],
            "sturmSequenceLength": sturm_length,
            "twistRealRootFormula": "2*quotientRealRootCount-sourceRealRootCount",
            "twistRealRootCount": negative_roots,
        },
        "genericTwistCertificate": {
            "ramificationPrime": prime,
            "sourceSquarefreeModuloRamificationPrime": True,
            "twistD": twist_d,
            "proof": (
                "The source is squarefree modulo the selected prime, so its "
                "splitting field is unramified there. The negative quadratic "
                "twist field is ramified there and therefore linearly disjoint. "
                "The cached unique action has its block flip in the source "
                "group, so the generic twist retains the exact source label."
            ),
        },
        "target": {
            "coefficientSha256": digest,
            "discovered": bool(target["discovered"]),
            "label": TARGET_PAIR[0],
            "projectedMarginalScoreExact": str(score),
            "r": TARGET_PAIR[1],
            "teamCount": int(target["team_count"]),
        },
        "exclusions": exclusions,
        "receiptExclusionSnapshot": {
            key: value for key, value in receipt_audit.items() if key != "audit"
        },
        "manifest": {
            "bytes": len(manifest_payload),
            "path": display(MANIFEST),
            "polynomials": 1,
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
        },
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "ledgerWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }
    write_new(CERTIFICATE, (json.dumps(certificate, indent=2, sort_keys=True) + "\n").encode())
    summary = {
        "schemaVersion": "v14-exact-negative-quadratic-twist-summary-v1",
        "status": certificate["status"],
        "coefficientMaterialIncluded": False,
        "exactSafeRows": 1,
        "projectedMarginalScoreExact": str(score),
        "target": {"label": TARGET_PAIR[0], "r": TARGET_PAIR[1]},
        "certificate": {
            "path": display(CERTIFICATE),
            "sha256": sha256_path(CERTIFICATE),
        },
        "manifest": certificate["manifest"],
        "submissionCalls": 0,
    }
    write_new(SUMMARY, (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

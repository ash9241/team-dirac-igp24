#!/usr/bin/env sage -python
"""Fail-closed single-source k=3 Kummer transform for 24T5027/r0.

The selected verified source is an even polynomial ``q(x^2)``.  The exact
degree-12 quotient action has one size-12 orbit of triples, and source
signature zero maps only to target signature zero.  Therefore the unique
degree-12 factor of ``q.symmetric_power(3)`` is canonically attached to the
single allowlisted live pair.  This program never calls the network or the
submission API and never writes the ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
RESULTS = DATA / "agent_f9_k3_safe_5027_r0_results.jsonl"
SUMMARY = DATA / "agent_f9_k3_safe_5027_r0_summary.json"
MANIFEST = OUTBOX / "agent_f9_k3_safe_5027_r0_live.txt"

SOURCE_SUBMISSION_ID = "sub_b97d316041334d9fa21535acbae1482d"
SOURCE_POLYNOMIAL_INDEX = 348
SOURCE_LABEL = "24T5027"
SOURCE_T = 5027
SOURCE_R = 0
SOURCE_COEFFICIENT_SHA256 = (
    "ec74b3fcff1c1aa84af4df3b10ef352cebe69f6b647c52b0a1e82000c0846c91"
)
SOURCE_QUOTIENT_SHA256 = (
    "ad86cb87aff1c10e64a7b851c73b1afc6ed765d9325d8bae9e060b05f665c512"
)
EXPECTED_ACTIONS_SHA256 = (
    "d191f5d5ed251f2e7af6ede4b930da24a75ca6593b62f7f1e6e01f061abc4d00"
)
TARGET_LABEL = "24T244"
TARGET_T = 244
TARGET_R = 0
QUOTIENT_T12 = 41
TARGET_REFRESH_FLOOR = datetime(2026, 7, 21, 23, 40, tzinfo=timezone.utc)
TARGET_MAX_AGE_SECONDS = 6 * 60 * 60


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_integer_line(values) -> str:
    return ",".join(str(ZZ(value)) for value in values)


def canonical_polynomial_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return canonical_integer_line(values)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def output_collision_candidates() -> tuple[Path, ...]:
    outputs = (RESULTS, SUMMARY, MANIFEST)
    return outputs + tuple(Path(str(path) + ".tmp") for path in outputs)


def validate_output_isolation() -> None:
    outputs = (RESULTS.resolve(), SUMMARY.resolve(), MANIFEST.resolve())
    if len(set(outputs)) != 3:
        raise ValueError("result, summary, and manifest paths collide")
    if RESULTS.parent.resolve() != DATA.resolve() or SUMMARY.parent.resolve() != DATA.resolve():
        raise ValueError("data output escaped the data directory")
    if MANIFEST.parent.resolve() != OUTBOX.resolve():
        raise ValueError("manifest output escaped the outbox directory")
    existing = [
        str(path.relative_to(ROOT))
        for path in output_collision_candidates()
        if path.exists()
    ]
    if existing:
        raise FileExistsError(
            "single-source execution will not overwrite existing outputs: "
            + ", ".join(existing)
        )


def publish_bytes_no_overwrite(path: Path, payload: bytes) -> str:
    """Atomically publish complete bytes while refusing every overwrite."""
    temporary = Path(str(path) + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except Exception:
        raise
    else:
        temporary.unlink()
    return sha256_bytes(payload)


def publish_json(path: Path, payload: dict) -> str:
    rendered = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    return publish_bytes_no_overwrite(path, rendered)


def publish_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    ).encode()
    return publish_bytes_no_overwrite(path, rendered)


def target_state(connection: sqlite3.Connection) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (TARGET_LABEL, TARGET_R),
    ).fetchone()
    if row is None:
        raise ValueError("allowlisted target row is missing")
    state = {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }
    if state["generatedAt"] is None:
        raise ValueError("target snapshot has no generation timestamp")
    generated_at = datetime.fromisoformat(state["generatedAt"].replace("Z", "+00:00"))
    if generated_at.tzinfo is None:
        raise ValueError("target snapshot timestamp is not timezone-aware")
    generated_at = generated_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    age_seconds = (now - generated_at).total_seconds()
    if generated_at < TARGET_REFRESH_FLOOR:
        raise ValueError("target snapshot predates the sealed refresh floor")
    if age_seconds < -300:
        raise ValueError("target snapshot timestamp is unexpectedly in the future")
    if age_seconds > TARGET_MAX_AGE_SECONDS:
        raise ValueError("target snapshot is stale; refresh targets before launch")
    state["ageSeconds"] = max(0, int(age_seconds))
    state["fresh"] = True
    return state


def require_live_target(state: dict) -> None:
    if state["baseline"]:
        raise ValueError("allowlisted target is a sealed baseline exclusion")
    if (
        state["teamCount"] != 0
        or state["discovered"]
        or state["minimumDiscAbs"] is not None
        or state["owned"]
        or not state["fresh"]
    ):
        raise ValueError("allowlisted target is no longer a fresh unowned tc0 pair")


def validate_action() -> tuple[dict, dict]:
    observed_sha = sha256_path(ACTIONS)
    if observed_sha != EXPECTED_ACTIONS_SHA256:
        raise ValueError("action-map hash mismatch")
    actions = load_jsonl(ACTIONS)
    source_actions = [row for row in actions if str(row["sourceLabel"]) == SOURCE_LABEL]
    triple_actions = [row for row in source_actions if int(row["subsetSize"]) == 3]
    if len(triple_actions) != 1:
        raise ValueError("source no longer has exactly one triple action")
    action = triple_actions[0]
    expected_signatures = {"0": [0], "8": [0, 8, 24], "16": [0, 24], "24": [24]}
    if (
        int(action["sourceT"]) != SOURCE_T
        or int(action["quotientT12"]) != QUOTIENT_T12
        or str(action["targetLabel"]) != TARGET_LABEL
        or int(action["targetT"]) != TARGET_T
        or int(action["sourceBlockKernelOrder"]) != 32
        or int(action["sourceKummerRank"]) != 5
        or int(action["targetKernelOrder"]) != 2
        or int(action["targetKummerRank"]) != 1
        or int(action["targetOrder"]) != 144
        or int(action["incidenceMatrixRank"]) != 8
        or action["sourceSignatureToPossibleTargetSignatures"] != expected_signatures
    ):
        raise ValueError("sealed triple action metadata changed")
    orbit = action["subsetOrbit"]
    blocks = action["sourceBlockSystem"]
    if len(orbit) != 12 or any(len(subset) != 3 for subset in orbit):
        raise ValueError("triple action is not a size-12 orbit of triples")
    if len({tuple(sorted(int(value) for value in subset)) for subset in orbit}) != 12:
        raise ValueError("triple orbit contains duplicate subsets")
    if len(blocks) != 12 or any(len(block) != 2 for block in blocks):
        raise ValueError("source block system is not twelve pairs")
    if any(row["sourceBlockSystem"] != blocks for row in source_actions):
        raise ValueError("source actions disagree on their exact block system")
    metadata = {
        "actionMapSha256": observed_sha,
        "compatibleTargetSignatures": list(expected_signatures[str(SOURCE_R)]),
        "quotientT12": QUOTIENT_T12,
        "sizeTwelveTripleActions": len(triple_actions),
        "targetLabel": TARGET_LABEL,
    }
    return action, metadata


def validate_source(connection: sqlite3.Connection) -> tuple[list[ZZ], str, dict]:
    row = connection.execute(
        """
        SELECT s.created_at,s.updated_at,s.queued_count,s.verified_count,s.failed_count,
          p.original_line,p.coefficients,p.coefficient_hash,
          v.label,v.t,v.r,v.status,v.scoreable,v.scoring_status
        FROM submissions s
        JOIN polynomials p USING(submission_id)
        JOIN verifications v USING(submission_id,polynomial_index)
        WHERE s.submission_id=? AND p.polynomial_index=?
        """,
        (SOURCE_SUBMISSION_ID, SOURCE_POLYNOMIAL_INDEX),
    ).fetchone()
    if row is None:
        raise ValueError("sealed source receipt is absent from the ledger")
    coefficients = [ZZ(value.strip()) for value in str(row["coefficients"]).split(",")]
    original = [ZZ(value.strip()) for value in str(row["original_line"]).split(",")]
    coefficient_line = canonical_integer_line(coefficients)
    observed_hash = sha256_bytes(coefficient_line.encode())
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or original != coefficients
        or observed_hash != SOURCE_COEFFICIENT_SHA256
        or str(row["coefficient_hash"]) != SOURCE_COEFFICIENT_SHA256
        or str(row["label"]) != SOURCE_LABEL
        or int(row["t"]) != SOURCE_T
        or int(row["r"]) != SOURCE_R
        or str(row["status"]) != "accepted"
        or int(row["scoreable"]) != 1
        or str(row["scoring_status"]) != "scoreable"
        or int(row["queued_count"]) != 0
        or int(row["verified_count"]) != 1000
        or int(row["failed_count"]) != 0
        or any(coefficients[index] != 0 for index in range(1, 25, 2))
    ):
        raise ValueError("sealed source receipt or polynomial provenance changed")
    quotient_line = canonical_integer_line(coefficients[::2])
    if sha256_bytes(quotient_line.encode()) != SOURCE_QUOTIENT_SHA256:
        raise ValueError("source quotient hash mismatch")
    receipt = {
        "createdAt": str(row["created_at"]),
        "failedCount": int(row["failed_count"]),
        "queuedCount": int(row["queued_count"]),
        "submissionId": SOURCE_SUBMISSION_ID,
        "updatedAt": str(row["updated_at"]),
        "verifiedCount": int(row["verified_count"]),
    }
    return coefficients, quotient_line, receipt


def known_candidate_hashes(connection: sqlite3.Connection) -> set[str]:
    hashes = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    for path in OUTBOX.glob("*.txt"):
        if path.resolve() == MANIFEST.resolve():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            try:
                values = [ZZ(value.strip()) for value in line.split(",")]
            except Exception:
                continue
            if len(values) == 25 and values[-1] == 1:
                hashes.add(sha256_bytes(canonical_integer_line(values).encode()))
    return hashes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the sealed 24T5027/r0 unique-triple transform."
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate every guard without constructing or factoring a resolvent.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_output_isolation()
    action, action_metadata = validate_action()

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source_coefficients, quotient_line, receipt = validate_source(connection)
    initial_target_state = target_state(connection)
    require_live_target(initial_target_state)

    preflight = {
        "action": action_metadata,
        "event": "k3_safe_preflight_ok",
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "outputs": {
            "manifest": str(MANIFEST.resolve()),
            "results": str(RESULTS.resolve()),
            "summary": str(SUMMARY.resolve()),
        },
        "source": {
            "coefficientSha256": SOURCE_COEFFICIENT_SHA256,
            "label": SOURCE_LABEL,
            "polynomialIndex": SOURCE_POLYNOMIAL_INDEX,
            "quotientPolynomialSha256": SOURCE_QUOTIENT_SHA256,
            "r": SOURCE_R,
            "receipt": receipt,
        },
        "submissionCalls": 0,
        "target": {
            "label": TARGET_LABEL,
            "r": TARGET_R,
            "state": initial_target_state,
        },
    }
    if args.preflight_only:
        connection.close()
        print(json.dumps(preflight, sort_keys=True), flush=True)
        return 0

    ring_y = PolynomialRing(ZZ, "y")
    quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
    if source_coefficients[::2] != quotient.list():
        raise ValueError("source polynomial no longer equals q(x^2)")
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("source quotient is not monic irreducible degree 12")

    triple_resolvent = quotient.symmetric_power(3, monic=True)
    factors = [(factor, int(exponent)) for factor, exponent in triple_resolvent.factor()]
    degree_twelve = [
        factor for factor, exponent in factors if int(factor.degree()) == 12 and exponent == 1
    ]
    if len(degree_twelve) != 1:
        raise ValueError("triple resolvent does not have exactly one simple degree-12 factor")
    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    triple_factor = ring_x(degree_twelve[0])
    candidate = triple_factor(x**2)
    if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
        raise ValueError("triple-product candidate is not monic irreducible degree 24")
    signature = int(candidate.number_of_real_roots())
    possible_signatures = {
        int(value)
        for value in action["sourceSignatureToPossibleTargetSignatures"][str(SOURCE_R)]
    }
    if possible_signatures != {TARGET_R} or signature != TARGET_R:
        raise ValueError("candidate signature contradicts the deterministic action frontier")

    coefficient_line = canonical_polynomial_line(candidate)
    coefficient_sha = sha256_bytes(coefficient_line.encode())
    field_disc = abs(ZZ(NumberField(candidate, "a").absolute_discriminant()))
    polynomial_disc = abs(ZZ(candidate.discriminant()))

    final_target_state = target_state(connection)
    require_live_target(final_target_state)
    fresh_hash = coefficient_sha not in known_candidate_hashes(connection)
    if not fresh_hash:
        raise ValueError("exact candidate hash already exists in the ledger or outbox")

    result = {
        "candidate": {
            "coefficientLine": coefficient_line,
            "coefficientSha256": coefficient_sha,
            "fieldDiscriminantAbs": str(field_disc),
            "freshHash": True,
            "irreducible": True,
            "polynomialDiscriminantAbs": str(polynomial_disc),
            "r": signature,
        },
        "exactTargetCertificate": {
            "action": action,
            "factorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent}
                for factor, exponent in factors
            ],
            "proof": (
                "The verified even source has one exact size-12 orbit of triples. "
                "The third symmetric power has one simple irreducible degree-12 "
                "factor, which is therefore canonically the displayed orbit; "
                "substitution x^2 gives the exact induced 24-point action."
            ),
            "targetLabel": TARGET_LABEL,
            "tripleFactorSha256": sha256_bytes(
                canonical_integer_line(triple_factor.list()).encode()
            ),
            "tripleResolventDegree": int(triple_resolvent.degree()),
            "tripleResolventSha256": sha256_bytes(
                canonical_integer_line(triple_resolvent.list()).encode()
            ),
            "uniqueDegree12Factor": True,
        },
        "initialTargetState": initial_target_state,
        "liveHit": True,
        "networkCalls": 0,
        "postArithmeticTargetState": final_target_state,
        "source": {
            "coefficientSha256": SOURCE_COEFFICIENT_SHA256,
            "label": SOURCE_LABEL,
            "polynomialIndex": SOURCE_POLYNOMIAL_INDEX,
            "quotientPolynomialSha256": SOURCE_QUOTIENT_SHA256,
            "r": SOURCE_R,
            "receipt": receipt,
        },
        "status": "exact_live_hit",
        "submissionCalls": 0,
        "target": {"label": TARGET_LABEL, "r": TARGET_R},
    }
    manifest_sha = publish_bytes_no_overwrite(MANIFEST, (coefficient_line + "\n").encode())
    results_sha = publish_jsonl(RESULTS, [result])
    summary = {
        "artifactSha256": {
            "manifest": manifest_sha,
            "results": results_sha,
        },
        "candidateCoefficientHashes": [coefficient_sha],
        "exactCandidates": 1,
        "exactLiveHits": 1,
        "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS_K3_SAFE_SINGLE_SOURCE",
        "inputSha256": {str(ACTIONS.relative_to(ROOT)): EXPECTED_ACTIONS_SHA256},
        "manifest": str(MANIFEST.resolve()),
        "networkCalls": 0,
        "results": str(RESULTS.resolve()),
        "stagedPairs": [f"{TARGET_LABEL}/r{TARGET_R}"],
        "stagedPolynomials": 1,
        "status": "exact_hit_staged",
        "submissionCalls": 0,
    }
    summary_sha = publish_json(SUMMARY, summary)
    connection.close()
    print(
        json.dumps(
            {
                "candidateSha256": coefficient_sha,
                "event": "k3_safe_complete",
                "liveHit": True,
                "networkCalls": 0,
                "stagedPolynomials": 1,
                "submissionCalls": 0,
                "summarySha256": summary_sha,
                "target": f"{TARGET_LABEL}/r{TARGET_R}",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

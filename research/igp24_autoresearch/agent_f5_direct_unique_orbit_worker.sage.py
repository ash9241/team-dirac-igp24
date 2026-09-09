#!/usr/bin/env sage -python
"""Resolve one pinned exact F5 unique-pair-orbit route without staging it."""

from __future__ import annotations

import argparse
import fcntl
import glob
import hashlib
import json
import os
import runpy
import sqlite3
import tempfile
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
LEDGER = DATA / "ledger.sqlite3"
RAW_ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
REFERENCE_WORKER = ROOT / "agent_f5_full_ledger_safe_unique_orbit_pilot.sage.py"
LOCK = DATA / ".low_contention_sequential.lock"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    return sha256_bytes(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def polynomial_hashes_in_outbox() -> set[str]:
    result = set()
    for path in OUTBOX.glob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        result.update(
            sha256_bytes(line.strip().encode("utf-8"))
            for line in lines
            if line.strip()
        )
    return result


def receipt_manifest_hashes() -> set[str]:
    result = set()
    for path in RECEIPTS.glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"])).expanduser().resolve()
            if (
                manifest.is_file()
                and sha256_path(manifest) == str(receipt["manifestHash"])
            ):
                result.update(
                    sha256_bytes(line.strip().encode("utf-8"))
                    for line in manifest.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def action_core(row: dict) -> dict:
    return {
        key: row[key]
        for key in (
            "pairOrbit",
            "sourceBlockSystem",
            "sourceLabel",
            "sourceSignatureToPossibleTargetSignatures",
            "sourceT",
            "targetLabel",
            "targetOrder",
            "targetT",
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", type=int, required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--source-r", type=int, required=True)
    parser.add_argument("--source-coefficient-sha256", required=True)
    parser.add_argument("--canonical-quotient-sha256", required=True)
    parser.add_argument("--action-file", type=Path, required=True)
    parser.add_argument("--action-file-sha256", required=True)
    parser.add_argument("--action-record", type=int, required=True)
    parser.add_argument("--action-row-sha256", required=True)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--target-r", type=int, required=True)
    args = parser.parse_args()

    if not args.run_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("run id must be alphanumeric with optional _ or -")
    output = DATA / f"agent_f5_direct_unique_{args.run_id}_result.json"
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")

    lock_handle = LOCK.open("a+")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("another exact arithmetic worker holds the global lock") from exc

    action_path = args.action_file.resolve()
    if (
        not action_path.is_file()
        or sha256_path(action_path) != args.action_file_sha256
        or args.action_record <= 0
    ):
        raise ValueError("pinned action artifact changed")
    action_lines = action_path.read_text(encoding="utf-8").splitlines()
    if args.action_record > len(action_lines):
        raise ValueError("action record is out of range")
    action_line = action_lines[args.action_record - 1]
    if sha256_bytes(action_line.encode("utf-8")) != args.action_row_sha256:
        raise ValueError("pinned action row changed")
    cached_action = json.loads(action_line)
    if (
        str(cached_action["sourceLabel"]) != args.source_label
        or str(cached_action["targetLabel"]) != args.target_label
    ):
        raise ValueError("pinned action labels disagree with the route")

    action_occurrences = 0
    for path_value in glob.glob(
        str(DATA / "agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
    ):
        for line in Path(path_value).read_text(encoding="utf-8").splitlines():
            if json.loads(line)["sourceLabel"] == args.source_label:
                action_occurrences += 1
    if action_occurrences != 1:
        raise ValueError("source label does not have exactly one cached pair action")

    raw_rows = [
        json.loads(line)
        for line in RAW_ACTION_MAP.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    raw = [row for row in raw_rows if row["sourceLabel"] == args.source_label]
    if len(raw) != 1 or len(raw[0]["systems"]) != 1:
        raise ValueError("source label does not have one literal two-block system")

    reference = runpy.run_path(str(REFERENCE_WORKER))
    rebuilt_action = reference["exact_unique_action"](
        int(raw[0]["sourceT"]), raw[0]["systems"][0]
    )
    if action_core(rebuilt_action) != action_core(cached_action):
        raise ValueError("independently rebuilt exact action differs from cached action")
    possible_target_r = rebuilt_action[
        "sourceSignatureToPossibleTargetSignatures"
    ].get(str(args.source_r), [])
    if possible_target_r != [args.target_r]:
        raise ValueError("route is not deterministic for the pinned source signature")

    connection = sqlite3.connect(f"file:{LEDGER.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.status,v.label,v.r,v.scoreable,
               v.field_disc_abs
        FROM polynomials AS p JOIN verifications AS v
          USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (args.submission_id, args.polynomial_index),
    ).fetchone()
    if (
        source is None
        or str(source["status"]) != "accepted"
        or int(source["scoreable"]) != 1
        or str(source["label"]) != args.source_label
        or int(source["r"]) != args.source_r
        or str(source["coefficient_hash"]) != args.source_coefficient_sha256
    ):
        raise ValueError("accepted ledger source pin changed")

    source_values = [ZZ(value) for value in str(source["coefficients"]).split(",")]
    if (
        len(source_values) != 25
        or source_values[-1] != 1
        or any(source_values[index] for index in range(1, 25, 2))
    ):
        raise ValueError("pinned source is not a monic even degree-24 polynomial")
    quotient_line = ",".join(str(value) for value in source_values[::2])
    canonical_line, canonical_sha256 = reference["canonical_quotient_pair"](
        quotient_line
    )
    if canonical_sha256 != args.canonical_quotient_sha256:
        raise ValueError("canonical quotient pin changed")

    ring_y = PolynomialRing(ZZ, "y")
    quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("source quotient is not monic irreducible degree 12")
    pair_resolvent = quotient.symmetric_power(2, monic=True)
    factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
    degree_twelve = [
        factor
        for factor, exponent in factors
        if factor.degree() == 12 and exponent == 1
    ]
    if len(degree_twelve) != 1:
        raise ValueError("pair resolvent does not have one simple degree-12 factor")

    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    pair_factor = ring_x(degree_twelve[0])
    candidate = pair_factor(x**2)
    if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
        raise ValueError("resolved candidate is not monic irreducible degree 24")
    candidate_r = int(candidate.number_of_real_roots())
    if candidate_r != args.target_r:
        raise ValueError("resolved candidate has the wrong real signature")
    coefficient_line = ",".join(str(value) for value in candidate.list())
    coefficient_sha256 = sha256_bytes(coefficient_line.encode("utf-8"))

    target = connection.execute(
        """
        SELECT t.*,
          EXISTS(
            SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r
          ) AS baseline,
          EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
          ) AS owned_scoreable,
          EXISTS(
            SELECT 1 FROM verifications v
            WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
          ) AS owned_accepted
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (args.target_label, args.target_r),
    ).fetchone()
    if (
        target is None
        or int(target["baseline"])
        or int(target["owned_scoreable"])
        or int(target["owned_accepted"])
    ):
        raise ValueError("resolved target is no longer locally novel")
    ledger_hash_occurrences = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
            (coefficient_sha256,),
        ).fetchone()[0]
    )
    connection.close()

    outbox_hashes = polynomial_hashes_in_outbox()
    receipted_hashes = receipt_manifest_hashes()
    if (
        ledger_hash_occurrences
        or coefficient_sha256 in outbox_hashes
        or coefficient_sha256 in receipted_hashes
    ):
        raise ValueError("resolved candidate is already known, staged, or receipted")

    factor_rows = []
    for factor, exponent in factors:
        factor_line = ",".join(str(value) for value in factor.list())
        factor_rows.append(
            {
                "coefficientSha256": sha256_bytes(factor_line.encode("utf-8")),
                "degree": int(factor.degree()),
                "exponent": exponent,
            }
        )
    pair_factor_line = ",".join(str(value) for value in pair_factor.list())
    field_discriminant_abs = abs(ZZ(NumberField(candidate, "a").absolute_discriminant()))
    route_identity = {
        "actionRowSha256": args.action_row_sha256,
        "sourceCoefficientSha256": args.source_coefficient_sha256,
        "targetLabel": args.target_label,
        "targetR": args.target_r,
    }
    result = {
        "action": {
            "artifact": str(action_path.relative_to(ROOT)),
            "artifactSha256": args.action_file_sha256,
            "exactRebuiltCoreSha256": canonical_json_sha256(action_core(rebuilt_action)),
            "record": args.action_record,
            "rowSha256": args.action_row_sha256,
        },
        "candidate": {
            "coefficientLine": coefficient_line,
            "coefficientSha256": coefficient_sha256,
            "fieldDiscriminantAbs": str(field_discriminant_abs),
            "irreducible": True,
            "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
            "r": candidate_r,
        },
        "factorCertificate": {
            "factors": factor_rows,
            "pairResolventSha256": sha256_bytes(
                ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
            ),
            "uniqueDegree12FactorCoefficientLine": pair_factor_line,
            "uniqueDegree12FactorSha256": sha256_bytes(
                pair_factor_line.encode("utf-8")
            ),
        },
        "inputArtifacts": {
            "rawActionMap": str(RAW_ACTION_MAP.relative_to(ROOT)),
            "rawActionMapSha256": sha256_path(RAW_ACTION_MAP),
            "referenceWorker": str(REFERENCE_WORKER.relative_to(ROOT)),
            "referenceWorkerSha256": sha256_path(REFERENCE_WORKER),
        },
        "localLiveRecheck": {
            "baseline": bool(target["baseline"]),
            "discovered": bool(target["discovered"]),
            "generatedAt": str(target["generated_at"]),
            "ownedAccepted": bool(target["owned_accepted"]),
            "ownedScoreable": bool(target["owned_scoreable"]),
            "teamCount": int(target["team_count"]),
        },
        "networkCalls": 0,
        "novelty": {
            "ledgerHashOccurrences": ledger_hash_occurrences,
            "presentInOutbox": coefficient_sha256 in outbox_hashes,
            "presentInReceiptedManifest": coefficient_sha256 in receipted_hashes,
        },
        "routeIdentity": route_identity,
        "routeIdentitySha256": canonical_json_sha256(route_identity),
        "source": {
            "canonicalQuotientLine": canonical_line,
            "canonicalQuotientSha256": canonical_sha256,
            "coefficientSha256": args.source_coefficient_sha256,
            "fieldDiscAbs": str(source["field_disc_abs"]),
            "label": args.source_label,
            "polynomialIndex": args.polynomial_index,
            "r": args.source_r,
            "submissionId": args.submission_id,
        },
        "status": "certified_exact_unstaged",
        "submissionCalls": 0,
        "target": {
            "label": args.target_label,
            "r": args.target_r,
            "t": int(args.target_label[3:]),
        },
    }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_write(output, text)
    print(
        json.dumps(
            {
                "candidateSha256": coefficient_sha256,
                "output": str(output),
                "outputSha256": sha256_bytes(text.encode("utf-8")),
                "target": result["target"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

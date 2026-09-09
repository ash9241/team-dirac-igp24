#!/usr/bin/env sage -python
"""Extract exact pair siblings from a committed-but-unverified staged source.

Unlike ``pair_sum_one.sage.py``, this worker never treats a queued source as a
ledger verification.  It reconstructs the source coefficient line from a
committed receipt and requires an unbroken hash/index/label/signature chain
through the original outbox, unique-pair result, and stage certificate.  The
pair-resolvent arithmetic is imported from ``pair_sum_one.sage.py``.

The worker is deliberately read-only apart from one explicit atomic output.
It performs no network calls and does not modify the ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari

from derived_stage_provenance import validate_derived_stage_source


ROOT = Path(__file__).resolve().parent
PAIR_WORKER = ROOT / "pair_sum_one.sage.py"
F5_WORKER = ROOT / "agent_f5_full_ledger_safe_unique_orbit_pilot.sage.py"
STAGED_SOURCE_VALIDATOR = ROOT / "stage_staged_source_frobenius_gold.py"
DERIVED_STAGE_VALIDATOR = ROOT / "derived_stage_provenance.py"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain one JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def write_json_atomic(path: Path, value: dict) -> None:
    destination = resolved(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid degree-24 label: {label!r}")
    return int(match.group(1))


def coefficient_list(line: str) -> list[int]:
    try:
        coefficients = [int(value) for value in line.split(",")]
    except ValueError as exc:
        raise ValueError("source coefficient line contains a noninteger") from exc
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("source is not monic of degree 24")
    if coefficients[0] == 0 or math.gcd(*coefficients) != 1:
        raise ValueError("source is not primitive with nonzero constant term")
    return coefficients


def import_pair_worker():
    spec = importlib.util.spec_from_file_location("pair_sum_one_arithmetic", PAIR_WORKER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import arithmetic from {PAIR_WORKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_f5_worker():
    spec = importlib.util.spec_from_file_location(
        "f5_unique_pair_source_arithmetic", F5_WORKER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import arithmetic from {F5_WORKER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_staged_source_validator():
    spec = importlib.util.spec_from_file_location(
        "staged_source_certificate_validator", STAGED_SOURCE_VALIDATOR
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import staged-source validator from {STAGED_SOURCE_VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_json_sha256(value: dict) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return sha256_bytes(payload.encode("utf-8"))


def validate_manifest_and_stage(
    manifest_path: Path,
    stage_path: Path,
    polynomial_index: int,
    expected_label: str,
    expected_r: int,
    expected_hash: str,
) -> tuple[str, dict, dict]:
    manifest_path = resolved(manifest_path)
    stage = read_json(stage_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest_hash = sha256_bytes(manifest_bytes)
    stage_manifest = stage.get("manifest")
    if not isinstance(stage_manifest, dict):
        raise ValueError("safe stage certificate has no manifest object")
    if resolved(Path(str(stage_manifest.get("path")))) != manifest_path:
        raise ValueError("safe stage certificate points at a different manifest")
    if str(stage_manifest.get("sha256")) != manifest_hash:
        raise ValueError("safe stage certificate manifest hash mismatch")
    if stage.get("status") != "staged_exact":
        raise ValueError("safe stage certificate is not staged_exact")
    if (
        int(stage.get("networkCalls", -1)) != 0
        or int(stage.get("submissionCalls", -1)) != 0
        or ("ledgerWrites" in stage and int(stage["ledgerWrites"]) != 0)
    ):
        raise ValueError("safe stage certificate records external calls")

    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    candidates = stage.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != len(lines):
        raise ValueError("safe stage candidate/manifest cardinality mismatch")
    if int(stage.get("batchSize", -1)) != len(lines):
        raise ValueError("safe stage batch size mismatch")
    for index, (line, candidate) in enumerate(zip(lines, candidates)):
        if not isinstance(candidate, dict):
            raise ValueError(f"safe stage candidate {index} is not an object")
        digest = sha256_bytes(line.encode("utf-8"))
        if str(candidate.get("coefficientLine")) != line:
            raise ValueError(f"safe stage candidate {index} line mismatch")
        if str(candidate.get("coefficientSha256")) != digest:
            raise ValueError(f"safe stage candidate {index} hash mismatch")
    if polynomial_index < 0 or polynomial_index >= len(lines):
        raise ValueError("source polynomial index is outside the committed manifest")
    line = lines[polynomial_index]
    candidate = candidates[polynomial_index]
    target = candidate.get("target")
    if not isinstance(target, dict):
        raise ValueError("source stage candidate has no target certificate")
    if (
        str(target.get("label")) != expected_label
        or int(target.get("r", -1)) != expected_r
        or int(target.get("t", -1)) != label_t(expected_label)
    ):
        raise ValueError("source stage target label/signature mismatch")
    if str(candidate.get("coefficientSha256")) != expected_hash:
        raise ValueError("source stage coefficient hash differs from the expected hash")
    factor = candidate.get("factorCertificate")
    if not isinstance(factor, dict):
        raise ValueError("source stage factor certificate is absent")
    if not factor.get("resolventSquarefree"):
        raise ValueError("source stage resolvent was not certified squarefree")
    if sorted(map(int, factor.get("actualDegrees", []))) != sorted(
        map(int, factor.get("expectedDegrees", []))
    ):
        raise ValueError("source stage factor degrees are not certified")
    if any(int(value) != 1 for value in factor.get("exponents", [])):
        raise ValueError("source stage factor certificate has a nontrivial exponent")
    return line, candidate, stage


def validate_receipt(
    receipt_path: Path,
    manifest_path: Path,
    manifest_hash: str,
    submission_id: str,
    polynomial_index: int,
) -> dict:
    receipt = read_json(receipt_path)
    if receipt.get("commit") is not True:
        raise ValueError("receipt is not a committed submission receipt")
    if resolved(Path(str(receipt.get("manifest")))) != resolved(manifest_path):
        raise ValueError("receipt points at a different manifest")
    if str(receipt.get("manifestHash")) != manifest_hash:
        raise ValueError("receipt manifest hash mismatch")
    response = receipt.get("response")
    if not isinstance(response, dict):
        raise ValueError("receipt has no submission response")
    if str(response.get("submissionId")) != submission_id:
        raise ValueError("receipt submission id mismatch")
    if str(response.get("submissionStatus")) != "queued":
        raise ValueError("receipt does not record a queued submission")
    payload = response.get("payload")
    queued = payload.get("queuedPolynomials") if isinstance(payload, dict) else None
    if not isinstance(queued, list):
        raise ValueError("receipt has no queued-polynomial index list")
    matching = [
        row
        for row in queued
        if isinstance(row, dict)
        and int(row.get("polynomialIndex", -1)) == polynomial_index
        and str(row.get("status")) == "queued"
    ]
    if len(matching) != 1:
        raise ValueError("receipt does not uniquely certify this queued source index")
    if int(response.get("rejectedCount", -1)) != 0:
        raise ValueError("receipt records rejected polynomials")
    return receipt


def validate_safe_result(
    results_path: Path,
    line: str,
    expected_label: str,
    expected_r: int,
    expected_hash: str,
) -> dict:
    matches = []
    for row in read_jsonl(results_path):
        candidate = row.get("candidate")
        if not isinstance(candidate, dict):
            continue
        if (
            str(row.get("candidateSha256")) == expected_hash
            and str(candidate.get("coefficientSha256")) == expected_hash
            and str(candidate.get("coefficientLine")) == line
            and str(candidate.get("targetLabel")) == expected_label
            and int(candidate.get("targetR", -1)) == expected_r
        ):
            matches.append(row)
    if not matches:
        raise ValueError("exact result does not certify this staged source")

    # One exact output polynomial can legitimately be reached from more than one
    # source action.  The retained v1 safe and ambiguous result banks contain a
    # few such duplicate derivations.  Validate every matching proof envelope,
    # then select one deterministically; never accept multiplicity by itself as
    # evidence that the target polynomial is ambiguous.
    candidate_invariants = set()
    for row in matches:
        candidate = row["candidate"]
        if (
            row.get("status") != "exact_frozen_gold_hit"
            or row.get("exactFrozenGoldHit") is not True
            or row.get("routeMode") not in {"safe", "ambiguous"}
            or candidate.get("status") != "certified"
        ):
            raise ValueError("result is not an exact certified frozen-gold hit")
        if int(row.get("realizedTargetR", -1)) != expected_r:
            raise ValueError("exact result realized signature mismatch")
        orbit_certificate = candidate.get("orbitCertificate")
        if not isinstance(orbit_certificate, dict):
            raise ValueError("exact result has no orbit factor certificate")
        if sorted(map(int, orbit_certificate.get("actualDegrees", []))) != sorted(
            map(int, orbit_certificate.get("expectedDegrees", []))
        ):
            raise ValueError("exact result factor-degree certificate mismatch")
        if any(int(value) != 1 for value in orbit_certificate.get("exponents", [])):
            raise ValueError("exact result factorization has nontrivial exponents")
        field_disc = str(candidate.get("fieldDiscriminantAbs"))
        if not field_disc.isdigit() or int(field_disc) <= 0:
            raise ValueError("exact result has no positive field discriminant")
        candidate_invariants.add(
            (
                str(candidate.get("coefficientSha256")),
                str(candidate.get("coefficientLine")),
                str(candidate.get("targetLabel")),
                int(candidate.get("targetR", -1)),
                field_disc,
                canonical_json_sha256(orbit_certificate),
            )
        )
    if len(candidate_invariants) != 1:
        raise ValueError("matching exact result derivations disagree")
    return min(matches, key=canonical_json_sha256)


def validate_f5_source_result(
    results_path: Path,
    plan_path: Path,
    line: str,
    expected_label: str,
    expected_r: int,
    expected_hash: str,
) -> tuple[dict, dict]:
    """Validate the retained exact F5 unique-pair construction envelope.

    The expensive algebraic reconstruction is repeated later in ``main``.
    This first pass freezes one unique row and ties it to the original plan.
    """

    matches = []
    for row in read_jsonl(results_path):
        candidate = row.get("candidate")
        target = row.get("target")
        if not isinstance(candidate, dict) or not isinstance(target, dict):
            continue
        if (
            str(candidate.get("coefficientSha256")) == expected_hash
            and str(candidate.get("coefficientLine")) == line
            and str(target.get("label")) == expected_label
            and int(target.get("r", -1)) == expected_r
        ):
            matches.append(row)
    if len(matches) != 1:
        raise ValueError("F5 results do not uniquely certify this staged source")
    row = matches[0]
    candidate = row["candidate"]
    target = row["target"]
    action = row.get("exactAction")
    source = row.get("source")
    if row.get("status") not in {
        "resolved_not_frozen_live_signature",
        "resolved_known_coefficient",
        "resolved_pair_claimed",
        "hit_staged",
    }:
        raise ValueError("F5 source result does not record an exact resolved candidate")
    if candidate.get("irreducible") is not True or int(candidate.get("r", -1)) != expected_r:
        raise ValueError("F5 source candidate exact checks are incomplete")
    if int(target.get("t", -1)) != label_t(expected_label):
        raise ValueError("F5 source target label/T mismatch")
    if not isinstance(action, dict) or not isinstance(source, dict):
        raise ValueError("F5 source result lacks action or upstream source provenance")
    if (
        str(action.get("targetLabel")) != expected_label
        or int(action.get("targetT", -1)) != label_t(expected_label)
        or str(action.get("sourceLabel")) != str(source.get("label"))
        or int(action.get("sourceT", -1)) != int(source.get("t", -1))
    ):
        raise ValueError("F5 exact action is inconsistent with source and target")
    factor_degrees = row.get("factorDegrees")
    if not isinstance(factor_degrees, list) or not factor_degrees:
        raise ValueError("F5 source result has no factor-degree certificate")
    if any(int(value.get("exponent", -1)) != 1 for value in factor_degrees):
        raise ValueError("F5 source pair resolvent is not squarefree")
    if sum(int(value.get("degree", -1)) == 12 for value in factor_degrees) != 1:
        raise ValueError("F5 source result lacks one unique degree-12 factor")
    if HEX64_RE.fullmatch(str(row.get("pairResolventSha256"))) is None:
        raise ValueError("F5 source result has no pair-resolvent digest")

    plan = read_json(plan_path)
    if (
        int(plan.get("networkCalls", -1)) != 0
        or int(plan.get("submissionCalls", -1)) != 0
        or ("ledgerWrites" in plan and int(plan["ledgerWrites"]) != 0)
    ):
        raise ValueError("F5 source plan records external calls")
    exact_actions = plan.get("exactActions")
    if not isinstance(exact_actions, dict) or exact_actions.get(str(source["label"])) != action:
        raise ValueError("F5 source plan action differs from the retained exact result")
    selected = plan.get("selected")
    if not isinstance(selected, list):
        raise ValueError("F5 source plan has no selected-source list")
    selected_matches = [
        value
        for value in selected
        if isinstance(value, dict)
        and isinstance(value.get("source"), dict)
        and str(value["source"].get("submissionId")) == str(source.get("submissionId"))
        and int(value["source"].get("polynomialIndex", -1))
        == int(source.get("polynomialIndex", -2))
        and str(value["source"].get("coefficientSha256"))
        == str(source.get("coefficientSha256"))
        and str(value["source"].get("label")) == str(source.get("label"))
        and int(value["source"].get("r", -1)) == int(source.get("r", -2))
    ]
    if len(selected_matches) != 1:
        raise ValueError("F5 source plan does not uniquely nominate the upstream source")
    return row, plan


def validate_orbit_row(
    orbit_map_path: Path, expected_label: str, expected_r: int
) -> dict:
    rows = [
        row
        for row in read_jsonl(orbit_map_path)
        if str(row.get("sourceLabel")) == expected_label
    ]
    if len(rows) != 1:
        raise ValueError("v3 orbit map does not contain one exact source row")
    row = rows[0]
    if row.get("status") != "certified":
        raise ValueError("v3 orbit row is not certified")
    if int(row.get("sourceT", -1)) != label_t(expected_label):
        raise ValueError("v3 orbit row source T mismatch")
    if expected_r not in {int(value) for value in row.get("sourceR", [])}:
        raise ValueError("v3 orbit row has no compatible source signature")
    certificate_hash = str(row.get("exactCertificateSha256"))
    if HEX64_RE.fullmatch(certificate_hash) is None:
        raise ValueError("v3 orbit row has no exact-certificate digest")
    targets = row.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("v3 orbit row has no degree-24 targets")
    if int(row.get("length24OrbitCount", -1)) != len(targets):
        raise ValueError("v3 orbit row length-24 count mismatch")
    orbit_indexes = set()
    for target in targets:
        if int(target.get("orbitSize", -1)) != 24:
            raise ValueError("v3 orbit target is not degree 24")
        if int(target.get("kernelOrder", -1)) != 1:
            raise ValueError("v3 orbit target action is not faithful")
        if int(target.get("targetT", -1)) != label_t(str(target.get("targetLabel"))):
            raise ValueError("v3 orbit target label/T mismatch")
        orbit_index = int(target.get("orbitIndex", -1))
        if orbit_index in orbit_indexes:
            raise ValueError("v3 orbit target indexes are not unique")
        orbit_indexes.add(orbit_index)
    orbit_sizes = [int(value) for value in row.get("orbitSizes", [])]
    if sum(orbit_sizes) != 24 * 23 // 2:
        raise ValueError("v3 orbit-size census does not sum to 276")
    if orbit_sizes.count(24) != len(targets):
        raise ValueError("v3 degree-24 orbit-size count mismatch")
    matching_profiles = [
        profile
        for profile in row.get("profiles", [])
        if int(profile.get("sourceR", -1)) == expected_r
    ]
    if not matching_profiles:
        raise ValueError("v3 orbit row has no exact source-signature profile")
    return row


def reconstruct_f5_source_exactly(
    row: dict,
    source_line: str,
    database: Path,
) -> dict:
    """Rebuild the queued source from its verified F5 parent and exact action."""

    upstream = row["source"]
    connection = sqlite3.connect(
        f"file:{resolved(database)}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        ledger_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.field_disc_abs,
                   v.label,v.r,v.scoreable
            FROM polynomials AS p JOIN verifications AS v
              USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (
                str(upstream["submissionId"]),
                int(upstream["polynomialIndex"]),
            ),
        ).fetchone()
    finally:
        connection.close()
    if ledger_row is None or int(ledger_row["scoreable"] or 0) != 1:
        raise ValueError("F5 upstream source is absent or not scoreable in the ledger")
    if (
        str(ledger_row["coefficient_hash"])
        != str(upstream.get("coefficientSha256"))
        or str(ledger_row["label"]) != str(upstream.get("label"))
        or int(ledger_row["r"]) != int(upstream.get("r", -1))
        or str(ledger_row["field_disc_abs"]) != str(upstream.get("fieldDiscAbs"))
    ):
        raise ValueError("F5 upstream ledger provenance mismatch")

    source_values = [ZZ(value) for value in str(ledger_row["coefficients"]).split(",")]
    if len(source_values) != 25 or any(source_values[index] for index in range(1, 25, 2)):
        raise ValueError("F5 upstream source is not the certified even polynomial")
    quotient_line = str(upstream.get("quotientLine"))
    if sha256_bytes(quotient_line.encode("utf-8")) != str(
        upstream.get("quotientPolynomialSha256")
    ):
        raise ValueError("F5 upstream quotient digest mismatch")
    quotient_ring = PolynomialRing(ZZ, "y_f5_source")
    quotient = quotient_ring([ZZ(value) for value in quotient_line.split(",")])
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("F5 upstream quotient is not monic irreducible degree 12")
    if source_values[::2] != quotient.list():
        raise ValueError("F5 upstream quotient does not reconstruct its ledger source")

    exact_action = row["exactAction"]
    f5_worker = import_f5_worker()
    rebuilt_action = f5_worker.exact_unique_action(
        int(upstream["t"]), {"blocks": exact_action["sourceBlockSystem"]}
    )
    if rebuilt_action != exact_action:
        raise ValueError("F5 unique pair action does not exactly reproduce")

    pair_resolvent = quotient.symmetric_power(2, monic=True)
    resolvent_hash = sha256_bytes(
        ",".join(str(int(value)) for value in pair_resolvent.list()).encode("utf-8")
    )
    if resolvent_hash != str(row["pairResolventSha256"]):
        raise ValueError("F5 source pair-resolvent digest does not reproduce")
    factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
    actual_degrees = sorted(
        (int(factor.degree()), exponent) for factor, exponent in factors
    )
    expected_degrees = sorted(
        (int(value["degree"]), int(value["exponent"]))
        for value in row["factorDegrees"]
    )
    if actual_degrees != expected_degrees or any(exponent != 1 for _, exponent in factors):
        raise ValueError("F5 source factor-degree certificate does not reproduce")
    degree_twelve = [
        factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1
    ]
    if len(degree_twelve) != 1:
        raise ValueError("F5 source reconstruction lacks one degree-12 factor")
    target_ring = PolynomialRing(ZZ, "z_f5_source")
    z = target_ring.gen()
    rebuilt_source = target_ring(degree_twelve[0])(z**2)
    if (
        rebuilt_source.degree() != 24
        or not rebuilt_source.is_monic()
        or not rebuilt_source.is_irreducible()
    ):
        raise ValueError("F5 reconstructed queued source fails exact algebraic checks")
    rebuilt_line = ",".join(str(int(value)) for value in rebuilt_source.list())
    if rebuilt_line != source_line:
        raise ValueError("F5 construction does not reproduce the committed source line")
    if int(rebuilt_source.number_of_real_roots()) != int(row["target"]["r"]):
        raise ValueError("F5 reconstructed source real-root signature mismatch")
    return {
        "upstreamSubmissionId": str(upstream["submissionId"]),
        "upstreamPolynomialIndex": int(upstream["polynomialIndex"]),
        "upstreamCoefficientSha256": str(upstream["coefficientSha256"]),
        "upstreamLabel": str(upstream["label"]),
        "upstreamR": int(upstream["r"]),
        "upstreamFieldDiscriminantAbs": str(upstream["fieldDiscAbs"]),
        "pairResolventSha256": resolvent_hash,
        "factorDegrees": [
            {"degree": degree, "exponent": exponent}
            for degree, exponent in actual_degrees
        ],
        "uniqueDegree12Factor": True,
        "exactAction": rebuilt_action,
        "matchingResultRowSha256": canonical_json_sha256(row),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission_id")
    parser.add_argument("polynomial_index", type=int)
    parser.add_argument("--expected-label", required=True)
    parser.add_argument("--expected-r", type=int, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--safe-results", type=Path)
    parser.add_argument("--safe-stage-certificate", type=Path)
    parser.add_argument("--f5-results", type=Path)
    parser.add_argument("--f5-plan", type=Path)
    parser.add_argument(
        "--derived-stage-certificate",
        type=Path,
        help="strict stage certificate for a queued source produced by this pipeline",
    )
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--orbit-map", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transforms", default="1,2,3,5,7")
    parser.add_argument("--reduce", choices=("none", "best", "abs"), default="best")
    args = parser.parse_args()

    expected_label = str(args.expected_label)
    expected_r = int(args.expected_r)
    label_t(expected_label)
    if expected_r < 0 or expected_r > 24 or expected_r % 2:
        parser.error("--expected-r must be even and between 0 and 24")
    expected_hash = str(args.expected_source_sha256)
    if HEX64_RE.fullmatch(expected_hash) is None:
        parser.error("--expected-source-sha256 must be a lowercase SHA-256 digest")
    safe_mode = args.safe_results is not None or args.safe_stage_certificate is not None
    f5_mode = args.f5_results is not None or args.f5_plan is not None
    derived_mode = args.derived_stage_certificate is not None
    if sum((safe_mode, f5_mode, derived_mode)) != 1:
        parser.error(
            "provide exactly one complete source proof family: either "
            "--safe-results with --safe-stage-certificate, or "
            "--f5-results with --f5-plan, or --derived-stage-certificate"
        )
    if safe_mode and (args.safe_results is None or args.safe_stage_certificate is None):
        parser.error("--safe-results and --safe-stage-certificate must be paired")
    if f5_mode and (args.f5_results is None or args.f5_plan is None):
        parser.error("--f5-results and --f5-plan must be paired")
    protected = {
        resolved(args.manifest),
        resolved(args.receipt),
        resolved(args.orbit_map),
        resolved(args.db),
        PAIR_WORKER,
        F5_WORKER,
        STAGED_SOURCE_VALIDATOR,
        DERIVED_STAGE_VALIDATOR,
    }
    for optional_path in (
        args.safe_results,
        args.safe_stage_certificate,
        args.f5_results,
        args.f5_plan,
        args.derived_stage_certificate,
    ):
        if optional_path is not None:
            protected.add(resolved(optional_path))
    if resolved(args.output) in protected:
        parser.error("--output must not overwrite a provenance input")

    staged = None
    stage = None
    safe_result = None
    f5_result = None
    f5_plan = None
    derived_proof = None
    if safe_mode:
        line, staged, stage = validate_manifest_and_stage(
            args.manifest,
            args.safe_stage_certificate,
            args.polynomial_index,
            expected_label,
            expected_r,
            expected_hash,
        )
        safe_result = validate_safe_result(
            args.safe_results, line, expected_label, expected_r, expected_hash
        )
    elif f5_mode:
        lines = resolved(args.manifest).read_text(encoding="utf-8").splitlines()
        if args.polynomial_index < 0 or args.polynomial_index >= len(lines):
            raise ValueError("source polynomial index is outside the committed manifest")
        line = lines[args.polynomial_index]
        coefficient_list(line)
        if sha256_bytes(line.encode("utf-8")) != expected_hash:
            raise ValueError("committed F5 source hash/index mismatch")
        f5_result, f5_plan = validate_f5_source_result(
            args.f5_results,
            args.f5_plan,
            line,
            expected_label,
            expected_r,
            expected_hash,
        )
    else:
        validator = import_staged_source_validator()
        derived_proof = validate_derived_stage_source(
            args.manifest,
            args.derived_stage_certificate,
            args.polynomial_index,
            expected_label,
            expected_r,
            expected_hash,
            validator.validate_source_certificate,
        )
        line = str(derived_proof["line"])
        staged = derived_proof["candidate"]
        stage = derived_proof["stage"]
    manifest_hash = sha256_path(args.manifest)
    receipt = validate_receipt(
        args.receipt,
        args.manifest,
        manifest_hash,
        args.submission_id,
        args.polynomial_index,
    )
    orbit_row = validate_orbit_row(args.orbit_map, expected_label, expected_r)

    coefficients = coefficient_list(line)
    ring = PolynomialRing(ZZ, "x")
    source_polynomial = ring(coefficients)
    if source_polynomial.degree() != 24 or not source_polynomial.is_irreducible():
        raise ValueError("staged source fails exact degree/irreducibility checks")
    realized_r = int(source_polynomial.number_of_real_roots())
    if realized_r != expected_r:
        raise ValueError(
            f"staged source has {realized_r} real roots, expected {expected_r}"
        )
    source_nfdisc_started = time.monotonic()
    source_nfdisc = str(abs(int(pari(source_polynomial).nfdisc())))
    source_nfdisc_seconds = round(time.monotonic() - source_nfdisc_started, 3)
    f5_reconstruction = None
    if safe_mode:
        staged_nfdisc = str(staged.get("fieldDiscriminantAbs"))
        result_nfdisc = str(safe_result["candidate"].get("fieldDiscriminantAbs"))
        if source_nfdisc != staged_nfdisc or source_nfdisc != result_nfdisc:
            raise ValueError("staged source field-discriminant certificate mismatch")
    elif f5_mode:
        f5_reconstruction = reconstruct_f5_source_exactly(
            f5_result, line, args.db
        )
    else:
        if source_nfdisc != str(derived_proof["sourceFieldDiscriminantAbs"]):
            raise ValueError("derived staged source field-discriminant mismatch")

    arithmetic = import_pair_worker()
    pair_degree = 24 * 23 // 2
    started = time.monotonic()
    root_powers = arithmetic.root_power_sums(coefficients, 2 * pair_degree)
    log(f"computed staged-source Newton sums in {time.monotonic() - started:.2f}s")

    selected_factors = None
    factor_certificate = None
    selected_transform = None
    attempts = []
    transforms = [int(value) for value in args.transforms.split(",") if value.strip()]
    if not transforms:
        parser.error("--transforms must contain at least one integer")
    for transform in transforms:
        attempt_started = time.monotonic()
        transformed = arithmetic.transformed_power_sums(
            root_powers, pair_degree, transform
        )
        resolvent = arithmetic.pair_sum_resolvent(ring, transformed, pair_degree)
        resolvent_hash = sha256_bytes(
            ",".join(str(int(value)) for value in resolvent.list()).encode("utf-8")
        )
        log(
            f"c={transform}: built degree-{resolvent.degree()} staged resolvent "
            f"in {time.monotonic() - attempt_started:.2f}s"
        )
        factor_started = time.monotonic()
        selected_factors, factor_certificate = arithmetic.factor_with_certificate(
            resolvent,
            [int(value) for value in orbit_row["orbitSizes"]],
            len(orbit_row["targets"]),
        )
        attempts.append(
            {
                "transform": transform,
                "resolventSha256": resolvent_hash,
                "factorSeconds": round(time.monotonic() - factor_started, 3),
                "certificate": factor_certificate,
            }
        )
        log(
            f"c={transform}: factor/certificate took "
            f"{attempts[-1]['factorSeconds']:.2f}s; "
            f"accepted={selected_factors is not None}"
        )
        if selected_factors is not None:
            selected_transform = transform
            break
    if selected_factors is None:
        result = {
            "status": "no_separating_transform",
            "sourceSubmissionId": args.submission_id,
            "sourcePolynomialIndex": args.polynomial_index,
            "sourceLabel": expected_label,
            "sourceR": expected_r,
            "attempts": attempts,
        }
        write_json_atomic(args.output, result)
        return 2

    candidate_rows = []
    for factor_index, selected in enumerate(selected_factors):
        candidate = arithmetic.reduce_polynomial(selected, args.reduce)
        if (
            candidate.degree() != 24
            or not candidate.is_monic()
            or not candidate.is_irreducible()
        ):
            raise ValueError("derived factor fails degree/monicity/irreducibility")
        candidate_line = arithmetic.coefficient_line(candidate)
        nfdisc_started = time.monotonic()
        field_disc = str(abs(int(pari(candidate).nfdisc())))
        candidate_rows.append(
            {
                "factorIndex": factor_index,
                "targetR": int(candidate.number_of_real_roots()),
                "coefficientLine": candidate_line,
                "coefficientBytes": len(candidate_line.encode("utf-8")),
                "coefficientSha256": sha256_bytes(candidate_line.encode("utf-8")),
                "polynomialDiscriminantAbs": str(abs(int(candidate.discriminant()))),
                "fieldDiscriminantAbs": field_disc,
                "nfdiscSeconds": round(time.monotonic() - nfdisc_started, 3),
                "degree": 24,
                "monic": True,
                "irreducible": True,
            }
        )

    response = receipt["response"]
    source_checks = {
        "degree": 24,
        "monic": True,
        "primitive": True,
        "irreducible": True,
        "realRoots": realized_r,
        "fieldDiscriminantAbs": source_nfdisc,
        "nfdiscSeconds": source_nfdisc_seconds,
    }
    source_certificate = {
        "manifest": {
            "path": str(resolved(args.manifest)),
            "sha256": manifest_hash,
            "polynomialIndex": args.polynomial_index,
            "coefficientSha256": expected_hash,
        },
        "committedReceipt": {
            "path": str(resolved(args.receipt)),
            "sha256": sha256_path(args.receipt),
            "submissionId": args.submission_id,
            "submissionStatus": response.get("submissionStatus"),
            "createdAt": response.get("createdAt"),
        },
        "sourceChecks": source_checks,
    }
    if safe_mode:
        source_certificate.update(
            {
                "kind": "committed-queued-exact-stage-provenance-v1",
                "safeResults": {
                    "path": str(resolved(args.safe_results)),
                    "sha256": sha256_path(args.safe_results),
                },
                "safeStageCertificate": {
                    "path": str(resolved(args.safe_stage_certificate)),
                    "sha256": sha256_path(args.safe_stage_certificate),
                    "certificateVersion": stage.get("certificateVersion"),
                },
            }
        )
    elif f5_mode:
        source_certificate.update(
            {
                "kind": "committed-queued-f5-unique-pair-provenance-v1",
                "f5Results": {
                    "path": str(resolved(args.f5_results)),
                    "sha256": sha256_path(args.f5_results),
                    "matchingRowSha256": f5_reconstruction[
                        "matchingResultRowSha256"
                    ],
                },
                "f5Plan": {
                    "path": str(resolved(args.f5_plan)),
                    "sha256": sha256_path(args.f5_plan),
                },
                "f5ExactConstruction": f5_reconstruction,
            }
        )
    else:
        source_certificate.update(
            {
                "kind": "committed-queued-derived-stage-provenance-v1",
                "derivedStageCertificate": {
                    "path": str(derived_proof["stagePath"]),
                    "sha256": str(derived_proof["stageSha256"]),
                    "certificateVersion": stage.get("certificateVersion"),
                },
                "derivedCandidates": {
                    "path": str(derived_proof["candidatesPath"]),
                    "sha256": str(derived_proof["candidatesSha256"]),
                },
                "derivedFrobenius": {
                    "path": str(derived_proof["frobeniusPath"]),
                    "sha256": str(derived_proof["frobeniusSha256"]),
                },
                "derivedOrbitMap": {
                    "path": str(derived_proof["orbitMapPath"]),
                    "sha256": str(derived_proof["orbitMapSha256"]),
                    "exactCertificateSha256": str(
                        derived_proof["pairCensusCertificateSha256"]
                    ),
                },
            }
        )
    result = {
        "status": "certified_multi",
        "sourceSubmissionId": args.submission_id,
        "sourcePolynomialIndex": args.polynomial_index,
        "sourceLabel": expected_label,
        "sourceR": expected_r,
        "sourceCoefficientSha256": expected_hash,
        "sourceFieldDiscriminantAbs": source_nfdisc,
        "sourceCertificate": source_certificate,
        "orbitMap": {
            "path": str(resolved(args.orbit_map)),
            "sha256": sha256_path(args.orbit_map),
            "exactCertificateSha256": str(orbit_row["exactCertificateSha256"]),
        },
        "orbitCertificate": factor_certificate,
        "orbitTargets": orbit_row["targets"],
        "transform": {"kind": "x+c*x^2", "c": selected_transform},
        "reduction": args.reduce,
        "attempts": attempts,
        "candidates": candidate_rows,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "networkCalls": 0,
        "ledgerWrites": 0,
        "submissionCalls": 0,
    }
    write_json_atomic(args.output, result)
    print(
        json.dumps(
            {
                "output": str(resolved(args.output)),
                "status": result["status"],
                "source": f"{expected_label}/r{expected_r}",
                "factors": len(candidate_rows),
                "outputSha256": sha256_path(args.output),
                "candidateSha256": [row["coefficientSha256"] for row in candidate_rows],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env sage -python
"""Resolve one pinned exact F5 multi-orbit packet without staging it."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
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
REFERENCE_WORKER = ROOT / "run_f5_multiorbit_plan.sage.py"
COMMON_MODULE = ROOT / "f5_multiorbit_common.py"
REGISTRY_BUILDER = ROOT / "build_f5_current_exclusion_registry.py"
LOCK = DATA / ".low_contention_sequential.lock"
PAIR_PIN = re.compile(r"^(24T\d+)/r(\d+)$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def normalized_polynomial_hash(line: str) -> str | None:
    """Hash only the normalized coefficient payload, ignoring safe annotations."""
    payload = line.split("#", 1)[0].strip()
    if not payload:
        return None
    try:
        values = [int(value.strip()) for value in payload.split(",")]
    except ValueError:
        return None
    if len(values) != 25 or values[-1] != 1:
        return None
    normalized = ",".join(str(value) for value in values)
    return sha256_bytes(normalized.encode("utf-8"))


def staged_hashes() -> tuple[set[str], set[str]]:
    outbox = set()
    for path in OUTBOX.glob("*.txt"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                digest = normalized_polynomial_hash(line)
                if digest is not None:
                    outbox.add(digest)
        except (OSError, UnicodeDecodeError):
            pass
    receipted = set()
    for path in RECEIPTS.glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            manifest = Path(str(receipt["manifest"])).expanduser().resolve()
            if manifest.is_file() and sha256_path(manifest) == str(
                receipt["manifestHash"]
            ):
                for line in manifest.read_text(encoding="utf-8").splitlines():
                    digest = normalized_polynomial_hash(line)
                    if digest is not None:
                        receipted.add(digest)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return outbox, receipted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--polynomial-index", type=int, required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--source-r", type=int, required=True)
    parser.add_argument("--source-coefficient-sha256", required=True)
    parser.add_argument("--canonical-quotient-sha256", required=True)
    parser.add_argument("--raw-action-map-sha256", required=True)
    parser.add_argument("--reference-worker-sha256", required=True)
    parser.add_argument("--common-module-sha256", required=True)
    parser.add_argument("--registry-builder-sha256", required=True)
    parser.add_argument("--exclusion-registry", type=Path, required=True)
    parser.add_argument("--exclusion-registry-sha256", required=True)
    parser.add_argument("--action-file", type=Path, required=True)
    parser.add_argument("--action-file-sha256", required=True)
    parser.add_argument("--action-record", type=int, action="append", required=True)
    parser.add_argument("--action-row-sha256", action="append", required=True)
    parser.add_argument(
        "--allowed-target-pair",
        action="append",
        default=[],
        help="Enable generalized tc0 mode with a pinned LABEL/rR pair",
    )
    args = parser.parse_args()

    if not args.run_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("malformed run id")
    if len(args.action_record) < 2 or len(args.action_record) != len(
        args.action_row_sha256
    ):
        raise ValueError("multi-orbit action pins are malformed")
    allowed_pairs = set()
    for value in args.allowed_target_pair:
        match = PAIR_PIN.fullmatch(value)
        if match is None:
            raise ValueError("malformed allowed target pair")
        pair = (match.group(1), int(match.group(2)))
        if pair[1] < 0 or pair[1] > 24 or pair[1] % 2:
            raise ValueError("allowed target signature is impossible")
        allowed_pairs.add(pair)
    if len(allowed_pairs) != len(args.allowed_target_pair):
        raise ValueError("allowed target pair pins repeat")
    generalized_tc0 = bool(allowed_pairs)
    if (
        not HEX_SHA256.fullmatch(args.raw_action_map_sha256)
        or not HEX_SHA256.fullmatch(args.reference_worker_sha256)
        or not HEX_SHA256.fullmatch(args.common_module_sha256)
        or not HEX_SHA256.fullmatch(args.registry_builder_sha256)
        or not HEX_SHA256.fullmatch(args.exclusion_registry_sha256)
    ):
        raise ValueError("critical artifact SHA pin is malformed")
    output = DATA / f"agent_f5_direct_multiorbit_{args.run_id}_result.json"
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")

    lock_handle = LOCK.open("a+")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("another exact arithmetic worker holds the global lock") from exc

    if (
        not RAW_ACTION_MAP.is_file()
        or sha256_path(RAW_ACTION_MAP) != args.raw_action_map_sha256
        or not REFERENCE_WORKER.is_file()
        or sha256_path(REFERENCE_WORKER) != args.reference_worker_sha256
        or not COMMON_MODULE.is_file()
        or sha256_path(COMMON_MODULE) != args.common_module_sha256
        or not REGISTRY_BUILDER.is_file()
        or sha256_path(REGISTRY_BUILDER) != args.registry_builder_sha256
    ):
        raise ValueError("critical action or dispatcher artifact changed")
    exclusion_path = args.exclusion_registry.resolve()
    if (
        not exclusion_path.is_file()
        or sha256_path(exclusion_path) != args.exclusion_registry_sha256
    ):
        raise ValueError("pinned exclusion registry changed")
    exclusion = json.loads(exclusion_path.read_text(encoding="utf-8"))
    if (
        not isinstance(exclusion, dict)
        or exclusion.get("schemaVersion") != "f5-current-exclusion-registry-v1"
        or exclusion.get("status") != "sealed_current_exclusions"
        or not isinstance(exclusion.get("blockedPairs"), list)
        or not isinstance(exclusion.get("blockedHashes"), list)
    ):
        raise ValueError("pinned exclusion registry is malformed")
    blocked_pairs = set()
    for row in exclusion["blockedPairs"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("label"), str)
            or not isinstance(row.get("r"), int)
        ):
            raise ValueError("blocked pair registry row is malformed")
        blocked_pairs.add((row["label"], int(row["r"])))
    blocked_hashes = set()
    for digest in exclusion["blockedHashes"]:
        if not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest):
            raise ValueError("blocked hash registry row is malformed")
        blocked_hashes.add(digest)
    if (
        len(blocked_pairs) != len(exclusion["blockedPairs"])
        or len(blocked_hashes) != len(exclusion["blockedHashes"])
        or allowed_pairs & blocked_pairs
    ):
        raise ValueError("allowed target conflicts with pinned exclusions")

    registry_builder = runpy.run_path(str(REGISTRY_BUILDER))
    if registry_builder["build_registry"]() != exclusion:
        raise ValueError("sealed exclusion registry is not current before arithmetic")
    common = runpy.run_path(str(COMMON_MODULE))
    action_core = common["action_core"]
    action_sha256 = common["action_sha256"]
    canonical_quotient_sha256 = common["canonical_quotient_sha256"]
    quotient_from_even_coefficients = common["quotient_from_even_coefficients"]

    action_path = args.action_file.resolve()
    if not action_path.is_file() or sha256_path(action_path) != args.action_file_sha256:
        raise ValueError("pinned action artifact changed")
    lines = action_path.read_text(encoding="utf-8").splitlines()
    cached_actions = []
    for record, expected_sha in zip(
        args.action_record, args.action_row_sha256, strict=True
    ):
        if record <= 0 or record > len(lines):
            raise ValueError("action record is out of range")
        line = lines[record - 1]
        if sha256_bytes(line.encode("utf-8")) != expected_sha:
            raise ValueError("pinned action row changed")
        row = json.loads(line)
        if str(row["sourceLabel"]) != args.source_label:
            raise ValueError("action row belongs to another source")
        cached_actions.append(row)
    if len({action_sha256(row) for row in cached_actions}) != len(cached_actions):
        raise ValueError("action pins repeat an exact action")
    action_pins = {
        action_sha256(row): {
            "record": int(record),
            "rowSha256": str(row_sha),
        }
        for row, record, row_sha in zip(
            cached_actions,
            args.action_record,
            args.action_row_sha256,
            strict=True,
        )
    }

    if (
        sha256_path(RAW_ACTION_MAP) != args.raw_action_map_sha256
        or sha256_path(REFERENCE_WORKER) != args.reference_worker_sha256
        or sha256_path(COMMON_MODULE) != args.common_module_sha256
        or sha256_path(REGISTRY_BUILDER) != args.registry_builder_sha256
        or sha256_path(exclusion_path) != args.exclusion_registry_sha256
    ):
        raise ValueError("critical pinned boundary changed before reconstruction")
    raw_rows = [
        json.loads(line)
        for line in RAW_ACTION_MAP.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    raw = [row for row in raw_rows if row["sourceLabel"] == args.source_label]
    if len(raw) != 1 or len(raw[0].get("systems") or []) != 1:
        raise ValueError("source does not have one literal block system")

    reference = runpy.run_path(str(REFERENCE_WORKER))
    rebuilt = reference["rebuild_exact_actions"](args.source_label, raw[0])
    cached = {action_sha256(row): row for row in cached_actions}
    if set(rebuilt) != set(cached):
        raise ValueError("pinned action set differs from independent reconstruction")
    for digest in rebuilt:
        if action_core(rebuilt[digest]) != action_core(cached[digest]):
            raise ValueError("cached action differs from rebuilt exact action")
    actions = sorted(
        cached_actions,
        key=lambda row: (str(row["targetLabel"]), row["pairOrbit"], action_sha256(row)),
    )
    if generalized_tc0:
        action_labels = {str(action["targetLabel"]) for action in actions}
        for label, signature in allowed_pairs:
            if label not in action_labels:
                raise ValueError("allowed target label is absent from exact actions")
            possible = {
                int(value)
                for action in actions
                if str(action["targetLabel"]) == label
                for value in action[
                    "sourceSignatureToPossibleTargetSignatures"
                ].get(str(args.source_r), [])
            }
            if signature not in possible:
                raise ValueError(
                    "allowed target pair is impossible for the source signature"
                )
    else:
        for action in actions:
            if action["sourceSignatureToPossibleTargetSignatures"].get(
                str(args.source_r)
            ) != [args.source_r]:
                raise ValueError("pinned source signature is not deterministic")

    connection = sqlite3.connect(f"file:{LEDGER.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.status,v.label,v.t,v.r,
               v.scoreable,v.field_disc_abs
        FROM polynomials p JOIN verifications v
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
        raise ValueError("accepted source pin changed")
    coefficient_text = str(source["coefficients"])
    quotient_text = quotient_from_even_coefficients(coefficient_text)
    if (
        quotient_text is None
        or canonical_quotient_sha256(quotient_text)
        != args.canonical_quotient_sha256
    ):
        raise ValueError("canonical quotient pin changed")

    source_coefficients = [int(value) for value in coefficient_text.split(",")]
    ring_y = PolynomialRing(ZZ, "y")
    quotient = ring_y([ZZ(value) for value in quotient_text.split(",")])
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("source quotient is not monic irreducible degree 12")
    pair_resolvent = quotient.symmetric_power(2, monic=True)
    factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
    if any(factor.degree() == 12 and exponent != 1 for factor, exponent in factors):
        raise ValueError("degree-12 factor is not simple")
    degree_twelve = [factor for factor, _ in factors if factor.degree() == 12]
    if len(degree_twelve) != len(actions):
        raise ValueError("degree-12 factor count differs from exact action count")

    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    candidates = [ring_x(factor)(x**2) for factor in degree_twelve]
    if any(
        candidate.degree() != 24
        or not candidate.is_monic()
        or not candidate.is_irreducible()
        for candidate in candidates
    ):
        raise ValueError("factor lift is not monic irreducible degree 24")
    candidate_coefficients = [
        [int(value) for value in candidate.list()] for candidate in candidates
    ]
    assignment = reference["resolve_labels"](
        args.source_label, source_coefficients, candidate_coefficients, actions
    )
    if int(assignment["labelAssignmentCount"]) != 1:
        raise ArithmeticError("bounded modular dispatcher did not prove unique labels")
    labels = list(assignment["candidateTargetLabels"])
    if len(labels) != len(candidates):
        raise ValueError("dispatcher returned the wrong label count")

    resolved = []
    for index, (candidate, label) in enumerate(zip(candidates, labels, strict=True)):
        label = str(label)
        signature = int(candidate.number_of_real_roots())
        matching_actions = [
            action for action in actions if str(action["targetLabel"]) == label
        ]
        possible_signatures = {
            int(value)
            for action in matching_actions
            for value in action[
                "sourceSignatureToPossibleTargetSignatures"
            ].get(str(args.source_r), [])
        }
        if signature not in possible_signatures:
            raise ValueError(
                "resolved candidate signature contradicts the exact action profile"
            )
        resolved.append(
            {
                "candidate": candidate,
                "factorIndex": index,
                "label": label,
                "pair": (label, signature),
                "signature": signature,
            }
        )

    if generalized_tc0:
        resolved_pair_counts = {
            pair: sum(row["pair"] == pair for row in resolved)
            for pair in {row["pair"] for row in resolved}
        }
        if any(resolved_pair_counts.get(pair, 0) != 1 for pair in allowed_pairs):
            raise ValueError(
                "resolved factors did not hit every pinned tc0 target exactly once"
            )
        distinct_pairs = sorted(allowed_pairs)
    else:
        if any(row["signature"] != args.source_r for row in resolved):
            raise ValueError("candidate signature differs from deterministic profile")
        distinct_pairs = sorted({row["pair"] for row in resolved})
        expected_pairs = sorted(
            {(str(action["targetLabel"]), args.source_r) for action in actions}
        )
        if distinct_pairs != expected_pairs:
            raise ValueError("resolved target-pair set differs from exact action set")

    live_rows = {}
    for pair in distinct_pairs:
        row = connection.execute(
            """
            SELECT t.*,
              EXISTS(
                SELECT 1 FROM baseline_pairs b
                WHERE b.label=t.label AND b.r=t.r
              ) baseline,
              EXISTS(
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              ) owned_scoreable,
              EXISTS(
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
              ) owned_accepted
            FROM targets t WHERE t.label=? AND t.r=?
            """,
            pair,
        ).fetchone()
        if (
            row is None
            or int(row["baseline"])
            or int(row["owned_scoreable"])
            or int(row["owned_accepted"])
            or (
                generalized_tc0
                and (
                    int(row["team_count"]) != 0
                    or int(row["discovered"])
                    or row["minimum_disc_abs"] is not None
                )
            )
            or (
                not generalized_tc0
                and int(row["team_count"]) not in (1, 2)
            )
        ):
            expected_class = "tc0" if generalized_tc0 else "tc1/tc2"
            raise ValueError(f"target is no longer live {expected_class}: {pair}")
        live_rows[pair] = {
            "baseline": bool(row["baseline"]),
            "discovered": bool(row["discovered"]),
            "generatedAt": str(row["generated_at"]),
            "minimumDiscAbs": row["minimum_disc_abs"],
            "ownedAccepted": bool(row["owned_accepted"]),
            "ownedScoreable": bool(row["owned_scoreable"]),
            "teamCount": int(row["team_count"]),
        }

    outbox_hashes, receipted_hashes = staged_hashes()
    candidate_results = []
    for resolved_row in resolved:
        index = int(resolved_row["factorIndex"])
        candidate = resolved_row["candidate"]
        label = str(resolved_row["label"])
        signature = int(resolved_row["signature"])
        pair = resolved_row["pair"]
        eligible = not generalized_tc0 or pair in allowed_pairs
        line = ",".join(str(value) for value in candidate.list())
        digest = sha256_bytes(line.encode("utf-8"))
        ledger_occurrences = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        )
        if eligible and (
            ledger_occurrences
            or digest in blocked_hashes
            or digest in outbox_hashes
            or digest in receipted_hashes
        ):
            raise ValueError("candidate is already known, staged, or receipted")
        factor_line = ",".join(str(value) for value in degree_twelve[index].list())
        candidate_results.append(
            {
                "coefficientLine": line,
                "coefficientSha256": digest,
                "eligibleForPinnedTarget": eligible,
                "factorIndex": index,
                "fieldDiscriminantAbs": (
                    str(
                        abs(
                            ZZ(
                                NumberField(
                                    candidate, f"a{index}"
                                ).absolute_discriminant()
                            )
                        )
                    )
                    if eligible
                    else None
                ),
                "irreducible": True,
                "novelty": {
                    "ledgerHashOccurrences": ledger_occurrences,
                    "presentInBlockedHashRegistry": digest in blocked_hashes,
                    "presentInOutbox": digest in outbox_hashes,
                    "presentInReceiptedManifest": digest in receipted_hashes,
                },
                "polynomialDiscriminantAbs": (
                    str(abs(ZZ(candidate.discriminant()))) if eligible else None
                ),
                "r": signature,
                "selectedFactorCoefficientSha256": sha256_bytes(
                    factor_line.encode("utf-8")
                ),
                "status": (
                    "certified_pinned_live_tc0"
                    if generalized_tc0 and eligible
                    else (
                        "resolved_outside_pinned_target_set"
                        if generalized_tc0
                        else "certified_live_tc1_tc2"
                    )
                ),
                "targetLabel": str(label),
            }
        )

    eligible_hashes = {
        row["coefficientSha256"]
        for row in candidate_results
        if row["eligibleForPinnedTarget"]
    }
    if (
        sha256_path(RAW_ACTION_MAP) != args.raw_action_map_sha256
        or sha256_path(REFERENCE_WORKER) != args.reference_worker_sha256
        or sha256_path(COMMON_MODULE) != args.common_module_sha256
        or sha256_path(REGISTRY_BUILDER) != args.registry_builder_sha256
        or sha256_path(exclusion_path) != args.exclusion_registry_sha256
        or sha256_path(action_path) != args.action_file_sha256
    ):
        raise ValueError("pinned proof boundary changed during arithmetic")
    if registry_builder["build_registry"]() != exclusion:
        raise ValueError("sealed exclusion boundary changed during arithmetic")
    final_outbox_hashes, final_receipted_hashes = staged_hashes()
    if eligible_hashes & (
        blocked_hashes | final_outbox_hashes | final_receipted_hashes
    ):
        raise ValueError("candidate became blocked, staged, or receipted during arithmetic")
    for digest in eligible_hashes:
        if int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0]
        ):
            raise ValueError("candidate entered the ledger during arithmetic")
    for pair in distinct_pairs:
        row = connection.execute(
            """
            SELECT t.*,
              EXISTS(
                SELECT 1 FROM baseline_pairs b
                WHERE b.label=t.label AND b.r=t.r
              ) baseline,
              EXISTS(
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              ) owned_scoreable,
              EXISTS(
                SELECT 1 FROM verifications v
                WHERE v.label=t.label AND v.r=t.r AND v.status='accepted'
              ) owned_accepted
            FROM targets t WHERE t.label=? AND t.r=?
            """,
            pair,
        ).fetchone()
        final_snapshot = None if row is None else {
            "baseline": bool(row["baseline"]),
            "discovered": bool(row["discovered"]),
            "generatedAt": str(row["generated_at"]),
            "minimumDiscAbs": row["minimum_disc_abs"],
            "ownedAccepted": bool(row["owned_accepted"]),
            "ownedScoreable": bool(row["owned_scoreable"]),
            "teamCount": int(row["team_count"]),
        }
        if final_snapshot != live_rows[pair]:
            raise ValueError("target state changed during arithmetic")
    connection.close()

    factor_rows = []
    for factor, exponent in factors:
        line = ",".join(str(value) for value in factor.list())
        factor_rows.append(
            {
                "coefficientSha256": sha256_bytes(line.encode("utf-8")),
                "degree": int(factor.degree()),
                "exponent": exponent,
            }
        )
    route_identity = {
        "actionRowSha256s": list(args.action_row_sha256),
        "generalizedTc0": generalized_tc0,
        "sourceCoefficientSha256": args.source_coefficient_sha256,
        "targetPairs": [
            {"label": label, "r": signature} for label, signature in distinct_pairs
        ],
    }
    guaranteed_score = sum(
        2.0 ** (-live_rows[pair]["teamCount"]) for pair in distinct_pairs
    )
    result = {
        "actions": [
            {
                "actionSha256": action_sha256(action),
                "record": action_pins[action_sha256(action)]["record"],
                "rowSha256": action_pins[action_sha256(action)]["rowSha256"],
                "targetLabel": str(action["targetLabel"]),
            }
            for action in actions
        ],
        "actionArtifact": str(action_path.relative_to(ROOT)),
        "actionArtifactSha256": args.action_file_sha256,
        "assignmentCertificate": assignment,
        "allowedTargetPairs": [
            {"label": label, "r": signature}
            for label, signature in sorted(allowed_pairs)
        ],
        "candidates": candidate_results,
        "factorCertificate": {
            "factors": factor_rows,
            "pairResolventSha256": sha256_bytes(
                ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
            ),
        },
        "guaranteedDistinctPairScore": guaranteed_score,
        "inputArtifacts": {
            "commonModule": str(COMMON_MODULE.relative_to(ROOT)),
            "commonModuleSha256": args.common_module_sha256,
            "exclusionRegistry": str(exclusion_path.relative_to(ROOT)),
            "exclusionRegistrySha256": args.exclusion_registry_sha256,
            "registryBuilder": str(REGISTRY_BUILDER.relative_to(ROOT)),
            "registryBuilderSha256": args.registry_builder_sha256,
            "rawActionMap": str(RAW_ACTION_MAP.relative_to(ROOT)),
            "rawActionMapSha256": args.raw_action_map_sha256,
            "referenceWorker": str(REFERENCE_WORKER.relative_to(ROOT)),
            "referenceWorkerSha256": args.reference_worker_sha256,
        },
        "liveTargets": [
            {"label": pair[0], "r": pair[1], **live_rows[pair]}
            for pair in distinct_pairs
        ],
        "networkCalls": 0,
        "routeIdentity": route_identity,
        "routeIdentitySha256": canonical_json_sha256(route_identity),
        "source": {
            "canonicalQuotientSha256": args.canonical_quotient_sha256,
            "coefficientSha256": args.source_coefficient_sha256,
            "fieldDiscAbs": str(source["field_disc_abs"]),
            "label": args.source_label,
            "polynomialIndex": args.polynomial_index,
            "r": args.source_r,
            "submissionId": args.submission_id,
        },
        "status": (
            "certified_exact_general_multiorbit_tc0_unstaged"
            if generalized_tc0
            else "certified_exact_set_packet_unstaged"
        ),
        "submissionCalls": 0,
    }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_write(output, text)
    print(
        json.dumps(
            {
                "candidateSha256s": [
                    row["coefficientSha256"]
                    for row in candidate_results
                    if row["eligibleForPinnedTarget"]
                ],
                "guaranteedDistinctPairScore": guaranteed_score,
                "output": str(output),
                "outputSha256": sha256_bytes(text.encode("utf-8")),
                "targetPairs": route_identity["targetPairs"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal jointly exact bundles from an unresolved Frobenius certificate.

An unresolved factor-to-label assignment can still certify a batch when every
surviving assignment maps a set of factors bijectively onto the same set of
target pairs.  This offline stage validates that invariant, excludes every
baseline/owned/known/receipted outcome, and emits a coefficient-free audit
certificate plus an optional polynomial manifest.  It never submits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import stage_frobenius_gold as frobenius
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RECEIPTS = ROOT / "receipts"
DATABASE = DATA / "ledger.sqlite3"
METHOD = "joint-bijective-unresolved-frobenius-stage-v1"
MULTISET_METHOD = "joint-multiset-unresolved-frobenius-stage-v1"
INJECTIVE_SET_METHOD = "joint-injective-variable-set-unresolved-frobenius-stage-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new(path: Path, payload: bytes) -> None:
    path = path.expanduser().resolve()
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


def source_row(
    candidate_rows: list[dict], source_pair: tuple[str, int]
) -> dict:
    matches = [
        row
        for row in candidate_rows
        if row.get("status") == "certified_multi"
        and str(row.get("sourceLabel")) == source_pair[0]
        and int(row.get("sourceR", -1)) == source_pair[1]
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one certified_multi source, found {len(matches)}")
    return matches[0]


def certificate_row(certificate: dict, source: dict) -> dict:
    key = frobenius.source_key(source)
    matches = [
        row
        for row in certificate.get("rows") or []
        if frobenius.source_key(row) == key
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one matching certificate row, found {len(matches)}")
    row = matches[0]
    if row.get("status") != "unresolved":
        raise ValueError(f"certificate row is not unresolved: {row.get('status')}")
    return row


def source_provenance(connection: sqlite3.Connection, source: dict) -> dict:
    key = frobenius.source_key(source)
    rows = connection.execute(
        """
        SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable
        FROM polynomials p JOIN verifications v
          ON v.submission_id=p.submission_id
         AND v.polynomial_index=p.polynomial_index
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        key[:2],
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"source provenance is missing or nonunique: {key}")
    digest, status, label, r_value, scoreable = rows[0]
    if (
        str(status) != "accepted"
        or str(label) != key[2]
        or int(r_value) != key[3]
        or int(scoreable or 0) != 1
    ):
        raise ValueError(f"source provenance is not accepted and scoreable: {key}")
    return {
        "coefficientSha256": str(digest),
        "label": key[2],
        "polynomialIndex": key[1],
        "r": key[3],
        "scoreable": True,
        "status": "accepted",
        "submissionId": key[0],
    }


def normalized_assignments(row: dict, factor_count: int) -> list[tuple[str, ...]]:
    slot_labels = [str(slot["targetLabel"]) for slot in row.get("targetSlots") or []]
    if len(slot_labels) != factor_count:
        raise ValueError("target-slot count does not match factor count")
    remaining = row.get("remainingLabelAssignments")
    if not isinstance(remaining, list) or len(remaining) < 2:
        raise ValueError("joint stage requires at least two surviving assignments")
    normalized = []
    for raw in remaining:
        assignment = tuple(str(label) for label in raw)
        if len(assignment) != factor_count or Counter(assignment) != Counter(slot_labels):
            raise ValueError("surviving assignment is not a target-label permutation")
        for label in assignment:
            frobenius.label_t(label)
        normalized.append(assignment)
    if len(normalized) != len(set(normalized)):
        raise ValueError("surviving label assignments are not unique")
    if int(row.get("remainingSlotAssignmentCount", -1)) < len(normalized):
        raise ValueError("certificate slot-assignment count is inconsistent")
    return normalized


def target_snapshot(connection: sqlite3.Connection) -> dict[tuple[str, int], dict]:
    return {
        (str(row[0]), int(row[1])): {
            "discovered": bool(row[2]),
            "generatedAt": str(row[3]),
            "minimumDiscAbs": str(row[4]) if row[4] is not None else None,
            "teamCount": int(row[5]),
        }
        for row in connection.execute(
            "SELECT label,r,discovered,generated_at,minimum_disc_abs,team_count "
            "FROM targets"
        )
    }


def stage(args: argparse.Namespace) -> dict:
    if args.allow_multiplicity and args.allow_injective_variable_set:
        raise ValueError("multiplicity and variable-set modes are mutually exclusive")
    if args.allow_injective_variable_set:
        method = INJECTIVE_SET_METHOD
    elif args.allow_multiplicity:
        method = MULTISET_METHOD
    else:
        method = METHOD
    certificate_path = args.certificate.expanduser().resolve()
    candidates_path = args.candidates.expanduser().resolve()
    database_path = args.database.expanduser().resolve()
    receipts_path = args.receipts.expanduser().resolve()
    data_path = args.data.expanduser().resolve()
    outputs = {
        args.manifest.expanduser().resolve(),
        args.stage_certificate.expanduser().resolve(),
        args.summary.expanduser().resolve(),
    }
    if len(outputs) != 3 or database_path in outputs:
        raise ValueError("stage outputs must be distinct and must not overwrite the ledger")

    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    candidate_rows = frobenius.read_jsonl(candidates_path)
    frobenius.validate_certificate_header(
        certificate, certificate.get("rows") or [], candidates_path, allow_unresolved=True
    )
    source_pair = (str(args.source_pair[0]), int(args.source_pair[1]))
    source = source_row(candidate_rows, source_pair)
    proof_row = certificate_row(certificate, source)
    candidates = {
        int(candidate["factorIndex"]): candidate
        for candidate in source.get("candidates") or []
    }
    if sorted(candidates) != list(range(len(candidates))) or not candidates:
        raise ValueError("candidate factor indexes are not consecutive")
    assignments = normalized_assignments(proof_row, len(candidates))

    candidate_rows_by_factor = {}
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for factor_index, candidate in candidates.items():
        line = frobenius.validated_coefficient_line(candidate, frobenius.source_key(source))
        digest = str(candidate["coefficientSha256"])
        target_r = int(candidate["targetR"])
        polynomial_disc = int(candidate["polynomialDiscriminantAbs"])
        if target_r not in range(0, 25, 2) or polynomial_disc <= 0:
            raise ValueError(f"invalid candidate metadata for factor {factor_index}")
        possible_pairs = frozenset(
            (assignment[factor_index], target_r) for assignment in assignments
        )
        if not possible_pairs:
            raise ValueError(f"factor {factor_index} has no possible target pair")
        pair_index[digest].update(possible_pairs)
        candidate_rows_by_factor[factor_index] = {
            "coefficientBytes": len(line.encode("ascii")),
            "coefficientLine": line,
            "coefficientSha256": digest,
            "factorIndex": factor_index,
            "polynomialDiscriminantAbs": polynomial_disc,
            "possiblePairs": possible_pairs,
            "targetR": target_r,
        }

    groups: dict[tuple[int, frozenset[str]], list[int]] = defaultdict(list)
    for factor_index, row in candidate_rows_by_factor.items():
        possible_labels = frozenset(label for label, _r in row["possiblePairs"])
        groups[(row["targetR"], possible_labels)].append(factor_index)

    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
        provenance = source_provenance(connection, source)
        known_hashes = {
            str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        baseline_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        owned_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        receipt_hashes, receipt_pairs, receipt_audit = single.receipt_exclusions(
            receipts_path, data_path, connection, pair_index
        )
        targets = target_snapshot(connection)

    selected_groups = []
    skipped = Counter()
    occupied_pairs: set[tuple[str, int]] = set()
    for (target_r, possible_labels), factor_indexes in sorted(groups.items()):
        pair_set = frozenset((label, target_r) for label in possible_labels)
        assignment_pair_counters = [
            Counter((assignment[index], target_r) for index in factor_indexes)
            for assignment in assignments
        ]
        exact_pair_multiset = assignment_pair_counters[0]
        outcome_pair_sets = {
            frozenset(counter) for counter in assignment_pair_counters
        }
        if args.allow_injective_variable_set:
            # Each surviving assignment may choose a different exact pair set,
            # but every set must remain injective within this factor group.
            if any(
                sum(counter.values()) != len(factor_indexes)
                or any(count != 1 for count in counter.values())
                for counter in assignment_pair_counters
            ):
                skipped["not_jointly_injective"] += 1
                continue
        elif args.allow_multiplicity:
            # The full factor group is safe when every surviving global
            # assignment preserves exactly the same target-pair multiset.
            # Repeated pairs are submitted as repeated rows because no proper
            # subset need retain the rarer outcome under the ambiguity.
            if any(counter != exact_pair_multiset for counter in assignment_pair_counters):
                skipped["not_jointly_multiset_exact"] += 1
                continue
        elif len(factor_indexes) != len(pair_set) or any(
            set(counter) != pair_set or any(count != 1 for count in counter.values())
            for counter in assignment_pair_counters
        ):
            # Default mode retains the original stronger bijective invariant.
            skipped["not_jointly_bijective"] += 1
            continue
        rows = [candidate_rows_by_factor[index] for index in sorted(factor_indexes)]
        if any(row["coefficientSha256"] in known_hashes for row in rows):
            skipped["known_ledger_hash"] += 1
            continue
        if any(row["coefficientSha256"] in receipt_hashes for row in rows):
            skipped["receipt_hash"] += 1
            continue
        if pair_set & baseline_pairs:
            skipped["baseline_pair"] += 1
            continue
        if pair_set & owned_pairs:
            skipped["locally_owned_pair"] += 1
            continue
        if pair_set & receipt_pairs:
            skipped["receipt_pair"] += 1
            continue
        if any(pair not in targets for pair in pair_set):
            skipped["target_missing"] += 1
            continue
        if any(targets[pair]["teamCount"] <= 0 for pair in pair_set):
            skipped["not_current_shared"] += 1
            continue
        if occupied_pairs & pair_set:
            skipped["overlapping_selected_pair_set"] += 1
            continue
        occupied_pairs.update(pair_set)
        selected_groups.append(
            {
                "outcomePairSets": outcome_pair_sets,
                "pairMultiset": exact_pair_multiset,
                "pairs": pair_set,
                "rows": rows,
            }
        )

    selected_rows = [row for group in selected_groups for row in group["rows"]]
    if not selected_rows:
        raise ValueError("no safe jointly bijective unresolved bundle remains")
    lines = [row["coefficientLine"] for row in selected_rows]
    hashes = [row["coefficientSha256"] for row in selected_rows]
    if len(lines) != len(set(lines)) or len(hashes) != len(set(hashes)):
        raise ValueError("selected joint bundle contains duplicate polynomials")
    manifest_payload = "".join(line + "\n" for line in lines).encode("ascii")

    public_groups = []
    score_minimum = Fraction(0, 1)
    score_maximum = Fraction(0, 1)
    for group in selected_groups:
        pairs = sorted(group["pairs"], key=lambda pair: (frobenius.label_t(pair[0]), pair[1]))
        outcome_scores = [
            sum(
                (Fraction(1, 2 ** targets[pair]["teamCount"]) for pair in outcome_set),
                Fraction(),
            )
            for outcome_set in group["outcomePairSets"]
        ]
        score_minimum += min(outcome_scores)
        score_maximum += max(outcome_scores)
        public_group = {
            "factorIndexes": [row["factorIndex"] for row in group["rows"]],
            "polynomialHashes": [row["coefficientSha256"] for row in group["rows"]],
            "targets": [
                {**targets[pair], "label": pair[0], "r": pair[1]} for pair in pairs
            ],
        }
        if args.allow_injective_variable_set:
            outcome_sets = sorted(
                group["outcomePairSets"],
                key=lambda outcome_set: [
                    (frobenius.label_t(pair[0]), pair[1]) for pair in sorted(outcome_set)
                ],
            )
            public_group.update(
                {
                    "possibleDistinctPairSets": [
                        [
                            f"{label}/r{r_value}"
                            for label, r_value in sorted(
                                outcome_set,
                                key=lambda pair: (frobenius.label_t(pair[0]), pair[1]),
                            )
                        ]
                        for outcome_set in outcome_sets
                    ],
                    "possiblePairsUnion": [
                        f"{label}/r{r_value}" for label, r_value in pairs
                    ],
                }
            )
        elif args.allow_multiplicity:
            expanded_pairs = [
                pair
                for pair in pairs
                for _ in range(int(group["pairMultiset"][pair]))
            ]
            public_group.update(
                {
                    "distinctPairsAsExactSet": [
                        f"{label}/r{r_value}" for label, r_value in pairs
                    ],
                    "pairMultiplicities": {
                        f"{label}/r{r_value}": int(group["pairMultiset"][(label, r_value)])
                        for label, r_value in pairs
                    },
                    "possiblePairsAsExactMultiset": [
                        f"{label}/r{r_value}" for label, r_value in expanded_pairs
                    ],
                }
            )
        else:
            public_group["possiblePairsAsExactSet"] = [
                f"{label}/r{r_value}" for label, r_value in pairs
            ]
        public_groups.append(public_group)

    checks = {
            "allCandidatePayloadHashesCanonicalAndMatched": True,
            "allRemainingAssignmentsAreTargetLabelPermutations": True,
            "baselineOwnedKnownAndReceiptHashesPairsExcluded": True,
            "certificateContainsNoCoefficientPayload": True,
            "sourceRejoinedToAcceptedScoreableLedgerRow": True,
    }
    if args.allow_injective_variable_set:
        checks["allSelectedOutcomeSetsAreInjectiveAndPairwiseUnionDisjoint"] = True
    elif args.allow_multiplicity:
        checks["allSelectedGroupsPreserveTheSameExactPairMultiset"] = True
    else:
        checks["allSelectedGroupsAreBijectiveOntoTheSameDistinctPairSet"] = True
    stage_certificate = {
        "checks": checks,
        "input": {
            "candidates": display_path(candidates_path),
            "candidatesSha256": sha256_path(candidates_path),
            "frobeniusCertificate": display_path(certificate_path),
            "frobeniusCertificateSha256": sha256_path(certificate_path),
        },
        "manifest": {
            "bytes": len(manifest_payload),
            "path": display_path(args.manifest),
            "polynomials": len(selected_rows),
            "sha256": sha256_bytes(manifest_payload),
        },
        "method": method,
        "networkCalls": 0,
        "receiptExclusion": receipt_audit,
        "remainingLabelAssignments": assignments,
        "selectedGroups": public_groups,
        "selectionAudit": {"skippedGroupCounts": dict(sorted(skipped.items()))},
        "source": provenance,
        "submissionCalls": 0,
    }
    if args.allow_injective_variable_set:
        stage_certificate["projectedMarginalScoreRangeExact"] = {
            "maximum": str(score_maximum),
            "minimum": str(score_minimum),
        }
    else:
        if score_minimum != score_maximum:
            raise ValueError("constant-set stage unexpectedly has a score range")
        stage_certificate["projectedMarginalScoreExact"] = str(score_minimum)
    stage_certificate_payload = json_bytes(stage_certificate)
    summary = {
        "certificate": display_path(args.stage_certificate),
        "certificateSha256": sha256_bytes(stage_certificate_payload),
        "manifest": display_path(args.manifest),
        "manifestBytes": len(manifest_payload),
        "manifestSha256": sha256_bytes(manifest_payload),
        "method": method,
        "polynomials": len(selected_rows),
        "selectedDistinctPairs": sum(
            min(len(outcome_set) for outcome_set in group["outcomePairSets"])
            for group in selected_groups
        ),
        "status": "sealed_not_submitted",
        "submissionCalls": 0,
    }
    if args.allow_injective_variable_set:
        summary["possiblePairUnionCount"] = len(occupied_pairs)
        summary["projectedMarginalScoreRangeExact"] = {
            "maximum": str(score_maximum),
            "minimum": str(score_minimum),
        }
    else:
        summary["projectedMarginalScoreExact"] = str(score_minimum)
    summary_payload = json_bytes(summary)
    write_new(args.stage_certificate, stage_certificate_payload)
    write_new(args.summary, summary_payload)
    write_new(args.manifest, manifest_payload)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--source-pair", nargs=2, metavar=("LABEL", "R"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stage-certificate", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument(
        "--allow-multiplicity",
        action="store_true",
        help=(
            "stage a full factor group when every surviving assignment preserves "
            "the same exact target-pair multiset"
        ),
    )
    parser.add_argument(
        "--allow-injective-variable-set",
        action="store_true",
        help=(
            "stage factor groups whose surviving assignments give varying but "
            "always injective, all-safe target-pair sets"
        ),
    )
    args = parser.parse_args()
    try:
        result = stage(args)
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

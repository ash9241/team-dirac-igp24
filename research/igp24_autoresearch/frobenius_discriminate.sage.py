#!/usr/bin/env sage -python
"""Exactly label degree-24 sibling factors using Frobenius cycle types.

This is a read-only certifier.  It reads the multi-factor JSONL produced by
``build_pair_sum_multi.py`` and writes a reproducible certificate to stdout or
an explicit atomic ``--output`` path; it never opens the submission ledger or
calls the SAIR API.

For a prime at which a monic integral polynomial is squarefree modulo p,
Dedekind's theorem identifies its modular factor-degree multiset with the
cycle type of Frobenius in the polynomial's natural Galois action.  GAP
enumerates the exact cycle-type set of each candidate ``24T`` group, so an
observed type outside that set rigorously excludes the corresponding label.
The known multiset of pair-orbit targets then turns exclusions into a finite
matching problem.

Occasionally two target actions have the same marginal cycle-type set.  The
optional (default-on) joint fallback reconstructs all length-24 pair actions
of the source group in GAP.  At a prime squarefree for every sibling, their
factorization patterns must be one joint cycle profile of a single source
conjugacy class.  This is still an exact exclusion test, not a density or
probabilistic heuristic.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

from sage.all import GF, PolynomialRing, libgap, prime_range
from sage.env import SAGE_VERSION


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "data" / "pair_sum_multi_candidates.jsonl"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
POINTS_24 = libgap.eval("[1..24]")

CycleType = tuple[int, ...]
SlotAssignment = tuple[int, ...]  # factor position -> target-slot position


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(label)
    if match is None:
        raise ValueError(f"invalid degree-24 transitive-group label: {label!r}")
    return int(match.group(1))


def cycle_type(permutation) -> CycleType:
    """Return all natural-action cycle lengths, including fixed points."""
    return tuple(
        sorted(int(value) for value in libgap.CycleLengths(permutation, POINTS_24))
    )


def collection_digest(values: Iterable[tuple]) -> str:
    serializable = []
    for value in sorted(values):
        if value and isinstance(value[0], tuple):
            serializable.append([list(part) for part in value])
        else:
            serializable.append(list(value))
    payload = json.dumps(serializable, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TargetCycleTypes:
    """Lazily enumerate and cache exact natural-action cycle-type sets."""

    def __init__(self) -> None:
        self.types: dict[str, frozenset[CycleType]] = {}
        self.metadata: dict[str, dict] = {}

    def get(self, label: str) -> frozenset[CycleType]:
        if label in self.types:
            return self.types[label]
        group = libgap.TransitiveGroup(24, label_t(label))
        classes = list(libgap.ConjugacyClasses(group))
        types = frozenset(
            cycle_type(libgap.Representative(conjugacy_class))
            for conjugacy_class in classes
        )
        if not types or any(sum(pattern) != 24 for pattern in types):
            raise ArithmeticError(f"invalid GAP cycle-type census for {label}")
        self.types[label] = types
        self.metadata[label] = {
            "conjugacyClassCount": len(classes),
            "cycleTypeCount": len(types),
            "cycleTypeSetSha256": collection_digest(types),
        }
        return types


class ModularPatterns:
    """Cache exact squarefree modular factorization patterns for one packet."""

    def __init__(self, coefficient_lists: list[list[int]]) -> None:
        self.coefficient_lists = coefficient_lists
        self.rings: dict[int, object] = {}
        self.cache: dict[tuple[int, int], CycleType | None] = {}
        self.factorizations = 0
        self.nonsquarefree = 0

    def pattern(self, factor_position: int, prime: int) -> CycleType | None:
        key = (factor_position, prime)
        if key in self.cache:
            return self.cache[key]
        ring = self.rings.get(prime)
        if ring is None:
            ring = PolynomialRing(GF(prime), "x")
            self.rings[prime] = ring
        polynomial = ring(self.coefficient_lists[factor_position])
        factorization = list(polynomial.factor())
        self.factorizations += 1
        if any(int(exponent) != 1 for _, exponent in factorization):
            self.nonsquarefree += 1
            self.cache[key] = None
            return None
        pattern = tuple(
            sorted(
                int(factor.degree())
                for factor, exponent in factorization
                for _ in range(int(exponent))
            )
        )
        if sum(pattern) != 24:
            raise ArithmeticError(
                f"mod-{prime} factor degrees sum to {sum(pattern)}, not 24"
            )
        self.cache[key] = pattern
        return pattern


class JointCycleProfiles:
    """Cache exact correlated cycle profiles of source pair-orbit actions."""

    def __init__(self) -> None:
        self.cache: dict[tuple, tuple[frozenset[tuple[CycleType, ...]], dict]] = {}

    def get(self, source_label: str, target_slots: list[dict]):
        key = (
            source_label,
            tuple(
                (int(slot["orbitIndex"]), str(slot["targetLabel"]))
                for slot in target_slots
            ),
        )
        if key in self.cache:
            return self.cache[key]

        group = libgap.TransitiveGroup(24, label_t(source_label))
        pairs = libgap.Combinations(POINTS_24, 2)
        pair_orbits = list(libgap.Orbits(group, pairs, libgap.OnSets))
        homomorphisms = []
        verified_slots = []
        for slot_position, slot in enumerate(target_slots):
            orbit_index = int(slot["orbitIndex"])
            if orbit_index < 0 or orbit_index >= len(pair_orbits):
                raise ValueError(
                    f"{source_label} target slot {slot_position} has invalid "
                    f"pair-orbit index {orbit_index}"
                )
            orbit = pair_orbits[orbit_index]
            orbit_size = int(libgap.Length(orbit))
            if orbit_size != 24:
                raise ValueError(
                    f"{source_label} pair orbit {orbit_index} has size "
                    f"{orbit_size}, not 24"
                )
            homomorphism = libgap.ActionHomomorphism(group, orbit, libgap.OnSets)
            image = libgap.Image(homomorphism)
            identified_label = f"24T{int(libgap.TransitiveIdentification(image))}"
            expected_label = str(slot["targetLabel"])
            if identified_label != expected_label:
                raise ValueError(
                    f"{source_label} pair orbit {orbit_index}: GAP identifies "
                    f"{identified_label}, JSONL claims {expected_label}"
                )
            kernel_order = int(libgap.Size(libgap.Kernel(homomorphism)))
            if kernel_order != 1:
                raise ValueError(
                    f"joint Frobenius proof requires faithful pair actions; "
                    f"{source_label} orbit {orbit_index} has kernel order "
                    f"{kernel_order}"
                )
            homomorphisms.append(homomorphism)
            verified_slots.append(
                {
                    "slotPosition": slot_position,
                    "orbitIndex": orbit_index,
                    "targetLabel": identified_label,
                    "kernelOrder": kernel_order,
                }
            )

        classes = list(libgap.ConjugacyClasses(group))
        profiles = frozenset(
            tuple(
                cycle_type(libgap.Image(homomorphism, representative))
                for homomorphism in homomorphisms
            )
            for conjugacy_class in classes
            for representative in [libgap.Representative(conjugacy_class)]
        )
        if not profiles:
            raise ArithmeticError(f"empty joint cycle-profile census for {source_label}")
        metadata = {
            "sourceLabel": source_label,
            "sourceConjugacyClassCount": len(classes),
            "jointCycleProfileCount": len(profiles),
            "jointCycleProfileSetSha256": collection_digest(profiles),
            "verifiedTargetSlots": verified_slots,
        }
        result = (profiles, metadata)
        self.cache[key] = result
        return result


def label_assignments(
    slot_assignments: Iterable[SlotAssignment], target_labels: list[str]
) -> list[tuple[str, ...]]:
    return sorted(
        {
            tuple(target_labels[slot_position] for slot_position in assignment)
            for assignment in slot_assignments
        }
    )


def assignment_counts(
    slot_assignments: list[SlotAssignment], target_labels: list[str]
) -> dict:
    return {
        "slotAssignments": len(slot_assignments),
        "labelAssignments": len(label_assignments(slot_assignments, target_labels)),
    }


def validate_packet(row: dict) -> tuple[list[dict], list[dict], list[list[int]]]:
    if row.get("status") != "certified_multi":
        raise ValueError(
            f"{row.get('sourceLabel', '<unknown>')} is not a certified_multi row"
        )
    source_label = str(row["sourceLabel"])
    label_t(source_label)
    candidates = sorted(row["candidates"], key=lambda item: int(item["factorIndex"]))
    if [int(item["factorIndex"]) for item in candidates] != list(
        range(len(candidates))
    ):
        raise ValueError(f"{source_label} factor indexes are not contiguous from zero")
    target_slots = list(row["orbitTargets"])
    if len(candidates) != len(target_slots):
        raise ValueError(
            f"{source_label} has {len(candidates)} factors but "
            f"{len(target_slots)} target slots"
        )
    if not candidates:
        raise ValueError(f"{source_label} has no degree-24 candidates")

    coefficient_lists = []
    for candidate in candidates:
        line = str(candidate["coefficientLine"])
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
        if digest != str(candidate["coefficientSha256"]):
            raise ValueError(
                f"{source_label} factor {candidate['factorIndex']} has a bad "
                "coefficient SHA-256"
            )
        coefficients = [int(value) for value in line.split(",")]
        if len(coefficients) != 25 or coefficients[-1] != 1:
            raise ValueError(
                f"{source_label} factor {candidate['factorIndex']} is not a "
                "monic degree-24 coefficient line"
            )
        if coefficients[0] == 0:
            raise ValueError(
                f"{source_label} factor {candidate['factorIndex']} has zero "
                "constant coefficient"
            )
        coefficient_lists.append(coefficients)

    for slot in target_slots:
        target_label = str(slot["targetLabel"])
        if int(slot["targetT"]) != label_t(target_label):
            raise ValueError(f"inconsistent target label/T pair: {slot}")
        if int(slot["orbitSize"]) != 24:
            raise ValueError(f"non-degree-24 target slot: {slot}")
    return candidates, target_slots, coefficient_lists


def discriminate_packet(
    row: dict,
    primes: list[int],
    target_census: TargetCycleTypes,
    joint_census: JointCycleProfiles,
    use_joint: bool,
) -> dict:
    candidates, target_slots, coefficient_lists = validate_packet(row)
    source_label = str(row["sourceLabel"])
    target_labels = [str(slot["targetLabel"]) for slot in target_slots]
    distinct_labels = sorted(set(target_labels), key=label_t)
    for label in distinct_labels:
        target_census.get(label)

    assignments: list[SlotAssignment] = list(
        itertools.permutations(range(len(target_slots)))
    )
    patterns = ModularPatterns(coefficient_lists)
    marginal_evidence = []
    marginal_primes_examined = 0

    # First use only the natural-action cycle-type set of each target group.
    for prime in primes:
        marginal_primes_examined += 1
        for factor_position, candidate in enumerate(candidates):
            pattern = patterns.pattern(factor_position, prime)
            if pattern is None:
                continue
            before_assignments = assignments
            before_counts = assignment_counts(before_assignments, target_labels)
            assignments = [
                assignment
                for assignment in assignments
                if pattern
                in target_census.get(target_labels[assignment[factor_position]])
            ]
            if len(assignments) < len(before_assignments):
                marginal_evidence.append(
                    {
                        "prime": prime,
                        "factorIndex": int(candidate["factorIndex"]),
                        "coefficientSha256": str(candidate["coefficientSha256"]),
                        "squarefreeModuloPrime": True,
                        "factorDegrees": list(pattern),
                        "before": before_counts,
                        "after": assignment_counts(assignments, target_labels),
                        "compatibleTargetLabels": [
                            label
                            for label in distinct_labels
                            if pattern in target_census.get(label)
                        ],
                    }
                )
            if not assignments or len(label_assignments(assignments, target_labels)) == 1:
                break
        if not assignments or len(label_assignments(assignments, target_labels)) == 1:
            break

    marginal_remaining = label_assignments(assignments, target_labels)
    joint_metadata = None
    joint_evidence = []
    joint_primes_examined = 0

    # Equal marginal cycle-type sets can leave distinct labels unresolved.  A
    # common unramified prime supplies a correlated profile in all sibling
    # actions, which is checked against one source conjugacy class at a time.
    if assignments and len(marginal_remaining) > 1 and use_joint:
        joint_profiles, joint_metadata = joint_census.get(source_label, target_slots)
        for prime in primes:
            joint_primes_examined += 1
            observed = [patterns.pattern(position, prime) for position in range(len(candidates))]
            if any(pattern is None for pattern in observed):
                continue
            before_assignments = assignments
            before_counts = assignment_counts(before_assignments, target_labels)
            kept = []
            for assignment in assignments:
                profile_by_slot: list[CycleType | None] = [None] * len(target_slots)
                for factor_position, slot_position in enumerate(assignment):
                    profile_by_slot[slot_position] = observed[factor_position]
                profile = tuple(profile_by_slot)
                if profile in joint_profiles:
                    kept.append(assignment)
            assignments = kept
            if len(assignments) < len(before_assignments):
                joint_evidence.append(
                    {
                        "prime": prime,
                        "allSiblingPolynomialsSquarefreeModuloPrime": True,
                        "patternsByFactor": [
                            {
                                "factorIndex": int(candidate["factorIndex"]),
                                "coefficientSha256": str(candidate["coefficientSha256"]),
                                "factorDegrees": list(observed[position]),
                            }
                            for position, candidate in enumerate(candidates)
                        ],
                        "before": before_counts,
                        "after": assignment_counts(assignments, target_labels),
                    }
                )
            if not assignments or len(label_assignments(assignments, target_labels)) == 1:
                break

    remaining_labels = label_assignments(assignments, target_labels)
    if not assignments:
        status = "contradiction"
    elif len(remaining_labels) == 1:
        status = "resolved"
    else:
        status = "unresolved"

    result = {
        "status": status,
        "sourceLabel": source_label,
        "sourceR": int(row["sourceR"]),
        "sourceSubmissionId": str(row["sourceSubmissionId"]),
        "sourcePolynomialIndex": int(row["sourcePolynomialIndex"]),
        "targetSlots": [
            {
                "slotPosition": position,
                "orbitIndex": int(slot["orbitIndex"]),
                "targetLabel": str(slot["targetLabel"]),
                "targetT": int(slot["targetT"]),
            }
            for position, slot in enumerate(target_slots)
        ],
        "targetCycleTypeCensus": {
            label: target_census.metadata[label] for label in distinct_labels
        },
        "marginalProof": {
            "primesExamined": marginal_primes_examined,
            "eliminatingObservations": marginal_evidence,
            "remainingLabelAssignments": [list(item) for item in marginal_remaining],
        },
        "jointProof": {
            "enabled": use_joint,
            "used": joint_metadata is not None,
            "primesExamined": joint_primes_examined,
            "census": joint_metadata,
            "eliminatingObservations": joint_evidence,
        },
        "modularFactorizationsComputed": patterns.factorizations,
        "nonsquarefreeReductionsSkipped": patterns.nonsquarefree,
        "remainingSlotAssignmentCount": len(assignments),
        "remainingLabelAssignments": [list(item) for item in remaining_labels],
    }
    if status == "resolved":
        labels = remaining_labels[0]
        result["assignments"] = [
            {
                "factorIndex": int(candidate["factorIndex"]),
                "coefficientSha256": str(candidate["coefficientSha256"]),
                "targetLabel": labels[position],
                "targetT": label_t(labels[position]),
                "targetR": int(candidate["targetR"]),
            }
            for position, candidate in enumerate(candidates)
        ]
    return result


def parse_source_pairs(values: list[list[str]]) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for label_value, r_value in values:
        label = str(label_value)
        label_t(label)
        try:
            r = int(r_value)
        except ValueError as exc:
            raise ValueError(
                f"invalid source real-root count for {label}: {r_value!r}"
            ) from exc
        if r < 0 or r > 24 or r % 2:
            raise ValueError(
                f"source real-root count must be even and between 0 and 24: "
                f"{label}/{r}"
            )
        pairs.add((label, r))
    return pairs


def load_rows(
    path: Path,
    source_labels: set[str],
    source_pairs: set[tuple[str, int]] | None = None,
) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if source_labels:
        rows = [
            row
            for row in rows
            if str(row.get("sourceLabel")) in source_labels
        ]
    if source_pairs:
        rows = [
            row
            for row in rows
            if (str(row.get("sourceLabel")), int(row.get("sourceR", -1)))
            in source_pairs
        ]
    return rows


def selected_rows_digest(rows: list[dict]) -> str:
    payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_json(value: dict, compact: bool) -> str:
    return json.dumps(
        value,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        sort_keys=True,
    ) + "\n"


def write_text_atomic(path: Path, text: str) -> None:
    """Durably replace ``path`` through a temporary file beside it."""
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exactly assign multi-orbit degree-24 factors by Frobenius types."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--source-label",
        action="append",
        default=[],
        help="only certify rows for this source label (repeatable)",
    )
    parser.add_argument(
        "--source-pair",
        action="append",
        nargs=2,
        default=[],
        metavar=("LABEL", "R"),
        help=(
            "only certify this exact source (label,r) pair (repeatable); "
            "when combined with --source-label both filters must match"
        ),
    )
    parser.add_argument(
        "--prime-bound",
        type=int,
        default=5000,
        help="test primes p with 2 <= p < this bound (default: 5000)",
    )
    parser.add_argument(
        "--no-joint",
        action="store_true",
        help="disable the exact joint source-action fallback",
    )
    parser.add_argument(
        "--require-resolved",
        action="store_true",
        help="exit nonzero unless every selected row is uniquely assigned",
    )
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="atomically write the JSON certificate here instead of stdout",
    )
    args = parser.parse_args()

    if args.prime_bound <= 2:
        parser.error("--prime-bound must be greater than 2")
    source_labels = {str(label) for label in args.source_label}
    for label in source_labels:
        label_t(label)
    try:
        source_pairs = parse_source_pairs(args.source_pair)
    except ValueError as exc:
        parser.error(str(exc))
    if (
        args.output is not None
        and args.output.expanduser().resolve() == args.input.expanduser().resolve()
    ):
        parser.error("--output must not overwrite --input")
    rows = load_rows(args.input, source_labels, source_pairs)
    if not rows:
        parser.error("no matching input rows")
    primes = [int(value) for value in prime_range(2, args.prime_bound)]
    if not primes:
        parser.error("prime range is empty")

    target_census = TargetCycleTypes()
    joint_census = JointCycleProfiles()
    certified_rows = []
    for index, row in enumerate(rows, start=1):
        log(
            f"frobenius {index}/{len(rows)}: {row.get('sourceLabel')} "
            f"r={row.get('sourceR')}"
        )
        certified_rows.append(
            discriminate_packet(
                row,
                primes,
                target_census,
                joint_census,
                use_joint=not args.no_joint,
            )
        )

    counts = {
        status: sum(row["status"] == status for row in certified_rows)
        for status in ("resolved", "unresolved", "contradiction")
    }
    result = {
        "method": "exact-unramified-frobenius-cycle-type-exclusion-v1",
        "input": str(args.input.resolve()),
        "inputSha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "selectedInputRowsSha256": selected_rows_digest(rows),
        "selection": {
            "sourceLabels": sorted(source_labels, key=label_t),
            "sourcePairs": [
                {"label": label, "r": r}
                for label, r in sorted(
                    source_pairs,
                    key=lambda item: (label_t(item[0]), item[1]),
                )
            ],
        },
        "software": {
            "sageVersion": SAGE_VERSION,
            "gapVersion": str(libgap.eval("GAPInfo.Version")),
        },
        "primeBoundExclusive": args.prime_bound,
        "primeCount": len(primes),
        "jointFallbackEnabled": not args.no_joint,
        "summary": {"rows": len(certified_rows), **counts},
        "rows": certified_rows,
    }
    rendered = render_json(result, args.compact)
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        write_text_atomic(args.output, rendered)
        print(
            json.dumps(
                {
                    "output": str(args.output.expanduser().resolve()),
                    "summary": result["summary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
    if counts["contradiction"]:
        return 3
    if args.require_resolved and counts["unresolved"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

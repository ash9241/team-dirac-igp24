#!/usr/bin/env python3
"""Pure helpers shared by the F5 multi-orbit planner and Sage worker."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_quotient_line(quotient_line: str) -> str:
    """Identify q(y) with q(-y), whose pair-product resolvents are equal."""
    values = quotient_line.split(",")
    if not values or any(value == "" for value in values):
        raise ValueError("malformed quotient coefficient line")
    reflected = ",".join(
        str(-int(value) if index % 2 else int(value))
        for index, value in enumerate(values)
    )
    direct = ",".join(str(int(value)) for value in values)
    return min(direct, reflected)


def canonical_quotient_sha256(quotient_line: str) -> str:
    return sha256_bytes(canonical_quotient_line(quotient_line).encode("utf-8"))


def quotient_from_even_coefficients(coefficients: str) -> str | None:
    values = coefficients.split(",")
    if (
        len(values) != 25
        or values[-1] != "1"
        or any(int(values[index]) != 0 for index in range(1, 25, 2))
    ):
        return None
    return ",".join(str(int(value)) for value in values[::2])


def profile_key(system: dict, source_order: int) -> tuple:
    block_order = int(system["blockActionOrder"])
    target_order = int(system["targetOrder"])
    if block_order <= 0 or source_order % block_order or target_order % block_order:
        return ()
    return (
        int(system["blockActionT12"]),
        source_order // block_order,
        bool(system["flipInSource"]),
        str(system["targetLabel"]),
        target_order // block_order,
    )


def action_core(row: dict) -> dict:
    """Fields needed to bind an exact cached action, including its block system."""
    return {
        "pairOrbit": row["pairOrbit"],
        "quotientT12": int(row["quotientT12"]),
        "sourceBlockSystem": row["sourceBlockSystem"],
        "sourceLabel": str(row["sourceLabel"]),
        "sourceSignatureToPossibleTargetSignatures": row[
            "sourceSignatureToPossibleTargetSignatures"
        ],
        "sourceT": int(row["sourceT"]),
        "targetKernelOrder": int(row["targetKernelOrder"]),
        "targetLabel": str(row["targetLabel"]),
        "targetOrder": int(row["targetOrder"]),
        "targetT": int(row["targetT"]),
    }


def action_sha256(row: dict) -> str:
    rendered = json.dumps(action_core(row), separators=(",", ":"), sort_keys=True)
    return sha256_bytes(rendered.encode("utf-8"))


def label_assignments(
    slot_assignments: Iterable[Sequence[int]], target_labels: Sequence[str]
) -> set[tuple[str, ...]]:
    return {
        tuple(target_labels[action_position] for action_position in assignment)
        for assignment in slot_assignments
    }


def filter_slot_assignments(
    slot_assignments: Iterable[Sequence[int]],
    observed_factor_patterns: Sequence[tuple[int, ...]],
    allowed_action_profiles: set[tuple[tuple[int, ...], ...]],
) -> list[tuple[int, ...]]:
    """Apply one correlated Frobenius observation to factor->action slots."""
    count = len(observed_factor_patterns)
    kept: list[tuple[int, ...]] = []
    for raw_assignment in slot_assignments:
        assignment = tuple(int(value) for value in raw_assignment)
        if len(assignment) != count or set(assignment) != set(range(count)):
            raise ValueError("slot assignment is not a permutation")
        profile_by_action: list[tuple[int, ...] | None] = [None] * count
        for factor_position, action_position in enumerate(assignment):
            profile_by_action[action_position] = observed_factor_patterns[factor_position]
        profile = tuple(profile_by_action)
        if None in profile:
            raise ValueError("incomplete factor-to-action profile")
        if profile in allowed_action_profiles:
            kept.append(assignment)
    return kept


def dispatch_kind(actions: Sequence[dict]) -> str:
    if len(actions) < 2:
        raise ValueError("multi-orbit dispatch requires at least two actions")
    labels = {str(action["targetLabel"]) for action in actions}
    return "same_target_label_multiset" if len(labels) == 1 else "joint_modular_profiles"


def rank_frontier(rows: Sequence[dict]) -> list[dict]:
    """Proof-risk first, then deterministic coverage and height; no learned scores."""
    remaining = [dict(row) for row in rows]
    selected: list[dict] = []
    covered: set[tuple[str, int]] = set()
    while remaining:
        def key(row: dict) -> tuple:
            pairs = {
                (str(pair["label"]), int(pair["r"]))
                for pair in row["possibleGoldPairs"]
            }
            return (
                0 if row["dispatchKind"] == "same_target_label_multiset" else 1,
                -len(pairs - covered),
                -len(pairs),
                int(row["coefficientHeightBits"]),
                int(str(row["sourceLabel"])[3:]),
                str(row["canonicalQuotientSha256"]),
            )

        chosen = min(remaining, key=key)
        pairs = {
            (str(pair["label"]), int(pair["r"]))
            for pair in chosen["possibleGoldPairs"]
        }
        chosen["newGoldPairsAtSelection"] = len(pairs - covered)
        chosen["selectionRank"] = len(selected) + 1
        selected.append(chosen)
        covered.update(pairs)
        remaining.remove(chosen)
    return selected

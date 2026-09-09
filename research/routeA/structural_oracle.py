#!/usr/bin/env python3
"""Annotate unlabeled candidate shards with exact cycle-index target predictions."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from routeA.ledger import DEFAULT_DB, Ledger, candidate_hash, canonical_coefficients
from routeA.oracle import fingerprint_lines
from routeA.oracle_v2 import CycleIndexOracle
from routeA.scheduler import load_owned_pairs


PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_KNOWLEDGE = Path(__file__).resolve().parent / "knowledge.jsonl"


@dataclass(frozen=True)
class FamilyProfile:
    support: int
    label_counts: Mapping[int, int]

    @property
    def compatible_labels(self) -> tuple[int, ...]:
        return tuple(sorted(self.label_counts))

    @property
    def priors(self) -> dict[int, float]:
        return {
            label: count / self.support
            for label, count in self.label_counts.items()
        }


def normalize_family(value: object) -> str:
    family = str(value or "unknown")
    for prefix in ("empirical_harvest_", "legacy_"):
        if family.startswith(prefix):
            family = family[len(prefix):]
    return family


def learn_family_profiles(
    knowledge_rows: Iterable[Mapping[str, Any]],
    *,
    min_support: int = 20,
) -> dict[str, FamilyProfile]:
    counts: dict[str, Counter[int]] = defaultdict(Counter)
    for row in knowledge_rows:
        dial = row.get("dial")
        if not isinstance(dial, Mapping) or row.get("t") is None:
            continue
        family = normalize_family(dial.get("cls") or dial.get("fam") or dial.get("arm"))
        counts[family][int(row["t"])] += 1
    return {
        family: FamilyProfile(sum(label_counts.values()), dict(label_counts))
        for family, label_counts in counts.items()
        if sum(label_counts.values()) >= min_support
    }


def load_family_profiles(
    path: str | Path = DEFAULT_KNOWLEDGE,
    *,
    min_support: int = 20,
) -> dict[str, FamilyProfile]:
    with Path(path).open(encoding="utf-8") as handle:
        return learn_family_profiles(
            (json.loads(line) for line in handle if line.strip()),
            min_support=min_support,
        )


def open_target_values(
    progress_labels: Iterable[Mapping[str, Any]],
    owned_pairs: set[tuple[int, int]],
) -> dict[tuple[int, int], float]:
    values = {}
    for label in progress_labels:
        target = int(label["t"])
        for signature in label.get("signatures", []):
            pair = (target, int(signature["r"]))
            team_count = int(signature.get("teamCount", 0))
            if (
                bool(signature.get("baseline"))
                or team_count > 1
                or pair in owned_pairs
            ):
                continue
            values[pair] = 2.0 ** (-team_count)
    return values


def annotate_predictions(
    candidates: Sequence[Mapping[str, Any]],
    fingerprints: Sequence[Mapping[str, float] | None],
    oracle: CycleIndexOracle,
    target_values: Mapping[tuple[int, int], float],
    *,
    min_probability: float = 0.5,
    max_prediction_set: int = 3,
    family_profiles: Mapping[str, FamilyProfile] | None = None,
) -> list[dict[str, Any]]:
    annotated = []
    for candidate, fingerprint in zip(candidates, fingerprints):
        if not fingerprint:
            continue
        roots = int(candidate.get("local_root_count", candidate.get("r")))
        family = normalize_family(
            candidate.get("recipe_family") or candidate.get("cls") or candidate.get("fam")
        )
        profile = (family_profiles or {}).get(family)
        exact_compatibility_proven = bool(
            candidate.get("exact_compatibility_proven")
        )
        requested_compatible = candidate.get("compatible_labels")
        if requested_compatible:
            compatible = tuple(int(label) for label in requested_compatible)
            if profile:
                compatible = tuple(
                    label for label in compatible if label in profile.label_counts
                )
        elif profile:
            compatible = profile.compatible_labels
        else:
            # Cycle statistics alone are overconfident on this atlas. A new
            # family needs an exact construction-compatible set before it can
            # produce submission-ready records.
            continue
        observed_counts = {
            pattern: max(1, round(float(frequency) * 200))
            for pattern, frequency in fingerprint.items()
            if frequency > 0
        }
        prediction = oracle.predict(
            observed_counts,
            compatible,
            priors=profile.priors if profile else None,
            prediction_mass=0.95,
        )
        eligible = [
            (probability * target_values[(label, roots)], probability, label)
            for label, probability in prediction.probabilities.items()
            if (label, roots) in target_values
        ]
        if not eligible:
            continue
        _, probability, target = max(eligible)
        if probability < min_probability or len(prediction.prediction_set) > max_prediction_set:
            continue
        record = dict(candidate)
        coefficients = canonical_coefficients(record.get("coefficients") or record["line"])
        family = str(record.get("recipe_family") or record.get("cls") or "structural_exploration")
        lineage = str(record.get("recipe_lineage") or record.get("recipe_id") or family)
        record.update({
            "candidate_hash": candidate_hash(coefficients),
            "coefficients": coefficients,
            "target_t": int(target),
            "target_r": roots,
            "local_root_count": roots,
            "local_irreducible": bool(record.get("local_irreducible", True)),
            "label_probability": float(probability),
            "recipe_family": family,
            "recipe_lineage": lineage,
            "model_version": "cycle-index-248-v1",
            "prediction_set": list(prediction.prediction_set),
            "prediction_entropy": prediction.entropy,
            "predicted_open_value": oracle.expected_pair_value(
                prediction, roots, target_values
            ),
            "family_profile_support": profile.support if profile else 0,
            "family_compatible_labels": (
                list(profile.compatible_labels) if profile else []
            ),
            "exact_compatibility_proven": exact_compatibility_proven,
            "submission_ready": bool(profile) or exact_compatibility_proven,
            "calibration_status": (
                "family_profile"
                if profile
                else "exact_compatibility_proven"
                if exact_compatibility_proven
                else "needs_prospective_calibration"
            ),
        })
        annotated.append(record)
    return annotated


def load_unlabeled_candidates(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest_path = Path(str(path) + ".manifest")
    manifests = []
    if manifest_path.exists():
        manifests = [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    records = []
    for index, line in enumerate(lines):
        metadata = manifests[index] if index < len(manifests) else {}
        roots = metadata.get("r")
        if roots is None:
            raise ValueError(f"candidate {index} has no local root count")
        records.append({
            **metadata,
            "coefficients": line,
            "local_root_count": int(roots),
            "recipe_family": metadata.get("cls") or metadata.get("fam") or "legacy_exploration",
            "recipe_lineage": (
                f"legacy:{metadata.get('cls') or metadata.get('fam') or 'unknown'}:"
                f"{metadata.get('t', metadata.get('a', 'na'))}:"
                f"{metadata.get('e', 'na')}"
            ),
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates")
    parser.add_argument("cycle_indices")
    parser.add_argument("output")
    parser.add_argument(
        "--progress",
        default=str(PROJECT / "daemon" / "data" / "all_progress.json"),
    )
    parser.add_argument("--min-probability", type=float, default=0.5)
    parser.add_argument("--max-prediction-set", type=int, default=3)
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--min-family-support", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    candidates = load_unlabeled_candidates(args.candidates)
    fingerprints = fingerprint_lines(
        [str(candidate.get("coefficients") or candidate["line"]) for candidate in candidates],
        workers=args.workers,
    )
    indices = {
        int(label): index
        for label, index in json.loads(Path(args.cycle_indices).read_text(encoding="utf-8")).items()
    }
    progress = json.loads(Path(args.progress).read_text(encoding="utf-8"))
    with Ledger(args.db) as ledger:
        owned = load_owned_pairs(PROJECT) | ledger.owned_pairs()
    values = open_target_values(progress, owned)
    annotated = annotate_predictions(
        candidates,
        fingerprints,
        CycleIndexOracle(indices),
        values,
        min_probability=args.min_probability,
        max_prediction_set=args.max_prediction_set,
        family_profiles=load_family_profiles(
            args.knowledge,
            min_support=args.min_family_support,
        ),
    )
    Path(args.output).write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in annotated)
        + ("\n" if annotated else ""),
        encoding="utf-8",
    )
    print(json.dumps({
        "candidates": len(candidates),
        "fingerprinted": sum(fingerprint is not None for fingerprint in fingerprints),
        "selected": len(annotated),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

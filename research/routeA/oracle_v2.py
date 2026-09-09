"""Construction-aware cycle-index likelihood oracle.

The model operates only on structurally compatible labels.  It complements,
but does not silently replace, the legacy centroid oracle until prospective
validation data exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class OraclePrediction:
    probabilities: dict[int, float]
    prediction_set: tuple[int, ...]
    entropy: float


class CycleIndexOracle:
    def __init__(
        self,
        cycle_indices: Mapping[int, Mapping[str, float]],
        *,
        smoothing: float = 1e-9,
        temperature: float = 1.5,
    ):
        if smoothing <= 0 or temperature <= 0:
            raise ValueError("smoothing and temperature must be positive")
        self.cycle_indices = {
            int(label): {
                normalize_cycle_pattern(pattern): float(probability)
                for pattern, probability in index.items()
            }
            for label, index in cycle_indices.items()
        }
        self.smoothing = smoothing
        self.temperature = temperature

    def predict(
        self,
        observed_counts: Mapping[str, int | float],
        compatible_labels: Sequence[int],
        priors: Mapping[int, float] | None = None,
        prediction_mass: float = 0.95,
    ) -> OraclePrediction:
        if not compatible_labels:
            return OraclePrediction({}, (), 0.0)
        if not (0 < prediction_mass <= 1):
            raise ValueError("prediction_mass must be in (0, 1]")
        priors = priors or {}
        logits: dict[int, float] = {}
        for label in compatible_labels:
            index = self.cycle_indices.get(int(label))
            if not index:
                continue
            prior = max(float(priors.get(int(label), 1.0)), self.smoothing)
            score = math.log(prior)
            impossible = False
            for pattern, raw_count in observed_counts.items():
                count = float(raw_count)
                probability = index.get(normalize_cycle_pattern(pattern), 0.0)
                if count > 0 and probability <= 0:
                    impossible = True
                    break
                score += count * math.log(max(probability, self.smoothing)) / self.temperature
            if not impossible:
                logits[int(label)] = score
        if not logits:
            return OraclePrediction({}, (), 0.0)
        maximum = max(logits.values())
        weights = {label: math.exp(value - maximum) for label, value in logits.items()}
        total = sum(weights.values())
        probabilities = {label: value / total for label, value in weights.items()}
        entropy = -sum(value * math.log(value) for value in probabilities.values() if value > 0)
        ordered = sorted(probabilities, key=probabilities.get, reverse=True)
        chosen = []
        mass = 0.0
        for label in ordered:
            chosen.append(label)
            mass += probabilities[label]
            if mass >= prediction_mass:
                break
        return OraclePrediction(probabilities, tuple(chosen), entropy)

    def expected_pair_value(
        self,
        prediction: OraclePrediction,
        root_count: int,
        target_values: Mapping[tuple[int, int], float],
    ) -> float:
        return sum(
            probability * float(target_values.get((label, root_count), 0.0))
            for label, probability in prediction.probabilities.items()
        )


def normalize_cycle_pattern(pattern: object) -> str:
    if isinstance(pattern, (list, tuple)):
        return ".".join(str(int(value)) for value in pattern)
    raw = str(pattern).strip().strip("[]")
    parts = raw.replace(".", " ").replace(",", " ").split()
    return ".".join(str(int(value)) for value in parts)

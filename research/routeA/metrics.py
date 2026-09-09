"""Statistical gates for oracle precision and retained-score replacement."""

from __future__ import annotations

import math
import random
from statistics import mean
from typing import Mapping, Sequence


def wilson_lower_bound(successes: int, trials: int, z: float = 1.959963984540054) -> float:
    if trials <= 0:
        return 0.0
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between zero and trials")
    p = successes / trials
    denominator = 1.0 + z * z / trials
    center = p + z * z / (2.0 * trials)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * trials)) / trials)
    return max(0.0, (center - margin) / denominator)


def cluster_bootstrap_lower_bound(
    values_by_cluster: Mapping[str, Sequence[float]],
    *,
    samples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> float:
    clusters = [list(values) for values in values_by_cluster.values() if values]
    if not clusters:
        return 0.0
    if not 0 < alpha < 1 or samples < 1:
        raise ValueError("invalid bootstrap parameters")
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        chosen = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        flat = [value for cluster in chosen for value in cluster]
        estimates.append(mean(flat))
    estimates.sort()
    return estimates[max(0, min(len(estimates) - 1, int(alpha * samples)))]


def replacement_ready(
    targeted_by_cluster: Mapping[str, Sequence[float]],
    volume_points_per_slot: float,
    *,
    multiplier: float = 2.0,
) -> tuple[bool, float]:
    lower = cluster_bootstrap_lower_bound(targeted_by_cluster)
    return lower > multiplier * volume_points_per_slot, lower

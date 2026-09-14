"""Review statistics: stratum-weighted sample accuracy and the Wilson score interval.

Accuracy is computed only from the random stratified sample. Each sampled item carries the weight
stratum_size / stratum_sample_n, so strata sampled at a lower rate count proportionally more. The Wilson 95% interval
uses the weighted accuracy as p and the random sample size as n.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

Z95 = 1.959963984540054


def wilson_interval(p: float, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a proportion p observed on n trials, clamped to [0, 1]."""
    if n <= 0:
        raise ValueError("wilson_interval needs n >= 1")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be within [0, 1]")
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def weighted_accuracy(items: Iterable[tuple[float, bool]]) -> float | None:
    """sum(weight * correct) / sum(weight) over (weight, correct) pairs; None when there is no weight."""
    total = 0.0
    correct = 0.0
    for weight, ok in items:
        total += weight
        if ok:
            correct += weight
    return correct / total if total > 0 else None


def proportional_allocation(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Split a sample of n across strata proportionally to their sizes (largest remainder), at least 1 per stratum.

    A stratum never gets more than its size. When there are more strata than n, every stratum still gets 1 (the
    sample is then larger than n); when the population is at most n, every item is taken.
    """
    strata = {k: v for k, v in sizes.items() if v > 0}
    total = sum(strata.values())
    if not strata:
        return {}
    if total <= n:
        return dict(strata)
    quotas = {k: n * v / total for k, v in strata.items()}
    alloc = {k: min(strata[k], max(1, math.floor(q))) for k, q in quotas.items()}
    order = sorted(strata)
    while sum(alloc.values()) < n:
        growable = [k for k in order if alloc[k] < strata[k]]
        if not growable:
            break
        best = max(growable, key=lambda k: (quotas[k] - alloc[k], -order.index(k)))
        alloc[best] += 1
    while sum(alloc.values()) > n:
        shrinkable = [k for k in order if alloc[k] > 1]
        if not shrinkable:
            break
        worst = min(shrinkable, key=lambda k: (quotas[k] - alloc[k], order.index(k)))
        alloc[worst] -= 1
    return alloc

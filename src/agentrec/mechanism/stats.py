"""Summary statistics for suite rows.

Every comparison is paired on the same unit (family, template, schedule, seed)
and resampled by cluster (family:template), since runs from one template share
structure and are not independent.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

Row = dict[str, Any]

OUTCOMES = (
    "safe_completion", "safe_but_incomplete", "correct_but_unsafe", "unsafe_incomplete",
    "timeout", "infrastructure_error",
)


def unit(row: Row) -> tuple[str, str, str, int]:
    return (row["family"], row["template"], row["schedule"], int(row["seed"]))


def quantile(values: Iterable[float], q: float) -> float | None:
    """Linear-interpolated quantile; None for an empty sample."""
    data = sorted(float(value) for value in values if value is not None)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    position = q * (len(data) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return data[low] + (data[high] - data[low]) * (position - low)


def mean(values: Iterable[float]) -> float | None:
    data = [float(value) for value in values if value is not None]
    return sum(data) / len(data) if data else None


def safe_completion(row: Row) -> float:
    return 1.0 if row.get("outcome") == "safe_completion" else 0.0


def common_clean_population(rows: list[Row], policies: Iterable[str] | None = None) -> set[tuple[str, str, int]]:
    """(family, template, seed) where every compared policy completes safely without a fault.

    CRSR_safe is reported only on this shared population, so no policy is credited
    with recovering a run it could not complete cleanly.
    """
    clean = [row for row in rows if row.get("condition") == "clean"]
    names = set(policies) if policies is not None else {row["policy"] for row in clean}
    by_key: dict[tuple[str, str, int], dict[str, bool]] = defaultdict(dict)
    for row in clean:
        if row["policy"] in names:
            by_key[(row["family"], row["template"], int(row["seed"]))][row["policy"]] = safe_completion(row) == 1.0
    return {key for key, results in by_key.items() if set(results) == names and all(results.values())}


def crsr_safe(rows: list[Row], policies: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """Conditional recovery success rate: safe completions among fault runs on the clean population."""
    names = list(policies) if policies is not None else sorted({row["policy"] for row in rows})
    population = common_clean_population(rows, names)
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        faulty = [
            row for row in rows
            if row["policy"] == name and row.get("condition") == "fault"
            and (row["family"], row["template"], int(row["seed"])) in population
        ]
        out[name] = {
            "n": len(faulty),
            "crsr_safe": mean(safe_completion(row) for row in faulty),
        }
    return out


def _restore_times(row: Row) -> list[float]:
    times = []
    for item in row.get("obligations") or []:
        if item.get("discharged") is not None and item.get("opened") is not None:
            times.append(item["discharged"] - item["opened"])
    return times


def summarize(rows: list[Row]) -> dict[str, dict[str, Any]]:
    """Per-policy summary over fault runs."""
    by_policy: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.get("condition", "fault") == "fault":
            by_policy[row["policy"]].append(row)
    out: dict[str, dict[str, Any]] = {}
    for name, group in sorted(by_policy.items()):
        completed = [row for row in group if row.get("outcome") == "safe_completion"]
        restores = [value for row in group for value in _restore_times(row)]
        preserved = sum(row.get("independent_preserved", 0) for row in group)
        independent = sum(row.get("independent_tasks", 0) for row in group)
        outcomes = Counter(row.get("outcome") for row in group)
        out[name] = {
            "n": len(group),
            "adaptation_of": group[0].get("adaptation_of", ""),
            "outcomes": {key: outcomes.get(key, 0) for key in OUTCOMES},
            "safe_completion_rate": mean(safe_completion(row) for row in group),
            "unsafe_rate": mean(0.0 if row.get("safe", True) else 1.0 for row in group),
            "makespan_p50": quantile((row["makespan"] for row in completed), 0.5),
            "makespan_p95": quantile((row["makespan"] for row in completed), 0.95),
            "restore_p50": quantile(restores, 0.5),
            "restore_p95": quantile(restores, 0.95),
            "verdict_to_restored_p95": quantile(
                (row["verdict_to_restored_max"] for row in group if row.get("verdict_to_restored_max") is not None), 0.95,
            ),
            "tokens_mean": mean(row.get("tokens", 0) for row in group),
            "tokens_after_cancel": sum(row.get("tokens_after_cancel", 0) for row in group),
            "cancelled_requests": sum(row.get("cancelled_requests", 0) for row in group),
            "wasted_model_seconds_mean": mean(row.get("wasted_model_seconds", 0) for row in group),
            "stale_outputs_accepted": sum(row.get("stale_outputs_accepted", 0) for row in group),
            "dispatched_on_dead_support": sum(row.get("dispatched_on_dead_support", 0) for row in group),
            "forbidden_effects": sum(row.get("forbidden_effects", 0) for row in group),
            "duplicate_effects": sum(row.get("duplicate_effects", 0) for row in group),
            "missing_effects": sum(row.get("missing_effects", 0) for row in group),
            "uncertain_effects": sum(row.get("uncertain_effects", 0) for row in group),
            "compensated_effects": sum(row.get("compensated_effects", 0) for row in group),
            "preserved_fraction": preserved / independent if independent else None,
            "repeated_independent": sum(row.get("independent_repeated", 0) for row in group),
            "invalidation_precision": mean(row.get("invalidation_precision") for row in group),
            "invalidation_recall": mean(row.get("invalidation_recall") for row in group),
            "tainted_versions_live": sum(row.get("tainted_versions_live", 0) for row in group),
            "rebases": sum(row.get("rebases", 0) for row in group),
            "lock_wait_s": sum((row.get("lock") or {}).get("wait_s", 0.0) for row in group),
            "lock_max_held_s": max(((row.get("lock") or {}).get("max_held_s", 0.0) for row in group), default=0.0),
            "max_queue_depth": max((row.get("max_queue_depth", 0) for row in group), default=0),
            "visibility": dict(Counter(
                value for row in group for value in (row.get("visibility") or {}).values()
            )),
        }
    return out


def paired(rows: list[Row], a: str, b: str, metric: Callable[[Row], float | None]) -> list[tuple[str, float, float]]:
    """(cluster, metric under a, metric under b) for every unit both policies ran."""
    left: dict[tuple, Row] = {}
    right: dict[tuple, Row] = {}
    for row in rows:
        if row.get("condition", "fault") != "fault":
            continue
        if row["policy"] == a:
            left[unit(row)] = row
        elif row["policy"] == b:
            right[unit(row)] = row
    out = []
    for key in sorted(set(left) & set(right)):
        x = metric(left[key])
        y = metric(right[key])
        if x is None or y is None:
            continue
        out.append((left[key]["cluster"], float(x), float(y)))
    return out


def cluster_bootstrap(
    pairs: list[tuple[str, float, float]],
    *,
    iterations: int = 5000,
    seed: int = 0,
    level: float = 0.95,
) -> dict[str, Any]:
    """Mean paired difference (a - b) with a percentile CI from resampling whole clusters."""
    if not pairs:
        return {"n": 0, "clusters": 0, "diff": None, "low": None, "high": None}
    clusters: dict[str, list[float]] = defaultdict(list)
    for cluster, x, y in pairs:
        clusters[cluster].append(x - y)
    names = sorted(clusters)
    observed = sum(sum(values) for values in clusters.values()) / len(pairs)
    rng = random.Random(seed)
    stats = []
    for _ in range(iterations):
        total = 0.0
        count = 0
        for _cluster in names:
            values = clusters[rng.choice(names)]
            total += sum(values)
            count += len(values)
        stats.append(total / count)
    alpha = (1 - level) / 2
    return {
        "n": len(pairs),
        "clusters": len(names),
        "diff": observed,
        "low": quantile(stats, alpha),
        "high": quantile(stats, 1 - alpha),
    }


def mcnemar_exact(pairs: list[tuple[str, float, float]]) -> dict[str, Any]:
    """Exact two-sided McNemar test on paired binary outcomes."""
    b = sum(1 for _c, x, y in pairs if x >= 0.5 > y)
    c = sum(1 for _c, x, y in pairs if y >= 0.5 > x)
    n = b + c
    if n == 0:
        return {"a_only": b, "b_only": c, "p": 1.0}
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2 ** n
    return {"a_only": b, "b_only": c, "p": min(1.0, 2 * tail)}


def compare(rows: list[Row], a: str, b: str, *, iterations: int = 5000, seed: int = 0) -> dict[str, Any]:
    """Paired comparison of two policies on safe completion, makespan, and tokens."""
    safe_pairs = paired(rows, a, b, safe_completion)
    both_done = paired(
        rows, a, b,
        lambda row: row["makespan"] if row.get("outcome") == "safe_completion" else None,
    )
    tokens = paired(rows, a, b, lambda row: row.get("tokens"))
    unsafe = paired(rows, a, b, lambda row: 0.0 if row.get("safe", True) else 1.0)
    return {
        "a": a,
        "b": b,
        "safe_completion": cluster_bootstrap(safe_pairs, iterations=iterations, seed=seed) | {
            "mcnemar": mcnemar_exact(safe_pairs),
        },
        "unsafe": cluster_bootstrap(unsafe, iterations=iterations, seed=seed) | {"mcnemar": mcnemar_exact(unsafe)},
        "makespan_both_completed": cluster_bootstrap(both_done, iterations=iterations, seed=seed),
        "tokens": cluster_bootstrap(tokens, iterations=iterations, seed=seed),
    }


__all__ = [
    "OUTCOMES", "cluster_bootstrap", "common_clean_population", "compare", "crsr_safe", "mcnemar_exact",
    "paired", "quantile", "summarize", "unit",
]

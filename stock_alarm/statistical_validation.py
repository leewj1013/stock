from __future__ import annotations

import math
from statistics import mean


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations, epsilon, floor = 200, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1 / max(abs(d), floor) * (1 if d >= 0 else -1)
    result = d
    for iteration in range(1, maximum_iterations + 1):
        twice = 2 * iteration
        numerator = iteration * (b - iteration) * x / ((qam + twice) * (a + twice))
        d = 1 + numerator * d
        d = 1 / max(abs(d), floor) * (1 if d >= 0 else -1)
        c = 1 + numerator / c
        c = max(abs(c), floor) * (1 if c >= 0 else -1)
        result *= d * c
        numerator = -(a + iteration) * (qab + iteration) * x / ((a + twice) * (qap + twice))
        d = 1 + numerator * d
        d = 1 / max(abs(d), floor) * (1 if d >= 0 else -1)
        c = 1 + numerator / c
        c = max(abs(c), floor) * (1 if c >= 0 else -1)
        delta = d * c
        result *= delta
        if abs(delta - 1) < epsilon:
            break
    return result


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Dependency-free regularized incomplete beta used by Student's t CDF."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1 - front * _beta_continued_fraction(b, a, 1 - x) / b


def student_t_p_value(t_statistic: float, degrees_of_freedom: int, alternative: str = "two-sided") -> float:
    if degrees_of_freedom <= 0:
        return 1.0
    if math.isinf(t_statistic):
        if alternative == "two-sided":
            return 0.0
        return 0.0 if (alternative == "greater" and t_statistic > 0) or (alternative == "less" and t_statistic < 0) else 1.0
    x = degrees_of_freedom / (degrees_of_freedom + t_statistic * t_statistic)
    tail_twice = regularized_incomplete_beta(x, degrees_of_freedom / 2, 0.5)
    if alternative == "two-sided":
        return min(1.0, tail_twice)
    upper_tail = tail_twice / 2 if t_statistic >= 0 else 1 - tail_twice / 2
    if alternative == "greater":
        return min(1.0, max(0.0, upper_tail))
    if alternative == "less":
        return min(1.0, max(0.0, 1 - upper_tail))
    raise ValueError("alternative must be two-sided, greater, or less")


def newey_west_mean_test(values: list[float], lag: int, alternative: str = "two-sided") -> dict:
    """Test whether a time-series mean differs from zero using Bartlett HAC.

    For overlapping h-session forward returns, observations can share h-1
    sessions.  The caller therefore uses lag=h-1 (bounded by the configured
    maximum). Variant daily return differences use their own configured lag.
    """
    series = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    count = len(series)
    if count < 2:
        return {"sample_count": count, "mean": mean(series) if series else None, "lag": 0,
                "standard_error": None, "t_statistic": None, "p_value": 1.0}
    lag = max(0, min(int(lag), count - 1))
    average = mean(series)
    centered = [value - average for value in series]
    gamma_zero = sum(value * value for value in centered) / count
    long_run_variance = gamma_zero
    for offset in range(1, lag + 1):
        covariance = sum(centered[index] * centered[index - offset] for index in range(offset, count)) / count
        bartlett_weight = 1 - offset / (lag + 1)
        long_run_variance += 2 * bartlett_weight * covariance
    variance_of_mean = max(0.0, long_run_variance / count)
    standard_error = math.sqrt(variance_of_mean)
    if standard_error == 0:
        statistic = math.copysign(math.inf, average) if average else 0.0
    else:
        statistic = average / standard_error
    return {
        "sample_count": count, "mean": average, "lag": lag, "standard_error": standard_error,
        "t_statistic": statistic, "p_value": student_t_p_value(statistic, count - 1, alternative),
    }


def benjamini_hochberg(p_values: list[float | None]) -> list[float | None]:
    """Return order-preserving Benjamini-Hochberg adjusted p-values."""
    valid = [(index, float(value)) for index, value in enumerate(p_values) if value is not None and math.isfinite(float(value))]
    adjusted: list[float | None] = [None] * len(p_values)
    if not valid:
        return adjusted
    ordered = sorted(valid, key=lambda item: item[1])
    total = len(ordered)
    running = 1.0
    for rank_index in range(total - 1, -1, -1):
        original_index, p_value = ordered[rank_index]
        rank = rank_index + 1
        running = min(running, p_value * total / rank)
        adjusted[original_index] = min(1.0, max(0.0, running))
    return adjusted

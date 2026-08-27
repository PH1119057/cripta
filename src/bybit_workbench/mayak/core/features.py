from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: float
    close: float
    volume: float


def simple_return(start: float, end: float) -> float:
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return end / start - 1.0


def realised_volatility(prices: Sequence[float]) -> float:
    returns = [simple_return(left, right) for left, right in zip(prices, prices[1:], strict=False)]
    return statistics.pstdev(returns) if len(returns) >= 2 else 0.0


def velocity(returns: Sequence[float]) -> float:
    return statistics.fmean(returns) if returns else 0.0


def acceleration(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    midpoint = len(returns) // 2
    older = returns[:midpoint]
    newer = returns[midpoint:]
    return velocity(newer) - velocity(older)


def normalized_displacement(displacement: float, recent_returns: Sequence[float]) -> float:
    scale = realised_volatility(_prices_from_returns(recent_returns))
    return displacement / scale if scale > 0 else 0.0


def breadth(
    returns: Mapping[str, float], *, excluded_symbol: str | None = None
) -> dict[str, float]:
    values = [value for symbol, value in returns.items() if symbol != excluded_symbol]
    if not values:
        raise ValueError("breadth requires at least one included symbol")
    total = len(values)
    return {
        "up_share": sum(value > 0 for value in values) / total,
        "down_share": sum(value < 0 for value in values) / total,
        "abs_gt_0p25_share": sum(abs(value) > 0.0025 for value in values) / total,
        "abs_gt_0p50_share": sum(abs(value) > 0.005 for value in values) / total,
        "abs_gt_1p00_share": sum(abs(value) > 0.010 for value in values) / total,
        "median_return": statistics.median(values),
        "dispersion": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def directional_agreement(returns: Mapping[str, float]) -> float:
    if not returns:
        raise ValueError("agreement requires returns")
    positives = sum(value > 0 for value in returns.values())
    negatives = sum(value < 0 for value in returns.values())
    return max(positives, negatives) / len(returns)


def pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    denominator = left_scale * right_scale
    return numerator / denominator if denominator else 0.0


def synchronization(return_series: Mapping[str, Sequence[float]]) -> float:
    symbols = tuple(return_series)
    correlations = [
        pearson(return_series[left], return_series[right])
        for index, left in enumerate(symbols)
        for right in symbols[index + 1 :]
    ]
    return statistics.fmean(correlations) if correlations else 0.0


def agreement_persistence(agreements: Sequence[float], threshold: float) -> float:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between zero and one")
    count = 0
    for value in reversed(agreements):
        if value < threshold:
            break
        count += 1
    return float(count)


def _prices_from_returns(returns: Sequence[float]) -> list[float]:
    prices = [1.0]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    return prices

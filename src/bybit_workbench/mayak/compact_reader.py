from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

FLOW_HORIZONS = ("1m", "5m", "15m", "30m", "60m")
FLOW_FIELDS = (
    "buy_usd",
    "sell_usd",
    "net_usd",
    "turnover_usd",
    "net_share",
    "speed_usd_per_min",
    "acceleration_usd_per_min2",
    "large_buy_usd",
    "large_sell_usd",
    "large_trade_share",
    "prior_turnover_usd",
    "turnover_ratio_to_prior",
    "return_pct",
    "status",
)
BOOK_FIELDS = (
    "best_bid",
    "best_ask",
    "mid_price",
    "bid_usd",
    "ask_usd",
    "imbalance",
    "bid_depth_5bps_usd",
    "ask_depth_5bps_usd",
    "bid_depth_10bps_usd",
    "ask_depth_10bps_usd",
    "bid_depth_25bps_usd",
    "ask_depth_25bps_usd",
    "bid_depth_50bps_usd",
    "ask_depth_50bps_usd",
    "bid_change_pct",
    "ask_change_pct",
    "bid_change_1m_pct",
    "ask_change_1m_pct",
    "imbalance_change_1m",
    "bid_change_5m_pct",
    "ask_change_5m_pct",
    "imbalance_change_5m",
    "bid_change_15m_pct",
    "ask_change_15m_pct",
    "imbalance_change_15m",
)
POSITIONING_FIELDS = (
    "open_interest",
    "open_interest_value",
    "open_interest_change_5m_pct",
    "open_interest_change_15m_pct",
    "open_interest_change_30m_pct",
    "open_interest_change_60m_pct",
    "open_interest_speed_5m_pct_per_min",
    "open_interest_acceleration_5m_pct_per_min2",
    "funding_rate",
    "funding_rate_change_from_previous",
    "last_price",
    "mark_price",
    "index_price",
    "mark_index_premium_pct",
    "last_index_premium_pct",
    "last_mark_premium_pct",
    "long_ratio",
    "short_ratio",
)
RELATIVE_HORIZONS = ("1m", "5m", "15m", "60m")
RELATIVE_FIELDS = (
    "coin_return_pct",
    "panel_median_return_pct",
    "relative_to_panel_pct",
    "relative_to_btc_pct",
    "relative_to_eth_pct",
)

R8B_REQUIRED_ANALYTICAL_PATHS = frozenset(
    {f"price.returns.{horizon}" for horizon in FLOW_HORIZONS}
    | {
        f"money.{market}.{horizon}.{field}"
        for market in ("spot", "derivatives")
        for horizon in FLOW_HORIZONS
        for field in FLOW_FIELDS
    }
    | {
        f"liquidity.{market}.{field}"
        for market in ("spot", "derivatives")
        for field in BOOK_FIELDS
    }
    | {f"positioning.{field}" for field in POSITIONING_FIELDS}
    | {
        f"relative_strength.{horizon}.{field}"
        for horizon in RELATIVE_HORIZONS
        for field in RELATIVE_FIELDS
    }
)

R8B_DIRECT_PATHS = frozenset(
    {"price.returns.5m"}
    | {
        f"money.{market}.5m.{field}"
        for market in ("spot", "derivatives")
        for field in (
            "buy_usd",
            "sell_usd",
            "net_usd",
            "turnover_usd",
            "large_buy_usd",
            "large_sell_usd",
            "return_pct",
        )
    }
    | {
        f"liquidity.{market}.{field}"
        for market in ("spot", "derivatives")
        for field in (
            "mid_price",
            "bid_usd",
            "ask_usd",
            "bid_change_pct",
            "ask_change_pct",
        )
    }
    | {
        f"positioning.{field}"
        for field in (
            "open_interest",
            "open_interest_change_5m_pct",
            "funding_rate",
            "last_price",
            "mark_price",
            "index_price",
            "long_ratio",
            "short_ratio",
        )
    }
)

R8B_RECOMPUTABLE_PATHS = frozenset(
    {
        f"money.{market}.5m.{field}"
        for market in ("spot", "derivatives")
        for field in ("net_share", "speed_usd_per_min", "large_trade_share", "status")
    }
    | {f"liquidity.{market}.imbalance" for market in ("spot", "derivatives")}
    | {
        "positioning.mark_index_premium_pct",
        "positioning.last_index_premium_pct",
        "positioning.last_mark_premium_pct",
    }
    | {
        f"relative_strength.5m.{field}"
        for field in RELATIVE_FIELDS
    }
)

R8B_EXACT_EVENT_FAMILIES = frozenset({"liquidations.*"})
R8B_UNCOVERED_REQUIRED_PATHS = frozenset(
    R8B_REQUIRED_ANALYTICAL_PATHS - R8B_DIRECT_PATHS - R8B_RECOMPUTABLE_PATHS
)


@dataclass(frozen=True, slots=True)
class CompactParityReport:
    checked_paths: tuple[str, ...]
    mismatches: tuple[str, ...]
    uncovered_paths: tuple[str, ...]

    @property
    def replacement_ready(self) -> bool:
        return not self.mismatches and not self.uncovered_paths


def compact_replacement_ready() -> bool:
    return not R8B_UNCOVERED_REQUIRED_PATHS


def compact_pair_projection(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    spot, linear = _validated_pair(rows)
    result: dict[str, Any] = {}

    _put(result, "price.returns.5m", linear.get("return_5m_pct"))
    for market_name, row in (("spot", spot), ("derivatives", linear)):
        for field in (
            "buy_usd",
            "sell_usd",
            "net_usd",
            "turnover_usd",
            "large_buy_usd",
            "large_sell_usd",
        ):
            _put(result, f"money.{market_name}.5m.{field}", row.get(field))
        _put(result, f"money.{market_name}.5m.return_pct", row.get("return_5m_pct"))
        net = _number(row.get("net_usd"))
        turnover = _number(row.get("turnover_usd"))
        large_buy = _number(row.get("large_buy_usd"))
        large_sell = _number(row.get("large_sell_usd"))
        _put(
            result,
            f"money.{market_name}.5m.net_share",
            _safe_ratio(net, turnover),
        )
        _put(
            result,
            f"money.{market_name}.5m.speed_usd_per_min",
            net / 5 if net is not None else None,
        )
        large_total = (
            (large_buy or 0.0) + (large_sell or 0.0)
            if turnover is not None
            else None
        )
        _put(
            result,
            f"money.{market_name}.5m.large_trade_share",
            _safe_ratio(large_total, turnover),
        )
        _put(
            result,
            f"money.{market_name}.5m.status",
            "VALID" if net is not None else "NO_DATA",
        )

        bid = _number(row.get("bid_usd"))
        ask = _number(row.get("ask_usd"))
        for field in (
            "mid_price",
            "bid_usd",
            "ask_usd",
            "bid_change_pct",
            "ask_change_pct",
        ):
            _put(result, f"liquidity.{market_name}.{field}", row.get(field))
        _put(
            result,
            f"liquidity.{market_name}.imbalance",
            (bid - ask) / (bid + ask)
            if bid is not None and ask is not None and bid + ask
            else None,
        )

    positioning_map = {
        "open_interest": "open_interest",
        "open_interest_change_5m_pct": "open_interest_change_pct",
        "funding_rate": "funding_rate",
        "last_price": "last_price",
        "mark_price": "mark_price",
        "index_price": "index_price",
        "long_ratio": "long_ratio",
        "short_ratio": "short_ratio",
    }
    for path_field, row_field in positioning_map.items():
        _put(result, f"positioning.{path_field}", linear.get(row_field))
    _put(
        result,
        "positioning.mark_index_premium_pct",
        _pct(linear.get("mark_price"), linear.get("index_price")),
    )
    _put(
        result,
        "positioning.last_index_premium_pct",
        _pct(linear.get("last_price"), linear.get("index_price")),
    )
    _put(
        result,
        "positioning.last_mark_premium_pct",
        _pct(linear.get("last_price"), linear.get("mark_price")),
    )
    return result


def compare_compact_pair_to_context(
    rows: Sequence[Mapping[str, Any]],
    coin_payload: Mapping[str, Any],
) -> CompactParityReport:
    projection = compact_pair_projection(rows)
    flattened = _flatten(projection)
    mismatches = tuple(
        path
        for path, actual in sorted(flattened.items())
        if not _equivalent(actual, _get_path(coin_payload, path))
    )
    return CompactParityReport(
        checked_paths=tuple(sorted(flattened)),
        mismatches=mismatches,
        uncovered_paths=tuple(sorted(R8B_UNCOVERED_REQUIRED_PATHS)),
    )


def _validated_pair(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if len(rows) != 2:
        raise ValueError("compact parity requires exactly one SPOT and one LINEAR row")
    by_product = {str(row.get("product_category")): row for row in rows}
    if set(by_product) != {"SPOT", "LINEAR"}:
        raise ValueError("compact parity requires SPOT + LINEAR product pair")
    spot, linear = by_product["SPOT"], by_product["LINEAR"]
    for field in (
        "market_source_id",
        "external_symbol",
        "observed_minute",
        "source_observed_at",
        "config_fingerprint",
        "engine_version",
        "feature_version",
    ):
        if spot.get(field) != linear.get(field):
            raise ValueError(f"compact pair provenance mismatch: {field}")
    return spot, linear


def _put(target: dict[str, Any], path: str, value: Any) -> None:
    cursor = target
    parts = path.split(".")
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise TypeError(f"path collision: {path}")
        cursor = child
    cursor[parts[-1]] = value


def _flatten(source: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in source.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            result.update(_flatten(value, path))
        else:
            result[path] = value
    return result


def _get_path(source: Mapping[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _Missing
        current = current[part]
    return current


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _pct(a: Any, b: Any) -> float | None:
    if a is None or b in (None, 0):
        return None
    return (float(a) / float(b) - 1) * 100


def _equivalent(left: Any, right: Any) -> bool:
    if left is _Missing or right is _Missing:
        other = right if left is _Missing else left
        return other is None or other is _Missing
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


class _MissingType:
    pass


_Missing = _MissingType()

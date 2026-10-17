from __future__ import annotations

import math
import statistics
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
    | {f"relative_strength.5m.{field}" for field in RELATIVE_FIELDS}
)

R8B_EXACT_EVENT_FAMILIES = frozenset({"liquidations.*"})
R8B_UNCOVERED_REQUIRED_PATHS = frozenset(
    R8B_REQUIRED_ANALYTICAL_PATHS - R8B_DIRECT_PATHS - R8B_RECOMPUTABLE_PATHS
)

_FLOW_RECOMPUTABLE_FIELDS = frozenset(
    {
        "net_share",
        "speed_usd_per_min",
        "large_trade_share",
        "turnover_ratio_to_prior",
        "status",
    }
)
_POSITIONING_RECOMPUTABLE_FIELDS = frozenset(
    {
        "mark_index_premium_pct",
        "last_index_premium_pct",
        "last_mark_premium_pct",
    }
)

R8C_REQUIRED_ANALYTICAL_PATHS = R8B_REQUIRED_ANALYTICAL_PATHS
R8C_RECOMPUTABLE_PATHS = frozenset(
    {
        f"money.{market}.{horizon}.{field}"
        for market in ("spot", "derivatives")
        for horizon in FLOW_HORIZONS
        for field in _FLOW_RECOMPUTABLE_FIELDS
    }
    | {f"liquidity.{market}.imbalance" for market in ("spot", "derivatives")}
    | {f"positioning.{field}" for field in _POSITIONING_RECOMPUTABLE_FIELDS}
    | {
        f"relative_strength.{horizon}.{field}"
        for horizon in RELATIVE_HORIZONS
        for field in RELATIVE_FIELDS
    }
)
R8C_DIRECT_PATHS = frozenset(
    R8C_REQUIRED_ANALYTICAL_PATHS - R8C_RECOMPUTABLE_PATHS
)
R8C_EXACT_EVENT_FAMILIES = frozenset({"liquidations.*"})
R8C_UNCOVERED_REQUIRED_PATHS = frozenset(
    R8C_REQUIRED_ANALYTICAL_PATHS - R8C_DIRECT_PATHS - R8C_RECOMPUTABLE_PATHS
)


@dataclass(frozen=True, slots=True)
class CompactParityReport:
    checked_paths: tuple[str, ...]
    mismatches: tuple[str, ...]
    uncovered_paths: tuple[str, ...]

    @property
    def replacement_ready(self) -> bool:
        return not self.mismatches and not self.uncovered_paths


@dataclass(frozen=True, slots=True)
class CompactPanelParityReport:
    checked_symbols: tuple[str, ...]
    checked_paths: int
    mismatches: tuple[str, ...]
    uncovered_paths: tuple[str, ...]

    @property
    def replacement_ready(self) -> bool:
        return not self.mismatches and not self.uncovered_paths


def compact_replacement_ready() -> bool:
    return not R8C_UNCOVERED_REQUIRED_PATHS


def compact_pair_projection(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    spot, linear = _validated_pair(rows)
    result: dict[str, Any] = {}

    for horizon in FLOW_HORIZONS:
        _put(
            result,
            f"price.returns.{horizon}",
            _flow_direct_value(linear, horizon, "return_pct"),
        )

    for market_name, row in (("spot", spot), ("derivatives", linear)):
        for horizon in FLOW_HORIZONS:
            minutes = int(horizon.removesuffix("m"))
            direct = {
                field: _flow_direct_value(row, horizon, field)
                for field in (
                    "buy_usd",
                    "sell_usd",
                    "net_usd",
                    "turnover_usd",
                    "acceleration_usd_per_min2",
                    "large_buy_usd",
                    "large_sell_usd",
                    "prior_turnover_usd",
                    "return_pct",
                )
            }
            for field, value in direct.items():
                _put(result, f"money.{market_name}.{horizon}.{field}", value)

            net = _number(direct["net_usd"])
            turnover = _number(direct["turnover_usd"])
            large_buy = _number(direct["large_buy_usd"])
            large_sell = _number(direct["large_sell_usd"])
            prior_turnover = _number(direct["prior_turnover_usd"])
            large_total = (
                (large_buy or 0.0) + (large_sell or 0.0)
                if turnover is not None
                else None
            )
            derived = {
                "net_share": _safe_ratio(net, turnover),
                "speed_usd_per_min": (
                    net / minutes if net is not None else None
                ),
                "large_trade_share": _safe_ratio(large_total, turnover),
                "turnover_ratio_to_prior": _safe_ratio(turnover, prior_turnover),
                "status": "VALID" if net is not None else "NO_DATA",
            }
            for field, value in derived.items():
                _put(result, f"money.{market_name}.{horizon}.{field}", value)

        bid = _number(row.get("bid_usd"))
        ask = _number(row.get("ask_usd"))
        for field in BOOK_FIELDS:
            if field == "imbalance":
                value = (
                    (bid - ask) / (bid + ask)
                    if bid is not None and ask is not None and bid + ask
                    else None
                )
            else:
                value = row.get(field)
            _put(result, f"liquidity.{market_name}.{field}", value)

    positioning_map = {
        "open_interest": "open_interest",
        "open_interest_value": "open_interest_value",
        "open_interest_change_5m_pct": "open_interest_change_pct",
        "open_interest_change_15m_pct": "open_interest_change_15m_pct",
        "open_interest_change_30m_pct": "open_interest_change_30m_pct",
        "open_interest_change_60m_pct": "open_interest_change_60m_pct",
        "open_interest_speed_5m_pct_per_min": "open_interest_speed_5m_pct_per_min",
        "open_interest_acceleration_5m_pct_per_min2": (
            "open_interest_acceleration_5m_pct_per_min2"
        ),
        "funding_rate": "funding_rate",
        "funding_rate_change_from_previous": "funding_rate_change_from_previous",
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


def compact_panel_projection(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    pairs = _validated_panel(rows)
    result = {
        symbol: compact_pair_projection(pair)
        for symbol, pair in sorted(pairs.items())
    }

    for horizon in RELATIVE_HORIZONS:
        values = [
            _number(_get_path(payload, f"price.returns.{horizon}"))
            for payload in result.values()
        ]
        present = [value for value in values if value is not None]
        panel = statistics.median(present) if present else None
        btc = _number(
            _get_path(result.get("BTCUSDT", {}), f"price.returns.{horizon}")
        )
        eth = _number(
            _get_path(result.get("ETHUSDT", {}), f"price.returns.{horizon}")
        )
        for payload in result.values():
            value = _number(_get_path(payload, f"price.returns.{horizon}"))
            relative = {
                "coin_return_pct": value,
                "panel_median_return_pct": panel,
                "relative_to_panel_pct": (
                    value - panel if value is not None and panel is not None else None
                ),
                "relative_to_btc_pct": (
                    value - btc if value is not None and btc is not None else None
                ),
                "relative_to_eth_pct": (
                    value - eth if value is not None and eth is not None else None
                ),
            }
            for field, field_value in relative.items():
                _put(
                    payload,
                    f"relative_strength.{horizon}.{field}",
                    field_value,
                )
    return result


def compare_compact_pair_to_context(
    rows: Sequence[Mapping[str, Any]],
    coin_payload: Mapping[str, Any],
) -> CompactParityReport:
    projection = compact_pair_projection(rows)
    flattened = _flatten(projection)
    relative_paths = frozenset(
        path
        for path in R8C_REQUIRED_ANALYTICAL_PATHS
        if path.startswith("relative_strength.")
    )
    mismatches = tuple(
        path
        for path, actual in sorted(flattened.items())
        if path not in relative_paths
        and not _equivalent(actual, _get_path(coin_payload, path))
    )
    return CompactParityReport(
        checked_paths=tuple(
            sorted(path for path in flattened if path not in relative_paths)
        ),
        mismatches=mismatches,
        uncovered_paths=tuple(sorted(relative_paths)),
    )


def compare_compact_panel_to_contexts(
    rows: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Any],
) -> CompactPanelParityReport:
    projection = compact_panel_projection(rows)
    mismatches: list[str] = []
    checked_paths = 0
    for symbol, compact_payload in sorted(projection.items()):
        context = contexts.get(symbol)
        if not isinstance(context, Mapping):
            mismatches.append(f"{symbol}:MISSING_CONTEXT")
            continue
        live_payload = context.get("payload", context)
        if not isinstance(live_payload, Mapping):
            mismatches.append(f"{symbol}:INVALID_CONTEXT")
            continue
        flattened = _flatten(compact_payload)
        for path in sorted(R8C_REQUIRED_ANALYTICAL_PATHS):
            checked_paths += 1
            if not _equivalent(
                flattened.get(path, _Missing),
                _get_path(live_payload, path),
            ):
                mismatches.append(f"{symbol}:{path}")
    extra_symbols = sorted(set(contexts) - set(projection))
    mismatches.extend(f"{symbol}:MISSING_COMPACT_ROWS" for symbol in extra_symbols)
    return CompactPanelParityReport(
        checked_symbols=tuple(sorted(projection)),
        checked_paths=checked_paths,
        mismatches=tuple(mismatches),
        uncovered_paths=tuple(sorted(R8C_UNCOVERED_REQUIRED_PATHS)),
    )


def _flow_direct_value(
    row: Mapping[str, Any],
    horizon: str,
    field: str,
) -> Any:
    if horizon == "5m":
        mapping = {
            "return_pct": "return_5m_pct",
            "buy_usd": "buy_usd",
            "sell_usd": "sell_usd",
            "net_usd": "net_usd",
            "turnover_usd": "turnover_usd",
            "large_buy_usd": "large_buy_usd",
            "large_sell_usd": "large_sell_usd",
            "prior_turnover_usd": "flow_5m_prior_turnover_usd",
            "acceleration_usd_per_min2": "flow_5m_acceleration_usd_per_min2",
        }
        return row.get(mapping[field])
    return row.get(f"flow_{horizon}_{field}")


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
        "schema_version",
    ):
        if spot.get(field) != linear.get(field):
            raise ValueError(f"compact pair provenance mismatch: {field}")
    return spot, linear


def _validated_panel(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    if not rows:
        return {}
    market_source = rows[0].get("market_source_id")
    observed_minute = rows[0].get("observed_minute")
    by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("market_source_id") != market_source:
            raise ValueError("compact panel mixes MarketSource identities")
        if row.get("observed_minute") != observed_minute:
            raise ValueError("compact panel mixes observed minutes")
        symbol = str(row.get("external_symbol") or "")
        if not symbol:
            raise ValueError("compact panel row missing external_symbol")
        by_symbol.setdefault(symbol, []).append(row)
    return {
        symbol: _validated_pair(symbol_rows)
        for symbol, symbol_rows in by_symbol.items()
    }


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
    if value is None or value is _Missing:
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

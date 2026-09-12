from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

from .contracts import (
    CandidateCooldown,
    ContextFailureAction,
    ContextMode,
    ContextRequirement,
    CooldownScope,
    DataQuality,
    FrozenPolicy,
    NumericRule,
    SensorRequirement,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)
from .storage import StrategyEntryStore


class DashboardCursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


class DashboardConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> DashboardCursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class StaleActivationState(RuntimeError):
    """The browser attempted to overwrite a newer StrategyActivation state."""


class UnknownActivation(KeyError):
    """Exact activation_id does not exist."""


STRATEGY_CONTEXT_FEATURE_CATALOG: tuple[dict[str, str], ...] = (
    # Dispatcher V2.1 GlobalMarketContext: 19 objective features actually persisted now.
    {"feature_id": "btc.state", "scope": "GLOBAL", "label": "BTC: состояние"},
    {"feature_id": "eth.state", "scope": "GLOBAL", "label": "ETH: состояние"},
    {"feature_id": "event.context", "scope": "GLOBAL", "label": "Событийный контекст"},
    {"feature_id": "market.breadth", "scope": "GLOBAL", "label": "Ширина рынка"},
    {"feature_id": "money.pressure", "scope": "GLOBAL", "label": "Общее денежное давление"},
    {"feature_id": "liquidity.trend", "scope": "GLOBAL", "label": "Тренд ликвидности"},
    {"feature_id": "event.importance", "scope": "GLOBAL", "label": "Важность события"},
    {"feature_id": "market.direction", "scope": "GLOBAL", "label": "Направление рынка"},
    {"feature_id": "liquidation.phase", "scope": "GLOBAL", "label": "Фаза ликвидаций"},
    {"feature_id": "liquidation.breadth", "scope": "GLOBAL", "label": "Ширина ликвидаций"},
    {"feature_id": "money.spot_pressure", "scope": "GLOBAL", "label": "Давление спотовых денег"},
    {"feature_id": "liquidation.intensity", "scope": "GLOBAL", "label": "Интенсивность ликвидаций"},
    {"feature_id": "positioning.oi_regime", "scope": "GLOBAL", "label": "Режим открытого интереса"},
    {"feature_id": "market.synchronization", "scope": "GLOBAL", "label": "Синхронность рынка"},
    {"feature_id": "liquidation.acceleration", "scope": "GLOBAL", "label": "Ускорение ликвидаций"},
    {
        "feature_id": "market.timeframe_alignment",
        "scope": "GLOBAL",
        "label": "Согласованность таймфреймов",
    },
    {
        "feature_id": "money.derivatives_pressure",
        "scope": "GLOBAL",
        "label": "Давление фьючерсных денег",
    },
    {
        "feature_id": "positioning.price_oi_state",
        "scope": "GLOBAL",
        "label": "Цена + открытый интерес",
    },
    {
        "feature_id": "money.spot_derivatives_alignment",
        "scope": "GLOBAL",
        "label": "Согласованность spot / derivatives",
    },
    # CoinMarketContext: 15 meaningful groups over the current persisted objective payload.
    {"feature_id": "money.spot", "scope": "COIN", "label": "Монета: спотовый денежный поток"},
    {
        "feature_id": "money.derivatives",
        "scope": "COIN",
        "label": "Монета: фьючерсный денежный поток",
    },
    {
        "feature_id": "money.flow_dynamics",
        "scope": "COIN",
        "label": "Монета: скорость / ускорение денег",
    },
    {"feature_id": "money.large_trades", "scope": "COIN", "label": "Монета: крупные сделки"},
    {"feature_id": "price.returns", "scope": "COIN", "label": "Монета: движение цены 1/5/15/60м"},
    {
        "feature_id": "liquidity.spot",
        "scope": "COIN",
        "label": "Монета: спотовая ликвидность / стакан",
    },
    {
        "feature_id": "liquidity.derivatives",
        "scope": "COIN",
        "label": "Монета: фьючерсная ликвидность / стакан",
    },
    {
        "feature_id": "positioning.open_interest",
        "scope": "COIN",
        "label": "Монета: открытый интерес",
    },
    {"feature_id": "positioning.funding", "scope": "COIN", "label": "Монета: funding"},
    {
        "feature_id": "positioning.long_short",
        "scope": "COIN",
        "label": "Монета: long / short positioning",
    },
    {
        "feature_id": "positioning.mark_index_premium",
        "scope": "COIN",
        "label": "Монета: mark/index premium",
    },
    {"feature_id": "liquidations", "scope": "COIN", "label": "Монета: ликвидации"},
    {"feature_id": "relative_strength", "scope": "COIN", "label": "Монета: относительная сила"},
    {"feature_id": "event_context", "scope": "COIN", "label": "Монета: событийный контекст"},
    {
        "feature_id": "data_quality",
        "scope": "COIN",
        "label": "Монета: качество / полнота источников",
    },
)

_CONTEXT_FEATURE_IDS = frozenset(item["feature_id"] for item in STRATEGY_CONTEXT_FEATURE_CATALOG)
_CONTEXT_MODES = frozenset(mode.value for mode in ContextMode)
_CONTEXT_FAILURE_ACTIONS = frozenset(action.value for action in ContextFailureAction)
_CONTEXT_QUALITIES = frozenset(item.value for item in DataQuality)


def strategy_context_feature_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in STRATEGY_CONTEXT_FEATURE_CATALOG]


def _validate_feature_policy(value: object, label: str) -> None:
    if value is None:
        return
    rows = _list(value, label)
    seen: set[str] = set()
    for index, item in enumerate(rows):
        raw = _mapping(item, f"{label}[{index}]")
        feature_id = str(raw.get("feature_id") or "")
        if feature_id not in _CONTEXT_FEATURE_IDS:
            raise ValueError(f"{label}[{index}] unknown feature_id: {feature_id}")
        if feature_id in seen:
            raise ValueError(f"{label} duplicate feature_id: {feature_id}")
        seen.add(feature_id)
        mode = str(raw.get("mode") or "")
        if mode not in _CONTEXT_MODES:
            raise ValueError(f"{label}[{index}] invalid mode: {mode}")
        if mode in {ContextMode.CONDITION.value, ContextMode.RANKING.value}:
            max_age = raw.get("max_age_seconds")
            try:
                max_age_value = int(str(max_age))
            except (TypeError, ValueError):
                raise ValueError(
                    f"{label}[{index}] decision mode requires max_age_seconds"
                ) from None
            if max_age_value <= 0:
                raise ValueError(f"{label}[{index}] max_age_seconds must be positive")
            quality = str(raw.get("min_quality") or "")
            if quality not in _CONTEXT_QUALITIES:
                raise ValueError(f"{label}[{index}] decision mode requires min_quality")
            for field in ("on_missing", "on_stale", "on_partial"):
                action = str(raw.get(field) or "")
                if action not in _CONTEXT_FAILURE_ACTIONS:
                    raise ValueError(f"{label}[{index}] decision mode requires {field}")
        if mode == ContextMode.CONDITION.value:
            condition = _mapping(raw.get("condition"), f"{label}[{index}].condition")
            if str(condition.get("operator") or "") not in {"EQ", "NE", "GT", "GTE", "LT", "LTE"}:
                raise ValueError(f"{label}[{index}] CONDITION requires operator")
            if condition.get("value") in (None, ""):
                raise ValueError(f"{label}[{index}] CONDITION requires value")
        if mode == ContextMode.RANKING.value:
            weight = _decimal(raw.get("weight"), f"{label}[{index}].weight")
            if weight == 0:
                raise ValueError(f"{label}[{index}] RANKING weight cannot be zero")


def strategy_authoring_template() -> dict[str, object]:
    """Return an inert StrategyCard authoring skeleton with no trading-number defaults."""

    disabled_numeric = {"enabled": False, "value": None, "unit": None, "scope": None}
    disabled_cooldown = {
        "enabled": False,
        "duration": None,
        "unit": None,
        "scope": None,
        "trigger_event": None,
        "anchor": None,
    }
    return {
        "strategy_id": "",
        "strategy_version": "",
        "name": "",
        "description": "",
        "scope": {"kind": "symbols", "symbols": []},
        "symbols": [],
        "direction_policy": [],
        "entry_policy": {
            "entry_plan_version": "entry-owner-1",
            "predicate": {"op": "TOUCH"},
            "entry_reference_policy": {
                "enabled": False,
                "reference": "CALCULATED_ENTRY",
            },
            "local_entry_policy": {
                "enabled": False,
                "lookback_by_timeframe": {},
                "use_1m": False,
                "require_5m_15m_confluence": False,
                "require_macro_relation": False,
            },
            "watch_policy": {
                "enabled": False,
                "derived_event_kind": "TOUCH",
                "candidate_timeframe_minutes": 5,
                "candidate_lifecycle": {"clear_on_touch": True},
                "events": {
                    "bar_open": "BAR_OPEN",
                    "candle_closed": "CANDLE_CLOSED",
                    "open_interest": "OPEN_INTEREST",
                    "trade": "PUBLIC_TRADE",
                },
                "geometry": {
                    "operator": "RANGE_ATR_CONFLUENCE",
                    "timeframes": ["5", "15"],
                    "primary_timeframe": "5",
                    "confirming_timeframe": "15",
                    "lookback_by_timeframe": {},
                    "shock_reset_policy": {"enabled": False},
                },
                "hourly_swing": {"enabled": False},
            },
            "execution_policy": {},
            "context_feature_policy": [],
        },
        "exit_policy": {
            "exit_plan_version": "exit-owner-1",
            "context_feature_policy": [],
        },
        "capital_policy": {"require_capacity": False},
        "protection_policy": {"initial_protection": {}},
        "lifecycle_policy": {
            "post_signal_outcome_policy": {"enabled": False},
            "hedge_policy": {"enabled": False},
        },
        "touch_policy": {
            "accepted_touch_numbers": [],
            "accept_touch_from": None,
            "require_exit_from_zone": False,
            "minimum_exit_distance": dict(disabled_numeric),
            "minimum_time_between_touches": dict(disabled_numeric),
            "maximum_touch_count": None,
            "reset_on": [],
            "candidate_cooldown": disabled_cooldown,
        },
        "market_sensor_policy": [],
        "mayak_context_policy": [],
        "dispatcher_context_policy": [],
    }


def _validate_symbols_scope(raw: Mapping[str, object]) -> None:
    symbols = [str(item).strip().upper() for item in _list(raw.get("symbols"), "symbols")]
    if not symbols:
        raise ValueError("Strategy requires at least one symbol")
    if any(not symbol for symbol in symbols):
        raise ValueError("Strategy symbols cannot be blank")
    if len(set(symbols)) != len(symbols):
        raise ValueError("Strategy symbols must be unique")
    scope = _mapping(raw.get("scope"), "scope")
    if str(scope.get("kind") or "") != "symbols":
        raise ValueError("Strategy UI scope.kind must be symbols")
    scoped_raw = scope.get("symbols", scope.get("trading_symbols"))
    scoped = [str(item).strip().upper() for item in _list(scoped_raw, "scope symbols")]
    if tuple(sorted(scoped)) != tuple(sorted(symbols)):
        raise ValueError("Strategy scope symbols must exactly match Strategy symbols")


def _validate_shock_reset_policy(geometry: Mapping[str, object]) -> None:
    value = geometry.get("shock_reset_policy")
    if value is None:
        return
    policy = _mapping(value, "geometry.shock_reset_policy")
    enabled = _bool(policy.get("enabled"), "shock_reset_policy.enabled")
    if not enabled:
        return
    mode = str(policy.get("detection_mode") or "")
    if mode not in {"ATR_MULTIPLE", "RANGE_PERCENT"}:
        raise ValueError("shock_reset_policy requires ATR_MULTIPLE or RANGE_PERCENT")
    try:
        maturity = int(str(policy.get("maturity_minutes")))
    except (TypeError, ValueError):
        raise ValueError("shock_reset_policy.maturity_minutes must be integer") from None
    if maturity < 0:
        raise ValueError("shock_reset_policy.maturity_minutes cannot be negative")
    if mode == "ATR_MULTIPLE":
        try:
            period = int(str(policy.get("tr_period")))
        except (TypeError, ValueError):
            raise ValueError("shock_reset_policy.tr_period must be integer") from None
        if period <= 0:
            raise ValueError("shock_reset_policy.tr_period must be positive")
        if _decimal(policy.get("multiple"), "shock_reset_policy.multiple") <= 0:
            raise ValueError("shock_reset_policy.multiple must be positive")
        return
    thresholds = _mapping(
        policy.get("threshold_percent_by_timeframe"),
        "shock_reset_policy.threshold_percent_by_timeframe",
    )
    timeframes = [str(item) for item in _list(geometry.get("timeframes"), "geometry.timeframes")]
    for timeframe in timeframes:
        if (
            _decimal(
                thresholds.get(timeframe),
                f"shock_reset_policy threshold {timeframe}",
            )
            <= 0
        ):
            raise ValueError(f"shock_reset_policy threshold {timeframe} must be positive")


def _validate_rolling_swing_policy(watch: Mapping[str, object]) -> None:
    value = watch.get("hourly_swing")
    if value is None:
        return
    policy = _mapping(value, "entry_policy.watch_policy.hourly_swing")
    enabled = _bool(policy.get("enabled"), "hourly_swing.enabled")
    if not enabled:
        return
    if str(policy.get("operator") or "") != "ROLLING_RANGE_PERCENT":
        raise ValueError("hourly_swing.operator must be ROLLING_RANGE_PERCENT")
    try:
        timeframe = int(str(policy.get("timeframe")))
        window_bars = int(str(policy.get("window_bars")))
    except (TypeError, ValueError):
        raise ValueError("hourly_swing timeframe/window_bars must be integers") from None
    if timeframe <= 0 or window_bars <= 0:
        raise ValueError("hourly_swing timeframe/window_bars must be positive")
    if _decimal(policy.get("threshold_percent"), "hourly_swing.threshold_percent") <= 0:
        raise ValueError("hourly_swing.threshold_percent must be positive")
    if str(policy.get("comparator") or "") != "GTE":
        raise ValueError("hourly_swing.comparator must be GTE")


def _validate_post_signal_policy(lifecycle: Mapping[str, object]) -> None:
    value = lifecycle.get("post_signal_outcome_policy")
    if value is None:
        return
    policy = _mapping(value, "lifecycle_policy.post_signal_outcome_policy")
    enabled = _bool(policy.get("enabled"), "post_signal_outcome_policy.enabled")
    if not enabled:
        extra = {key for key, item in policy.items() if key != "enabled" and item not in (None, "")}
        if extra:
            raise ValueError("disabled post_signal_outcome_policy cannot carry hidden values")
        return
    favorable = _decimal(policy.get("favorable_threshold"), "post_signal favorable_threshold")
    adverse = _decimal(policy.get("adverse_threshold"), "post_signal adverse_threshold")
    if favorable <= 0:
        raise ValueError("post_signal favorable_threshold must be positive")
    if adverse >= 0:
        raise ValueError("post_signal adverse_threshold must be negative")
    horizon = _decimal(policy.get("horizon"), "post_signal horizon")
    if horizon <= 0 or str(policy.get("horizon_unit") or "") not in {
        "seconds",
        "minutes",
        "hours",
    }:
        raise ValueError("post_signal horizon requires positive value and explicit unit")
    if str(policy.get("resolution_semantics") or "") != "FIRST_THRESHOLD":
        raise ValueError("post_signal resolution_semantics must be FIRST_THRESHOLD")
    embargo = _mapping(policy.get("optional_embargo"), "post_signal optional_embargo")
    embargo_enabled = _bool(embargo.get("enabled"), "post_signal embargo.enabled")
    if embargo_enabled:
        if str(embargo.get("on_resolution") or "") != "ADVERSE":
            raise ValueError("post_signal failure embargo must be anchored to ADVERSE")
        if _decimal(embargo.get("duration"), "post_signal embargo.duration") <= 0:
            raise ValueError("post_signal embargo.duration must be positive")
        if str(embargo.get("unit") or "") not in {"seconds", "minutes", "hours"}:
            raise ValueError("post_signal embargo requires explicit unit")
        if str(embargo.get("scope") or "") not in {
            "PER_SYMBOL",
            "PER_STRATEGY",
            "PER_ACCOUNT",
        }:
            raise ValueError("post_signal embargo requires explicit scope")
        if not str(embargo.get("anchor") or ""):
            raise ValueError("post_signal embargo requires causal anchor")


def _validate_authoring_extensions(raw: Mapping[str, object]) -> None:
    directions = _list(raw.get("direction_policy"), "direction_policy")
    if len(directions) != 1 or str(directions[0]) not in {"LONG", "SHORT"}:
        raise ValueError("new Strategy UI version requires exactly one direction: LONG or SHORT")
    if not str(raw.get("name") or "").strip():
        raise ValueError("Strategy name is required")
    _validate_symbols_scope(raw)

    entry = _mapping(raw.get("entry_policy"), "entry_policy")
    reference = entry.get("entry_reference_policy")
    if reference is not None:
        policy = _mapping(reference, "entry_policy.entry_reference_policy")
        enabled = _bool(policy.get("enabled"), "entry_reference_policy.enabled")
        if enabled:
            _decimal(policy.get("offset_pct_signed"), "entry_reference_policy.offset_pct_signed")
            if str(policy.get("reference") or "") != "CALCULATED_ENTRY":
                raise ValueError("entry_reference_policy.reference must be CALCULATED_ENTRY")
    local = entry.get("local_entry_policy")
    if local is not None:
        local_policy = _mapping(local, "entry_policy.local_entry_policy")
        enabled = _bool(local_policy.get("enabled"), "local_entry_policy.enabled")
        if enabled:
            try:
                window_minutes = int(str(local_policy.get("window_minutes")))
            except (TypeError, ValueError):
                raise ValueError("local_entry_policy.window_minutes must be integer") from None
            if window_minutes <= 0:
                raise ValueError("local_entry_policy.window_minutes must be positive")
            lookbacks = _mapping(
                local_policy.get("lookback_by_timeframe"),
                "local_entry_policy.lookback_by_timeframe",
            )
            for timeframe in ("5", "15"):
                try:
                    count = int(str(lookbacks.get(timeframe)))
                except (TypeError, ValueError):
                    raise ValueError(
                        f"local_entry_policy lookback {timeframe} must be integer"
                    ) from None
                if count <= 0:
                    raise ValueError(f"local_entry_policy lookback {timeframe} must be positive")
            if _bool(local_policy.get("use_1m", False), "local_entry_policy.use_1m"):
                try:
                    one_minute = int(str(lookbacks.get("1")))
                except (TypeError, ValueError):
                    raise ValueError("local_entry_policy lookback 1 must be integer") from None
                if one_minute <= 0:
                    raise ValueError("local_entry_policy lookback 1 must be positive")
            _bool(
                local_policy.get("require_5m_15m_confluence", False),
                "local_entry_policy.require_5m_15m_confluence",
            )
            _bool(
                local_policy.get("require_macro_relation", False),
                "local_entry_policy.require_macro_relation",
            )
    watch_value = entry.get("watch_policy")
    if watch_value is not None:
        watch = _mapping(watch_value, "entry_policy.watch_policy")
        watch_enabled = _bool(watch.get("enabled"), "entry_policy.watch_policy.enabled")
        geometry_value = watch.get("geometry")
        if geometry_value is not None:
            geometry = _mapping(geometry_value, "entry_policy.watch_policy.geometry")
            if watch_enabled:
                if str(geometry.get("operator") or "") != "RANGE_ATR_CONFLUENCE":
                    raise ValueError("enabled Strategy UI Entry requires RANGE_ATR_CONFLUENCE")
                timeframes = [
                    str(item) for item in _list(geometry.get("timeframes"), "geometry.timeframes")
                ]
                if set(timeframes) != {"5", "15"}:
                    raise ValueError("enabled Strategy UI Entry requires 5m and 15m geometry")
                lookbacks = _mapping(
                    geometry.get("lookback_by_timeframe"), "geometry.lookback_by_timeframe"
                )
                for timeframe in ("5", "15"):
                    try:
                        bars = int(str(lookbacks.get(timeframe)))
                    except (TypeError, ValueError):
                        raise ValueError(f"geometry lookback {timeframe} must be integer") from None
                    if bars <= 0:
                        raise ValueError(f"geometry lookback {timeframe} must be positive")
                try:
                    atr_period = int(str(geometry.get("atr_period")))
                except (TypeError, ValueError):
                    raise ValueError("geometry atr_period must be integer") from None
                if atr_period <= 0:
                    raise ValueError("geometry atr_period must be positive")
                if _decimal(geometry.get("zone_half_width_atr"), "zone_half_width_atr") <= 0:
                    raise ValueError("zone_half_width_atr must be positive")
                if (
                    _decimal(
                        geometry.get("confluence_max_gap_percent"),
                        "confluence_max_gap_percent",
                    )
                    < 0
                ):
                    raise ValueError("confluence_max_gap_percent cannot be negative")
            _validate_shock_reset_policy(geometry)
        elif watch_enabled:
            raise ValueError("enabled Strategy UI Entry requires geometry")
        _validate_rolling_swing_policy(watch)
    _validate_feature_policy(
        entry.get("context_feature_policy"), "entry_policy.context_feature_policy"
    )

    exit_policy = _mapping(raw.get("exit_policy"), "exit_policy")
    for field in ("hard_stop", "take_profit"):
        value = exit_policy.get(field)
        if value is None:
            continue
        policy = _mapping(value, f"exit_policy.{field}")
        enabled = _bool(policy.get("enabled"), f"exit_policy.{field}.enabled")
        if enabled and _decimal(policy.get("percent"), f"exit_policy.{field}.percent") <= 0:
            raise ValueError(f"exit_policy.{field}.percent must be positive")
    for field in ("break_even", "trailing"):
        value = exit_policy.get(field)
        if value is None:
            continue
        policy = _mapping(value, f"exit_policy.{field}")
        enabled = _bool(policy.get("enabled"), f"exit_policy.{field}.enabled")
        if enabled and policy.get("activation_profit_pct") not in (None, ""):
            _decimal(
                policy.get("activation_profit_pct"),
                f"exit_policy.{field}.activation_profit_pct",
            )
        if (
            enabled
            and field == "trailing"
            and _decimal(policy.get("distance_pct"), "exit_policy.trailing.distance_pct") <= 0
        ):
            raise ValueError("exit_policy.trailing.distance_pct must be positive")
    time_exit = exit_policy.get("time_exit")
    if time_exit is not None:
        policy = _mapping(time_exit, "exit_policy.time_exit")
        enabled = _bool(policy.get("enabled"), "exit_policy.time_exit.enabled")
        if enabled:
            try:
                horizon = int(str(policy.get("horizon_minutes")))
            except (TypeError, ValueError):
                raise ValueError("exit_policy.time_exit.horizon_minutes must be integer") from None
            if horizon <= 0:
                raise ValueError("exit_policy.time_exit.horizon_minutes must be positive")
    _validate_feature_policy(
        exit_policy.get("context_feature_policy"), "exit_policy.context_feature_policy"
    )

    lifecycle = _mapping(raw.get("lifecycle_policy"), "lifecycle_policy")
    _validate_post_signal_policy(lifecycle)
    hedge = lifecycle.get("hedge_policy")
    if hedge is not None:
        hedge_policy = _mapping(hedge, "lifecycle_policy.hedge_policy")
        enabled = _bool(hedge_policy.get("enabled"), "hedge_policy.enabled")
        if enabled:
            trigger = _mapping(hedge_policy.get("trigger"), "hedge_policy.trigger")
            if str(trigger.get("reference") or "") != "PRIMARY_ENTRY":
                raise ValueError("hedge trigger reference must be PRIMARY_ENTRY")
            _decimal(trigger.get("offset_pct_signed"), "hedge trigger offset_pct_signed")
            capital = _mapping(hedge_policy.get("capital"), "hedge_policy.capital")
            size = _decimal(capital.get("size_percent_of_primary"), "hedge size_percent_of_primary")
            if size <= 0:
                raise ValueError("hedge size_percent_of_primary must be positive")
            try:
                leverage = int(str(capital.get("leverage")))
            except (TypeError, ValueError):
                raise ValueError("hedge leverage must be integer") from None
            if leverage <= 0:
                raise ValueError("hedge leverage must be positive")
            for field in ("stop_loss", "take_profit"):
                value = hedge_policy.get(field)
                if value is None:
                    continue
                policy = _mapping(value, f"hedge_policy.{field}")
                field_enabled = _bool(policy.get("enabled"), f"hedge_policy.{field}.enabled")
                if (
                    field_enabled
                    and _decimal(policy.get("percent"), f"hedge_policy.{field}.percent") <= 0
                ):
                    raise ValueError(f"hedge_policy.{field}.percent must be positive")
            trailing = hedge_policy.get("trailing")
            if trailing is not None:
                policy = _mapping(trailing, "hedge_policy.trailing")
                trailing_enabled = _bool(policy.get("enabled"), "hedge_policy.trailing.enabled")
                if trailing_enabled:
                    if policy.get("activation_profit_pct") not in (None, ""):
                        _decimal(
                            policy.get("activation_profit_pct"),
                            "hedge_policy.trailing.activation_profit_pct",
                        )
                    if (
                        _decimal(policy.get("distance_pct"), "hedge_policy.trailing.distance_pct")
                        <= 0
                    ):
                        raise ValueError("hedge_policy.trailing.distance_pct must be positive")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list")
    return list(value)


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid {label}") from None
    if not result.is_finite():
        raise ValueError(f"invalid {label}")
    return result


def _enum_or_none[T](enum_type: type[T], value: object | None, label: str) -> T | None:
    if value is None:
        return None
    try:
        constructor = cast(Callable[[str], T], enum_type)
        return constructor(str(value))
    except ValueError:
        raise ValueError(f"invalid {label}: {value}") from None


def _unwrap_policy(value: object, label: str) -> dict[str, object]:
    raw = _mapping(value, label)
    if "payload_json" not in raw:
        return raw
    payload = raw.get("payload_json")
    if not isinstance(payload, str):
        raise ValueError(f"{label}.payload_json must be a JSON string")
    decoded = json.loads(payload)
    return _mapping(decoded, label)


def _numeric_rule_to_mapping(rule: NumericRule) -> dict[str, object]:
    return {
        "enabled": rule.enabled,
        "value": None if rule.value is None else str(rule.value),
        "unit": rule.unit,
        "scope": rule.scope,
    }


def _numeric_rule_from_mapping(value: object, label: str) -> NumericRule:
    raw = _mapping(value, label)
    if "enabled" not in raw:
        raise ValueError(f"{label}.enabled is required")
    enabled = _bool(raw["enabled"], f"{label}.enabled")
    if not enabled:
        return NumericRule(enabled=False)
    return NumericRule(
        enabled=True,
        value=_decimal(raw.get("value"), f"{label}.value"),
        unit=str(raw.get("unit") or ""),
        scope=None if raw.get("scope") is None else str(raw["scope"]),
    )


def _cooldown_to_mapping(cooldown: CandidateCooldown) -> dict[str, object]:
    return {
        "enabled": cooldown.enabled,
        "duration": None if cooldown.duration is None else str(cooldown.duration),
        "unit": cooldown.unit,
        "scope": None if cooldown.scope is None else cooldown.scope.value,
        "trigger_event": cooldown.trigger_event,
        "anchor": cooldown.anchor,
    }


def _cooldown_from_mapping(value: object) -> CandidateCooldown:
    raw = _mapping(value, "touch_policy.candidate_cooldown")
    if "enabled" not in raw:
        raise ValueError("touch_policy.candidate_cooldown.enabled is required")
    if not _bool(raw["enabled"], "touch_policy.candidate_cooldown.enabled"):
        return CandidateCooldown(enabled=False)
    scope = _enum_or_none(
        CooldownScope,
        raw.get("scope"),
        "touch_policy.candidate_cooldown.scope",
    )
    if scope is None:
        raise ValueError("touch_policy.candidate_cooldown.scope is required")
    return CandidateCooldown(
        enabled=True,
        duration=_decimal(raw.get("duration"), "candidate cooldown duration"),
        unit=str(raw.get("unit") or ""),
        scope=scope,
        trigger_event=str(raw.get("trigger_event") or ""),
        anchor=str(raw.get("anchor") or ""),
    )


def _touch_to_mapping(touch: TouchPolicy) -> dict[str, object]:
    return {
        "accepted_touch_numbers": list(touch.accepted_touch_numbers),
        "accept_touch_from": touch.accept_touch_from,
        "require_exit_from_zone": touch.require_exit_from_zone,
        "minimum_exit_distance": _numeric_rule_to_mapping(touch.minimum_exit_distance),
        "minimum_time_between_touches": _numeric_rule_to_mapping(
            touch.minimum_time_between_touches
        ),
        "maximum_touch_count": touch.maximum_touch_count,
        "reset_on": list(touch.reset_on),
        "candidate_cooldown": _cooldown_to_mapping(touch.candidate_cooldown),
    }


def _touch_from_mapping(value: object) -> TouchPolicy:
    raw = _mapping(value, "touch_policy")
    required = (
        "accepted_touch_numbers",
        "accept_touch_from",
        "require_exit_from_zone",
        "minimum_exit_distance",
        "minimum_time_between_touches",
        "maximum_touch_count",
        "reset_on",
        "candidate_cooldown",
    )
    missing = tuple(name for name in required if name not in raw)
    if missing:
        raise ValueError(f"touch_policy missing required fields: {missing}")
    return TouchPolicy(
        accepted_touch_numbers=tuple(
            int(str(item))
            for item in _list(raw["accepted_touch_numbers"], "accepted_touch_numbers")
        ),
        accept_touch_from=(
            None if raw["accept_touch_from"] is None else int(str(raw["accept_touch_from"]))
        ),
        require_exit_from_zone=_bool(
            raw["require_exit_from_zone"], "touch_policy.require_exit_from_zone"
        ),
        minimum_exit_distance=_numeric_rule_from_mapping(
            raw["minimum_exit_distance"], "touch_policy.minimum_exit_distance"
        ),
        minimum_time_between_touches=_numeric_rule_from_mapping(
            raw["minimum_time_between_touches"],
            "touch_policy.minimum_time_between_touches",
        ),
        maximum_touch_count=(
            None if raw["maximum_touch_count"] is None else int(str(raw["maximum_touch_count"]))
        ),
        reset_on=tuple(str(item) for item in _list(raw["reset_on"], "reset_on")),
        candidate_cooldown=_cooldown_from_mapping(raw["candidate_cooldown"]),
    )


def _sensor_to_mapping(item: SensorRequirement) -> dict[str, object]:
    return {
        "sensor_id": item.sensor_id,
        "mode": item.mode.value,
        "max_age_seconds": item.max_age_seconds,
        "min_quality": None if item.min_quality is None else item.min_quality.value,
        "on_missing": None if item.on_missing is None else item.on_missing.value,
        "on_stale": None if item.on_stale is None else item.on_stale.value,
        "on_partial": None if item.on_partial is None else item.on_partial.value,
    }


def _sensor_from_mapping(value: object) -> SensorRequirement:
    raw = _mapping(value, "market_sensor_policy item")
    try:
        mode = ContextMode(str(raw.get("mode")))
    except ValueError:
        raise ValueError("invalid sensor mode") from None
    return SensorRequirement(
        sensor_id=str(raw.get("sensor_id") or ""),
        mode=mode,
        max_age_seconds=(
            None if raw.get("max_age_seconds") is None else int(str(raw["max_age_seconds"]))
        ),
        min_quality=_enum_or_none(DataQuality, raw.get("min_quality"), "sensor min_quality"),
        on_missing=_enum_or_none(ContextFailureAction, raw.get("on_missing"), "sensor on_missing"),
        on_stale=_enum_or_none(ContextFailureAction, raw.get("on_stale"), "sensor on_stale"),
        on_partial=_enum_or_none(ContextFailureAction, raw.get("on_partial"), "sensor on_partial"),
    )


def _context_to_mapping(item: ContextRequirement) -> dict[str, object]:
    return {
        "context_id": item.context_id,
        "mode": item.mode.value,
        "max_age_seconds": item.max_age_seconds,
        "min_quality": None if item.min_quality is None else item.min_quality.value,
        "on_missing": None if item.on_missing is None else item.on_missing.value,
        "on_stale": None if item.on_stale is None else item.on_stale.value,
        "on_partial": None if item.on_partial is None else item.on_partial.value,
    }


def _context_from_mapping(value: object, label: str) -> ContextRequirement:
    raw = _mapping(value, label)
    try:
        mode = ContextMode(str(raw.get("mode")))
    except ValueError:
        raise ValueError(f"invalid {label} mode") from None
    return ContextRequirement(
        context_id=str(raw.get("context_id") or ""),
        mode=mode,
        max_age_seconds=(
            None if raw.get("max_age_seconds") is None else int(str(raw["max_age_seconds"]))
        ),
        min_quality=_enum_or_none(DataQuality, raw.get("min_quality"), f"{label} min_quality"),
        on_missing=_enum_or_none(
            ContextFailureAction, raw.get("on_missing"), f"{label} on_missing"
        ),
        on_stale=_enum_or_none(ContextFailureAction, raw.get("on_stale"), f"{label} on_stale"),
        on_partial=_enum_or_none(
            ContextFailureAction, raw.get("on_partial"), f"{label} on_partial"
        ),
    )


def card_to_editable(card: StrategyCard) -> dict[str, object]:
    """Return a complete UI-editable policy without inventing defaults."""

    return {
        "strategy_id": card.strategy_id,
        "strategy_version": card.strategy_version,
        "name": card.name,
        "description": card.description,
        "scope": card.scope.to_dict(),
        "symbols": list(card.symbols),
        "direction_policy": [item.value for item in card.direction_policy],
        "entry_policy": card.entry_policy.to_dict(),
        "exit_policy": card.exit_policy.to_dict(),
        "capital_policy": card.capital_policy.to_dict(),
        "protection_policy": card.protection_policy.to_dict(),
        "lifecycle_policy": card.lifecycle_policy.to_dict(),
        "touch_policy": _touch_to_mapping(card.touch_policy),
        "market_sensor_policy": [_sensor_to_mapping(item) for item in card.market_sensor_policy],
        "mayak_context_policy": [_context_to_mapping(item) for item in card.mayak_context_policy],
        "dispatcher_context_policy": [
            _context_to_mapping(item) for item in card.dispatcher_context_policy
        ],
    }


def editable_from_stored_card(card_json: object) -> dict[str, object]:
    raw = _mapping(card_json, "card_json")
    touch = _mapping(raw.get("touch_policy"), "card_json.touch_policy")
    # Stored canonical dataclass JSON already represents touch rules structurally.
    editable_touch = {
        "accepted_touch_numbers": _list(
            touch.get("accepted_touch_numbers"), "accepted_touch_numbers"
        ),
        "accept_touch_from": touch.get("accept_touch_from"),
        "require_exit_from_zone": bool(touch.get("require_exit_from_zone")),
        "minimum_exit_distance": dict(
            _mapping(touch.get("minimum_exit_distance"), "minimum_exit_distance")
        ),
        "minimum_time_between_touches": dict(
            _mapping(touch.get("minimum_time_between_touches"), "minimum_time_between_touches")
        ),
        "maximum_touch_count": touch.get("maximum_touch_count"),
        "reset_on": _list(touch.get("reset_on"), "reset_on"),
        "candidate_cooldown": dict(_mapping(touch.get("candidate_cooldown"), "candidate_cooldown")),
    }
    return {
        "strategy_id": str(raw.get("strategy_id") or ""),
        "strategy_version": str(raw.get("strategy_version") or ""),
        "name": str(raw.get("name") or ""),
        "description": str(raw.get("description") or ""),
        "scope": _unwrap_policy(raw.get("scope"), "scope"),
        "symbols": list(_list(raw.get("symbols"), "symbols")),
        "direction_policy": list(_list(raw.get("direction_policy"), "direction_policy")),
        "entry_policy": _unwrap_policy(raw.get("entry_policy"), "entry_policy"),
        "exit_policy": _unwrap_policy(raw.get("exit_policy"), "exit_policy"),
        "capital_policy": _unwrap_policy(raw.get("capital_policy"), "capital_policy"),
        "protection_policy": _unwrap_policy(raw.get("protection_policy"), "protection_policy"),
        "lifecycle_policy": _unwrap_policy(raw.get("lifecycle_policy"), "lifecycle_policy"),
        "touch_policy": editable_touch,
        "market_sensor_policy": list(
            _list(raw.get("market_sensor_policy"), "market_sensor_policy")
        ),
        "mayak_context_policy": list(
            _list(raw.get("mayak_context_policy"), "mayak_context_policy")
        ),
        "dispatcher_context_policy": list(
            _list(raw.get("dispatcher_context_policy"), "dispatcher_context_policy")
        ),
    }


def card_from_editable(
    payload: Mapping[str, object],
    *,
    approved_at: datetime,
    approved_source: str,
    validate_authoring: bool = True,
) -> StrategyCard:
    raw = _mapping(payload, "StrategyCard payload")
    required = (
        "strategy_id",
        "strategy_version",
        "name",
        "description",
        "scope",
        "symbols",
        "direction_policy",
        "entry_policy",
        "exit_policy",
        "capital_policy",
        "protection_policy",
        "lifecycle_policy",
        "touch_policy",
        "market_sensor_policy",
        "mayak_context_policy",
        "dispatcher_context_policy",
    )
    missing = tuple(name for name in required if name not in raw)
    if missing:
        raise ValueError(f"StrategyCard payload missing required fields: {missing}")
    if validate_authoring:
        _validate_authoring_extensions(raw)
    symbols = tuple(str(item).upper() for item in _list(raw["symbols"], "symbols"))
    directions = tuple(
        TradeDirection(str(item)) for item in _list(raw["direction_policy"], "direction_policy")
    )
    sensors = tuple(
        _sensor_from_mapping(item)
        for item in _list(raw["market_sensor_policy"], "market_sensor_policy")
    )
    mayak_context = tuple(
        _context_from_mapping(item, "mayak_context_policy item")
        for item in _list(raw["mayak_context_policy"], "mayak_context_policy")
    )
    dispatcher_context = tuple(
        _context_from_mapping(item, "dispatcher_context_policy item")
        for item in _list(raw["dispatcher_context_policy"], "dispatcher_context_policy")
    )
    return StrategyCard.build(
        strategy_id=str(raw["strategy_id"]),
        strategy_version=str(raw["strategy_version"]),
        name=str(raw["name"]),
        description=str(raw["description"]),
        scope=FrozenPolicy.from_mapping(_mapping(raw["scope"], "scope")),
        symbols=symbols,
        direction_policy=directions,
        entry_policy=FrozenPolicy.from_mapping(_mapping(raw["entry_policy"], "entry_policy")),
        exit_policy=FrozenPolicy.from_mapping(_mapping(raw["exit_policy"], "exit_policy")),
        capital_policy=FrozenPolicy.from_mapping(_mapping(raw["capital_policy"], "capital_policy")),
        protection_policy=FrozenPolicy.from_mapping(
            _mapping(raw["protection_policy"], "protection_policy")
        ),
        lifecycle_policy=FrozenPolicy.from_mapping(
            _mapping(raw["lifecycle_policy"], "lifecycle_policy")
        ),
        touch_policy=_touch_from_mapping(raw["touch_policy"]),
        market_sensor_policy=sensors,
        mayak_context_policy=mayak_context,
        dispatcher_context_policy=dispatcher_context,
        approved_at=approved_at.astimezone(UTC),
        approved_source=approved_source,
    )


def build_new_strategy_version(
    base: StrategyCard,
    payload: Mapping[str, object],
    *,
    approved_at: datetime,
    approved_source: str,
) -> StrategyCard:
    raw = _mapping(payload, "new StrategyCard")
    if str(raw.get("strategy_id") or "") != base.strategy_id:
        raise ValueError("new Strategy version cannot change strategy_id")
    new_version = str(raw.get("strategy_version") or "")
    if not new_version or new_version == base.strategy_version:
        raise ValueError("new strategy_version must be explicit and different from base")
    return card_from_editable(raw, approved_at=approved_at, approved_source=approved_source)


def _identity(row: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(row.get("strategy_id") or ""),
        str(row.get("strategy_version") or ""),
        str(row.get("strategy_config_fingerprint") or ""),
    )


def _sections(editable: Mapping[str, object]) -> dict[str, object]:
    entry = _mapping(editable.get("entry_policy"), "entry_policy")
    watch = entry.get("watch_policy")
    watch_mapping = dict(watch) if isinstance(watch, Mapping) else None
    touch = _mapping(editable.get("touch_policy"), "touch_policy")
    return {
        "general": {
            "strategy_id": editable.get("strategy_id"),
            "strategy_version": editable.get("strategy_version"),
            "name": editable.get("name"),
            "description": editable.get("description"),
            "scope": editable.get("scope"),
            "symbols": editable.get("symbols"),
            "direction_policy": editable.get("direction_policy"),
        },
        "entry": editable.get("entry_policy"),
        "touch": editable.get("touch_policy"),
        "lifecycle": {
            "lifecycle_policy": editable.get("lifecycle_policy"),
            "candidate_cooldown": touch.get("candidate_cooldown"),
            "reset_on": touch.get("reset_on"),
        },
        "geometry_sensors": {
            "watch_policy": watch_mapping,
            "market_sensor_policy": editable.get("market_sensor_policy"),
        },
        "capital_leverage": editable.get("capital_policy"),
        "protection": editable.get("protection_policy"),
        "exit": editable.get("exit_policy"),
        "hedge": (
            _mapping(editable.get("lifecycle_policy"), "lifecycle_policy").get("hedge_policy")
        ),
        "mayak_usage": editable.get("mayak_context_policy"),
        "dispatcher_usage": editable.get("dispatcher_context_policy"),
    }


def assemble_strategy_catalog(
    cards: Sequence[Mapping[str, object]],
    activations: Sequence[Mapping[str, object]],
    entry_plans: Sequence[Mapping[str, object]],
    exit_plans: Sequence[Mapping[str, object]],
    activation_events: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for card in cards:
        key = _identity(card)
        editable = editable_from_stored_card(card.get("card_json"))
        card_activations = [dict(row) for row in activations if _identity(row) == key]
        card_entry = [dict(row) for row in entry_plans if _identity(row) == key]
        card_exit = [dict(row) for row in exit_plans if _identity(row) == key]
        activation_ids = {str(row.get("activation_id")) for row in card_activations}
        history = [
            dict(row)
            for row in activation_events
            if str(row.get("activation_id")) in activation_ids
        ]
        if not card_activations:
            activation_state = "NOT SET"
        elif len(card_activations) == 1:
            activation_state = "ENABLED" if bool(card_activations[0].get("enabled")) else "DISABLED"
        else:
            activation_state = "MULTIPLE"
        result.append(
            {
                "strategy_id": key[0],
                "strategy_version": key[1],
                "strategy_config_fingerprint": key[2],
                "name": card.get("name"),
                "description": card.get("description"),
                "direction_policy": editable.get("direction_policy"),
                "scope": editable.get("scope"),
                "symbols": editable.get("symbols"),
                "activation_state": activation_state,
                "activations": card_activations,
                "entry_plan_fingerprints": [
                    str(row.get("entry_plan_fingerprint")) for row in card_entry
                ],
                "exit_plan_fingerprints": [
                    str(row.get("exit_plan_fingerprint")) for row in card_exit
                ],
                "entry_plans": card_entry,
                "exit_plans": card_exit,
                "activation_history": history,
                "approved_at": card.get("approved_at"),
                "approved_source": card.get("approved_source"),
                "editable_card": editable,
                "sections": _sections(editable),
                "read_only": True,
            }
        )
    return result


_CARD_COLUMNS = (
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "name",
    "description",
    "card_json",
    "approved_at",
    "approved_source",
    "created_at",
)
_ACTIVATION_COLUMNS = (
    "activation_id",
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "enabled",
    "enabled_at",
    "disabled_at",
    "scope",
    "operator",
    "source",
    "change_reason",
    "created_at",
    "updated_at",
)
_ENTRY_PLAN_COLUMNS = (
    "entry_plan_fingerprint",
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "entry_plan_version",
    "created_at",
)
_EXIT_PLAN_COLUMNS = (
    "exit_plan_fingerprint",
    "strategy_id",
    "strategy_version",
    "strategy_config_fingerprint",
    "exit_plan_version",
    "created_at",
)
_EVENT_COLUMNS = (
    "activation_event_id",
    "activation_id",
    "occurred_at",
    "enabled",
    "enabled_at",
    "disabled_at",
    "operator",
    "source",
    "reason",
    "scope",
)


def _dict_rows(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> list[dict[str, object]]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


class StrategyDashboardStore:
    """Narrow U6 Strategy read/control store; no Execution or market mutation API."""

    def __init__(self, connection: DashboardConnectionLike) -> None:
        self._connection = connection

    def list_catalog(self) -> list[dict[str, object]]:
        cards = _dict_rows(
            _CARD_COLUMNS,
            self._connection.execute(
                """SELECT strategy_id,strategy_version,strategy_config_fingerprint,
                          name,description,card_json,approved_at,approved_source,created_at
                   FROM strategy_entry.strategy_cards
                   ORDER BY strategy_id,strategy_version,strategy_config_fingerprint"""
            ).fetchall(),
        )
        activations = _dict_rows(
            _ACTIVATION_COLUMNS,
            self._connection.execute(
                """SELECT activation_id,strategy_id,strategy_version,
                          strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                          scope,operator,source,change_reason,created_at,updated_at
                   FROM strategy_entry.strategy_activations
                   ORDER BY strategy_id,strategy_version,created_at,activation_id"""
            ).fetchall(),
        )
        entry_plans = _dict_rows(
            _ENTRY_PLAN_COLUMNS,
            self._connection.execute(
                """SELECT entry_plan_fingerprint,strategy_id,strategy_version,
                          strategy_config_fingerprint,entry_plan_version,created_at
                   FROM strategy_entry.entry_plans
                   ORDER BY strategy_id,strategy_version,entry_plan_version,
                            entry_plan_fingerprint"""
            ).fetchall(),
        )
        exit_plans = _dict_rows(
            _EXIT_PLAN_COLUMNS,
            self._connection.execute(
                """SELECT exit_plan_fingerprint,strategy_id,strategy_version,
                          strategy_config_fingerprint,exit_plan_version,created_at
                   FROM strategy_entry.exit_plans
                   ORDER BY strategy_id,strategy_version,exit_plan_version,
                            exit_plan_fingerprint"""
            ).fetchall(),
        )
        events = _dict_rows(
            _EVENT_COLUMNS,
            self._connection.execute(
                """SELECT activation_event_id,activation_id,occurred_at,enabled,
                          enabled_at,disabled_at,operator,source,reason,scope
                   FROM strategy_entry.strategy_activation_events
                   ORDER BY activation_event_id"""
            ).fetchall(),
        )
        return assemble_strategy_catalog(cards, activations, entry_plans, exit_plans, events)

    def _load_exact_base(
        self, strategy_id: str, strategy_version: str, strategy_config_fingerprint: str
    ) -> StrategyCard:
        row = self._connection.execute(
            """SELECT card_json,approved_at,approved_source
               FROM strategy_entry.strategy_cards
               WHERE strategy_id=%s AND strategy_version=%s
                 AND strategy_config_fingerprint=%s""",
            (strategy_id, strategy_version, strategy_config_fingerprint),
        ).fetchone()
        if row is None:
            raise KeyError("exact base StrategyCard not found")
        editable = editable_from_stored_card(row[0])
        approved_at = row[1]
        if not isinstance(approved_at, datetime):
            approved_at = datetime.fromisoformat(str(approved_at))
        card = card_from_editable(
            editable,
            approved_at=approved_at,
            approved_source=str(row[2]),
            validate_authoring=False,
        )
        if card.strategy_config_fingerprint != strategy_config_fingerprint:
            raise ValueError("stored StrategyCard fingerprint does not reproduce canonically")
        return card

    def create_strategy(
        self,
        *,
        payload: Mapping[str, object],
        approved_at: datetime,
        operator: str,
    ) -> StrategyCard:
        created = card_from_editable(
            payload,
            approved_at=approved_at,
            approved_source=f"dashboard:{operator}",
        )
        with self._connection.transaction():
            StrategyEntryStore(self._connection).insert_strategy_card(created)
        return created

    def create_new_version(
        self,
        *,
        base_strategy_id: str,
        base_strategy_version: str,
        base_strategy_config_fingerprint: str,
        payload: Mapping[str, object],
        approved_at: datetime,
        operator: str,
    ) -> StrategyCard:
        with self._connection.transaction():
            base = self._load_exact_base(
                base_strategy_id,
                base_strategy_version,
                base_strategy_config_fingerprint,
            )
            created = build_new_strategy_version(
                base,
                payload,
                approved_at=approved_at,
                approved_source=f"dashboard:{operator}",
            )
            StrategyEntryStore(self._connection).insert_strategy_card(created)
        return created

    def set_activation_enabled_cas(
        self,
        *,
        activation_id: str,
        strategy_id: str,
        strategy_version: str,
        strategy_config_fingerprint: str,
        expected_enabled: bool,
        expected_updated_at: datetime,
        enabled: bool,
        changed_at: datetime,
        operator: str,
        source: str,
        reason: str,
    ) -> dict[str, object]:
        with self._connection.transaction():
            row = self._connection.execute(
                """SELECT activation_id,strategy_id,strategy_version,
                          strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                          operator,source,updated_at
                   FROM strategy_entry.strategy_activations
                   WHERE activation_id=%s
                   FOR UPDATE""",
                (activation_id,),
            ).fetchone()
            if row is None:
                raise UnknownActivation(activation_id)
            current_updated_at = row[9]
            if not isinstance(current_updated_at, datetime):
                current_updated_at = datetime.fromisoformat(str(current_updated_at))
            current_identity = (str(row[1]), str(row[2]), str(row[3]))
            expected_identity = (
                strategy_id,
                strategy_version,
                strategy_config_fingerprint,
            )
            if (
                current_identity != expected_identity
                or bool(row[4]) != expected_enabled
                or current_updated_at != expected_updated_at
            ):
                raise StaleActivationState("STALE_ACTIVATION_STATE")
            if bool(row[4]) == enabled:
                return {
                    "status": "NO_CHANGE",
                    "activation_id": activation_id,
                    "enabled": enabled,
                    "updated_at": current_updated_at,
                }
            cursor = self._connection.execute(
                """UPDATE strategy_entry.strategy_activations
                   SET enabled=%s,
                       enabled_at=CASE WHEN %s THEN %s ELSE enabled_at END,
                       disabled_at=CASE WHEN %s THEN NULL ELSE %s END,
                       operator=%s,source=%s,change_reason=%s
                   WHERE activation_id=%s
                     AND strategy_id=%s AND strategy_version=%s
                     AND strategy_config_fingerprint=%s
                     AND enabled=%s AND updated_at=%s
                   RETURNING updated_at""",
                (
                    enabled,
                    enabled,
                    changed_at,
                    enabled,
                    changed_at,
                    operator,
                    source,
                    reason,
                    activation_id,
                    strategy_id,
                    strategy_version,
                    strategy_config_fingerprint,
                    expected_enabled,
                    expected_updated_at,
                ),
            )
            persisted = cursor.fetchone()
            if cursor.rowcount != 1 or persisted is None:
                raise StaleActivationState("STALE_ACTIVATION_STATE")
            persisted_updated_at = persisted[0]
            if not isinstance(persisted_updated_at, datetime):
                persisted_updated_at = datetime.fromisoformat(str(persisted_updated_at))
            return {
                "status": "UPDATED",
                "activation_id": activation_id,
                "enabled": enabled,
                "updated_at": persisted_updated_at,
            }

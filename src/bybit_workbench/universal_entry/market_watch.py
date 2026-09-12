from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bybit_workbench.domain.models import Candle
from bybit_workbench.strategies.indicators import true_ranges, wilder_atr

from .contracts import EntryPlan, FrozenPolicy, MarketFactEnvelope, TradeDirection
from .fingerprint import fingerprint


@dataclass(frozen=True, slots=True)
class GenericOiPoint:
    timestamp: datetime
    open_interest: Decimal


@dataclass(frozen=True, slots=True)
class GenericZone:
    timeframe: str
    observed_at: datetime
    range_high: Decimal
    range_low: Decimal
    atr: Decimal
    resistance_top: Decimal
    resistance_bottom: Decimal
    support_top: Decimal
    support_bottom: Decimal
    effective_lookback: int
    regime_reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class WatchTracePoint:
    category: str
    causal_key: str
    observed_at: datetime
    payload: FrozenPolicy


@dataclass(frozen=True, slots=True)
class MarketWatchSnapshot:
    candidate_id: str | None
    candidate_bar_at: datetime | None
    geometry: dict[str, GenericZone]
    long_entry: Decimal | None
    short_entry: Decimal | None
    long_gap_percent: Decimal | None
    short_gap_percent: Decimal | None
    hourly_swing_blocked: bool
    hourly_swing_percent: Decimal | None
    last_flow_condition_met: bool | None
    last_oi_condition_met: bool | None
    last_touch_at: datetime | None


@dataclass(slots=True)
class _FlowBucket:
    opened_at: datetime
    buy_notional: Decimal = Decimal("0")
    sell_notional: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class _OiFeatures:
    short_change_percent: Decimal
    long_change_percent: Decimal
    acceleration: Decimal
    anchor_at: datetime


@dataclass(frozen=True, slots=True)
class _Candidate:
    candidate_id: str
    bar_opened_at: datetime
    bar_reference_price: Decimal
    long_entry: Decimal | None
    short_entry: Decimal | None
    long_gap_percent: Decimal | None
    short_gap_percent: Decimal | None
    oi_features: _OiFeatures | None
    geometry: dict[str, GenericZone]
    source_refs: tuple[str, ...]


@dataclass(slots=True)
class _WatchState:
    candles: dict[str, deque[Candle]] = field(default_factory=dict)
    oi: deque[GenericOiPoint] = field(default_factory=deque)
    flow: dict[datetime, _FlowBucket] = field(default_factory=dict)
    current_bar_open: datetime | None = None
    bar_reference_price: Decimal | None = None
    candidate: _Candidate | None = None
    hourly_swing_blocked: bool = False
    hourly_swing_percent: Decimal | None = None
    last_flow_condition_met: bool | None = None
    last_oi_condition_met: bool | None = None
    last_touch_at: datetime | None = None
    history_ready: bool = False
    traces: list[WatchTracePoint] = field(default_factory=list)


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"invalid {label}") from None
    if not result.is_finite():
        raise ValueError(f"invalid {label}")
    return result


def _integer(value: object, label: str) -> int:
    try:
        result = int(str(value))
    except (ValueError, TypeError):
        raise ValueError(f"invalid {label}") from None
    return result


def _timestamp(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value)
    else:
        raise ValueError(f"invalid {label}")
    if result.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return result.astimezone(UTC)


def _floor_time(timestamp: datetime, minutes: int) -> datetime:
    if timestamp.tzinfo is None or minutes <= 0:
        raise ValueError("floor_time requires aware timestamp and positive minutes")
    utc = timestamp.astimezone(UTC)
    total = utc.hour * 60 + utc.minute
    floored = (total // minutes) * minutes
    hour, minute = divmod(floored, 60)
    return utc.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)


def _compare(left: Decimal, comparator: str, right: Decimal) -> bool:
    op = comparator.upper()
    if op == "GT":
        return left > right
    if op == "GTE":
        return left >= right
    if op == "LT":
        return left < right
    if op == "LTE":
        return left <= right
    if op == "EQ":
        return left == right
    raise ValueError(f"unsupported numeric comparator: {comparator}")


def _policy(plan: EntryPlan) -> dict[str, object]:
    payload = plan.watch_policy.to_dict()
    if "enabled" not in payload:
        raise ValueError("watch_policy requires explicit enabled")
    if not bool(payload["enabled"]):
        if set(payload) != {"enabled"}:
            raise ValueError("disabled watch_policy cannot carry hidden configuration")
        return payload
    required = (
        "candidate_timeframe_minutes",
        "required_closed_timeframes",
        "events",
        "geometry",
        "hourly_swing",
        "direction_rules",
        "direction_precedence",
        "candidate_lifecycle",
        "flow",
        "oi",
        "derived_event_kind",
    )
    missing = tuple(key for key in required if key not in payload)
    if missing:
        raise ValueError(f"enabled watch_policy missing explicit fields: {missing}")
    return payload


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a list")
    return value


def _zone_gap_percent(
    first_low: Decimal,
    first_high: Decimal,
    second_low: Decimal,
    second_high: Decimal,
    reference: Decimal,
) -> Decimal:
    if reference <= 0:
        raise ValueError("reference price must be positive")
    if max(first_low, second_low) <= min(first_high, second_high):
        return Decimal("0")
    gap = second_low - first_high if first_high < second_low else first_low - second_high
    return abs(gap) / reference * Decimal("100")


def _compute_range_atr_zone(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    lookback: int,
    atr_period: int,
    width_atr: Decimal,
    shock_period: int,
    shock_multiple: Decimal,
    maturity_minutes: int,
    shock_mode: str = "ATR_MULTIPLE",
    shock_threshold_percent: Decimal | None = None,
) -> GenericZone | None:
    count = len(candles)
    if count < max(lookback, atr_period):
        return None
    ranges = true_ranges(candles)
    atr_values = wilder_atr(candles, atr_period)
    atr = atr_values[-1]
    if atr is None or atr <= 0:
        return None
    shock_flags = [False] * count
    if shock_mode == "ATR_MULTIPLE":
        if shock_period <= 0:
            raise ValueError("ATR_MULTIPLE shock requires positive tr_period")
        if shock_multiple <= 0:
            raise ValueError("ATR_MULTIPLE shock requires positive multiple")
        if count > shock_period:
            rolling = sum(ranges[:shock_period], Decimal("0"))
            for index in range(shock_period, count):
                baseline = rolling / Decimal(shock_period)
                shock_flags[index] = baseline > 0 and ranges[index] >= shock_multiple * baseline
                rolling += ranges[index] - ranges[index - shock_period]
        shock_search_floor = shock_period
    elif shock_mode == "RANGE_PERCENT":
        if shock_threshold_percent is None or shock_threshold_percent <= 0:
            raise ValueError("RANGE_PERCENT shock requires positive threshold percent")
        for index in range(1, count):
            previous_close = candles[index - 1].close
            if previous_close > 0:
                move_percent = ranges[index] / previous_close * Decimal("100")
                shock_flags[index] = move_percent >= shock_threshold_percent
        shock_search_floor = 1
    elif shock_mode == "OFF":
        shock_search_floor = 0
    else:
        raise ValueError(f"unsupported shock mode: {shock_mode}")
    history_len = count
    last_index = history_len - 1
    window_start = history_len - lookback
    reset_index: int | None = None
    if shock_mode != "OFF":
        for index in range(last_index, max(window_start, shock_search_floor) - 1, -1):
            if shock_flags[index]:
                reset_index = index
                break
    selected_start = window_start
    reset_at: datetime | None = None
    timeframe_minutes = _integer(timeframe, "geometry timeframe")
    minimum_regime_bars = max(1, maturity_minutes // timeframe_minutes)
    if reset_index is not None:
        selected_start = reset_index + 1
        if history_len - selected_start < minimum_regime_bars:
            return None
        reset_at = candles[reset_index].closed_at
    selected = candles[selected_start:history_len]
    if not selected:
        return None
    range_high = max(item.high for item in selected)
    range_low = min(item.low for item in selected)
    width = width_atr * atr
    support_top = range_low + width
    support_bottom = range_low - width
    resistance_top = range_high + width
    resistance_bottom = range_high - width
    if support_top >= resistance_bottom:
        return None
    return GenericZone(
        timeframe=timeframe,
        observed_at=candles[last_index].closed_at,
        range_high=range_high,
        range_low=range_low,
        atr=atr,
        resistance_top=resistance_top,
        resistance_bottom=resistance_bottom,
        support_top=support_top,
        support_bottom=support_bottom,
        effective_lookback=len(selected),
        regime_reset_at=reset_at,
    )


def _directional_delta(direction: TradeDirection, buy: Decimal, sell: Decimal) -> Decimal:
    total = buy + sell
    if total <= 0:
        return Decimal("0")
    raw = (buy - sell) / total * Decimal("100")
    return raw if direction is TradeDirection.LONG else -raw


def _percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return (current / previous - Decimal("1")) * Decimal("100")


def _derived_history_limit(policy: Mapping[str, object]) -> int:
    explicit = policy.get("history_limit")
    if explicit is not None:
        result = _integer(explicit, "history_limit")
        if result <= 0:
            raise ValueError("history_limit must be positive")
        return result
    geometry = _mapping(policy.get("geometry"), "geometry")
    lookbacks = _mapping(geometry.get("lookback_by_timeframe"), "geometry.lookback_by_timeframe")
    candidates = [_integer(value, f"lookback {key}") for key, value in lookbacks.items()]
    if geometry.get("atr_period") is not None:
        candidates.append(_integer(geometry.get("atr_period"), "atr_period"))
    shock = geometry.get("shock_reset_policy") or geometry.get("shock")
    if (
        isinstance(shock, Mapping)
        and bool(shock.get("enabled", True))
        and shock.get("tr_period") is not None
    ):
        candidates.append(_integer(shock.get("tr_period"), "shock tr_period"))
    swing = policy.get("hourly_swing")
    if isinstance(swing, Mapping) and bool(swing.get("enabled")):
        candidates.append(_integer(swing.get("window_bars"), "hourly_swing window_bars"))
    return max(candidates, default=1) + 2


class ParameterizedCausalMarketWatch:
    """Entry-plan-specific interpretation of already normalized causal market facts.

    It does not connect to an exchange, normalize feeds, choose a Strategy, or own
    shared market-data collection. All trading numbers arrive through EntryPlan.
    """

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], _WatchState] = {}

    def _state(self, plan: EntryPlan, symbol: str) -> _WatchState:
        key = (plan.entry_plan_fingerprint, symbol)
        state = self._states.get(key)
        if state is None:
            state = _WatchState()
            self._states[key] = state
        return state

    def load_history(
        self,
        plan: EntryPlan,
        symbol: str,
        candles: Mapping[str, tuple[Candle, ...]],
        oi_points: tuple[GenericOiPoint, ...],
        *,
        observed_at: datetime,
        allow_candidate: bool = True,
    ) -> None:
        policy = _policy(plan)
        if not bool(policy["enabled"]):
            return
        if symbol not in plan.symbols:
            raise ValueError("watch history symbol is outside EntryPlan scope")
        history_limit = _derived_history_limit(policy)
        state = self._state(plan, symbol)
        state.candles.clear()
        required = tuple(
            str(item)
            for item in _sequence(
                policy["required_closed_timeframes"], "required_closed_timeframes"
            )
        )
        geometry = _mapping(policy["geometry"], "geometry")
        geometry_timeframes = tuple(
            str(item) for item in _sequence(geometry.get("timeframes"), "geometry.timeframes")
        )
        for timeframe in dict.fromkeys(required + geometry_timeframes):
            target: deque[Candle] = deque(maxlen=history_limit)
            for candle in sorted(candles.get(timeframe, ()), key=lambda item: item.opened_at):
                if candle.symbol != symbol or candle.timeframe != timeframe:
                    raise ValueError("normalized candle symbol/timeframe mismatch")
                if candle.is_closed:
                    target.append(candle)
            state.candles[timeframe] = target
        state.oi = deque(sorted(oi_points, key=lambda item: item.timestamp), maxlen=history_limit)
        state.flow.clear()
        state.current_bar_open = None
        state.bar_reference_price = None
        state.candidate = None
        state.hourly_swing_blocked = False
        state.hourly_swing_percent = None
        state.last_flow_condition_met = None
        state.last_oi_condition_met = None
        state.last_touch_at = None
        state.history_ready = True
        candidate_minutes = _integer(
            policy["candidate_timeframe_minutes"], "candidate_timeframe_minutes"
        )
        self._ensure_candidate(
            plan,
            state,
            symbol,
            _floor_time(observed_at, candidate_minutes),
            observed_at=observed_at,
            allow_candidate=allow_candidate,
        )

    def process(
        self,
        plan: EntryPlan,
        fact: MarketFactEnvelope,
        *,
        allow_candidate: bool,
    ) -> tuple[MarketFactEnvelope, ...]:
        policy = _policy(plan)
        if not bool(policy["enabled"]):
            return (fact,)
        state = self._state(plan, fact.symbol)
        if not state.history_ready:
            return ()
        events = _mapping(policy["events"], "events")
        kind = fact.event_kind
        if kind == str(events.get("candle_closed")):
            candle = self._candle_from_fact(fact)
            self._on_closed_candle(plan, state, candle, fact.observed_at, allow_candidate)
            return ()
        if kind == str(events.get("bar_open")):
            attrs = fact.attributes.to_dict()
            opened_at = _timestamp(attrs.get("opened_at"), "bar opened_at")
            open_price = _decimal(attrs.get("open_price"), "bar open_price")
            state.current_bar_open = opened_at
            state.bar_reference_price = open_price
            self._ensure_candidate(
                plan,
                state,
                fact.symbol,
                opened_at,
                observed_at=fact.observed_at,
                allow_candidate=allow_candidate,
            )
            return ()
        if kind == str(events.get("open_interest")):
            attrs = fact.attributes.to_dict()
            value = _decimal(attrs.get("open_interest"), "open_interest")
            if value > 0 and (not state.oi or fact.observed_at > state.oi[-1].timestamp):
                state.oi.append(GenericOiPoint(fact.observed_at, value))
            return ()
        if kind == str(events.get("trade")):
            return self._on_trade(plan, state, fact, allow_candidate)
        return ()

    def _candle_from_fact(self, fact: MarketFactEnvelope) -> Candle:
        attrs = fact.attributes.to_dict()
        timeframe = str(attrs.get("timeframe") or "")
        return Candle(
            symbol=fact.symbol,
            timeframe=timeframe,
            opened_at=_timestamp(attrs.get("opened_at"), "candle opened_at"),
            closed_at=_timestamp(attrs.get("closed_at"), "candle closed_at"),
            open=_decimal(attrs.get("open"), "candle open"),
            high=_decimal(attrs.get("high"), "candle high"),
            low=_decimal(attrs.get("low"), "candle low"),
            close=_decimal(attrs.get("close"), "candle close"),
            volume=_decimal(attrs.get("volume"), "candle volume"),
        )

    def _on_closed_candle(
        self,
        plan: EntryPlan,
        state: _WatchState,
        candle: Candle,
        observed_at: datetime,
        allow_candidate: bool,
    ) -> None:
        policy = _policy(plan)
        target = state.candles.get(candle.timeframe)
        if target is None:
            target = deque(maxlen=_derived_history_limit(policy))
            state.candles[candle.timeframe] = target
        if target and target[-1].opened_at == candle.opened_at:
            target[-1] = candle
        elif not target or target[-1].opened_at < candle.opened_at:
            target.append(candle)
        else:
            merged = {item.opened_at: item for item in target}
            merged[candle.opened_at] = candle
            target.clear()
            target.extend(sorted(merged.values(), key=lambda item: item.opened_at))
        candidate_minutes = _integer(
            policy["candidate_timeframe_minutes"], "candidate_timeframe_minutes"
        )
        candidate_tf = str(candidate_minutes)
        current = state.current_bar_open
        if candle.timeframe == candidate_tf and (current is None or candle.closed_at > current):
            state.current_bar_open = candle.closed_at
            state.bar_reference_price = candle.close
            current = candle.closed_at
        if current is None or candle.closed_at >= current:
            bar_open = max(candle.closed_at, current or candle.closed_at)
            self._ensure_candidate(
                plan,
                state,
                candle.symbol,
                bar_open,
                observed_at=observed_at,
                allow_candidate=allow_candidate,
            )

    def _record_flow(
        self, state: _WatchState, fact: MarketFactEnvelope, policy: Mapping[str, object]
    ) -> tuple[Decimal, str]:
        attrs = fact.attributes.to_dict()
        price = _decimal(attrs.get("price"), "trade price")
        size = _decimal(attrs.get("size"), "trade size")
        side = str(attrs.get("taker_side") or "")
        if price <= 0 or size < 0 or side not in {"Buy", "Sell"}:
            raise ValueError("normalized trade is invalid")
        minute = fact.observed_at.astimezone(UTC).replace(second=0, microsecond=0)
        bucket = state.flow.get(minute)
        if bucket is None:
            bucket = _FlowBucket(minute)
            state.flow[minute] = bucket
        notional = price * size
        if side == "Buy":
            bucket.buy_notional += notional
        else:
            bucket.sell_notional += notional
        flow_policy = _mapping(policy["flow"], "flow")
        all_offsets = [
            _integer(item, "flow offset")
            for item in _sequence(
                flow_policy.get("required_offsets_minutes"), "flow.required_offsets_minutes"
            )
        ]
        retention = max(all_offsets, default=0) + 5
        cutoff = minute - timedelta(minutes=retention)
        for key in tuple(state.flow):
            if key < cutoff:
                del state.flow[key]
        return price, side

    def _flow_result(
        self,
        state: _WatchState,
        direction: TradeDirection,
        touch_at: datetime,
        flow_policy: Mapping[str, object],
    ) -> tuple[bool, bool, Decimal, Decimal]:
        if not bool(flow_policy.get("enabled")):
            return True, True, Decimal("0"), Decimal("0")
        if str(flow_policy.get("operator")) != "DIRECTIONAL_NOTIONAL_DELTA":
            raise ValueError("unsupported flow operator")
        normalized = touch_at.astimezone(UTC).replace(second=0, microsecond=0)
        required_offsets = tuple(
            _integer(item, "required flow offset")
            for item in _sequence(
                flow_policy.get("required_offsets_minutes"), "flow.required_offsets_minutes"
            )
        )
        ready = all(
            normalized - timedelta(minutes=offset) in state.flow for offset in required_offsets
        )
        pressure_offsets = tuple(
            _integer(item, "pressure flow offset")
            for item in _sequence(
                flow_policy.get("pressure_offsets_minutes"), "flow.pressure_offsets_minutes"
            )
        )
        reversal_offsets = tuple(
            _integer(item, "reversal flow offset")
            for item in _sequence(
                flow_policy.get("reversal_offsets_minutes"), "flow.reversal_offsets_minutes"
            )
        )
        pressure_buy = Decimal("0")
        pressure_sell = Decimal("0")
        for offset in pressure_offsets:
            bucket = state.flow.get(normalized - timedelta(minutes=offset))
            if bucket is not None:
                pressure_buy += bucket.buy_notional
                pressure_sell += bucket.sell_notional
        reversal_buy = Decimal("0")
        reversal_sell = Decimal("0")
        for offset in reversal_offsets:
            bucket = state.flow.get(normalized - timedelta(minutes=offset))
            if bucket is not None:
                reversal_buy += bucket.buy_notional
                reversal_sell += bucket.sell_notional
        pressure = _directional_delta(direction, pressure_buy, pressure_sell)
        reversal = _directional_delta(direction, reversal_buy, reversal_sell)
        condition = _mapping(flow_policy.get("condition"), "flow.condition")
        pressure_rule = _mapping(condition.get("pressure"), "flow.condition.pressure")
        reversal_rule = _mapping(condition.get("reversal"), "flow.condition.reversal")
        matched = (
            ready
            and _compare(
                pressure,
                str(pressure_rule.get("comparator")),
                _decimal(pressure_rule.get("value"), "flow pressure threshold"),
            )
            and _compare(
                reversal,
                str(reversal_rule.get("comparator")),
                _decimal(reversal_rule.get("value"), "flow reversal threshold"),
            )
        )
        return ready, matched, pressure, reversal

    def _oi_features(
        self,
        state: _WatchState,
        anchor_at: datetime,
        oi_policy: Mapping[str, object],
    ) -> _OiFeatures | None:
        if not bool(oi_policy.get("enabled")):
            return None
        if str(oi_policy.get("operator")) != "PERCENT_CHANGE_ACCELERATION":
            raise ValueError("unsupported OI operator")
        ordered = tuple(sorted(state.oi, key=lambda item: item.timestamp))

        def at_or_before(timestamp: datetime) -> GenericOiPoint | None:
            for item in reversed(ordered):
                if item.timestamp <= timestamp:
                    return item
            return None

        short_offset = _integer(oi_policy.get("short_offset_minutes"), "OI short offset")
        long_offset = _integer(oi_policy.get("long_offset_minutes"), "OI long offset")
        current = at_or_before(anchor_at)
        short = at_or_before(anchor_at - timedelta(minutes=short_offset))
        long = at_or_before(anchor_at - timedelta(minutes=long_offset))
        if current is None or short is None or long is None:
            return None
        short_change = _percent_change(current.open_interest, short.open_interest)
        long_change = _percent_change(current.open_interest, long.open_interest)
        if short_change is None or long_change is None:
            return None
        divisor = _decimal(oi_policy.get("acceleration_divisor"), "OI acceleration divisor")
        if divisor == 0:
            raise ValueError("OI acceleration divisor cannot be zero")
        return _OiFeatures(
            short_change,
            long_change,
            short_change - long_change / divisor,
            current.timestamp,
        )

    def _oi_result(
        self,
        plan: EntryPlan,
        symbol: str,
        features: _OiFeatures | None,
        oi_policy: Mapping[str, object],
    ) -> tuple[bool, bool, bool | None]:
        if not bool(oi_policy.get("enabled")):
            return True, True, None
        rows = _mapping(oi_policy.get("calibration_rows"), "oi.calibration_rows")
        row_raw = rows.get(symbol)
        row = row_raw if isinstance(row_raw, Mapping) else None
        required = bool(oi_policy.get("required"))
        if row is None or features is None:
            return (not required, not required, None)
        values = {
            "short_change_percent": features.short_change_percent,
            "long_change_percent": features.long_change_percent,
            "acceleration": features.acceleration,
        }
        outcomes: list[bool] = []
        for raw_rule in _sequence(oi_policy.get("danger_rules"), "oi.danger_rules"):
            rule = _mapping(raw_rule, "oi danger rule")
            field = str(rule.get("field") or "")
            threshold_field = str(rule.get("threshold_field") or "")
            if field not in values or threshold_field not in row:
                raise ValueError("OI danger rule references unknown field")
            outcomes.append(
                _compare(
                    values[field],
                    str(rule.get("comparator")),
                    _decimal(row[threshold_field], f"OI calibration {threshold_field}"),
                )
            )
        combine = str(oi_policy.get("danger_combine") or "")
        if combine == "OR":
            danger = any(outcomes)
        elif combine == "AND":
            danger = all(outcomes)
        else:
            raise ValueError("unsupported OI danger_combine")
        return True, not danger, danger

    def _on_trade(
        self,
        plan: EntryPlan,
        state: _WatchState,
        fact: MarketFactEnvelope,
        allow_candidate: bool,
    ) -> tuple[MarketFactEnvelope, ...]:
        policy = _policy(plan)
        price, _ = self._record_flow(state, fact, policy)
        candidate_minutes = _integer(
            policy["candidate_timeframe_minutes"], "candidate_timeframe_minutes"
        )
        bar_open = _floor_time(fact.observed_at, candidate_minutes)
        if state.current_bar_open != bar_open:
            state.current_bar_open = bar_open
            state.bar_reference_price = price
            self._ensure_candidate(
                plan,
                state,
                fact.symbol,
                bar_open,
                observed_at=fact.observed_at,
                allow_candidate=allow_candidate,
            )
        elif not allow_candidate and state.candidate is not None:
            self._clear_candidate(state, fact.observed_at, "candidate lifecycle blocked")
        candidate = state.candidate
        if candidate is None or candidate.bar_opened_at != bar_open or not allow_candidate:
            return ()
        direction_rules = _mapping(policy["direction_rules"], "direction_rules")
        precedence = tuple(
            str(item) for item in _sequence(policy["direction_precedence"], "direction_precedence")
        )
        selected: tuple[TradeDirection, Decimal, Decimal] | None = None
        for direction_name in precedence:
            try:
                direction = TradeDirection(direction_name)
            except ValueError:
                raise ValueError(f"unsupported direction in precedence: {direction_name}") from None
            if direction not in plan.directions:
                continue
            rule = _mapping(
                direction_rules.get(direction_name), f"direction_rules.{direction_name}"
            )
            entry = (
                candidate.long_entry if direction is TradeDirection.LONG else candidate.short_entry
            )
            gap = (
                candidate.long_gap_percent
                if direction is TradeDirection.LONG
                else candidate.short_gap_percent
            )
            if entry is None or gap is None:
                continue
            if _compare(price, str(rule.get("touch_comparator")), entry):
                selected = (direction, entry, gap)
                break
        if selected is None:
            return ()
        direction, entry, gap = selected
        flow_policy = _mapping(policy["flow"], "flow")
        flow_ready, flow_match, pressure, reversal = self._flow_result(
            state, direction, fact.observed_at, flow_policy
        )
        oi_policy = _mapping(policy["oi"], "oi")
        oi_ready, oi_match, oi_danger = self._oi_result(
            plan, fact.symbol, candidate.oi_features, oi_policy
        )
        state.last_flow_condition_met = flow_match
        state.last_oi_condition_met = oi_match
        state.last_touch_at = fact.observed_at
        geometry_payload = {
            timeframe: {
                "observed_at": zone.observed_at,
                "range_high": zone.range_high,
                "range_low": zone.range_low,
                "atr": zone.atr,
                "resistance_top": zone.resistance_top,
                "resistance_bottom": zone.resistance_bottom,
                "support_top": zone.support_top,
                "support_bottom": zone.support_bottom,
                "effective_lookback": zone.effective_lookback,
                "regime_reset_at": zone.regime_reset_at,
            }
            for timeframe, zone in candidate.geometry.items()
        }
        attrs = {
            "candidate_id": candidate.candidate_id,
            "direction": direction.value,
            "candidate_bar_at": candidate.bar_opened_at,
            "entry_price": entry,
            "price": price,
            "zone_gap_percent": gap,
            "geometry": geometry_payload,
            "hourly_swing_blocked": state.hourly_swing_blocked,
            "hourly_swing_percent": state.hourly_swing_percent,
            "flow_ready": flow_ready,
            "flow_condition_met": flow_match,
            "flow_pressure_delta": pressure,
            "flow_reversal_delta": reversal,
            "oi_ready": oi_ready,
            "oi_condition_met": oi_match,
            "oi_danger": oi_danger,
        }
        derived_id = (
            "watch-"
            + fingerprint(
                {
                    "plan": plan.entry_plan_fingerprint,
                    "source_fact": fact.fact_id,
                    "candidate": candidate.candidate_id,
                    "direction": direction,
                }
            )[:32]
        )
        derived = MarketFactEnvelope(
            fact_id=derived_id,
            event_kind=str(policy["derived_event_kind"]),
            symbol=fact.symbol,
            observed_at=fact.observed_at,
            event_at=fact.event_at,
            received_at=fact.received_at,
            source_refs=tuple(dict.fromkeys(fact.source_refs + candidate.source_refs)),
            attributes=FrozenPolicy.from_mapping(attrs),
            direction=direction,
        )
        state.traces.append(
            WatchTracePoint(
                "touch",
                candidate.candidate_id,
                fact.observed_at,
                FrozenPolicy.from_mapping(attrs),
            )
        )
        lifecycle = _mapping(policy["candidate_lifecycle"], "candidate_lifecycle")
        if bool(lifecycle.get("clear_on_touch")):
            self._clear_candidate(state, fact.observed_at, "touch")
        return (derived,)

    def _clear_candidate(self, state: _WatchState, observed_at: datetime, reason: str) -> None:
        previous = state.candidate
        if previous is None:
            return
        state.traces.append(
            WatchTracePoint(
                "candidate_cleared",
                previous.candidate_id,
                observed_at,
                FrozenPolicy.from_mapping({"reason": reason}),
            )
        )
        state.candidate = None

    def _ensure_candidate(
        self,
        plan: EntryPlan,
        state: _WatchState,
        symbol: str,
        bar_open: datetime,
        *,
        observed_at: datetime,
        allow_candidate: bool,
    ) -> None:
        policy = _policy(plan)
        if not allow_candidate:
            self._clear_candidate(state, observed_at, "candidate lifecycle blocked")
            return
        required = tuple(
            str(item)
            for item in _sequence(
                policy["required_closed_timeframes"], "required_closed_timeframes"
            )
        )
        for timeframe in required:
            rows = state.candles.get(timeframe)
            expected_close = _floor_time(bar_open, _integer(timeframe, "required timeframe"))
            if not rows or rows[-1].closed_at < expected_close:
                self._clear_candidate(state, observed_at, f"{timeframe} history not closed")
                return
        geometry_policy = _mapping(policy["geometry"], "geometry")
        if str(geometry_policy.get("operator")) != "RANGE_ATR_CONFLUENCE":
            raise ValueError("unsupported geometry operator")
        timeframes = tuple(
            str(item)
            for item in _sequence(geometry_policy.get("timeframes"), "geometry.timeframes")
        )
        if len(timeframes) != 2:
            raise ValueError("RANGE_ATR_CONFLUENCE requires exactly two timeframes")
        primary = str(geometry_policy.get("primary_timeframe") or "")
        confirming = str(geometry_policy.get("confirming_timeframe") or "")
        if {primary, confirming} != set(timeframes):
            raise ValueError("geometry primary/confirming timeframes must match timeframes")
        swing = _mapping(policy["hourly_swing"], "hourly_swing")
        state.hourly_swing_blocked = False
        state.hourly_swing_percent = None
        if bool(swing.get("enabled")):
            if str(swing.get("operator")) != "ROLLING_RANGE_PERCENT":
                raise ValueError("unsupported hourly_swing operator")
            timeframe = str(swing.get("timeframe") or "")
            window = _integer(swing.get("window_bars"), "hourly_swing window_bars")
            swing_rows = tuple(state.candles.get(timeframe, ()))
            recent = swing_rows[-window:]
            if len(recent) == window:
                low = min(item.low for item in recent)
                high = max(item.high for item in recent)
                if low > 0:
                    value = (high / low - Decimal("1")) * Decimal("100")
                    state.hourly_swing_percent = value
                    state.hourly_swing_blocked = _compare(
                        value,
                        str(swing.get("comparator")),
                        _decimal(swing.get("threshold_percent"), "hourly_swing threshold"),
                    )
        if state.hourly_swing_blocked:
            self._clear_candidate(state, observed_at, "rolling swing gate")
            return
        lookback_map = _mapping(
            geometry_policy.get("lookback_by_timeframe"), "geometry.lookback_by_timeframe"
        )
        shock_reset = geometry_policy.get("shock_reset_policy")
        legacy_shock = geometry_policy.get("shock")
        zones: dict[str, GenericZone] = {}
        for timeframe in timeframes:
            if shock_reset is not None:
                shock = _mapping(shock_reset, "geometry.shock_reset_policy")
                shock_enabled = bool(shock.get("enabled"))
                shock_mode = str(shock.get("detection_mode") or "") if shock_enabled else "OFF"
                maturity_minutes = (
                    _integer(shock.get("maturity_minutes"), "shock maturity_minutes")
                    if shock_enabled
                    else 0
                )
                if shock_mode == "ATR_MULTIPLE":
                    shock_period = _integer(shock.get("tr_period"), "shock tr_period")
                    shock_multiple = _decimal(shock.get("multiple"), "shock multiple")
                    threshold_percent = None
                elif shock_mode == "RANGE_PERCENT":
                    threshold_map = _mapping(
                        shock.get("threshold_percent_by_timeframe"),
                        "shock threshold_percent_by_timeframe",
                    )
                    threshold_percent = _decimal(
                        threshold_map.get(timeframe), f"shock threshold {timeframe}"
                    )
                    shock_period = 1
                    shock_multiple = Decimal("1")
                elif shock_mode == "OFF":
                    shock_period = 1
                    shock_multiple = Decimal("1")
                    threshold_percent = None
                else:
                    raise ValueError(f"unsupported shock_reset_policy mode: {shock_mode}")
            else:
                shock = _mapping(legacy_shock, "geometry.shock")
                shock_mode = "ATR_MULTIPLE"
                shock_period = _integer(shock.get("tr_period"), "shock tr_period")
                shock_multiple = _decimal(shock.get("multiple"), "shock multiple")
                maturity_minutes = _integer(shock.get("maturity_minutes"), "shock maturity_minutes")
                threshold_percent = None
            geometry_rows = tuple(state.candles.get(timeframe, ()))
            zone = _compute_range_atr_zone(
                geometry_rows,
                timeframe=timeframe,
                lookback=_integer(lookback_map.get(timeframe), f"lookback {timeframe}"),
                atr_period=_integer(geometry_policy.get("atr_period"), "atr_period"),
                width_atr=_decimal(
                    geometry_policy.get("zone_half_width_atr"), "zone_half_width_atr"
                ),
                shock_period=shock_period,
                shock_multiple=shock_multiple,
                maturity_minutes=maturity_minutes,
                shock_mode=shock_mode,
                shock_threshold_percent=threshold_percent,
            )
            if zone is None:
                self._clear_candidate(state, observed_at, "causal geometry unavailable")
                return
            zones[timeframe] = zone
        reference = state.bar_reference_price
        primary_rows = state.candles.get(primary)
        if reference is None or reference <= 0:
            if not primary_rows:
                self._clear_candidate(state, observed_at, "bar reference unavailable")
                return
            reference = primary_rows[-1].close
        first = zones[confirming]
        second = zones[primary]
        long_gap = _zone_gap_percent(
            first.support_bottom,
            first.support_top,
            second.support_bottom,
            second.support_top,
            reference,
        )
        short_gap = _zone_gap_percent(
            first.resistance_bottom,
            first.resistance_top,
            second.resistance_bottom,
            second.resistance_top,
            reference,
        )
        max_gap = _decimal(
            geometry_policy.get("confluence_max_gap_percent"), "confluence_max_gap_percent"
        )
        direction_rules = _mapping(policy["direction_rules"], "direction_rules")
        long_entry: Decimal | None = None
        short_entry: Decimal | None = None
        if TradeDirection.LONG in plan.directions and long_gap <= max_gap:
            rule = _mapping(direction_rules.get("LONG"), "direction_rules.LONG")
            field = str(rule.get("entry_zone_field") or "")
            if not hasattr(zones[primary], field):
                raise ValueError("LONG entry_zone_field is unknown")
            long_entry = _decimal(getattr(zones[primary], field), "LONG entry level")
        if TradeDirection.SHORT in plan.directions and short_gap <= max_gap:
            rule = _mapping(direction_rules.get("SHORT"), "direction_rules.SHORT")
            field = str(rule.get("entry_zone_field") or "")
            if not hasattr(zones[primary], field):
                raise ValueError("SHORT entry_zone_field is unknown")
            short_entry = _decimal(getattr(zones[primary], field), "SHORT entry level")
        if long_entry is None and short_entry is None:
            self._clear_candidate(state, observed_at, "confluence absent")
            return
        oi_policy = _mapping(policy["oi"], "oi")
        oi_features = self._oi_features(state, bar_open, oi_policy)
        source_refs = tuple(
            f"candle:{symbol}:{timeframe}:{state.candles[timeframe][-1].opened_at.isoformat()}"
            for timeframe in timeframes
            if state.candles.get(timeframe)
        )
        candidate_id = (
            "candidate-"
            + fingerprint(
                {
                    "plan": plan.entry_plan_fingerprint,
                    "symbol": symbol,
                    "bar_open": bar_open,
                    "reference": reference,
                    "long_entry": long_entry,
                    "short_entry": short_entry,
                    "long_gap": long_gap,
                    "short_gap": short_gap,
                    "geometry": zones,
                }
            )[:24]
        )
        candidate = _Candidate(
            candidate_id,
            bar_open,
            reference,
            long_entry,
            short_entry,
            long_gap if long_entry is not None else None,
            short_gap if short_entry is not None else None,
            oi_features,
            zones,
            source_refs,
        )
        if state.candidate == candidate:
            return
        state.candidate = candidate
        state.current_bar_open = bar_open
        state.traces.append(
            WatchTracePoint(
                "candidate",
                candidate_id,
                observed_at,
                FrozenPolicy.from_mapping(
                    {
                        "candidate_bar_at": bar_open,
                        "long_entry": long_entry,
                        "short_entry": short_entry,
                        "long_gap_percent": candidate.long_gap_percent,
                        "short_gap_percent": candidate.short_gap_percent,
                        "geometry": {
                            tf: {
                                "support_top": zone.support_top,
                                "support_bottom": zone.support_bottom,
                                "resistance_top": zone.resistance_top,
                                "resistance_bottom": zone.resistance_bottom,
                                "atr": zone.atr,
                                "regime_reset_at": zone.regime_reset_at,
                            }
                            for tf, zone in zones.items()
                        },
                        "hourly_swing_percent": state.hourly_swing_percent,
                    }
                ),
            )
        )

    def snapshot(self, entry_plan_fingerprint: str, symbol: str) -> MarketWatchSnapshot:
        state = self._states.get((entry_plan_fingerprint, symbol))
        if state is None:
            return MarketWatchSnapshot(
                None, None, {}, None, None, None, None, False, None, None, None, None
            )
        candidate = state.candidate
        return MarketWatchSnapshot(
            candidate_id=None if candidate is None else candidate.candidate_id,
            candidate_bar_at=None if candidate is None else candidate.bar_opened_at,
            geometry={} if candidate is None else dict(candidate.geometry),
            long_entry=None if candidate is None else candidate.long_entry,
            short_entry=None if candidate is None else candidate.short_entry,
            long_gap_percent=None if candidate is None else candidate.long_gap_percent,
            short_gap_percent=None if candidate is None else candidate.short_gap_percent,
            hourly_swing_blocked=state.hourly_swing_blocked,
            hourly_swing_percent=state.hourly_swing_percent,
            last_flow_condition_met=state.last_flow_condition_met,
            last_oi_condition_met=state.last_oi_condition_met,
            last_touch_at=state.last_touch_at,
        )

    def drain_trace(self, entry_plan_fingerprint: str, symbol: str) -> tuple[WatchTracePoint, ...]:
        state = self._states.get((entry_plan_fingerprint, symbol))
        if state is None:
            return ()
        rows = tuple(state.traces)
        state.traces.clear()
        return rows

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ParityPoint:
    category: str
    causal_key: str
    value: object


@dataclass(frozen=True, slots=True)
class ParityMismatch:
    category: str
    causal_key: str
    legacy_value: object
    universal_value: object
    index: int


@dataclass(frozen=True, slots=True)
class ParityReport:
    passed: bool
    compared_points: int
    legacy_points: int
    universal_points: int
    first_mismatch: ParityMismatch | None


def compare_parity_points(
    legacy: tuple[ParityPoint, ...],
    universal: tuple[ParityPoint, ...],
) -> ParityReport:
    shared = min(len(legacy), len(universal))
    for index in range(shared):
        left = legacy[index]
        right = universal[index]
        if (
            left.category != right.category
            or left.causal_key != right.causal_key
            or left.value != right.value
        ):
            category = left.category if left.category == right.category else "SEQUENCE_CATEGORY"
            key = left.causal_key if left.causal_key == right.causal_key else "SEQUENCE_CAUSAL_KEY"
            return ParityReport(
                False,
                index,
                len(legacy),
                len(universal),
                ParityMismatch(category, key, left.value, right.value, index),
            )
    if len(legacy) != len(universal):
        left_extra: Any = legacy[shared] if len(legacy) > shared else None
        right_extra: Any = universal[shared] if len(universal) > shared else None
        category = (
            left_extra.category
            if isinstance(left_extra, ParityPoint)
            else right_extra.category
            if isinstance(right_extra, ParityPoint)
            else "SEQUENCE_LENGTH"
        )
        key = (
            left_extra.causal_key
            if isinstance(left_extra, ParityPoint)
            else right_extra.causal_key
            if isinstance(right_extra, ParityPoint)
            else "UNKNOWN"
        )
        return ParityReport(
            False,
            shared,
            len(legacy),
            len(universal),
            ParityMismatch(category, key, left_extra, right_extra, shared),
        )
    return ParityReport(True, shared, len(legacy), len(universal), None)


@dataclass(frozen=True, slots=True)
class StatefulParityResult:
    report: ParityReport
    legacy_points: tuple[ParityPoint, ...]
    universal_points: tuple[ParityPoint, ...]


class V1DeterministicParityRunner:
    """Compatibility-only deterministic replay against canonical legacy EntrySymbolEngine.

    It consumes normalized domain candles and MarketFactEnvelope inputs. It does not use
    the installed shadow scanner, does not connect to an exchange, and cannot mutate orders.
    """

    def __init__(
        self,
        bundle: object,
        *,
        symbol: str,
        candles: dict[str, tuple[object, ...]],
        oi_points: tuple[object, ...],
        observed_at: object,
    ) -> None:
        from datetime import datetime
        from decimal import Decimal

        from bybit_workbench.domain.models import Candle
        from bybit_workbench.entry_bot.config import EntryBotConfig
        from bybit_workbench.entry_bot.engine import EntrySymbolEngine, OiPoint
        from bybit_workbench.entry_bot.models import EntryBotCalibration
        from bybit_workbench.universal_entry import ActivePlanRegistry, UniversalEntryEngine
        from bybit_workbench.universal_entry.market_watch import GenericOiPoint

        from .v1_compat import V1CompatibilityBundle

        if not isinstance(bundle, V1CompatibilityBundle):
            raise TypeError("V1DeterministicParityRunner requires V1CompatibilityBundle")
        if not isinstance(observed_at, datetime):
            raise TypeError("observed_at must be datetime")
        typed_candles: dict[str, tuple[Candle, ...]] = {}
        for timeframe, rows in candles.items():
            if not all(isinstance(row, Candle) for row in rows):
                raise TypeError("parity history requires normalized Candle objects")
            typed_candles[timeframe] = tuple(row for row in rows if isinstance(row, Candle))
        generic_oi: list[GenericOiPoint] = []
        legacy_oi: list[OiPoint] = []
        for point in oi_points:
            timestamp = getattr(point, "timestamp", None)
            value = getattr(point, "open_interest", None)
            if not isinstance(timestamp, datetime):
                raise TypeError("parity OI point requires timestamp")
            try:
                decimal_value = Decimal(str(value))
            except Exception as exc:
                raise TypeError("parity OI point requires numeric open_interest") from exc
            generic_oi.append(GenericOiPoint(timestamp, decimal_value))
            legacy_oi.append(OiPoint(timestamp, decimal_value))
        row_raw = bundle.calibration_rows.get(symbol)
        if not isinstance(row_raw, dict):
            raise ValueError(f"compatibility calibration row missing: {symbol}")
        calibration = EntryBotCalibration(
            symbol=symbol,
            high_oi_change_60m_pct=Decimal(str(row_raw["high_oi_change_60m_pct"])),
            low_oi_acceleration_5_vs_60=Decimal(str(row_raw["low_oi_acceleration_5_vs_60"])),
            source_period=str(row_raw["source_period"]),
            source_summary_sha256=str(row_raw["source_summary_sha256"]),
        )
        self.symbol = symbol
        self.bundle = bundle
        self.legacy = EntrySymbolEngine(symbol, EntryBotConfig(), calibration)
        self.legacy.load_history(typed_candles, tuple(legacy_oi), observed_at=observed_at)
        registry = ActivePlanRegistry()
        registry.register_card(bundle.card)
        self.plan = registry.activate(bundle.activation)
        self.universal = UniversalEntryEngine(registry)
        self.universal.load_watch_history(
            symbol,
            typed_candles,
            tuple(generic_oi),
            observed_at=observed_at,
        )
        self._legacy_points: list[ParityPoint] = []
        self._universal_points: list[ParityPoint] = []
        self._legacy_last_resolution: str | None = None
        self._step_index = 0
        self._capture("LOAD", None, (), (), (), ())

    @staticmethod
    def _zone_payload(zone: Any) -> dict[str, object]:
        return {
            "timeframe": zone.timeframe,
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

    def _legacy_candidate_payload(self) -> object:
        candidate = self.legacy._candidate
        if candidate is None:
            return None
        return {
            "bar_at": candidate.bar_opened_at,
            "long_entry": candidate.long_entry,
            "short_entry": candidate.short_entry,
            "long_gap": candidate.long_gap_pct,
            "short_gap": candidate.short_gap_pct,
        }

    def _universal_candidate_payload(self) -> object:
        snapshot = self.universal.watch_snapshot(self.plan.entry_plan_fingerprint, self.symbol)
        if snapshot.candidate_bar_at is None:
            return None
        return {
            "bar_at": snapshot.candidate_bar_at,
            "long_entry": snapshot.long_entry,
            "short_entry": snapshot.short_entry,
            "long_gap": snapshot.long_gap_percent,
            "short_gap": snapshot.short_gap_percent,
        }

    def _legacy_geometry_payload(self) -> object:
        candidate = self.legacy._candidate
        if candidate is None:
            return None
        return {zone.timeframe: self._zone_payload(zone) for zone in candidate.geometry}

    def _universal_geometry_payload(self) -> object:
        snapshot = self.universal.watch_snapshot(self.plan.entry_plan_fingerprint, self.symbol)
        if not snapshot.geometry:
            return None
        return {
            timeframe: self._zone_payload(zone) for timeframe, zone in snapshot.geometry.items()
        }

    @staticmethod
    def _touch_from_legacy_audit(events: tuple[Any, ...]) -> dict[str, object] | None:
        candidates = [
            event
            for event in events
            if getattr(event, "event_type", "") in {"TOUCH_BLOCKED", "TOUCH_VETO", "CORE_SIGNAL"}
        ]
        if not candidates:
            return None
        event = candidates[-1]
        direction = getattr(event, "direction", None)
        return {
            "direction": None if direction is None else str(direction).upper(),
            "candidate_bar_at": getattr(event, "candidate_bar_at", None),
            "entry_price": getattr(event, "entry_price", None),
            "touch_at": getattr(event, "occurred_at", None),
        }

    @staticmethod
    def _touch_from_universal_trace(events: tuple[Any, ...]) -> dict[str, object] | None:
        rows = [event for event in events if getattr(event, "category", "") == "touch"]
        if not rows:
            return None
        event = rows[-1]
        payload = event.payload.to_dict()
        direction = payload.get("direction")
        return {
            "direction": direction,
            "candidate_bar_at": _maybe_datetime(payload.get("candidate_bar_at")),
            "entry_price": _maybe_decimal(payload.get("entry_price")),
            "touch_at": event.observed_at,
        }

    def _capture(
        self,
        causal_key: str,
        fact: object | None,
        legacy_audit: tuple[Any, ...],
        universal_trace: tuple[Any, ...],
        legacy_results: tuple[Any, ...],
        universal_results: tuple[Any, ...],
    ) -> None:
        from bybit_workbench.entry_bot.engine import flow_features
        from bybit_workbench.entry_bot.models import Direction

        key = f"{self._step_index}:{causal_key}"
        legacy_touch = self._touch_from_legacy_audit(legacy_audit)
        universal_touch = self._touch_from_universal_trace(universal_trace)
        self._append_pair(
            "candidate", key, self._legacy_candidate_payload(), self._universal_candidate_payload()
        )
        self._append_pair(
            "geometry", key, self._legacy_geometry_payload(), self._universal_geometry_payload()
        )
        legacy_reset = None
        legacy_geometry = self._legacy_geometry_payload()
        if isinstance(legacy_geometry, dict):
            legacy_reset = {tf: row["regime_reset_at"] for tf, row in legacy_geometry.items()}
        universal_reset = None
        universal_geometry = self._universal_geometry_payload()
        if isinstance(universal_geometry, dict):
            universal_reset = {tf: row["regime_reset_at"] for tf, row in universal_geometry.items()}
        self._append_pair("shock_reset", key, legacy_reset, universal_reset)
        self._append_pair(
            "swing",
            key,
            {
                "blocked": self.legacy._hourly_swing_blocked,
                "percent": self.legacy._hourly_swing_pct,
            },
            {
                "blocked": self.universal.watch_snapshot(
                    self.plan.entry_plan_fingerprint, self.symbol
                ).hourly_swing_blocked,
                "percent": self.universal.watch_snapshot(
                    self.plan.entry_plan_fingerprint, self.symbol
                ).hourly_swing_percent,
            },
        )
        legacy_state = self.universal._states.get((self.plan.entry_plan_fingerprint, self.symbol))
        universal_cooldown = None if legacy_state is None else legacy_state.cooldown_until
        self._append_pair(
            "candidate_cooldown",
            key,
            self.legacy._candidate_cooldown_until,
            universal_cooldown,
        )
        self._append_pair("touch", key, legacy_touch, universal_touch)

        legacy_flow_value: object = None
        universal_flow_value: object = None
        if legacy_touch is not None and fact is not None:
            direction_raw = legacy_touch.get("direction")
            direction: Direction = "Long" if direction_raw == "LONG" else "Short"
            touch_at = legacy_touch.get("touch_at")
            if isinstance(touch_at, datetime):
                flow = flow_features(direction, touch_at, self.legacy._flow)
                legacy_flow_value = {
                    "pressure": flow.pressure_directional_delta_pct,
                    "reversal": flow.reversal_directional_delta_pct,
                    "matched": flow.state == "pressure_then_reversal",
                }
        touch_rows = [row for row in universal_trace if getattr(row, "category", "") == "touch"]
        if touch_rows:
            payload = touch_rows[-1].payload.to_dict()
            universal_flow_value = {
                "pressure": _maybe_decimal(payload.get("flow_pressure_delta")),
                "reversal": _maybe_decimal(payload.get("flow_reversal_delta")),
                "matched": bool(payload.get("flow_condition_met")),
            }
        self._append_pair("pressure_reversal", key, legacy_flow_value, universal_flow_value)

        legacy_oi_value = None if legacy_touch is None else self.legacy._last_oi_state
        universal_oi_value: object = None
        if touch_rows:
            payload = touch_rows[-1].payload.to_dict()
            if not bool(payload.get("oi_ready")):
                universal_oi_value = "missing"
            else:
                universal_oi_value = "OK" if bool(payload.get("oi_condition_met")) else "TAIL"
        self._append_pair("oi_result", key, legacy_oi_value, universal_oi_value)

        legacy_signal = legacy_results[-1] if legacy_results else None
        universal_eval = universal_results[-1] if universal_results else None
        legacy_signal_value = None
        if legacy_signal is not None:
            legacy_signal_value = {
                "present": True,
                "direction": str(legacy_signal.direction).upper(),
                "candidate_bar_at": legacy_signal.candidate_bar_at,
                "touch_at": legacy_signal.touch_at,
                "entry_price": legacy_signal.entry_price,
            }
        universal_signal_value = None
        if universal_eval is not None and universal_touch is not None:
            universal_signal_value = {
                "present": True,
                "direction": universal_eval.signal.direction.value,
                "candidate_bar_at": universal_touch["candidate_bar_at"],
                "touch_at": universal_eval.signal.detected_at,
                "entry_price": universal_touch["entry_price"],
            }
        self._append_pair("strategy_signal", key, legacy_signal_value, universal_signal_value)

        lifecycle = self.universal.lifecycle_snapshot(self.plan.entry_plan_fingerprint, self.symbol)
        self._append_pair(
            "post_signal_resolution",
            key,
            self._legacy_last_resolution,
            lifecycle.last_resolution,
        )
        self._append_pair(
            "entry_embargo",
            key,
            self.legacy._failure_embargo_until,
            lifecycle.entry_embargo_until,
        )
        source_ok = True
        if universal_eval is not None and fact is not None:
            source_refs = tuple(getattr(fact, "source_refs", ()))
            source_ok = all(ref in universal_eval.signal.source_refs for ref in source_refs)
        self._append_pair("causal_source_refs", key, True, source_ok)
        self._step_index += 1

    def _append_pair(
        self, category: str, causal_key: str, legacy_value: object, universal_value: object
    ) -> None:
        self._legacy_points.append(ParityPoint(category, causal_key, legacy_value))
        self._universal_points.append(ParityPoint(category, causal_key, universal_value))

    def step(self, fact: object) -> StatefulParityResult:
        from decimal import Decimal

        from bybit_workbench.domain.models import Candle
        from bybit_workbench.universal_entry import MarketFactEnvelope, TechnicalReadiness

        if not isinstance(fact, MarketFactEnvelope):
            raise TypeError("parity step requires MarketFactEnvelope")
        before = tuple(self.legacy._outcomes)
        legacy_signal = None
        attrs = fact.attributes.to_dict()
        if fact.event_kind == "CANDLE_CLOSED":
            candle = Candle(
                symbol=fact.symbol,
                timeframe=str(attrs["timeframe"]),
                opened_at=_require_datetime(attrs["opened_at"]),
                closed_at=_require_datetime(attrs["closed_at"]),
                open=Decimal(str(attrs["open"])),
                high=Decimal(str(attrs["high"])),
                low=Decimal(str(attrs["low"])),
                close=Decimal(str(attrs["close"])),
                volume=Decimal(str(attrs["volume"])),
            )
            self.legacy.on_closed_candle(candle)
        elif fact.event_kind == "BAR_OPEN":
            self.legacy.on_current_five_minute_open(
                _require_datetime(attrs["opened_at"]),
                Decimal(str(attrs["open_price"])),
                fact.observed_at,
            )
        elif fact.event_kind == "OPEN_INTEREST":
            self.legacy.on_open_interest(Decimal(str(attrs["open_interest"])), fact.observed_at)
        elif fact.event_kind == "PUBLIC_TRADE":
            legacy_signal = self.legacy.on_trade(
                price=Decimal(str(attrs["price"])),
                size=Decimal(str(attrs["size"])),
                taker_side=str(attrs["taker_side"]),
                traded_at=fact.observed_at,
            )
        else:
            raise ValueError(f"unsupported parity event kind: {fact.event_kind}")
        after = tuple(self.legacy._outcomes)
        removed = [item for item in before if item not in after]
        if removed and fact.event_kind == "PUBLIC_TRADE":
            item = removed[-1]
            price = Decimal(str(attrs["price"]))
            if fact.observed_at > item.expires_at:
                self._legacy_last_resolution = "EXPIRED"
            else:
                move = (
                    (price - item.entry_price) / item.entry_price * Decimal("100")
                    if item.direction == "Long"
                    else (item.entry_price - price) / item.entry_price * Decimal("100")
                )
                policy = self.plan.post_signal_outcome_policy
                if policy.favorable_threshold is not None and move >= policy.favorable_threshold:
                    self._legacy_last_resolution = "FAVORABLE"
                elif policy.adverse_threshold is not None and move <= policy.adverse_threshold:
                    self._legacy_last_resolution = "ADVERSE"
        legacy_audit = self.legacy.drain_audit_events()
        universal_results = self.universal.process(
            fact,
            technical_readiness=TechnicalReadiness(True, fact.observed_at, "parity-ready"),
        )
        universal_trace = self.universal.drain_watch_trace(
            self.plan.entry_plan_fingerprint, self.symbol
        )
        legacy_results = () if legacy_signal is None else (legacy_signal,)
        self._capture(
            fact.fact_id,
            fact,
            legacy_audit,
            universal_trace,
            legacy_results,
            universal_results,
        )
        return self.result()

    def result(self) -> StatefulParityResult:
        legacy = tuple(self._legacy_points)
        universal = tuple(self._universal_points)
        return StatefulParityResult(compare_parity_points(legacy, universal), legacy, universal)


def _require_datetime(value: object) -> datetime:
    from datetime import datetime

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("parity datetime is invalid")


def _maybe_datetime(value: object) -> object:
    if value is None:
        return None
    return _require_datetime(value)


def _maybe_decimal(value: object) -> object:
    from decimal import Decimal

    if value is None:
        return None
    return Decimal(str(value))

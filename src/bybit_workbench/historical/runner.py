from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from bybit_workbench.domain.intents import (
    CancelEntryIntent,
    EnterIntent,
    ExitIntent,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.models import Execution, InstrumentRules, Position
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.replay import ProtectionPlan, ReplayConfig, ReplayEngine
from bybit_workbench.replay.models import ReplayFill, ReplayTradeResult
from bybit_workbench.risk import (
    RiskContext,
    RiskDecision,
    RiskEngine,
    RiskProfile,
    ceil_to_step,
    floor_to_step,
)
from bybit_workbench.strategies.base import (
    IntentOutcome,
    IntentOutcomeStatus,
    PendingEntrySnapshot,
    ProtectionSnapshot,
    ReadOnlyStrategyContext,
    Strategy,
    StrategyMetadata,
    TradeIntent,
)
from bybit_workbench.strategies.manual import default_strategy_registry

from .market_data import HistoricalDataQuality, HistoricalMarketData
from .validation import (
    BacktestMetrics,
    EquityPoint,
    HistoricalAcceptancePolicy,
    HistoricalDataset,
    HistoricalValidationReport,
    WalkForwardFold,
    build_validation_report,
    calculate_metrics,
)


@dataclass(frozen=True, slots=True)
class HistoricalRunConfig:
    initial_equity: Decimal
    available_balance: Decimal
    risk_profile: RiskProfile
    instrument_rules: InstrumentRules
    replay: ReplayConfig = ReplayConfig()
    flatten_at_end: bool = True

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if self.available_balance < 0:
            raise ValueError("available_balance cannot be negative")


@dataclass(frozen=True, slots=True)
class HistoricalIntentOutcome:
    observed_at: datetime
    source: str
    intent: TradeIntent
    risk_decision: RiskDecision | None
    submitted: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HistoricalRunResult:
    metadata: StrategyMetadata
    symbol: str
    timeframe: str
    dataset: HistoricalDataset
    dataset_fingerprint: str
    warmup_fingerprint: str | None
    parameters: Mapping[str, object]
    outcomes: tuple[HistoricalIntentOutcome, ...]
    fills: tuple[ReplayFill, ...]
    trades: tuple[ReplayTradeResult, ...]
    ending_position: Position
    net_realized_pnl: Decimal
    data_quality: HistoricalDataQuality
    forced_flatten: bool
    price_trigger: str
    execution_mode: str
    funding_events_applied: int
    initial_equity: Decimal
    equity_curve: tuple[EquityPoint, ...]

    @property
    def metrics(self) -> BacktestMetrics:
        return calculate_metrics(
            self.trades,
            self.dataset,
            self.initial_equity,
            self.equity_curve,
        )


@dataclass(frozen=True, slots=True)
class WalkForwardFoldResult:
    index: int
    train: HistoricalRunResult
    test: HistoricalRunResult


@dataclass(frozen=True, slots=True)
class TemporalValidationResult:
    train: HistoricalRunResult
    out_of_sample: HistoricalRunResult
    report: HistoricalValidationReport
    report_id: str | None


class HistoricalReportStore(Protocol):
    def save_historical_validation_report(
        self,
        report_id: str,
        report: Any,
    ) -> None: ...


async def run_strategy(
    strategy: Strategy,
    dataset: HistoricalDataset,
    *,
    parameters: Mapping[str, object] | None,
    config: HistoricalRunConfig,
    market_data: HistoricalMarketData | None = None,
    warmup_dataset: HistoricalDataset | None = None,
    warmup_market_data: HistoricalMarketData | None = None,
) -> HistoricalRunResult:
    """Run a strategy only after each bar is closed; submitted entries start next bar."""
    metadata = strategy.metadata()
    requirements = strategy.required_data()
    resolved_parameters = _resolve_parameters(strategy, parameters)
    if dataset.timeframe not in requirements.timeframes:
        raise ValueError(
            f"strategy does not support timeframe {dataset.timeframe}; "
            f"required={requirements.timeframes}"
        )
    dynamic_warmup = (
        strategy.warmup_bars(resolved_parameters)
        if hasattr(strategy, "warmup_bars")
        else requirements.minimum_closed_bars
    )
    minimum_bars = max(requirements.minimum_closed_bars, dynamic_warmup)
    warmup_count = 0 if warmup_dataset is None else len(warmup_dataset.candles)
    if len(dataset.candles) + warmup_count < minimum_bars:
        raise ValueError("historical dataset plus causal warm-up does not satisfy minimum bars")
    if warmup_dataset is not None:
        if warmup_dataset.symbol != dataset.symbol or warmup_dataset.timeframe != dataset.timeframe:
            raise ValueError("causal warm-up metadata differs from evaluated dataset")
        if warmup_dataset.ended_at > dataset.started_at:
            raise ValueError("causal warm-up must end before the evaluated dataset starts")
    if config.instrument_rules.symbol != dataset.symbol:
        raise ValueError("instrument rules do not match historical dataset symbol")
    market = market_data or HistoricalMarketData(dataset)
    if market.trade.fingerprint != dataset.fingerprint:
        raise ValueError("market_data trade series differs from historical dataset")
    warmup_market = (
        None
        if warmup_dataset is None
        else (warmup_market_data or HistoricalMarketData(warmup_dataset))
    )
    if warmup_market is not None:
        if warmup_dataset is None:
            raise ValueError("warmup_market_data requires a causal warm-up dataset")
        if warmup_market.trade.fingerprint != warmup_dataset.fingerprint:
            raise ValueError("warmup_market_data trade series differs from causal warm-up")

    engine = ReplayEngine(dataset.symbol, config.replay)
    risk_engine = RiskEngine()
    outcomes: list[HistoricalIntentOutcome] = []
    seen_intent_ids: set[str] = set()
    first = dataset.candles[0] if warmup_dataset is None else warmup_dataset.candles[0]
    first_mark = (
        market.mark_for(0) if warmup_market is None else warmup_market.mark_for(0)
    )
    latest_execution: Execution | None = None
    funding_index = 0
    funding_events_applied = 0
    forced_flatten = False
    equity_curve: list[EquityPoint] = []
    started = False
    try:
        await strategy.on_start(
            _strategy_context(
                dataset.symbol,
                first.open,
                engine,
                resolved_parameters,
                config,
                latest_execution,
                None if first_mark is None else first_mark.open,
            )
        )
        started = True
        if warmup_dataset is not None:
            assert warmup_market is not None
            for index, bar in enumerate(warmup_dataset.candles):
                mark_bar = warmup_market.mark_for(index)
                engine.on_candle(bar, mark_bar)
                mark_price = None if mark_bar is None else mark_bar.close
                context = _strategy_context(
                    dataset.symbol,
                    bar.close,
                    engine,
                    resolved_parameters,
                    config,
                    None,
                    mark_price,
                )
                warmup_intents = await strategy.on_bar_closed(context, bar)
                callback = getattr(strategy, "on_intent_outcome", None)
                if callback is not None:
                    for intent in warmup_intents:
                        await callback(
                            context,
                            IntentOutcome(
                                intent.intent_id,
                                IntentOutcomeStatus.REJECTED,
                                bar.closed_at,
                                "causal warm-up intent suppressed",
                            ),
                        )
        for index, bar in enumerate(dataset.candles):
            while (
                funding_index < len(market.funding_events)
                and market.funding_events[funding_index].occurred_at <= bar.opened_at
            ):
                event = market.funding_events[funding_index]
                if engine.position.side is not PositionSide.FLAT:
                    engine.apply_funding(
                        _funding_cost(engine.position, event.rate, event.mark_price)
                    )
                    funding_events_applied += 1
                funding_index += 1
            position_before = engine.position
            realized_before = engine.net_realized_pnl
            mark_bar = market.mark_for(index)
            mark_price = None if mark_bar is None else mark_bar.close
            fills = engine.on_candle(bar, mark_bar)
            context = _strategy_context(
                dataset.symbol,
                bar.close,
                engine,
                resolved_parameters,
                config,
                latest_execution,
                mark_price,
            )
            execution_intents: list[TradeIntent] = []
            for fill in fills:
                latest_execution = Execution(
                    fill.execution_id,
                    f"replay:{fill.client_order_id}",
                    fill.client_order_id,
                    dataset.symbol,
                    fill.side,
                    fill.quantity,
                    fill.price,
                    fill.occurred_at,
                )
                context = _strategy_context(
                    dataset.symbol,
                    bar.close,
                    engine,
                    resolved_parameters,
                    config,
                    latest_execution,
                    mark_price,
                )
                execution_intents.extend(await strategy.on_execution(context, latest_execution))
            outcome_start = len(outcomes)
            await _process_intents(
                execution_intents,
                source="execution",
                observed_at=bar.closed_at,
                latest_price=bar.close,
                engine=engine,
                risk_engine=risk_engine,
                config=config,
                outcomes=outcomes,
                seen_intent_ids=seen_intent_ids,
            )
            await _notify_intent_outcomes(
                strategy,
                outcomes[outcome_start:],
                _strategy_context(
                    dataset.symbol,
                    bar.close,
                    engine,
                    resolved_parameters,
                    config,
                    latest_execution,
                    mark_price,
                ),
            )
            context = _strategy_context(
                dataset.symbol,
                bar.close,
                engine,
                resolved_parameters,
                config,
                latest_execution,
                mark_price,
            )
            bar_intents = await strategy.on_bar_closed(context, bar)
            outcome_start = len(outcomes)
            await _process_intents(
                bar_intents,
                source="bar_closed",
                observed_at=bar.closed_at,
                latest_price=bar.close,
                engine=engine,
                risk_engine=risk_engine,
                config=config,
                outcomes=outcomes,
                seen_intent_ids=seen_intent_ids,
            )
            followups = await _notify_intent_outcomes(
                strategy,
                outcomes[outcome_start:],
                _strategy_context(
                    dataset.symbol,
                    bar.close,
                    engine,
                    resolved_parameters,
                    config,
                    latest_execution,
                    mark_price,
                ),
            )
            if followups:
                await _process_intents(
                    followups,
                    source="intent_outcome",
                    observed_at=bar.closed_at,
                    latest_price=bar.close,
                    engine=engine,
                    risk_engine=risk_engine,
                    config=config,
                    outcomes=outcomes,
                    seen_intent_ids=seen_intent_ids,
                )
            equity_curve.append(
                _equity_point(
                    bar,
                    mark_bar,
                    config.initial_equity,
                    engine,
                    position_before,
                    realized_before,
                )
            )
        while funding_index < len(market.funding_events):
            event = market.funding_events[funding_index]
            if engine.position.side is not PositionSide.FLAT:
                engine.apply_funding(_funding_cost(engine.position, event.rate, event.mark_price))
                funding_events_applied += 1
            funding_index += 1
        engine.cancel_entry_orders("historical-end-cancel")
        if config.flatten_at_end:
            forced_flatten = (
                engine.flatten_position(
                    "historical-end-flatten",
                    dataset.candles[-1].close,
                    dataset.ended_at,
                )
                is not None
            )
        if equity_curve:
            final_equity = config.initial_equity + engine.net_realized_pnl
            last = equity_curve[-1]
            equity_curve[-1] = EquityPoint(
                last.observed_at,
                final_equity,
                min(last.intrabar_min_equity, final_equity),
            )
    finally:
        if started:
            await strategy.on_stop("historical replay completed")

    return HistoricalRunResult(
        metadata=metadata,
        symbol=dataset.symbol,
        timeframe=dataset.timeframe,
        dataset=dataset,
        dataset_fingerprint=(
            market.fingerprint
            if warmup_market is None
            else _combined_fingerprint(warmup_market.fingerprint, market.fingerprint)
        ),
        warmup_fingerprint=None if warmup_market is None else warmup_market.fingerprint,
        parameters=resolved_parameters,
        outcomes=tuple(outcomes),
        fills=tuple(engine.fills),
        trades=tuple(engine.completed_trades),
        ending_position=engine.position,
        net_realized_pnl=engine.net_realized_pnl,
        data_quality=market.quality,
        forced_flatten=forced_flatten,
        price_trigger="MarkPrice" if market.mark_price_complete else "TradePriceFallback",
        execution_mode="closed-candle-limit-retest",
        funding_events_applied=funding_events_applied,
        initial_equity=config.initial_equity,
        equity_curve=tuple(equity_curve),
    )


async def evaluate_walk_forward(
    strategy_factory: Callable[[], Strategy],
    folds: Sequence[WalkForwardFold],
    *,
    parameters: Mapping[str, object] | None,
    config: HistoricalRunConfig,
    market_data: HistoricalMarketData | None = None,
) -> tuple[WalkForwardFoldResult, ...]:
    if not folds:
        raise ValueError("at least one walk-forward fold is required")
    results: list[WalkForwardFoldResult] = []
    for fold in folds:
        train = await run_strategy(
            strategy_factory(),
            fold.train,
            parameters=parameters,
            config=config,
            market_data=None if market_data is None else market_data.slice_for(fold.train),
        )
        test = await run_strategy(
            strategy_factory(),
            fold.test,
            parameters=parameters,
            config=config,
            market_data=None if market_data is None else market_data.slice_for(fold.test),
            warmup_dataset=fold.train,
            warmup_market_data=(
                None if market_data is None else market_data.slice_for(fold.train)
            ),
        )
        _require_matching_metadata(train.metadata, test.metadata)
        results.append(WalkForwardFoldResult(fold.index, train, test))
    return tuple(results)


async def evaluate_temporal_validation(
    strategy_factory: Callable[[], Strategy],
    *,
    train_dataset: HistoricalDataset,
    out_of_sample_dataset: HistoricalDataset,
    parameters: Mapping[str, object] | None,
    config: HistoricalRunConfig,
    policy: HistoricalAcceptancePolicy,
    code_version: str,
    report_store: HistoricalReportStore | None = None,
    report_id: str | None = None,
    train_market_data: HistoricalMarketData | None = None,
    out_of_sample_market_data: HistoricalMarketData | None = None,
) -> TemporalValidationResult:
    train = await run_strategy(
        strategy_factory(),
        train_dataset,
        parameters=parameters,
        config=config,
        market_data=train_market_data,
    )
    out_of_sample = await run_strategy(
        strategy_factory(),
        out_of_sample_dataset,
        parameters=parameters,
        config=config,
        market_data=out_of_sample_market_data,
        warmup_dataset=train_dataset,
        warmup_market_data=train_market_data,
    )
    _require_matching_metadata(train.metadata, out_of_sample.metadata)
    report = build_validation_report(
        strategy_id=train.metadata.strategy_id,
        strategy_version=train.metadata.version,
        code_version=code_version,
        parameters=train.parameters,
        train_dataset=train_dataset,
        out_of_sample_dataset=out_of_sample_dataset,
        in_sample_trades=train.trades,
        out_of_sample_trades=out_of_sample.trades,
        policy=policy,
        in_sample_equity_curve=train.equity_curve,
        out_of_sample_equity_curve=out_of_sample.equity_curve,
        fee_rate=config.replay.fee_rate,
        slippage_percent=config.replay.slippage_percent,
        seed=config.replay.seed,
        maker_fee_rate=config.replay.effective_maker_fee_rate,
        taker_fee_rate=config.replay.effective_taker_fee_rate,
        mark_price_complete=(
            train.data_quality.mark_price_complete
            and out_of_sample.data_quality.mark_price_complete
        ),
        funding_complete=(
            train.data_quality.funding_complete and out_of_sample.data_quality.funding_complete
        ),
        production_equivalent=(
            train.data_quality.production_equivalent
            and out_of_sample.data_quality.production_equivalent
        ),
        price_trigger=(
            "MarkPrice"
            if train.price_trigger == out_of_sample.price_trigger == "MarkPrice"
            else "TradePriceFallback"
        ),
        execution_mode=train.execution_mode,
        forced_flatten=train.forced_flatten or out_of_sample.forced_flatten,
        data_quality_flags=tuple(
            sorted(set(train.data_quality.flags + out_of_sample.data_quality.flags))
        ),
        dataset_fingerprint_override=_combined_fingerprint(
            train.dataset_fingerprint,
            out_of_sample.dataset_fingerprint,
        ),
        initial_equity=config.initial_equity,
        instrument_rules=config.instrument_rules,
    )
    saved_id = None
    if report_store is not None:
        saved_id = report_id or f"historical-{uuid4().hex}"
        report_store.save_historical_validation_report(saved_id, report)
    elif report_id is not None:
        raise ValueError("report_id requires report_store")
    return TemporalValidationResult(train, out_of_sample, report, saved_id)


async def _process_intents(
    intents: Sequence[TradeIntent],
    *,
    source: str,
    observed_at: datetime,
    latest_price: Decimal,
    engine: ReplayEngine,
    risk_engine: RiskEngine,
    config: HistoricalRunConfig,
    outcomes: list[HistoricalIntentOutcome],
    seen_intent_ids: set[str],
) -> None:
    for intent in intents:
        if intent.intent_id in seen_intent_ids:
            outcomes.append(
                HistoricalIntentOutcome(
                    observed_at, source, intent, None, False, "duplicate intent_id"
                )
            )
            continue
        seen_intent_ids.add(intent.intent_id)
        if intent.symbol != engine.symbol:
            outcomes.append(
                HistoricalIntentOutcome(
                    observed_at, source, intent, None, False, "intent symbol mismatch"
                )
            )
            continue
        if isinstance(intent, NoOpIntent):
            outcomes.append(HistoricalIntentOutcome(observed_at, source, intent, None, False))
            continue
        if isinstance(intent, CancelEntryIntent):
            cancelled = engine.cancel_entry_orders(f"cancel-{intent.intent_id}")
            outcomes.append(
                HistoricalIntentOutcome(
                    observed_at,
                    source,
                    intent,
                    None,
                    cancelled,
                    None if cancelled else "there is no pending entry",
                )
            )
            continue
        if isinstance(intent, ExitIntent):
            fill = engine.exit_position(intent.intent_id, latest_price, observed_at)
            outcomes.append(
                HistoricalIntentOutcome(
                    observed_at,
                    source,
                    intent,
                    None,
                    fill is not None,
                    None if fill is not None else "there is no open position",
                )
            )
            continue
        if isinstance(intent, UpdateProtectionIntent):
            try:
                stop = _normalized_protection_stop(intent.stop_price, engine, config)
                changed = engine.update_protection(
                    stop_price=stop,
                    take_profit=intent.take_profit,
                )
            except (RuntimeError, ValueError) as exc:
                outcomes.append(
                    HistoricalIntentOutcome(observed_at, source, intent, None, False, str(exc))
                )
            else:
                outcomes.append(HistoricalIntentOutcome(observed_at, source, intent, None, changed))
            continue
        if not isinstance(intent, EnterIntent):
            raise TypeError(f"unsupported historical intent: {type(intent).__name__}")
        context = _risk_context(observed_at, engine, config)
        decision = risk_engine.evaluate_entry(
            intent,
            config.risk_profile,
            context,
            config.instrument_rules,
        )
        if not decision.approved:
            outcomes.append(HistoricalIntentOutcome(observed_at, source, intent, decision, False))
            continue
        order = decision.normalized_order
        stop = decision.normalized_stop
        if order is None or stop is None:
            outcomes.append(
                HistoricalIntentOutcome(
                    observed_at,
                    source,
                    intent,
                    decision,
                    False,
                    "approved risk decision is incomplete",
                )
            )
            continue
        try:
            engine.submit_entry(order, ProtectionPlan(stop, intent.take_profit))
        except (RuntimeError, ValueError) as exc:
            outcomes.append(
                HistoricalIntentOutcome(observed_at, source, intent, decision, False, str(exc))
            )
        else:
            outcomes.append(HistoricalIntentOutcome(observed_at, source, intent, decision, True))


def _strategy_context(
    symbol: str,
    latest_price: Decimal,
    engine: ReplayEngine,
    parameters: Mapping[str, object],
    config: HistoricalRunConfig,
    latest_execution: Execution | None,
    mark_price: Decimal | None = None,
) -> ReadOnlyStrategyContext:
    pending = None
    if engine.pending_entry is not None:
        order = engine.pending_entry
        if order.request.price is None:
            raise RuntimeError("automatic strategy pending entry must be a limit order")
        pending = PendingEntrySnapshot(
            order.request.client_order_id,
            order.request.side,
            order.request.price,
            order.request.quantity,
            order.remaining_quantity,
            order.status,
            engine.pending_entry_age,
        )
    protection = ProtectionSnapshot(
        confirmed_stop=None if engine.protection is None else engine.protection.stop_price,
        confirmed_take_profit=(
            None if engine.protection is None else engine.protection.take_profit
        ),
    )
    return ReadOnlyStrategyContext(
        symbol,
        latest_price,
        engine.position,
        parameters,
        mark_price=mark_price or latest_price,
        protection=protection,
        pending_entry=pending,
        latest_execution=latest_execution,
        tick_size=config.instrument_rules.tick_size,
    )


def _equity_point(
    bar: Any,
    mark_bar: Any,
    initial_equity: Decimal,
    engine: ReplayEngine,
    position_before: Position,
    realized_before: Decimal,
) -> EquityPoint:
    trigger = mark_bar or bar
    close_price = trigger.close
    close_equity = _equity_at_price(
        initial_equity, engine.net_realized_pnl, engine.position, close_price
    )
    candidates = [close_equity]
    if position_before.side is not PositionSide.FLAT:
        worst = trigger.low if position_before.side is PositionSide.LONG else trigger.high
        candidates.append(
            _equity_at_price(initial_equity, realized_before, position_before, worst)
        )
    if engine.position.side is not PositionSide.FLAT:
        worst = trigger.low if engine.position.side is PositionSide.LONG else trigger.high
        candidates.append(
            _equity_at_price(initial_equity, engine.net_realized_pnl, engine.position, worst)
        )
    return EquityPoint(bar.closed_at, close_equity, min(candidates))


def _equity_at_price(
    initial_equity: Decimal,
    realized_pnl: Decimal,
    position: Position,
    price: Decimal,
) -> Decimal:
    unrealized = Decimal("0")
    if position.side is not PositionSide.FLAT and position.average_price is not None:
        move = (
            price - position.average_price
            if position.side is PositionSide.LONG
            else position.average_price - price
        )
        unrealized = move * position.quantity
    return initial_equity + realized_pnl + unrealized


def _funding_cost(position: Position, rate: Decimal, mark_price: Decimal) -> Decimal:
    notional = position.quantity * mark_price
    return notional * rate if position.side is PositionSide.LONG else -notional * rate


def _combined_fingerprint(first: str, second: str) -> str:
    return hashlib.sha256(f"{first}:{second}".encode()).hexdigest()


def _resolve_parameters(
    strategy: Strategy,
    supplied: Mapping[str, object] | None,
) -> dict[str, object]:
    metadata = strategy.metadata()
    try:
        registration = default_strategy_registry().get(metadata.strategy_id)
    except LookupError:
        resolved = dict(strategy.default_parameters())
        resolved.update(supplied or {})
        return resolved
    if registration.metadata.version != metadata.version:
        raise ValueError("strategy implementation and registry versions do not match")
    return registration.resolve_parameters(supplied)


async def _notify_intent_outcomes(
    strategy: Strategy,
    outcomes: Sequence[HistoricalIntentOutcome],
    context: ReadOnlyStrategyContext,
) -> tuple[TradeIntent, ...]:
    callback = getattr(strategy, "on_intent_outcome", None)
    if callback is None:
        return ()
    followups: list[TradeIntent] = []
    for item in outcomes:
        if item.error is not None or (
            item.risk_decision is not None and not item.risk_decision.approved
        ):
            status = IntentOutcomeStatus.REJECTED
        elif isinstance(item.intent, CancelEntryIntent) and item.submitted:
            status = IntentOutcomeStatus.CANCELLED
        elif item.submitted:
            status = IntentOutcomeStatus.SUBMITTED
        else:
            status = IntentOutcomeStatus.APPROVED
        followups.extend(
            await callback(
                context,
                IntentOutcome(item.intent.intent_id, status, item.observed_at, item.error or ""),
            )
        )
    return tuple(followups)


def _risk_context(
    evaluated_at: datetime,
    engine: ReplayEngine,
    config: HistoricalRunConfig,
) -> RiskContext:
    equity = config.initial_equity + engine.net_realized_pnl
    if equity <= 0:
        raise RuntimeError("historical equity is depleted")
    available = max(Decimal("0"), config.available_balance + engine.net_realized_pnl)
    trades_today = [
        trade for trade in engine.completed_trades if trade.closed_at.date() == evaluated_at.date()
    ]
    daily_pnl = sum((trade.net_pnl for trade in trades_today), Decimal("0"))
    consecutive_losses = 0
    for trade in reversed(engine.completed_trades):
        if trade.net_pnl >= 0:
            break
        consecutive_losses += 1
    return RiskContext(
        equity=equity,
        available_balance=available,
        daily_realized_pnl=daily_pnl,
        consecutive_losses=consecutive_losses,
        open_positions=int(engine.position.side is not PositionSide.FLAT),
        pending_entries=int(engine.pending_entry is not None),
        market_data_at=evaluated_at,
        private_stream_at=evaluated_at,
        evaluated_at=evaluated_at,
        current_position_side=engine.position.side,
        position_is_protected=(
            engine.position.side is PositionSide.FLAT or engine.protection is not None
        ),
    )


def _require_matching_metadata(
    first: StrategyMetadata,
    second: StrategyMetadata,
) -> None:
    if first.strategy_id != second.strategy_id or first.version != second.version:
        raise ValueError("strategy factory returned inconsistent metadata")


def _normalized_protection_stop(
    stop_price: Decimal | None,
    engine: ReplayEngine,
    config: HistoricalRunConfig,
) -> Decimal | None:
    if stop_price is None:
        return None
    if engine.position.side is PositionSide.LONG:
        return floor_to_step(stop_price, config.instrument_rules.tick_size)
    if engine.position.side is PositionSide.SHORT:
        return ceil_to_step(stop_price, config.instrument_rules.tick_size)
    return stop_price

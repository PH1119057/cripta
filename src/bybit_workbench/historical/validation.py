from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from bybit_workbench.domain.models import Candle, InstrumentRules
from bybit_workbench.replay.models import ReplayTradeResult


@dataclass(frozen=True, slots=True)
class HistoricalDataset:
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if not self.candles:
            raise ValueError("historical dataset cannot be empty")
        first = self.candles[0]
        previous = None
        for candle in self.candles:
            if not candle.is_closed:
                raise ValueError("historical validation accepts closed candles only")
            if candle.symbol != first.symbol or candle.timeframe != first.timeframe:
                raise ValueError("historical dataset must contain one symbol and timeframe")
            if previous is not None:
                if candle.opened_at <= previous.opened_at:
                    raise ValueError("historical candles must be strictly chronological")
                if candle.opened_at < previous.closed_at:
                    raise ValueError("historical candles cannot overlap")
            previous = candle

    @property
    def symbol(self) -> str:
        return self.candles[0].symbol

    @property
    def timeframe(self) -> str:
        return self.candles[0].timeframe

    @property
    def started_at(self) -> datetime:
        return self.candles[0].opened_at

    @property
    def ended_at(self) -> datetime:
        return self.candles[-1].closed_at

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for candle in self.candles:
            payload = (
                candle.symbol,
                candle.timeframe,
                candle.opened_at.isoformat(),
                candle.closed_at.isoformat(),
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                str(candle.volume),
            )
            digest.update("|".join(payload).encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TemporalPartitions:
    train: HistoricalDataset
    validation: HistoricalDataset
    test: HistoricalDataset

    def __post_init__(self) -> None:
        if self.train.ended_at > self.validation.started_at:
            raise ValueError("train and validation periods overlap")
        if self.validation.ended_at > self.test.started_at:
            raise ValueError("validation and test periods overlap")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    index: int
    train: HistoricalDataset
    test: HistoricalDataset

    def __post_init__(self) -> None:
        if self.train.ended_at > self.test.started_at:
            raise ValueError("walk-forward train data overlaps its test data")


def chronological_split(
    dataset: HistoricalDataset,
    *,
    train_fraction: Decimal = Decimal("0.6"),
    validation_fraction: Decimal = Decimal("0.2"),
    minimum_partition_bars: int = 1,
) -> TemporalPartitions:
    if minimum_partition_bars < 1:
        raise ValueError("minimum_partition_bars must be positive")
    if train_fraction <= 0 or validation_fraction <= 0:
        raise ValueError("train and validation fractions must be positive")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("test fraction must remain positive")
    count = len(dataset.candles)
    train_count = int((Decimal(count) * train_fraction).to_integral_value(rounding=ROUND_FLOOR))
    validation_count = int(
        (Decimal(count) * validation_fraction).to_integral_value(rounding=ROUND_FLOOR)
    )
    test_count = count - train_count - validation_count
    if min(train_count, validation_count, test_count) < minimum_partition_bars:
        raise ValueError("historical dataset is too small for requested temporal split")
    return TemporalPartitions(
        HistoricalDataset(dataset.candles[:train_count]),
        HistoricalDataset(dataset.candles[train_count : train_count + validation_count]),
        HistoricalDataset(dataset.candles[train_count + validation_count :]),
    )


def walk_forward_splits(
    dataset: HistoricalDataset,
    *,
    training_bars: int,
    test_bars: int,
    step_bars: int | None = None,
    anchored: bool = False,
) -> tuple[WalkForwardFold, ...]:
    if training_bars < 1 or test_bars < 1:
        raise ValueError("walk-forward window sizes must be positive")
    step = test_bars if step_bars is None else step_bars
    if step < 1:
        raise ValueError("step_bars must be positive")
    candles = dataset.candles
    folds: list[WalkForwardFold] = []
    train_end = training_bars
    index = 1
    while train_end + test_bars <= len(candles):
        train_start = 0 if anchored else train_end - training_bars
        folds.append(
            WalkForwardFold(
                index,
                HistoricalDataset(candles[train_start:train_end]),
                HistoricalDataset(candles[train_end : train_end + test_bars]),
            )
        )
        index += 1
        train_end += step
    if not folds:
        raise ValueError("historical dataset is too small for one walk-forward fold")
    return tuple(folds)


@dataclass(frozen=True, slots=True)
class EquityPoint:
    observed_at: datetime
    close_equity: Decimal
    intrabar_min_equity: Decimal

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("equity point timestamp must be timezone-aware")
        if not self.close_equity.is_finite() or not self.intrabar_min_equity.is_finite():
            raise ValueError("equity point values must be finite")
        if self.intrabar_min_equity > self.close_equity:
            raise ValueError("intrabar minimum equity cannot exceed close equity")


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    net_pnl: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    win_rate_percent: Decimal
    profit_factor: Decimal | None
    max_drawdown: Decimal
    ambiguous_trades: int
    ambiguous_fraction: Decimal
    exposure_seconds: Decimal
    time_in_market_percent: Decimal | None
    buy_and_hold_return_percent: Decimal | None
    initial_equity: Decimal | None
    ending_equity: Decimal | None
    return_percent: Decimal | None
    max_drawdown_percent: Decimal | None
    expectancy_money: Decimal | None
    expectancy_r: Decimal | None
    average_win: Decimal | None
    median_win: Decimal | None
    average_loss: Decimal | None
    median_loss: Decimal | None
    average_holding_seconds: Decimal | None
    median_holding_seconds: Decimal | None
    payoff_ratio: Decimal | None
    longest_loss_streak: int


def calculate_metrics(
    trades: Sequence[ReplayTradeResult],
    dataset: HistoricalDataset | None = None,
    initial_equity: Decimal | None = None,
    equity_curve: Sequence[EquityPoint] = (),
) -> BacktestMetrics:
    if initial_equity is not None and initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    wins = sum(trade.net_pnl > 0 for trade in trades)
    losses = sum(trade.net_pnl < 0 for trade in trades)
    net = sum((trade.net_pnl for trade in trades), Decimal("0"))
    gross = sum((trade.gross_pnl for trade in trades), Decimal("0"))
    fees = sum((trade.fees for trade in trades), Decimal("0"))
    funding = sum((trade.funding for trade in trades), Decimal("0"))
    gross_profit = sum(
        (trade.net_pnl for trade in trades if trade.net_pnl > 0),
        Decimal("0"),
    )
    gross_loss = -sum(
        (trade.net_pnl for trade in trades if trade.net_pnl < 0),
        Decimal("0"),
    )
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    max_drawdown_percent: Decimal | None = None
    if equity_curve:
        ordered_curve = sorted(equity_curve, key=lambda item: item.observed_at)
        peak = initial_equity or ordered_curve[0].close_equity
        for point in ordered_curve:
            peak = max(peak, point.close_equity)
            drawdown = max(Decimal("0"), peak - point.intrabar_min_equity)
            max_drawdown = max(max_drawdown, drawdown)
            if peak > 0:
                percent = drawdown / peak * Decimal("100")
                max_drawdown_percent = max(max_drawdown_percent or Decimal("0"), percent)
    else:
        for trade in sorted(trades, key=lambda item: item.closed_at):
            equity += trade.net_pnl
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
    ambiguous = sum(trade.ambiguous_bar for trade in trades)
    count = len(trades)
    exposure_seconds = _exposure_seconds(trades)
    dataset_seconds = (
        None
        if dataset is None
        else Decimal(str((dataset.ended_at - dataset.started_at).total_seconds()))
    )
    time_in_market = None
    if dataset_seconds is not None and dataset_seconds != 0:
        time_in_market = min(Decimal("100"), exposure_seconds / dataset_seconds * Decimal("100"))
    benchmark = None
    if dataset is not None:
        first_price = dataset.candles[0].open
        benchmark = (dataset.candles[-1].close - first_price) / first_price * Decimal("100")
    wins_pnl = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses_pnl = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    holding = [
        Decimal(str((trade.closed_at - trade.opened_at).total_seconds())) for trade in trades
    ]
    average_win = _average(wins_pnl)
    average_loss = _average(losses_pnl)
    longest_loss_streak = 0
    current_loss_streak = 0
    for trade in sorted(trades, key=lambda item: item.closed_at):
        if trade.net_pnl < 0:
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0
    ending_equity = None if initial_equity is None else initial_equity + net
    return BacktestMetrics(
        trades=count,
        wins=wins,
        losses=losses,
        net_pnl=net,
        gross_pnl=gross,
        fees=fees,
        funding=funding,
        win_rate_percent=(
            Decimal("0") if count == 0 else Decimal(wins) / Decimal(count) * Decimal("100")
        ),
        profit_factor=None if gross_loss == 0 else gross_profit / gross_loss,
        max_drawdown=max_drawdown,
        ambiguous_trades=ambiguous,
        ambiguous_fraction=(Decimal("0") if count == 0 else Decimal(ambiguous) / Decimal(count)),
        exposure_seconds=exposure_seconds,
        time_in_market_percent=time_in_market,
        buy_and_hold_return_percent=benchmark,
        initial_equity=initial_equity,
        ending_equity=ending_equity,
        return_percent=(None if initial_equity is None else net / initial_equity * Decimal("100")),
        max_drawdown_percent=(
            max_drawdown_percent
            if equity_curve
            else (
                None
                if initial_equity is None
                else max_drawdown / initial_equity * Decimal("100")
            )
        ),
        expectancy_money=None if count == 0 else net / Decimal(count),
        expectancy_r=None,
        average_win=average_win,
        median_win=_median(wins_pnl),
        average_loss=average_loss,
        median_loss=_median(losses_pnl),
        average_holding_seconds=_average(holding),
        median_holding_seconds=_median(holding),
        payoff_ratio=(
            None
            if average_win is None or average_loss is None or average_loss == 0
            else average_win / abs(average_loss)
        ),
        longest_loss_streak=longest_loss_streak,
    )


@dataclass(frozen=True, slots=True)
class HistoricalAcceptancePolicy:
    minimum_out_of_sample_trades: int = 20
    maximum_out_of_sample_drawdown: Decimal = Decimal("100")
    maximum_ambiguous_fraction: Decimal = Decimal("0.05")
    require_positive_out_of_sample_pnl: bool = True
    require_production_data: bool = False

    def __post_init__(self) -> None:
        if self.minimum_out_of_sample_trades < 1:
            raise ValueError("minimum trades must be positive")
        if self.maximum_out_of_sample_drawdown < 0:
            raise ValueError("maximum drawdown cannot be negative")
        if self.maximum_ambiguous_fraction < 0 or self.maximum_ambiguous_fraction > 1:
            raise ValueError("maximum ambiguous fraction must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class HistoricalValidationCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class HistoricalValidationReport:
    strategy_id: str
    strategy_version: str
    code_version: str
    parameters_fingerprint: str
    dataset_fingerprint: str
    symbol: str
    timeframe: str
    instrument_rules: InstrumentRules | None
    instrument_rules_fingerprint: str | None
    train_period: tuple[datetime, datetime]
    out_of_sample_period: tuple[datetime, datetime]
    in_sample: BacktestMetrics
    out_of_sample: BacktestMetrics
    checks: tuple[HistoricalValidationCheck, ...]
    fee_rate: Decimal
    slippage_percent: Decimal
    seed: int
    generated_at: datetime
    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None
    mark_price_complete: bool = False
    funding_complete: bool = False
    production_equivalent: bool = False
    price_trigger: str = "TradePriceFallback"
    execution_mode: str = "closed-candle-limit-retest"
    forced_flatten: bool = False
    data_quality_flags: tuple[str, ...] = ()
    limitation: str = (
        "Candle-based historical validation is not evidence of future profitability; "
        "intrabar order, queue position, liquidity and outages are approximations."
    )

    @property
    def eligible_for_micro_live(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def eligible_for_testnet(self) -> bool:
        """Compatibility alias for reports stored before the Mainnet-first migration."""

        return self.eligible_for_micro_live


def build_validation_report(
    *,
    strategy_id: str,
    strategy_version: str,
    code_version: str,
    parameters: Mapping[str, Any],
    train_dataset: HistoricalDataset,
    out_of_sample_dataset: HistoricalDataset,
    in_sample_trades: Sequence[ReplayTradeResult],
    out_of_sample_trades: Sequence[ReplayTradeResult],
    policy: HistoricalAcceptancePolicy,
    in_sample_equity_curve: Sequence[EquityPoint] = (),
    out_of_sample_equity_curve: Sequence[EquityPoint] = (),
    fee_rate: Decimal,
    slippage_percent: Decimal,
    seed: int,
    generated_at: datetime | None = None,
    maker_fee_rate: Decimal | None = None,
    taker_fee_rate: Decimal | None = None,
    mark_price_complete: bool = False,
    funding_complete: bool = False,
    production_equivalent: bool = False,
    price_trigger: str = "TradePriceFallback",
    execution_mode: str = "closed-candle-limit-retest",
    forced_flatten: bool = False,
    data_quality_flags: tuple[str, ...] = (),
    dataset_fingerprint_override: str | None = None,
    initial_equity: Decimal | None = None,
    instrument_rules: InstrumentRules | None = None,
) -> HistoricalValidationReport:
    if not strategy_id.strip() or not strategy_version.strip() or not code_version.strip():
        raise ValueError("strategy id, strategy version and code version are required")
    if train_dataset.symbol != out_of_sample_dataset.symbol:
        raise ValueError("train and out-of-sample symbols differ")
    if train_dataset.timeframe != out_of_sample_dataset.timeframe:
        raise ValueError("train and out-of-sample timeframes differ")
    if train_dataset.ended_at > out_of_sample_dataset.started_at:
        raise ValueError("out-of-sample data must be strictly after train data")
    _require_trades_inside(in_sample_trades, train_dataset, "in-sample")
    _require_trades_inside(out_of_sample_trades, out_of_sample_dataset, "out-of-sample")
    ins = calculate_metrics(
        in_sample_trades, train_dataset, initial_equity, in_sample_equity_curve
    )
    oos = calculate_metrics(
        out_of_sample_trades, out_of_sample_dataset, initial_equity, out_of_sample_equity_curve
    )
    if instrument_rules is not None and instrument_rules.symbol != train_dataset.symbol:
        raise ValueError("instrument rules do not match validation dataset symbol")
    rules_fingerprint = (
        None if instrument_rules is None else instrument_rules_fingerprint(instrument_rules)
    )
    checks = [
        HistoricalValidationCheck(
            "minimum_oos_trades",
            oos.trades >= policy.minimum_out_of_sample_trades,
            f"actual={oos.trades} required={policy.minimum_out_of_sample_trades}",
        ),
        HistoricalValidationCheck(
            "oos_drawdown",
            oos.max_drawdown <= policy.maximum_out_of_sample_drawdown,
            f"actual={oos.max_drawdown} max={policy.maximum_out_of_sample_drawdown}",
        ),
        HistoricalValidationCheck(
            "ambiguous_fraction",
            oos.ambiguous_fraction <= policy.maximum_ambiguous_fraction,
            f"actual={oos.ambiguous_fraction} max={policy.maximum_ambiguous_fraction}",
        ),
        HistoricalValidationCheck(
            "positive_oos_pnl",
            not policy.require_positive_out_of_sample_pnl or oos.net_pnl > 0,
            f"actual={oos.net_pnl}",
        ),
        HistoricalValidationCheck(
            "execution_costs_modelled",
            (
                (fee_rate > 0 or (maker_fee_rate is not None and taker_fee_rate is not None))
                and slippage_percent > 0
            ),
            (
                f"fee_rate={fee_rate} maker={maker_fee_rate} taker={taker_fee_rate} "
                f"slippage_percent={slippage_percent}"
            ),
        ),
    ]
    if policy.require_production_data:
        checks.extend(
            (
                HistoricalValidationCheck(
                    "mark_price_complete",
                    mark_price_complete,
                    f"complete={mark_price_complete} trigger={price_trigger}",
                ),
                HistoricalValidationCheck(
                    "funding_complete",
                    funding_complete,
                    f"complete={funding_complete}",
                ),
                HistoricalValidationCheck(
                    "production_equivalent",
                    production_equivalent,
                    f"production_equivalent={production_equivalent}",
                ),
                HistoricalValidationCheck(
                    "instrument_rules_bound",
                    instrument_rules is not None,
                    f"fingerprint={rules_fingerprint}",
                ),
                HistoricalValidationCheck(
                    "execution_mode_matches_mainnet",
                    execution_mode == "closed-candle-limit-retest",
                    f"execution_mode={execution_mode}",
                ),
                HistoricalValidationCheck(
                    "mark_price_trigger_matches_mainnet",
                    price_trigger == "MarkPrice",
                    f"price_trigger={price_trigger}",
                ),
            )
        )
    dataset_fingerprint = (
        dataset_fingerprint_override
        or hashlib.sha256(
            f"{train_dataset.fingerprint}:{out_of_sample_dataset.fingerprint}".encode()
        ).hexdigest()
    )
    return HistoricalValidationReport(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        code_version=code_version,
        parameters_fingerprint=parameters_fingerprint(parameters),
        dataset_fingerprint=dataset_fingerprint,
        symbol=train_dataset.symbol,
        timeframe=train_dataset.timeframe,
        instrument_rules=instrument_rules,
        instrument_rules_fingerprint=rules_fingerprint,
        train_period=(train_dataset.started_at, train_dataset.ended_at),
        out_of_sample_period=(
            out_of_sample_dataset.started_at,
            out_of_sample_dataset.ended_at,
        ),
        in_sample=ins,
        out_of_sample=oos,
        checks=tuple(checks),
        fee_rate=fee_rate,
        slippage_percent=slippage_percent,
        seed=seed,
        generated_at=generated_at or datetime.now(UTC),
        maker_fee_rate=maker_fee_rate,
        taker_fee_rate=taker_fee_rate,
        mark_price_complete=mark_price_complete,
        funding_complete=funding_complete,
        production_equivalent=production_equivalent,
        price_trigger=price_trigger,
        execution_mode=execution_mode,
        forced_flatten=forced_flatten,
        data_quality_flags=data_quality_flags,
    )


def instrument_rules_fingerprint(rules: InstrumentRules) -> str:
    payload = {
        "symbol": rules.symbol,
        "tick_size": str(rules.tick_size),
        "qty_step": str(rules.qty_step),
        "min_order_qty": str(rules.min_order_qty),
        "min_notional": str(rules.min_notional),
        "max_order_qty": str(rules.max_order_qty),
        "max_market_order_qty": (
            None if rules.max_market_order_qty is None else str(rules.max_market_order_qty)
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_trades_inside(
    trades: Sequence[ReplayTradeResult],
    dataset: HistoricalDataset,
    label: str,
) -> None:
    for trade in trades:
        if trade.opened_at < dataset.started_at or trade.closed_at > dataset.ended_at:
            raise ValueError(f"{label} trade falls outside its dataset period")
        if trade.closed_at < trade.opened_at:
            raise ValueError(f"{label} trade closes before it opens")


def _exposure_seconds(trades: Sequence[ReplayTradeResult]) -> Decimal:
    intervals = sorted(
        ((trade.opened_at, trade.closed_at) for trade in trades),
        key=lambda item: item[0],
    )
    if not intervals:
        return Decimal("0")
    total = Decimal("0")
    current_start, current_end = intervals[0]
    for opened_at, closed_at in intervals[1:]:
        if opened_at <= current_end:
            current_end = max(current_end, closed_at)
            continue
        total += Decimal(str((current_end - current_start).total_seconds()))
        current_start, current_end = opened_at, closed_at
    total += Decimal(str((current_end - current_start).total_seconds()))
    return total


def _average(values: Sequence[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal("0")) / Decimal(len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def parameters_fingerprint(parameters: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _json_safe(parameters),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=str)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)

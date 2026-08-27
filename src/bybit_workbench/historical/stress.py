from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from bybit_workbench.strategies.base import Strategy

from .market_data import HistoricalMarketData
from .runner import HistoricalRunConfig, HistoricalRunResult, run_strategy
from .validation import HistoricalDataset


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    fee_rate: Decimal
    slippage_percent: Decimal
    execution_delay_bars: int = 0
    gap_every_n_bars: int | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("stress scenario name is required")
        if self.fee_rate < 0 or self.slippage_percent < 0:
            raise ValueError("stress costs cannot be negative")
        if self.execution_delay_bars < 0:
            raise ValueError("execution delay cannot be negative")
        if self.gap_every_n_bars is not None and self.gap_every_n_bars < 2:
            raise ValueError("gap_every_n_bars must be at least 2")


@dataclass(frozen=True, slots=True)
class StressRunResult:
    scenario: StressScenario
    run: HistoricalRunResult


async def evaluate_stress_scenarios(
    strategy_factory: Callable[[], Strategy],
    dataset: HistoricalDataset,
    *,
    parameters: Mapping[str, object] | None,
    config: HistoricalRunConfig,
    scenarios: Sequence[StressScenario],
    market_data: HistoricalMarketData | None = None,
) -> tuple[StressRunResult, ...]:
    if not scenarios:
        raise ValueError("at least one stress scenario is required")
    results: list[StressRunResult] = []
    for scenario in scenarios:
        replay = replace(
            config.replay,
            fee_rate=scenario.fee_rate,
            maker_fee_rate=scenario.fee_rate,
            taker_fee_rate=scenario.fee_rate,
            slippage_percent=scenario.slippage_percent,
            execution_delay_bars=scenario.execution_delay_bars,
        )
        stressed_dataset = _with_synthetic_gaps(dataset, scenario.gap_every_n_bars)
        run = await run_strategy(
            strategy_factory(),
            stressed_dataset,
            parameters=parameters,
            config=replace(config, replay=replay),
            market_data=(None if market_data is None else market_data.slice_for(stressed_dataset)),
        )
        results.append(StressRunResult(scenario, run))
    return tuple(results)


def _with_synthetic_gaps(
    dataset: HistoricalDataset,
    every: int | None,
) -> HistoricalDataset:
    if every is None:
        return dataset
    candles = tuple(
        candle for index, candle in enumerate(dataset.candles, start=1) if index % every != 0
    )
    return HistoricalDataset(candles)

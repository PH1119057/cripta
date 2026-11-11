"""R03 bounded cold history seed: native source data, not a new producer.

This is a source-ownership handoff for five specified LINEAR assets and
confirmed 1/5/15 bars. Other symbols, timeframes and the protected P90 OPEN
restart path keep their existing policy until separate roadmap acceptance.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.candle_warmup import (
    R03_NATIVE_CLOSED_SYMBOLS,
    load_verified_r03_native_closed_seed,
)

FetchHistory = Callable[..., dict[str, tuple[Candle, ...]]]


def prepare_initial_r03_observer_history(
    connection: Any, *, symbol: str, observed_at: datetime,
    intervals: tuple[str, ...], p90l90_full_prev7d: bool,
    p90_gap_from: Mapping[str, datetime] | None,
    native_candle_reader: bool, source_fetch: FetchHistory,
) -> dict[str, tuple[Candle, ...]]:
    """Fast-path only exact owned native keys; never relax OPEN recovery.

    A current P90 recovery watermark can request a strictly larger
    historical window, so preserve the older exact recovery function
    without guessing or silently reducing that obligation.
    """
    if (
        not native_candle_reader
        or symbol not in R03_NATIVE_CLOSED_SYMBOLS
        or p90_gap_from
    ):
        return source_fetch(
            symbol, observed_at, intervals,
            p90l90_full_prev7d=p90l90_full_prev7d,
            p90_gap_from=p90_gap_from,
        )

    native_intervals = tuple(
        tf for tf in intervals if tf in ("1", "5", "15")
    )
    if not native_intervals:
        return source_fetch(
            symbol, observed_at, intervals,
            p90l90_full_prev7d=p90l90_full_prev7d,
            p90_gap_from=p90_gap_from,
        )
    # Existing Observer _fetch_history() demands 1000 closed bars per
    # interval except 2600 for P90 seven-day 5m geometry. Do not silently
    # reduce that demand to the R01 Position minimum of six 1m bars.
    required = {
        tf: 2600 if tf == "5" and p90l90_full_prev7d else 1000
        for tf in native_intervals
    }
    native = load_verified_r03_native_closed_seed(
        connection, symbol=symbol, required_counts=required,
        observed_at=observed_at,
    )
    other_intervals = tuple(tf for tf in intervals if tf not in native_intervals)
    other = (
        source_fetch(
            symbol, observed_at, other_intervals,
            p90l90_full_prev7d=p90l90_full_prev7d,
            p90_gap_from=p90_gap_from,
        )
        if other_intervals else {}
    )
    if native.keys() != required.keys() or set(other) != set(other_intervals):
        raise RuntimeError("R03_OBSERVER_SEED_INPUT_SCOPE_MISMATCH")
    # The ordinary 60m geometry stays on the existing provider path until
    # its own consumer migration. Each native bar is still exact SOURCE data
    # and is rechecked by cache_approved_bybit_history() with no INSERT.
    return {**native, **other}

"""P90L90 no-I/O event-order parity against the frozen research implementation.

Compares the exact native tick state machine and after-fee math; this is
NOT a claim of full 90-day source-event replay parity.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90CausalExit,
    modeled_pnl_after_fees,
)
from scripts.research.p90l90_exit_90d_v2 import (
    Position,
    gross_and_net,
    initial_position,
    on_trade,
)

D = Decimal


@pytest.mark.parametrize(("side", "prices", "targets"), [
    ("LONG", ("100.20", "100.50", "100.90", "100.66"),
     (None, None, None, None)),
    ("LONG", ("100.2", "100.6", "100.7", "101.2"),
     (None, None, "101", None)),
    ("LONG", ("99.5", "98", "94", "89"),
     (None, None, None, None)),
    ("SHORT", ("99.9", "99.5", "99.1", "99.4"),
     (None, None, None, None)),
    ("SHORT", ("99.7", "99.5", "99.3", "98.7"),
     (None, None, "99", None)),
    ("SHORT", ("100.3", "103", "108", "111"),
     (None, None, None, None)),
])
def test_p90l90_reference_exit_native_tick_parity(side, prices, targets):
    seed = {"side": side, "price": "100", "ts": "1"}
    historical = initial_position(
        seed, seq=0, bucket=0, cohort="PREV7D", exit_kind="TRAIL035"
    )
    current = P90L90CausalExit(
        side=side,  # type: ignore[arg-type]
        entry_price=D("100"), entry_sequence=0, state_bucket=0,
    )
    for sequence, (px, tp) in enumerate(zip(prices, targets, strict=True), start=1):
        bucket = 300 if sequence >= 3 else 0
        historical, expected_close = on_trade(
            historical, D(px), sequence, bucket,
            None if tp is None else D(tp), "TRAIL035",
        )
        actual_close = current.on_trade(
            price=D(px), sequence=sequence, bucket=bucket,
            candidate_opposite_raw_tp=None if tp is None else D(tp),
        )
        if expected_close is None:
            assert actual_close is None
            assert historical is not None
            assert current.stop == historical.stop
            assert current.protected == historical.protected
            assert current.best_favorable_pct == historical.best_favorable
            assert current.target == historical.tp
            assert current.target_live_after_sequence == historical.tp_live_after_seq
            continue
        assert actual_close is not None
        reason, exit_price, maker = expected_close
        assert (actual_close.reason, actual_close.modeled_price,
                actual_close.resting_maker_proxy) == (reason, exit_price, maker)
        # The reference position is None at closure, hence the fee ledger
        # is calculated on the same modeled reference Entry and target price.
        # gross_and_net only relies on the reference position's Entry fields.
        ref_for_fees = Position(
            side=side, entry=D("100"), entry_seq=0, entry_ts=1.,
            state_bucket=0, stop=D("90") if side == "LONG" else D("110"),
        )
        gross, entry_fee, exit_fee, net = gross_and_net(
            ref_for_fees, exit_price, maker_exit=maker
        )
        actual = modeled_pnl_after_fees(
            side=side,  # type: ignore[arg-type]
            entry_price=D("100"), exit_price=exit_price,
            entry_notional_usdt=D("10"), maker_exit=maker,
        )
        assert (actual.gross_usdt, actual.entry_fee_usdt,
                actual.exit_fee_usdt, actual.net_usdt) == (
                    gross, entry_fee, exit_fee, net
                )
        break

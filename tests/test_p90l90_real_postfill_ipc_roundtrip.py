"""Real Linux abstract-UDS postfill proof round-trip (no Bybit POST)."""
from __future__ import annotations

import sys
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from test_p90l90_real_postfill_source_broker import EPOCH, SHA, _market

from bybit_workbench.exchange_position_identity import exchange_position_key
from operations.connectivity.p90l90_real_postfill_source_client import (
    P90RealPostfillSourceClientUnverified,
    read_real_postfill_source_window,
)
from operations.monitoring.p90l90_real_postfill_source_broker import (
    serve_source_windows,
)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux SO_PEERCRED abstract socket")
def test_actual_private_to_market_source_uds_proof_with_live_native_ids() -> None:
    mirror, position, now = _market()
    # The real IPC contract requires an exact persistent ta-<32 hex> account,
    # unlike the generic observation unit tests' deliberately synthetic IDs.
    account = "ta-" + "a" * 32
    position = replace(
        position,
        trading_account_id=account,
        exchange_position_key=exchange_position_key(
            account, position.symbol, 0,
        ),
    )
    # A busy native interval must stay complete, not lose all trades merely
    # because 32 events occurred during a real first-fill handoff window.
    for i in range(64):
        event_at = now - timedelta(microseconds=100_000 - i * 1000)
        mirror.record_message({
            "topic": "publicTrade.LTCUSDT",
            "data": [{
                "s": "LTCUSDT", "i": f"burst-ipc-{i}",
                "seq": 1000 + i, "T": int(event_at.timestamp() * 1000),
                "S": "Buy", "p": "100.01", "v": "0.01",
            }],
        }, received_at=event_at)
    stopping = threading.Event()
    ready = threading.Event()
    errors: list[str] = []
    requested: list[tuple[str, str, str]] = []

    def read_durable(position_id: str, account: str, symbol: str):
        requested.append((position_id, account, symbol))
        assert (position_id, account, symbol) == (
            position.strategy_position_id,
            position.trading_account_id,
            position.symbol,
        )
        return position, EPOCH

    def worker() -> None:
        try:
            serve_source_windows(
                mirror=mirror,
                source_commit=SHA,
                source_epoch_id=EPOCH,
                read_position=read_durable,
                running=lambda: not stopping.is_set(),
                ready=ready.set,
            )
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert ready.wait(3)
        response = read_real_postfill_source_window(
            position=position,
            source_epoch_id=EPOCH,
            source_commit=SHA,
            timeout_seconds=3.0,
        )
        assert response.position_id == position.strategy_position_id
        assert response.complete_native_window is True
        assert len(response.source_refs) == 66
        assert response.covered_through >= position.initial_protection_confirmed_at
        assert requested == [(
            position.strategy_position_id,
            position.trading_account_id,
            position.symbol,
        )]
    finally:
        stopping.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert not errors


@pytest.mark.skipif(sys.platform != "linux", reason="Linux SO_PEERCRED abstract socket")
def test_absent_market_source_broker_fails_closed_without_exchange_action() -> None:
    _mirror, position, _now = _market()
    account = "ta-" + "a" * 32
    position = replace(
        position,
        trading_account_id=account,
        exchange_position_key=exchange_position_key(account, position.symbol, 0),
    )
    with pytest.raises(
        P90RealPostfillSourceClientUnverified,
        match="TRANSPORT_UNAVAILABLE",
    ):
        read_real_postfill_source_window(
            position=position,
            source_epoch_id=EPOCH,
            source_commit=SHA,
            timeout_seconds=0.2,
        )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux SO_PEERCRED abstract socket")
def test_empty_native_interval_has_independent_rest_proof_over_actual_ipc() -> None:
    from datetime import UTC, datetime

    mirror, position, now = _market(trades=0)
    account = "ta-" + "a" * 32
    position = replace(
        position, trading_account_id=account,
        exchange_position_key=exchange_position_key(account, position.symbol, 0),
    )
    stopping = threading.Event()
    ready = threading.Event()
    faults: list[str] = []
    rest_reads: list[str] = []
    anchor = {
        "symbol": position.symbol, "execId": "exact-before-fill-id",
        "time": int((position.fill_at - timedelta(seconds=1)).timestamp() * 1000),
        "seq": 100, "side": "Buy",
    }

    def fetch_recent(symbol: str):
        rest_reads.append(symbol)
        return [anchor], datetime.now(UTC) + timedelta(milliseconds=50)

    def serve() -> None:
        try:
            serve_source_windows(
                mirror=mirror, source_commit=SHA,
                source_epoch_id=EPOCH,
                read_position=lambda *_args: (position, EPOCH),
                fetch_recent_trades=fetch_recent,
                running=lambda: not stopping.is_set(),
                ready=ready.set,
            )
        except Exception as exc:
            faults.append(type(exc).__name__)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        assert ready.wait(3)
        window = read_real_postfill_source_window(
            position=position, source_epoch_id=EPOCH, source_commit=SHA,
            timeout_seconds=3.0,
        )
        assert window.no_new_native_trade_rest_verified is True
        assert window.best_favorable_price == position.actual_avg_fill
        assert len(window.source_refs) == 1
        assert window.source_refs[0].startswith(
            "bybit:recentTrade:LTCUSDT:zero-after-fill:"
        )
        assert rest_reads == [position.symbol]
    finally:
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert faults == []

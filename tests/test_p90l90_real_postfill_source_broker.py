"""Native REAL postfill MarketSource: original mirror and broker, no orders."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_p90l90_real_observation import _scope

from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import ContinuityNotProvable
from operations.monitoring.p90l90_real_postfill_source_broker import (
    P90RealPostfillMarketSourceUnverified,
    build_source_window,
    checked_request,
)

SHA = "a" * 40
EPOCH = "p90-real-epoch-" + "b" * 40


def _market(*, trades: int = 2):
    now = datetime.now(UTC).replace(microsecond=0)
    fill = now - timedelta(milliseconds=250)
    mirror = PublicTradeMirrorBuffer(("LTCUSDT",), retention_seconds=600)
    mirror.start_epoch(now - timedelta(seconds=2))
    for n in range(trades):
        market_at = now - timedelta(milliseconds=160 - n * 50)
        mirror.record_message({
            "topic": "publicTrade.LTCUSDT",
            "data": [{
                "s": "LTCUSDT", "i": f"real-native-{n}",
                "seq": 500 + n, "T": int(market_at.timestamp() * 1000),
                "S": "Buy", "p": str(100 + n / 10), "v": "0.05",
            }],
        }, received_at=market_at + timedelta(milliseconds=5))
    pos, *_ = _scope()
    pos = replace(
        pos, fill_at=fill,
        initial_protection_confirmed_at=fill + timedelta(milliseconds=5),
    )
    return mirror, pos, now


def test_real_mirror_proves_bounded_original_postfill_window() -> None:
    mirror, pos, now = _market()
    source = build_source_window(
        mirror=mirror, position=pos, source_commit=SHA,
        source_epoch_id=EPOCH, evidence_epoch_id=EPOCH, at=now,
    )
    assert source.complete_native_window
    assert source.position_id == pos.strategy_position_id
    assert source.fill_at == pos.fill_at
    assert source.last_native_price > 0
    assert len(source.source_refs) == 2
    assert source.source_refs[0].startswith("bybit:publicTrade:LTCUSDT:")
    assert source.best_favorable_price >= pos.actual_avg_fill


@pytest.mark.parametrize("reason", ("epoch", "mirror-gap", "empty", "expired"))
def test_real_mirror_refuses_unproven_fill_forward_window(reason: str) -> None:
    mirror, pos, now = _market(trades=0 if reason == "empty" else 2)
    kwargs = {
        "mirror": mirror, "position": pos, "source_commit": SHA,
        "source_epoch_id": EPOCH, "evidence_epoch_id": EPOCH, "at": now,
    }
    if reason == "epoch":
        kwargs["evidence_epoch_id"] = "p90-real-epoch-" + "c" * 40
    elif reason == "mirror-gap":
        mirror.mark_gap("lost websocket")
    elif reason == "expired":
        kwargs["at"] = now + timedelta(seconds=601)
    with pytest.raises(
        (P90RealPostfillMarketSourceUnverified, ContinuityNotProvable),
    ):
        build_source_window(**kwargs)


def test_mirror_rejects_native_replay_after_restart_and_clock_drift() -> None:
    mirror, pos, now = _market()
    mirror.start_epoch(now)
    with pytest.raises(ContinuityNotProvable):
        mirror.verified_fill_forward_events(
            symbol=pos.symbol, fill_at=pos.fill_at, at=now,
        )
    with pytest.raises(ContinuityNotProvable):
        mirror.verified_fill_forward_events(
            symbol="OTHER", fill_at=pos.fill_at, at=now,
        )


def test_source_ipc_request_rejects_fake_position_or_epoch() -> None:
    data = {
        "kind": "POSTFILL_SOURCE_WINDOW", "version": 1,
        "nonce": "d" * 32, "position_id": "pos-123",
        "trading_account_id": "ta-" + "e" * 32,
        "symbol": "LTCUSDT", "source_epoch_id": EPOCH,
    }
    assert checked_request(data, source_epoch_id=EPOCH) == (
        "pos-123", "ta-" + "e" * 32, "LTCUSDT", "d" * 32,
    )
    for changed in (
        {"trading_account_id": "ta-foreign"},
        {"source_epoch_id": "another"},
        {"nonce": "invalid"},
        {"position_id": ""},
        {"caller_fill_at": "forged"},
    ):
        with pytest.raises(P90RealPostfillMarketSourceUnverified):
            checked_request({**data, **changed}, source_epoch_id=EPOCH)


def test_busy_liquid_market_keeps_all_native_exec_ids_below_ipc_frame_limit() -> None:
    """64 real source trades cannot be discarded by a 32-ref transport cap."""
    mirror, pos, now = _market(trades=0)
    for i in range(64):
        market_at = now - timedelta(microseconds=160_000 - i * 1000)
        mirror.record_message({
            "topic": "publicTrade.LTCUSDT",
            "data": [{
                "s": "LTCUSDT", "i": f"burst-{i}",
                "seq": 1000 + i, "T": int(market_at.timestamp() * 1000),
                "S": "Buy", "p": "100.01", "v": "0.01",
            }],
        }, received_at=market_at)
    result = build_source_window(
        mirror=mirror, position=pos, source_commit=SHA,
        source_epoch_id=EPOCH, evidence_epoch_id=EPOCH, at=now,
    )
    assert len(result.source_refs) == 64
    from bybit_workbench.universal_exit.p90l90_real_private_ipc import _frame
    from operations.monitoring.p90l90_real_postfill_source_broker import (
        window_to_wire,
    )
    assert len(_frame({"window": window_to_wire(result)})) < 32768


def test_native_window_explicitly_bounds_high_density_burst() -> None:
    mirror, pos, now = _market(trades=0)
    for i in range(257):
        market_at = now - timedelta(microseconds=200_000 - i * 500)
        mirror.record_message({
            "topic": "publicTrade.LTCUSDT",
            "data": [{
                "s": "LTCUSDT", "i": f"heavy-{i}",
                "seq": 2000 + i, "T": int(market_at.timestamp() * 1000),
                "S": "Buy", "p": "100.01", "v": "0.01",
            }],
        }, received_at=market_at)
    with pytest.raises(
        P90RealPostfillMarketSourceUnverified,
        match="EPOCH_RACE_OR_UNBOUNDED",
    ):
        build_source_window(
            mirror=mirror, position=pos, source_commit=SHA,
            source_epoch_id=EPOCH, evidence_epoch_id=EPOCH, at=now,
        )

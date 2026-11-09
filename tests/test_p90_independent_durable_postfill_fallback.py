"""P90 independent Exit can survive Observer broker loss without invented proof."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_legacy_strategy import SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from operations.connectivity import p90_real_independent_exit as exit_mod
from operations.monitoring import p90l90_real_durable_postfill_source as source

AT = datetime(2026, 11, 9, 4, 7, 30, tzinfo=UTC)
SESSION = "a" * 32
COMMIT = "b" * 40
EPOCH = "p90-real-epoch-" + "c" * 40
SYMBOL = "ARBUSDT"
MARKET = BYBIT_PUBLIC_MARKET_SOURCE_ID
INSTRUMENT = bybit_instrument_id("linear", SYMBOL)
OLD = "native-before-fill"
FIRST = "native-after-fill-one"
LAST = "native-after-fill-two"


def position() -> StrategyPosition:
    return cast(StrategyPosition, SimpleNamespace(
        strategy_position_id="POS-test-first-fill",
        strategy_id=SUB_ID, strategy_version=VERSION, symbol=SYMBOL,
        identity_status="PROVEN",
        trading_account_id="ta-" + "d" * 32,
        strategy_execution_binding_id="binding-test",
        fill_at=AT,
        initial_protection_confirmed_at=AT + timedelta(seconds=2),
        actual_avg_fill=Decimal("0.1736275"),
        direction=TradeDirection.SHORT,
    ))


def trade(ident: str, seq: int, at: datetime, price: str) -> dict[str, object]:
    return {
        "symbol": SYMBOL, "execId": ident, "seq": seq,
        "time": int(at.timestamp() * 1000), "price": price,
        "size": "0.1", "side": "Buy",
    }


def event(ident: str, seq: int, at: datetime, price: str, frame: int):
    return {
        "frame_number": frame, "frame_index": 0,
        "market_source_id": MARKET, "instrument_id": INSTRUMENT,
        "external_symbol": SYMBOL, "exchange_sequence": seq,
        "exec_id": ident, "event_at": at,
        "accepted_at": at + timedelta(milliseconds=80),
        "journal_at": at, "price": Decimal(price),
        "quantity": Decimal("0.1"), "taker_side": "Buy",
        "source_transport": "BYBIT_WS_PUBLIC_TRADE",
        "producer_release_commit": COMMIT,
    }


class Curs:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class Store:
    def __init__(self):
        self.epoch = {"source_commit": COMMIT, "status": "ACTIVE"}
        self.session = {
            "source_session_id": SESSION,
            "market_source_id": MARKET,
            "producer_release_commit": COMMIT,
            "state": "ACTIVE",
            "subscribed_at": AT - timedelta(minutes=3),
            "last_accepted_at": AT + timedelta(seconds=6),
            "ended_at": None, "reason": None,
        }
        self.rows = [
            event(FIRST, 101, AT + timedelta(seconds=1), "0.1730", 5),
            event(LAST, 102, AT + timedelta(seconds=3), "0.1720", 6),
        ]
        self.sql: list[str] = []

    def execute(self, sql, params):
        self.sql.append(sql)
        assert any(word in sql for word in (
            "p90l90_real_source_epochs",
            "native_public_trade_source_sessions",
            "native_public_trade_source_events",
        ))
        if "p90l90_real_source_epochs" in sql:
            assert params == (EPOCH,)
            return Curs([self.epoch])
        if "native_public_trade_source_sessions" in sql:
            assert params == (MARKET,)
            return Curs([self.session])
        assert params == (SESSION, SYMBOL, AT, AT + timedelta(seconds=5), 257)
        return Curs(self.rows[:257])


def venue(_symbol: str):
    assert _symbol == SYMBOL
    return (
        [
            trade(LAST, 102, AT + timedelta(seconds=3), "0.1720"),
            trade(FIRST, 101, AT + timedelta(seconds=1), "0.1730"),
            trade(OLD, 100, AT - timedelta(seconds=1), "0.1737"),
        ],
        AT + timedelta(seconds=6),
    )


def verified(store: Store, *, venue_fn=venue, **changes):
    options = {
        "position": position(), "source_epoch_id": EPOCH,
        "source_commit": COMMIT,
        "accepted_producer_commits": frozenset((COMMIT,)),
        "observed_at": AT + timedelta(seconds=5),
        "fetch_recent": venue_fn,
    }
    options.update(changes)
    return source.read_independent_durable_postfill_window(store, **options)


def test_exact_original_pg_and_bybit_window_allows_short_firstfill_proof():
    db = Store()
    output = verified(db)
    assert output.complete_native_window is True
    assert output.no_new_native_trade_rest_verified is False
    assert output.covered_through == AT + timedelta(seconds=5)
    assert output.last_native_price == Decimal("0.1720")
    assert output.best_favorable_price == Decimal("0.1720")
    assert len(output.source_refs) == 2
    assert output.source_refs == (
        f"bybit:publicTrade:{SYMBOL}:{FIRST}",
        f"bybit:publicTrade:{SYMBOL}:{LAST}",
    )
    assert all("INSERT" not in item and "UPDATE" not in item
               for item in db.sql)


def test_no_new_trade_has_independent_older_rest_anchor_and_no_fake_tick():
    db = Store()
    db.rows = []

    def no_new(_symbol):
        return ([trade(OLD, 100, AT - timedelta(seconds=1), "0.1737")],
                AT + timedelta(seconds=6))

    result = verified(db, venue_fn=no_new)
    assert result.no_new_native_trade_rest_verified
    assert result.last_native_price == position().actual_avg_fill
    assert result.best_favorable_price == position().actual_avg_fill
    assert result.source_refs[0].startswith(
        f"bybit:recentTrade:{SYMBOL}:zero-after-fill:"
    )


@pytest.mark.parametrize("failure", [
    "gap", "unapproved-producer", "session-postfill", "session-stale",
    "epoch-mismatch", "missing-source", "extra-source",
    "changed-price", "changed-seq", "changed-time",
    "wrong-market", "wrong-transport", "missing-pre-fill-venue",
    "wrong-venue-seq", "rest-too-old", "stale-window",
])
def test_incomplete_or_wrong_original_market_source_remains_blocked(failure):
    db = Store()
    other_venue = venue
    options: dict[str, Any] = {}
    if failure == "gap":
        db.session["state"] = "GAP"
    elif failure == "unapproved-producer":
        db.session["producer_release_commit"] = "f" * 40
    elif failure == "session-postfill":
        db.session["subscribed_at"] = AT + timedelta(seconds=1)
    elif failure == "session-stale":
        db.session["last_accepted_at"] = AT - timedelta(seconds=1)
    elif failure == "epoch-mismatch":
        db.epoch["source_commit"] = "f" * 40
    elif failure == "missing-source":
        db.rows.pop()
    elif failure == "extra-source":
        db.rows.append(event("not-on-bybit", 102, AT + timedelta(seconds=3),
                             "0.1720", 7))
    elif failure == "changed-price":
        db.rows[1]["price"] = Decimal("0.1719")
    elif failure == "changed-seq":
        db.rows[1]["exchange_sequence"] = 99
    elif failure == "changed-time":
        db.rows[1]["event_at"] = AT + timedelta(seconds=4)
    elif failure == "wrong-market":
        db.rows[0]["market_source_id"] = "someone-else"
    elif failure == "wrong-transport":
        db.rows[0]["source_transport"] = "BYBIT_REST_KLINE"
    elif failure == "missing-pre-fill-venue":
        other_venue = lambda _symbol: (venue(_symbol)[0][:-1],
                                       AT + timedelta(seconds=6))
    elif failure == "wrong-venue-seq":
        def corrupt(_symbol):
            out, at = venue(_symbol)
            x = [dict(item) for item in out]
            x[0]["seq"] = 99
            return x, at
        other_venue = corrupt
    elif failure == "rest-too-old":
        other_venue = lambda _symbol: (venue(_symbol)[0], AT)
    elif failure == "stale-window":
        options["observed_at"] = AT + timedelta(seconds=18)
    with pytest.raises(source.P90IndependentDurablePostfillUnverified):
        verified(db, venue_fn=other_venue, **options)


def test_rest_window_truncated_at_latest_1000_blocks_old_anchor():
    rows = [
        trade(f"new-{index}", 10000 - index,
              AT + timedelta(seconds=4), "0.172")
        for index in range(1000)
    ]
    with pytest.raises(source.P90IndependentDurablePostfillUnverified,
                       match="VENUE_PREFILL_ANCHOR_EVICTED"):
        verified(Store(), venue_fn=lambda _symbol: (
            rows, AT + timedelta(seconds=6),
        ))


def test_exit_fallback_only_for_exact_transport_outage(monkeypatch):
    proof = object()
    calls = []

    def lost(**_kwargs):
        calls.append("broker")
        raise exit_mod.P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_TRANSPORT_UNAVAILABLE"
        )

    def approved(_db, **kwargs):
        calls.append("producer")
        assert kwargs["accepted_producer_commits"] == frozenset((COMMIT,))
        assert kwargs["source_epoch_id"] == EPOCH
        return proof

    monkeypatch.setattr(exit_mod, "read_real_postfill_source_window", lost)
    from operations.monitoring import p90l90_real_durable_postfill_source as mod
    monkeypatch.setattr(mod, "read_independent_durable_postfill_window", approved)
    result = exit_mod.read_firstfill_source_with_independent_market_fallback(
        Store(), position=position(), source_epoch_id=EPOCH,
        source_commit=COMMIT,
        accepted_producer_commits=frozenset((COMMIT,)),
    )
    assert result is proof
    assert calls == ["broker", "producer"]


def test_invalid_broker_source_proof_not_bypassed(monkeypatch):
    calls = []

    def bad(**_kwargs):
        calls.append("broker")
        raise exit_mod.P90RealPostfillSourceClientUnverified(
            "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE"
        )

    from operations.monitoring import p90l90_real_durable_postfill_source as mod
    monkeypatch.setattr(exit_mod, "read_real_postfill_source_window", bad)
    monkeypatch.setattr(
        mod, "read_independent_durable_postfill_window",
        lambda *_a, **_k: calls.append("producer"),
    )
    with pytest.raises(exit_mod.P90RealPostfillSourceClientUnverified):
        exit_mod.read_firstfill_source_with_independent_market_fallback(
            Store(), position=position(), source_epoch_id=EPOCH,
            source_commit=COMMIT,
            accepted_producer_commits=frozenset((COMMIT,)),
        )
    assert calls == ["broker"]


def test_successful_broker_proof_makes_no_secondary_market_request(monkeypatch):
    first = object()
    monkeypatch.setattr(
        exit_mod, "read_real_postfill_source_window", lambda **_kwargs: first,
    )
    from operations.monitoring import p90l90_real_durable_postfill_source as mod
    def unexpected(*_args, **_kwargs):
        raise AssertionError("never query fallback for a good broker")
    monkeypatch.setattr(mod, "read_independent_durable_postfill_window",
                        unexpected)
    assert exit_mod.read_firstfill_source_with_independent_market_fallback(
        Store(), position=position(), source_epoch_id=EPOCH,
        source_commit=COMMIT, accepted_producer_commits=frozenset((COMMIT,)),
    ) is first

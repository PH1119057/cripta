"""Dedicated publicTrade MarketSource producer: exact native ID and fail-closed tests."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from operations.monitoring.native_public_trade_source import (
    NativeSourceInputUnverified,
    decode_native_trades,
)

RELEASE = "a" * 40
SYMBOLS = frozenset(("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT"))


def _trade(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "s": "LTCUSDT", "i": "test-exec-1", "seq": 1234,
        "T": 1794150000123, "p": "51.75", "v": "0.12", "S": "Buy",
    }
    row.update(overrides)
    return row


def test_real_bybit_public_trade_identity_survives_independent_capture() -> None:
    rows = decode_native_trades(
        {"topic": "publicTrade.LTCUSDT", "data": [_trade()]},
        symbols=SYMBOLS, release=RELEASE,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.external_symbol == "LTCUSDT"
    assert row.exec_id == "test-exec-1" and row.exchange_sequence == 1234
    assert row.event_at == datetime.fromtimestamp(1794150000.123, UTC)
    assert row.price == Decimal("51.75")
    assert row.quantity == Decimal("0.12")
    assert row.taker_side == "Buy"
    assert row.producer_release_commit == RELEASE


@pytest.mark.parametrize("message", [
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(i="")]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(seq=-1)]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(T=0)]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(v="0")]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(S="Middle")]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(s="BTCUSDT")]},
    {"topic": "publicTrade.INJUSDT", "data": [_trade()]},
    {"topic": "publicTrade.LTCUSDT", "data": "not-a-list"},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(), _trade(i="test-exec-1")]},
])
def test_invalid_native_provider_messages_never_become_recorded_facts(
    message: dict[str, object],
) -> None:
    with pytest.raises(NativeSourceInputUnverified):
        decode_native_trades(message, symbols=SYMBOLS, release=RELEASE)


def test_unrelated_market_topics_are_not_native_evidence() -> None:
    assert decode_native_trades(
        {"topic": "kline.5.LTCUSDT", "data": [_trade()]},
        symbols=SYMBOLS, release=RELEASE,
    ) == ()


def test_source_transport_requires_exact_release() -> None:
    with pytest.raises(NativeSourceInputUnverified):
        decode_native_trades(
            {"topic": "publicTrade.LTCUSDT", "data": [_trade()]},
            symbols=SYMBOLS, release="stale",
        )


def test_exact_external_writer_is_claimed_once_and_fails_closed() -> None:
    from operations.monitoring.native_public_trade_source import (
        NativeSourceInputUnverified,
        _acquire_exclusive_producer,
    )

    class Database:
        def __init__(self, granted: bool) -> None:
            self.granted = granted
            self.calls: list[object] = []

        def execute(self, sql: str, params: object) -> Database:
            self.calls.append((sql, params))
            return self

        def fetchone(self) -> tuple[bool]:
            return (self.granted,)

        def commit(self) -> None:
            self.calls.append("commit")

    first = Database(True)
    _acquire_exclusive_producer(first)
    assert "pg_try_advisory_lock" in first.calls[0][0]
    assert first.calls[-1] == "commit"
    second = Database(False)
    with pytest.raises(NativeSourceInputUnverified, match="WRITER_ALREADY_OWNED"):
        _acquire_exclusive_producer(second)


def test_source_commits_trade_before_session_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    from operations.monitoring import native_public_trade_source as native

    rows = decode_native_trades(
        {"topic": "publicTrade.LTCUSDT", "data": [_trade()]},
        symbols=SYMBOLS, release=RELEASE,
    )
    operations: list[str] = []

    class Database:
        @contextmanager
        def transaction(self):
            operations.append("begin")
            yield
            operations.append("commit")

        def execute(self, sql: str, _params: object = ()):
            if "SELECT exchange_sequence" in sql:
                operations.append("cursor-read")
                return self
            if "source_cursors" in sql:
                operations.append("cursor")
            elif "source_sessions" in sql:
                operations.append("session")
            else:
                raise AssertionError(sql)

        def fetchone(self):
            return None

    class Journal:
        def __init__(self, _db: object) -> None:
            pass

        def append(self, _event: object) -> bool:
            operations.append("trade")
            return True

    monkeypatch.setattr(native, "NativePublicTradeJournal", Journal)
    native._persist_frame(Database(), session="a" * 32,
                          frame_number=1, facts=rows)
    assert operations == [
        "begin", "cursor-read", "trade", "cursor", "session", "commit",
    ]


def test_failed_journal_write_cannot_advance_source_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    from operations.monitoring import native_public_trade_source as native

    rows = decode_native_trades(
        {"topic": "publicTrade.LTCUSDT", "data": [_trade()]},
        symbols=SYMBOLS, release=RELEASE,
    )
    calls: list[str] = []

    class Database:
        @contextmanager
        def transaction(self):
            calls.append("begin")
            yield

        def execute(self, sql: str, _params: object = ()):
            if "SELECT exchange_sequence" in sql:
                calls.append("cursor-read")
                return self
            calls.append("cursor")
            raise AssertionError("source cursor must not advance")

        def fetchone(self):
            return None

    class BrokenJournal:
        def __init__(self, _db: object) -> None:
            pass

        def append(self, _event: object) -> None:
            raise RuntimeError("persist failed")

    monkeypatch.setattr(native, "NativePublicTradeJournal", BrokenJournal)
    with pytest.raises(RuntimeError, match="persist failed"):
        native._persist_frame(Database(), session="a" * 32,
                              frame_number=1, facts=rows)
    assert calls == ["begin", "cursor-read"]


def test_external_producer_install_is_not_a_trading_permission() -> None:
    from pathlib import Path

    source = Path(
        "operations/systemd/cripta-native-public-trade-source.service"
    ).read_text(encoding="utf-8")
    assert "User=cripta" in source
    assert "NoNewPrivileges=true" in source
    assert "ReadOnlyPaths=/srv/cripta/runtime/current" in source
    assert "LoadCredential" not in source
    assert "Environment=CRIPTA_NATIVE_PUBLIC_TRADE_SYMBOLS=" in source
    observer = Path(
        "operations/systemd/cripta-universal-entry-observer.service"
    ).read_text(encoding="utf-8")
    assert "CRIPTA_NATIVE_SOURCE_EXTERNAL=ENABLED" in observer
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert installer.index("systemctl start cripta-native-public-trade-source") < (
        installer.index('if [[ "$service" == "cripta-real-service-broker')
        if False else installer.index("INDEPENDENT_NATIVE_MARKET_SOURCE=ACTIVE_FIVE_SYMBOLS")
    )
    assert "20261108_native_public_trade_source_sessions.sql" in installer
    sql = Path(
        "operations/sql/20261108_native_public_trade_source_sessions.sql"
    ).read_text(encoding="utf-8")
    assert "PRIMARY KEY (source_session_id,external_symbol)" in sql
    assert "REVOKE DELETE,TRUNCATE" in sql


def test_out_of_order_source_frame_never_appends_or_acknowledges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager
    from operations.monitoring import native_public_trade_source as native

    row = decode_native_trades(
        {"topic": "publicTrade.LTCUSDT", "data": [_trade(seq=1234)]},
        symbols=SYMBOLS, release=RELEASE,
    )
    writes: list[str] = []

    class Database:
        @contextmanager
        def transaction(self):
            yield

        def execute(self, sql: str, _params: object = ()):
            assert "SELECT exchange_sequence" in sql
            return self

        def fetchone(self):
            return (1235, row[0].event_at)

    class Journal:
        def __init__(self, _db: object) -> None:
            pass

        def append(self, _event: object) -> None:
            writes.append("unexpected native append")

    monkeypatch.setattr(native, "NativePublicTradeJournal", Journal)
    with pytest.raises(NativeSourceInputUnverified, match="ORDER_REGRESSION"):
        native._persist_frame(Database(), session="a" * 32,
                              frame_number=2, facts=row)
    assert writes == []


def test_releases_never_stop_native_source_as_trading_managed_service() -> None:
    from pathlib import Path
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    managed = installer.split("managed_services=(", 1)[1].split(")", 1)[0]
    assert "cripta-native-public-trade-source.service" not in managed
    assert "systemctl start cripta-native-public-trade-source.service" in installer

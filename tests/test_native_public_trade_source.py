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
            elif "source_events" in sql:
                operations.append("event-order")
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
        "begin", "cursor-read", "trade", "event-order",
        "cursor", "session", "commit",
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
    assert "20261109_native_public_trade_source_event_order.sql" in installer
    # A live writer code upgrade requires its own exact, reviewed cutover.
    # Merely adding this migration must NEVER bypass that production rail.
    assert (
        "NATIVE_SOURCE_PRODUCER_CODE_CHANGED_SEPARATE_CUTOVER_REQUIRED"
        in installer
    )
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


def test_releases_preserve_native_source_state_outside_producer_cutover() -> None:
    from pathlib import Path

    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    managed = installer.split("managed_services=(", 1)[1].split(")", 1)[0]
    assert "cripta-native-public-trade-source.service" not in managed
    assert 'if [[ "$native_code_changed" == "1" ||' in installer
    assert '"$native_source_was_active" == "1" ]]; then' in installer
    assert "systemctl start cripta-native-public-trade-source.service" in installer
    assert "INDEPENDENT_NATIVE_MARKET_SOURCE=PRESERVED_ACTIVE" in installer
    assert "INDEPENDENT_NATIVE_MARKET_SOURCE=PRESERVED_INACTIVE" in installer
    assert "ordinary release unexpectedly started inactive native MarketSource" in installer


@pytest.mark.parametrize(("failure_reason", "recover"), [
    ("PUBLIC_FEED_TRANSPORT_FAILED:PUBLIC_LINEAR_TRANSPORT_FATAL_FULL", True),
    (
        "PUBLIC_FEED_TRANSPORT_FAILED:"
        "PUBLIC_LINEAR_TRANSPORT_FATAL_WEBSOCKETCONNECTIONCLOSEDEXCEPTION",
        True,
    ),
    (
        "PUBLIC_FEED_TRANSPORT_FAILED:PUBLIC_LINEAR_TRANSPORT_FATAL_VALUEERROR",
        False,
    ),
    ("PUBLIC_FEED_EPOCH_INVALID", False),
])
def test_required_linear_transport_reconnect_keeps_single_producer_lock(
    monkeypatch: pytest.MonkeyPatch, failure_reason: str, recover: bool,
) -> None:
    """RED for actual 2026-11-23 crash: no Exchange/WS/DB in this fixture.

    The union owner has already marked the previous epoch GAP before re-raise.
    The native outer loop must retain one advisory-lock owner across a fresh
    session only for exact known transport errors; provider/DB identity
    failures still terminate fail-closed.
    """
    from bybit_workbench.market_data.public_source_second_store import (
        PublicSourceSecondUnavailable,
    )
    from operations.monitoring import native_public_trade_source as native
    from operations.monitoring import public_market_source_union_capture as union

    class Manifest:
        def is_file(self) -> bool:
            return True

        def is_symlink(self) -> bool:
            return False

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            return "[]"

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    locks: list[str] = []
    status: list[str] = []
    attempts: list[str] = []
    sleeps: list[float] = []

    def controlled_run(_db: object, **_kwargs: object) -> None:
        attempts.append("fresh-source-epoch")
        if len(attempts) == 1:
            # Union writer writes a GAP for this old epoch before it raises.
            status.append("GAP_DURABLE_FROM_UNION")
            raise PublicSourceSecondUnavailable(failure_reason)
        native.stopping = True

    monkeypatch.setattr(native, "RELEASE", "a" * 40)
    monkeypatch.setattr(native, "_UNION_CAPTURE_FLAG", "1")
    monkeypatch.setattr(native, "_KLINE_CAPTURE_FLAG", "0")
    monkeypatch.setattr(native, "_R11_MAYAK_ANCHORS_FLAG", "0")
    monkeypatch.setattr(native, "_UNION_MANIFEST_FILE",
                        "/etc/cripta/market-source/required_public_feeds.json")
    monkeypatch.setattr(native, "stopping", False)
    monkeypatch.setattr(native, "Path", lambda _path: Manifest())
    monkeypatch.setattr(native.psycopg, "connect", lambda *_a, **_k: Connection())
    monkeypatch.setattr(native, "_status",
                        lambda state, **_kw: status.append(state))
    monkeypatch.setattr(native, "_acquire_exclusive_producer",
                        lambda _db: locks.append("one-lock"))
    monkeypatch.setattr(native, "_enable_source_union_autocommit", lambda _db: None)
    monkeypatch.setattr(native.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(native.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        union, "parse_explicit_source_manifest",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(union, "run_source_union", controlled_run)
    if recover:
        assert native.main() == 0
        assert attempts == ["fresh-source-epoch", "fresh-source-epoch"]
        assert locks == ["one-lock"]
        assert sleeps == [2.0]
        assert "BLOCKED" not in status
    else:
        with pytest.raises(PublicSourceSecondUnavailable):
            native.main()
        assert attempts == ["fresh-source-epoch"]
        assert locks == ["one-lock"]
        assert sleeps == []
        assert status[-1] == "BLOCKED"

"""R02/R03/R05: no private API, no parallel market writer, no LIVE.

The new source paths are INERT unless the existing native producer is
explicitly configured for an independently reviewed source cutover.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_data.market_publication import (
    MarketPublicationUnavailable,
    publish_market_source_second,
)
from bybit_workbench.market_data.market_source_public_candles import (
    ClosedKlineSourceInvalid,
    decode_confirmed_bybit_klines,
)
from operations.monitoring import native_public_trade_source as native

RELEASE = "a" * 40
SESSION = "a" * 32
SYMBOLS = ("APTUSDT", "LTCUSDT")
AT = datetime(2026, 11, 9, 11, 0, 1, tzinfo=UTC)
START = 1794221700000  # 2026-11-09T10:55:00Z
TF_MS = 300000


def _kline(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "start": START,
        "end": START + TF_MS - 1,
        "interval": "5",
        "open": "55.0", "high": "56.0",
        "low": "54.0", "close": "55.5", "volume": "150.0",
        "confirm": True,
    }
    row.update(changes)
    return {
        "topic": "kline.5.LTCUSDT",
        "type": "snapshot",
        "ts": START + TF_MS + 200,
        "data": [row],
    }


def test_confirmed_bybit_kline_is_exact_exclusive_closed_utc_bar() -> None:
    result = decode_confirmed_bybit_klines(
        _kline(), symbols=frozenset(SYMBOLS),
        intervals=frozenset(("1", "5", "15")), received_at=AT,
    )
    assert len(result) == 1
    candle = result[0]
    assert candle.symbol == "LTCUSDT"
    assert candle.timeframe == "5"
    assert candle.opened_at == datetime.fromtimestamp(START / 1000, UTC)
    assert candle.closed_at == datetime.fromtimestamp(
        (START + TF_MS) / 1000, UTC,
    )
    assert candle.close == Decimal("55.5")
    assert candle.volume == Decimal("150.0")


def test_forming_bar_never_becomes_confirmed_market_source_evidence() -> None:
    message = _kline()
    message["data"][0]["confirm"] = False
    assert decode_confirmed_bybit_klines(
        message, symbols=frozenset(SYMBOLS),
        intervals=frozenset(("5",)), received_at=AT,
    ) == ()


@pytest.mark.parametrize("change", [
    {"start": START + 60000},
    {"end": START + TF_MS},
    {"interval": "15"},
    {"volume": "-3"},
    {"high": "54"},
    {"confirm": "true"},
    {"open": "NaN"},
])
def test_bad_confirmed_candle_is_rejected(change: dict[str, object]) -> None:
    message = _kline(**change)
    with pytest.raises(ClosedKlineSourceInvalid):
        decode_confirmed_bybit_klines(
            message, symbols=frozenset(SYMBOLS),
            intervals=frozenset(("5",)), received_at=AT,
        )


def test_unapproved_topic_and_clock_cannot_enter_source() -> None:
    message = _kline()
    message["topic"] = "kline.5.BTCUSDT"
    with pytest.raises(ClosedKlineSourceInvalid):
        decode_confirmed_bybit_klines(
            message, symbols=frozenset(SYMBOLS),
            intervals=frozenset(("5",)), received_at=AT,
        )
    message = _kline()
    message["ts"] = START + 1000
    with pytest.raises(ClosedKlineSourceInvalid):
        decode_confirmed_bybit_klines(
            message, symbols=frozenset(SYMBOLS),
            intervals=frozenset(("5",)), received_at=AT,
        )


def test_identical_source_socket_is_reused_for_native_and_closed_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class Socket:
        def settimeout(self, _seconds: float) -> None:
            pass

        def send(self, data: str) -> None:
            requests.append(json.loads(data))

        def recv(self) -> str:
            return json.dumps({
                "op": "subscribe", "req_id": "cripta-native-source-v1",
                "success": True, "retCode": 0,
            })

    monkeypatch.setattr(native.websocket, "create_connection",
                        lambda *_a, **_k: Socket())
    socket, buffered = native._subscribe(
        SYMBOLS,
        extra_topics=("kline.1.LTCUSDT", "kline.5.LTCUSDT"),
    )
    assert socket is not None and buffered == ()
    assert len(requests) == 1
    assert requests[0]["args"] == [
        "publicTrade.APTUSDT", "publicTrade.LTCUSDT",
        "kline.1.LTCUSDT", "kline.5.LTCUSDT",
    ]


def test_native_source_candle_write_uses_shared_canonical_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[object, object]] = []

    class Store:
        def __init__(self, _db: object, key: object) -> None:
            self.key = key

        def ingest(self, bars: tuple[object, ...], **kw: object) -> int:
            writes.append((self.key, (bars, kw)))
            return 1

    monkeypatch.setattr(native, "SharedClosedCandleStore", Store)
    monkeypatch.setattr(native, "RELEASE", RELEASE)
    assert native._persist_closed_kline_frame(
        object(), message=_kline(), symbols=SYMBOLS,
        intervals=("1", "5", "15"),
    ) == 1
    assert len(writes) == 1
    key, details = writes[0]
    assert key.instrument_id == "inst-bybit-linear-ltcusdt"
    assert key.timeframe == "5"
    assert details[1]["transport"] == "BYBIT_WS_KLINE"


def test_spot_closed_bar_uses_same_canonical_store_with_spot_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[object] = []

    class Store:
        def __init__(self, _db: object, key: object) -> None:
            self.key = key

        def ingest(self, bars: tuple[object, ...], **_kw: object) -> int:
            writes.append((self.key, bars))
            return 1

    monkeypatch.setattr(native, "SharedClosedCandleStore", Store)
    monkeypatch.setattr(native, "RELEASE", RELEASE)
    assert native._persist_closed_kline_frame(
        object(), message=_kline(), symbols=SYMBOLS,
        intervals=("5",), product="SPOT",
    ) == 1
    assert len(writes) == 1
    key = writes[0][0]
    assert key.product_category == "SPOT"
    assert key.instrument_id == "inst-bybit-spot-ltcusdt"

    with pytest.raises(native.NativeSourceInputUnverified, match="CATEGORY"):
        native._persist_closed_kline_frame(
            object(), message=_kline(), symbols=SYMBOLS,
            intervals=("5",), product="PRIVATE",
        )


def test_source_capture_and_publication_both_default_off() -> None:
    assert native._source_feature_enabled("0") is False
    assert native._source_feature_enabled("1") is True
    with pytest.raises(native.NativeSourceInputUnverified):
        native._source_feature_enabled("YES")
    source = Path(
        "operations/monitoring/native_public_trade_source.py"
    ).read_text(encoding="utf-8")
    assert 'os.environ.get("CRIPTA_SOURCE_CLOSED_KLINES", "0")' in source
    assert 'os.environ.get("CRIPTA_SOURCE_PUBLICATION", "0")' in source
    assert "cripta-private-runtime.service" not in source
    assert "BYBIT_PRIVATE" not in source


class PublicationDB:
    def __init__(self, *, valid_epoch: bool = True) -> None:
        self.valid_epoch = valid_epoch
        self.sql: list[str] = []
        self.writes: list[object] = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql: str, params: object = ()):
        self.sql.append(sql)
        db = self

        class Result:
            def fetchone(self):
                if "FROM analytics.native_public_trade_source_sessions" in sql:
                    if db.valid_epoch:
                        return (
                            "ACTIVE", "ms-bybit-mainnet-public-v1", RELEASE,
                        )
                    return ("GAP", "ms-bybit-mainnet-public-v1", RELEASE)
                if "INSERT INTO analytics.market_publications" in sql:
                    db.writes.append(params)
                    return (27,)
                raise AssertionError(sql)

            def fetchall(self):
                if "FROM analytics.native_public_trade_source_cursors" in sql:
                    return [
                        (
                            "LTCUSDT", "inst-bybit-linear-ltcusdt",
                            5, 0, 123, "trade-abc",
                            AT - timedelta(seconds=2),
                            AT - timedelta(seconds=1),
                        ),
                    ]
                if "FROM analytics.closed_candles_effective" in sql:
                    return [(
                        "LTCUSDT", "5",
                        datetime(2026, 11, 9, 11, 0, tzinfo=UTC),
                    )]
                raise AssertionError(sql)

        return Result()


def test_publisher_stores_only_watermarks_one_immutable_version() -> None:
    db = PublicationDB()
    n = publish_market_source_second(
        db, source_session_id=SESSION,
        producer_release_commit=RELEASE, symbols=SYMBOLS,
        timeframes=("1", "5", "15"), observed_at=AT,
    )
    assert n == 27
    assert len(db.writes) == 1
    params = db.writes[0]
    assert params[3] == AT.replace(microsecond=0)
    native_refs = params[4].obj
    candle_refs = params[5].obj
    coverage = params[6].obj
    assert native_refs["LTCUSDT"]["exec_id"] == "trade-abc"
    assert "price" not in str(native_refs)
    assert candle_refs["LTCUSDT"]["5"].endswith("11:00:00+00:00")
    assert coverage["APTUSDT"]["native"] == "MISSING_IN_SOURCE_EPOCH"
    assert coverage["LTCUSDT"]["closed"]["1"] == "MISSING"
    assert any("ON CONFLICT" in sql for sql in db.sql)


def test_publisher_rejects_gap_epoch_without_new_publication() -> None:
    db = PublicationDB(valid_epoch=False)
    with pytest.raises(MarketPublicationUnavailable, match="NOT_ACTIVE"):
        publish_market_source_second(
            db, source_session_id=SESSION,
            producer_release_commit=RELEASE, symbols=SYMBOLS,
            observed_at=AT,
        )
    assert db.writes == []


def test_inert_schema_requires_a_source_session_not_a_trading_owner() -> None:
    sql = Path(
        "operations/sql/20261110_market_source_publication.sql"
    ).read_text(encoding="utf-8")
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "REFERENCES analytics.native_public_trade_source_sessions" in sql
    assert "REFERENCES identity.market_sources" in sql
    assert "UNIQUE (market_source_id,source_session_id,as_of_second)" in sql
    assert "REVOKE UPDATE,DELETE,TRUNCATE" in sql
    assert installer.index(
        "20261108_native_public_trade_source_sessions.sql"
    ) < installer.index("20261110_market_source_publication.sql")
    assert "systemctl start cripta-native-public-trade-source.service" in installer


def test_r11_original_refs_inserted_in_same_immutable_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bybit_workbench.market_data.market_publication as pub

    calls: list[object] = []

    def collect(_db: object, **kwargs: object):
        calls.append(kwargs)
        return (
            {"SPOT": {
                "source_session_id": SESSION,
                "transport_epoch_id": "b" * 32,
                "source_frame_number": 42,
                "frame_content_sha256": "c" * 64,
            }},
            {"SPOT": "PHYSICAL_FRAME_REF_ONLY",
             "LINEAR": "NO_ORIGINAL_FRAME_IN_CURRENT_EPOCH"},
        )

    monkeypatch.setattr(pub, "collect_r11_original_optional_publication_refs",
                        collect)
    db = PublicationDB()
    assert pub.publish_market_source_second(
        db, source_session_id=SESSION, producer_release_commit=RELEASE,
        symbols=SYMBOLS, observed_at=AT,
        original_optional_transport_epochs={
            "SPOT": "b" * 32, "LINEAR": "d" * 32,
        },
    ) == 27
    assert len(calls) == 1
    assert len(db.writes) == 1
    assert db.writes[0][15].obj["SPOT"]["source_frame_number"] == 42
    assert db.writes[0][16].obj["LINEAR"] == (
        "NO_ORIGINAL_FRAME_IN_CURRENT_EPOCH"
    )
    assert any("original_optional_frame_refs" in sql for sql in db.sql)
    assert any("ON CONFLICT" in sql for sql in db.sql)


def test_r11_invalid_ref_aborts_publication_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bybit_workbench.market_data.market_publication as pub
    from bybit_workbench.market_data.r11_original_publication_refs import (
        R11PublicationOriginalRefsUnverified,
    )

    def fail(_db: object, **_kwargs: object):
        raise R11PublicationOriginalRefsUnverified(
            "R11_PUBLICATION_ORIGINAL_PHYSICAL_ORIGINAL_FRAME_INVALID"
        )

    monkeypatch.setattr(pub, "collect_r11_original_optional_publication_refs",
                        fail)
    db = PublicationDB()
    with pytest.raises(R11PublicationOriginalRefsUnverified):
        pub.publish_market_source_second(
            db, source_session_id=SESSION, producer_release_commit=RELEASE,
            symbols=SYMBOLS, observed_at=AT,
            original_optional_transport_epochs={
                "SPOT": "b" * 32, "LINEAR": "d" * 32,
            },
        )
    assert db.writes == []

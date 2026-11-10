"""R04/R05: source-only ONE forming minute, not canonical confirmed OHLCV."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_data.public_forming_minute_source import (
    FormingMinuteUnverified,
    collect_forming_minute_references,
    persist_forming_minute_fact,
)
from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

AT = datetime(2026, 11, 10, 8, 0, 20, 500000, tzinfo=UTC)
START = AT.replace(second=0, microsecond=0)
SOURCE_MS = int((AT - timedelta(milliseconds=100)).timestamp() * 1000)
SESSION = "a" * 32
RELEASE = "b" * 40
IDENT = bybit_instrument_id("linear", "LTCUSDT")


def _frame(*, confirmed: bool = False, start: datetime = START,
           high: str = "52", low: str = "50", last_at: int = SOURCE_MS,
           frame_type: str = "snapshot") -> dict:
    opened = int(start.timestamp() * 1000)
    return {
        "topic": "kline.1.LTCUSDT",
        "ts": SOURCE_MS,
        "type": frame_type,
        "data": [{
            "start": opened, "end": opened + 59999,
            "interval": "1", "open": "51", "high": high, "low": low,
            "close": "51.5", "volume": "1.1", "turnover": "56.6",
            "confirm": confirmed, "timestamp": last_at,
        }],
    }


def _decode(frame: dict):
    return decode_bybit_public_frame(
        frame, product="LINEAR",
        allowed_symbols=frozenset({"LTCUSDT"}), received_at=AT,
    )


def _need(*, ttl: int = 10) -> RequiredPublicFeed:
    return RequiredPublicFeed(
        consumer_id="MAYAK", market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=IDENT, product="LINEAR", symbol="LTCUSDT",
        dataset="FORMING_KLINE", timeframe="1", freshness_seconds=ttl,
    )


def test_forming_1m_preserves_venue_match_time_and_never_confirms() -> None:
    (fact,) = _decode(_frame())
    assert fact.kind == "FORMING_KLINE"
    assert fact.product == "LINEAR"
    assert fact.payload["confirmed"] is False
    assert fact.payload["provider_last_match_at"] == (
        AT - timedelta(milliseconds=100)
    ).isoformat()
    assert fact.payload["turnover"] == "56.6"
    assert fact.payload["opened_at"] == START.isoformat()
    assert fact.payload["closed_at"] == (
        START + timedelta(minutes=1)
    ).isoformat()
    assert _decode(_frame(confirmed=True)) == ()


@pytest.mark.parametrize("frame,reason", [
    (_frame(high="50"), "OHLC"),
    (_frame(low="-1"), "NUMBER"),
    (_frame(last_at=SOURCE_MS + 60000), "LAST_MATCH"),
    (_frame(frame_type="delta"), "FRAME"),
    (_frame(start=START + timedelta(minutes=1)), "EXPIRED"),
])
def test_bad_unconfirmed_candle_never_becomes_source_evidence(
    frame: dict, reason: str,
) -> None:
    with pytest.raises(PublicSourceFrameInvalid, match=reason):
        _decode(frame)


class FakePG:
    def __init__(self, *, rows: tuple = (), active: bool = True):
        self.rows = rows
        self.active = active
        self.sql: list[str] = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql: str, _params: object = ()):
        self.sql.append(sql)
        owner = self

        class Result:
            def fetchone(self):
                if "FROM analytics.native_public_trade_source_sessions" in sql:
                    return (
                        "ACTIVE" if owner.active else "GAP",
                        BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE,
                    )
                if "INSERT INTO analytics.public_forming_minute_observations" in sql:
                    return (919,)
                raise AssertionError(sql)

            def fetchall(self):
                if "FROM analytics.public_forming_minute_observations" in sql:
                    return list(owner.rows)
                raise AssertionError(sql)

        return Result()


def test_writer_only_accepts_active_native_source_epoch() -> None:
    fact = _decode(_frame())[0]
    db = FakePG()
    assert persist_forming_minute_fact(
        db, source_session_id=SESSION, producer_release_commit=RELEASE,
        fact=fact, received_at=AT,
    ) == 919
    assert any("ON CONFLICT" in q for q in db.sql)
    with pytest.raises(FormingMinuteUnverified, match="NOT_ACTIVE"):
        persist_forming_minute_fact(
            FakePG(active=False), source_session_id=SESSION,
            producer_release_commit=RELEASE, fact=fact, received_at=AT,
        )


def test_only_same_open_minute_and_fresh_source_is_referenced() -> None:
    source_at = AT - timedelta(milliseconds=100)
    latest = (
        919, IDENT, "LINEAR", "LTCUSDT", "1", START,
        START + timedelta(minutes=1), source_at, AT,
        {"open": "51", "high": "52", "low": "50", "close": "51.5",
         "volume": "1.1", "turnover": "56.6"},
    )
    db = FakePG(rows=(latest,))
    publication_at = START + timedelta(seconds=22)
    refs, coverage = collect_forming_minute_references(
        db, source_session_id=SESSION, as_of_second=publication_at,
        requirements=(_need(ttl=10),),
    )
    assert refs[IDENT]["FORMING_KLINE"]["forming_observation_id"] == 919
    assert coverage[IDENT]["FORMING_KLINE"]["status"] == (
        "UNCONFIRMED_OPEN_MINUTE"
    )
    assert coverage[IDENT]["FORMING_KLINE"]["age_seconds"] < 2
    assert "forming_ohlcv" not in str(refs)
    assert "closed_candle" not in str(refs)
    assert "closed_at > %s" in db.sql[-1]


def test_missing_or_expired_minute_never_reused_for_future_decision() -> None:
    db = FakePG()
    refs, coverage = collect_forming_minute_references(
        db, source_session_id=SESSION, as_of_second=START + timedelta(minutes=1),
        requirements=(_need(ttl=30),),
    )
    assert refs == {}
    assert coverage[IDENT]["FORMING_KLINE"]["status"] == "MISSING_OR_STALE"


def test_spot_and_5m_forming_are_out_of_approved_scope() -> None:
    with pytest.raises(FormingMinuteUnverified, match="NOT_SUPPORTED"):
        collect_forming_minute_references(
            FakePG(), source_session_id=SESSION,
            as_of_second=START + timedelta(seconds=23),
            requirements=(RequiredPublicFeed(
                consumer_id="MAYAK", market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
                instrument_id=IDENT, product="LINEAR", symbol="LTCUSDT",
                dataset="FORMING_KLINE", timeframe="5", freshness_seconds=10,
            ),),
        )

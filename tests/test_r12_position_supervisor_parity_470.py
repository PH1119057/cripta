"""R12 #470: publication features preserve Position semantics across restart."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.market_data.r12_position_publication import (
    R12PositionMarketSnapshot,
)
from bybit_workbench.position_supervisor import (
    FeatureEvidence,
    PositionEvent,
    PositionIdentity,
    PositionSupervisor,
)
from bybit_workbench.position_supervisor.models import Quality
from operations.monitoring.position_supervisor import _position_market_features

T0 = datetime(2026, 11, 15, 4, 10, tzinfo=UTC)


def market(
    *,
    publication_id: int,
    at: datetime,
    forming: str,
    closes: tuple[str, ...],
    bid_depth: str = "125000",
    ask_depth: str = "100000",
) -> R12PositionMarketSnapshot:
    return R12PositionMarketSnapshot(
        symbol="LTCUSDT",
        publication_id=publication_id,
        source_session_id="a" * 32,
        producer_release_commit="b" * 40,
        as_of_second=at,
        forming_opened_at=at.replace(second=0),
        forming_source_at=at - timedelta(seconds=1),
        forming_close=Decimal(forming),
        closed_closes_newest_first=tuple(Decimal(x) for x in closes),
        ticker_source_at=at - timedelta(seconds=1),
        last_price=Decimal(forming),
        bid_price=Decimal(forming) - Decimal("0.1"),
        ask_price=Decimal(forming) + Decimal("0.1"),
        public_mark_price=Decimal(forming) + Decimal("0.02"),
        book_source_at=at - timedelta(seconds=2),
        top25_bid_depth_usdt=Decimal(bid_depth),
        top25_ask_depth_usdt=Decimal(ask_depth),
    )


def evidence(
    snapshot: R12PositionMarketSnapshot,
) -> dict[str, FeatureEvidence]:
    price, execution, book = _position_market_features(
        snapshot, side="Buy",
    )
    return {
        "structure": FeatureEvidence(
            "hold", snapshot.as_of_second, Quality.FRESH,
        ),
        "price_1m": price,
        "flow": FeatureEvidence(
            "favorable", snapshot.as_of_second, Quality.FRESH,
        ),
        "absorption": FeatureEvidence(
            "none", snapshot.as_of_second, Quality.FRESH,
        ),
        "orderbook": book,
        "oi_price": FeatureEvidence(
            "available", snapshot.as_of_second, Quality.FRESH,
        ),
        "execution_now": execution,
    }


def test_r12_exact_legacy_price_and_top25_book_semantics() -> None:
    snap = market(
        publication_id=41,
        at=T0 + timedelta(seconds=10),
        forming="101",
        closes=("100", "99", "98", "97", "96", "95"),
    )
    price, execution, book = _position_market_features(snap, side="Buy")
    assert price.measurements["move_1m_pct"] == Decimal("1.00")
    assert price.measurements["move_3m_pct"] == (
        Decimal("101") / Decimal("98") - 1
    ) * 100
    assert price.measurements["move_5m_pct"] == (
        Decimal("101") / Decimal("96") - 1
    ) * 100
    assert price.state == "continuation"
    assert execution.measurements["bid"] == Decimal("100.9")
    assert execution.measurements["ask"] == Decimal("101.1")
    assert book.measurements["bid_depth_usdt"] == Decimal("125000")
    assert book.measurements["ask_depth_usdt"] == Decimal("100000")
    assert book.measurements["directional_ratio"] == Decimal("1.25")
    assert book.state == "replenishment"


def test_r12_restart_matches_uninterrupted_position_state() -> None:
    identity = PositionIdentity(
        position_id="position-r12",
        symbol="LTCUSDT",
        side="Buy",
        actual_avg_fill=Decimal("100"),
        qty=Decimal("1"),
        fill_time=T0,
    )
    first_market = market(
        publication_id=41,
        at=T0 + timedelta(seconds=10),
        forming="101",
        closes=("100", "99", "98", "97", "96", "95"),
    )
    second_market = market(
        publication_id=42,
        at=T0 + timedelta(seconds=20),
        forming="102",
        closes=("101", "100", "99", "98", "97", "96"),
        bid_depth="130000",
        ask_depth="100000",
    )

    uninterrupted = PositionSupervisor(identity)
    first = uninterrupted.update(PositionEvent(
        first_market.as_of_second,
        Decimal("101"),
        evidence(first_market),
    ))

    restored = PositionSupervisor(identity)
    restored.restore_path(
        mfe_pct=first.mfe_pct,
        mae_pct=first.mae_pct,
        state=first.state,
        state_since=first.state_since,
        last_at=first.observed_at,
    )

    expected = uninterrupted.update(PositionEvent(
        second_market.as_of_second,
        Decimal("102"),
        evidence(second_market),
    ))
    actual = restored.update(PositionEvent(
        second_market.as_of_second,
        Decimal("102"),
        evidence(second_market),
    ))
    assert actual.audit_dict() == expected.audit_dict()
    assert actual.mfe_pct == Decimal("2.00")
    assert actual.mae_pct == Decimal("0")
    assert actual.features["price_1m"].measurements["publication_id"] == "42"

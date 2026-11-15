from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.mayak.provider_continuity import (
    R11ProviderObservation,
    R11ProviderQualityBoundary,
    prove_r11_provider_continuity,
    r11_provider_continuity_proof_matches,
    r11_required_provider_scopes,
    read_r11_provider_continuity_for_candidate,
)

SESSION = "a" * 32
SPOT_EPOCH = "b" * 32
RELEASE = "c" * 40
AT = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
START = AT - timedelta(hours=3)


def boundary(
    product: str, *,
    topic: str = "*",
    symbol: str = "",
    quality: str = "RECOVERING",
    reason: str | None = None,
    observed_at: datetime = START,
    epoch: str | None = None,
) -> R11ProviderQualityBoundary:
    return R11ProviderQualityBoundary(
        transport_epoch_id=epoch or (
            SPOT_EPOCH if product == "SPOT" else SESSION
        ),
        product=product,
        topic=topic,
        symbol=symbol,
        quality=quality,
        reason=reason or f"PUBLIC_{product}_SUBSCRIPTIONS_ACKED",
        observed_at=observed_at,
    )


def evidence() -> tuple[
    tuple[R11ProviderQualityBoundary, ...],
    tuple[R11ProviderObservation, ...],
]:
    boundaries = (boundary("SPOT"), boundary("LINEAR"))
    observations: list[R11ProviderObservation] = [
        R11ProviderObservation(
            transport_epoch_id=SPOT_EPOCH,
            product="SPOT", topic="*", symbol="",
            observed_at=AT - timedelta(seconds=1),
            kind="TRANSPORT_FRAME",
        ),
        R11ProviderObservation(
            transport_epoch_id=SESSION,
            product="LINEAR", topic="*", symbol="",
            observed_at=AT - timedelta(seconds=1),
            kind="TRANSPORT_FRAME",
        ),
    ]
    for scope in r11_required_provider_scopes():
        if scope.coverage != "POINT_IN_TIME":
            continue
        observations.append(R11ProviderObservation(
            transport_epoch_id=(
                SPOT_EPOCH if scope.product == "SPOT" else SESSION
            ),
            product=scope.product, topic=scope.topic, symbol=scope.symbol,
            observed_at=AT - timedelta(seconds=60),
            kind="REST_FACT",
        ))
    return boundaries, tuple(observations)


def prove(
    boundaries: tuple[R11ProviderQualityBoundary, ...] | None = None,
    observations: tuple[R11ProviderObservation, ...] | None = None,
    *,
    source_state: str = "ACTIVE",
    spot_epoch: str = SPOT_EPOCH,
):
    original_boundaries, original_observations = evidence()
    return prove_r11_provider_continuity(
        publication_id=91,
        source_session_id=SESSION,
        release_commit=RELEASE,
        as_of_second=AT,
        source_state=source_state,
        subscribed_at=START - timedelta(seconds=1),
        transport_epochs={"SPOT": spot_epoch, "LINEAR": SESSION},
        boundaries=(original_boundaries if boundaries is None else boundaries),
        observations=(
            original_observations if observations is None else observations
        ),
    )


def test_exact_63_scope_panel_has_real_green_continuity() -> None:
    proof = prove()

    assert len(proof.scopes) == 63
    assert proof.provider_continuity_verified is True
    assert proof.mayak_ready is False
    assert all(
        row.state in ("CONTINUOUS", "POINT_CURRENT")
        for row in proof.scopes
    )
    assert proof.scope(
        "LINEAR", "PUBLIC_TRADE", "BTCUSDT",
    ).continuous_from == START
    assert proof.scope(
        "SPOT", "INSTRUMENT_INFO", "ETHUSDT",
    ).state == "POINT_CURRENT"
    assert r11_provider_continuity_proof_matches(
        proof, publication_id=91, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=AT,
    )
    assert not r11_provider_continuity_proof_matches(
        replace(proof, scopes=proof.scopes[:-1]),
        publication_id=91, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=AT,
    )


@pytest.mark.parametrize(
    ("boundaries", "source_state"),
    [
        ((boundary("LINEAR"),), "ACTIVE"),
        ((boundary("SPOT"), boundary("LINEAR")), "GAP"),
    ],
)
def test_missing_ack_or_gapped_source_cannot_be_green(
    boundaries: tuple[R11ProviderQualityBoundary, ...],
    source_state: str,
) -> None:
    proof = prove(boundaries, source_state=source_state)

    assert proof.provider_continuity_verified is False
    assert any(row.state in ("WARMUP", "UNKNOWN") for row in proof.scopes)


def test_reconnect_ack_without_later_physical_witness_is_warmup() -> None:
    boundaries, observations = evidence()
    reconnected_at = AT - timedelta(seconds=10)
    boundaries += (boundary("SPOT", observed_at=reconnected_at),)
    observations = tuple(
        row for row in observations
        if not (row.product == "SPOT" and row.topic == "*")
    )

    proof = prove(boundaries, observations)

    assert proof.provider_continuity_verified is False
    assert proof.scope("SPOT", "PUBLIC_TRADE", "APTUSDT").state == "WARMUP"


def test_global_gap_after_ack_is_not_repaired_by_frame_presence() -> None:
    boundaries, observations = evidence()
    boundaries += (boundary(
        "SPOT", quality="GAP", reason="PUBLIC_SPOT_TRANSPORT_UNAVAILABLE",
        observed_at=AT - timedelta(seconds=2),
    ),)

    proof = prove(boundaries, observations)

    assert proof.provider_continuity_verified is False
    assert proof.scope("SPOT", "ORDERBOOK_50", "BTCUSDT").state == "WARMUP"


def test_scoped_book_recovery_requires_new_snapshot_not_delta() -> None:
    boundaries, observations = evidence()
    topic = "orderbook.50.BTCUSDT"
    fault_at = AT - timedelta(seconds=20)
    boundaries += (boundary(
        "LINEAR", topic=topic, symbol="BTCUSDT", quality="UNKNOWN",
        reason="PUBLIC_BOOK_SEQUENCE_GAP", observed_at=fault_at,
    ),)
    delta = R11ProviderObservation(
        transport_epoch_id=SESSION,
        product="LINEAR", topic=topic, symbol="BTCUSDT",
        observed_at=fault_at + timedelta(seconds=1), kind="SOURCE_EVENT",
    )

    red = prove(boundaries, observations + (delta,))
    assert red.provider_continuity_verified is False
    assert red.scope("LINEAR", "ORDERBOOK_50", "BTCUSDT").state == "WARMUP"

    snapshot = replace(delta, kind="BOOK_SNAPSHOT")
    green = prove(boundaries, observations + (delta, snapshot))
    assert green.provider_continuity_verified is True
    assert green.scope(
        "LINEAR", "ORDERBOOK_50", "BTCUSDT",
    ).continuous_from == snapshot.observed_at


def test_scoped_unknown_needs_post_boundary_exact_event() -> None:
    boundaries, observations = evidence()
    topic = "tickers.ETHUSDT"
    fault_at = AT - timedelta(seconds=15)
    boundaries += (boundary(
        "LINEAR", topic=topic, symbol="ETHUSDT", quality="UNKNOWN",
        reason="PUBLIC_TOPIC_ACCUMULATOR_UNVERIFIED", observed_at=fault_at,
    ),)

    red = prove(boundaries, observations)
    assert red.provider_continuity_verified is False

    event = R11ProviderObservation(
        transport_epoch_id=SESSION,
        product="LINEAR", topic=topic, symbol="ETHUSDT",
        observed_at=fault_at + timedelta(seconds=1), kind="SOURCE_EVENT",
    )
    green = prove(boundaries, observations + (event,))
    assert green.provider_continuity_verified is True


def test_stale_rest_is_not_promoted_to_stream_continuity() -> None:
    boundaries, observations = evidence()
    observations = tuple(
        replace(row, observed_at=AT - timedelta(seconds=651))
        if row.topic == "REST:market/account-ratio:APTUSDT" else row
        for row in observations
    )

    proof = prove(boundaries, observations)

    scope = proof.scope("LINEAR", "ACCOUNT_RATIO", "APTUSDT")
    assert proof.provider_continuity_verified is False
    assert scope.state == "UNKNOWN"
    assert scope.continuous_from is None


def test_transport_epoch_change_cannot_reuse_old_ack_or_warm_state() -> None:
    proof = prove(spot_epoch="d" * 32)

    assert proof.provider_continuity_verified is False
    assert proof.scope("SPOT", "PUBLIC_TRADE", "LTCUSDT").state == "WARMUP"


def test_source_persists_ack_as_recovering_never_ready() -> None:
    code = Path(
        "operations/monitoring/public_market_source_union_capture.py"
    ).read_text(encoding="utf-8")

    assert 'reason="PUBLIC_LINEAR_SUBSCRIPTIONS_ACKED"' in code
    assert 'reason="PUBLIC_SPOT_SUBSCRIPTIONS_ACKED"' in code
    assert 'quality="RECOVERING"' in code
    assert "quality=\"READY\"" not in code


def test_rr_candidate_reader_uses_durable_quality_and_rest_receipts() -> None:
    class Rows:
        def __init__(self, rows: list[tuple[object, ...]]) -> None:
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class DB:
        def execute(self, sql: str, _params=()):
            if sql == "SHOW transaction_isolation":
                return Rows([("repeatable read",)])
            if sql == "SHOW transaction_read_only":
                return Rows([("on",)])
            if "native_public_trade_source_sessions" in sql:
                return Rows([(
                    "ACTIVE", START - timedelta(seconds=1),
                    BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE,
                )])
            if "public_market_source_quality_events" in sql:
                return Rows([
                    (
                        SPOT_EPOCH, "SPOT", "*", "", "RECOVERING",
                        "PUBLIC_SPOT_SUBSCRIPTIONS_ACKED", START,
                    ),
                    (
                        SESSION, "LINEAR", "*", "", "RECOVERING",
                        "PUBLIC_LINEAR_SUBSCRIPTIONS_ACKED", START,
                    ),
                ])
            raise AssertionError(sql)

    _boundaries, observations = evidence()
    witnesses = tuple(row for row in observations if row.topic == "*")
    rest = tuple(
        SimpleNamespace(
            symbol=symbol, source_session_id=SESSION,
            release_commit=RELEASE,
            ratio_received_at=AT - timedelta(seconds=60),
            spot_instrument_received_at=AT - timedelta(seconds=60),
            linear_instrument_received_at=AT - timedelta(seconds=60),
            spot_supported=True, linear_supported=True,
        )
        for symbol in (
            "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
            "BTCUSDT", "ETHUSDT",
        )
    )

    proof = read_r11_provider_continuity_for_candidate(
        DB(), publication_id=91, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=AT,
        transport_epochs={"SPOT": SPOT_EPOCH, "LINEAR": SESSION},
        transport_witnesses=witnesses,
        optional_events=(), rest_facts=rest,
    )

    assert proof.provider_continuity_verified is True
    assert len(proof.scopes) == 63

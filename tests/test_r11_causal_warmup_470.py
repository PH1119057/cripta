from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from bybit_workbench.mayak.causal_warmup import (
    evaluate_r11_causal_warmup,
    r11_stream_continuity_starts,
    reconcile_r11_engine_continuity,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.native_trade_replay_parity import R11ReplayCursor
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.provider_continuity import (
    R11ProviderObservation,
    R11ProviderQualityBoundary,
    prove_r11_provider_continuity,
    r11_required_provider_scopes,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestCandidate,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakObservationCandidate,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
SESSION = "a" * 32
SPOT_EPOCH = "b" * 32
RELEASE = "c" * 40
AT = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
START = AT - timedelta(seconds=7205)


def provider_proof():
    boundaries = tuple(
        R11ProviderQualityBoundary(
            transport_epoch_id=(SPOT_EPOCH if product == "SPOT" else SESSION),
            product=product, topic="*", symbol="", quality="RECOVERING",
            reason=f"PUBLIC_{product}_SUBSCRIPTIONS_ACKED",
            observed_at=START,
        )
        for product in ("SPOT", "LINEAR")
    )
    observations: list[R11ProviderObservation] = [
        R11ProviderObservation(
            transport_epoch_id=(SPOT_EPOCH if product == "SPOT" else SESSION),
            product=product, topic="*", symbol="",
            observed_at=AT - timedelta(seconds=1),
            kind="TRANSPORT_FRAME",
        )
        for product in ("SPOT", "LINEAR")
    ]
    for scope in r11_required_provider_scopes():
        if scope.coverage != "POINT_IN_TIME":
            continue
        observations.append(R11ProviderObservation(
            transport_epoch_id=(
                SPOT_EPOCH if scope.product == "SPOT" else SESSION
            ),
            product=scope.product, topic=scope.topic, symbol=scope.symbol,
            observed_at=AT - timedelta(seconds=10), kind="REST_FACT",
        ))
    return prove_r11_provider_continuity(
        publication_id=101, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=AT,
        source_state="ACTIVE", subscribed_at=START - timedelta(seconds=1),
        transport_epochs={"SPOT": SPOT_EPOCH, "LINEAR": SESSION},
        boundaries=boundaries, observations=tuple(observations),
    )


def prepared_engine() -> LiveMayakEngine:
    engine = LiveMayakEngine(SEVEN)
    engine.set_instrument_support("spot", set(SEVEN))
    engine.set_instrument_support("linear", set(SEVEN))
    for symbol in SEVEN:
        for market in ("spot", "linear"):
            engine.on_trade(
                market, symbol, (AT - timedelta(seconds=7199)).timestamp(),
                "Buy", 99.0, 1.0,
            )
            engine.on_trade(
                market, symbol, (AT - timedelta(seconds=3601)).timestamp(),
                "Sell", 100.0, 1.0,
            )
            engine.on_trade(
                market, symbol, (AT - timedelta(seconds=1)).timestamp(),
                "Buy", 101.0, 1.0,
            )
        for market in ("spot", "linear"):
            engine.on_book(
                market, symbol, (AT - timedelta(seconds=901)).timestamp(),
                [(99.0, 2.0)], [(101.0, 2.0)],
            )
            engine.on_book(
                market, symbol, (AT - timedelta(seconds=1)).timestamp(),
                [(99.5, 2.0)], [(100.5, 2.0)],
            )
        engine.on_ticker(
            symbol, (AT - timedelta(seconds=3601)).timestamp(),
            open_interest=1000.0, open_interest_value=100000.0,
            funding_rate=0.0001, last_price=100.0,
            mark_price=100.0, index_price=100.0,
        )
        engine.on_ticker(
            symbol, (AT - timedelta(seconds=1)).timestamp(),
            open_interest=1010.0, open_interest_value=101000.0,
            funding_rate=0.0001, last_price=101.0,
            mark_price=101.0, index_price=101.0,
            long_ratio=0.48, short_ratio=0.52,
        )
    return engine


def evaluated(engine: LiveMayakEngine, proof=None):
    proof = proof or provider_proof()
    reconcile_r11_engine_continuity(
        engine, proof=proof, previous_starts={},
    )
    engine.snapshot(AT)
    snapshot = engine.snapshot(AT + timedelta(seconds=1))
    return evaluate_r11_causal_warmup(
        engine, provider_proof=proof, snapshot=snapshot,
        now=AT + timedelta(seconds=1),
    )


def test_full_63_scope_causal_warmup_uses_real_engine_windows() -> None:
    proof = evaluated(prepared_engine())

    assert len(proof.scopes) == 63
    assert proof.causal_warmup_verified is True
    assert proof.dispatcher_coverage_verified is True
    assert proof.durable_commit_verified is False
    assert proof.consumer_ready is False
    assert all(row.state == "READY" for row in proof.scopes)
    trades = tuple(
        row for row in proof.scopes
        if row.provider_scope.dataset == "PUBLIC_TRADE"
    )
    assert len(trades) == 14
    assert all(row.required_seconds == 7200 for row in trades)


def test_elapsed_time_without_book_baseline_is_not_warmup_ready() -> None:
    engine = prepared_engine()
    for key in tuple(engine.book_history):
        engine.book_history[key].popleft()

    proof = evaluated(engine)

    assert proof.causal_warmup_verified is False
    books = tuple(
        row for row in proof.scopes
        if row.provider_scope.dataset == "ORDERBOOK_50"
    )
    assert len(books) == 14
    assert all(row.state == "WARMUP" for row in books)


def test_119_minutes_cannot_cover_current_and_prior_60m_trade_windows() -> None:
    original = provider_proof()
    short_start = AT - timedelta(seconds=7199)
    scopes = tuple(
        replace(row, continuous_from=short_start)
        if row.scope.dataset == "PUBLIC_TRADE" else row
        for row in original.scopes
    )
    short = replace(original, scopes=scopes)

    proof = evaluated(prepared_engine(), short)

    trades = tuple(
        row for row in proof.scopes
        if row.provider_scope.dataset == "PUBLIC_TRADE"
    )
    assert proof.causal_warmup_verified is False
    assert all(row.continuous_seconds == 7199 for row in trades)
    assert all(row.state == "WARMUP" for row in trades)


def test_scoped_book_reset_discards_old_baseline_and_reenters_warmup() -> None:
    old = provider_proof()
    engine = prepared_engine()
    previous = r11_stream_continuity_starts(old)
    reset_at = AT - timedelta(seconds=100)
    scopes = tuple(
        replace(row, continuous_from=reset_at)
        if row.scope.product == "LINEAR"
        and row.scope.dataset == "ORDERBOOK_50"
        and row.scope.symbol == "BTCUSDT"
        else row
        for row in old.scopes
    )
    recovered = replace(old, scopes=scopes)

    starts = reconcile_r11_engine_continuity(
        engine, proof=recovered, previous_starts=previous,
    )

    assert starts["LINEAR", "ORDERBOOK_50", "BTCUSDT"] == reset_at
    assert ("linear", "BTCUSDT") not in engine.books
    assert len(engine.book_history["linear", "BTCUSDT"]) == 1
    assert engine.book_history["linear", "BTCUSDT"][0][0] >= (
        reset_at.timestamp()
    )
    engine.on_book(
        "linear", "BTCUSDT", (AT - timedelta(seconds=1)).timestamp(),
        [(99.0, 1.0)], [(101.0, 1.0)],
    )
    engine.snapshot(AT)
    snapshot = engine.snapshot(AT + timedelta(seconds=1))
    warmup = evaluate_r11_causal_warmup(
        engine, provider_proof=recovered, snapshot=snapshot,
        now=AT + timedelta(seconds=1),
    )
    row = next(
        item for item in warmup.scopes
        if item.provider_scope.product == "LINEAR"
        and item.provider_scope.dataset == "ORDERBOOK_50"
        and item.provider_scope.symbol == "BTCUSDT"
    )
    assert row.state == "WARMUP"
    assert row.continuous_seconds == 100
    assert row.engine_coverage_verified is False


def test_pg_replay_exports_warmup_proof_but_not_consumer_ready() -> None:
    proof = provider_proof()
    engine = prepared_engine()
    engine.snapshot(AT - timedelta(seconds=1))
    optional = tuple(
        R11OptionalMayakCursor(
            source_session_id=SESSION,
            transport_epoch_id=(
                SPOT_EPOCH if product == "SPOT" else SESSION
            ),
            product_category=product,
            producer_release_commit=RELEASE,
            next_cursor=1, next_digest="d" * 64, books=(),
        )
        for product in ("SPOT", "LINEAR")
    )
    native = tuple(
        R11ReplayCursor(
            symbol=symbol, source_session_id=SESSION,
            producer_release_commit=RELEASE, publication_id=101,
            publication_as_of=AT, publication_watermark=(1, 0),
            cursor=(1, 0), last_seq=1,
            last_event_at=AT - timedelta(seconds=1), recent_exec_ids=(),
        )
        for symbol in SEVEN
    )
    rest = tuple(
        R11MayakRestCandidate(
            symbol=symbol, publication_id=101,
            source_session_id=SESSION, release_commit=RELEASE,
            as_of=AT, ratio_sample_at=AT - timedelta(seconds=10),
            long_ratio=0.48, short_ratio=0.52,
            spot_supported=True, linear_supported=True,
        )
        for symbol in SEVEN
    )
    candidate = R11SevenMayakObservationCandidate(
        publication_id=101, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=AT,
        optional_events=(), native_linear_trades=(), rest_facts=rest,
        optional_cursors=optional, native_cursors=native,
        provider_continuity_verified=True,
        provider_continuity_proof=proof,
    )
    replay = R11MayakPublicationReplay(
        engine=engine, require_provider_continuity=True,
    )

    snapshot = replay.apply(candidate, now=AT + timedelta(seconds=1))

    status = snapshot["r11_market_publication"]
    assert status["causal_warmup_verified"] is True
    assert status["warmup_time_sufficient"] is True
    assert len(status["warmup_scopes"]) == 63
    assert status["consumer_ready"] is False
    assert replay.warmup_proof is not None
    assert replay.warmup_proof.causal_warmup_verified is True
    assert all(row["connected"] for row in replay.engine.transport.values())

"""R11 #470: real MAYAK engine stateful PG-only seven-panel replay.

No DB, Bybit sockets, production service or financial trading.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.dispatcher_v2.builders import (
    build_coin_market_context,
    build_global_market_context,
)
from bybit_workbench.mayak.context_records import (
    coin_market_context_records,
    shared_market_context_record,
)
from bybit_workbench.mayak.native_trade_replay_parity import (
    R11MayakTradeInput,
    R11ReplayCursor,
)
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
    R11OptionalMayakEvent,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestCandidate,
)
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    decode_r11_mayak_state,
    encode_r11_mayak_state,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakObservationCandidate,
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
    R11MayakReplayNotReady,
)

AT = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)
SESSION = "a" * 32
RELEASE = "b" * 40
SEVEN = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
         "BTCUSDT", "ETHUSDT")


def candidate(
    *, publication_id: int = 42, as_of: datetime = AT,
    previous: R11SevenMayakReplayCheckpoint | None = None,
    optional_events: tuple[R11OptionalMayakEvent, ...] = (),
    native_trades: tuple[R11MayakTradeInput, ...] = (),
) -> R11SevenMayakObservationCandidate:
    options = tuple(R11OptionalMayakCursor(
        source_session_id=SESSION,
        transport_epoch_id=("c" if product == "SPOT" else "d") * 32,
        product_category=product, producer_release_commit=RELEASE,
        next_cursor=1, next_digest="e" * 64, books=(),
    ) for product in ("SPOT", "LINEAR"))
    native = tuple(R11ReplayCursor(
        symbol=symbol, source_session_id=SESSION,
        producer_release_commit=RELEASE, publication_id=publication_id,
        publication_as_of=as_of, publication_watermark=(1, 0),
        cursor=(1, 0), last_seq=100,
        last_event_at=as_of - timedelta(seconds=2),
        recent_exec_ids=(),
    ) for symbol in SEVEN)
    rest = tuple(R11MayakRestCandidate(
        symbol=symbol, publication_id=publication_id,
        source_session_id=SESSION, release_commit=RELEASE,
        as_of=as_of,
        ratio_sample_at=as_of - timedelta(minutes=2),
        long_ratio=0.48, short_ratio=0.52,
        spot_supported=True, linear_supported=True,
    ) for symbol in SEVEN)
    return R11SevenMayakObservationCandidate(
        publication_id=publication_id, source_session_id=SESSION,
        release_commit=RELEASE, as_of_second=as_of,
        optional_events=optional_events, native_linear_trades=native_trades,
        rest_facts=rest, optional_cursors=options, native_cursors=native,
        read_after_checkpoint=previous,
    )


def native(
    symbol: str, timestamp: float, *,
    accepted_at: datetime | None = None,
) -> R11MayakTradeInput:
    return R11MayakTradeInput(
        symbol=symbol, timestamp=timestamp, side="Buy",
        price=100.5, quantity=0.25,
        frame_number=1, frame_index=0,
        seq=100, exec_id="physical-id-" + symbol,
        accepted_at=(
            accepted_at
            or datetime.fromtimestamp(timestamp, UTC)
            + timedelta(milliseconds=90)
        ),
    )


def optional_spot_trade(
    symbol: str, timestamp: float, *,
    received_at: datetime, frame_number: int,
) -> R11OptionalMayakEvent:
    return R11OptionalMayakEvent(
        event="TRADE", product="SPOT", symbol=symbol, timestamp=timestamp,
        source_frame_number=frame_number, source_fact_index=0,
        source_sequence=100 + frame_number,
        exec_id=f"spot-{symbol}-{frame_number}", side="Buy",
        price=100.25, quantity=0.5, received_at=received_at,
        frame_type="snapshot",
    )


def test_full_seven_panel_real_mayak_snapshot_and_dispatcher_lineage() -> None:
    engine = R11MayakPublicationReplay()
    first = candidate(native_trades=(native("BTCUSDT", AT.timestamp()-2),))
    snapshot = engine.apply(first, now=AT + timedelta(seconds=5))
    assert set(snapshot["coins"]) == set(SEVEN)
    assert snapshot["btc"] == snapshot["coins"]["BTCUSDT"]
    assert snapshot["eth"] == snapshot["coins"]["ETHUSDT"]
    assert snapshot["r11_market_publication"]["publication_id"] == 42
    assert snapshot["r11_market_publication"]["source_mode"] == (
        "PG_ORIGINAL_REPLAY"
    )
    assert not snapshot["r11_market_publication"]["consumer_ready"]
    assert not snapshot["r11_market_publication"]["mayak_ws_retirement_allowed"]
    assert snapshot["r11_market_publication"]["warmup_seconds_observed"] == 0
    assert snapshot["money_breadth"]["derivatives_coverage"]["total"] == 7
    handoff = snapshot["dispatcher_handoff"]
    assert handoff["provenance"]["r11_market_publication_id"] == 42
    assert handoff["provenance"]["r11_source_session_id"] == SESSION
    assert handoff["provenance"]["config_fingerprint"] == (
        snapshot["config_fingerprint"]
    )
    assert engine.checkpoint == first.checkpoint()
    assert not engine.failed
    assert engine.observations == 1


def test_incremental_publication_preserves_prior_engine_trade_window() -> None:
    replay = R11MayakPublicationReplay()
    first = candidate(native_trades=(native("BTCUSDT", AT.timestamp()-2),))
    replay.apply(first, now=AT + timedelta(seconds=5))
    second_at = AT + timedelta(seconds=20)
    second = candidate(publication_id=43, as_of=second_at,
                       previous=first.checkpoint(),
                       native_trades=(native("ETHUSDT", second_at.timestamp()-2),))
    snapshot = replay.apply(second, now=second_at + timedelta(seconds=5))
    assert replay.observations == 2
    assert replay.checkpoint == second.checkpoint()
    assert len(replay.engine.trades[("linear", "BTCUSDT")].rows) == 1
    assert len(replay.engine.trades[("linear", "ETHUSDT")].rows) == 1
    assert snapshot["dispatcher_handoff"]["provenance"][
        "r11_market_publication_id"
    ] == 43
    assert not snapshot["r11_market_publication"]["consumer_ready"]



def test_restart_matches_uninterrupted_seven_symbol_dispatcher_output() -> None:
    first = candidate(
        native_trades=(native("BTCUSDT", AT.timestamp() - 2),),
    )
    second_at = AT + timedelta(seconds=20)

    uninterrupted = R11MayakPublicationReplay()
    uninterrupted.apply(first, now=AT + timedelta(seconds=5))
    second = candidate(
        publication_id=43,
        as_of=second_at,
        previous=first.checkpoint(),
        native_trades=(native("ETHUSDT", second_at.timestamp() - 2),),
    )
    expected = uninterrupted.apply(
        second, now=second_at + timedelta(seconds=5),
    )

    before_restart = R11MayakPublicationReplay()
    before_restart.apply(first, now=AT + timedelta(seconds=5))
    restored = decode_r11_mayak_state(
        encode_r11_mayak_state(before_restart),
        expected_source_session_id=SESSION,
        expected_release_commit=RELEASE,
    )
    resumed_second = candidate(
        publication_id=43,
        as_of=second_at,
        previous=restored.checkpoint,
        native_trades=(native("ETHUSDT", second_at.timestamp() - 2),),
    )
    actual = restored.apply(
        resumed_second, now=second_at + timedelta(seconds=5),
    )

    assert actual == expected
    assert actual["dispatcher_handoff"] == expected["dispatcher_handoff"]
    assert restored.checkpoint == uninterrupted.checkpoint
    assert restored.observations == uninterrupted.observations

    expected_shared = shared_market_context_record(7001, expected)
    actual_shared = shared_market_context_record(7001, actual)
    expected_global = build_global_market_context(
        expected_shared, now=second_at + timedelta(seconds=5),
    )
    actual_global = build_global_market_context(
        actual_shared, now=second_at + timedelta(seconds=5),
    )
    assert actual_global == expected_global

    expected_coins = coin_market_context_records(7001, expected)
    actual_coins = coin_market_context_records(7001, actual)
    assert len(actual_coins) == len(SEVEN)
    assert actual_coins == expected_coins
    for expected_coin, actual_coin in zip(
        expected_coins, actual_coins, strict=True,
    ):
        expected_dispatch = build_coin_market_context(
            expected_coin,
            global_context_id=expected_global.global_context_id,
            now=second_at + timedelta(seconds=5),
        )
        actual_dispatch = build_coin_market_context(
            actual_coin,
            global_context_id=actual_global.global_context_id,
            now=second_at + timedelta(seconds=5),
        )
        assert actual_dispatch == expected_dispatch


def test_cross_stream_provider_time_interleaving_uses_receiver_order() -> None:
    replay = R11MayakPublicationReplay()
    first = candidate(native_trades=(
        native(
            "BTCUSDT", AT.timestamp() - 2,
            accepted_at=AT - timedelta(seconds=1),
        ),
    ))
    replay.apply(first, now=AT + timedelta(seconds=5))

    second_at = AT + timedelta(seconds=20)
    # This SPOT provider timestamp is older than the last LINEAR/BTC event,
    # but it belongs to an independent stream and was received later.
    second = candidate(
        publication_id=43, as_of=second_at, previous=first.checkpoint(),
        optional_events=(
            optional_spot_trade(
                "BTCUSDT", AT.timestamp() - 3,
                received_at=second_at - timedelta(seconds=1),
                frame_number=2,
            ),
        ),
    )
    replay.apply(second, now=second_at + timedelta(seconds=5))

    assert replay.observations == 2
    assert len(replay.engine.trades[("linear", "BTCUSDT")].rows) == 1
    assert len(replay.engine.trades[("spot", "BTCUSDT")].rows) == 1
    assert replay.last_original_event_at == AT.timestamp() - 2


def test_same_stream_provider_time_regression_still_fails_closed() -> None:
    replay = R11MayakPublicationReplay()
    first = candidate(optional_events=(
        optional_spot_trade(
            "BTCUSDT", AT.timestamp() - 2,
            received_at=AT - timedelta(seconds=1),
            frame_number=1,
        ),
    ))
    replay.apply(first, now=AT + timedelta(seconds=5))
    second_at = AT + timedelta(seconds=20)
    second = candidate(
        publication_id=43, as_of=second_at, previous=first.checkpoint(),
        optional_events=(
            optional_spot_trade(
                "BTCUSDT", AT.timestamp() - 3,
                received_at=second_at - timedelta(seconds=1),
                frame_number=2,
            ),
        ),
    )
    with pytest.raises(
        R11MayakReplayNotReady, match="ORIGINAL_EVENT_TIME_REGRESSED",
    ):
        replay.apply(second, now=second_at + timedelta(seconds=5))
    assert replay.failed


@pytest.mark.parametrize("corrupt", [
    "mixed_epoch", "detached_cursor", "faked_ready", "foreign_symbol",
    "more_pages", "wrong_rest", "foreign_native_cursor",
    "same_stream_late_event",
])
def test_invalid_source_fails_closed_and_poisoned_replay_stays_stopped(
    corrupt: str,
) -> None:
    replay = R11MayakPublicationReplay()
    first = candidate(native_trades=(native("BTCUSDT", AT.timestamp()-2),))
    replay.apply(first, now=AT + timedelta(seconds=5))
    second_at = AT + timedelta(seconds=20)
    following = candidate(
        publication_id=43, as_of=second_at,
        previous=first.checkpoint(),
    )
    if corrupt == "mixed_epoch":
        following = replace(following, source_session_id="f" * 32)
    elif corrupt == "detached_cursor":
        following = replace(following, read_after_checkpoint=None)
    elif corrupt == "faked_ready":
        following = replace(following, mayak_cutover_ready=True)
    elif corrupt == "foreign_symbol":
        following = replace(following, native_linear_trades=(
            native("XRPUSDT", second_at.timestamp()-2),
        ))
    elif corrupt == "more_pages":
        following = replace(following, has_more_source_pages=True)
    elif corrupt == "wrong_rest":
        following = replace(
            following, rest_facts=(
                replace(following.rest_facts[0], release_commit="f" * 40),
                *following.rest_facts[1:],
            ),
        )
    elif corrupt == "foreign_native_cursor":
        following = replace(
            following, native_cursors=(
                replace(following.native_cursors[0], source_session_id="f"*32),
                *following.native_cursors[1:],
            ),
        )
    elif corrupt == "same_stream_late_event":
        following = replace(following, native_linear_trades=(
            native(
                "BTCUSDT", AT.timestamp()-3,
                accepted_at=second_at - timedelta(seconds=1),
            ),
        ))
    with pytest.raises(R11MayakReplayNotReady):
        replay.apply(following, now=second_at+timedelta(seconds=5))
    assert replay.failed
    with pytest.raises(R11MayakReplayNotReady, match="PREVIOUS_SOURCE_FAILURE"):
        replay.apply(candidate(), now=AT+timedelta(seconds=5))

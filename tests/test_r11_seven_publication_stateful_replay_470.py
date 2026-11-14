"""R11 #470: real MAYAK engine stateful PG-only seven-panel replay.

No DB, Bybit sockets, production service or financial trading.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.mayak.native_trade_replay_parity import (
    R11MayakTradeInput,
    R11ReplayCursor,
)
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestCandidate,
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
        optional_events=(), native_linear_trades=native_trades,
        rest_facts=rest, optional_cursors=options, native_cursors=native,
        read_after_checkpoint=previous,
    )


def native(symbol: str, timestamp: float) -> R11MayakTradeInput:
    return R11MayakTradeInput(
        symbol=symbol, timestamp=timestamp, side="Buy",
        price=100.5, quantity=0.25,
        frame_number=1, frame_index=0,
        seq=100, exec_id="physical-id-" + symbol,
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


@pytest.mark.parametrize("corrupt", [
    "mixed_epoch", "detached_cursor", "faked_ready", "foreign_symbol",
    "more_pages", "wrong_rest", "foreign_native_cursor", "late_event",
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
    elif corrupt == "late_event":
        following = replace(following, native_linear_trades=(
            native("ETHUSDT", AT.timestamp()-3),
        ))
    with pytest.raises(R11MayakReplayNotReady):
        replay.apply(following, now=second_at+timedelta(seconds=5))
    assert replay.failed
    with pytest.raises(R11MayakReplayNotReady, match="PREVIOUS_SOURCE_FAILURE"):
        replay.apply(candidate(), now=AT+timedelta(seconds=5))

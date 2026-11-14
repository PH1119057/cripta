"""R11 #470 SOURCE-only fail-closed durable seven-coin engine checkpoint tests."""
from __future__ import annotations

import hashlib
import json
import zlib
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.mayak.native_trade_replay_parity import R11ReplayCursor
from bybit_workbench.mayak.optional_original_event_parity import (
    R11MayakBookState, R11OptionalMayakCursor,
)
from bybit_workbench.mayak.seven_publication_engine_checkpoint import (
    R11MayakStateUnverified,
    atomic_write_r11_mayak_state,
    decode_r11_mayak_state,
    encode_r11_mayak_state,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

AT = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
SESSION = "a" * 32
RELEASE = "b" * 40
SEVEN = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT",
         "LTCUSDT", "BTCUSDT", "ETHUSDT")


def source_checkpoint() -> R11SevenMayakReplayCheckpoint:
    return R11SevenMayakReplayCheckpoint(
        publication_id=100,
        source_session_id=SESSION, release_commit=RELEASE,
        as_of_second=AT,
        optional_cursors=tuple(
            R11OptionalMayakCursor(
                source_session_id=SESSION,
                transport_epoch_id=("c" if product == "SPOT" else "d") * 32,
                product_category=product, producer_release_commit=RELEASE,
                next_cursor=22, next_digest="e" * 64,
                books=(R11MayakBookState(
                    symbol="BTCUSDT", source_sequence=42, update_id=5,
                    bids=((100.0, 5.0),), asks=((100.2, 8.0),),
                ),),
            )
            for product in ("SPOT", "LINEAR")
        ),
        native_cursors=tuple(
            R11ReplayCursor(
                symbol=symbol, source_session_id=SESSION,
                producer_release_commit=RELEASE,
                publication_id=100, publication_as_of=AT,
                publication_watermark=(15, 1), cursor=(15, 1),
                last_seq=155, last_event_at=AT-timedelta(seconds=1),
                recent_exec_ids=("one-" + symbol,),
            )
            for symbol in SEVEN
        ),
    )


def state() -> R11MayakPublicationReplay:
    replay = R11MayakPublicationReplay(
        checkpoint=source_checkpoint(),
        first_publication_at=AT-timedelta(minutes=15),
        observations=22,
        last_original_event_at=AT.timestamp()-1,
    )
    engine = replay.engine
    engine.on_trade("spot", "BTCUSDT", AT.timestamp()-20,
                    "Buy", 100.0, 2.5)
    engine.on_trade("linear", "BTCUSDT", AT.timestamp()-18,
                    "Sell", 100.1, 1.5)
    engine.on_trade("linear", "ETHUSDT", AT.timestamp()-17,
                    "Buy", 100.5, 0.75)
    engine.on_book("spot", "BTCUSDT", AT.timestamp()-10,
                   [(100.0, 4.0), (99.8, 2.0)],
                   [(100.2, 8.0), (100.3, 3.0)])
    engine.on_book("linear", "BTCUSDT", AT.timestamp()-8,
                   [(100.0, 5.0)], [(100.2, 6.0)])
    engine.on_ticker("BTCUSDT", AT.timestamp()-6,
                     open_interest=12345.0, last_price=100.15,
                     funding_rate=-0.001)
    engine.on_ticker("BTCUSDT", AT.timestamp()-4,
                     open_interest=12445.0, funding_rate=-0.0005)
    engine.on_liquidation("BTCUSDT", AT.timestamp()-5,
                          "Sell", 100.4, 3.0)
    engine.set_instrument_support("spot", set(SEVEN))
    engine.set_instrument_support("linear", set(SEVEN))
    return replay


def test_full_feature_window_and_exact_original_cursor_roundtrip() -> None:
    original = state()
    packed = encode_r11_mayak_state(original)
    assert packed.startswith(b"CRIPTA_R11_MAYAK_CHECKPOINT_V1\n")
    restored = decode_r11_mayak_state(
        packed, expected_source_session_id=SESSION,
        expected_release_commit=RELEASE,
    )
    assert restored.checkpoint == original.checkpoint
    assert restored.observations == 22
    assert restored.first_publication_at == original.first_publication_at
    assert restored.last_original_event_at == original.last_original_event_at
    assert restored.engine.ticker_history["BTCUSDT"] == (
        original.engine.ticker_history["BTCUSDT"]
    )
    assert restored.engine.trades["linear", "ETHUSDT"].rows == (
        original.engine.trades["linear", "ETHUSDT"].rows
    )
    assert restored.engine.liquidations.rows == original.engine.liquidations.rows
    now = AT+timedelta(seconds=1)
    expected = original.engine.snapshot(now)
    actual = restored.engine.snapshot(now)
    assert actual == expected
    assert set(actual["coins"]) == set(SEVEN)
    assert not restored.engine.transport["linear"]["connected"]
    assert not restored.engine.transport["spot"]["connected"]
    assert encode_r11_mayak_state(restored) == encode_r11_mayak_state(original)


def test_atomic_local_checkpoint_roundtrip_only_in_disposable_temp(
    tmp_path,
) -> None:
    saved = tmp_path / "r11-mayak-checkpoint.bin"
    bytes_value = encode_r11_mayak_state(state())
    atomic_write_r11_mayak_state(saved, bytes_value)
    assert saved.read_bytes() == bytes_value
    assert not list(tmp_path.glob(".r11-mayak-*.tmp"))


def _repack_with_authored_digest(packed: bytes, change) -> bytes:
    magic, _separator, remainder = packed.partition(b"\n")
    _old_hash, _newline, comp = remainder.partition(b"\n")
    parsed = json.loads(zlib.decompress(comp))
    change(parsed)
    original = json.dumps(
        parsed, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return (magic + b"\n" + hashlib.sha256(original).hexdigest().encode()
            + b"\n" + zlib.compress(original, 6))


@pytest.mark.parametrize("problem", [
    "truncated", "wrong_epoch", "wrong_release", "false_ready",
    "old_format", "new_symbol", "wrong_cursor", "transport_connected",
])
def test_checkpoint_must_never_resume_unproven_data(
    problem: str,
) -> None:
    saved = encode_r11_mayak_state(state())
    if problem == "truncated":
        saved = saved[:-5]
    elif problem == "wrong_epoch":
        with pytest.raises(R11MayakStateUnverified):
            decode_r11_mayak_state(
                saved, expected_source_session_id="f"*32,
                expected_release_commit=RELEASE,
            )
        return
    elif problem == "wrong_release":
        with pytest.raises(R11MayakStateUnverified):
            decode_r11_mayak_state(
                saved, expected_source_session_id=SESSION,
                expected_release_commit="f"*40,
            )
        return
    elif problem == "false_ready":
        saved = _repack_with_authored_digest(
            saved, lambda p: p.update(consumer_ready=True),
        )
    elif problem == "old_format":
        saved = _repack_with_authored_digest(
            saved, lambda p: p.update(format_version=2),
        )
    elif problem == "new_symbol":
        saved = _repack_with_authored_digest(
            saved, lambda p: p["engine"]["trades"]["linear"].update(
                {"XRPUSDT": [[AT.timestamp(), 1.0, 1.0]]}
            ),
        )
    elif problem == "wrong_cursor":
        saved = _repack_with_authored_digest(
            saved, lambda p: p["checkpoint"]["native_cursors"][0].update(
                source_session_id="f"*32
            ),
        )
    elif problem == "transport_connected":
        # This value is not serialized at all; a fake top-level claim must
        # not be accepted as restored transport proof.
        saved = _repack_with_authored_digest(
            saved, lambda p: p.update(
                mayak_ws_retirement_allowed=True
            ),
        )
    with pytest.raises(R11MayakStateUnverified):
        decode_r11_mayak_state(
            saved, expected_source_session_id=SESSION,
            expected_release_commit=RELEASE,
        )


def test_unsynchronized_or_false_connected_engine_is_not_checkpointable() -> None:
    replay = state()
    replay.engine.on_transport(
        "linear", connected=True, timestamp=AT.timestamp(),
    )
    with pytest.raises(R11MayakStateUnverified):
        encode_r11_mayak_state(replay)

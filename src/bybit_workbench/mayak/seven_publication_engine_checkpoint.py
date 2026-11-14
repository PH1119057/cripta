"""R11 #470: bounded, non-executable durable state for a MAYAK PG replay.

The saved bytes are inert versioned JSON+zlib, NEVER pickle. They contain
physical source cursor/book checkpoints and the full causal MAYAK feature
windows. A restart cannot use a source cursor without restoring its exact
engine windows. Every restore re-enters disconnected/WARMUP until separately
verified provider/source continuity and a current publication are read.

This module is SOURCE-ONLY. No production file/DB changes or live enable.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import zlib
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.core.live import LiveMayakEngine, SourceStamp
from bybit_workbench.mayak.native_trade_replay_parity import R11ReplayCursor
from bybit_workbench.mayak.optional_original_event_parity import (
    R11MayakBookState,
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

_SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
_MAGIC = b"CRIPTA_R11_MAYAK_CHECKPOINT_V1\n"
_MAX_RAW = 64 * 1024 * 1024
_MAX_COMPRESSED = 24 * 1024 * 1024
_MAX_ROWS_PER_WINDOW = 1_000_000


class R11MayakStateUnverified(RuntimeError):
    """Damaged/mixed/forged/unbounded state must not skip source replay."""


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise R11MayakStateUnverified("R11_MAYAK_STATE_" + code)


def _utc(raw: object) -> datetime:
    _need(isinstance(raw, str), "TIME_TYPE_INVALID")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise R11MayakStateUnverified("R11_MAYAK_STATE_TIME_INVALID") from exc
    _need(value.tzinfo is not None and value.utcoffset() == timedelta(0),
          "TIME_NOT_UTC")
    return value.astimezone(UTC)


def _finite(raw: object) -> float:
    _need(not isinstance(raw, bool) and isinstance(raw, (int, float)),
          "NONNUMERIC_VALUE")
    number = float(raw)
    _need(math.isfinite(number), "NONFINITE_VALUE")
    return number


def _history(raw: object, *, width: int, limit: int) -> deque[Any]:
    _need(isinstance(raw, list) and len(raw) <= limit, "WINDOW_UNBOUNDED")
    out: deque[Any] = deque()
    for row in raw:
        _need(isinstance(row, list) and len(row) == width,
              "WINDOW_SHAPE_INVALID")
        out.append(tuple(_finite(value) for value in row))
    return out


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _encode_checkpoint(
    checkpoint: R11SevenMayakReplayCheckpoint,
) -> dict[str, Any]:
    _need(
        checkpoint.provider_continuity_verified is False
        and checkpoint.mayak_cutover_ready is False
        and tuple(x.product_category for x in checkpoint.optional_cursors)
        == ("SPOT", "LINEAR")
        and tuple(x.symbol for x in checkpoint.native_cursors) == _SEVEN,
        "CHECKPOINT_NOT_DEGRADED_OR_COMPLETE",
    )
    object_value = {
        "publication_id": checkpoint.publication_id,
        "source_session_id": checkpoint.source_session_id,
        "release_commit": checkpoint.release_commit,
        "as_of_second": checkpoint.as_of_second.isoformat(),
        "optional_cursors": [asdict(item) for item in checkpoint.optional_cursors],
        "native_cursors": [
            {
                **asdict(item),
                "publication_as_of": item.publication_as_of.isoformat(),
                "last_event_at": _timestamp(item.last_event_at),
            }
            for item in checkpoint.native_cursors
        ],
    }
    # The source wire representation is JSON arrays, never Python tuples.
    return json.loads(json.dumps(object_value, allow_nan=False))


def _decode_checkpoint(raw: object) -> R11SevenMayakReplayCheckpoint:
    _need(isinstance(raw, dict), "CHECKPOINT_NOT_OBJECT")
    opt: list[R11OptionalMayakCursor] = []
    for item in raw["optional_cursors"]:
        books = tuple(R11MayakBookState(
            symbol=b["symbol"],
            source_sequence=b["source_sequence"],
            update_id=b["update_id"],
            bids=tuple(tuple(_finite(x) for x in pair) for pair in b["bids"]),
            asks=tuple(tuple(_finite(x) for x in pair) for pair in b["asks"]),
        ) for b in item["books"])
        opt.append(R11OptionalMayakCursor(
            source_session_id=item["source_session_id"],
            transport_epoch_id=item["transport_epoch_id"],
            product_category=item["product_category"],
            producer_release_commit=item["producer_release_commit"],
            next_cursor=item["next_cursor"],
            next_digest=item["next_digest"],
            books=books,
        ))
    native: list[R11ReplayCursor] = []
    for item in raw["native_cursors"]:
        native.append(R11ReplayCursor(
            symbol=item["symbol"],
            source_session_id=item["source_session_id"],
            producer_release_commit=item["producer_release_commit"],
            publication_id=item["publication_id"],
            publication_as_of=_utc(item["publication_as_of"]),
            publication_watermark=tuple(item["publication_watermark"]),
            cursor=tuple(item["cursor"]),
            last_seq=item["last_seq"],
            last_event_at=(
                _utc(item["last_event_at"])
                if item["last_event_at"] is not None else None
            ),
            recent_exec_ids=tuple(item["recent_exec_ids"]),
        ))
    decoded = R11SevenMayakReplayCheckpoint(
        publication_id=raw["publication_id"],
        source_session_id=raw["source_session_id"],
        release_commit=raw["release_commit"],
        as_of_second=_utc(raw["as_of_second"]),
        optional_cursors=tuple(opt),
        native_cursors=tuple(native),
    )
    _need(
        type(decoded.publication_id) is int and decoded.publication_id > 0
        and len(decoded.source_session_id) == 32
        and len(decoded.release_commit) == 40
        and decoded.provider_continuity_verified is False
        and decoded.mayak_cutover_ready is False
        and _encode_checkpoint(decoded) == raw,
        "CHECKPOINT_ROUNDTRIP_DRIFT",
    )
    return decoded


def _engine_payload(engine: LiveMayakEngine) -> dict[str, Any]:
    _need(
        isinstance(engine, LiveMayakEngine)
        and engine.symbols == _SEVEN
        and all(not row.get("connected") for row in engine.transport.values()),
        "ENGINE_SCOPE_OR_FALSE_CONNECTIVITY",
    )
    return {
        "fingerprint": engine.configuration_fingerprint(),
        "trades": {
            market: {
                symbol: list(engine.trades[market, symbol].frozen_view().rows)
                for symbol in _SEVEN
            } for market in ("spot", "linear")
        },
        "stamps": [
            [market, symbol, source, stamp.observed_at,
             stamp.expected_seconds, stamp.stale_seconds]
            for (market, symbol, source), stamp in sorted(engine.stamps.items())
        ],
        "instrument_support": {
            product: (
                sorted(engine.instrument_support[product])
                if engine.instrument_support[product] is not None else None
            )
            for product in ("spot", "linear")
        },
        "tickers": engine.tickers,
        "ticker_history": {
            symbol: list(engine.ticker_history[symbol]) for symbol in _SEVEN
        },
        "funding_history": {
            symbol: list(engine.funding_history[symbol]) for symbol in _SEVEN
        },
        "books": [
            [market, symbol, value]
            for (market, symbol), value in sorted(engine.books.items())
        ],
        "book_history": [
            [market, symbol, list(engine.book_history[market, symbol])]
            for market in ("spot", "linear") for symbol in _SEVEN
        ],
        "previous_books": [
            [market, symbol, value]
            for (market, symbol), value in sorted(engine.previous_books.items())
        ],
        "market_history": list(engine.history),
        "liquidations": list(engine.liquidations.rows),
    }


def encode_r11_mayak_state(replay: R11MayakPublicationReplay) -> bytes:
    _need(
        isinstance(replay, R11MayakPublicationReplay)
        and replay.checkpoint is not None
        and replay.first_publication_at is not None
        and not replay.failed
        and replay.observations > 0,
        "REPLAY_NOT_CHECKPOINTABLE",
    )
    payload = {
        "format_version": 1,
        "checkpoint": _encode_checkpoint(replay.checkpoint),
        "first_publication_at": replay.first_publication_at.isoformat(),
        "last_original_event_at": replay.last_original_event_at,
        "observations": replay.observations,
        "engine": _engine_payload(replay.engine),
        "consumer_ready": False,
        "mayak_ws_retirement_allowed": False,
    }
    try:
        original = json.dumps(
            payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise R11MayakStateUnverified(
            "R11_MAYAK_STATE_SERIALIZATION_INVALID"
        ) from exc
    _need(len(original) <= _MAX_RAW, "STATE_TOO_LARGE")
    packed = zlib.compress(original, 6)
    _need(len(packed) <= _MAX_COMPRESSED, "COMPRESSED_STATE_TOO_LARGE")
    return _MAGIC + hashlib.sha256(original).hexdigest().encode() + b"\n" + packed


def decode_r11_mayak_state(
    binary: bytes,
    *,
    expected_source_session_id: str,
    expected_release_commit: str,
) -> R11MayakPublicationReplay:
    _need(
        isinstance(binary, bytes)
        and binary.startswith(_MAGIC)
        and len(binary) <= len(_MAGIC) + 65 + _MAX_COMPRESSED,
        "STATE_ENVELOPE_INVALID",
    )
    header, sep, compressed = binary[len(_MAGIC):].partition(b"\n")
    _need(
        sep == b"\n" and len(header) == 64,
        "STATE_DIGEST_HEADER_INVALID",
    )
    try:
        decoder = zlib.decompressobj()
        original = decoder.decompress(compressed, _MAX_RAW + 1)
        _need(
            len(original) <= _MAX_RAW
            and not decoder.unconsumed_tail
            and decoder.eof
            and not decoder.unused_data,
            "COMPRESSED_STATE_NOT_BOUNDED",
        )
        _need(
            hashlib.sha256(original).hexdigest().encode() == header,
            "STATE_HASH_MISMATCH",
        )
        payload = json.loads(
            original, parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("nonfinite")
            ),
        )
    except (zlib.error, UnicodeError, ValueError) as exc:
        raise R11MayakStateUnverified(
            "R11_MAYAK_STATE_DATA_CORRUPT"
        ) from exc
    _need(
        isinstance(payload, dict)
        and payload.get("format_version") == 1
        and payload.get("consumer_ready") is False
        and payload.get("mayak_ws_retirement_allowed") is False,
        "STATE_FALSE_READY_OR_VERSION",
    )
    try:
        checkpoint = _decode_checkpoint(payload["checkpoint"])
        _need(
            checkpoint.source_session_id == expected_source_session_id
            and checkpoint.release_commit == expected_release_commit,
            "STATE_DIFFERENT_SOURCE_EPOCH",
        )
        engine = LiveMayakEngine(_SEVEN)
        state = payload["engine"]
        _need(
            state["fingerprint"] == engine.configuration_fingerprint(),
            "ENGINE_CONFIG_CHANGED",
        )
        for product in ("spot", "linear"):
            for symbol in _SEVEN:
                rows = _history(
                    state["trades"][product][symbol],
                    width=3, limit=_MAX_ROWS_PER_WINDOW,
                )
                window = engine.trades[product, symbol]
                window.rows = rows
                window.revision = len(rows)
        for market, symbol, source, at, expected, stale in state["stamps"]:
            _need(
                market in ("spot", "linear")
                and symbol in _SEVEN
                and source in ("trades", "book", "ticker")
                and (at is None or math.isfinite(float(at)))
                and type(expected) is int and expected > 0
                and type(stale) is int and stale > 0,
                "STAMP_INVALID",
            )
            engine.stamps[market, symbol, source] = SourceStamp(
                observed_at=at, expected_seconds=expected, stale_seconds=stale,
            )
        for product in ("spot", "linear"):
            support = state["instrument_support"][product]
            _need(
                support is None or (
                    isinstance(support, list)
                    and len(support) == len(set(support))
                    and set(support) <= set(_SEVEN)
                ),
                "INSTRUMENT_SUPPORT_INVALID",
            )
            engine.set_instrument_support(
                product, set(support) if support is not None else None,
            )
        _need(
            isinstance(state["tickers"], dict)
            and set(state["tickers"]) <= set(_SEVEN),
            "TICKER_SCOPE_INVALID",
        )
        engine.tickers = state["tickers"]
        for symbol in _SEVEN:
            engine.ticker_history[symbol] = _history(
                state["ticker_history"][symbol],
                width=2, limit=100_000,
            )
            engine.funding_history[symbol] = _history(
                state["funding_history"][symbol],
                width=2, limit=100_000,
            )
        for market, symbol, value in state["books"]:
            _need(market in ("spot", "linear") and symbol in _SEVEN
                  and isinstance(value, dict), "BOOK_STATE_INVALID")
            engine.books[market, symbol] = value
        for market, symbol, rows in state["book_history"]:
            _need(market in ("spot", "linear") and symbol in _SEVEN,
                  "BOOK_HISTORY_SCOPE_INVALID")
            engine.book_history[market, symbol] = _history(
                rows, width=4, limit=100_000,
            )
        for market, symbol, value in state["previous_books"]:
            _need(market in ("spot", "linear") and symbol in _SEVEN
                  and isinstance(value, dict), "PREVIOUS_BOOK_STATE_INVALID")
            engine.previous_books[market, symbol] = value
        _need(
            isinstance(state["market_history"], list)
            and len(state["market_history"]) <= 60,
            "MARKET_HISTORY_INVALID",
        )
        engine.history = deque(
            (float(at), dict(returns))
            for at, returns in state["market_history"]
        )
        engine.history = deque(engine.history, maxlen=60)
        _need(
            isinstance(state["liquidations"], list)
            and len(state["liquidations"]) <= _MAX_ROWS_PER_WINDOW,
            "LIQUIDATIONS_UNBOUNDED",
        )
        engine.liquidations.rows = deque(
            (float(at), str(symbol), str(side), float(usd))
            for at, symbol, side, usd in state["liquidations"]
        )
        _need(
            all(
                symbol in _SEVEN and side in ("Buy", "Sell")
                and math.isfinite(at) and math.isfinite(usd) and usd > 0
                for at, symbol, side, usd in engine.liquidations.rows
            ),
            "LIQUIDATION_VALUE_INVALID",
        )
        replay = R11MayakPublicationReplay(
            engine=engine, checkpoint=checkpoint,
            first_publication_at=_utc(payload["first_publication_at"]),
            last_original_event_at=(
                _finite(payload["last_original_event_at"])
                if payload["last_original_event_at"] is not None else None
            ),
            observations=payload["observations"],
        )
        _need(
            type(replay.observations) is int and replay.observations > 0
            and replay.first_publication_at is not None
            and replay.first_publication_at <= checkpoint.as_of_second,
            "REPLAY_POSITION_INVALID",
        )
        # Never revive transport CONNECTED across restart.
        _need(
            all(not row.get("connected")
                for row in replay.engine.transport.values()),
            "FALSE_TRANSPORT_RESTORE",
        )
        return replay
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise R11MayakStateUnverified(
            "R11_MAYAK_STATE_STRUCTURE_INVALID"
        ) from exc


def atomic_write_r11_mayak_state(path: Path, binary: bytes) -> None:
    """Atomic one-file save, operator-owned parent preflight is external."""
    _need(
        isinstance(path, Path)
        and path.is_absolute()
        and path.parent.is_dir()
        and path.parent != Path("/")
        and isinstance(binary, bytes)
        and binary.startswith(_MAGIC),
        "CHECKPOINT_PATH_OR_BYTES_INVALID",
    )
    fd, temp = tempfile.mkstemp(
        prefix=".r11-mayak-", suffix=".tmp", dir=str(path.parent),
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

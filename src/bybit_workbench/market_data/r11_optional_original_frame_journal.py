"""R11: original optional public WS frames, one canonical Native MarketSource.

SOURCE-ONLY capability, not installed or connected to the producer loop.
Original LINEAR publicTrade already has a separate exact native journal.
This append-only frame contract is ONLY for approved five-symbol SPOT trades,
SPOT/LINEAR BOOK snapshot/delta and LINEAR TICKER/LIQUIDATION events.

The original parsed provider JSON frame and EVERY codec-verified fact retain
their own source timestamp, sequence/execId and original order. The compiled
frame digest enables idempotence/mismatch detection; it does NOT prove WS
completeness, sequence continuity, seven-symbol MAYAK or live consumer parity.
Runtime INSERT must be invoked only by the single advisory-lock producer on
its existing cripta PostgreSQL connection after physical schema/release gates.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFact,
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

Family = Literal["SPOT_TRADE", "ORDERBOOK_50", "TICKER", "LIQUIDATION"]
_FIVE = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
))
_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_RELEASE = re.compile(r"^[0-9a-f]{40}$")
_MAX_FACTS = 4096
_ALLOWED: dict[tuple[str, str], Family] = {
    ("SPOT", "TRADE"): "SPOT_TRADE",
    ("SPOT", "BOOK"): "ORDERBOOK_50",
    ("LINEAR", "BOOK"): "ORDERBOOK_50",
    ("LINEAR", "TICKER"): "TICKER",
    ("LINEAR", "LIQUIDATION"): "LIQUIDATION",
}


class R11OriginalFrameUnavailable(RuntimeError):
    """Source identity/parsed JSON/source ownership/append state unverified."""


@dataclass(frozen=True, slots=True)
class R11OriginalPublicFrame:
    source_session_id: str
    transport_epoch_id: str
    producer_release_commit: str
    market_source_id: str
    instrument_id: str
    product_category: str
    external_symbol: str
    fact_family: Family
    source_topic: str
    source_frame_number: int
    frame_type: str
    provider_frame_at: datetime
    source_event_first_at: datetime
    source_event_last_at: datetime
    received_at: datetime
    provider_frame: dict[str, object]
    decoded_original_facts: tuple[dict[str, object], ...]
    frame_content_sha256: str
    # Neither the JSON image nor a single frame proves continuous capture.
    transport_continuity_verified: bool = False
    mayak_event_replay_ready: bool = False


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise R11OriginalFrameUnavailable("R11_ORIGINAL_FRAME_" + code)


def _utc(value: datetime, code: str) -> datetime:
    _need(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0),
        code,
    )
    return value.astimezone(UTC)


def _json_dict(value: Mapping[str, object], code: str) -> dict[str, object]:
    try:
        encoded = json.dumps(
            dict(value), ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, OverflowError) as exc:
        raise R11OriginalFrameUnavailable(
            "R11_ORIGINAL_FRAME_" + code
        ) from exc
    _need(isinstance(decoded, dict), code)
    return cast(dict[str, object], decoded)


def make_r11_original_optional_frame(
    *,
    source_session_id: str,
    transport_epoch_id: str,
    producer_release_commit: str,
    product: str,
    source_frame_number: int,
    message: Mapping[str, object],
    facts: tuple[PublicSourceFact, ...],
    received_at: datetime,
) -> R11OriginalPublicFrame:
    """Prove exact decoded originals match the parsed Bybit frame.

    Duplicate/foreign/out-of-order event identity is not reconstructed from
    one-second compact aggregates. Raw provider JSON here means parsed JSONB,
    not a byte-for-byte capture of the WS TCP payload.
    """
    received = _utc(received_at, "RECEIVED_CLOCK_INVALID")
    _need(
        isinstance(source_session_id, str)
        and _EPOCH.fullmatch(source_session_id) is not None
        and isinstance(transport_epoch_id, str)
        and _EPOCH.fullmatch(transport_epoch_id) is not None
        and isinstance(producer_release_commit, str)
        and _RELEASE.fullmatch(producer_release_commit) is not None
        and type(source_frame_number) is int
        and source_frame_number > 0
        and isinstance(facts, tuple)
        and 1 <= len(facts) <= _MAX_FACTS,
        "SOURCE_FRAME_IDENTITY_OR_BOUND_INVALID",
    )
    # Any one input frame belongs to exactly one venue/product/topic/symbol.
    first = facts[0]
    _need(
        isinstance(first, PublicSourceFact)
        and first.symbol in _FIVE
        and isinstance(product, str)
        and (product, first.kind) in _ALLOWED
        and first.product == product
        and first.market_source_id == BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "PRODUCT_OR_SYMBOL_SCOPE_INVALID",
    )
    family = _ALLOWED[product, first.kind]
    topic = first.source_topic
    expected_type = str(message.get("type") or "")
    _need(message.get("topic") == topic, "ORIGINAL_TOPIC_MISMATCH")
    # Re-running the canonical codec gives exact identity, event ordering,
    # provider event timestamps and snapshot-vs-delta semantics.
    try:
        decoded = decode_bybit_public_frame(
            message, product=cast(Any, product),
            allowed_symbols=frozenset((first.symbol,)),
            received_at=received,
        )
    except PublicSourceFrameInvalid as exc:
        raise R11OriginalFrameUnavailable(
            "R11_ORIGINAL_FRAME_CODEC_REJECTED"
        ) from exc
    _need(decoded == facts, "ORIGINAL_CODEC_FACT_DRIFT")
    _need(
        all(
            isinstance(fact, PublicSourceFact)
            and fact.source_topic == topic
            and fact.product == product
            and fact.symbol == first.symbol
            and fact.instrument_id == first.instrument_id
            and fact.frame_type == expected_type
            for fact in facts
        ),
        "MIXED_SOURCE_FRAME_INVALID",
    )
    if family == "SPOT_TRADE":
        _need(
            all(
                fact.exchange_sequence is not None
                and fact.exec_id is not None
                for fact in facts
            ),
            "SPOT_TRADE_NATIVE_IDENTITY_MISSING",
        )
    if family == "ORDERBOOK_50":
        _need(
            all(
                fact.exchange_sequence is not None
                and fact.frame_type in ("snapshot", "delta")
                for fact in facts
            ),
            "ORIGINAL_BOOK_SEQUENCE_OR_TYPE_INVALID",
        )
    provider_frame = _json_dict(message, "PROVIDER_JSON_INVALID")
    original_facts: list[dict[str, object]] = []
    for fact in facts:
        original_facts.append(_json_dict({
            "market_source_id": fact.market_source_id,
            "instrument_id": fact.instrument_id,
            "product_category": fact.product,
            "symbol": fact.symbol,
            "kind": fact.kind,
            "source_topic": fact.source_topic,
            "source_time": _utc(
                fact.source_time, "SOURCE_EVENT_CLOCK_INVALID",
            ).isoformat(),
            "frame_type": fact.frame_type,
            "exchange_sequence": fact.exchange_sequence,
            "exec_id": fact.exec_id,
            "payload": dict(fact.payload),
        }, "CODEC_JSON_INVALID"))
    native_ts = message.get("ts")
    if type(native_ts) is not int:
        raise R11OriginalFrameUnavailable(
            "R11_ORIGINAL_FRAME_PROVIDER_FRAME_TIME_INVALID"
        )
    _need(
        946684800000 <= native_ts < 4102444800000,
        "PROVIDER_FRAME_TIME_INVALID",
    )
    provider_at = datetime.fromtimestamp(native_ts / 1000, UTC)
    first_at = min(fact.source_time for fact in facts)
    last_at = max(fact.source_time for fact in facts)
    _need(
        provider_at <= received + timedelta(seconds=5)
        and last_at <= received + timedelta(seconds=5),
        "PROVIDER_FRAME_FUTURE_INVALID",
    )
    image = {
        "session": source_session_id, "epoch": transport_epoch_id,
        "producer": producer_release_commit,
        "source": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "instrument": first.instrument_id,
        "product": product, "symbol": first.symbol,
        "topic": topic, "frame": source_frame_number,
        "family": family, "frame_type": expected_type,
        "frame_at": provider_at.isoformat(),
        "received_at": received.isoformat(),
        "provider": provider_frame,
        "decoded_facts": original_facts,
    }
    digest = hashlib.sha256(json.dumps(
        image, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return R11OriginalPublicFrame(
        source_session_id=source_session_id,
        transport_epoch_id=transport_epoch_id,
        producer_release_commit=producer_release_commit,
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=first.instrument_id,
        product_category=product,
        external_symbol=first.symbol,
        fact_family=family,
        source_topic=topic,
        source_frame_number=source_frame_number,
        frame_type=expected_type,
        provider_frame_at=provider_at,
        source_event_first_at=first_at,
        source_event_last_at=last_at,
        received_at=received,
        provider_frame=provider_frame,
        decoded_original_facts=tuple(original_facts),
        frame_content_sha256=digest,
    )


def append_r11_original_optional_frame(
    connection: Any, frame: R11OriginalPublicFrame,
) -> int:
    """Authorized source-owner DML only; 1 inserted, 0 identical replay.

    Caller must already own the one native producer advisory lock and use its
    PostgreSQL actor/connection. No consumer/analyst/second writer may call.
    The schema is NOT installed by this source-only stage.
    """
    _need(
        isinstance(frame, R11OriginalPublicFrame)
        and frame.transport_continuity_verified is False
        and frame.mayak_event_replay_ready is False,
        "APPEND_CALLER_FRAME_INVALID",
    )
    # Reverify a dataclass that may have been constructed, copied or
    # modified outside the canonical codec before ANY PostgreSQL DML.
    image = {
        "session": frame.source_session_id,
        "epoch": frame.transport_epoch_id,
        "producer": frame.producer_release_commit,
        "source": frame.market_source_id,
        "instrument": frame.instrument_id,
        "product": frame.product_category,
        "symbol": frame.external_symbol,
        "topic": frame.source_topic,
        "frame": frame.source_frame_number,
        "family": frame.fact_family,
        "frame_type": frame.frame_type,
        "frame_at": frame.provider_frame_at.isoformat(),
        "received_at": frame.received_at.isoformat(),
        "provider": frame.provider_frame,
        "decoded_facts": list(frame.decoded_original_facts),
    }
    try:
        expected_digest = hashlib.sha256(json.dumps(
            image, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError) as exc:
        raise R11OriginalFrameUnavailable(
            "R11_ORIGINAL_FRAME_APPEND_JSON_INVALID"
        ) from exc
    _need(
        expected_digest == frame.frame_content_sha256
        and isinstance(frame.source_session_id, str)
        and _EPOCH.fullmatch(frame.source_session_id) is not None
        and isinstance(frame.transport_epoch_id, str)
        and _EPOCH.fullmatch(frame.transport_epoch_id) is not None
        and isinstance(frame.producer_release_commit, str)
        and _RELEASE.fullmatch(frame.producer_release_commit) is not None,
        "APPEND_DIGEST_OR_SOURCE_IDENTITY_INVALID",
    )
    from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

    with connection.transaction():
        source_epoch = connection.execute(
            """SELECT state,market_source_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (frame.source_session_id,),
        ).fetchone()
        _need(
            source_epoch == (
                "ACTIVE", frame.market_source_id,
                frame.producer_release_commit,
            ),
            "ACTIVE_CANONICAL_SOURCE_EPOCH_REQUIRED",
        )
        inserted = connection.execute(
            """INSERT INTO analytics.public_source_original_event_frames
               (source_session_id,transport_epoch_id,market_source_id,
                venue_id,instrument_id,product_category,external_symbol,
                fact_family,source_topic,source_frame_number,frame_type,
                provider_frame_at,source_event_first_at,source_event_last_at,
                received_at,provider_frame,decoded_original_facts,
                frame_content_sha256,producer_release_commit)
               VALUES
               (%s,%s,%s,'venue-bybit',%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (
                 source_session_id,transport_epoch_id,product_category,
                 source_frame_number
               ) DO NOTHING RETURNING frame_content_sha256""",
            (
                frame.source_session_id, frame.transport_epoch_id,
                frame.market_source_id, frame.instrument_id,
                frame.product_category, frame.external_symbol,
                frame.fact_family, frame.source_topic,
                frame.source_frame_number, frame.frame_type,
                frame.provider_frame_at, frame.source_event_first_at,
                frame.source_event_last_at, frame.received_at,
                Jsonb(frame.provider_frame),
                Jsonb(list(frame.decoded_original_facts)),
                frame.frame_content_sha256, frame.producer_release_commit,
            ),
        ).fetchone()
        if inserted is not None:
            _need(
                inserted == (frame.frame_content_sha256,),
                "INSERT_RETURNING_IDENTITY_DRIFT",
            )
            return 1
        old = connection.execute(
            """SELECT frame_content_sha256
                 FROM analytics.public_source_original_event_frames
                WHERE source_session_id=%s AND transport_epoch_id=%s
                  AND product_category=%s AND source_frame_number=%s""",
            (
                frame.source_session_id, frame.transport_epoch_id,
                frame.product_category, frame.source_frame_number,
            ),
        ).fetchone()
        _need(
            old == (frame.frame_content_sha256,),
            "FRAME_COLLISION_OR_MISSING_ORIGINAL",
        )
        return 0

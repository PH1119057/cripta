"""Credential-local source-window client for immediate REAL postfill Exit.

The private worker sends only a durable position ID and receives original
publicTrade evidence from the live Observer source owner. This transport has
NO Bybit credentials or Exchange mutation methods.
"""
from __future__ import annotations

import re
import secrets
import socket
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealPostfillSourceWindow,
)
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
    _frame,
    _receive,
    peer_same_runtime_uid,
)

_SOURCE_SOCKET = "\0cripta-p90-real-postfill-source-v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class P90RealPostfillSourceClientUnverified(RuntimeError):
    """No currently verified original-native MarketSource postfill window."""


def read_real_postfill_source_window(
    *,
    position: StrategyPosition,
    source_epoch_id: str,
    source_commit: str,
    timeout_seconds: float = 3.0,
) -> P90RealPostfillSourceWindow:
    if (
        not position.strategy_position_id
        or not position.trading_account_id
        or not position.symbol
        or not source_epoch_id.startswith("p90-real-epoch-")
        or _SHA.fullmatch(source_commit) is None
        or not 0 < timeout_seconds <= 3
    ):
        raise P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_CLIENT_CONFIG_INVALID"
        )
    nonce = secrets.token_hex(16)
    request = {
        "version": 1, "kind": "POSTFILL_SOURCE_WINDOW", "nonce": nonce,
        "position_id": position.strategy_position_id,
        "trading_account_id": position.trading_account_id,
        "symbol": position.symbol,
        "source_epoch_id": source_epoch_id,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(timeout_seconds)
            stream.connect(_SOURCE_SOCKET)
            if not peer_same_runtime_uid(stream):
                raise P90RealPostfillSourceClientUnverified(
                    "P90_REAL_POSTFILL_SOURCE_PEER_NOT_VERIFIED"
                )
            stream.sendall(_frame(request))
            result = _receive(stream)
    except (OSError, P90PrivateIpcUnverified):
        raise P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_TRANSPORT_UNAVAILABLE"
        ) from None
    if (
        result.get("version") != 1
        or result.get("status") != "VERIFIED"
        or result.get("nonce") != nonce
        or result.get("position_id") != position.strategy_position_id
        or result.get("trading_account_id") != position.trading_account_id
        or result.get("source_epoch_id") != source_epoch_id
        or not isinstance(result.get("window"), dict)
    ):
        raise P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_RESPONSE_UNVERIFIED"
        )
    data = result["window"]
    if set(data) != {
        "position_id", "symbol", "fill_at", "covered_through",
        "source_commit", "last_native_price", "best_favorable_price",
        "source_refs", "complete_native_window",
    } or data["position_id"] != position.strategy_position_id or (
        data["symbol"] != position.symbol
        or data["source_commit"] != source_commit
        or data["complete_native_window"] is not True
    ):
        raise P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_IDENTITY_MISMATCH"
        )
    try:
        raw_fill = datetime.fromisoformat(data["fill_at"])
        raw_covered = datetime.fromisoformat(data["covered_through"])
        if (
            raw_fill.tzinfo is None or raw_fill.utcoffset() is None
            or raw_covered.tzinfo is None or raw_covered.utcoffset() is None
        ):
            raise ValueError("postfill timestamps lack original UTC offsets")
        fill_at = raw_fill.astimezone(UTC)
        covered = raw_covered.astimezone(UTC)
        price = Decimal(str(data["last_native_price"]))
        best = Decimal(str(data["best_favorable_price"]))
        refs = data["source_refs"]
    except (ValueError, TypeError, InvalidOperation, KeyError):
        raise P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_FIELD_INVALID"
        ) from None
    if (
        fill_at != position.fill_at.astimezone(UTC)
        or covered < position.fill_at
        or not 0 <= (datetime.now(UTC) - covered).total_seconds() <= 5
        or not price.is_finite() or price <= 0
        or not best.is_finite() or best <= 0
        or not isinstance(refs, list) or not 1 <= len(refs) <= 256
        or any(
            not isinstance(ref, str)
            or not ref.startswith(f"bybit:publicTrade:{position.symbol}:")
            for ref in refs
        )
    ):
        raise P90RealPostfillSourceClientUnverified(
            "P90_REAL_POSTFILL_SOURCE_WINDOW_INCOMPLETE"
        )
    return P90RealPostfillSourceWindow(
        position_id=position.strategy_position_id,
        symbol=position.symbol,
        fill_at=fill_at,
        covered_through=covered,
        source_commit=source_commit,
        last_native_price=price,
        best_favorable_price=best,
        source_refs=tuple(refs),
        complete_native_window=True,
    )

"""Append-only evidence joining an old REAL source epoch to a new WS mirror.

A restart bridge is not an execution permission.  It exists so later native
ticks can prove that an already-open StrategyPosition's durable MFE cursor was
reconstructed through the new process' exact live publicTrade cursor without
replacing the original source epoch or replaying historical trading actions.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeCheckpoint

from .fingerprint import canonical_json
from .transport_continuity import TradeCursor

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_EPOCH = re.compile(r"p90-real-epoch-[0-9a-f]{40}\Z")
_RESTART = re.compile(r"p90-real-restart-[0-9a-f]{40}\Z")
_SYMBOLS = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))


class P90RealRestartBridgeUnverified(RuntimeError):
    """Restart bridge evidence is absent, incomplete or belongs elsewhere."""


@dataclass(frozen=True, slots=True)
class P90RealRestartBridge:
    restart_id: str
    source_epoch_id: str
    source_commit: str
    mirror_serial: int
    mirror_started_at: datetime
    source_ws_ready_at: datetime
    completed_at: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_TIME_INVALID"
        )
    return value.astimezone(UTC)


def _cursor_payload(cursor: TradeCursor) -> dict[str, object]:
    if (
        cursor.symbol not in _SYMBOLS
        or not cursor.exec_id
        or type(cursor.seq) is not int
        or cursor.seq < 0
    ):
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_CURSOR_INVALID"
        )
    return {
        "symbol": cursor.symbol,
        "exec_id": cursor.exec_id,
        "seq": cursor.seq,
        "traded_at": _utc(cursor.traded_at).isoformat(),
    }


def write_p90_real_restart_bridge(
    connection: Any,
    *,
    source_epoch_id: str,
    source_commit: str,
    mirror_serial: int,
    mirror_started_at: datetime,
    source_ws_ready_at: datetime,
    completed_at: datetime,
    symbol_cursors: Mapping[str, TradeCursor],
    position_mfe: Mapping[str, tuple[StrategyPosition, P90RealMfeCheckpoint]],
    account_generations: Mapping[str, str],
) -> P90RealRestartBridge:
    """Persist one exact bridge after all source/private recovery gates pass."""
    status = getattr(
        getattr(getattr(connection, "info", None), "transaction_status", None),
        "name", None,
    )
    if status != "INTRANS":
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_SOURCE_TRANSACTION_NOT_OPEN"
        )
    started = _utc(mirror_started_at)
    ready = _utc(source_ws_ready_at)
    completed = _utc(completed_at)
    if (
        _EPOCH.fullmatch(source_epoch_id) is None
        or _SHA.fullmatch(source_commit) is None
        or type(mirror_serial) is not int
        or mirror_serial <= 0
        or set(symbol_cursors) != _SYMBOLS
        or not position_mfe
        or not account_generations
        or any(not key or not value for key, value in account_generations.items())
        or not started <= ready <= completed
        or (completed - ready) > timedelta(seconds=30)
    ):
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_ARGUMENTS_INVALID"
        )
    epoch = connection.execute(
        """SELECT source_commit,status
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE epoch_id=%s FOR SHARE""",
        (source_epoch_id,),
    ).fetchone()
    if epoch != (source_commit, "ACTIVE"):
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_SOURCE_EPOCH_NOT_CURRENT"
        )

    symbols = {
        symbol: _cursor_payload(cursor)
        for symbol, cursor in sorted(symbol_cursors.items())
    }
    positions: dict[str, dict[str, object]] = {}
    for position_id, pair in sorted(position_mfe.items()):
        if (
            not isinstance(position_id, str)
            or not position_id
            or not isinstance(pair, tuple)
            or len(pair) != 2
        ):
            raise P90RealRestartBridgeUnverified(
                "P90_REAL_RESTART_BRIDGE_POSITION_INPUT_INVALID"
            )
        position, mfe = pair
        cursor = symbol_cursors.get(position.symbol)
        if (
            position.strategy_position_id != position_id
            or position.trading_account_id is None
            or mfe.position_id != position_id
            or mfe.account_id != position.trading_account_id
            or mfe.symbol != position.symbol
            or mfe.source_commit != source_commit
            or cursor is None
            or (
                mfe.cursor_exec_id, mfe.cursor_seq, _utc(mfe.cursor_at)
            ) != (
                cursor.exec_id, cursor.seq, _utc(cursor.traded_at)
            )
        ):
            raise P90RealRestartBridgeUnverified(
                "P90_REAL_RESTART_BRIDGE_POSITION_MFE_NOT_AT_LIVE_CURSOR"
            )
        positions[position_id] = {
            "position_id": position_id,
            "trading_account_id": position.trading_account_id,
            "symbol": position.symbol,
            "exec_id": mfe.cursor_exec_id,
            "seq": mfe.cursor_seq,
            "traded_at": _utc(mfe.cursor_at).isoformat(),
        }
    payload = {
        "epoch_id": source_epoch_id,
        "source_commit": source_commit,
        "mirror_serial": mirror_serial,
        "mirror_started_at": started.isoformat(),
        "source_ws_ready_at": ready.isoformat(),
        "completed_at": completed.isoformat(),
        "symbol_cursors": symbols,
        "position_cursors": positions,
        "account_generations": dict(sorted(account_generations.items())),
    }
    restart_id = "p90-real-restart-" + hashlib.sha1(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    connection.execute(
        """INSERT INTO strategy_entry.p90l90_real_restart_bridges(
               restart_id,epoch_id,source_commit,mirror_serial,mirror_started_at,
               source_ws_ready_at,completed_at,symbol_cursors,position_cursors,
               account_generations)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
           ON CONFLICT(restart_id) DO NOTHING""",
        (
            restart_id, source_epoch_id, source_commit, mirror_serial,
            started, ready, completed, canonical_json(symbols),
            canonical_json(positions),
            canonical_json(dict(sorted(account_generations.items()))),
        ),
    )
    saved = connection.execute(
        """SELECT epoch_id,source_commit,mirror_serial,mirror_started_at,
                  source_ws_ready_at,completed_at,symbol_cursors,
                  position_cursors,account_generations
             FROM strategy_entry.p90l90_real_restart_bridges
            WHERE restart_id=%s""",
        (restart_id,),
    ).fetchone()
    if saved != (
        source_epoch_id, source_commit, mirror_serial, started, ready, completed,
        symbols, positions, dict(sorted(account_generations.items())),
    ):
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_DURABLE_IDEMPOTENCY_MISMATCH"
        )
    return P90RealRestartBridge(
        restart_id=restart_id,
        source_epoch_id=source_epoch_id,
        source_commit=source_commit,
        mirror_serial=mirror_serial,
        mirror_started_at=started,
        source_ws_ready_at=ready,
        completed_at=completed,
    )


def verify_restart_bridge_for_position(
    connection: Any,
    *,
    restart_id: str,
    source_epoch_id: str,
    source_commit: str,
    mirror_serial: int,
    mirror_started_at: datetime,
    position: StrategyPosition,
    prior_mfe: P90RealMfeCheckpoint,
) -> bool:
    """Prove current process mirror starts from the recovered MFE cursor."""
    if (
        _RESTART.fullmatch(restart_id) is None
        or position.trading_account_id is None
        or prior_mfe.position_id != position.strategy_position_id
    ):
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_POSITION_ARGUMENT_INVALID"
        )
    row = connection.execute(
        """SELECT epoch_id,source_commit,mirror_serial,mirror_started_at,
                  position_cursors
             FROM strategy_entry.p90l90_real_restart_bridges
            WHERE restart_id=%s""",
        (restart_id,),
    ).fetchone()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 5
        or row[0] != source_epoch_id
        or row[1] != source_commit
        or row[2] != mirror_serial
        or row[3] != _utc(mirror_started_at)
        or not isinstance(row[4], Mapping)
    ):
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_SESSION_MISMATCH"
        )
    raw = row[4].get(position.strategy_position_id)
    expected = {
        "position_id": position.strategy_position_id,
        "trading_account_id": position.trading_account_id,
        "symbol": position.symbol,
        "exec_id": prior_mfe.cursor_exec_id,
        "seq": prior_mfe.cursor_seq,
        "traded_at": _utc(prior_mfe.cursor_at).isoformat(),
    }
    if raw != expected:
        raise P90RealRestartBridgeUnverified(
            "P90_REAL_RESTART_BRIDGE_MFE_CURSOR_MISMATCH"
        )
    return True

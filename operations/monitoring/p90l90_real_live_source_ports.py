"""Live original publicTrade / canonical-closed-bar evidence for P90 REAL Exit.

This module is a read-only source owner adapter. A valid source cursor alone
never grants account LIVE-arm, Exchange mutation rights, or permission to
reconstruct a missed native path from OHLC. Its methods must execute under the
same PostgreSQL source event transaction as the REAL consumer's checkpoint.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.p90l90_real_shared_candle_ledger import (
    P90RealSharedCandleLedger,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityNotProvable,
    TradeCursor,
)
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeCheckpoint


class P90RealLiveSourceUnverified(RuntimeError):
    """A live original native or canonical closed-candle epoch is unproven."""


class P90RealLiveSourcePorts:
    """Two source ports that cannot outlive their exact original mirror epoch.

    Construction has no side effect. It must only be called by the already
    subscribed live Observer after the real source epoch was verified in
    PostgreSQL. A fresh source epoch on restart is NOT an existing open
    position's original source epoch; restart with exposure needs separate
    exact replay/reconciliation and is intentionally not implemented here.
    """

    def __init__(
        self,
        connection: Any,
        *,
        mirror: PublicTradeMirrorBuffer,
        ledger: P90RealSharedCandleLedger,
        source_epoch_id: str,
        source_commit: str,
        mirror_serial: int,
        mirror_started_at: datetime,
    ) -> None:
        if (
            not callable(getattr(connection, "execute", None))
            or not isinstance(mirror, PublicTradeMirrorBuffer)
            or not isinstance(ledger, P90RealSharedCandleLedger)
            or not isinstance(source_commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
            or not isinstance(source_epoch_id, str)
            or re.fullmatch(r"p90-real-epoch-[0-9a-f]{40}", source_epoch_id) is None
            or type(mirror_serial) is not int or mirror_serial <= 0
            or mirror_started_at.tzinfo is None
            or mirror_started_at.utcoffset() != timedelta()
        ):
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_EPOCH_IDENTITY_INVALID"
            )
        self._db = connection
        self._mirror = mirror
        self._ledger = ledger
        self._epoch = source_epoch_id
        self._commit = source_commit
        self._serial = mirror_serial
        self._started = mirror_started_at.astimezone(UTC)
        self._snapshot()

    def _snapshot(self) -> dict[str, object]:
        snapshot = self._mirror.snapshot()
        if (
            snapshot.get("state") != "ACTIVE"
            or snapshot.get("gaps") != 0
            or type(snapshot.get("epoch")) is not int
            or snapshot.get("epoch") != self._serial
            or snapshot.get("epoch_started_at") != self._started.isoformat()
        ):
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_MIRROR_EPOCH_CHANGED"
            )
        return snapshot

    def _source_transaction(self) -> None:
        status = getattr(
            getattr(getattr(self._db, "info", None), "transaction_status", None),
            "name", None,
        )
        if status != "INTRANS":
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_TRANSACTION_NOT_OPEN"
            )
        row = self._db.execute(
            """SELECT epoch_id FROM strategy_entry.p90l90_real_source_epochs
                WHERE epoch_id=%s AND source_commit=%s AND status='ACTIVE'
                FOR SHARE""",
            (self._epoch, self._commit),
        ).fetchone()
        if row != (self._epoch,):
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_DURABLE_EPOCH_MISSING"
            )
        self._snapshot()

    def native_coverage(
        self, position: StrategyPosition, cursor: TradeCursor,
    ) -> tuple[bool, bool]:
        """Prove from actual mirror trades between signed fill/prior MFE and tick.

        No invented source cursor. If previous MFE has fallen out of mirror
        retention, there is no shortcut: fail-closed and exact recovery needed.
        """
        self._source_transaction()
        if (
            position.strategy_id not in ALLOWED_SYMBOLS
            or position.strategy_version != VERSION
            or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
            or position.identity_status != "PROVEN"
            or cursor.symbol != position.symbol
            or cursor.traded_at.tzinfo is None
            or cursor.traded_at.utcoffset() != timedelta()
            or position.fill_at.tzinfo is None
            or position.fill_at.utcoffset() != timedelta()
            or cursor.traded_at < position.fill_at
            or self._started > position.fill_at
            or not position.trading_account_id
        ):
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_NATIVE_POSITION_OR_FILL_INVALID"
            )
        raw = self._db.execute(
            """SELECT attributes
                 FROM strategy_exit.exit_observations
                WHERE strategy_position_id=%s AND exit_plan_fingerprint=%s
                  AND event_kind='P90L90_REAL_NATIVE_MFE_CHECKPOINT'
                ORDER BY (attributes->'checkpoint'->>'cursor_seq')::bigint DESC,
                         event_at DESC,observation_id DESC
                LIMIT 1""",
            (position.strategy_position_id, position.exit_plan_fingerprint),
        ).fetchone()
        prior = None
        if raw is not None:
            # The actual Observer runs psycopg tuple_row for its canonical
            # Entry checkpoints. Exit SQL uses a cursor-local dict_row
            # adapter, but this source-side query must accept a SINGLE
            # strictly shaped tuple, without changing the shared connection.
            if isinstance(raw, Mapping):
                attrs = raw.get("attributes")
            elif isinstance(raw, (tuple, list)) and len(raw) == 1:
                attrs = raw[0]
            else:
                raise P90RealLiveSourceUnverified(
                    "P90_REAL_LIVE_SOURCE_MFE_ROW_NOT_PROVEN"
                )
            if not isinstance(attrs, Mapping):
                raise P90RealLiveSourceUnverified(
                    "P90_REAL_LIVE_SOURCE_MFE_ATTRIBUTES_INVALID"
                )
            prior = P90RealMfeCheckpoint.restore(attrs.get("checkpoint"))
            if (
                prior.position_id != position.strategy_position_id
                or prior.account_id != position.trading_account_id
                or prior.source_commit != self._commit
                or prior.cursor_at < position.fill_at
                or prior.cursor_at > cursor.traded_at
            ):
                raise P90RealLiveSourceUnverified(
                    "P90_REAL_LIVE_SOURCE_MFE_PREVIOUS_CURSOR_DRIFT"
                )
        at = datetime.now(UTC)
        if not timedelta() <= at - cursor.traded_at <= timedelta(seconds=15):
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_NATIVE_EVENT_STALE"
            )
        try:
            trades = self._mirror.verified_fill_forward_events(
                symbol=position.symbol,
                fill_at=position.fill_at if prior is None else prior.cursor_at,
                at=at,
            )
        except ContinuityNotProvable:
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_NATIVE_INTERVAL_UNPROVEN"
            ) from None
        matches = [
            i for i, trade in enumerate(trades)
            if (trade.exec_id, trade.seq, trade.traded_at) ==
               (cursor.exec_id, cursor.seq, cursor.traded_at)
        ]
        if len(matches) != 1:
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_CURRENT_EXACT_TRADE_MISSING"
            )
        current_index = matches[0]
        # The native MFE ledger must receive each original market trade in
        # exact received order. Merely finding the requested execId somewhere
        # later in the mirror would skip adverse/favorable ticks and falsify
        # exit provenance.
        if prior is None and current_index != 0:
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_FIRST_POSTFILL_TRADE_SKIPPED"
            )
        if prior is not None:
            anchors = [
                i for i, trade in enumerate(trades)
                if (trade.exec_id, trade.seq, trade.traded_at) ==
                   (prior.cursor_exec_id, prior.cursor_seq, prior.cursor_at)
            ]
            if len(anchors) != 1 or anchors[0] > current_index:
                raise P90RealLiveSourceUnverified(
                    "P90_REAL_LIVE_SOURCE_PREVIOUS_ANCHOR_EVICTED_OR_REORDERED"
                )
            if current_index not in {anchors[0], anchors[0] + 1}:
                raise P90RealLiveSourceUnverified(
                    "P90_REAL_LIVE_SOURCE_INTERMEDIATE_NATIVE_TRADES_SKIPPED"
                )
            if (
                prior.cursor_seq > cursor.seq
                or (prior.cursor_seq == cursor.seq
                    and prior.cursor_at > cursor.traded_at)
            ):
                raise P90RealLiveSourceUnverified(
                    "P90_REAL_LIVE_SOURCE_NATIVE_CURSOR_REGRESSION"
                )
        self._snapshot()
        return True, True

    def closed_history(self, symbol: str, boundary: datetime):
        """Only the 36 already-emitted original 5m bars, no future OHLC."""
        self._source_transaction()
        if symbol not in self._mirror.required_symbols:
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_CLOSED_SYMBOL_UNSUBSCRIBED"
            )
        candles = self._ledger.history(
            symbol=symbol, timeframe="5", through=boundary, limit=36,
        )
        if len(candles) != 36 or candles[-1].closed_at != boundary:
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_CLOSED_HISTORY_NOT_CAUSAL"
            )
        self._snapshot()
        return candles

    def closed_continuity(self, symbol: str, boundary: datetime) -> bool:
        """A current emitted 5m checkpoint, not merely an archived candle."""
        self._source_transaction()
        if (
            symbol not in self._mirror.required_symbols
            or boundary.tzinfo is None
            or boundary.utcoffset() != timedelta()
            or self._ledger.restart_watermarks(symbol)["5"] != boundary
        ):
            raise P90RealLiveSourceUnverified(
                "P90_REAL_LIVE_SOURCE_CLOSED_BOUNDARY_NOT_EMITTED"
            )
        self._snapshot()
        return True

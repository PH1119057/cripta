"""Complete bounded source recovery for an already-open P90 REAL epoch.

This is the only restart path allowed to bridge a process outage with exposure.
It reuses the original durable source epoch, proves complete signed MAIN/SUB
inventory/protection, reconstructs exact native publicTrade + 5m/15m causal
state, advances MFE observation only, then writes an append-only restart bridge.

Historical replay never calls normal Entry candidate or Universal Exit action
ports.  Failure at any point aborts the source transaction and leaves Exchange
TP/SL/TRAIL035 as the protection owner while runtime remains fail-closed.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.candle_warmup import (
    cache_approved_bybit_history,
)
from bybit_workbench.universal_exit.p90l90_real_mfe import (
    P90RealMfeCheckpoint,
    P90RealMfeUnverified,
)
from bybit_workbench.universal_exit.p90l90_real_runtime_ports import (
    P90RealExitRuntimePorts,
)

from .contracts import FrozenPolicy, MarketFactEnvelope
from .fingerprint import fingerprint
from .p90l90_causal_fact_codec import encode_p90l90_fact
from .p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)
from .p90l90_observer_real_consumer import P90L90ObserverRealConsumer
from .p90l90_real_bounded_native_restart import (
    attest_bounded_native_restart,
    resolve_durable_restart_anchor,
)
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS
from .p90l90_real_open_restart import (
    P90RealOpenRestartInventory,
    P90RealRestartPosition,
    prove_p90_real_open_restart_inventory,
)
from .p90l90_real_restart_bridge import (
    P90RealRestartBridge,
    write_p90_real_restart_bridge,
)
from .trade_mirror import PublicTradeMirrorBuffer
from .transport_continuity import (
    ReplayTrade,
    TradeCursor,
    expected_boundaries,
)

_SYMBOLS = frozenset().union(*ALLOWED_SYMBOLS.values())
FetchRecentTrades = Callable[
    [str], tuple[Sequence[Mapping[str, object]], datetime]
]
FetchClosedCandles = Callable[[str, str, datetime], Sequence[Candle]]


class P90RealOpenSourceRecoveryUnverified(RuntimeError):
    """Exact old source -> new live source recovery could not be proven."""


@dataclass(frozen=True, slots=True)
class P90RealOpenSourceRecovery:
    """Exact handoff state needed before the primary WS socket may resume."""

    bridge: P90RealRestartBridge
    inventory: P90RealOpenRestartInventory
    trade_cursors: Mapping[str, TradeCursor]
    current_sequence_exec_ids: Mapping[str, frozenset[str]]
    closed_watermarks: Mapping[str, tuple[datetime, datetime]]
    replayed_fact_ids: frozenset[str]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise P90RealOpenSourceRecoveryUnverified(
            "P90_REAL_RESTART_SOURCE_" + reason
        )


def _trade_fact(row: ReplayTrade, received_at: datetime) -> MarketFactEnvelope:
    return MarketFactEnvelope(
        fact_id="trade-" + row.exec_id,
        event_kind="PUBLIC_TRADE",
        symbol=row.symbol,
        observed_at=row.traded_at,
        event_at=row.traded_at,
        received_at=received_at,
        source_refs=(f"bybit:publicTrade:{row.symbol}:{row.exec_id}",),
        attributes=FrozenPolicy.from_mapping({
            "price": row.price,
            "size": row.size,
            "taker_side": row.side,
        }),
    )


def _candle_fact(candle: Candle, received_at: datetime) -> MarketFactEnvelope:
    boundary = candle.closed_at
    topic = f"kline.{candle.timeframe}.{candle.symbol}"
    identity = fingerprint({
        "topic": topic,
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "boundary": boundary,
        "closed": True,
    })[:32]
    return MarketFactEnvelope(
        fact_id=f"candle_closed-{identity}",
        event_kind="CANDLE_CLOSED",
        symbol=candle.symbol,
        observed_at=boundary,
        event_at=boundary,
        received_at=received_at,
        source_refs=(
            f"bybit:{topic}:{candle.symbol}:{candle.timeframe}:"
            f"{boundary.isoformat()}",
        ),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": candle.timeframe,
            "opened_at": candle.opened_at.isoformat(),
            "closed_at": candle.closed_at.isoformat(),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
        }),
    )


def _load_joins(
    connection: Any, *, source_epoch_id: str, source_commit: str,
) -> dict[str, P90L90CausalTransportJoin]:
    rows = connection.execute(
        """SELECT symbol,checkpoint_json,source_commit
             FROM strategy_entry.p90l90_real_causal_checkpoints
            WHERE epoch_id=%s ORDER BY symbol FOR UPDATE""",
        (source_epoch_id,),
    ).fetchall()
    _require(
        len(rows) == len(_SYMBOLS)
        and {row[0] for row in rows} == _SYMBOLS
        and all(row[2] == source_commit for row in rows),
        "EXACT_DURABLE_CHECKPOINTS_MISSING",
    )
    joins: dict[str, P90L90CausalTransportJoin] = {}
    for symbol, raw, _commit in rows:
        try:
            join = P90L90CausalTransportJoin.restore(raw)
        except Exception:
            raise P90RealOpenSourceRecoveryUnverified(
                "P90_REAL_RESTART_SOURCE_DURABLE_CHECKPOINT_CORRUPT"
            ) from None
        _require(
            join.symbol == symbol
            and not join.pending
            and join.last_emitted_trade_at is not None
            and type(join.last_emitted_trade_sequence) is int
            and bool(join.last_emitted_exec_ids),
            "DURABLE_NATIVE_CURSOR_INCOMPLETE",
        )
        joins[symbol] = join
    return joins


def _closed_replay(
    connection: Any,
    *,
    source_commit: str,
    symbol: str,
    join: P90L90CausalTransportJoin,
    live_anchor: TradeCursor,
    received_at: datetime,
    fetch_closed_candles: FetchClosedCandles,
) -> list[P90TransportEvent]:
    events: list[P90TransportEvent] = []
    for timeframe, old in (
        ("5", join.closed_5m_at),
        ("15", join.closed_15m_at),
    ):
        target = live_anchor.traded_at.astimezone(UTC).replace(
            minute=(
                live_anchor.traded_at.astimezone(UTC).minute
                // int(timeframe)
            ) * int(timeframe),
            second=0,
            microsecond=0,
        )
        boundaries = expected_boundaries(
            old, target, timeframe_minutes=int(timeframe),
        )
        if not boundaries:
            continue
        rows = tuple(fetch_closed_candles(symbol, timeframe, received_at))
        by_closed = {
            candle.closed_at: candle
            for candle in rows
            if candle.is_closed
            and candle.symbol == symbol
            and candle.timeframe == timeframe
        }
        _require(
            all(boundary in by_closed for boundary in boundaries),
            f"CLOSED_{timeframe}M_GAP_OUTSIDE_BOUNDED_REST_WINDOW",
        )
        for boundary in boundaries:
            candle = by_closed[boundary]
            cache_approved_bybit_history(
                # The caller's source transaction owns this exact write.
                # REST is the same bounded recovery source used by ordinary
                # Observer reconnect, not a substitute native trade stream.
                connection,
                symbol=symbol,
                history={timeframe: (candle,)},
                observed_at=received_at,
                producer_release_commit=source_commit,
                source_transport="BYBIT_REST_KLINE_REAL_RESTART",
            )
            events.append(encode_p90l90_fact(_candle_fact(candle, received_at)))
    return events


def recover_p90_real_open_source(
    connection: Any,
    *,
    bundles: tuple[object, ...],
    source_commit: str,
    source_epoch_id: str,
    consumer: P90L90ObserverRealConsumer,
    exit_ports: P90RealExitRuntimePorts,
    mirror: PublicTradeMirrorBuffer,
    mirror_serial: int,
    mirror_started_at: datetime,
    source_ws_ready_at: datetime,
    fetch_recent_trades: FetchRecentTrades,
    fetch_closed_candles: FetchClosedCandles,
) -> P90RealOpenSourceRecovery:
    """Recover old epoch through current live mirror with zero replay actions."""
    if (
        not isinstance(consumer, P90L90ObserverRealConsumer)
        or not isinstance(exit_ports, P90RealExitRuntimePorts)
        or not isinstance(mirror, PublicTradeMirrorBuffer)
        or not callable(fetch_recent_trades)
        or not callable(fetch_closed_candles)
        or source_ws_ready_at.tzinfo is None
        or mirror_started_at.tzinfo is None
    ):
        raise P90RealOpenSourceRecoveryUnverified(
            "P90_REAL_RESTART_SOURCE_DEPENDENCIES_INVALID"
        )
    snapshot = mirror.snapshot()
    _require(
        snapshot.get("state") == "ACTIVE"
        and snapshot.get("gaps") == 0
        and snapshot.get("epoch") == mirror_serial
        and snapshot.get("epoch_started_at")
        == mirror_started_at.astimezone(UTC).isoformat(),
        "LIVE_MIRROR_SESSION_UNVERIFIED",
    )

    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        started_at = datetime.now(UTC)
        inventory = prove_p90_real_open_restart_inventory(
            connection,
            bundles=bundles,
            source_commit=source_commit,
            source_epoch_id=source_epoch_id,
            checked_at=started_at,
        )
        consumer.recover_all()
        joins = _load_joins(
            connection,
            source_epoch_id=source_epoch_id,
            source_commit=source_commit,
        )
        live_cursors, _live_seq_ids = mirror.current_cursors()
        _require(
            set(live_cursors) == _SYMBOLS,
            "LIVE_MIRROR_FIVE_SYMBOL_CURSORS_MISSING",
        )

        recoveries = {}
        replayed_fact_ids: set[str] = set()
        received_at = datetime.now(UTC)
        for symbol in sorted(_SYMBOLS):
            rows, server_at = fetch_recent_trades(symbol)
            join = joins[symbol]
            durable = resolve_durable_restart_anchor(
                symbol=symbol,
                last_sequence=join.last_emitted_trade_sequence,
                last_event_at=join.last_emitted_trade_at,
                last_exec_ids=sorted(join.last_emitted_exec_ids),
                latest_rest_rows=rows,
            )
            recoveries[symbol] = attest_bounded_native_restart(
                source_commit=source_commit,
                source_epoch_id=source_epoch_id,
                durable_anchor=durable,
                live_anchor=live_cursors[symbol],
                mirror=mirror,
                latest_rest_rows=rows,
                rest_server_at=server_at,
                checked_at=datetime.now(UTC),
                durable_exec_ids=sorted(join.last_emitted_exec_ids),
            )

        position_by_symbol = inventory.by_symbol
        mfe_state: dict[str, P90RealMfeCheckpoint | None] = {}
        for symbol, item in position_by_symbol.items():
            try:
                latest, _fact, _refs = exit_ports.mfe_ledger.load_latest(
                    position=item.position,
                    source_commit=source_commit,
                )
            except P90RealMfeUnverified as exc:
                if str(exc) != "P90_REAL_MFE_DURABLE_NATIVE_STATE_MISSING":
                    raise
                latest = None
            join = joins[symbol]
            last_emitted_at = join.last_emitted_trade_at
            if last_emitted_at is None:
                raise P90RealOpenSourceRecoveryUnverified(
                    "P90_REAL_RESTART_SOURCE_DURABLE_NATIVE_CURSOR_INCOMPLETE"
                )
            if latest is not None:
                _require(
                    latest.cursor_seq == join.last_emitted_trade_sequence
                    and latest.cursor_at == last_emitted_at
                    and latest.cursor_exec_id in join.last_emitted_exec_ids,
                    "DURABLE_MFE_NOT_AT_SOURCE_CURSOR",
                )
            elif last_emitted_at > item.position.fill_at:
                raise P90RealOpenSourceRecoveryUnverified(
                    "P90_REAL_RESTART_SOURCE_MFE_MISSING_AFTER_POSTFILL_TICK"
                )
            mfe_state[symbol] = latest

        for symbol in sorted(_SYMBOLS):
            recovery = recoveries[symbol]
            replay: list[tuple[datetime, int, P90TransportEvent]] = []
            for event in _closed_replay(
                connection,
                source_commit=source_commit,
                symbol=symbol,
                join=joins[symbol],
                live_anchor=recovery.live_anchor,
                received_at=received_at,
                fetch_closed_candles=fetch_closed_candles,
            ):
                # Completed 5m precedes 15m at one exact boundary.
                priority = 0 if event.timeframe == "5" else 1
                replay.append((event.event_at, priority, event))
            for row in recovery.trades:
                replay.append((
                    row.traded_at, 2,
                    encode_p90l90_fact(_trade_fact(row, received_at), cursor=TradeCursor(
                        row.symbol, row.exec_id, row.seq, row.traded_at,
                    )),
                ))
            replay.sort(key=lambda item: (
                item[0], item[1],
                -int(item[2].timeframe or "0"),
                item[2].exec_id or "",
            ))

            open_item = position_by_symbol.get(symbol)

            def observe_native(
                fact: MarketFactEnvelope,
                cursor: TradeCursor,
                *,
                current: P90RealRestartPosition | None = open_item,
                old_anchor: TradeCursor = recovery.durable_anchor,
                current_symbol: str = symbol,
            ) -> None:
                if current is None or cursor.traded_at < current.position.fill_at:
                    return
                previous = mfe_state[current_symbol]
                first = previous is None
                if first:
                    _require(
                        old_anchor.traded_at <= current.position.fill_at,
                        "FIRST_MFE_FILL_FORWARD_INTERVAL_NOT_COVERED",
                    )
                checkpoint = exit_ports.mfe_ledger.on_trade(
                    position=current.position,
                    source_commit=source_commit,
                    fact_id=fact.fact_id,
                    cursor=cursor,
                    price=Decimal(str(fact.attributes.to_dict()["price"])),
                    received_at=fact.received_at,
                    source_refs=fact.source_refs,
                    first_fill_forward_coverage_proven=first,
                    native_continuity_proven=True,
                )
                mfe_state[current_symbol] = checkpoint

            for _at, _priority, event in replay:
                consumer.accept_restart_recovery(
                    event,
                    on_recovered_native=observe_native,
                )
                replayed_fact_ids.add(event.event_id)
        # Durable source checkpoints must now terminate at the exact live
        # cursors chosen before replay; no historical event may remain pending.
        current_joins = _load_joins(
            connection,
            source_epoch_id=source_epoch_id,
            source_commit=source_commit,
        )
        for symbol, live in live_cursors.items():
            join = current_joins[symbol]
            _require(
                not join.pending
                and join.last_emitted_trade_sequence == live.seq
                and join.last_emitted_trade_at == live.traded_at
                and live.exec_id in join.last_emitted_exec_ids,
                "REPLAY_DID_NOT_REACH_LIVE_CURSOR",
            )
        position_mfe = {}
        for symbol, item in position_by_symbol.items():
            try:
                latest, _fact, _refs = exit_ports.mfe_ledger.load_latest(
                    position=item.position,
                    source_commit=source_commit,
                )
            except P90RealMfeUnverified:
                raise P90RealOpenSourceRecoveryUnverified(
                    "P90_REAL_RESTART_SOURCE_OPEN_POSITION_MFE_NOT_RECONSTRUCTED"
                ) from None
            live = live_cursors[symbol]
            _require(
                (
                    latest.cursor_exec_id, latest.cursor_seq, latest.cursor_at
                ) == (live.exec_id, live.seq, live.traded_at),
                "OPEN_POSITION_MFE_NOT_AT_LIVE_CURSOR",
            )
            position_mfe[item.position.strategy_position_id] = (
                item.position, latest,
            )

        # Protection is signed again after the replay window.  Replay cannot
        # silently continue if TP/SL/trailing/account inventory changed while
        # source state was being rebuilt.
        final_inventory = prove_p90_real_open_restart_inventory(
            connection,
            bundles=bundles,
            source_commit=source_commit,
            source_epoch_id=source_epoch_id,
            checked_at=datetime.now(UTC),
        )
        _require(
            {
                (item.position.strategy_position_id,
                 item.position.trading_account_id,
                 item.position.symbol)
                for item in final_inventory.positions
            }
            == {
                (item.position.strategy_position_id,
                 item.position.trading_account_id,
                 item.position.symbol)
                for item in inventory.positions
            },
            "SIGNED_POSITION_SET_CHANGED_DURING_RECOVERY",
        )
        final_snapshot = mirror.snapshot()
        _require(
            final_snapshot.get("state") == "ACTIVE"
            and final_snapshot.get("gaps") == 0
            and final_snapshot.get("epoch") == mirror_serial
            and final_snapshot.get("epoch_started_at")
            == mirror_started_at.astimezone(UTC).isoformat(),
            "LIVE_MIRROR_CHANGED_DURING_RECOVERY",
        )
        bridge = write_p90_real_restart_bridge(
            connection,
            source_epoch_id=source_epoch_id,
            source_commit=source_commit,
            mirror_serial=mirror_serial,
            mirror_started_at=mirror_started_at,
            source_ws_ready_at=source_ws_ready_at,
            completed_at=datetime.now(UTC),
            symbol_cursors=live_cursors,
            position_mfe=position_mfe,
            account_generations={
                final_inventory.main.trading_account_id:
                    final_inventory.main.private_generation_id,
                final_inventory.sub.trading_account_id:
                    final_inventory.sub.private_generation_id,
            },
        )
        # The primary WS socket was subscribed before recovery and can still
        # contain frames already reconstructed above.  Export the exact
        # committed transport boundary so the caller can seed its ordinary
        # deduper/cursors before reading that socket.  This is not optional:
        # otherwise a buffered pre-bridge publicTrade could regress the join.
        for join in current_joins.values():
            replayed_fact_ids.update(
                "trade-" + exec_id
                for exec_id in join.last_emitted_exec_ids
            )
        current_sequence_exec_ids = {
            symbol: frozenset(join.last_emitted_exec_ids)
            for symbol, join in current_joins.items()
        }
        closed_watermarks = {
            symbol: (join.closed_5m_at, join.closed_15m_at)
            for symbol, join in current_joins.items()
        }
    return P90RealOpenSourceRecovery(
        bridge=bridge,
        inventory=final_inventory,
        trade_cursors=dict(live_cursors),
        current_sequence_exec_ids=current_sequence_exec_ids,
        closed_watermarks=closed_watermarks,
        replayed_fact_ids=frozenset(replayed_fact_ids),
    )

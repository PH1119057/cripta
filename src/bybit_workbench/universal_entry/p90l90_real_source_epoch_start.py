"""One exact fresh WS epoch for P90 REAL, preserving all old PAPER evidence.

This function must run only after independent Bybit subscription/mirror
attestation, authenticated MAIN/SUB flat-account readback and a complete
canonical MarketSource REST warmup. It creates NO trade, Entry activation,
execution permission, arm or Exchange command. A fresh epoch intentionally
has no native cursor: the first LIVE trade primes Entry, never retro-trades.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey, SharedClosedCandleStore,
)

from .p90l90_atomic_real_epoch_checkpoint import (
    P90L90AtomicRealEpochCheckpointStore,
)
from .p90l90_causal import P90L90CausalEntry, causal_entry_state

_SYMBOLS = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))
_ACCOUNTS = frozenset((
    "ta-2122c81ecff14f3b85b102db605212f7",
    "ta-853ac5fcd58185697396d33cd57459fa",
))
_SOURCE_ID = "ms-bybit-mainnet-public-v1"


class P90RealEpochStartUnverified(RuntimeError):
    """No exact no-exposure fresh native source/entry epoch proof."""


@dataclass(frozen=True, slots=True)
class P90RealNewWsSourceProof:
    """Fresh two-channel transport proof BEFORE new durable checkpoints.

    Unlike P90NativeBarRuntimeProof, bootstrap must not falsely claim
    the just-created five checkpoints have already been restored.
    """

    source_commit: str
    subscribed_symbols: frozenset[str]
    subscribed_intervals: frozenset[str]
    mirror_active: bool
    subscribed_local_at: datetime
    subscribed_server_at: datetime
    epoch_started_at: datetime

    def verified_for(self, symbols: frozenset[str]) -> bool:
        times = (
            self.subscribed_local_at, self.subscribed_server_at,
            self.epoch_started_at,
        )
        return bool(
            len(self.source_commit) == 40
            and all(c in "0123456789abcdef" for c in self.source_commit)
            and symbols.issubset(self.subscribed_symbols)
            and {"5", "15"}.issubset(self.subscribed_intervals)
            and self.mirror_active is True
            and all(t.tzinfo is not None
                    and t.utcoffset() == timedelta() for t in times)
            and self.epoch_started_at <= self.subscribed_local_at
            <= datetime.now(UTC)
            and abs((
                self.subscribed_local_at - self.subscribed_server_at
            ).total_seconds()) <= 15
        )


@dataclass(frozen=True, slots=True)
class P90RealSignedFlatAccount:
    """Authenticated *provider-produced* account snapshot, not a DB roster."""

    trading_account_id: str
    signed_uid: str
    private_generation_id: str
    source_ref: str
    observed_at: datetime
    open_positions: int
    open_orders: int
    pending_commands: int


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise P90RealEpochStartUnverified("P90_REAL_EPOCH_" + message)


def start_p90_real_source_epoch(
    connection: Any, *,
    source_commit: str,
    source: P90RealNewWsSourceProof,
    signed_accounts: tuple[P90RealSignedFlatAccount, P90RealSignedFlatAccount],
    mirror_ready_at: datetime,
    new_epoch_requested_at: datetime,
    retire_previous: bool = False,
) -> str:
    """Create exactly five independent REAL checkpoints in one serializable xact.

    The caller must supply genuine signed private inventory from BOTH
    separate private account workers. A single REST ticker/publicTrade or
    a boolean cannot stand in for their authenticated flat evidence.
    Current pending/ambiguous Exchange commands fail closed.
    """
    info = getattr(connection, "info", None)
    _require(
        getattr(getattr(info, "transaction_status", None), "name", None)
        == "INTRANS", "SOURCE_TRANSACTION_NOT_OPEN",
    )
    _require(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("serializable",), "SERIALIZABLE_REQUIRED",
    )
    _require(
        connection.execute("SHOW transaction_read_only").fetchone() == ("off",),
        "WRITABLE_SOURCE_TRANSACTION_REQUIRED",
    )
    _require(source.source_commit == source_commit, "SOURCE_SHA_DRIFT")
    _require(source.verified_for(_SYMBOLS), "LIVE_WS_MIRROR_NOT_ATTESTED")
    _require(
        mirror_ready_at.tzinfo is not None
        and mirror_ready_at.utcoffset() == timedelta()
        and new_epoch_requested_at.tzinfo is not None
        and new_epoch_requested_at.utcoffset() == timedelta()
        and source.subscribed_local_at >= mirror_ready_at
        and mirror_ready_at >= source.epoch_started_at
        and timedelta() <= new_epoch_requested_at - source.subscribed_local_at
        <= timedelta(seconds=10),
        "SOURCE_TIMESTAMPS_INVALID",
    )
    _require(
        len(signed_accounts) == 2
        and {item.trading_account_id for item in signed_accounts} == _ACCOUNTS,
        "EXACT_TWO_ACCOUNTS_REQUIRED",
    )
    uids = set()
    for account in signed_accounts:
        _require(
            account.signed_uid.isascii() and account.signed_uid.isdigit()
            and int(account.signed_uid) > 0
            and account.signed_uid not in uids
            and bool(account.private_generation_id)
            and account.source_ref.startswith("bybit:signed-")
            and account.observed_at.tzinfo is not None
            and account.observed_at.utcoffset() == timedelta()
            and source.subscribed_local_at <= account.observed_at
            <= new_epoch_requested_at
            and (account.open_positions, account.open_orders,
                 account.pending_commands) == (0, 0, 0),
            "SIGNED_MAIN_SUB_NOT_FRESH_FLAT",
        )
        uids.add(account.signed_uid)
    # Durable local checks are independent of private readback. Hot Exchange
    # inventory, pending command and local v3 exposure must ALL be empty.
    probes = (
        ("SELECT count(*) FROM runtime.position_ownership "
         "WHERE strategy_version='2.0-p90l90-real-main-sub-20261028' "
         "AND state IN ('OPEN','RECONCILIATION_REQUIRED')", ()),
        ("SELECT count(*) FROM runtime.hot_positions "
         "WHERE trading_account_id=ANY(%s)", (sorted(_ACCOUNTS),)),
        ("SELECT count(*) FROM runtime.trade_commands "
         "WHERE trading_account_id=ANY(%s) "
         "AND state NOT IN ('completed','failed','cancelled','rejected')",
         (sorted(_ACCOUNTS),)),
        ("SELECT count(*) FROM strategy_entry.execution_requests e "
         "WHERE e.strategy_version='2.0-p90l90-real-main-sub-20261028' "
         "AND NOT EXISTS (SELECT 1 FROM strategy_entry.execution_dispatches d "
         "WHERE d.execution_request_id=e.execution_request_id)", ()),
        ("SELECT count(*) FROM strategy_exit.execution_requests e "
         "WHERE e.strategy_version='2.0-p90l90-real-main-sub-20261028' "
         "AND NOT EXISTS (SELECT 1 FROM strategy_exit.execution_dispatches d "
         "WHERE d.execution_request_id=e.execution_request_id)", ()),
    )
    for sql, args in probes:
        _require(connection.execute(sql, args).fetchone() == (0,),
                 "LOCAL_POSITION_OR_PENDING_COMMAND")
    # A previous REAL epoch is an audit artifact; retire explicitly, never
    # overwrite its 5 checkpoint rows or old PAPER restart state.
    previous = connection.execute(
        "SELECT epoch_id FROM strategy_entry.p90l90_real_source_epochs "
        "WHERE status='ACTIVE' FOR UPDATE"
    ).fetchall()
    _require(len(previous) <= 1, "MULTIPLE_ACTIVE_EPOCHS")
    if previous:
        _require(retire_previous is True, "ACTIVE_EPOCH_REUSE_UNVERIFIED")
    keys = connection.execute(
        """SELECT s.market_source_id,s.venue_id,i.instrument_id,
                  i.external_symbol,i.product_category
             FROM identity.market_sources s
             JOIN identity.instruments i ON i.venue_id=s.venue_id
            WHERE s.market_source_id=%s
              AND s.source_kind='DIRECT_PUBLIC_COMPOSITE'
              AND s.status='ACTIVE'
              AND i.product_category='LINEAR'
              AND i.external_symbol=ANY(%s)""",
        (_SOURCE_ID, sorted(_SYMBOLS)),
    ).fetchall()
    _require(
        len(keys) == 5 and {k[3] for k in keys} == _SYMBOLS,
        "FIVE_CANONICAL_MARKET_INSTRUMENTS_MISSING",
    )
    ready: dict[str, tuple[datetime, datetime, object]] = {}
    for source_id, venue, instrument, symbol, product in keys:
        key5 = MarketCandleKey(source_id, venue, instrument, symbol, product, "5")
        key15 = MarketCandleKey(source_id, venue, instrument, symbol, product, "15")
        # As-of last fully closed boundaries before the subscription;
        # never infer native cursor or Entry from these historical candles.
        end5 = source.subscribed_local_at.replace(
            minute=(source.subscribed_local_at.minute // 5) * 5,
            second=0, microsecond=0,
        )
        end15 = source.subscribed_local_at.replace(
            minute=(source.subscribed_local_at.minute // 15) * 15,
            second=0, microsecond=0,
        )
        fives = SharedClosedCandleStore(connection, key5).history(
            through=end5, required=2600,
        )
        fifteens = SharedClosedCandleStore(connection, key15).history(
            through=end15, required=999,
        )
        _require(
            fives[-1].closed_at == end5
            and fifteens[-1].closed_at == end15
            and timedelta() <= source.subscribed_local_at - end5
            < timedelta(minutes=5),
            "CANONICAL_CLOSED_WINDOW_INCOMPLETE",
        )
        geometry = causal_entry_state(
            fives, fifteens, effective_at=end5,
        )
        ready[symbol] = (end5, end15, geometry)
    epoch_digest = hashlib.sha1(
        (
            source_commit
            + "|" + source.subscribed_local_at.isoformat()
            + "|" + mirror_ready_at.isoformat()
            + "|" + ",".join(sorted(_SYMBOLS))
        ).encode("utf-8")
    ).hexdigest()
    epoch_id = "p90-real-epoch-" + epoch_digest
    if previous:
        _require(previous[0][0] != epoch_id, "SAME_EPOCH_CANNOT_RESET")
        updated = connection.execute(
            """UPDATE strategy_entry.p90l90_real_source_epochs
                  SET status='RETIRED',retired_at=%s,
                      retire_reason='NEW_VERIFIED_SOURCE_NO_EXPOSURE'
                WHERE epoch_id=%s AND status='ACTIVE'""",
            (new_epoch_requested_at, previous[0][0]),
        )
        _require(updated.rowcount == 1, "EPOCH_RETIRE_RACE")
    connection.execute(
        """INSERT INTO strategy_entry.p90l90_real_source_epochs(
               epoch_id,source_commit,market_source_id,
               opened_at,source_ws_ready_at,native_mirror_ready_at)
           VALUES(%s,%s,%s,%s,%s,%s)""",
        (
            epoch_id, source_commit, _SOURCE_ID, source.epoch_started_at,
            source.subscribed_local_at, mirror_ready_at,
        ),
    )
    atomic = P90L90AtomicRealEpochCheckpointStore(
        connection, source_commit=source_commit,
        writer_release_commit=source_commit, epoch_id=epoch_id,
    )
    for symbol in sorted(_SYMBOLS):
        five, fifteen, geometry = ready[symbol]
        atomic.seed(
            symbol=symbol,
            last_closed_5m=five,
            last_closed_15m=fifteen,
            entry=P90L90CausalEntry(state=geometry),
            proven_geometry=geometry,
        )
    return epoch_id

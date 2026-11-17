"""R06 PostgreSQL-only continuous P90 REAL Exit owner.

R06 replaced only the Entry decision reader.  This owner preserves the
independent P90 Exit capability from the same immutable MarketPublication and
original native-publicTrade journal.  It never opens a public Bybit socket or
REST endpoint and never sends an Exchange mutation itself: signed account
proof remains credential-local and existing Universal Exit/Execution publishes
only account-scoped commands for the private runtime.

The current P90 Strategy contract has exactly one native-tick action:
first-fill TRAIL035, owned by p90_real_independent_exit.  After that Bybit owns
the autonomous trailing watermark.  This module therefore maintains the exact
native MFE checkpoint from PostgreSQL and invokes the existing closed-5m P90
Exit port for dynamic opposite RAW L5-3 TP.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg.rows import tuple_row

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.entry_publication_reader import (
    EntryPublicationPin,
    read_entry_publication_pin,
    read_entry_publication_seed,
)
from bybit_workbench.market_data.source_required_feed_manifest import RequiredPublicFeed
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    P90RealAccountRoutes,
    read_p90_real_account_routes,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (
    P90RealSourceIdentity,
    inspect_real_source_identity,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceEvidence,
    OrderedNativeTrade,
    read_ordered_native_events,
    read_scoped_native_source,
)
from bybit_workbench.universal_entry.r07_publication_provenance import (
    require_r07_dispatch_epoch_guard,
)
from bybit_workbench.universal_entry.runtime_loader import load_active_strategy_bundles
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.p90l90_real_closed_exit_port import (
    P90RealClosedExitPort,
)
from bybit_workbench.universal_exit.p90l90_real_exit_execution import (
    P90RealExitExecutionPort,
)
from bybit_workbench.universal_exit.p90l90_real_mfe import (
    P90RealMfeCheckpoint,
    P90RealMfeUnverified,
)
from bybit_workbench.universal_exit.p90l90_real_mfe_ledger import P90RealMfeLedger
from bybit_workbench.universal_exit.p90l90_real_observation import (
    ScopedPrivatePositionEvidence,
)
from bybit_workbench.universal_exit.p90l90_real_pending import p90_real_exit_no_pending
from bybit_workbench.universal_exit.p90l90_real_position_provider import (
    P90RealPositionProvider,
)
from bybit_workbench.universal_exit.p90l90_real_private_router import (
    P90RealPrivateEvidenceRouter,
)
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore

_MANIFEST = Path(__file__).resolve().parents[1] / "monitoring/required_public_feeds.json"
_SYMBOLS = tuple(sorted(set().union(*ALLOWED_SYMBOLS.values())))
_MAX_BATCH = 256
_PUBLICATION_AGE = timedelta(seconds=5)
_RAW_L53_BARS = 36
_MAX_CLOSED_CATCHUP_BARS = 72
_CLOSED_SEED_BARS = _RAW_L53_BARS + _MAX_CLOSED_CATCHUP_BARS
_MAX_CLOSED_CATCHUP_AGE = timedelta(minutes=5 * _MAX_CLOSED_CATCHUP_BARS)
_SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
_FRAME_RE = re.compile(r"^market-source-frame:(\d+):(\d+)$")
_SESSION_REF_RE = re.compile(r"^market-source-session:([0-9a-f]{32})$")
_NO_NATIVE_KIND = "P90L90_REAL_CLOSED_5M_NO_NATIVE_PROVEN"
_PENDING_CLOSED_KIND = "P90L90_REAL_CLOSED_5M_PENDING_MUTATION_PROCESSED"


class P90R06ExitBlocked(RuntimeError):
    """R06 source/account causal proof is insufficient; never infer READY."""


@dataclass(frozen=True, slots=True)
class _RuntimeScope:
    bundles: tuple[object, ...]
    routes: P90RealAccountRoutes
    identity: P90RealSourceIdentity


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    evidence: NativeSourceEvidence
    pin: EntryPublicationPin
    candles: tuple[Candle, ...]
    events: tuple[OrderedNativeTrade, ...]
    truncated: bool
    closed_at: datetime
    anchor_frame: int
    anchor_index: int


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise P90R06ExitBlocked(code)


def _exit_native_ttls() -> dict[str, timedelta]:
    try:
        payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        requirements = tuple(RequiredPublicFeed(**item) for item in payload)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise P90R06ExitBlocked("P90_R06_EXIT_SOURCE_MANIFEST_INVALID") from exc
    wanted = [
        item for item in requirements
        if item.consumer_id == "EXIT_P90"
        and item.product == "LINEAR"
        and item.dataset == "PUBLIC_TRADE"
    ]
    _need(
        len(wanted) == len(_SYMBOLS)
        and {item.symbol for item in wanted} == set(_SYMBOLS)
        and all(item.required and 0 < item.freshness_seconds <= 300 for item in wanted),
        "P90_R06_EXIT_SOURCE_MANIFEST_SCOPE_DRIFT",
    )
    return {
        item.symbol: timedelta(seconds=item.freshness_seconds)
        for item in wanted
    }


def _next_closed_boundary(
    fill_at: datetime,
    last_closed: datetime | None,
) -> datetime:
    if fill_at.tzinfo is None or fill_at.utcoffset() is None:
        raise P90R06ExitBlocked("P90_R06_EXIT_FILL_CLOCK_INVALID")
    if last_closed is not None:
        if last_closed.tzinfo is None or last_closed.utcoffset() is None:
            raise P90R06ExitBlocked("P90_R06_EXIT_CLOSED_CLOCK_INVALID")
        return last_closed.astimezone(UTC) + timedelta(minutes=5)
    fill = fill_at.astimezone(UTC)
    floor = fill.replace(
        minute=(fill.minute // 5) * 5,
        second=0,
        microsecond=0,
    )
    return floor + timedelta(minutes=5)


def _mfe_source_anchor(refs: tuple[str, ...]) -> tuple[str, int, int]:
    sessions = [
        match.group(1)
        for ref in refs
        if (match := _SESSION_REF_RE.fullmatch(ref)) is not None
    ]
    frames = [
        (int(match.group(1)), int(match.group(2)))
        for ref in refs
        if (match := _FRAME_RE.fullmatch(ref)) is not None
    ]
    if (
        len(sessions) != 1
        or len(frames) != 1
        or not _SESSION_RE.fullmatch(sessions[0])
        or frames[0][0] < 1
        or frames[0][1] < 0
    ):
        raise P90R06ExitBlocked("P90_R06_EXIT_MFE_SOURCE_ANCHOR_UNVERIFIED")
    return sessions[0], frames[0][0], frames[0][1]


def _bounded_published_events(
    events: tuple[OrderedNativeTrade, ...],
    *,
    causal_boundary: datetime,
) -> tuple[tuple[OrderedNativeTrade, ...], bool]:
    if causal_boundary.tzinfo is None or causal_boundary.utcoffset() is None:
        raise P90R06ExitBlocked("P90_R06_EXIT_CLOSED_CLOCK_INVALID")
    if any(
        current.event_at.astimezone(UTC) < previous.event_at.astimezone(UTC)
        for previous, current in zip(events, events[1:], strict=False)
    ):
        raise P90R06ExitBlocked("P90_R06_EXIT_NATIVE_EVENT_TIME_REGRESSION")
    truncated = (
        len(events) > _MAX_BATCH
        and events[_MAX_BATCH].event_at.astimezone(UTC)
            < causal_boundary.astimezone(UTC)
    )
    return events[:_MAX_BATCH], truncated


def _before_boundary(
    events: tuple[Any, ...],
    *,
    boundary: datetime,
    truncated: bool,
) -> tuple[tuple[Any, ...], bool]:
    if boundary.tzinfo is None or boundary.utcoffset() is None:
        raise P90R06ExitBlocked("P90_R06_EXIT_CLOSED_CLOCK_INVALID")
    selected = tuple(
        event for event in events
        if event.event_at.astimezone(UTC) < boundary.astimezone(UTC)
    )
    # Caller sets truncated only when an omitted event is still inside the
    # publication bound.  If so we cannot claim complete MFE through boundary.
    return selected, not truncated


def _runtime_scope(
    connection: Any,
    *,
    loaded_commit: str,
    now: datetime,
) -> _RuntimeScope:
    if (
        len(loaded_commit) != 40
        or any(c not in "0123456789abcdef" for c in loaded_commit)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise P90R06ExitBlocked("P90_R06_EXIT_RELEASE_OR_CLOCK_INVALID")
    with connection.cursor(row_factory=tuple_row) as evidence_db:
        bundles = tuple(
            bundle for bundle in load_active_strategy_bundles(evidence_db)
            if bundle.card.strategy_version == VERSION
            and bundle.card.strategy_id in ALLOWED_SYMBOLS
        )
        _need(
            len(bundles) == 2
            and {bundle.card.strategy_id for bundle in bundles} == set(ALLOWED_SYMBOLS),
            "P90_R06_EXIT_EXACT_TWO_BUNDLES_REQUIRED",
        )
        epochs = evidence_db.execute(
            """SELECT epoch_id
                 FROM strategy_entry.p90l90_real_source_epochs
                WHERE status='ACTIVE'
                ORDER BY created_at DESC"""
        ).fetchall()
        _need(len(epochs) == 1, "P90_R06_EXIT_SOURCE_EPOCH_NOT_EXACT")
        identity = inspect_real_source_identity(
            evidence_db,
            epoch_id=str(epochs[0][0]),
            loaded_commit=loaded_commit,
            bundles=bundles,
        )
        routes = read_p90_real_account_routes(
            evidence_db,
            bundles=bundles,
            at=now,
        )
    return _RuntimeScope(bundles=bundles, routes=routes, identity=identity)


def _flat_source_ready(
    source_connection: Any,
    *,
    scope: _RuntimeScope,
    now: datetime,
) -> None:
    ttls = _exit_native_ttls()
    with source_connection.transaction():
        source_connection.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        # Capture source-observation time immediately before the immutable
        # snapshot reads. A publication may advance after run_once() began;
        # reusing that older clock would falsely classify a newer committed
        # publication as "from the future".
        source_now = max(now, datetime.now(UTC))
        source = read_scoped_native_source(
            source_connection,
            symbols=_SYMBOLS,
            accepted_producer_commits=scope.identity.approved_producers,
            observed_at=source_now,
            max_lag=min(ttls.values()),
        )
        _need(source.ready, "P90_R06_EXIT_NATIVE_SOURCE_NOT_READY")
        pin = read_entry_publication_pin(
            source_connection,
            observed_at=source_now,
            accepted_producer_commits=scope.identity.approved_producers,
            max_publication_age=_PUBLICATION_AGE,
            symbols=_SYMBOLS,
            intervals=("5",),
        )
        _need(
            source.session_id == pin.source_session_id
            and source.producer_release_commit == pin.producer_release_commit,
            "P90_R06_EXIT_PUBLICATION_SOURCE_DRIFT",
        )


def _latest_mfe(
    connection: Any,
    *,
    position: StrategyPosition,
    source_commit: str,
) -> tuple[P90RealMfeCheckpoint, tuple[str, ...]] | None:
    try:
        with connection.transaction():
            checkpoint, _fact, refs = P90RealMfeLedger(connection).load_latest(
                position=position,
                source_commit=source_commit,
            )
        return checkpoint, refs
    except P90RealMfeUnverified as exc:
        if str(exc) == "P90_REAL_MFE_DURABLE_NATIVE_STATE_MISSING":
            return None
        raise


def _last_closed_boundary(connection: Any, position: StrategyPosition) -> datetime | None:
    row = connection.execute(
        """SELECT max(event_at) AS closed_at
             FROM strategy_exit.exit_observations
            WHERE strategy_position_id=%s
              AND event_kind IN ('P90L90_REAL_CLOSED_5M',%s,%s)""",
        (position.strategy_position_id, _NO_NATIVE_KIND, _PENDING_CLOSED_KIND),
    ).fetchone()
    value = None if row is None else row.get("closed_at")
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise P90R06ExitBlocked("P90_R06_EXIT_DURABLE_CLOSED_BOUNDARY_INVALID")
    return value.astimezone(UTC)


def _source_snapshot(
    source_connection: Any,
    *,
    scope: _RuntimeScope,
    position: StrategyPosition,
    latest: tuple[P90RealMfeCheckpoint, tuple[str, ...]] | None,
    causal_boundary: datetime,
    now: datetime,
) -> _SourceSnapshot:
    ttl = _exit_native_ttls()[position.symbol]
    with source_connection.transaction():
        source_connection.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        source_now = max(now, datetime.now(UTC))
        source = read_scoped_native_source(
            source_connection,
            symbols=(position.symbol,),
            accepted_producer_commits=scope.identity.approved_producers,
            observed_at=source_now,
            max_lag=ttl,
        )
        _need(
            source.ready
            and source.session_id is not None
            and source.producer_release_commit is not None,
            "P90_R06_EXIT_NATIVE_SOURCE_NOT_READY",
        )
        pin = read_entry_publication_pin(
            source_connection,
            observed_at=source_now,
            accepted_producer_commits=scope.identity.approved_producers,
            max_publication_age=_PUBLICATION_AGE,
            symbols=(position.symbol,),
            intervals=("5",),
        )
        _need(
            pin.source_session_id == source.session_id
            and pin.producer_release_commit == source.producer_release_commit,
            "P90_R06_EXIT_PUBLICATION_SOURCE_DRIFT",
        )
        # Read extra VERIFIED history only for bounded causal catch-up after
        # a technical outage. Strategy geometry still consumes exactly RAW36
        # ending at each processed 5m boundary.
        seed = read_entry_publication_seed(
            source_connection,
            pin=pin,
            symbol=position.symbol,
            required_counts={"5": _CLOSED_SEED_BARS},
        )
        candles_raw = tuple(seed["5"])
        _need(
            len(candles_raw) == _CLOSED_SEED_BARS
            and all(isinstance(item, Candle) for item in candles_raw),
            "P90_R06_EXIT_RAW36_HISTORY_MISSING",
        )
        candles: tuple[Candle, ...] = tuple(
            item for item in candles_raw if isinstance(item, Candle)
        )
        closed_at = candles[-1].closed_at.astimezone(UTC)

        if latest is None:
            anchor = source_connection.execute(
                """SELECT frame_number,frame_index,event_at
                     FROM analytics.native_public_trade_source_events
                    WHERE source_session_id=%s
                      AND external_symbol=%s
                      AND event_at<%s
                    ORDER BY frame_number DESC,frame_index DESC LIMIT 1""",
                (source.session_id, position.symbol, position.fill_at),
            ).fetchone()
            _need(anchor is not None, "P90_R06_EXIT_PREFILL_NATIVE_ANCHOR_MISSING")
            anchor_frame, anchor_index, anchor_at = anchor
            _need(
                type(anchor_frame) is int
                and type(anchor_index) is int
                and isinstance(anchor_at, datetime)
                and position.fill_at - ttl <= anchor_at < position.fill_at,
                "P90_R06_EXIT_PREFILL_NATIVE_ANCHOR_STALE",
            )
        else:
            checkpoint, refs = latest
            session, anchor_frame, anchor_index = _mfe_source_anchor(refs)
            _need(
                session == source.session_id,
                "P90_R06_EXIT_OPEN_POSITION_SOURCE_SESSION_CHANGED",
            )
            anchor = source_connection.execute(
                """SELECT exchange_sequence,exec_id,event_at
                     FROM analytics.native_public_trade_source_events
                    WHERE source_session_id=%s AND external_symbol=%s
                      AND frame_number=%s AND frame_index=%s""",
                (session, position.symbol, anchor_frame, anchor_index),
            ).fetchone()
            _need(
                anchor is not None
                and anchor[0] == checkpoint.cursor_seq
                and anchor[1] == checkpoint.cursor_exec_id
                and anchor[2].astimezone(UTC) == checkpoint.cursor_at,
                "P90_R06_EXIT_MFE_SOURCE_ANCHOR_DRIFT",
            )

        rows = read_ordered_native_events(
            source_connection,
            source=source,
            after_frame=anchor_frame,
            after_index=anchor_index,
            limit=_MAX_BATCH + 1,
        )
        native = pin.native_watermarks.get(position.symbol)
        if not isinstance(native, dict):
            raise P90R06ExitBlocked("P90_R06_EXIT_NATIVE_WATERMARK_MISSING")
        bound = (native.get("frame_number"), native.get("frame_index"))
        _need(
            type(bound[0]) is int
            and type(bound[1]) is int
            and bound[0] >= 1
            and bound[1] >= 0,
            "P90_R06_EXIT_NATIVE_WATERMARK_INVALID",
        )
        published = tuple(
            event for event in rows
            if (event.frame_number, event.frame_index) <= bound
        )
        if latest is None:
            _need(
                all(
                    event.event_at.astimezone(UTC)
                    >= position.fill_at.astimezone(UTC)
                    for event in published
                ),
                "P90_R06_EXIT_PREFILL_EVENT_AFTER_ANCHOR",
            )
        published, truncated = _bounded_published_events(
            published, causal_boundary=causal_boundary,
        )
        return _SourceSnapshot(
            evidence=source,
            pin=pin,
            candles=candles,
            events=published,
            truncated=truncated,
            closed_at=closed_at,
            anchor_frame=anchor_frame,
            anchor_index=anchor_index,
        )


def _lock_source(
    connection: Any,
    *,
    scope: _RuntimeScope,
    snapshot: _SourceSnapshot,
    position: StrategyPosition,
) -> None:
    # Reuse the canonical R07 transition fence. It prevents ACTIVE/GAP/session
    # identity changes through this transaction without row-locking the hot
    # source-session row whose last_accepted_at is updated continuously.
    require_r07_dispatch_epoch_guard(
        connection, {"strategy_id": position.strategy_id},
    )
    row = connection.execute(
        """SELECT producer_release_commit,state,ended_at
             FROM analytics.native_public_trade_source_sessions
            WHERE source_session_id=%s""",
        (snapshot.pin.source_session_id,),
    ).fetchone()
    _need(
        row is not None
        and row.get("producer_release_commit") == snapshot.pin.producer_release_commit
        and row.get("state") == "ACTIVE"
        and row.get("ended_at") is None
        and snapshot.pin.producer_release_commit in scope.identity.approved_producers,
        "P90_R06_EXIT_SOURCE_SESSION_CHANGED_DURING_COMMIT",
    )
    publication = connection.execute(
        """SELECT source_session_id,producer_release_commit,publication_status
             FROM analytics.market_publications
            WHERE publication_id=%s
            FOR SHARE""",
        (snapshot.pin.publication_id,),
    ).fetchone()
    _need(
        publication is not None
        and publication.get("source_session_id") == snapshot.pin.source_session_id
        and publication.get("producer_release_commit")
            == snapshot.pin.producer_release_commit
        and publication.get("publication_status") == "WATERMARKS_ONLY",
        "P90_R06_EXIT_PUBLICATION_CHANGED_DURING_COMMIT",
    )
    epoch = connection.execute(
        """SELECT source_commit,status
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE epoch_id=%s
            FOR SHARE""",
        (scope.identity.epoch_id,),
    ).fetchone()
    _need(
        epoch is not None
        and epoch.get("source_commit") == scope.identity.origin_commit
        and epoch.get("status") == "ACTIVE",
        "P90_R06_EXIT_STRATEGY_SOURCE_EPOCH_CHANGED",
    )


def _append_mfe(
    connection: Any,
    *,
    scope: _RuntimeScope,
    position: StrategyPosition,
    snapshot: _SourceSnapshot,
    events: tuple[OrderedNativeTrade, ...],
    first: bool,
) -> int:
    if not events:
        return 0
    with connection.transaction():
        _lock_source(
            connection, scope=scope, snapshot=snapshot, position=position,
        )
        ledger = P90RealMfeLedger(connection)
        first_event = first
        for event in events:
            cursor = TradeCursor(
                symbol=event.symbol,
                seq=event.exchange_sequence,
                exec_id=event.exec_id,
                traded_at=event.event_at,
            )
            ledger.on_trade(
                position=position,
                source_commit=scope.identity.origin_commit,
                fact_id=(
                    f"r06-pg-native:{snapshot.pin.source_session_id}:"
                    f"{event.frame_number}:{event.frame_index}:{event.exec_id}"
                ),
                cursor=cursor,
                price=event.price,
                received_at=event.db_accepted_at,
                source_refs=(
                    f"bybit:publicTrade:{position.symbol}:{event.exec_id}",
                    f"market-source-session:{snapshot.pin.source_session_id}",
                    f"market-source-frame:{event.frame_number}:{event.frame_index}",
                    f"market-publication:{snapshot.pin.publication_id}",
                ),
                first_fill_forward_coverage_proven=first_event,
                native_continuity_proven=True,
            )
            first_event = False
    return len(events)


def _record_no_native_boundary(
    connection: Any,
    *,
    scope: _RuntimeScope,
    position: StrategyPosition,
    snapshot: _SourceSnapshot,
    boundary: datetime,
) -> None:
    observation_id = "p90-r06-no-native-" + fingerprint({
        "position_id": position.strategy_position_id,
        "boundary": boundary,
        "publication_id": snapshot.pin.publication_id,
        "session": snapshot.pin.source_session_id,
    })[:32]
    refs = (
        f"market-source-session:{snapshot.pin.source_session_id}",
        f"market-publication:{snapshot.pin.publication_id}",
        f"closed-candle:{position.symbol}:5:{boundary.isoformat()}",
    )
    attrs = FrozenPolicy.from_mapping({
        "source_session_id": snapshot.pin.source_session_id,
        "publication_id": snapshot.pin.publication_id,
        "no_postfill_native_trade_before_boundary": True,
    })
    with connection.transaction():
        _lock_source(
            connection, scope=scope, snapshot=snapshot, position=position,
        )
        connection.execute(
            """INSERT INTO strategy_exit.exit_observations(
                   observation_id,strategy_position_id,exit_plan_fingerprint,
                   symbol,event_kind,event_at,observed_at,received_at,
                   attributes,source_refs)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
               ON CONFLICT(observation_id) DO NOTHING""",
            (
                observation_id,
                position.strategy_position_id,
                position.exit_plan_fingerprint,
                position.symbol,
                _NO_NATIVE_KIND,
                boundary,
                boundary,
                datetime.now(UTC),
                attrs.payload_json,
                canonical_json(refs),
            ),
        )


def _record_pending_closed_boundary(
    connection: Any,
    *,
    scope: _RuntimeScope,
    position: StrategyPosition,
    snapshot: _SourceSnapshot,
    boundary: datetime,
) -> None:
    observation_id = "p90-r06-pending-closed-" + fingerprint({
        "position_id": position.strategy_position_id,
        "boundary": boundary,
        "publication_id": snapshot.pin.publication_id,
        "session": snapshot.pin.source_session_id,
    })[:32]
    refs = (
        f"market-source-session:{snapshot.pin.source_session_id}",
        f"market-publication:{snapshot.pin.publication_id}",
        f"closed-candle:{position.symbol}:5:{boundary.isoformat()}",
    )
    attrs = FrozenPolicy.from_mapping({
        "source_session_id": snapshot.pin.source_session_id,
        "publication_id": snapshot.pin.publication_id,
        "pending_exit_mutation": True,
        "closed_boundary_processed": True,
    })
    with connection.transaction():
        _lock_source(
            connection, scope=scope, snapshot=snapshot, position=position,
        )
        connection.execute(
            """INSERT INTO strategy_exit.exit_observations(
                   observation_id,strategy_position_id,exit_plan_fingerprint,
                   symbol,event_kind,event_at,observed_at,received_at,
                   attributes,source_refs)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
               ON CONFLICT(observation_id) DO NOTHING""",
            (
                observation_id,
                position.strategy_position_id,
                position.exit_plan_fingerprint,
                position.symbol,
                _PENDING_CLOSED_KIND,
                boundary,
                boundary,
                datetime.now(UTC),
                attrs.payload_json,
                canonical_json(refs),
            ),
        )


class _MutationSafety:
    def __init__(
        self,
        connection: Any,
        *,
        routes: P90RealAccountRoutes,
        private: P90RealPrivateEvidenceRouter,
        first: dict[str, ScopedPrivatePositionEvidence],
        boundary: datetime,
    ) -> None:
        self._db = connection
        self._routes = routes
        self._private = private
        self._first = first
        self._boundary = boundary
        self.last_denial_code: str | None = None

    def __call__(
        self,
        position: StrategyPosition,
        evaluation: object,
        checked_at: datetime,
    ) -> bool:
        self.last_denial_code = None
        decision = getattr(evaluation, "decision", None)
        if decision is None or checked_at.tzinfo is None:
            self.last_denial_code = "P90_R06_EXIT_DECISION_INVALID"
            return False
        prior = self._first.get(position.strategy_position_id)
        route = (
            self._routes.main
            if position.strategy_id == self._routes.main.strategy_id
            else self._routes.sub
        )
        if prior is None:
            self.last_denial_code = "P90_R06_EXIT_PRIVATE_BASELINE_MISSING"
            return False
        if not p90_real_exit_no_pending(
            self._db,
            position=position,
            trading_account_id=route.trading_account_id,
            publishing_exit_decision_id=decision.exit_decision_id,
        ):
            self.last_denial_code = "P90_R06_EXIT_PENDING_MUTATION"
            return False
        try:
            again = self._private.for_closed_boundary(position, checked_at)
        except Exception:
            self.last_denial_code = "P90_R06_EXIT_PRIVATE_RECHECK_FAILED"
            return False
        fields = (
            "trading_account_id", "connector_id", "signed_provider_uid",
            "strategy_execution_binding_id", "exchange_position_key",
            "symbol", "position_idx",
        )
        if (
            again.signed_provider_uid != route.registered_exchange_uid
            or any(getattr(again, field) != getattr(prior, field) for field in fields)
            or again.pending_exit_mutation is not False
            or again.observed_at < prior.observed_at
            or again.position != prior.position
        ):
            self.last_denial_code = "P90_R06_EXIT_PRIVATE_RECHECK_DRIFT"
            return False
        return True


def _closed_history_for_boundary(
    candles: tuple[Candle, ...],
    boundary: datetime,
) -> tuple[Candle, ...]:
    """Exact RAW36 ending at one verified historical/current 5m boundary."""
    if boundary.tzinfo is None or boundary.utcoffset() is None:
        raise P90R06ExitBlocked("P90_R06_EXIT_CLOSED_CLOCK_INVALID")
    target = boundary.astimezone(UTC)
    matches = [
        index for index, candle in enumerate(candles)
        if candle.closed_at.astimezone(UTC) == target
    ]
    if len(matches) != 1:
        raise P90R06ExitBlocked("P90_R06_EXIT_CLOSED_BOUNDARY_GAP")
    end = matches[0] + 1
    start = end - _RAW_L53_BARS
    if start < 0:
        raise P90R06ExitBlocked("P90_R06_EXIT_RAW36_HISTORY_MISSING")
    history = candles[start:end]
    if (
        len(history) != _RAW_L53_BARS
        or history[-1].closed_at.astimezone(UTC) != target
    ):
        raise P90R06ExitBlocked("P90_R06_EXIT_RAW36_HISTORY_MISSING")
    return history


def _publication_closed_watermark_at_or_after(
    snapshot: _SourceSnapshot,
    *,
    symbol: str,
    boundary: datetime,
    raw: object,
) -> bool:
    scoped = raw.get(symbol) if isinstance(raw, dict) else None
    published = scoped.get("5") if isinstance(scoped, dict) else None
    pinned = snapshot.pin.closed_refs.get(symbol, {}).get("5")
    if not isinstance(published, str) or published != pinned:
        return False
    try:
        at = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (
        at.tzinfo is not None
        and at.utcoffset() is not None
        and at.astimezone(UTC) >= boundary.astimezone(UTC)
    )


def _process_closed(
    connection: Any,
    *,
    scope: _RuntimeScope,
    position: StrategyPosition,
    snapshot: _SourceSnapshot,
    boundary: datetime,
    now: datetime,
) -> str:
    _need(
        timedelta(0) <= now - boundary <= _MAX_CLOSED_CATCHUP_AGE,
        "P90_R06_EXIT_CLOSED_BOUNDARY_TOO_OLD",
    )
    history = _closed_history_for_boundary(snapshot.candles, boundary)
    candle = history[-1]
    private = P90RealPrivateEvidenceRouter(
        account_by_strategy=scope.routes.account_by_strategy,
        connector_by_strategy=scope.routes.connector_by_strategy,
    )
    first_private: dict[str, ScopedPrivatePositionEvidence] = {}

    def private_provider(
        subject: StrategyPosition,
        _boundary: datetime,
    ) -> ScopedPrivatePositionEvidence:
        # CLOSED market causality belongs to the verified MarketSource/RAW36
        # fact. The independent private port proves the CURRENT signed
        # position/protection immediately before any Exit publication; it must
        # not replay an old IPC request timestamp during bounded catch-up.
        evidence = private.for_closed_boundary(subject, datetime.now(UTC))
        route = (
            scope.routes.main
            if subject.strategy_id == scope.routes.main.strategy_id
            else scope.routes.sub
        )
        if evidence.signed_provider_uid != route.registered_exchange_uid:
            raise P90R06ExitBlocked("P90_R06_EXIT_PRIVATE_UID_MISMATCH")
        first_private[subject.strategy_position_id] = evidence
        return evidence

    provider = P90RealPositionProvider(
        connection,
        account_by_strategy=scope.routes.account_by_strategy,
    )
    safety = _MutationSafety(
        connection,
        routes=scope.routes,
        private=private,
        first=first_private,
        boundary=boundary,
    )
    execution = P90RealExitExecutionPort(
        connection,
        independently_verified_execution_safety=safety,
        account_by_strategy=scope.routes.account_by_strategy,
    )

    def pending(subject: StrategyPosition) -> bool:
        return not p90_real_exit_no_pending(
            connection,
            position=subject,
            trading_account_id=subject.trading_account_id or "",
        )

    def closed_history(symbol: str, at: datetime) -> tuple[Candle, ...]:
        return (
            history
            if symbol == position.symbol and at == boundary
            else ()
        )

    def closed_continuity(symbol: str, at: datetime) -> bool:
        if symbol != position.symbol or at != boundary:
            return False
        row = connection.execute(
            """SELECT closed_candle_watermarks
                 FROM analytics.market_publications
                WHERE publication_id=%s""",
            (snapshot.pin.publication_id,),
        ).fetchone()
        raw = None if row is None else row.get("closed_candle_watermarks")
        return _publication_closed_watermark_at_or_after(
            snapshot,
            symbol=symbol,
            boundary=boundary,
            raw=raw,
        )

    port = P90RealClosedExitPort(
        position_provider=provider,
        independently_verified_closed_history=closed_history,
        independent_causal_source_proof=closed_continuity,
        independently_signed_private_evidence=private_provider,
        source_commit=scope.identity.origin_commit,
        mfe_ledger=P90RealMfeLedger(connection),
        exit_store=PostgresExitShadowStore(connection),
        verified_execution_port=execution,
        private_state_pending=pending,
    )
    fact = MarketFactEnvelope(
        fact_id="r06-p90-closed-" + fingerprint({
            "session": snapshot.pin.source_session_id,
            "publication": snapshot.pin.publication_id,
            "symbol": position.symbol,
            "boundary": boundary,
        })[:32],
        event_kind="CANDLE_CLOSED",
        symbol=position.symbol,
        event_at=boundary,
        observed_at=boundary,
        received_at=now,
        source_refs=(
            f"market-source-session:{snapshot.pin.source_session_id}",
            f"market-publication:{snapshot.pin.publication_id}",
            f"closed-candle:{position.symbol}:5:{boundary.isoformat()}",
        ),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": "5",
            "closed_at": boundary.isoformat(),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
        }),
    )
    with connection.transaction():
        _lock_source(
            connection, scope=scope, snapshot=snapshot, position=position,
        )
        result = port(fact)
    if not result:
        return "P90_R06_EXIT_CLOSED_NO_POSITION"
    status = str(result[0].status.value)
    return "P90_R06_EXIT_CLOSED_" + status


def _open_positions(
    connection: Any,
    *,
    scope: _RuntimeScope,
) -> tuple[StrategyPosition, ...]:
    provider = P90RealPositionProvider(
        connection,
        account_by_strategy=scope.routes.account_by_strategy,
    )
    result: list[StrategyPosition] = []
    for symbol in _SYMBOLS:
        rows = provider(symbol)
        if rows:
            result.append(rows[0][0])
    return tuple(result)


def run_p90_r06_exit_once(
    connection: Any,
    source_connection: Any,
    *,
    loaded_commit: str,
    now: datetime | None = None,
) -> str:
    """One bounded R06 Exit cycle. No public network and no direct Exchange write."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    scope = _runtime_scope(connection, loaded_commit=loaded_commit, now=current)
    positions = _open_positions(connection, scope=scope)
    if not positions:
        _flat_source_ready(source_connection, scope=scope, now=current)
        return "P90_R06_EXIT_OWNER_READY_FLAT"

    results: list[str] = []
    for position in positions:
        latest = _latest_mfe(
            connection,
            position=position,
            source_commit=scope.identity.origin_commit,
        )
        last_closed = _last_closed_boundary(connection, position)
        expected = _next_closed_boundary(position.fill_at, last_closed)
        snapshot = _source_snapshot(
            source_connection,
            scope=scope,
            position=position,
            latest=latest,
            causal_boundary=expected,
            now=current,
        )
        if snapshot.closed_at > expected:
            # A technical outage may leave one or more CLOSED boundaries
            # behind the current publication. Process EXACTLY the next durable
            # boundary from the verified seed; never jump to the newest bar.
            _closed_history_for_boundary(snapshot.candles, expected)

        # Always stop native MFE immediately before the next unprocessed closed
        # 5m boundary. A trade stamped exactly at that boundary belongs after
        # the closed-candle Exit evaluation, matching the canonical causal join.
        before, complete = _before_boundary(
            snapshot.events,
            boundary=expected,
            truncated=snapshot.truncated,
        )
        appended = _append_mfe(
            connection,
            scope=scope,
            position=position,
            snapshot=snapshot,
            events=before,
            first=latest is None,
        )
        if not complete:
            results.append(
                f"P90_R06_EXIT_NATIVE_CATCHUP:{position.symbol}:{appended}"
            )
            continue
        if snapshot.closed_at < expected:
            results.append(
                f"P90_R06_EXIT_MFE_AWAITING_CLOSED:{position.symbol}:{appended}"
            )
            continue

        latest_after = _latest_mfe(
            connection,
            position=position,
            source_commit=scope.identity.origin_commit,
        )
        if latest_after is None:
            # Exact source proves no post-fill native trade before this
            # boundary. Favorable MFE therefore cannot exceed Entry; the
            # +0.5% dynamic-TP gate cannot match. Persist only this proof.
            _record_no_native_boundary(
                connection,
                scope=scope,
                position=position,
                snapshot=snapshot,
                boundary=expected,
            )
            results.append(f"P90_R06_EXIT_NO_NATIVE_BOUNDARY:{position.symbol}")
            continue
        closed_result = _process_closed(
            connection,
            scope=scope,
            position=position,
            snapshot=snapshot,
            boundary=expected,
            now=datetime.now(UTC),
        )
        if closed_result == "P90_R06_EXIT_CLOSED_BLOCKED":
            # The only non-exception BLOCKED result is the explicit
            # pending-private-mutation path. Existing Exchange protection
            # remains resident; advance the causal bar boundary so a completed
            # trailing/TP command does not permanently strand future bars.
            _record_pending_closed_boundary(
                connection,
                scope=scope,
                position=position,
                snapshot=snapshot,
                boundary=expected,
            )
        results.append(f"{position.symbol}:" + closed_result)

    return "P90_R06_EXIT_OWNER_RUNNING|" + "|".join(results)

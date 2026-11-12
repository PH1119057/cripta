"""R06 PG-only, NO-ORDER published Entry consumer for exactly five P90 symbols.

No Bybit public socket or REST is opened. No EntryExecutionRequest, Exchange
mutation, PAPER proxy or financial permission is created. The old Observer
process must be physically stopped by the release rail before this gated mode
can run. Never run both market decision readers simultaneously.
"""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import psycopg

from bybit_workbench.market_data.entry_publication_reader import (
    EntryPublicationPin, EntryPublicationUnavailable, read_entry_publication_pin,
    read_entry_publication_seed,
)
from bybit_workbench.universal_entry.engine import UniversalEntryEngine
from bybit_workbench.universal_entry.p90l90_crossing_fact import p90l90_crossing_fact
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from bybit_workbench.universal_entry.p90l90_scoped_native_codec import original_native_fact
from bybit_workbench.universal_entry.p90l90_scoped_native_dispatch import (
    consume_pinned_entry_native_batch,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    read_scoped_native_source, verify_native_producer_code_compatibility,
)
from bybit_workbench.universal_entry.p90_r06_no_order_state import (
    advance_symbol, dump_symbol, restore_symbol,
)
from bybit_workbench.universal_entry.registry import ActivePlanRegistry
from bybit_workbench.universal_entry.runtime_loader import load_active_strategy_bundles
from operations.monitoring.r06_entry_publication_shadow_probe import (
    EXISTING_P90_COLD_COUNTS, FIVE, verify_current_entry_plan_scope,
)
from operations.monitoring.r06_fresh_entry_boundary import (
    R06FreshCutoverBlocked, _require_no_exposure_or_requests,
    bootstrap_future_entry_checkpoints, verify_r06_fresh_publication,
)

_SOURCE_CHECKOUT = Path("/srv/cripta/source_checkout")
_STRATEGIES = (MAIN_ID, SUB_ID)
_SOURCE = "ms-bybit-mainnet-public-v1"
_FRESH = timedelta(seconds=5)
_MAX_LATE_SIGNAL = timedelta(seconds=5)

class R06PublishedEntryBlocked(RuntimeError):
    """A no-order PG-only Entry consumer has no verified current contract."""


def _bootstrap_transient_retryable(
    exc: BaseException, *, detached_at: datetime, observed_at: datetime,
) -> bool:
    """Wait only for fresh evidence BEFORE the first no-order decision cursor.

    The existing 10-minute future-5m bootstrap budget is reused. No stale
    publication is accepted, no H9/ATR defaults change, and no financial
    action or new source reader is introduced.
    """
    if not timedelta(0) <= observed_at-detached_at <= timedelta(minutes=10):
        return False
    if isinstance(exc, EntryPublicationUnavailable):
        return True
    return (
        isinstance(exc, R06FreshCutoverBlocked)
        and str(exc) in {
            "R06_NEW_PUBLICATION_UNVERIFIED",
            "R06_START_PUBLICATION_STALE_OR_CHANGED",
            "R06_FUTURE_CLOSED_BOUNDARY_NOT_REACHED",
        }
    )

def _bundles(connection: Any) -> tuple[Any, ...]:
    rows = tuple(
        b for b in load_active_strategy_bundles(connection)
        if b.card.scope.to_dict().get("kind") != "OWNER_FULL_CONTOUR_DIAGNOSTIC"
    )
    if (
        len(rows) != 2
        or {b.card.strategy_id for b in rows} != set(_STRATEGIES)
        or any(
            b.card.strategy_version != VERSION
            or frozenset(b.card.symbols) != ALLOWED_SYMBOLS[b.card.strategy_id]
            or frozenset(b.entry_plan.symbols) != ALLOWED_SYMBOLS[b.card.strategy_id]
            for b in rows
        )
    ):
        raise R06PublishedEntryBlocked("R06_PG_ONLY_LEGACY_OR_ORDINARY_OWNER_PRESENT")
    return tuple(sorted(rows, key=lambda b: b.card.strategy_id))

def _exact_scope_identity(bundle: Any) -> tuple[object, ...]:
    return (
        bundle.card.strategy_id,
        bundle.card.strategy_version,
        bundle.card.strategy_config_fingerprint,
        bundle.entry_plan.entry_plan_fingerprint,
        bundle.exit_plan.exit_plan_fingerprint,
        bundle.activation.activation_id,
        bundle.activation.enabled,
        bundle.activation.enabled_at,
    )


def _engines(bundles: tuple[Any, ...]) -> dict[str, UniversalEntryEngine]:
    engines = {}
    for b in bundles:
        registry = ActivePlanRegistry()
        registry.register_card(b.card)
        if registry.activate(b.activation) != b.entry_plan:
            raise R06PublishedEntryBlocked("R06_P90_ENTRY_PLAN_REBUILD_DRIFT")
        engines[b.card.strategy_id] = UniversalEntryEngine(registry)
    return engines

def _one_pin(readonly: Any, *, at: datetime, loaded: str) -> tuple[
    EntryPublicationPin, dict[str, dict[str, tuple[Any, ...]]], frozenset[str],
]:
    with readonly.transaction():
        readonly.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        readonly.execute("SET LOCAL statement_timeout=20000")
        if verify_current_entry_plan_scope(readonly) != FIVE:
            raise R06PublishedEntryBlocked("R06_PLAN_SCOPE_UNVERIFIED")
        sessions = readonly.execute(
            "SELECT source_session_id,producer_release_commit "
            "FROM analytics.native_public_trade_source_sessions "
            "WHERE market_source_id=%s AND state='ACTIVE' "
            "ORDER BY subscribed_at DESC LIMIT 2",
            (_SOURCE,),
        ).fetchall()
        if len(sessions) != 1:
            raise R06PublishedEntryBlocked("R06_SOURCE_EPOCH_AMBIGUOUS")
        accepted = verify_native_producer_code_compatibility(
            source_checkout=_SOURCE_CHECKOUT,
            claimed_producer_commit=sessions[0][1],
            loaded_consumer_commit=loaded,
        )
        pin = read_entry_publication_pin(
            readonly, observed_at=at, accepted_producer_commits=accepted,
            max_publication_age=_FRESH, symbols=FIVE, intervals=("5", "15"),
        )
        if pin.source_session_id != sessions[0][0]:
            raise R06PublishedEntryBlocked("R06_MIXED_SOURCE_EPOCH")
        history = {
            sym: read_entry_publication_seed(
                readonly, pin=pin, symbol=sym,
                required_counts=dict(EXISTING_P90_COLD_COUNTS),
            ) for sym in FIVE
        }
    return pin, history, accepted

def _checkpoints(writer: Any, *, pin: EntryPublicationPin) -> tuple[Any, ...]:
    rows = writer.execute(
        "SELECT strategy_id,strategy_version,source_session_id,"
        "consumer_release_commit,bootstrap_publication_id,causal_state "
        "FROM strategy_entry.p90_scoped_native_consumer_checkpoints "
        "WHERE source_session_id=%s ORDER BY strategy_id",
        (pin.source_session_id,),
    ).fetchall()
    if not rows:
        historical = writer.execute(
            "SELECT count(*) FROM strategy_entry.p90_scoped_native_consumer_checkpoints"
        ).fetchone()
        if historical != (0,):
            raise R06PublishedEntryBlocked("R06_OLD_ENTRY_EPOCH_REQUIRES_EXPLICIT_BRIDGE")
        return ()
    if (
        len(rows) != 2 or {r[0] for r in rows} != set(_STRATEGIES)
        or any(r[1] != VERSION or r[2] != pin.source_session_id
               or not isinstance(r[4], int) or not isinstance(r[5], dict)
               or r[5].get("version") != 1 for r in rows)
    ):
        raise R06PublishedEntryBlocked("R06_EXISTING_DECISION_EPOCH_MISMATCH")
    return tuple(rows)

def _observe_one(
    writer: Any, *, pin: EntryPublicationPin, history: dict[str, dict[str, tuple[Any, ...]]],
    source: Any, bundle: Any, engine: UniversalEntryEngine,
    loaded: str, at: datetime,
) -> int:
    name = bundle.card.strategy_id
    saved = writer.execute(
        "SELECT causal_state,last_publication_id FROM "
        "strategy_entry.p90_scoped_native_consumer_checkpoints "
        "WHERE strategy_id=%s AND strategy_version=%s AND source_session_id=%s FOR UPDATE",
        (name, VERSION, pin.source_session_id),
    ).fetchone()
    if saved is None or not isinstance(saved[0], dict):
        raise R06PublishedEntryBlocked("R06_DURABLE_ENTRY_CAUSAL_STATE_MISSING")
    stored = saved[0]
    if stored.get("version") != 1 or not isinstance(stored.get("by_symbol"), dict):
        raise R06PublishedEntryBlocked("R06_DURABLE_ENTRY_CAUSAL_SCHEMA_DRIFT")
    original = stored["by_symbol"]
    if not set(original) <= set(ALLOWED_SYMBOLS[name]):
        raise R06PublishedEntryBlocked("R06_DURABLE_ENTRY_CAUSAL_SYMBOL_DRIFT")
    automata: dict[str, Any] = {
        sym: restore_symbol(
            original.get(sym), five=history[sym]["5"], fifteen=history[sym]["15"],
            published_closed_at=datetime.fromisoformat(pin.closed_refs[sym]["5"]),
        )
        for sym in ALLOWED_SYMBOLS[name]
    }
    new_count = 0
    fresh = at
    def evaluate(event: Any, publication_id: int) -> None:
        nonlocal new_count
        sym = event.symbol
        if sym not in automata or publication_id != pin.publication_id:
            raise R06PublishedEntryBlocked("R06_PUBLICATION_SYMBOL_OR_ID_MISMATCH")
        closed_at = datetime.fromisoformat(pin.closed_refs[sym]["5"])
        fact, cursor = original_native_fact(event)
        crossing = advance_symbol(
            automata[sym], price=event.price, event_at=event.event_at,
            five=history[sym]["5"], fifteen=history[sym]["15"],
            published_closed_at=closed_at,
        )
        # Late original events are used ONLY to reconstruct the immutable
        # causal automaton. Never backdate any EntrySignal/decision.
        if not timedelta(0) <= fresh-event.event_at <= _MAX_LATE_SIGNAL:
            return
        for candidate in crossing:
            geometry = automata[sym].state
            if geometry is None:
                raise R06PublishedEntryBlocked("R06_CANDIDATE_GEOMETRY_MISSING")
            signal_fact = p90l90_crossing_fact(
                entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
                source_fact=fact, crossing=candidate, geometry=geometry,
            )
            evaluations = engine.process(
                signal_fact, proven_p90_native_cursor=cursor,
                allow_new_signals=True, account_ref="BYBIT:UNIFIED",
            )
            for item in evaluations:
                if (
                    item.decision is not None or item.execution_request is not None
                    or item.notifications or item.account_outcomes
                    or item.signal.strategy_id != name
                    or item.signal.entry_plan_fingerprint
                       != bundle.entry_plan.entry_plan_fingerprint
                ):
                    raise R06PublishedEntryBlocked("R06_FINANCIAL_OR_CROSS_PLAN_EFFECT_FORBIDDEN")
                writer.execute(
                    "INSERT INTO strategy_entry.p90_r06_publication_observations "
                    "(strategy_id,strategy_version,source_session_id,"
                    "frame_number,frame_index,native_exec_id,publication_id,"
                    "signal_id,symbol,direction,event_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (name, VERSION, pin.source_session_id,
                     event.frame_number,event.frame_index,event.exec_id,
                     publication_id,item.signal.signal_id,sym,
                     item.signal.direction.value,event.event_at),
                )
                new_count += 1
    consumed = consume_pinned_entry_native_batch(
        writer, publication_pin=pin, max_publication_age=_FRESH, source=source,
        strategy_id=name, strategy_version=VERSION,
        entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
        exit_plan_fingerprint=bundle.exit_plan.exit_plan_fingerprint,
        consumer_release_commit=loaded, observed_at=at,
        max_evidence_age=timedelta(seconds=30),
        on_publication_trade=evaluate,
    )
    serialized = {
        "version": 1,
        "by_symbol": {sym: dump_symbol(state) for sym, state in automata.items()
                      if state.previous_price is not None},
    }
    # Even an idle publication is an explicitly processed immutable view.
    # Advancing its ID without moving native cursor is safe ONLY after the
    # pinned dispatcher proved there were no published native rows to commit.
    expected_id = pin.publication_id if consumed else saved[1]
    result = writer.execute(
        "UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
        "SET causal_state=%s::jsonb,last_publication_id=%s,checked_at=clock_timestamp() "
        "WHERE strategy_id=%s AND strategy_version=%s AND source_session_id=%s "
        "AND last_publication_id=%s",
        (json.dumps(serialized,sort_keys=True),pin.publication_id,
         name,VERSION,pin.source_session_id,expected_id),
    )
    if result.rowcount != 1:
        raise R06PublishedEntryBlocked("R06_ENTRY_ATOMIC_CAUSAL_STATE_CAS_FAILED")
    return new_count

def _runtime_actor_verified() -> bool:
    """Compare actual service UID with the deployed runtime owner on Linux."""
    try:
        return os.geteuid() == os.stat("/srv/cripta/runtime/current").st_uid
    except (OSError, AttributeError):
        return False


def _require_exclusive_systemd_owner() -> None:
    """Only the SAME unit that owned old WS can replace its Entry reader.

    The service manager waits for old unit cgroup teardown before launching
    the new ExecStart. Running this script from a shell is not a cutover.
    """
    try:
        scope = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError as exc:
        raise R06PublishedEntryBlocked("R06_SINGLE_UNIT_IDENTITY_UNAVAILABLE") from exc
    if (
        not _runtime_actor_verified()
        or not os.environ.get("INVOCATION_ID")
        or "/system.slice/cripta-universal-entry-observer.service" not in scope
    ):
        raise R06PublishedEntryBlocked("R06_OLD_ENTRY_SERVICE_EXCLUSIVITY_NOT_PROVEN")


def run_pg_only(
    *, loaded_commit: str, dsn: str,
    write_status: Callable[..., None],
) -> None:
    """Explicitly gated same-unit replacement; no worker/Exchange network."""
    if (
        os.environ.get("CRIPTA_R06_PG_ONLY") != "ENABLED"
        or not isinstance(loaded_commit,str) or len(loaded_commit) != 40
        or any(c not in "0123456789abcdef" for c in loaded_commit)
    ):
        raise R06PublishedEntryBlocked("R06_PG_ONLY_OPERATOR_GATE_OR_RELEASE_INVALID")
    _require_exclusive_systemd_owner()
    detached_at = datetime.now(UTC)
    stopping = False
    def stop(_sig: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    status = {
        "state":"R06_PG_NO_ORDER_PREPARING", "epoch_id":None,
        "active_bundles":(), "symbols":FIVE,
        "reason":"R06 PG-only SOURCE_ONLY; financial and observer READY forbidden",
        "sensor_status":{"native_pg_delivery_scope":"R06_NO_ORDER_ENTRY_DECISION_ONLY"},
    }
    write_status(**status)
    try:
        with psycopg.connect(dsn,autocommit=True,connect_timeout=5) as writer, \
             psycopg.connect(dsn,autocommit=True,connect_timeout=5) as reader:
            if writer.execute(
                "SELECT pg_try_advisory_lock(hashtext('R06_P90_PG_ONLY_ENTRY_DECISION_OWNER'))"
            ).fetchone() != (True,):
                raise R06PublishedEntryBlocked("R06_PG_ONLY_DECISION_OWNER_ALREADY_RUNNING")
            bundles = _bundles(writer)
            engines = _engines(bundles)
            original_scope = tuple(_exact_scope_identity(b) for b in bundles)
            _require_no_exposure_or_requests(writer)
            prior_pub = 0
            last_status = 0.0
            first_cursor_accepted = False
            while not stopping:
                at = datetime.now(UTC)
                _require_no_exposure_or_requests(writer)
                if tuple(_exact_scope_identity(b) for b in _bundles(writer)) != original_scope:
                    raise R06PublishedEntryBlocked("R06_ACTIVATION_OR_PLAN_IDENTITY_DRIFT")
                try:
                    pin, history, commits = _one_pin(reader,at=at,loaded=loaded_commit)
                except EntryPublicationUnavailable as exc:
                    if (
                        first_cursor_accepted
                        or not _bootstrap_transient_retryable(
                            exc,detached_at=detached_at,observed_at=at,
                        )
                    ):
                        raise
                    time.sleep(1.0)
                    continue
                checkpoints = _checkpoints(writer,pin=pin)
                if not checkpoints:
                    # A single owner process replaces old WS/REST after the
                    # service manager stopped its preceding ExecStart; the
                    # NEXT fully closed 5m after process start is mandatory.
                    if any(
                        datetime.fromisoformat(pin.closed_refs[s]["5"]) <= detached_at
                        for s in FIVE
                    ):
                        if at-detached_at > timedelta(minutes=10):
                            raise R06PublishedEntryBlocked("R06_NEW_5M_BOOTSTRAP_TIMEOUT")
                        time.sleep(0.25)
                        continue
                    with reader.transaction():
                        reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                        proof = verify_r06_fresh_publication(
                            reader,legacy_detached_at=detached_at,observed_at=at,
                            source_checkout=_SOURCE_CHECKOUT,
                            installed_consumer_commit=loaded_commit,
                        )
                    identities=tuple(
                        (b.card.strategy_id,b.card.strategy_version,
                         b.entry_plan.entry_plan_fingerprint,
                         b.exit_plan.exit_plan_fingerprint)
                        for b in bundles
                    )
                    try:
                        bootstrap_future_entry_checkpoints(
                            writer,proof=proof,strategies=identities,
                            consumer_release_commit=loaded_commit,
                            active_p90_bundles=bundles,
                        )
                    except R06FreshCutoverBlocked as exc:
                        if not _bootstrap_transient_retryable(
                            exc,detached_at=detached_at,observed_at=at,
                        ):
                            raise
                        time.sleep(1.0)
                        continue
                    prior_pub=0
                    continue
                if any(r[3] != loaded_commit for r in checkpoints):
                    raise R06PublishedEntryBlocked(
                        "R06_CURRENT_RELEASE_DIFFERS_FROM_DECISION_CURSOR"
                    )
                if pin.publication_id <= prior_pub:
                    time.sleep(0.15)
                    continue
                # All five originals/closed seeds are tied to ONE verified
                # publication; the two strategy checkpoints commit together.
                with writer.transaction():
                    _require_no_exposure_or_requests(writer)
                    signals = 0
                    for bundle in bundles:
                        name = bundle.card.strategy_id
                        source = read_scoped_native_source(
                            writer,symbols=tuple(sorted(ALLOWED_SYMBOLS[name])),
                            accepted_producer_commits=commits,observed_at=at,
                            max_lag=timedelta(seconds=30),
                        )
                        if not source.ready:
                            raise R06PublishedEntryBlocked(
                                "R06_PUBLISHED_NATIVE_SOURCE_NOT_CURRENT:"+name
                            )
                        signals += _observe_one(
                            writer,pin=pin,history=history,source=source,
                            bundle=bundle,engine=engines[name],
                            loaded=loaded_commit,at=at,
                        )
                prior_pub = pin.publication_id
                first_cursor_accepted = True
                if time.monotonic()-last_status > 2.0:
                    write_status(
                        state="R06_PG_NO_ORDER_SOURCE_ONLY",epoch_id=pin.source_session_id,
                        active_bundles=bundles,symbols=FIVE,signals=signals,
                        reason="verified PG-only; NO financial Entry/Exit permissions",
                        sensor_status={
                            "publication_id":pin.publication_id,
                            "native_pg_delivery_scope":"R06_NO_ORDER_ENTRY_DECISION_ONLY",
                            "financial_ready":False,
                            "observer_market_ws":"DETACHED_BY_R06_MODE",
                        },
                    )
                    last_status=time.monotonic()
                time.sleep(0.15)
    except Exception as exc:
        # Fixed exit status 42 is deliberately excluded from Restart=on-failure
        # by the exact R06 systemd unit. A permanent provenance block must be
        # visible as failure and may never cause an automatic old-path fallback.
        write_status(
            state="BLOCKED",epoch_id=None,active_bundles=(),symbols=FIVE,
            reason="R06_PG_ONLY_BLOCKED:"+type(exc).__name__+":"+str(exc)[:160],
            sensor_status={"native_pg_delivery_scope":"R06_BLOCKED_NO_ORDERS"},
        )
        raise SystemExit(42) from exc

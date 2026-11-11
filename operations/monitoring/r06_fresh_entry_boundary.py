"""R06 future NEW-publication Entry boundary; no historical Entry replay.

This module does NOT by itself switch Observer input or grant Entry/Exit/Exchange
authority. It provides a single fail-closed read-only proof and a separately
guarded, exact-actor atomic bootstrap for the EXISTING Strategy decision
checkpoint. The Observer must fully detach its former public WS/REST Entry
reader, prove approved signed MAIN+SUB FLAT, and activate only its no-order
PG consumer BEFORE calling the writer. No root/background fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_data.entry_publication_reader import (
    EntryPublicationPin,
    EntryPublicationUnavailable,
    read_entry_publication_pin,
    read_entry_publication_seed,
)
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90EntryState,
    causal_entry_state,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceProofUnavailable,
    verify_native_producer_code_compatibility,
)
from operations.monitoring.r06_entry_publication_shadow_probe import (
    EXISTING_P90_COLD_COUNTS,
    FIVE,
    verify_current_entry_plan_scope,
)

_SOURCE = "ms-bybit-mainnet-public-v1"
_IDS = (MAIN_ID, SUB_ID)


class R06FreshCutoverBlocked(RuntimeError):
    """A future new Entry epoch was not independently proved."""


@dataclass(frozen=True, slots=True)
class R06FreshEntryProof:
    pin: EntryPublicationPin
    closed_5m_at: datetime
    first_cursor: tuple[int, int]
    geometry: dict[str, P90L90EntryState]
    exact_lookback: dict[str, dict[str, int]]
    legacy_detached_at: datetime

    @property
    def new_epoch_only(self) -> bool:
        return self.closed_5m_at > self.legacy_detached_at


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise R06FreshCutoverBlocked("R06_BOUNDARY_UTC_REQUIRED")
    return value.astimezone(UTC)


def verify_r06_fresh_publication(
    readonly_connection: Any,
    *,
    legacy_detached_at: datetime,
    observed_at: datetime,
    source_checkout: Any,
    installed_consumer_commit: str,
    max_age: timedelta = timedelta(seconds=5),
) -> R06FreshEntryProof:
    """Causal H9/ATR seed from ONE future publication; no old Entry replay.

    Caller owns a repeatable-read/read-only transaction; old market reader
    MUST already have stopped before legacy_detached_at. Every required
    original provider row and confirmed candle ref is reverified by the
    lower-level R06 physical reader; NOT_VERIFIED history is not faked READY.
    """
    detached_at = _aware_utc(legacy_detached_at)
    now = _aware_utc(observed_at)
    if now <= detached_at or now - detached_at > timedelta(minutes=10):
        raise R06FreshCutoverBlocked("R06_OLD_OWNER_DETACH_FENCE_INVALID")
    try:
        actual_symbols = verify_current_entry_plan_scope(readonly_connection)
        if tuple(actual_symbols) != FIVE:
            raise R06FreshCutoverBlocked("R06_FIVE_SYMBOL_SCOPE_DRIFT")
        sessions = readonly_connection.execute(
            """SELECT source_session_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE market_source_id=%s AND state='ACTIVE'
                ORDER BY subscribed_at DESC LIMIT 2""",
            (_SOURCE,),
        ).fetchall()
        if len(sessions) != 1:
            raise R06FreshCutoverBlocked("R06_PRODUCER_UNIQUE_EPOCH_NOT_PROVEN")
        producer = sessions[0][1]
        accepted = verify_native_producer_code_compatibility(
            source_checkout=source_checkout,
            claimed_producer_commit=producer,
            loaded_consumer_commit=installed_consumer_commit,
        )
        pin = read_entry_publication_pin(
            readonly_connection,
            observed_at=now,
            accepted_producer_commits=accepted,
            max_publication_age=max_age,
            symbols=FIVE,
            intervals=("5", "15"),
        )
        if pin.source_session_id != sessions[0][0]:
            raise R06FreshCutoverBlocked("R06_PUBLICATION_EPOCH_MIXED")
        observed_closes = {
            _aware_utc(datetime.fromisoformat(pin.closed_refs[sym]["5"]))
            for sym in FIVE
        }
        if len(observed_closes) != 1:
            raise R06FreshCutoverBlocked("R06_5M_CLOSED_BOUNDARY_NOT_COMMON")
        closed_at = observed_closes.pop()
        if (
            closed_at <= detached_at
            or closed_at > pin.as_of_second
            or closed_at.second != 0
            or closed_at.minute % 5 != 0
        ):
            raise R06FreshCutoverBlocked("R06_FUTURE_CLOSED_BOUNDARY_NOT_REACHED")
        geometry: dict[str, P90L90EntryState] = {}
        lengths: dict[str, dict[str, int]] = {}
        for symbol in FIVE:
            seed = read_entry_publication_seed(
                readonly_connection, pin=pin, symbol=symbol,
                required_counts=dict(EXISTING_P90_COLD_COUNTS),
            )
            state = causal_entry_state(
                seed["5"], seed["15"], effective_at=closed_at,
            )
            if state is None or state.effective_at != closed_at:
                raise R06FreshCutoverBlocked("R06_ENTRY_H9_ATR_NOT_VERIFIED")
            geometry[symbol] = state
            lengths[symbol] = {tf: len(seed[tf]) for tf in ("5", "15")}
    except (EntryPublicationUnavailable, NativeSourceProofUnavailable) as exc:
        raise R06FreshCutoverBlocked("R06_NEW_PUBLICATION_UNVERIFIED") from exc

    # Global source frame/index is intentionally used for BOTH P90 strategy
    # subsets: this is a single new causal point after all published events,
    # not the unrelated Observer technical transport ACK cursor.
    last = max(
        (ref.get("frame_number"), ref.get("frame_index"))
        for ref in pin.native_watermarks.values()
    )
    if (
        type(last[0]) is not int or last[0] < 0
        or type(last[1]) is not int or last[1] < 0
    ):
        raise R06FreshCutoverBlocked("R06_START_SOURCE_FRAME_INVALID")
    return R06FreshEntryProof(
        pin=pin, closed_5m_at=closed_at, first_cursor=last,
        geometry=geometry, exact_lookback=lengths,
        legacy_detached_at=detached_at,
    )


def _require_no_exposure_or_requests(
    connection: Any, *, strategies: tuple[str, ...] = _IDS,
) -> None:
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    selected = connection.execute(
        """SELECT count(*) FROM strategy_entry.execution_permissions
            WHERE enabled=true AND enabled_at IS NOT NULL"""
    ).fetchone()
    exposure = connection.execute(
        """SELECT count(*) FROM runtime.position_ownership
            WHERE strategy_id=ANY(%s)
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')""",
        (list(strategies),),
    ).fetchone()
    pending = []
    for schema in ("strategy_entry", "strategy_exit"):
        row = connection.execute(
            f"""SELECT count(*) FROM {schema}.execution_requests r
                WHERE r.strategy_id=ANY(%s) AND NOT EXISTS (
                  SELECT 1 FROM {schema}.execution_dispatches d
                   WHERE d.execution_request_id=r.execution_request_id)""",
            (list(strategies),),
        ).fetchone()
        pending.append(row)
    if (
        gate != (False,) or selected != (0,) or exposure != (0,)
        or any(value != (0,) for value in pending)
    ):
        raise R06FreshCutoverBlocked("R06_OPEN_OR_PENDING_OR_ARMED_CANNOT_CUTOVER")


def bootstrap_future_entry_checkpoints(
    connection: Any,
    *,
    proof: R06FreshEntryProof,
    strategies: tuple[tuple[str, str, str, str], ...],
    consumer_release_commit: str,
    active_p90_bundles: tuple[object, ...],
) -> tuple[str, ...]:
    """Atomically INITIALIZE the EXISTING P90 cursor at the future boundary.

    The caller must first stop the legacy Entry public-data owner. A fresh
    signed private preflight is executed, never simulated by a status 0/0.
    This is an explicit operator cutover action, NOT called by imports/tests,
    and cannot be run while the old WS owner is active.
    """
    if (
        not isinstance(proof, R06FreshEntryProof)
        or not proof.new_epoch_only
        or len(strategies) != 2
        or {part[0] for part in strategies} != set(_IDS)
        or any(
            len(part) != 4 or not part[1].strip()
            or len(part[2]) != 64 or len(part[3]) != 64
            for part in strategies
        )
        or len(active_p90_bundles) != 2
        or len(consumer_release_commit) != 40
        or any(c not in "0123456789abcdef" for c in consumer_release_commit)
        or {getattr(getattr(bundle, "card", None), "strategy_id", None)
            for bundle in active_p90_bundles} != set(_IDS)
    ):
        raise R06FreshCutoverBlocked("R06_BOOTSTRAP_CONTRACT_INVALID")

    # The exact existing credential-local MAIN/SUB signed proof is required.
    # No caller-provided bool, no-op lambda, status 0/0, or debug callback is
    # admissible. It checks account routes, generations and private IPC.
    from operations.monitoring.p90l90_real_source_bootstrap import (
        preflight_p90_real_private_flat_source,
    )

    for name, version, entry_fp, exit_fp in strategies:
        matches = [
            bundle for bundle in active_p90_bundles
            if getattr(getattr(bundle, "card", None), "strategy_id", None)
            == name
            and getattr(getattr(bundle, "card", None), "strategy_version", None)
            == version
            and getattr(
                getattr(bundle, "entry_plan", None),
                "entry_plan_fingerprint", None,
            ) == entry_fp
            and getattr(
                getattr(bundle, "exit_plan", None),
                "exit_plan_fingerprint", None,
            ) == exit_fp
        ]
        if len(matches) != 1:
            raise R06FreshCutoverBlocked("R06_SIGNED_P90_PLAN_SCOPE_UNVERIFIED")
    preflight_p90_real_private_flat_source(
        connection, bundles=active_p90_bundles,
    )

    pin = proof.pin
    with connection.transaction():
        # Lock before checking actual arm/exposure/pending + one active epoch.
        source = connection.execute(
            """SELECT state,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s AND market_source_id=%s
                FOR SHARE""",
            (pin.source_session_id, _SOURCE),
        ).fetchone()
        publication = connection.execute(
            """SELECT source_session_id,producer_release_commit,as_of_second,
                      native_watermarks,closed_candle_watermarks
                 FROM analytics.market_publications
                WHERE publication_id=%s AND market_source_id=%s
                FOR SHARE""",
            (pin.publication_id, _SOURCE),
        ).fetchone()
        now_row = connection.execute("SELECT clock_timestamp()").fetchone()
        if (
            source != ("ACTIVE", pin.producer_release_commit)
            or publication is None
            or publication[:3] != (
                pin.source_session_id,pin.producer_release_commit,pin.as_of_second
            )
            or publication[3] != pin.native_watermarks
            or any(
                publication[4].get(s) != pin.closed_refs[s] for s in FIVE
            )
            or now_row is None
            or not isinstance(now_row[0], datetime)
            or not timedelta(0) <= now_row[0]-pin.as_of_second <= timedelta(seconds=5)
        ):
            raise R06FreshCutoverBlocked("R06_START_PUBLICATION_STALE_OR_CHANGED")
        _require_no_exposure_or_requests(connection)

        first_frame, first_index = proof.first_cursor
        inserted: list[str] = []
        for name,version,entry_fp,exit_fp in strategies:
            # No backfill, reset, overwrite or technical ACK substitution.
            row = connection.execute(
                """INSERT INTO strategy_entry.p90_scoped_native_consumer_checkpoints (
                       strategy_id,strategy_version,entry_plan_fingerprint,
                       exit_plan_fingerprint,source_session_id,
                       producer_release_commit,consumer_release_commit,
                       frame_number,frame_index,last_exec_id,
                       bootstrap_publication_id,last_publication_id,
                       bootstrap_source_at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s)
                     ON CONFLICT DO NOTHING RETURNING strategy_id""",
                (name,version,entry_fp,exit_fp,pin.source_session_id,
                 pin.producer_release_commit,consumer_release_commit,
                 first_frame,first_index,pin.publication_id,
                 pin.publication_id,proof.closed_5m_at),
            ).fetchone()
            if row != (name,):
                raise R06FreshCutoverBlocked("R06_DECISION_CURSOR_ALREADY_EXISTS")
            inserted.append(name)
        if len(inserted) != 2:
            raise R06FreshCutoverBlocked("R06_INCOMPLETE_P90_DECISION_BOOTSTRAP")
    return tuple(inserted)

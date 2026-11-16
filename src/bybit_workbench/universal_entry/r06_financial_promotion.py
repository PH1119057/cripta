"""R06 SOURCE_ONLY -> account-scoped Universal Entry financial admission.

R06 owns causal market/Strategy-signal observation only. This module is a
separate Entry-layer promotion stage used by the existing account-scoped
Universal Entry Consumer. While mainnet is disarmed it tails the R06 decision
checkpoint without creating StrategySignal/EntryDecision/EntryExecutionRequest.
Once exact account LIVE-arm/safety is proven, only observations newer than that
durable baseline may enter ordinary Universal Entry admission.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from bybit_workbench.account_entry_readiness import real_entry_account_state
from bybit_workbench.account_entry_safety import account_entry_safety_ready
from bybit_workbench.account_live_arm import account_live_arm_ready
from bybit_workbench.entry_admission import PostgresEntryAdmissionPort
from bybit_workbench.strategy_execution_binding_policy import (
    real_binding_allocation_allowed,
)
from bybit_workbench.strategy_position_binding import exchange_position_key

from .contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyExecutionBindingRef,
    TradeDirection,
)
from .engine import AccountAdmissionContext, UniversalEntryEngine
from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_real_contract_v3 import VERSION
from .p90l90_real_native_candidate_owner import P90L90RealNativeCandidateOwner
from .p90l90_real_second48 import read_real_second48
from .p90l90_strategy_embargo import StrategyHardStopEmbargoPolicy
from .r07_publication_provenance import require_r07_publication_provenance
from .registry import ActivePlanRegistry
from .runtime_loader import load_active_strategy_bundles
from .storage import StrategyEntryStore
from .transport_continuity import TradeCursor

_P90_IDS = frozenset({MAIN_ID, SUB_ID})
_MAX_PROMOTION_AGE = timedelta(seconds=5)


class R06FinancialPromotionBlocked(RuntimeError):
    """Fail-closed R06 -> financial admission handoff."""


def _gate_enabled(connection: Any) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if row is None:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_MAINNET_GATE_MISSING")
    value = row.get("enabled") if isinstance(row, Mapping) else row[0]
    if type(value) is not bool:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_MAINNET_GATE_INVALID")
    return value


def _execution_permission_ready(
    connection: Any,
    *,
    bundle: Any,
    event_at: datetime,
) -> bool:
    rows = connection.execute(
        """SELECT enabled,enabled_at
             FROM strategy_entry.execution_permissions
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s""",
        (
            bundle.card.strategy_id,
            bundle.card.strategy_version,
            bundle.card.strategy_config_fingerprint,
        ),
    ).fetchall()
    if len(rows) != 1:
        return False
    row = rows[0]
    enabled = row.get("enabled") if isinstance(row, Mapping) else row[0]
    enabled_at = row.get("enabled_at") if isinstance(row, Mapping) else row[1]
    return (
        enabled is True
        and isinstance(enabled_at, datetime)
        and enabled_at.tzinfo is not None
        and event_at.astimezone(UTC) >= enabled_at.astimezone(UTC)
    )


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise R06FinancialPromotionBlocked(label + "_MISSING")
    try:
        at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise R06FinancialPromotionBlocked(label + "_INVALID") from exc
    if at.tzinfo is None:
        raise R06FinancialPromotionBlocked(label + "_NAIVE")
    return at.astimezone(UTC)


def encode_candidate_fact(fact: MarketFactEnvelope) -> str:
    """Return JSON accepted by PostgreSQL jsonb for one immutable R06 crossing."""
    from .fingerprint import canonical_json

    if (
        fact.event_kind != "P90L90_RAW_H9_PREV7D_CROSSING"
        or fact.direction is None
        or fact.observed_at != fact.event_at
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CANDIDATE_FACT_INVALID")
    return canonical_json(
        {
            "fact_id": fact.fact_id,
            "event_kind": fact.event_kind,
            "symbol": fact.symbol,
            "observed_at": fact.observed_at.astimezone(UTC).isoformat(),
            "event_at": fact.event_at.astimezone(UTC).isoformat(),
            "received_at": fact.received_at.astimezone(UTC).isoformat(),
            "source_refs": list(fact.source_refs),
            "attributes": fact.attributes.to_dict(),
            "direction": fact.direction.value,
        }
    )


def _restore_candidate_fact(raw: object) -> MarketFactEnvelope:
    if not isinstance(raw, Mapping):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CANDIDATE_PAYLOAD_MISSING")
    required = {
        "fact_id", "event_kind", "symbol", "observed_at", "event_at",
        "received_at", "source_refs", "attributes", "direction",
    }
    if set(raw) != required or raw.get("event_kind") != "P90L90_RAW_H9_PREV7D_CROSSING":
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CANDIDATE_PAYLOAD_SCHEMA")
    refs = raw.get("source_refs")
    attrs = raw.get("attributes")
    direction = raw.get("direction")
    if (
        not isinstance(refs, list)
        or not refs
        or any(not isinstance(item, str) or not item for item in refs)
        or not isinstance(attrs, Mapping)
        or direction not in {"LONG", "SHORT"}
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CANDIDATE_PAYLOAD_INVALID")
    event_at = _utc(raw.get("event_at"), "R06_FINANCIAL_EVENT_AT")
    observed_at = _utc(raw.get("observed_at"), "R06_FINANCIAL_OBSERVED_AT")
    received_at = _utc(raw.get("received_at"), "R06_FINANCIAL_RECEIVED_AT")
    if observed_at != event_at or received_at < event_at:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CANDIDATE_TIME_INVALID")
    return MarketFactEnvelope(
        fact_id=str(raw.get("fact_id") or ""),
        event_kind=str(raw["event_kind"]),
        symbol=str(raw.get("symbol") or ""),
        observed_at=observed_at,
        event_at=event_at,
        received_at=received_at,
        source_refs=tuple(cast(list[str], refs)),
        attributes=FrozenPolicy.from_mapping(dict(attrs)),
        direction=TradeDirection(str(direction)),
    )


def _route_bundle(
    connection: Any,
    *,
    trading_account_id: str,
    now: datetime,
) -> tuple[Any, StrategyExecutionBindingRef] | None:
    bundles = {
        cast(Any, bundle).activation.activation_id: cast(Any, bundle)
        for bundle in load_active_strategy_bundles(connection)
        if (
            cast(Any, bundle).card.strategy_id in _P90_IDS
            and cast(Any, bundle).card.strategy_version == VERSION
        )
    }
    if not bundles:
        return None
    rows = connection.execute(
        """SELECT b.strategy_execution_binding_id,b.strategy_activation_id,
                  b.execution_leg,b.trading_account_id,b.allocation_mode
             FROM strategy_entry.strategy_execution_bindings b
             JOIN strategy_entry.strategy_activations a
               ON a.activation_id=b.strategy_activation_id
            WHERE b.trading_account_id=%s
              AND b.binding_state='ACTIVE'
              AND b.execution_role='PRIMARY'
              AND b.execution_leg='PRIMARY'
              AND b.effective_from<=%s
              AND (b.effective_to IS NULL OR b.effective_to>%s)
              AND a.enabled=true
            ORDER BY b.strategy_execution_binding_id""",
        (trading_account_id, now, now),
    ).fetchall()
    matches: list[tuple[Any, StrategyExecutionBindingRef]] = []
    for row in rows:
        activation_id = str(row[1])
        bundle = bundles.get(activation_id)
        if bundle is None:
            continue
        if not real_binding_allocation_allowed(
            strategy_id=bundle.card.strategy_id,
            strategy_version=bundle.card.strategy_version,
            allocation_mode=str(row[4]),
        ):
            continue
        matches.append(
            (
                bundle,
                StrategyExecutionBindingRef(
                    strategy_execution_binding_id=str(row[0]),
                    strategy_activation_id=activation_id,
                    execution_leg=str(row[2]),
                    trading_account_id=str(row[3]),
                ),
            )
        )
    if len(matches) != 1:
        if not matches:
            return None
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_ACCOUNT_ROUTE_AMBIGUOUS")
    return matches[0]


def _r06_checkpoint(
    connection: Any,
    *,
    bundle: Any,
    loaded_release_commit: str,
    lock: bool,
) -> tuple[str, int, int, int]:
    suffix = " FOR SHARE OF cp" if lock else ""
    rows = connection.execute(
        """SELECT cp.source_session_id,cp.frame_number,cp.frame_index,
                  cp.last_publication_id,cp.entry_plan_fingerprint,
                  cp.exit_plan_fingerprint,cp.consumer_release_commit
             FROM strategy_entry.p90_scoped_native_consumer_checkpoints cp
             JOIN analytics.native_public_trade_source_sessions s
               ON s.source_session_id=cp.source_session_id
            WHERE cp.strategy_id=%s AND cp.strategy_version=%s
              AND s.state='ACTIVE'"""
        + suffix,
        (bundle.card.strategy_id, bundle.card.strategy_version),
    ).fetchall()
    if len(rows) != 1:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CURRENT_SOURCE_CHECKPOINT_MISSING")
    row = rows[0]
    if (
        str(row[4]) != bundle.entry_plan.entry_plan_fingerprint
        or str(row[5]) != bundle.exit_plan.exit_plan_fingerprint
        or str(row[6]) != loaded_release_commit
        or row[3] is None
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_SOURCE_CHECKPOINT_IDENTITY_DRIFT")
    return str(row[0]), int(row[1]), int(row[2]), int(row[3])


def sync_disarmed_baseline(
    connection: Any,
    *,
    trading_account_id: str,
    account_connector_id: str,
    loaded_release_commit: str,
    now: datetime,
) -> str:
    """Tail R06 while mainnet is closed; never create financial Entry facts."""
    del account_connector_id
    if _gate_enabled(connection):
        return "GATE_ARMED_NO_BASELINE_CHANGE"
    route = _route_bundle(connection, trading_account_id=trading_account_id, now=now)
    if route is None:
        return "NOT_APPLICABLE"
    bundle, binding = route
    session, frame, index, publication = _r06_checkpoint(
        connection,
        bundle=bundle,
        loaded_release_commit=loaded_release_commit,
        lock=True,
    )
    connection.execute(
        """INSERT INTO strategy_entry.p90_r06_financial_promotion_checkpoints(
               trading_account_id,strategy_id,strategy_version,
               strategy_activation_id,strategy_execution_binding_id,
               source_session_id,frame_number,frame_index,last_publication_id,
               consumer_release_commit,state,last_signal_id,last_outcome,updated_at
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    'DISARMED_BASELINE',NULL,'DISARMED_TAIL',%s)
           ON CONFLICT(trading_account_id,strategy_id,strategy_version)
           DO UPDATE SET
               strategy_activation_id=EXCLUDED.strategy_activation_id,
               strategy_execution_binding_id=EXCLUDED.strategy_execution_binding_id,
               source_session_id=EXCLUDED.source_session_id,
               frame_number=EXCLUDED.frame_number,
               frame_index=EXCLUDED.frame_index,
               last_publication_id=EXCLUDED.last_publication_id,
               consumer_release_commit=EXCLUDED.consumer_release_commit,
               state='DISARMED_BASELINE',
               last_signal_id=NULL,
               last_outcome='DISARMED_TAIL',
               updated_at=EXCLUDED.updated_at""",
        (
            trading_account_id,
            bundle.card.strategy_id,
            bundle.card.strategy_version,
            bundle.activation.activation_id,
            binding.strategy_execution_binding_id,
            session,
            frame,
            index,
            publication,
            loaded_release_commit,
            now.astimezone(UTC),
        ),
    )
    return "BASELINE_SYNCED"


def require_disarmed_baseline(
    connection: Any,
    *,
    trading_account_id: str,
    loaded_release_commit: str,
    now: datetime,
) -> dict[str, object]:
    route = _route_bundle(connection, trading_account_id=trading_account_id, now=now)
    if route is None:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_ACCOUNT_ROUTE_MISSING")
    bundle, binding = route
    source = _r06_checkpoint(
        connection,
        bundle=bundle,
        loaded_release_commit=loaded_release_commit,
        lock=False,
    )
    row = connection.execute(
        """SELECT strategy_activation_id,strategy_execution_binding_id,
                  source_session_id,frame_number,frame_index,last_publication_id,
                  consumer_release_commit,state,updated_at
             FROM strategy_entry.p90_r06_financial_promotion_checkpoints
            WHERE trading_account_id=%s AND strategy_id=%s AND strategy_version=%s""",
        (trading_account_id, bundle.card.strategy_id, bundle.card.strategy_version),
    ).fetchone()
    if row is None:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_DISARMED_BASELINE_MISSING")
    expected = (
        bundle.activation.activation_id,
        binding.strategy_execution_binding_id,
        source[0],
        source[1],
        source[2],
        source[3],
        loaded_release_commit,
        "DISARMED_BASELINE",
    )
    actual = (
        str(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[4]),
        int(row[5]), str(row[6]), str(row[7]),
    )
    normalized_expected = (
        str(expected[0]), str(expected[1]), str(expected[2]), int(expected[3]),
        int(expected[4]), int(expected[5]), str(expected[6]), str(expected[7]),
    )
    if actual != normalized_expected:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_DISARMED_BASELINE_NOT_CURRENT")
    updated = row[8]
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_BASELINE_TIME_INVALID")
    return {
        "strategy_id": bundle.card.strategy_id,
        "trading_account_id": trading_account_id,
        "strategy_execution_binding_id": binding.strategy_execution_binding_id,
        "source_session_id": source[0],
        "frame_number": source[1],
        "frame_index": source[2],
        "last_publication_id": source[3],
        "updated_at": updated.astimezone(UTC).isoformat(),
    }


def _advance_cursor(
    connection: Any,
    *,
    trading_account_id: str,
    strategy_id: str,
    strategy_version: str,
    source_session_id: str,
    frame_number: int,
    frame_index: int,
    publication_id: int,
    signal_id: str | None,
    outcome: str,
    now: datetime,
) -> None:
    result = connection.execute(
        """UPDATE strategy_entry.p90_r06_financial_promotion_checkpoints
              SET source_session_id=%s,frame_number=%s,frame_index=%s,
                  last_publication_id=%s,state='ARMED_PROGRESS',
                  last_signal_id=%s,last_outcome=%s,updated_at=%s
            WHERE trading_account_id=%s AND strategy_id=%s AND strategy_version=%s""",
        (
            source_session_id, frame_number, frame_index, publication_id,
            signal_id, outcome, now.astimezone(UTC), trading_account_id,
            strategy_id, strategy_version,
        ),
    )
    if getattr(result, "rowcount", 0) != 1:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CURSOR_CAS_FAILED")


def promote_next(
    connection: Any,
    *,
    trading_account_id: str,
    account_connector_id: str,
    loaded_release_commit: str,
    now: datetime,
) -> str:
    """Promote at most one future R06 observation into ordinary REAL admission."""
    if not _gate_enabled(connection):
        return "EXECUTION_GATE_DISARMED"
    route = _route_bundle(connection, trading_account_id=trading_account_id, now=now)
    if route is None:
        return "NOT_APPLICABLE"
    bundle, binding = route
    source = _r06_checkpoint(
        connection, bundle=bundle,
        loaded_release_commit=loaded_release_commit, lock=True,
    )
    cursor = connection.execute(
        """SELECT strategy_activation_id,strategy_execution_binding_id,
                  source_session_id,frame_number,frame_index,last_publication_id,
                  consumer_release_commit,state
             FROM strategy_entry.p90_r06_financial_promotion_checkpoints
            WHERE trading_account_id=%s AND strategy_id=%s AND strategy_version=%s
            FOR UPDATE""",
        (trading_account_id, bundle.card.strategy_id, bundle.card.strategy_version),
    ).fetchone()
    if cursor is None:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_PROMOTION_BASELINE_MISSING")
    if (
        str(cursor[0]) != bundle.activation.activation_id
        or str(cursor[1]) != binding.strategy_execution_binding_id
        or str(cursor[2]) != source[0]
        or str(cursor[6]) != loaded_release_commit
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_PROMOTION_SCOPE_DRIFT")
    current_frame, current_index = int(cursor[3]), int(cursor[4])
    rows = connection.execute(
        """SELECT publication_id,source_session_id,frame_number,frame_index,
                  native_exec_id,signal_id,symbol,direction,event_at,candidate_fact
             FROM strategy_entry.p90_r06_publication_observations
            WHERE strategy_id=%s AND strategy_version=%s
              AND source_session_id=%s
              AND (frame_number>%s OR (frame_number=%s AND frame_index>%s))
              AND (frame_number<%s OR (frame_number=%s AND frame_index<=%s))
            ORDER BY frame_number,frame_index,direction,signal_id
            LIMIT 2""",
        (
            bundle.card.strategy_id, bundle.card.strategy_version, source[0],
            current_frame, current_frame, current_index,
            source[1], source[1], source[2],
        ),
    ).fetchall()
    if not rows:
        return "NO_R06_FINANCIAL_SIGNAL"
    first = rows[0]
    if (
        len(rows) > 1
        and int(rows[1][2]) == int(first[2])
        and int(rows[1][3]) == int(first[3])
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_MULTI_SIGNAL_SAME_NATIVE_EVENT")
    event_at = first[8]
    if not isinstance(event_at, datetime) or event_at.tzinfo is None:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_OBSERVATION_TIME_INVALID")
    age = now.astimezone(UTC) - event_at.astimezone(UTC)
    if age < timedelta(0):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_OBSERVATION_FROM_FUTURE")
    if age > _MAX_PROMOTION_AGE:
        _advance_cursor(
            connection, trading_account_id=trading_account_id,
            strategy_id=bundle.card.strategy_id,
            strategy_version=bundle.card.strategy_version,
            source_session_id=source[0], frame_number=source[1],
            frame_index=source[2], publication_id=source[3],
            signal_id=None, outcome="STALE_BACKLOG_SKIPPED", now=now,
        )
        return "STALE_BACKLOG_SKIPPED"
    if not _execution_permission_ready(connection, bundle=bundle, event_at=event_at):
        return "EXECUTION_PERMISSION_NOT_READY"

    arm_ready, arm_reasons = account_live_arm_ready(
        connection,
        strategy_activation_id=bundle.activation.activation_id,
        strategy_execution_binding_id=binding.strategy_execution_binding_id,
        trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
        strategy_id=bundle.card.strategy_id,
        strategy_version=bundle.card.strategy_version,
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        symbol=str(first[6]),
        release_commit=loaded_release_commit,
        now=now,
    )
    if not arm_ready:
        return "ARM_NOT_READY:" + ",".join(arm_reasons)
    safety_ready, safety_reasons = account_entry_safety_ready(
        connection,
        trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
    )
    if not safety_ready:
        return "ACCOUNT_SAFETY_NOT_READY:" + ",".join(safety_reasons)

    fact = _restore_candidate_fact(first[9])
    if (
        fact.symbol != str(first[6])
        or fact.direction is None
        or fact.direction.value != str(first[7])
        or fact.event_at != event_at.astimezone(UTC)
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_CANDIDATE_OBSERVATION_MISMATCH")
    original = connection.execute(
        """SELECT exec_id,exchange_sequence,event_at,external_symbol
             FROM analytics.native_public_trade_source_events
            WHERE source_session_id=%s AND frame_number=%s AND frame_index=%s""",
        (source[0], int(first[2]), int(first[3])),
    ).fetchone()
    if (
        original is None
        or str(original[0]) != str(first[4])
        or str(original[3]) != fact.symbol
        or not isinstance(original[2], datetime)
        or original[2].astimezone(UTC) != fact.event_at
    ):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_NATIVE_PROVENANCE_MISMATCH")
    cursor_obj = TradeCursor(
        fact.symbol, str(original[0]), int(original[1]), fact.event_at,
    )

    require_r07_publication_provenance(
        connection,
        {
            "strategy_id": bundle.card.strategy_id,
            "strategy_version": bundle.card.strategy_version,
            "signal_id": str(first[5]),
            "symbol": fact.symbol,
            "direction": fact.direction.value,
            "requested_at": fact.event_at,
            "entry_plan_fingerprint": bundle.entry_plan.entry_plan_fingerprint,
            "exit_plan_fingerprint": bundle.exit_plan.exit_plan_fingerprint,
        },
        lock_checkpoint_for_dispatch=True,
    )

    readiness, capacity = real_entry_account_state(
        connection, symbol=fact.symbol, observed_at=now,
        trading_account_id=trading_account_id,
    )
    admission = AccountAdmissionContext(
        binding=binding,
        capacity=capacity,
        technical_readiness=readiness,
        account_ref="BYBIT:UNIFIED",
        exchange_position_key=exchange_position_key(trading_account_id, fact.symbol, 0),
        entry_admission_port=(
            PostgresEntryAdmissionPort(connection)
            if readiness.ready and capacity is not None else None
        ),
    )

    lifecycle = bundle.card.lifecycle_policy.to_dict()
    allows_entry, _snapshot = read_real_second48(
        connection,
        strategy_id=bundle.card.strategy_id,
        strategy_version=bundle.card.strategy_version,
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        trading_account_id=trading_account_id,
        policy=StrategyHardStopEmbargoPolicy.parse(
            lifecycle["second_hardstop_embargo"]
        ),
        evaluated_at=now,
    )
    if not allows_entry:
        _advance_cursor(
            connection, trading_account_id=trading_account_id,
            strategy_id=bundle.card.strategy_id,
            strategy_version=bundle.card.strategy_version,
            source_session_id=source[0], frame_number=int(first[2]),
            frame_index=int(first[3]), publication_id=int(first[0]),
            signal_id=str(first[5]), outcome="SECOND48_BLOCKED", now=now,
        )
        return "SECOND48_BLOCKED"

    registry = ActivePlanRegistry()
    registry.register_card(bundle.card)
    if registry.activate(bundle.activation) != bundle.entry_plan:
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_PLAN_REBUILD_DRIFT")
    owner = P90L90RealNativeCandidateOwner(
        strategy_id=bundle.card.strategy_id,
        strategy_activation_id=bundle.activation.activation_id,
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
        engine=UniversalEntryEngine(registry),
        store=StrategyEntryStore(connection),
        provenance=FrozenPolicy.from_mapping(
            {
                "source_commit": loaded_release_commit,
                "data_lineage_id": (
                    f"R06:{source[0]}:{int(first[0])}:{int(first[2])}:{int(first[3])}"
                ),
                "financial_owner": "UNIVERSAL_ENTRY_ACCOUNT_SCOPED_PROMOTION",
            }
        ),
    )
    evaluations = owner.accept(
        fact, cursor_obj, account_admissions=(admission,),
        arm_and_source_verified=True, admitted_at=now, allow_new_signals=True,
    )
    if len(evaluations) != 1 or evaluations[0].signal.signal_id != str(first[5]):
        raise R06FinancialPromotionBlocked("R06_FINANCIAL_SIGNAL_REPLAY_MISMATCH")
    outcome = evaluations[0].account_outcomes[0]
    decision_code = outcome.decision.code.value
    _advance_cursor(
        connection, trading_account_id=trading_account_id,
        strategy_id=bundle.card.strategy_id,
        strategy_version=bundle.card.strategy_version,
        source_session_id=source[0], frame_number=int(first[2]),
        frame_index=int(first[3]), publication_id=int(first[0]),
        signal_id=str(first[5]), outcome=decision_code, now=now,
    )
    return (
        "PROMOTED_REQUEST:" + outcome.execution_request.execution_request_id
        if outcome.execution_request is not None
        else "PROMOTED_DECISION:" + decision_code
    )

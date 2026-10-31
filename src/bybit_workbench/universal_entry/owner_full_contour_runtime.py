"""Universal Entry owner for full-contour diagnostic triggers.

This is intentionally invoked from the account-scoped Universal Entry consumer,
not Dashboard and not the market Observer.  It converts one durable authenticated
owner trigger into the normal StrategySignal -> attempt -> real admission ->
EntryExecutionRequest lineage using UniversalEntryEngine.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bybit_workbench.account_entry_safety import account_entry_safety_ready
from bybit_workbench.account_live_arm import account_live_arm_ready
from bybit_workbench.account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)
from bybit_workbench.entry_admission import PostgresEntryAdmissionPort
from bybit_workbench.live_arm_readiness import LiveArmContext, active_live_arm_session
from bybit_workbench.strategy_position_binding import exchange_position_key

from .contracts import (
    DataQuality,
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyExecutionBindingRef,
    TechnicalReadiness,
    TradeDirection,
    TradingCapacitySnapshot,
)
from .engine import UniversalEntryEngine
from .owner_full_contour_control import (
    ACTIVE_STATES,
    retire_entry_rights,
    set_run_state,
)
from .owner_full_contour_diagnostic import (
    RUNTIME_SOURCE,
    TRIGGER_EVENT_KIND,
    VERSION,
    is_diagnostic_strategy,
)
from .registry import ActivePlanRegistry
from .runtime_loader import load_strategy_bundle_exact_identity
from .storage import StrategyEntryStore


def _v(row: object, key: str, index: int) -> object:
    return row[key] if isinstance(row, Mapping) else row[index]  # type: ignore[index]


def _fail(
    connection: Any,
    *,
    run_id: str,
    now: datetime,
    reason: str,
) -> str:
    retire_entry_rights(
        connection,
        run_id=run_id,
        at=now,
        reason="diagnostic materialization failed: " + reason,
    )
    set_run_state(
        connection,
        run_id=run_id,
        state="FAILED",
        now=now,
        error=reason,
    )
    return "OWNER_DIAGNOSTIC_FAILED:" + reason


def _current_reference_price(
    connection: Any,
    *,
    symbol: str,
    release_commit: str,
    now: datetime,
) -> tuple[Decimal, str]:
    row = connection.execute(
        """SELECT e.epoch_id,c.updated_at,
                  c.entry_checkpoint_json->>'previous_price'
             FROM strategy_entry.p90l90_real_source_epochs e
             JOIN strategy_entry.p90l90_real_causal_checkpoints c
               ON c.epoch_id=e.epoch_id
            WHERE e.status='ACTIVE'
              AND e.source_commit=%s
              AND c.symbol=%s
            ORDER BY e.opened_at DESC,c.updated_at DESC
            LIMIT 1""",
        (release_commit, symbol),
    ).fetchone()
    if row is None:
        raise RuntimeError("OWNER_FULL_CONTOUR_P90_PRICE_CHECKPOINT_MISSING")
    updated = _v(row, "updated_at", 1)
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        raise RuntimeError("OWNER_FULL_CONTOUR_P90_PRICE_CHECKPOINT_TIME_INVALID")
    age = (now.astimezone(UTC) - updated.astimezone(UTC)).total_seconds()
    if age < 0 or age > 15:
        raise RuntimeError("OWNER_FULL_CONTOUR_P90_PRICE_CHECKPOINT_STALE")
    try:
        price = Decimal(str(_v(row, "previous_price", 2)))
    except Exception as exc:
        raise RuntimeError("OWNER_FULL_CONTOUR_P90_PRICE_INVALID") from exc
    if not price.is_finite() or price <= 0:
        raise RuntimeError("OWNER_FULL_CONTOUR_P90_PRICE_INVALID")
    return price, str(_v(row, "epoch_id", 0))


def _load_bundle(connection: Any, run: Mapping[str, object]) -> object:
    rows = connection.execute(
        """SELECT ep.entry_plan_fingerprint,xp.exit_plan_fingerprint
             FROM strategy_entry.entry_plans ep
             JOIN strategy_entry.exit_plans xp
               ON xp.strategy_id=ep.strategy_id
              AND xp.strategy_version=ep.strategy_version
              AND xp.strategy_config_fingerprint=ep.strategy_config_fingerprint
            WHERE ep.strategy_id=%s AND ep.strategy_version=%s
              AND ep.strategy_config_fingerprint=%s""",
        (
            run["strategy_id"],
            run["strategy_version"],
            run["strategy_config_fingerprint"],
        ),
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError("OWNER_FULL_CONTOUR_EXACT_PLAN_PAIR_MISSING")
    bundle = load_strategy_bundle_exact_identity(
        connection,
        activation_id=str(run["strategy_activation_id"]),
        strategy_id=str(run["strategy_id"]),
        strategy_version=str(run["strategy_version"]),
        strategy_config_fingerprint=str(run["strategy_config_fingerprint"]),
        entry_plan_fingerprint=str(_v(rows[0], "entry_plan_fingerprint", 0)),
        exit_plan_fingerprint=str(_v(rows[0], "exit_plan_fingerprint", 1)),
    )
    if bundle is None or not bundle.activation.enabled:
        raise RuntimeError("OWNER_FULL_CONTOUR_EXACT_BUNDLE_NOT_ACTIVE")
    if not is_diagnostic_strategy(bundle.card.strategy_id, bundle.card.strategy_version):
        raise RuntimeError("OWNER_FULL_CONTOUR_STRATEGY_IDENTITY_INVALID")
    return bundle


def _account_state(
    connection: Any,
    *,
    trading_account_id: str,
    account_connector_id: str,
    symbol: str,
    observed_at: datetime,
) -> tuple[TechnicalReadiness, TradingCapacitySnapshot]:
    safety_ready, safety_reasons = account_entry_safety_ready(
        connection,
        trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
    )
    if not safety_ready:
        raise RuntimeError(
            "OWNER_FULL_CONTOUR_ACCOUNT_SAFETY:" + ",".join(safety_reasons)
        )
    critical = connection.execute(
        """SELECT fault_code FROM runtime.lifecycle_faults
            WHERE state='OPEN' AND severity='CRITICAL'
              AND (NULLIF(exact_ids->>'trading_account_id','') IS NULL
                   OR exact_ids->>'trading_account_id'=%s)
            ORDER BY detected_at,fault_id LIMIT 1""",
        (trading_account_id,),
    ).fetchone()
    if critical is not None:
        raise RuntimeError(
            "OWNER_FULL_CONTOUR_CRITICAL_LIFECYCLE_FAULT:"
            + str(_v(critical, "fault_code", 0))
        )
    try:
        generation = current_complete_account_state_generation(
            connection,
            account_ref="BYBIT:UNIFIED",
            trading_account_id=trading_account_id,
            acquire_account_lock=True,
        )
    except AccountStateGenerationUnavailable as exc:
        raise RuntimeError("OWNER_FULL_CONTOUR_ACCOUNT_STATE:" + str(exc)) from exc
    if symbol not in generation.position_mode_refs:
        raise RuntimeError("OWNER_FULL_CONTOUR_POSITION_MODE_PROOF_MISSING")
    capacity = TradingCapacitySnapshot(
        capacity_snapshot_id=generation.generation_id,
        observed_at=generation.completed_at,
        available_for_new_trading=generation.available_balance,
        quality=DataQuality.HIGH,
        source_ref=f"runtime.account_state_generations:{generation.generation_id}",
    )
    return (
        TechnicalReadiness(
            True,
            observed_at,
            f"owner diagnostic account generation {generation.generation_id}",
        ),
        capacity,
    )


def materialize_requested_run(
    connection: Any,
    *,
    trading_account_id: str,
    account_connector_id: str,
    release_commit: str,
    now: datetime,
) -> str | None:
    """Consume at most one REQUESTED run for this exact account."""
    current = now.astimezone(UTC)
    row = connection.execute(
        """SELECT *
             FROM control.owner_full_contour_diagnostic_runs
            WHERE state='REQUESTED'
              AND trading_account_id=%s
              AND account_connector_id=%s
              AND release_commit=%s
            ORDER BY requested_at,run_id
            FOR UPDATE SKIP LOCKED
            LIMIT 1""",
        (trading_account_id, account_connector_id, release_commit),
    ).fetchone()
    if row is None:
        return None
    run = dict(row) if isinstance(row, Mapping) else None
    if run is None:
        raise RuntimeError("OWNER_FULL_CONTOUR_RUN_ROW_MAPPING_REQUIRED")
    run_id = str(run["run_id"])
    try:
        if connection.execute(
            "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone() != {"enabled": True}:
            # dict_row equality is exact; fall back below for sequence drivers.
            gate = connection.execute(
                "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
            ).fetchone()
            if gate is None or not bool(_v(gate, "enabled", 0)):
                return _fail(
                    connection, run_id=run_id, now=current,
                    reason="OWNER_FULL_CONTOUR_GATE_NOT_OPEN",
                )

        bundle: Any = _load_bundle(connection, run)
        symbol = str(run["symbol"])
        if symbol not in bundle.card.symbols:
            return _fail(
                connection, run_id=run_id, now=current,
                reason="OWNER_FULL_CONTOUR_SYMBOL_NOT_IN_DIAGNOSTIC_CARD",
            )

        permission = connection.execute(
            """SELECT enabled FROM strategy_entry.execution_permissions
                WHERE strategy_id=%s AND strategy_version=%s
                  AND strategy_config_fingerprint=%s""",
            (
                bundle.card.strategy_id,
                bundle.card.strategy_version,
                bundle.card.strategy_config_fingerprint,
            ),
        ).fetchone()
        if permission is None or not bool(_v(permission, "enabled", 0)):
            return _fail(
                connection, run_id=run_id, now=current,
                reason="OWNER_FULL_CONTOUR_PERMISSION_NOT_ACTIVE",
            )

        context = LiveArmContext(
            strategy_id=bundle.card.strategy_id,
            strategy_version=bundle.card.strategy_version,
            strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
            strategy_activation_id=bundle.activation.activation_id,
            symbol=symbol,
            release_commit=release_commit,
        )
        if active_live_arm_session(connection, context=context) is None:
            return _fail(
                connection, run_id=run_id, now=current,
                reason="OWNER_FULL_CONTOUR_GENERIC_ARM_NOT_ACTIVE",
            )
        account_ready, reasons = account_live_arm_ready(
            connection,
            strategy_activation_id=bundle.activation.activation_id,
            strategy_execution_binding_id=str(run["strategy_execution_binding_id"]),
            trading_account_id=trading_account_id,
            account_connector_id=account_connector_id,
            strategy_id=bundle.card.strategy_id,
            strategy_version=bundle.card.strategy_version,
            strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
            symbol=symbol,
            release_commit=release_commit,
            now=current,
        )
        if not account_ready:
            return _fail(
                connection, run_id=run_id, now=current,
                reason="OWNER_FULL_CONTOUR_ACCOUNT_ARM:" + ",".join(reasons),
            )

        readiness, capacity = _account_state(
            connection,
            trading_account_id=trading_account_id,
            account_connector_id=account_connector_id,
            symbol=symbol,
            observed_at=current,
        )
        price, epoch_id = _current_reference_price(
            connection,
            symbol=symbol,
            release_commit=release_commit,
            now=current,
        )
        binding = StrategyExecutionBindingRef(
            strategy_execution_binding_id=str(run["strategy_execution_binding_id"]),
            strategy_activation_id=bundle.activation.activation_id,
            execution_leg="PRIMARY",
            trading_account_id=trading_account_id,
        )
        registry = ActivePlanRegistry()
        registry.register_card(bundle.card)
        registry.activate(bundle.activation)
        engine = UniversalEntryEngine(registry)
        direction = TradeDirection(str(run["direction"]))
        fact = MarketFactEnvelope(
            fact_id="owner-full-contour-fact-" + run_id.removeprefix("owner-fc-"),
            event_kind=TRIGGER_EVENT_KIND,
            symbol=symbol,
            observed_at=current,
            event_at=current,
            received_at=current,
            source_refs=(
                f"owner-full-contour-run:{run_id}",
                f"p90-real-source-epoch:{epoch_id}",
            ),
            attributes=FrozenPolicy.from_mapping(
                {
                    "price": str(price),
                    "diagnostic_run_id": run_id,
                    "diagnostic_source": RUNTIME_SOURCE,
                }
            ),
            direction=direction,
        )
        evaluations = engine.process(
            fact,
            capacity=capacity,
            technical_readiness=readiness,
            account_ref="BYBIT:UNIFIED",
            entry_admission_port=PostgresEntryAdmissionPort(connection),
            real_admission_required_for=frozenset({bundle.activation.activation_id}),
            real_admission_ready_for=frozenset({bundle.activation.activation_id}),
            execution_bindings={bundle.activation.activation_id: binding},
            exchange_position_keys={
                symbol: exchange_position_key(trading_account_id, symbol, 0)
            },
            allow_new_signals=True,
            admission_time=current,
        )
        if len(evaluations) != 1:
            return _fail(
                connection, run_id=run_id, now=current,
                reason="OWNER_FULL_CONTOUR_EXACT_ONE_EVALUATION_REQUIRED",
            )
        evaluation = evaluations[0]
        store = StrategyEntryStore(connection)
        store.record_evaluation(
            evaluation,
            bundle.entry_plan,
            source_ref=f"owner-full-contour-run:{run_id}",
        )
        decision = evaluation.decision
        request = evaluation.execution_request
        if decision is None or request is None:
            reason = (
                "OWNER_FULL_CONTOUR_ENTRY_NOT_ACCEPTED:"
                + ("NO_DECISION" if decision is None else decision.decision_code.value)
            )
            return _fail(connection, run_id=run_id, now=current, reason=reason)
        set_run_state(
            connection,
            run_id=run_id,
            state="MATERIALIZED",
            now=current,
            lineage={
                "signal_id": evaluation.signal.signal_id,
                "strategy_attempt_id": evaluation.attempt.strategy_attempt_id,
                "entry_decision_id": decision.entry_decision_id,
                "entry_execution_request_id": request.execution_request_id,
            },
        )
        return "OWNER_DIAGNOSTIC_MATERIALIZED:" + request.execution_request_id
    except (RuntimeError, ValueError, KeyError) as exc:
        return _fail(
            connection,
            run_id=run_id,
            now=current,
            reason=f"{type(exc).__name__}:{exc}",
        )


def refresh_run_lifecycle(
    connection: Any,
    *,
    trading_account_id: str,
    now: datetime,
) -> str | None:
    """Advance diagnostic run status from durable normal lifecycle evidence."""
    row = connection.execute(
        """SELECT run_id,state,entry_command_id,entry_execution_request_id,
                  strategy_position_id
             FROM control.owner_full_contour_diagnostic_runs
            WHERE trading_account_id=%s AND state=ANY(%s)
            ORDER BY requested_at DESC LIMIT 1""",
        (trading_account_id, list(ACTIVE_STATES)),
    ).fetchone()
    if row is None:
        return None
    run_id = str(_v(row, "run_id", 0))
    state = str(_v(row, "state", 1))
    request_id = _v(row, "entry_execution_request_id", 3)

    if request_id:
        position = connection.execute(
            """SELECT position_id,state
                 FROM runtime.position_ownership
                WHERE entry_execution_request_id=%s
                  AND trading_account_id=%s
                  AND identity_status='PROVEN'
                ORDER BY created_at DESC LIMIT 1""",
            (str(request_id), trading_account_id),
        ).fetchone()
        if position is not None:
            position_id = str(_v(position, "position_id", 0))
            position_state = str(_v(position, "state", 1))
            if position_state == "OPEN" and state != "POSITION_OPEN":
                set_run_state(
                    connection, run_id=run_id, state="POSITION_OPEN", now=now,
                    lineage={"strategy_position_id": position_id},
                )
                return "OWNER_DIAGNOSTIC_POSITION_OPEN:" + position_id
            if position_state == "RECONCILIATION_REQUIRED":
                set_run_state(
                    connection, run_id=run_id,
                    state="RECONCILIATION_REQUIRED", now=now,
                    lineage={"strategy_position_id": position_id},
                    error="POSITION_RECONCILIATION_REQUIRED",
                )
                return "OWNER_DIAGNOSTIC_RECONCILIATION_REQUIRED"
            if position_state == "CLOSED":
                set_run_state(
                    connection, run_id=run_id, state="CLOSED", now=now,
                    lineage={"strategy_position_id": position_id},
                )
                return "OWNER_DIAGNOSTIC_CLOSED:" + position_id

    command_id = _v(row, "entry_command_id", 2)
    if command_id:
        command = connection.execute(
            """SELECT state,error FROM runtime.trade_commands
                WHERE command_id=%s AND trading_account_id=%s""",
            (str(command_id), trading_account_id),
        ).fetchone()
        if command is not None and str(_v(command, "state", 0)) == "failed":
            set_run_state(
                connection,
                run_id=run_id,
                state="FAILED",
                now=now,
                error=str(_v(command, "error", 1) or "ENTRY_COMMAND_FAILED"),
            )
            return "OWNER_DIAGNOSTIC_FAILED:ENTRY_COMMAND"
    return None

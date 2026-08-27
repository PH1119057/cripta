from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.intents import EnterIntent
from bybit_workbench.domain.types import AppMode, AppState, OrderRole, OrderType, PositionSide
from bybit_workbench.risk import RiskContext, RiskDecision, RiskEngine, RiskProfile
from bybit_workbench.ui.view_model import ProtectionView, WorkbenchViewModel


@dataclass(frozen=True, slots=True)
class ManualTradeDraft:
    symbol: str
    direction: PositionSide
    entry_price: Decimal
    stop_price: Decimal
    take_profit: Decimal | None
    leverage: Decimal
    requested_notional: Decimal | None = None
    order_type: OrderType = OrderType.LIMIT
    reason: str = "manual protected test signal"

    def __post_init__(self) -> None:
        if self.direction is PositionSide.FLAT:
            raise ValueError("manual trade direction cannot be flat")


@dataclass(frozen=True, slots=True)
class PreparedManualTrade:
    run_id: str
    decision_id: str
    risk_decision_id: str
    intent: EnterIntent
    decision: RiskDecision
    risk_profile: RiskProfile
    checked_at: datetime
    equity_at_check: Decimal | None = None


class ManualTradeWorkflow:
    """Pure orchestration for Check → Arm → Run; contains no exchange calls."""

    def __init__(
        self,
        state_machine: AppStateMachine,
        view_model: WorkbenchViewModel,
        *,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self.state_machine = state_machine
        self.view_model = view_model
        self.risk_engine = risk_engine or RiskEngine()
        self.prepared: PreparedManualTrade | None = None

    def check(
        self,
        draft: ManualTradeDraft,
        *,
        evaluated_at: datetime | None = None,
        profile: RiskProfile | None = None,
    ) -> PreparedManualTrade:
        state = self.view_model.state
        now = evaluated_at or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("evaluation timestamp must be timezone-aware")
        if self.state_machine.state not in {AppState.READY, AppState.PAUSED}:
            raise PermissionError("manual trade check requires READY or PAUSED state")
        if state.instrument is None:
            raise RuntimeError("instrument rules are not synchronized")
        if state.equity is None or state.available_balance is None:
            raise RuntimeError("account balance is not synchronized")
        intent = EnterIntent(
            intent_id=f"manual-{uuid.uuid4().hex[:20]}",
            symbol=draft.symbol.strip().upper(),
            direction=draft.direction,
            order_type=draft.order_type,
            entry_price=draft.entry_price,
            stop_price=draft.stop_price,
            leverage=draft.leverage,
            reason=draft.reason,
            take_profit=draft.take_profit,
            requested_notional=draft.requested_notional,
        )
        market_at, private_at, context_now = self._timestamps(now)
        current_side = PositionSide(state.position_side)
        context = RiskContext(
            equity=state.equity,
            available_balance=state.available_balance,
            daily_realized_pnl=state.daily_realized_pnl or Decimal("0"),
            consecutive_losses=0,
            open_positions=0 if current_side is PositionSide.FLAT else 1,
            pending_entries=sum(
                order.request.role is OrderRole.ENTRY
                and not order.request.reduce_only
                and order.status.value in {"Accepted", "PartiallyFilled"}
                for order in state.orders
            ),
            market_data_at=market_at,
            private_stream_at=private_at,
            evaluated_at=context_now,
            current_position_side=current_side,
            position_is_protected=state.protection.confirmed_stop is not None,
            estimated_liquidation_price=state.liquidation_price,
        )
        selected_profile = profile or default_manual_risk_profile(intent.symbol)
        fee_candidates = tuple(
            value
            for value in (state.maker_fee_rate, state.taker_fee_rate)
            if value is not None
        )
        if fee_candidates:
            selected_profile = replace(
                selected_profile,
                estimated_fee_rate=max(fee_candidates),
            )
        decision = self.risk_engine.evaluate_entry(
            intent,
            selected_profile,
            context,
            state.instrument,
        )
        prepared = PreparedManualTrade(
            run_id=f"manual-run-{uuid.uuid4().hex}",
            decision_id=f"manual-decision-{uuid.uuid4().hex}",
            risk_decision_id=f"manual-risk-{uuid.uuid4().hex}",
            intent=intent,
            decision=decision,
            risk_profile=selected_profile,
            equity_at_check=state.equity,
            checked_at=context_now,
        )
        self.prepared = prepared
        self.view_model.apply_risk_decision(decision)
        self.view_model.set_protection(
            ProtectionView(
                planned_stop=decision.normalized_stop or intent.stop_price,
                planned_take_profit=intent.take_profit,
            )
        )
        self.view_model.append_strategy_decision(
            f"{context_now.isoformat()} manual check: "
            f"{'approved' if decision.approved else 'rejected'} "
            f"{intent.direction.value} {intent.symbol}"
        )
        return prepared

    def arm(self) -> PreparedManualTrade:
        prepared = self._required_prepared()
        if not prepared.decision.approved or prepared.decision.normalized_order is None:
            raise PermissionError("rejected manual trade cannot be armed")
        if self.state_machine.state is AppState.PAUSED:
            self.state_machine.transition(AppState.READY, "manual trade re-check completed")
        self.state_machine.transition(AppState.ARMED, "manual trade checked and armed")
        self.view_model.append_system_log("Manual protected trade armed")
        return prepared

    def run(self) -> PreparedManualTrade:
        prepared = self._required_prepared()
        if self.state_machine.state is not AppState.ARMED:
            raise PermissionError("manual trade must be ARMED before RUN")
        self.state_machine.transition(AppState.RUNNING, "operator confirmed manual run")
        self.view_model.append_system_log("Manual protected trade run confirmed")
        return prepared

    def stop(self) -> None:
        state = self.state_machine.state
        if state not in {AppState.ARMED, AppState.RUNNING, AppState.PAUSED}:
            raise PermissionError("strategy is not armed or running")
        self.state_machine.transition(AppState.READY, "operator stopped strategy")
        self.prepared = None
        self.view_model.append_system_log("Strategy stopped; new entry plan cleared")

    def invalidate(self, reason: str) -> None:
        self.prepared = None
        self.view_model.append_system_log(f"Manual plan invalidated: {reason}")

    def _timestamps(self, now: datetime) -> tuple[datetime, datetime, datetime]:
        state = self.view_model.state
        if state.mode is AppMode.REPLAY:
            simulated = state.candles[-1].closed_at if state.candles else now
            return simulated, simulated, simulated
        stale = now - timedelta(days=365)
        return (
            state.public.last_message_at or stale,
            state.private.last_message_at or stale,
            now,
        )

    def _required_prepared(self) -> PreparedManualTrade:
        if self.prepared is None:
            raise RuntimeError("manual trade has not passed Check")
        return self.prepared


def default_manual_risk_profile(symbol: str) -> RiskProfile:
    return RiskProfile(
        max_risk_amount=Decimal("0"),
        max_risk_percent=Decimal("1.00"),
        max_position_notional=Decimal("1000"),
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("0"),
        max_consecutive_losses=3,
        max_open_positions=1,
        max_pending_entries=1,
        max_slippage_percent=Decimal("0.1"),
        estimated_fee_rate=Decimal("0.0006"),
        max_market_data_age_seconds=Decimal("10"),
        max_private_stream_age_seconds=Decimal("30"),
        allowed_symbols=frozenset({symbol}),
        allowed_directions=frozenset({PositionSide.LONG, PositionSide.SHORT}),
        max_daily_loss_percent=Decimal("3.00"),
    )

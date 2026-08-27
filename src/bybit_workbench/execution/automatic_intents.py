from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from bybit_workbench.domain.intents import (
    CancelEntryIntent,
    EnterIntent,
    ExitIntent,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.models import InstrumentRules
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot
from bybit_workbench.exchange.bybit.testnet_execution import ExchangeProtectionPlan
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.risk import RiskContext, RiskEngine, RiskProfile
from bybit_workbench.strategies.base import (
    IntentOutcome,
    IntentOutcomeStatus,
    ReadOnlyStrategyContext,
    TradeIntent,
)

from .testnet_coordinator import TestnetExecutionCoordinator


class ShadowIntentJournal:
    """Persist real-data strategy decisions without exposing a write-capable callback."""

    def __init__(
        self,
        journal: TradingJournal,
        *,
        run_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
        parameters: Mapping[str, object],
        code_version: str | None = None,
    ) -> None:
        self.journal = journal
        self.run_id = run_id
        journal.start_strategy_run(
            run_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            code_version=code_version,
            mode="Mainnet Shadow",
            symbol=symbol,
            parameters=parameters,
        )

    def record(
        self,
        intent: TradeIntent,
        context: ReadOnlyStrategyContext,
        observed_at: datetime,
    ) -> None:
        decision_id = f"decision-{intent.intent_id}"
        self.journal.record_strategy_decision(
            decision_id,
            self.run_id,
            inputs={
                "latest_price": context.latest_price,
                "mark_price": context.mark_price,
                "position": context.position,
                "pending_entry": context.pending_entry,
            },
            decision={
                "intent_type": type(intent).__name__,
                "reason": intent.reason,
                "execution_mode": "SHADOW",
            },
            created_at=observed_at,
        )
        self.journal.record_trade_intent(
            intent,
            self.run_id,
            decision_id=decision_id,
            created_at=observed_at,
        )

    def finish(self, status: str = "COMPLETED") -> None:
        self.journal.finish_strategy_run(self.run_id, status)


class TestnetAutomaticIntentSink:
    """Maps automatic strategy intents onto the durable Testnet coordinator."""

    def __init__(
        self,
        coordinator: TestnetExecutionCoordinator,
        risk_profile: RiskProfile,
        instrument_rules: InstrumentRules,
        risk_context_provider: Callable[[], RiskContext],
        health_provider: Callable[[], BybitHealthSnapshot],
        *,
        run_id: str,
        strategy_id: str,
        strategy_version: str,
        parameters: Mapping[str, object],
        code_version: str | None = None,
    ) -> None:
        if instrument_rules.symbol not in risk_profile.allowed_symbols:
            raise ValueError("instrument symbol is not allowed by risk profile")
        self.coordinator = coordinator
        self.risk_profile = risk_profile
        self.instrument_rules = instrument_rules
        self.risk_context_provider = risk_context_provider
        self.health_provider = health_provider
        self.risk_engine = RiskEngine()
        self.run_id = run_id
        self.coordinator.journal.start_strategy_run(
            run_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            code_version=code_version,
            mode="Testnet",
            symbol=instrument_rules.symbol,
            parameters=parameters,
        )

    async def __call__(
        self,
        intent: TradeIntent,
        context: ReadOnlyStrategyContext,
    ) -> IntentOutcome:
        observed_at = datetime.now(UTC)
        decision_id = f"decision-{intent.intent_id}"
        self.coordinator.journal.record_strategy_decision(
            decision_id,
            self.run_id,
            inputs={
                "latest_price": context.latest_price,
                "mark_price": context.mark_price,
                "position": context.position,
                "pending_entry": context.pending_entry,
            },
            decision={"intent_type": type(intent).__name__, "reason": intent.reason},
            created_at=observed_at,
        )
        self.coordinator.journal.record_trade_intent(
            intent,
            self.run_id,
            decision_id=decision_id,
            created_at=observed_at,
        )
        try:
            if isinstance(intent, NoOpIntent):
                return IntentOutcome(
                    intent.intent_id,
                    IntentOutcomeStatus.APPROVED,
                    observed_at,
                    intent.reason,
                )
            if isinstance(intent, EnterIntent):
                decision = self.risk_engine.evaluate_entry(
                    intent,
                    self.risk_profile,
                    self.risk_context_provider(),
                    self.instrument_rules,
                )
                self.coordinator.journal.record_risk_decision(
                    f"risk-{intent.intent_id}",
                    intent.intent_id,
                    decision,
                    created_at=observed_at,
                )
                if (
                    not decision.approved
                    or decision.normalized_order is None
                    or decision.normalized_stop is None
                ):
                    return IntentOutcome(
                        intent.intent_id,
                        IntentOutcomeStatus.REJECTED,
                        observed_at,
                        "risk rejected: " + ", ".join(decision.rejection_codes),
                    )
                acknowledgement = await self.coordinator.submit_entry(
                    decision.normalized_order,
                    ExchangeProtectionPlan(decision.normalized_stop, intent.take_profit),
                    self.health_provider(),
                    intent_id=intent.intent_id,
                )
                return IntentOutcome(
                    intent.intent_id,
                    IntentOutcomeStatus.SUBMITTED,
                    observed_at,
                    f"Bybit orderId={acknowledgement.order_id}",
                )
            if isinstance(intent, CancelEntryIntent):
                pending = context.pending_entry
                if pending is None:
                    return IntentOutcome(
                        intent.intent_id,
                        IntentOutcomeStatus.REJECTED,
                        observed_at,
                        "there is no confirmed pending entry",
                    )
                order = await self.coordinator.observe_order(
                    context.symbol,
                    pending.client_order_id,
                )
                if order is None:
                    return IntentOutcome(
                        intent.intent_id,
                        IntentOutcomeStatus.UNKNOWN,
                        observed_at,
                        "pending entry is not visible; reconciliation required",
                    )
                await self.coordinator.cancel_entry(order)
                return IntentOutcome(
                    intent.intent_id,
                    IntentOutcomeStatus.CANCELLED,
                    observed_at,
                    f"cancel submitted for orderId={order.order_id}",
                )
            position = await self.coordinator.observe_position(context.symbol)
            if isinstance(intent, ExitIntent):
                await self.coordinator.close_position(
                    position.position,
                    intent_id=intent.intent_id,
                )
                return IntentOutcome(
                    intent.intent_id,
                    IntentOutcomeStatus.SUBMITTED,
                    observed_at,
                    "reduce-only strategy exit submitted",
                )
            if isinstance(intent, UpdateProtectionIntent):
                stop = intent.stop_price or position.stop_loss
                if stop is None:
                    return IntentOutcome(
                        intent.intent_id,
                        IntentOutcomeStatus.REJECTED,
                        observed_at,
                        "confirmed stop is required before protection update",
                    )
                take_profit = (
                    position.take_profit if intent.take_profit is None else intent.take_profit
                )
                await self.coordinator.move_stop(
                    position,
                    ExchangeProtectionPlan(stop, take_profit),
                    intent_id=intent.intent_id,
                )
                return IntentOutcome(
                    intent.intent_id,
                    IntentOutcomeStatus.SUBMITTED,
                    observed_at,
                    "exchange protection update submitted and confirmed",
                )
            raise TypeError(f"unsupported automatic intent: {type(intent).__name__}")
        except (PermissionError, RuntimeError, ValueError) as exc:
            return IntentOutcome(
                intent.intent_id,
                IntentOutcomeStatus.UNKNOWN,
                observed_at,
                str(exc),
            )

    def finish(self, status: str) -> None:
        self.coordinator.journal.finish_strategy_run(self.run_id, status)

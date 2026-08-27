from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bybit_workbench.app.config import AppSettings
from bybit_workbench.domain.types import AppMode


@dataclass(frozen=True, slots=True)
class LiveReadinessInput:
    confirmation_word: str
    symbol: str
    position_cap: Decimal
    daily_loss_cap: Decimal
    first_trade_notional: Decimal
    fresh_public: bool
    fresh_private: bool
    fresh_rest: bool
    reconciliation_complete: bool
    withdrawal_permission_absent: bool


@dataclass(frozen=True, slots=True)
class LiveReadinessCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class LiveReadinessDecision:
    checks: tuple[LiveReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(item.passed for item in self.checks)


class LiveReadinessGate:
    """Evaluates readiness only; this project deliberately exposes no Live writer."""

    def evaluate(
        self,
        settings: AppSettings,
        request: LiveReadinessInput,
    ) -> LiveReadinessDecision:
        checks = (
            LiveReadinessCheck(
                "external_live_switch",
                settings.mode is AppMode.LIVE and settings.allow_live_trading,
                f"mode={settings.mode.value} allow_live={settings.allow_live_trading}",
            ),
            LiveReadinessCheck(
                "confirmation_word",
                request.confirmation_word == "LIVE",
                "operator must type LIVE exactly",
            ),
            LiveReadinessCheck(
                "symbol_confirmed",
                bool(request.symbol.strip()),
                request.symbol.strip().upper(),
            ),
            LiveReadinessCheck(
                "positive_caps",
                request.position_cap > 0 and request.daily_loss_cap > 0,
                f"position={request.position_cap} daily_loss={request.daily_loss_cap}",
            ),
            LiveReadinessCheck(
                "first_trade_cap",
                0 < request.first_trade_notional <= request.position_cap,
                f"first={request.first_trade_notional} cap={request.position_cap}",
            ),
            LiveReadinessCheck(
                "fresh_channels",
                request.fresh_public and request.fresh_private and request.fresh_rest,
                "Public WS, Private WS and REST must all be fresh",
            ),
            LiveReadinessCheck(
                "reconciled",
                request.reconciliation_complete,
                "exchange truth must match local projection",
            ),
            LiveReadinessCheck(
                "no_withdrawal_permission",
                request.withdrawal_permission_absent,
                "trading key must not have withdrawal permission",
            ),
        )
        return LiveReadinessDecision(checks)

    def require_ready(
        self,
        settings: AppSettings,
        request: LiveReadinessInput,
    ) -> LiveReadinessDecision:
        decision = self.evaluate(settings, request)
        if not decision.ready:
            failed = ", ".join(item.code for item in decision.checks if not item.passed)
            raise PermissionError(f"Live readiness failed: {failed}")
        return decision

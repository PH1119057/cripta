from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from bybit_workbench.domain.models import InstrumentRules

from .validation import instrument_rules_fingerprint, parameters_fingerprint


@dataclass(frozen=True, slots=True)
class HistoricalEligibilityQuery:
    symbol: str
    timeframe: str
    code_version: str
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    slippage_percent: Decimal
    execution_mode: str
    price_trigger: str
    instrument_rules_fingerprint: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or self.symbol != self.symbol.strip().upper():
            raise ValueError("eligibility symbol must be non-empty uppercase")
        if not self.timeframe.strip() or not self.code_version.strip():
            raise ValueError("eligibility timeframe and code version are required")
        if not self.execution_mode.strip() or not self.price_trigger.strip():
            raise ValueError("eligibility execution mode and price trigger are required")
        if len(self.instrument_rules_fingerprint) != 64:
            raise ValueError("instrument rules fingerprint must be sha256")
        for name in ("maker_fee_rate", "taker_fee_rate", "slippage_percent"):
            value = getattr(self, name)
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        for name in ("maker_fee_rate", "taker_fee_rate"):
            value = getattr(self, name)
            if value < Decimal("-1") or value > Decimal("1"):
                raise ValueError(f"{name} must be between -1 and 1")
        if self.slippage_percent <= 0 or self.slippage_percent > Decimal("100"):
            raise ValueError("eligibility slippage must be within (0, 100]")

    @classmethod
    def from_instrument(
        cls,
        *,
        symbol: str,
        timeframe: str,
        code_version: str,
        instrument_rules: InstrumentRules,
        maker_fee_rate: Decimal,
        taker_fee_rate: Decimal,
        slippage_percent: Decimal,
        execution_mode: str = "closed-candle-limit-retest",
        price_trigger: str = "MarkPrice",
    ) -> HistoricalEligibilityQuery:
        if instrument_rules.symbol != symbol.strip().upper():
            raise ValueError("instrument rules do not match eligibility symbol")
        return cls(
            symbol.strip().upper(),
            timeframe.strip(),
            code_version.strip(),
            maker_fee_rate,
            taker_fee_rate,
            slippage_percent,
            execution_mode.strip(),
            price_trigger.strip(),
            instrument_rules_fingerprint(instrument_rules),
        )


@dataclass(frozen=True, slots=True)
class HistoricalEligibilityRecord:
    report_id: str
    eligible: bool
    dataset_fingerprint: str
    binding_fingerprint: str
    production_equivalent: bool

    def __post_init__(self) -> None:
        if not self.report_id.strip():
            raise ValueError("historical report id is required")
        if len(self.dataset_fingerprint) != 64:
            raise ValueError("dataset fingerprint must be sha256")
        if len(self.binding_fingerprint) != 64:
            raise ValueError("binding fingerprint must be sha256")


class EligibilityStore(Protocol):
    def latest_historical_eligibility(
        self,
        strategy_id: str,
        strategy_version: str,
        parameters_fingerprint: str,
        query: HistoricalEligibilityQuery,
    ) -> HistoricalEligibilityRecord | None: ...


class StrategyMetadataView(Protocol):
    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...


class StrategyRegistrationView(Protocol):
    @property
    def metadata(self) -> StrategyMetadataView: ...

    @property
    def requires_historical_validation(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class HistoricalGateDecision:
    allowed: bool
    reason: str
    parameters_fingerprint: str
    report_id: str | None = None
    dataset_fingerprint: str | None = None
    binding_fingerprint: str | None = None
    query: HistoricalEligibilityQuery | None = None


class HistoricalEligibilityGate:
    def __init__(self, store: EligibilityStore) -> None:
        self.store = store

    def evaluate(
        self,
        registration: StrategyRegistrationView,
        parameters: dict[str, object],
        query: HistoricalEligibilityQuery | None = None,
    ) -> HistoricalGateDecision:
        fingerprint = parameters_fingerprint(parameters)
        if not registration.requires_historical_validation:
            return HistoricalGateDecision(
                True,
                "manual operator-confirmed strategy is exempt from automatic history gate",
                fingerprint,
            )
        if query is None:
            return HistoricalGateDecision(
                False,
                (
                    "automatic strategy requires an exact symbol/timeframe/code/execution "
                    "BackTest binding"
                ),
                fingerprint,
            )
        record = self.store.latest_historical_eligibility(
            registration.metadata.strategy_id,
            registration.metadata.version,
            fingerprint,
            query,
        )
        if record is None:
            return HistoricalGateDecision(
                False,
                (
                    "no historical validation report matches symbol, timeframe, code, "
                    "data model, fees and execution assumptions"
                ),
                fingerprint,
                query=query,
            )
        expected_binding = eligibility_binding_fingerprint(
            strategy_id=registration.metadata.strategy_id,
            strategy_version=registration.metadata.version,
            parameters_fingerprint=fingerprint,
            query=query,
            dataset_fingerprint=record.dataset_fingerprint,
        )
        if record.binding_fingerprint != expected_binding:
            return HistoricalGateDecision(
                False,
                "historical eligibility binding fingerprint is invalid",
                fingerprint,
                record.report_id,
                record.dataset_fingerprint,
                record.binding_fingerprint,
                query,
            )
        if not record.production_equivalent:
            return HistoricalGateDecision(
                False,
                (
                    "matching historical report is not production-equivalent "
                    "(Mark Price/funding incomplete)"
                ),
                fingerprint,
                record.report_id,
                record.dataset_fingerprint,
                record.binding_fingerprint,
                query,
            )
        if not record.eligible:
            return HistoricalGateDecision(
                False,
                "latest exact historical validation report failed acceptance checks",
                fingerprint,
                record.report_id,
                record.dataset_fingerprint,
                record.binding_fingerprint,
                query,
            )
        return HistoricalGateDecision(
            True,
            "exact historical validation report is eligible for Micro-Live",
            fingerprint,
            record.report_id,
            record.dataset_fingerprint,
            record.binding_fingerprint,
            query,
        )

    def require(
        self,
        registration: StrategyRegistrationView,
        parameters: dict[str, object],
        query: HistoricalEligibilityQuery | None = None,
    ) -> HistoricalGateDecision:
        decision = self.evaluate(registration, parameters, query)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return decision


def eligibility_binding_fingerprint(
    *,
    strategy_id: str,
    strategy_version: str,
    parameters_fingerprint: str,
    query: HistoricalEligibilityQuery,
    dataset_fingerprint: str,
) -> str:
    payload = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "parameters_fingerprint": parameters_fingerprint,
        "symbol": query.symbol,
        "timeframe": query.timeframe,
        "code_version": query.code_version,
        "dataset_fingerprint": dataset_fingerprint,
        "maker_fee_rate": str(query.maker_fee_rate),
        "taker_fee_rate": str(query.taker_fee_rate),
        "slippage_percent": str(query.slippage_percent),
        "execution_mode": query.execution_mode,
        "price_trigger": query.price_trigger,
        "instrument_rules_fingerprint": query.instrument_rules_fingerprint,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

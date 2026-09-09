from typing import TYPE_CHECKING

from .contracts import (
    CandidateCooldown,
    ContextFailureAction,
    ContextMode,
    ContextRequirement,
    CooldownScope,
    DataQuality,
    EntryDecisionCode,
    EntryEmbargoPolicy,
    FrozenPolicy,
    MarketFactEnvelope,
    NumericRule,
    ObjectiveContext,
    PostSignalOutcomePolicy,
    SensorObservation,
    SensorRequirement,
    StrategyActivation,
    StrategyCard,
    TechnicalReadiness,
    TouchPolicy,
    TradeDirection,
    TradingCapacitySnapshot,
)

if TYPE_CHECKING:
    from .engine import UniversalEntryEngine
    from .registry import ActivePlanRegistry

__all__ = [
    "ActivePlanRegistry",
    "CandidateCooldown",
    "ContextFailureAction",
    "ContextMode",
    "ContextRequirement",
    "CooldownScope",
    "DataQuality",
    "EntryDecisionCode",
    "EntryEmbargoPolicy",
    "FrozenPolicy",
    "MarketFactEnvelope",
    "NumericRule",
    "ObjectiveContext",
    "PostSignalOutcomePolicy",
    "SensorObservation",
    "SensorRequirement",
    "StrategyActivation",
    "StrategyCard",
    "TechnicalReadiness",
    "TouchPolicy",
    "TradeDirection",
    "TradingCapacitySnapshot",
    "UniversalEntryEngine",
]


def __getattr__(name: str) -> object:
    if name == "UniversalEntryEngine":
        from .engine import UniversalEntryEngine

        return UniversalEntryEngine
    if name == "ActivePlanRegistry":
        from .registry import ActivePlanRegistry

        return ActivePlanRegistry
    raise AttributeError(name)

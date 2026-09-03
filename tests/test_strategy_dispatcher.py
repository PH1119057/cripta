from __future__ import annotations

import ast
import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

import bybit_workbench

PRODUCTION_PACKAGE = (
    Path(__file__).parents[1] / "production" / "src" / "bybit_workbench"
)
production_path = str(PRODUCTION_PACKAGE)
if production_path not in bybit_workbench.__path__:
    bybit_workbench.__path__.append(production_path)
dispatcher = importlib.import_module("bybit_workbench.strategy_dispatcher")

DispatcherDataQuality = dispatcher.DispatcherDataQuality
DispatcherMarketSnapshot = dispatcher.DispatcherMarketSnapshot
FeatureStatus = dispatcher.FeatureStatus
FeatureValue = dispatcher.FeatureValue
MatchOperator = dispatcher.MatchOperator
ProfileRule = dispatcher.ProfileRule
RequirementMode = dispatcher.RequirementMode
StrategyDispatcher = dispatcher.StrategyDispatcher
StrategyMarketProfile = dispatcher.StrategyMarketProfile
StrategyMarketProfileRegistry = dispatcher.StrategyMarketProfileRegistry
SuitabilityStatus = dispatcher.SuitabilityStatus


def snap(
    features: dict[str, FeatureValue],
    quality: DispatcherDataQuality = DispatcherDataQuality.HIGH,
) -> DispatcherMarketSnapshot:
    return DispatcherMarketSnapshot(
        snapshot_id="mayak-1",
        observed_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        mayak_version="test",
        architecture_version="1.0",
        data_quality=quality,
        features=features,
    )


def profile(*rules: ProfileRule) -> StrategyMarketProfile:
    return StrategyMarketProfile("p", "1", "Тестовый профиль", "тест", rules)


def fv(value: str | float) -> FeatureValue:
    return FeatureValue(value=value, status=FeatureStatus.VALID, confidence=1.0)


def test_required_missing_fails_closed_to_insufficient_data() -> None:
    item = profile(
        ProfileRule(
            "data.quality", RequirementMode.REQUIRED, MatchOperator.ONE_OF, ("HIGH",)
        )
    )
    result = StrategyDispatcher().evaluate(snap({}), item)
    assert result.status is SuitabilityStatus.INSUFFICIENT_DATA
    assert result.suitability is None
    assert result.missing_required == ("data.quality",)


def test_rejected_condition_overrides_good_soft_score() -> None:
    item = profile(
        ProfileRule(
            "data.quality", RequirementMode.REQUIRED, MatchOperator.ONE_OF, ("HIGH",)
        ),
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NORMAL",),
        ),
        ProfileRule(
            "liquidation.phase",
            RequirementMode.REJECTED,
            MatchOperator.ONE_OF,
            ("CASCADE",),
        ),
    )
    result = StrategyDispatcher().evaluate(
        snap({
            "data.quality": fv("HIGH"),
            "market.volatility": fv("NORMAL"),
            "liquidation.phase": fv("CASCADE"),
        }),
        item,
    )
    assert result.status is SuitabilityStatus.INCOMPATIBLE
    assert result.suitability == 0.0
    assert result.rejected_triggered == ("liquidation.phase",)


def test_preferred_rules_produce_explainable_score() -> None:
    item = profile(
        ProfileRule(
            "data.quality", RequirementMode.REQUIRED, MatchOperator.ONE_OF, ("HIGH",)
        ),
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NORMAL",),
            weight=2,
        ),
        ProfileRule(
            "market.synchronization",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("LOW",),
        ),
    )
    result = StrategyDispatcher().evaluate(
        snap({
            "data.quality": fv("HIGH"),
            "market.volatility": fv("NORMAL"),
            "market.synchronization": fv("EXTREME"),
        }), item,
    )
    assert result.suitability == pytest.approx(2 / 3)
    assert result.status is SuitabilityStatus.GOOD_MATCH
    assert result.matched_preferred == ("market.volatility",)


def test_low_quality_reduces_confidence_without_rewriting_market_values() -> None:
    item = profile(
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NORMAL",),
        )
    )
    result = StrategyDispatcher().evaluate(
        snap({"market.volatility": fv("NORMAL")}, DispatcherDataQuality.LOW), item
    )
    assert result.status is SuitabilityStatus.EXCELLENT_MATCH
    assert result.suitability == 1.0
    assert result.confidence == pytest.approx(0.5)


def test_insufficient_global_quality_is_not_neutral() -> None:
    item = profile(
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NORMAL",),
        )
    )
    result = StrategyDispatcher().evaluate(
        snap({"market.volatility": fv("NORMAL")}, DispatcherDataQuality.INSUFFICIENT), item
    )
    assert result.status is SuitabilityStatus.INSUFFICIENT_DATA
    assert result.suitability is None
    assert result.confidence == 0.0


def test_evaluation_is_deterministic() -> None:
    item = profile(
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NORMAL",),
        )
    )
    snapshot = snap({"market.volatility": fv("NORMAL")})
    first = StrategyDispatcher().evaluate(snapshot, item)
    second = StrategyDispatcher().evaluate(snapshot, item)
    assert first == second


def test_registry_is_version_aware_and_rejects_duplicate() -> None:
    item = profile(
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NORMAL",),
        )
    )
    registry = StrategyMarketProfileRegistry()
    registry.register(item)
    assert registry.get("p", "1") is item
    with pytest.raises(ValueError, match="already registered"):
        registry.register(item)


def test_profile_cannot_reference_unknown_feature() -> None:
    item = profile(
        ProfileRule(
            "strategy.profit",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("GOOD",),
        )
    )
    with pytest.raises(ValueError, match="unknown dispatcher features"):
        StrategyDispatcher().evaluate(snap({"strategy.profit": fv("GOOD")}), item)


def test_dispatcher_has_no_execution_risk_position_or_strategy_imports() -> None:
    package = (
        Path(__file__).parents[1]
        / "production"
        / "src"
        / "bybit_workbench"
        / "strategy_dispatcher"
    )
    forbidden = ("execution", "risk", "position_supervisor", "strategies", "private_runtime")
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(word in name.lower() for name in imports for word in forbidden), path


def test_profile_rejects_typo_in_categorical_value() -> None:
    item = profile(
        ProfileRule(
            "market.volatility",
            RequirementMode.PREFERRED,
            MatchOperator.ONE_OF,
            ("NROMAL",),
        )
    )
    with pytest.raises(ValueError, match="invalid values"):
        StrategyDispatcher().evaluate(snap({"market.volatility": fv("NORMAL")}), item)

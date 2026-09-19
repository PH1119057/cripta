from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dispatcher_v2_owner_decision_is_canonical() -> None:
    observation = (ROOT / "docs/OBSERVATION_ANALYTICS_RU.md").read_text(encoding="utf-8")
    glossary = (ROOT / "docs/CRIPTA_GLOSSARY_RU.md").read_text(encoding="utf-8")
    current_map = (ROOT / "docs/CURRENT_PROJECT_MAP_RU.md").read_text(encoding="utf-8")

    assert "Dispatcher находится между MAYAK и Strategy" in observation
    assert "- не создаёт Strategy profiles/suitability;" in observation
    assert "- не включает и не выключает Strategy;" in observation
    assert "- не создаёт StrategySignal;" in observation
    assert "CoinMarketRating" in observation
    assert "**CoinMarketRating**" in glossary
    assert "legacy source/config всё ещё содержит `M3_V1_*` identifiers" in current_map
    assert "Последний пункт — `FINDING`" in current_map

def test_legacy_dispatcher_docs_are_historical_only() -> None:
    index = (ROOT / "docs/DOCUMENTATION_INDEX_RU.md").read_text(encoding="utf-8")

    assert "LEVEL H — Git history, archive, patch payload docs" in index
    assert "В активном каталоге `docs/` находятся только текущие канонические документы." in index
    for name in (
        "STRATEGY_DISPATCHER_IMPLEMENTATION_D0_D6_RU.md",
        "STRATEGY_DISPATCHER_MARKET_VOCABULARY_RU.md",
        "STRATEGY_DISPATCHER_PROFILE_GUIDE_RU.md",
        "STRATEGY_DISPATCHER_RUNBOOK_RU.md",
    ):
        assert name not in index

def test_dispatcher_v2_source_tree_has_no_legacy_imports() -> None:
    package = ROOT / "production/src/bybit_workbench/dispatcher_v2"
    if not package.exists():
        return
    body = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert "bybit_workbench.strategy_dispatcher" not in body
    assert "StrategyMarketProfile" not in body
    assert "SuitabilityStatus" not in body
    assert "GOOD_MATCH" not in body
    assert "INCOMPATIBLE" not in body

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dispatcher_v2_owner_decision_is_canonical() -> None:
    architecture = (ROOT / "docs/STRATEGY_DISPATCHER_ARCHITECTURE_RU.md").read_text(
        encoding="utf-8"
    )
    implementation = (ROOT / "docs/DISPATCHER_V2_IMPLEMENTATION_RU.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "DISPATCHER_V2_CLEAN_IMPLEMENTATION = YES" in architecture
    assert "COIN_MARKET_RATING_IMPLEMENTED = NO" in implementation
    assert "bybit_workbench.strategy_dispatcher" in implementation
    assert "Clean implementation" in agents or "clean implementation" in agents


def test_legacy_dispatcher_docs_are_historical_only() -> None:
    authority = (ROOT / "docs/DOCUMENT_AUTHORITY_RU.md").read_text(encoding="utf-8")
    for name in (
        "STRATEGY_DISPATCHER_IMPLEMENTATION_D0_D6_RU.md",
        "STRATEGY_DISPATCHER_MARKET_VOCABULARY_RU.md",
        "STRATEGY_DISPATCHER_PROFILE_GUIDE_RU.md",
        "STRATEGY_DISPATCHER_RUNBOOK_RU.md",
    ):
        assert name in authority

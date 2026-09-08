from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_strategy_entry_contract_is_canonical_and_registered() -> None:
    contract = (ROOT / "docs" / "STRATEGY_ENTRY_ARCHITECTURE_RU.md").read_text(
        encoding="utf-8"
    )
    authority = (ROOT / "docs" / "DOCUMENT_AUTHORITY_RU.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Статус:** канонический специализированный архитектурный контракт" in contract
    assert "STRATEGY_ENTRY_ARCHITECTURE_RU.md" in authority
    assert "STRATEGY_ENTRY_ARCHITECTURE_RU.md" in agents


def test_strategy_owns_policy_and_entry_never_selects_strategy() -> None:
    contract = (ROOT / "docs" / "STRATEGY_ENTRY_ARCHITECTURE_RU.md").read_text(
        encoding="utf-8"
    )
    architecture = (ROOT / "CRIPTA_ARCHITECTURE_RULES_RU_V1.md").read_text(
        encoding="utf-8"
    )

    for token in (
        "Strategy является единственным владельцем торговой политики",
        "Entry не выбирает Strategy",
        "StrategyActivation",
        "EntryPlan",
        "ExitPlan",
        "candidate cooldown",
        "enabled=false",
        "StrategySignal",
        "Entry Watch",
        "Dispatcher не запускает Strategy",
    ):
        assert token in contract

    assert "Entry **не выбирает Strategy**" in architecture
    assert "исторические 30 минут не являются свойством универсального Entry" in architecture


def test_signal_lifecycle_is_strategy_specific_and_dispatcher_stays_objective() -> None:
    lifecycle = (ROOT / "docs" / "SIGNAL_LIFECYCLE_CONTRACT_RU.md").read_text(
        encoding="utf-8"
    )
    dispatcher = (ROOT / "docs" / "STRATEGY_DISPATCHER_ARCHITECTURE_RU.md").read_text(
        encoding="utf-8"
    )

    assert "Канонический торговый `signal_id` обозначает `StrategySignal`" in lifecycle
    assert "Один рыночный момент/набор фактов может породить" in lifecycle
    assert "Dispatcher signal не создаёт" in lifecycle
    assert "не читает `StrategyActivation`" in dispatcher
    assert "не создаёт `StrategySignal`" in dispatcher


def test_current_map_explicitly_separates_target_from_v1_implementation() -> None:
    current_map = (ROOT / "docs" / "CURRENT_PROJECT_MAP_RU.md").read_text(encoding="utf-8")

    assert "### 7.1 Текущий implementation status" in current_map
    assert "30m candidate cooldown" in current_map
    assert "implementation finding относительно новой целевой архитектуры" in current_map
    assert "Universal Entry consumer cutover ещё не реализован" in current_map

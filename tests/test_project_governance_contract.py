from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


ACTIVE_PROJECT_SOURCE_FAMILIES = (
    "CHATGPT_INTERACTION_RULES_RU*.md",
    "CRIPTA_ASSISTANT_WORK_RULES_RU_*.md",
    "CRIPTA_ARCHITECTURE_RULES_RU_*.md",
    "DOCUMENTATION_INDEX_RU*.md",
    "CRIPTA_GLOSSARY_RU*.md",
    "CURRENT_PROJECT_MAP_RU*.md",
    "TRADING_CONTOUR_RU*.md",
    "OBSERVATION_ANALYTICS_RU*.md",
)

LEGACY_STANDALONE_DOCS = (
    "PROJECT_GOVERNANCE_RU.md",
    "DOCUMENT_AUTHORITY_RU.md",
    "PROJECT_ARCHITECTURE_RU.md",
    "STRATEGY_ENTRY_ARCHITECTURE_RU.md",
    "SIGNAL_LIFECYCLE_CONTRACT_RU.md",
    "STRATEGY_DISPATCHER_ARCHITECTURE_RU.md",
)


def test_documentation_index_is_the_active_authority_router() -> None:
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    interaction = _read("docs/CHATGPT_INTERACTION_RULES_RU.md")
    agents = _read("AGENTS.md")
    work_rules = _read("CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")

    assert "**Статус:** канонический индекс документации" in index
    assert "# 3. Восемь файлов ChatGPT Project Source" in index
    for family in ACTIVE_PROJECT_SOURCE_FAMILIES:
        assert family in index

    assert "`AGENTS*.md` — GitHub-only bootstrap" in index
    assert "GitHub `PH1119057/cripta:main`" in interaction
    assert "синхронизированное operational mirror" in interaction
    assert "HARD_STOP=YES" in interaction
    assert "OWNER_DECISION_REQUIRED=YES" in interaction

    # The old standalone documentation topology must not silently regain
    # authority merely because historical copies still exist in archive/history.
    for legacy_name in LEGACY_STANDALONE_DOCS:
        assert legacy_name not in index

    for token in ("REMOTE_HEAD", "SOURCE_HEAD", "INSTALLED_COMMIT", "LOADED_COMMIT"):
        assert token in work_rules
    assert "DEPLOY EXACT VERIFIED COMMIT" in work_rules
    assert "INDEPENDENT REMOTE SHA VERIFICATION" in work_rules

    assert "AUTHORITATIVE: GitHub PH1119057/cripta:main" in agents
    assert "OPERATIONAL MIRROR: /srv/cripta/source_checkout" in agents


def test_upper_architecture_and_supporting_contour_preserve_layer_ownership() -> None:
    architecture = _read("CRIPTA_ARCHITECTURE_RULES_RU_V1.md")
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    observation = _read("docs/OBSERVATION_ANALYTICS_RU.md")

    assert "Strategy layer — единственный владелец торгового смысла" in architecture
    assert "Entry Engine — универсальный активный исполнитель EntryPlan." in architecture
    assert "Exit Engine получает/claim-ит эту StrategyPosition" in architecture
    assert "Execution — техническая граница биржевой мутации." in architecture
    assert "`Risk` не является самостоятельным верхнеуровневым слоем." in architecture

    assert "Entry не вводит winner/priority/arbitration между Strategy." in trading
    assert "EXCHANGE_POSITION_OWNERSHIP_CONFLICT — штатный admission outcome" in trading

    assert "- не читает PnL Strategy как рыночный признак;" in observation
    assert "- не создаёт StrategySignal;" in observation
    assert "- не закрывает позицию по собственной оценке;" in observation

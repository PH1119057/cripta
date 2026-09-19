import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _canonical_lifecycle_block(text: str) -> str:
    blocks = re.findall(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    matches = [
        block.strip()
        for block in blocks
        if "StrategyActivation" in block and "final economics/audit" in block
    ]
    assert matches, "canonical lifecycle block not found"
    return matches[-1]


def test_trading_contour_is_canonical_and_registered() -> None:
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    agents = _read("AGENTS.md")

    assert "**Статус:** активный канонический контракт торгового контура" in trading
    assert "docs/TRADING_CONTOUR_RU*.md" in index
    assert "Перед Strategy / Entry / Exit / Execution:" in index
    assert "При работе с конкретным слоем дополнительно читать его активный документ" in agents
    assert "`docs/DOCUMENTATION_INDEX_RU*.md`" in agents


def test_strategy_owns_policy_and_entry_does_not_arbitrate_between_strategies() -> None:
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    architecture = _read("CRIPTA_ARCHITECTURE_RULES_RU_V1.md")

    assert "Strategy layer — единственный владелец торгового смысла" in architecture
    assert "Изменение любого торгового параметра означает новую версию Strategy." in trading
    assert "Entry не вводит winner/priority/arbitration между Strategy." in trading
    assert "Неподдержанный параметр означает fail-closed" in trading

    assert "reservation success -> EntryDecision=ACCEPTED -> EntryExecutionRequest" in trading
    assert (
        "reservation failure -> EntryDecision=INSUFFICIENT_AVAILABLE_FUNDS -> no request"
        in trading
    )
    assert "`EXCHANGE_POSITION_OWNERSHIP_CONFLICT` до Exchange mutation." in trading
    assert "Hedge-mode/subaccount/internal netting требуют отдельного owner-approved" in trading


def test_lifecycle_is_unified_and_dispatcher_remains_strategy_agnostic() -> None:
    architecture = _read("CRIPTA_ARCHITECTURE_RULES_RU_V1.md")
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    observation = _read("docs/OBSERVATION_ANALYTICS_RU.md")

    lifecycle_blocks = {
        _canonical_lifecycle_block(architecture),
        _canonical_lifecycle_block(trading),
        _canonical_lifecycle_block(observation),
    }
    assert len(lifecycle_blocks) == 1

    lifecycle = lifecycle_blocks.pop()
    for token in (
        "strategy_attempt",
        "atomic capital reservation outcome",
        "initial protection confirmation / reconciliation",
        "Exit Engine claim / heartbeat",
        "final flat confirmation",
        "capital reservation finalization/release",
    ):
        assert token in lifecycle

    assert "Dispatcher находится между MAYAK и Strategy" in observation
    assert "- не включает и не выключает Strategy;" in observation
    assert "- не создаёт StrategySignal;" in observation
    assert "- не отправляет ордер;" in observation


def test_current_map_uses_explicit_status_matrix_and_disarmed_real_execution() -> None:
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")

    assert "Status matrix на checkpoint 2026-09-19:" in current_map
    assert (
        "| Компонент / contract | CANON | IMPLEMENTED | DEPLOYED | "
        "RUNTIME VERIFIED | Evidence / режим |"
    ) in current_map
    for component in (
        "Universal Entry observer / plan ACK",
        "Atomic capital reservation / pre-dispatch TTL",
        "StrategyPosition exact binding / physical slot conflict",
        "Universal Exit Engine decision-only",
        "Typed Exit execution bridge/consumer",
        "Lifecycle Supervisor",
    ):
        assert component in current_map

    assert "`RUNTIME VERIFIED=NO` не означает «не протестировано»" in current_map
    assert "cripta-universal-entry-consumer.service  inactive/disabled" in current_map
    assert "mainnet execution gate = 0" in current_map
    assert "physical-slot block реализован и PostgreSQL-tested;" in current_map
    assert "никакой stop/TP/H3/trailing value этой ревизией не утверждается." in current_map

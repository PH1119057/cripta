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
    assert "Strategy / Entry / Exit / Execution -> docs/TRADING_CONTOUR_RU*.md" in index
    assert "При работе с конкретным слоем дополнительно читать его active routed document" in agents
    assert "`docs/DOCUMENTATION_INDEX_RU*.md`" in agents


def test_strategy_owns_policy_and_entry_does_not_arbitrate_between_strategies() -> None:
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    architecture = _read("docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md")

    assert "Strategy layer — единственный владелец торгового смысла" in architecture
    assert "Изменение любого торгового параметра означает новую версию Strategy." in trading
    assert "Entry не вводит winner/priority/arbitration между Strategy." in trading
    assert "Неподдержанный параметр означает fail-closed" in trading

    assert "slot claim и reservation должны быть зафиксированы all-or-nothing" in trading
    assert "Если reservation не получена, slot claim откатывается/освобождается" in trading
    assert "EXCHANGE_POSITION_OWNERSHIP_CONFLICT — штатный admission outcome" in trading
    assert "Hedge-mode," in trading
    assert (
        "subaccount isolation или внутренний netting требуют отдельного owner-approved"
        in trading
    )


def test_lifecycle_has_one_canonical_source_and_dispatcher_remains_strategy_agnostic() -> None:
    architecture = _read("docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md")
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    observation = _read("docs/OBSERVATION_ANALYTICS_RU.md")

    lifecycle = _canonical_lifecycle_block(architecture)
    for token in (
        "strategy_attempt",
        "atomic physical Exchange slot claim",
        "atomic capital reservation outcome",
        "initial protection confirmation / reconciliation",
        "Exit Engine claim / heartbeat",
        "final flat confirmation",
        "physical Exchange slot claim finalization/release",
        "capital reservation finalization/release",
    ):
        assert token in lifecycle

    assert "docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md §9.1" in trading
    assert "docs/CRIPTA_ARCHITECTURE_RULES_RU_*.md §9.1" in observation
    assert "final economics/audit" not in trading
    assert "final economics/audit" not in observation

    assert "Dispatcher находится между MAYAK и Strategy" in observation
    assert "- не включает и не выключает Strategy;" in observation
    assert "- не создаёт StrategySignal;" in observation
    assert "- не отправляет ордер;" in observation


def test_current_map_uses_split_runtime_evidence_and_disarmed_real_execution() -> None:
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")

    assert "## 12.1 Status matrix — checkpoint 2026-09-21" in current_map
    assert (
        "| Компонент / contract | CANON | IMPLEMENTED | DEPLOYED | "
        "LIVENESS | BEHAVIOR | Evidence / режим |"
    ) in current_map
    for component in (
        "Universal Entry observer / plan ACK",
        "Capital reservation admission",
        "Durable physical slot claim + fresh mode contract",
        "Universal Exit Engine decision-only",
        "Typed Exit execution bridge/consumer",
        "Lifecycle Supervisor full current contract",
        "Critical fault durable delivery contract",
        "LIVE-arm evidence/session gate",
    ):
        assert component in current_map

    assert "RUNTIME LIVENESS VERIFIED=YES" in current_map
    assert "READY_FOR_LIVE = NO" in current_map
    assert "READY_FOR_MICRO_LIVE = NO" in current_map
    assert "cripta-universal-entry-consumer.service = inactive" in current_map
    assert "mainnet execution gate = 0" in current_map
    assert "production fresh mode state сейчас отсутствует" in current_map


def test_current_map_mirrors_every_live_arm_gate_name() -> None:
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")
    gates = (
        "CANON_CURRENT",
        "REMOTE_COMMIT_VERIFIED",
        "SOURCE_LIVE_IDENTITY",
        "TESTS",
        "LIVE_EQUIVALENCE",
        "EXCHANGE_ACCOUNT_IDENTITY",
        "POSITION_MODE_FRESH",
        "POSITION_IDX_EXPECTED",
        "PHYSICAL_SLOT_CLAIM_CONTRACT",
        "CAPITAL_RESERVATION_CONTRACT",
        "EXACT_STRATEGY_ACTIVATION",
        "ENTRY_PLAN_EXECUTABLE",
        "EXIT_PLAN_EXECUTABLE",
        "INITIAL_PROTECTION_EXECUTABLE",
        "TERMINAL_LOSS_CONTAINMENT_PATH",
        "EMERGENCY_POLICY_SUPPORTED",
        "LIFECYCLE_SUPERVISOR_BEHAVIOR",
        "CRITICAL_FAULT_DELIVERY",
        "RECONCILIATION_PATH",
        "MAINNET_GATE_EXPLICIT_OWNER_APPROVAL",
        "MICRO_LIVE_LIMITS",
        "ROLLBACK_OR_KILL_PATH",
    )
    for gate in gates:
        assert gate in trading
        assert gate in current_map

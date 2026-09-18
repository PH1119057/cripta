from __future__ import annotations

import ast
from pathlib import Path

from operations.monitoring.exit_migration_readiness import _has_executable_rules

ROOT = Path(__file__).resolve().parents[1]
LEGACY_EXIT = (ROOT / "operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
PRIVATE_RUNTIME = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
UNIVERSAL_LOADER = (ROOT / "src/bybit_workbench/universal_exit/loader.py").read_text(
    encoding="utf-8"
)
UNIVERSAL_STORE = (ROOT / "src/bybit_workbench/universal_exit/execution_store.py").read_text(
    encoding="utf-8"
)
READINESS = (ROOT / "operations/monitoring/exit_migration_readiness.py").read_text(encoding="utf-8")
CUTOVER = (ROOT / "operations/implementation/TRADING_LIFECYCLE_P10_CUTOVER_PLAN_RU.md").read_text(
    encoding="utf-8"
)


def test_legacy_exit_excludes_universal_ownership_at_selection() -> None:
    assert "o.bot_instance_id <> 'universal-entry'" in LEGACY_EXIT
    assert "o.bot_instance_id IS NOT NULL" in LEGACY_EXIT
    assert "LEGACY_EXIT_OWNERSHIP_CONFLICT" in LEGACY_EXIT
    assert "UNIVERSAL_ENTRY_EXCLUDED_V1" in LEGACY_EXIT


def test_private_runtime_blocks_stale_legacy_auto_command_for_universal_position() -> None:
    assert "def assert_legacy_automated_exit_ownership(" in PRIVATE_RUNTIME
    assert "LEGACY_EXIT_OWNERSHIP_UNKNOWN" in PRIVATE_RUNTIME
    assert "LEGACY_EXIT_OWNERSHIP_CONFLICT" in PRIVATE_RUNTIME
    execute_start = PRIVATE_RUNTIME.index("def execute_command(")
    execute = PRIVATE_RUNTIME[execute_start : execute_start + 2500]
    assert "assert_legacy_automated_exit_ownership(" in execute
    assert execute.index("assert_legacy_automated_exit_ownership(") < execute.index(
        'api_get("/v5/position/list"'
    )


def test_universal_exit_only_loads_exact_universal_owner() -> None:
    assert "p.bot_instance_id='universal-entry'" in UNIVERSAL_LOADER
    assert 'str(row["bot_instance_id"]) != "universal-entry"' in UNIVERSAL_STORE


def test_readiness_auditor_is_read_only_and_never_authorizes_cutover() -> None:
    tree = ast.parse(READINESS)
    forbidden = {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "CREATE", "DROP"}
    upper = READINESS.upper()
    for token in forbidden:
        assert token + " " not in upper
    assert "owner_decision_required=True" in READINESS
    assert "live_cutover_authorized=False" in READINESS
    assert "ACTIVE_EXIT_PLAN_WITHOUT_EXECUTABLE_RULES" in READINESS
    assert "NO_LIVE_UNIVERSAL_SHADOW_SAMPLE" in READINESS
    assert "PRIVATE_RUNTIME_SOURCE_LIVE_DIVERGENCE" in READINESS
    assert "LEGACY_EXIT_RUNTIME_SOURCE_LIVE_DIVERGENCE" in READINESS
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "inspect_readiness"
        for node in ast.walk(tree)
    )


def test_plan_rule_detection_requires_explicit_nonempty_rules() -> None:
    assert not _has_executable_rules({})
    assert not _has_executable_rules({"exit_policy": {}})
    assert not _has_executable_rules({"exit_policy": {"rules": []}})
    assert _has_executable_rules({"exit_policy": {"rules": [{"rule_id": "r1"}]}})


def test_cutover_plan_forbids_mid_position_owner_transfer_and_requires_owner_decision() -> None:
    assert "НЕ РАЗРЕШЕНИЕ LIVE" in CUTOVER
    assert "OWNER DECISION" in CUTOVER
    assert "не переводится в Universal Exit посередине" in CUTOVER
    assert "не передаётся legacy Exit" in CUTOVER
    assert "не заменяется legacy defaults" in CUTOVER
    assert "MICRO_LIVE" in CUTOVER

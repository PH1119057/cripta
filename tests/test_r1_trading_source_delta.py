from __future__ import annotations

from operations.dashboard.app import _r1_trading_source_delta


def test_documents_and_research_do_not_invalidate_loaded_trading_release() -> None:
    changed = [
        "docs/TRADING_CONTOUR_RU.md",
        "research/CRIPTA_RESEARCH_JOURNAL_2026-10-18.md",
        "research/microstructure/experiments/new_research.py",
        "tests/test_strategy_entry.py",
        "README.md",
        "AGENTS.md",
        "operations/dashboard/templates/index.html",
    ]
    assert _r1_trading_source_delta(changed) == []


def test_trading_code_execution_config_and_new_roots_are_fail_closed() -> None:
    changed = [
        "src/bybit_workbench/universal_entry/engine.py",
        "operations/connectivity/universal_entry_consumer.py",
        "operations/infrastructure/install_verified_release.sh",
        "operations/runtime/runtime_requirements.lock",
        "pyproject.toml",
        "another_future_directory/program.py",
        "docs/readme.md",
    ]
    assert _r1_trading_source_delta(changed) == changed[:-1]


def test_unknown_or_security_sensitive_files_are_never_silently_exempted() -> None:
    changed = [".github/workflows/release.yml", "SECURITY.md", "scripts/new_script.sh"]
    assert _r1_trading_source_delta(changed) == changed

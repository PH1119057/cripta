from __future__ import annotations

from pathlib import Path

from bybit_workbench.lifecycle_supervisor import LifecycleSupervisorPolicy

ROOT = Path(__file__).resolve().parents[1]
EXIT_RUNTIME = (ROOT / "operations/monitoring/universal_exit_shadow_runtime.py").read_text(
    encoding="utf-8"
)
LIFECYCLE_RUNTIME = (ROOT / "operations/monitoring/lifecycle_supervisor_runtime.py").read_text(
    encoding="utf-8"
)
EXIT_UNIT = (ROOT / "operations/systemd/cripta-universal-exit-shadow.service").read_text(
    encoding="utf-8"
)
LIFECYCLE_UNIT = (ROOT / "operations/systemd/cripta-lifecycle-supervisor.service").read_text(
    encoding="utf-8"
)


def test_p9_shadow_runtimes_have_no_execution_or_exchange_path() -> None:
    combined = EXIT_RUNTIME + LIFECYCLE_RUNTIME
    for forbidden in (
        "universal_exit_consumer",
        "execution_bridge",
        "runtime.trade_commands",
        "publish_runtime_exit_command",
        "EntryExecutionRequest",
        "ExitExecutionRequest",
        "/v5/order/create",
        "/v5/position/trading-stop",
        "api_post(",
    ):
        assert forbidden not in combined
    assert "claim_exit_position(" in EXIT_RUNTIME
    assert "LifecycleSupervisor(" in LIFECYCLE_RUNTIME


def test_exit_owner_freshness_has_no_hidden_core_default() -> None:
    policy = LifecycleSupervisorPolicy()
    assert policy.exit_owner_max_age_seconds is None
    assert policy.exchange_state_max_age_seconds is None


def test_p9_units_are_shadow_only_and_explicitly_configured() -> None:
    assert "CRIPTA_UNIVERSAL_EXIT_SHADOW=ENABLED" in EXIT_UNIT
    assert "CRIPTA_UNIVERSAL_EXIT_SHADOW_CONSUMER_ID=universal-exit-shadow-v1" in EXIT_UNIT
    assert "CRIPTA_UNIVERSAL_EXIT_MAINNET_CONSUMER=ENABLED" not in EXIT_UNIT
    assert "universal_exit_consumer.py" not in EXIT_UNIT

    assert "CRIPTA_LIFECYCLE_SUPERVISOR=ENABLED" in LIFECYCLE_UNIT
    assert "CRIPTA_LIFECYCLE_EXIT_OWNER_MAX_AGE_SECONDS=10" in LIFECYCLE_UNIT
    assert "CRIPTA_LIFECYCLE_EXCHANGE_STATE_MAX_AGE_SECONDS" not in LIFECYCLE_UNIT

    for unit in (EXIT_UNIT, LIFECYCLE_UNIT):
        assert "User=cripta" in unit
        assert "WorkingDirectory=/srv/cripta/trade_lifecycle/current" in unit
        assert "ProtectSystem=strict" in unit
        assert "NoNewPrivileges=true" in unit


def test_exit_shadow_runtime_marks_claims_stale_on_shutdown() -> None:
    assert "mark_exit_claims_stale(" in EXIT_RUNTIME
    assert "finally:" in EXIT_RUNTIME
    assert "Universal Exit shadow runtime stopped" in EXIT_RUNTIME

from pathlib import Path


def test_dashboard_owner_arm_checks_exact_account_binding_snapshot():
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    begin = source.index("def live_rearm_readiness(")
    end = source.index("\ndef live_trading_state(", begin)
    readiness = source[begin:end]
    assert "read_binding_readiness(connection, now=datetime.now(UTC))" in readiness
    assert "STRATEGY_ACCOUNT_" in readiness
    assert "STRATEGY_ACCOUNT_BINDING_SNAPSHOT_UNAVAILABLE:" in readiness
    assert "rearm_ready\": not reasons" in readiness
    assert "UPDATE strategy_entry.strategy_execution_bindings" not in readiness
    assert "INSERT INTO strategy_entry.strategy_execution_bindings" not in readiness
    assert "enabled=1" not in readiness

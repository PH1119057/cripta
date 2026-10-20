from pathlib import Path


def test_dashboard_requires_registered_connector_proof_in_prearm_readiness():
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    begin = source.index("def live_rearm_readiness(")
    end = source.index("\ndef live_trading_state(", begin)
    readiness = source[begin:end]
    assert "read_binding_readiness(connection, now=datetime.now(UTC))" in readiness
    assert "read_account_connector_evidence(" in readiness
    assert "required_account_ids=required_accounts" in readiness
    assert "STRATEGY_ACCOUNT_CONNECTOR_SNAPSHOT_UNAVAILABLE:" in readiness
    assert "connector_evidence.reasons" in readiness
    assert '"rearm_ready": not reasons' in readiness
    assert "UPDATE control.execution_gates" not in readiness
    assert "INSERT INTO strategy_entry.strategy_execution_bindings" not in readiness

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IPC = (
    ROOT / "src/bybit_workbench/universal_exit/p90l90_real_private_ipc.py"
).read_text(encoding="utf-8")
ROUTES = (
    ROOT / "src/bybit_workbench/universal_entry/p90l90_real_account_routes.py"
).read_text(encoding="utf-8")
PREARM = (
    ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
).read_text(encoding="utf-8")
RESTART = (
    ROOT / "src/bybit_workbench/universal_entry/p90l90_real_open_restart.py"
).read_text(encoding="utf-8")


def test_account_wide_private_proof_uses_full_fifteen_second_window() -> None:
    assert "P90_ACCOUNT_PROOF_TIMEOUT_SECONDS = 15.0" in IPC
    assert (
        "timeout_seconds <= P90_ACCOUNT_PROOF_TIMEOUT_SECONDS"
        in IPC
    )
    assert "P90_ACCOUNT_PROOF_TIMEOUT_SECONDS" in ROUTES
    assert "timeout_seconds: float | None = None" in ROUTES
    assert "else float(timeout_seconds)" in ROUTES
    assert "P90_ACCOUNT_PROOF_TIMEOUT_SECONDS" in PREARM
    assert "private_proof_timeout_seconds: float | None = None" in PREARM
    assert "else float(private_proof_timeout_seconds)" in PREARM
    assert "_RECOVERY_ACCOUNT_PROOF_MAX_ATTEMPT_SECONDS = 15.0" in RESTART
    account_restart = RESTART[
        RESTART.index("def _read_recovery_account_bounded("):
        RESTART.index("def prove_p90_real_open_restart_inventory(")
    ]
    assert "timeout = min(_RECOVERY_ACCOUNT_PROOF_MAX_ATTEMPT_SECONDS, remaining)" in account_restart
    assert "timeout_seconds=timeout" in account_restart


def test_position_specific_restart_proof_keeps_shorter_bound() -> None:
    position_restart = RESTART[
        RESTART.index("for position, plan in durable:"):
    ]
    assert "timeout_seconds=3.0" in position_restart

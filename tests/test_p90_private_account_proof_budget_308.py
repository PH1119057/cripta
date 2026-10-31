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
    assert (
        "timeout_seconds=P90_ACCOUNT_PROOF_TIMEOUT_SECONDS"
        in ROUTES
    )
    assert (
        "timeout_seconds=P90_ACCOUNT_PROOF_TIMEOUT_SECONDS"
        in PREARM
    )
    account_restart = RESTART[
        RESTART.index("for route in (routes.main, routes.sub):"):
        RESTART.index("expected_symbols:", RESTART.index("for route in (routes.main, routes.sub):"))
    ]
    assert "timeout_seconds=15.0" in account_restart


def test_position_specific_restart_proof_keeps_shorter_bound() -> None:
    position_restart = RESTART[
        RESTART.index("for position, plan in durable:"):
    ]
    assert "timeout_seconds=3.0" in position_restart

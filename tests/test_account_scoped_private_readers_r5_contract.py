from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_entry_and_dispatcher_private_reads_are_exact_account_scoped() -> None:
    admission = _read("src/bybit_workbench/entry_admission.py")
    consumer = _read("operations/connectivity/universal_entry_consumer.py")
    dispatcher = _read("operations/monitoring/dispatcher_v2.py")
    assert admission.count("identity_status='PROVEN'") >= 2
    assert "request.trading_account_id" in admission
    assert consumer.count("identity_status='PROVEN'") >= 4
    assert "request.trading_account_id" in consumer
    assert dispatcher.count("identity_status='PROVEN'") >= 3


def test_supervisors_pin_exact_account_and_retire_legacy_open_reader() -> None:
    lifecycle = _read("src/bybit_workbench/lifecycle_supervisor.py")
    runtime = _read("operations/monitoring/lifecycle_supervisor_runtime.py")
    position = _read("operations/monitoring/position_supervisor.py")
    exit_runtime = _read("operations/monitoring/exit_runtime.py")
    for source in (lifecycle, runtime, position, exit_runtime):
        assert "LEGACY_UNKNOWN" not in source
        assert "identity_status='PROVEN'" in source
    assert "trading_account_id=TRADING_ACCOUNT_ID" in runtime


def test_production_units_pin_current_trading_account() -> None:
    for path in (
        "operations/systemd/cripta-lifecycle-supervisor.service",
        "operations/monitoring/cripta-position-supervisor.service",
        "operations/monitoring/cripta-exit-runtime.service",
    ):
        assert "CRIPTA_TRADING_ACCOUNT_ID=ta-2122c81ecff14f3b85b102db605212f7" in _read(path)

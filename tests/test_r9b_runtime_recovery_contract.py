from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = (
    ROOT
    / "operations/dispatcher_v2/cripta-dispatcher-v2-context-correlator.service"
).read_text(encoding="utf-8")
LIFECYCLE = (
    ROOT / "src/bybit_workbench/lifecycle_supervisor.py"
).read_text(encoding="utf-8")


def test_dispatcher_context_correlator_has_runtime_src_pythonpath() -> None:
    assert "Environment=PYTHONPATH=/srv/cripta/runtime/current/src" in UNIT


def test_lifecycle_account_scope_null_checks_have_explicit_text_type() -> None:
    assert "%s IS NULL" not in LIFECYCLE
    assert LIFECYCLE.count("%s::text IS NULL") == 18


def test_lifecycle_dispatch_scope_joins_execution_request_identity() -> None:
    needle = """FROM strategy_entry.execution_dispatches d
                 JOIN strategy_entry.execution_requests r
                   ON r.execution_request_id=d.execution_request_id
                WHERE d.state='DISPATCHED'"""
    assert needle in LIFECYCLE
    assert "r.trading_account_id=%s" in LIFECYCLE
    assert "r.identity_status='PROVEN'" in LIFECYCLE


def test_lifecycle_parameterized_like_patterns_escape_literal_percent() -> None:
    assert "INVARIANT_BROKEN%%'" in LIFECYCLE
    assert "MODE_MISMATCH%%'" in LIFECYCLE


DAILY_REPORT_UNIT = (
    ROOT / "operations/systemd/cripta-mayak-v2-report.service"
).read_text(encoding="utf-8")
WEEKLY_REPORT_UNIT = (
    ROOT / "operations/systemd/cripta-mayak-v2-weekly-report.service"
).read_text(encoding="utf-8")


def test_mayak_report_units_have_runtime_src_pythonpath() -> None:
    expected = "Environment=PYTHONPATH=/srv/cripta/runtime/current/src"
    assert expected in DAILY_REPORT_UNIT
    assert expected in WEEKLY_REPORT_UNIT


def test_mayak_report_units_keep_exact_report_actor_and_path_contract() -> None:
    for unit in (DAILY_REPORT_UNIT, WEEKLY_REPORT_UNIT):
        assert "User=cripta" in unit
        assert "Group=cripta" in unit
        assert "SupplementaryGroups=cripta-share" in unit
        assert "ReadWritePaths=/srv/cripta-share/reports" in unit
        assert (
            "ExecStartPre=/usr/local/sbin/cripta-permission-preflight "
            "--actor cripta --check create-child:/srv/cripta-share/reports"
        ) in unit

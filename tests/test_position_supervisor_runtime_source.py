from pathlib import Path


def test_post_fill_supervisor_is_independent_from_entry_runtime() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    assert "entry_shadow" not in source
    assert "ENTRY_STATUS" not in source
    assert "mayak_v2/status.json" in source


def test_supervisor_is_information_only_and_exit_runtime_owns_automation() -> None:
    supervisor = Path("operations/monitoring/position_supervisor.py").read_text(
        encoding="utf-8"
    )
    exit_runtime = Path("operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert "dispatcher_hold_context" in supervisor
    assert "POSITION_SUPERVISOR_INFORMATION_ONLY_V36" in supervisor
    assert "early_loss_eligible" not in supervisor
    assert "INSERT INTO runtime.trade_commands" not in supervisor
    assert "auto-be-" in exit_runtime
    assert "auto-trail-" in exit_runtime
    assert 'STRUCTURAL_BREAK_RULE = "NOT_PROVEN"' in exit_runtime
    assert "STRUCTURAL_EARLY_EXIT_ENABLED = False" in exit_runtime
    assert "EARLY_LOSS_PREVENTION" not in exit_runtime


def test_supervisor_has_independent_flat_safe_behavior_heartbeat() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(
        encoding="utf-8"
    )
    unit = Path("operations/monitoring/cripta-position-supervisor.service").read_text(
        encoding="utf-8"
    )

    assert '_STATUS_ROOT = Path("/run/cripta-position-supervisor")' in source
    assert '"CRIPTA_POSITION_SUPERVISOR_STATUS_PATH"' in source
    assert 'str(_STATUS_ROOT / "status.json")' in source
    assert '"service": "position_supervisor"' in source
    assert '"state": "RUNNING"' in source
    assert '"trading_rights": "NONE"' in source
    assert '"authority": "POSITION_SUPERVISOR_INFORMATION_ONLY_V36"' in source
    assert "connection.commit()\n            _write_status(" in source
    assert 'temporary = path.with_name("status.tmp")' in source
    assert "temporary.replace(path)" in source

    assert (
        "Environment=CRIPTA_POSITION_SUPERVISOR_STATUS_PATH="
        "/run/cripta-position-supervisor/status.json"
    ) in unit
    assert "RuntimeDirectory=cripta-position-supervisor" in unit
    assert "RuntimeDirectoryMode=0750" in unit

def test_r12_position_supervisor_has_no_independent_public_bybit_reader() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(
        encoding="utf-8"
    )
    assert "r12_position_publication" in source
    assert "read_r12_position_market_snapshot" in source
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in source
    assert "api.bybit.kz" not in source
    assert "urllib.request" not in source
    assert "/v5/market/kline" not in source
    assert "/v5/market/tickers" not in source
    assert "/v5/market/orderbook" not in source
    assert "websocket" not in source


def test_r12_position_mark_remains_account_local_not_public_ticker() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(
        encoding="utf-8"
    )
    assert "runtime.hot_positions" in source
    assert 'raw.get("markPrice")' in source
    assert "PositionEvent(" in source
    assert "public_mark_price" in source
    assert "POSITION_SUPERVISOR_INFORMATION_ONLY_V36" in source

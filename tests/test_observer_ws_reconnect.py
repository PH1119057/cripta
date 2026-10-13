from pathlib import Path

SOURCE = Path("operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")


def test_public_ws_disconnect_restarts_epoch_without_immediate_durable_fault() -> None:
    scope = SOURCE.split("def _run_multi_strategy_observer()", 1)[1].split(
        "def _run_parity_main()", 1
    )[0]
    assert "except websocket.WebSocketConnectionClosedException as exc:" in scope
    transient = scope.split("except websocket.WebSocketConnectionClosedException as exc:", 1)[1]
    transient = transient.split("except Exception as exc:", 1)[0]
    assert 'state="RECONNECTING"' in transient
    assert "time.sleep(2.0)" in transient
    assert "disconnect_count >= 3" in transient
    assert "_record_observer_runtime_fault(" in transient
    assert "if now_mono - disconnect_window_started > 60.0:" in transient
    assert "_run_observer_epoch(" in scope


def test_unhandled_faults_still_persist_durable_red() -> None:
    scope = SOURCE.split("def _run_multi_strategy_observer()", 1)[1].split(
        "def _run_parity_main()", 1
    )[0]
    fallback = scope.split("except Exception as exc:", 1)[1]
    assert "_record_observer_runtime_fault(" in fallback
    assert 'state="ERROR"' in fallback

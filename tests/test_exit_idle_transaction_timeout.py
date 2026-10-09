"""Crash-recovery regression: Exit must not hold PostgreSQL locks indefinitely."""

import importlib.util
from pathlib import Path


def test_exit_consumer_has_bounded_idle_transaction(monkeypatch):
    path = Path("operations/connectivity/universal_exit_consumer.py")
    spec = importlib.util.spec_from_file_location("exit_timeout_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []
    class Connection:
        def commit(self):
            calls.append("commit")
        def close(self):
            calls.append("close")

    def fake_connect(*args, **kwargs):
        calls.append(kwargs)
        return Connection()

    def fake_run_once(_conn):
        module.running = False
        return "NO_PENDING_EXIT"

    monkeypatch.setattr(module.psycopg, "connect", fake_connect)
    monkeypatch.setattr(module, "run_once", fake_run_once)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module, "CONSUMER_ARM", "ENABLED")

    assert module.main() == 0
    assert calls[0]["autocommit"] is False
    assert calls[0]["row_factory"] is module.dict_row
    assert calls[0]["options"] == "-c idle_in_transaction_session_timeout=120000"
    assert calls[-2:] == ["commit", "close"]

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from operations.monitoring import universal_entry_shadow as runtime


@pytest.mark.parametrize("network", [True, False])
def test_network_failure_reconnects_but_real_continuity_error_is_immediate(
    monkeypatch, tmp_path, network,
):
    connection = SimpleNamespace(rollback=lambda: None)
    monkeypatch.setattr(runtime.psycopg, "connect", lambda *_a, **_kw: nullcontext(connection))
    monkeypatch.setattr(runtime, "LOADED_COMMIT", "release")
    monkeypatch.setattr(runtime, "FACT_SOURCE_ID", runtime.REST_OI30S_SOURCE_ID)
    monkeypatch.setattr(runtime, "OI_SAMPLE_SECONDS", 30)
    monkeypatch.setattr(runtime, "OBSERVER_STATE_ROOT", tmp_path)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(runtime.time, "sleep", lambda _s: None)
    bundle = SimpleNamespace(card=SimpleNamespace(scope=SimpleNamespace(to_dict=lambda: {})))
    monkeypatch.setattr(runtime, "load_active_strategy_bundles", lambda _c: [bundle])
    statuses, faults = [], []
    monkeypatch.setattr(runtime, "_observer_status", lambda **kw: statuses.append(kw["state"]))
    monkeypatch.setattr(runtime, "_record_observer_runtime_fault",
                        lambda *_a, **_kw: faults.append("fault") or "fault-id")
    calls = 0

    def epoch(*_a, **_kw):
        nonlocal calls
        calls += 1
        if calls > (3 if network else 1):
            return "STOP"
        error = runtime.PublicTradeAuditUnavailable if network else runtime.ContinuityNotProvable
        raise error("test audit failure")

    monkeypatch.setattr(runtime, "_run_observer_epoch", epoch)
    runtime._run_multi_strategy_observer()
    assert faults == ["fault"]
    assert statuses == (["RECONNECTING", "RECONNECTING", "ERROR", "STOPPED"]
                        if network else ["ERROR", "STOPPED"])

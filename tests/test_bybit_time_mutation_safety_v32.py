from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "operations/connectivity/private_runtime.py"
SAFETY = ROOT / "research/server/connectivity/safety_observer.py"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_safety(monkeypatch: Any) -> ModuleType:
    """Load the server signer without making psycopg a Windows dependency."""
    monkeypatch.setitem(sys.modules, "psycopg", ModuleType("psycopg"))
    spec = importlib.util.spec_from_file_location("v32_safety_observer", SAFETY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self.payload).encode()


def test_changed_runtime_files_compile() -> None:
    compile(text(PRIVATE), str(PRIVATE), "exec")
    compile(text(SAFETY), str(SAFETY), "exec")


def test_signed_get_retries_only_explicit_10002(monkeypatch: Any) -> None:
    module = load_safety(monkeypatch)
    calls: list[tuple[float, str]] = []
    payloads = iter(
        [
            {"retCode": 10002, "retMsg": "timestamp"},
            {"retCode": 0, "retMsg": "OK", "result": {"list": []}},
        ]
    )

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        calls.append((timeout, str(request.get_header("X-bapi-timestamp") or "")))
        time.sleep(0.002)
        return FakeResponse(next(payloads))

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    payload, _latency = module.api_get(
        "/v5/position/list",
        {"category": "linear", "symbol": "BTCUSDT"},
        "key",
        "secret",
    )

    assert payload["retCode"] == 0
    assert len(calls) == 2
    assert all(timeout == 3.0 for timeout, _timestamp in calls)
    assert all(timestamp.isdigit() for _timeout, timestamp in calls)
    assert int(calls[1][1]) > int(calls[0][1])


def test_signed_get_does_not_retry_other_rejection(monkeypatch: Any) -> None:
    module = load_safety(monkeypatch)
    calls = 0

    def fake_urlopen(_request: Any, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        assert timeout == 3.0
        return FakeResponse({"retCode": 10001, "retMsg": "parameter error"})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    payload, _latency = module.api_get(
        "/v5/position/list",
        {"category": "linear", "symbol": "BTCUSDT"},
        "key",
        "secret",
    )
    assert payload["retCode"] == 10001
    assert calls == 1


def test_safety_observer_midpoint_clock_contract() -> None:
    source = text(SAFETY)
    assert 'SIGNED_RECV_WINDOW = "5000"' in source
    assert "SIGNED_IO_TIMEOUT_SECONDS = 3.0" in source
    assert "clock_started_ns = time.time_ns()" in source
    assert "clock_finished_ns = time.time_ns()" in source
    assert "clock_midpoint_ms = (clock_started_ns + clock_finished_ns) / 2_000_000" in source
    assert '"clock_offset_ms": int(server_ms - clock_midpoint_ms)' in source


def test_mutating_post_clock_and_transport_contract() -> None:
    source = text(PRIVATE)
    assert 'SIGNED_RECV_WINDOW = "5000"' in source
    assert "SIGNED_MUTATION_TIMEOUT_SECONDS = 3.0" in source
    assert "MUTATION_CLOCK_MAX_ABS_OFFSET_MS = 500.0" in source
    assert "MUTATION_CLOCK_CACHE_SECONDS" not in source
    assert "fresh midpoint clock observation" in source
    assert "class UnsafeBybitClock(ExchangeMutationBarrier)" in source
    assert "class AmbiguousBybitMutation(ExchangeMutationBarrier)" in source
    assert "def assert_mutation_clock_safe()" in source
    assert "timeout=SIGNED_MUTATION_TIMEOUT_SECONDS" in source
    assert 'recv_window = "10000"' not in source


def test_post_is_never_retried_and_barrier_restarts_runtime() -> None:
    source = text(PRIVATE)
    start = source.index("def api_post(")
    end = source.index("\ndef quantize(", start)
    body = source[start:end]
    assert "for attempt" not in body
    assert "while " not in body
    assert "AmbiguousBybitMutation" in body

    barrier_start = source.index("def handle_exchange_mutation_barrier(")
    barrier_end = source.index("\ndef quantize(", barrier_start)
    barrier = source[barrier_start:barrier_end]
    assert "disarm_new_entries(connection" in barrier
    assert "refresh_recent_executions(connection" in barrier
    assert 'reconcile(connection, key, secret, "ambiguous_exchange_mutation")' in barrier
    assert "finally:" in barrier
    assert "os._exit(75)" in barrier


def test_command_worker_keeps_ambiguous_command_unresolved() -> None:
    source = text(PRIVATE)
    start = source.index("def command_worker_loop(")
    end = source.index("\ndef command_loop(", start)
    worker = source[start:end]
    assert worker.index("except ExchangeMutationBarrier as exc:") < worker.index(
        "except Exception as exc:"
    )
    assert "handle_exchange_mutation_barrier(" in worker
    assert "SET state='failed'" in worker


def test_market_fill_and_post_reconcile_use_barrier() -> None:
    source = text(PRIVATE)
    assert "market entry acknowledged but actual fill is not yet confirmed" in source
    assert 'reconcile(connection, key, secret, "after_command")' in source
    assert 'f"post-mutation reconciliation failed: {exc}"' in source


def test_startup_recovery_order_and_exact_fill_protection() -> None:
    source = text(PRIVATE)
    start = source.index("def startup_live_safety(")
    end = source.index("\ndef record_entry_decision(", start)
    body = source[start:end]
    ordered = (
        'disarm_new_entries(connection, "restart: owner re-arm required")',
        'reconcile(connection, key, secret, "startup_preflight")',
        "refresh_recent_executions(connection, key, secret)",
        "cancel_bot_owned_pending_entry_orders(connection, key, secret)",
        "refresh_recent_executions(connection, key, secret)",
        "protect_recovered_bot_positions(connection, key, secret)",
        "resolve_prestart_entry_commands(connection)",
        "resolve_prestart_non_entry_running_commands(connection)",
        'reconcile(connection, key, secret, "startup_post_cancel")',
    )
    cursor = 0
    for marker in ordered:
        position = body.index(marker, cursor)
        cursor = position + len(marker)

    protect_start = source.index("def protect_recovered_bot_positions(")
    protect_end = source.index("\ndef resolve_prestart_entry_commands(", protect_start)
    protect = source[protect_start:protect_end]
    assert "JOIN runtime.executions e ON e.order_link_id=c.command_id" in protect
    assert "min(e.exec_time_ms)" in protect
    assert "abs(open_time_ms - int(fill_time_ms or 0)) > 10_000" in protect
    assert "calculate_initial_boundaries(" in protect
    assert '"/v5/position/trading-stop"' in protect
    assert '"restart_recovered_entry_protection"' in protect


def test_readiness_blocks_unresolved_entry_mutation() -> None:
    source = text(PRIVATE)
    start = source.index("def entry_runtime_readiness(")
    end = source.index("\ndef refresh_recent_executions(", start)
    body = source[start:end]
    assert "EXCHANGE_MUTATION_BARRIER:%" in body
    assert 'return False, "AMBIGUOUS_EXCHANGE_MUTATION"' in body

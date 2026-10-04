from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_private_runtime_supports_postonly_for_offset_entry() -> None:
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert 'payload.get("entry_time_in_force") or "GTC"' in source
    assert 'bybit_time_in_force = "PostOnly"' in source
    assert 'order.update({"price": str(price), "timeInForce": bybit_time_in_force})' in source


def test_hard_exit_path_remains_immediate_market_reduce_only() -> None:
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert '"orderType": "Market"' in source
    assert '"reduceOnly": True' in source


def test_private_runtime_dropin_exposes_safety_observer_module() -> None:
    source = (ROOT / "operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf").read_text(encoding="utf-8")
    assert ":/srv/cripta/runtime/current/research/server/connectivity" in source
    assert (ROOT / "research/server/connectivity/safety_observer.py").is_file()

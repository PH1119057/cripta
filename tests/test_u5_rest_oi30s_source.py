from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.oi30s_source import (
    CurrentOiResponse,
    Oi30sConfig,
    Oi30sHealthTracker,
    OiSlotState,
    poll_current_oi_slot,
    slot_at,
    slot_id_for,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
SYMBOLS = (
    "AAVEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "UNIUSDT",
    "XRPUSDT",
)
SOURCE_ID = "BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1"


def _response(at: datetime, *, missing: str | None = None) -> CurrentOiResponse:
    rows = {symbol: Decimal(str(index + 1)) for index, symbol in enumerate(SYMBOLS)}
    if missing is not None:
        rows.pop(missing)
    return CurrentOiResponse(
        request_started_at=at - timedelta(milliseconds=120),
        response_received_at=at,
        exchange_server_observed_at=at - timedelta(milliseconds=20),
        open_interest=rows,
        provenance="GET /v5/market/tickers?category=linear",
    )


def _config() -> Oi30sConfig:
    return Oi30sConfig(
        fact_source_id=SOURCE_ID,
        required_symbols=SYMBOLS,
        slot_seconds=30,
        request_timeout_seconds=5.0,
        retry_interval_seconds=0.25,
    )


def test_contract_declares_new_identity_without_rewriting_old_source() -> None:
    text = (ROOT / "docs/UNIVERSAL_STRATEGY_ENTRY_IMPLEMENTATION_RU.md").read_text(encoding="utf-8")
    assert "**Версия:** 1.5" in text
    assert SOURCE_ID in text
    assert "BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S" in text
    assert "historical evidence" in text
    assert "480 минут" in text


def test_slot_floor_and_progression_are_exactly_30_seconds() -> None:
    assert slot_at(NOW + timedelta(seconds=29, microseconds=999999), 30) == NOW
    assert slot_at(NOW + timedelta(seconds=30), 30) == NOW + timedelta(seconds=30)
    assert slot_id_for(NOW, 30) != slot_id_for(NOW + timedelta(seconds=30), 30)


def test_complete_slot_requires_10_of_10_and_emits_one_fact_per_symbol() -> None:
    cfg = _config()
    response_at = NOW + timedelta(seconds=1)
    result = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _timeout: _response(response_at),
        now=lambda: response_at,
        sleep=lambda _seconds: None,
    )
    assert result.state is OiSlotState.COMPLETE
    assert len(result.facts) == 10
    assert {fact.symbol for fact in result.facts} == set(SYMBOLS)
    assert all(fact.event_kind == "OPEN_INTEREST" for fact in result.facts)
    assert all(fact.observed_at == response_at for fact in result.facts)
    assert all(fact.attributes.to_dict()["open_interest"] is not None for fact in result.facts)
    assert all(fact.attributes.to_dict()["fact_source_id"] == SOURCE_ID for fact in result.facts)


def test_duplicate_same_slot_response_has_identical_fact_ids() -> None:
    cfg = _config()
    at = NOW + timedelta(seconds=2)
    a = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _t: _response(at),
        now=lambda: at,
        sleep=lambda _s: None,
    )
    b = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _t: _response(at),
        now=lambda: at,
        sleep=lambda _s: None,
    )
    assert [x.fact_id for x in a.facts] == [x.fact_id for x in b.facts]
    assert len(set(x.fact_id for x in a.facts)) == 10


def test_missing_one_symbol_cannot_emit_partial_slot() -> None:
    cfg = _config()
    times = iter(
        [
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=29, milliseconds=900),
            NOW + timedelta(seconds=30),
        ]
    )
    responses = iter([_response(NOW + timedelta(seconds=1), missing="UNIUSDT")])
    result = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _timeout: next(responses),
        now=lambda: next(times),
        sleep=lambda _seconds: None,
    )
    assert result.state in {OiSlotState.INCOMPLETE, OiSlotState.MISSED}
    assert result.facts == ()
    assert "UNIUSDT" in result.missing_symbols


def test_timeout_retry_can_complete_inside_same_slot() -> None:
    cfg = _config()
    calls = {"n": 0}
    clock = iter(
        [
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=2),
        ]
    )

    def fetch(_timeout: float) -> CurrentOiResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("synthetic")
        return _response(NOW + timedelta(seconds=2))

    result = poll_current_oi_slot(
        cfg, nominal_slot_at=NOW, fetch=fetch, now=lambda: next(clock), sleep=lambda _s: None
    )
    assert result.state is OiSlotState.COMPLETE
    assert result.attempts == 2
    assert len(result.facts) == 10


def test_retry_that_misses_deadline_is_gap_without_substitution() -> None:
    cfg = _config()
    clock = iter([NOW + timedelta(seconds=29, milliseconds=900), NOW + timedelta(seconds=30)])
    result = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _timeout: (_ for _ in ()).throw(TimeoutError("synthetic")),
        now=lambda: next(clock),
        sleep=lambda _s: None,
    )
    assert result.state is OiSlotState.MISSED
    assert result.facts == ()


def test_response_received_in_future_slot_cannot_close_previous_slot() -> None:
    cfg = _config()
    late = NOW + timedelta(seconds=30, milliseconds=1)
    result = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _t: _response(late),
        now=lambda: NOW + timedelta(seconds=1),
        sleep=lambda _s: None,
    )
    assert result.state is OiSlotState.MISSED
    assert result.facts == ()


def test_health_is_independent_from_websocket_state_and_detects_gap() -> None:
    cfg = _config()
    health = Oi30sHealthTracker(cfg)
    first = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _t: _response(NOW + timedelta(seconds=1)),
        now=lambda: NOW + timedelta(seconds=1),
        sleep=lambda _s: None,
    )
    health.accept(first)
    assert health.snapshot()["complete_slots"] == 1
    assert "ws" not in " ".join(health.snapshot().keys()).lower()
    health.accept_gap(
        NOW + timedelta(seconds=30), state=OiSlotState.MISSED, reason="synthetic REST timeout"
    )
    snap = health.snapshot()
    assert snap["oi_source_state"] == "NOT_PROVABLE"
    assert snap["missed_slots"] == 1
    assert snap["consecutive_complete_slots"] == 0


def test_no_last_known_carry_forward_or_5m_substitution_api_exists() -> None:
    source = ROOT / "src/bybit_workbench/universal_entry/oi30s_source.py"
    if not source.exists():
        pytest.skip("module not implemented yet")
    body = source.read_text(encoding="utf-8").lower()
    for token in ("5min", "open-interest", "carry_forward", "nearest", "interpol"):
        assert token not in body


def test_source_only_soak_is_public_one_request_all_symbols_and_no_trading_path() -> None:
    script = ROOT / "operations/monitoring/u5_oi30s_source_soak.py"
    unit = ROOT / "operations/systemd/cripta-u5-oi30s-source-soak.service"
    assert script.exists()
    assert unit.exists()
    body = script.read_text(encoding="utf-8")
    assert "/v5/market/tickers" in body
    assert "category=linear" in body or '"category": "linear"' in body
    assert (
        "for symbol in symbols"
        not in body.split("def _fetch_current_oi", 1)[-1].split("def ", 1)[0]
    )
    forbidden = (
        "runtime.trade_commands",
        "runtime.executions",
        "place_order",
        "cancel_order",
        "api_key",
        "api_secret",
        "private_ws",
        "private_runtime",
    )
    lowered = (body + unit.read_text(encoding="utf-8")).lower()
    for token in forbidden:
        assert token.lower() not in lowered


def test_u5_runtime_new_source_does_not_use_ws_ticker_oi_for_new_identity() -> None:
    body = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    assert SOURCE_ID in body
    assert "Oi30s" in body or "oi30s" in body
    assert "OPEN_INTEREST" in body


def test_new_source_ws_topics_exclude_ticker_oi(monkeypatch) -> None:
    import importlib.util

    runtime_path = ROOT / "operations/monitoring/universal_entry_shadow.py"
    spec = importlib.util.spec_from_file_location("u5_runtime_under_test", runtime_path)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    monkeypatch.setattr(runtime, "FACT_SOURCE_ID", SOURCE_ID)
    topics = runtime._topics(SYMBOLS)
    assert not any(topic.startswith("tickers.") for topic in topics)
    assert sum(topic.startswith("publicTrade.") for topic in topics) == 10
    assert sum(topic.startswith("kline.") for topic in topics) == 30


def test_complete_rest_oi_batch_fans_out_same_fact_objects_to_both_sides() -> None:
    from bybit_workbench.universal_entry.shadow_runtime import CausalFactFanout

    cfg = _config()
    at = NOW + timedelta(seconds=1)
    result = poll_current_oi_slot(
        cfg,
        nominal_slot_at=NOW,
        fetch=lambda _timeout: _response(at),
        now=lambda: at,
        sleep=lambda _seconds: None,
    )
    left = []
    right = []
    fanout = CausalFactFanout(left.append, right.append)
    for fact in result.facts:
        fanout.dispatch(fact)
    assert len(left) == len(right) == 10
    assert all(a is b for a, b in zip(left, right, strict=True))


def test_rest_oi_failure_is_independent_of_connected_ws_state() -> None:
    cfg = _config()
    health = Oi30sHealthTracker(cfg)
    ws_state = "CONNECTED"
    health.accept_gap(
        NOW,
        state=OiSlotState.MISSED,
        reason="synthetic REST timeout while WS remains connected",
    )
    assert ws_state == "CONNECTED"
    assert health.snapshot()["oi_source_state"] == "NOT_PROVABLE"
    assert health.snapshot()["missed_slots"] == 1


def test_soak_contract_is_480_minutes_fail_closed_and_append_only() -> None:
    script = (ROOT / "operations/monitoring/u5_oi30s_source_soak.py").read_text(encoding="utf-8")
    unit = (ROOT / "operations/systemd/cripta-u5-oi30s-source-soak.service").read_text(
        encoding="utf-8"
    )
    assert 'SOAK_MINUTES = int(os.environ.get("CRIPTA_U5_OI30S_SOAK_MINUTES", "480"))' in script
    assert "SOAK_MINUTES < 480" in script
    assert "os.O_APPEND" in script
    assert "Restart=no" in unit
    assert "WorkingDirectory=/srv/cripta/u5_oi30s_source/current" in unit
    assert "PYTHONPATH=/srv/cripta/u5_oi30s_source/current/src:" in unit
    expected_exec = (
        "ExecStart=/usr/bin/python3 "
        "/srv/cripta/u5_oi30s_source/current/operations/monitoring/"
        "u5_oi30s_source_soak.py"
    )
    assert expected_exec in unit
    assert "ReadWritePaths=/var/lib/cripta/u5_oi30s_source_soak" in unit
    assert "api_key" not in (script + unit).lower()
    assert "api_secret" not in (script + unit).lower()


def test_runtime_records_oi_slot_health_without_trading_mutation_path() -> None:
    body = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    for token in (
        'category="OI30S_SLOT"',
        '"oi_source_state"',
        '"last_complete_oi_slot"',
        '"complete_slots"',
        '"missed_slots"',
        '"incomplete_slots"',
        '"silent_gaps"',
        '"max_slot_delay"',
        '"consecutive_complete_slots"',
    ):
        assert token in body or token in (
            ROOT / "src/bybit_workbench/universal_entry/oi30s_source.py"
        ).read_text(encoding="utf-8")
    forbidden = (
        "runtime.trade_commands",
        "runtime.executions",
        "place_order",
        "cancel_order",
        "amend_order",
    )
    for token in forbidden:
        assert token not in body

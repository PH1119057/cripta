"""Pure tests for frozen P90L90 2025 side/portfolio cash and pause replay."""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from scripts.research import p90l90_bull2025_portfolio_v1 as p


def _event(side="LONG"):
    return {
        "side": side, "PREV7D": True, "price": "100",
        "ts": repr(p.START_TS + 1), "trade_id": "test-trade",
        "event_id": "test-event",
    }


def test_exact_six_month_window_and_new_2025_model_identity() -> None:
    assert (p.EVAL_END - p.EVAL_START).days == 183
    assert p.EVAL_START.isoformat() == "2025-04-01T00:00:00+00:00"
    assert p.EVAL_END.isoformat() == "2025-10-01T00:00:00+00:00"
    assert p.SIDES == ("LONG_ONLY", "SHORT_ONLY")
    assert len(p.SYMBOLS) == 5
    assert set(p.SYMBOLS) == {"LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"}
    assert p.SCENARIOS == (
        "NO_PAUSE", "SECOND48", "W7H24_B150", "W7H24_B200",
        "W7H24_B250", "W7H24_B300", "W7H24_B350", "W7H24_B400"
    )
    assert p.CASH_MODES == ("fixed", "compound_capped")
    assert str(p.FIXED_MARGIN * p.LEVERAGE) == "1000"
    assert str(p.START_WALLET) == "1000"
    source = Path(p.__file__).read_text(encoding="utf8")
    compile(ast.parse(source), str(p.__file__), "exec")
    assert source.find("original.on_trade(") >= 0
    assert source.find("original.initial_position(") >= 0
    assert source.find("original.trade_record(") >= 0
    assert source.find("2026-05-18") < 0
    assert source.find("PREV7D") >= 0


def test_wallet_fee_debit_closed_ledger_and_protected_stop() -> None:
    b = p.Book("LONG_ONLY", "NO_PAUSE", "fixed", [])
    t = p.START_TS + 1
    low_high_width = (D("90"), D("110"), D("1"))
    assert b.handle("LTCUSDT", t, 5, D("100"), (_event(),), low_high_width, 0)
    assert str(b.wallet) == "999.450000000000"
    assert str(b.reserved) == "200"
    assert b.handle("LTCUSDT", t + 1, 6, D("101"), (), low_high_width, 0)
    assert not b.handle("LTCUSDT", t + 2, 7, D("100.6"), (), low_high_width, 0)
    report = b.finish({"LTCUSDT": D("100.6")}, p.END_TS)
    assert report["counts"]["accepted"] == 1
    assert report["counts"]["closed"] == 1
    assert report["counts"]["exit_PROTECTED_STOP"] == 1
    assert report["counts"]["censored_open"] == 0
    assert report["reserved_margin_usdt"] == "0"
    assert D(report["wallet_usdt"]) - p.START_WALLET == D(
        report["closed_net_after_fees_usdt"]
    )
    assert D(report["closed_net_after_fees_usdt"]) > 0


def test_fixed_margin_reservation_is_not_overbooked() -> None:
    b = p.Book("SHORT_ONLY", "NO_PAUSE", "fixed", [])
    state = (D("90"), D("110"), D("1"))
    t = p.START_TS + 1
    for n, symbol in enumerate(p.SYMBOLS):
        e = {**_event("SHORT"), "event_id": f"event-{n}"}
        b.handle(symbol, t, n + 1, D("100"), (e,), state, n)
    assert b.counts["accepted"] == 4
    assert b.counts["capital_blocked_candidate"] == 1
    assert b.reserved <= p.START_WALLET


def test_second_actual_hardstop_in_48h_embargoes_new_entry_only() -> None:
    b = p.Book("LONG_ONLY", "SECOND48", "fixed", [])
    state = (D("90"), D("110"), D("1"))
    for i, symbol in enumerate(("LTCUSDT", "DOTUSDT")):
        t = p.START_TS + 10 + i*120
        e = {**_event(), "event_id": f"e-{i}"}
        assert b.handle(symbol, t, 2*i+1, D("100"), (e,), state, i)
        assert not b.handle(symbol, t+1, 2*i+2, D("89"), (), state, i)
    assert b.counts["second48_pause_triggers"] == 1
    t = p.START_TS + 300
    e = {**_event(), "event_id": "third"}
    b.handle("ARBUSDT", t, 50, D("100"), (e,), state, 2)
    assert b.counts["paused_candidate"] == 1


def test_w7_signal_uses_only_7_prior_complete_utc_days() -> None:
    first = datetime(2025, 4, 1, tzinfo=UTC).timestamp()
    maturity = datetime(2025, 4, 8, tzinfo=UTC).timestamp()
    rows = []
    for i in range(20):
        rows.append({
            "side": "LONG", "exit_ts": repr(first + i + 1),
            "exit_reason": "HARD_STOP" if i == 0 else "PROTECTED_STOP",
            "exit_source_order": i + 1,
        })
    rows.append({
        "side": "LONG", "exit_ts": repr(maturity + 50),
        "exit_reason": "HARD_STOP", "exit_source_order": 101,
    })
    records = {s: {"rows": rows if s == "LTCUSDT" else []} for s in p.SYMBOLS}
    schedule = p.shadow_schedule(records, "LONG_ONLY")
    for b in p.THRESHOLDS:
        assert len(schedule[b]) == 1
        assert schedule[b][0][0] == maturity + 50


def test_w7_simultaneous_symbol_tick_cannot_see_future_signal() -> None:
    key = (p.START_TS + 100, 1, 20)
    b = p.Book("LONG_ONLY", "W7H24_B150", "fixed", [key])
    b.advance_signals((key[0], 0, 100))
    assert b.signal_index == 0
    b.advance_signals(key)
    assert b.signal_index == 1
    assert b.pause_until == key[0] + 86400


def test_previous_day_equity_mark_never_uses_first_next_day_tick() -> None:
    source = Path(p.__file__).read_text(encoding="utf8")
    marker = source.find("if day != previous_day:")
    price_update = source.find("prices[s] = px", marker)
    shadow_update = source.find("current = shadow_positions[s]", marker)
    assert marker >= 0
    assert marker < price_update < shadow_update


def test_portfolio_source_gate_accepts_unrelated_advance_with_exact_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = tmp_path / p.SELF_REL
    source.parent.mkdir(parents=True)
    source.write_text("unchanged portfolio source", encoding="utf-8")
    pins = {rel: "1" * 64 for rel in p.unit.PINNED_SOURCE_PATHS}
    pins[p.SELF_REL] = p.sha(source)
    called = []

    def checked_unit_gate(head: str, original_pins: dict[str, str]) -> str:
        called.append((head, original_pins))
        return "2" * 40

    monkeypatch.setattr(p, "SOURCE", tmp_path)
    monkeypatch.setattr(p.unit, "verify_source_fast_forward", checked_unit_gate)
    assert p.verify_portfolio_source_fast_forward("1" * 40, pins) == "2" * 40
    assert called == [
        ("1" * 40, {rel: pins[rel] for rel in p.unit.PINNED_SOURCE_PATHS})
    ]


def test_portfolio_source_gate_rejects_own_code_change_after_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = tmp_path / p.SELF_REL
    source.parent.mkdir(parents=True)
    source.write_text("original bytes", encoding="utf-8")
    pins = {rel: "1" * 64 for rel in p.unit.PINNED_SOURCE_PATHS}
    pins[p.SELF_REL] = p.sha(source)
    monkeypatch.setattr(p, "SOURCE", tmp_path)
    monkeypatch.setattr(
        p.unit, "verify_source_fast_forward", lambda _head, _pins: "2" * 40
    )
    source.write_text("different portfolio bytes", encoding="utf-8")
    with pytest.raises(RuntimeError, match="portfolio source bytes changed"):
        p.verify_portfolio_source_fast_forward("1" * 40, pins)

"""Regression of distinct 5m RAW boundary crossings and trailing 0.20pp.

Scope: research-only Issue #233; the old LEGACY scenario stays unchanged.
"""
import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path
from types import ModuleType, SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_PRIOR_PKG = sys.modules.get("scripts.research")
_FRESH_PKG = ModuleType("scripts.research")
_FRESH_PKG.__path__ = [str(_SCRIPTS / "research")]
sys.modules["scripts.research"] = _FRESH_PKG
try:
    _SPEC = importlib.util.spec_from_file_location(
        "_p90l90_distinct5m_trail020_study", _SCRIPTS / "research"
        / "p90l90_l53_distinct5m_trail020_v1.py"
    )
    assert _SPEC is not None and _SPEC.loader is not None
    study = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = study
    _SPEC.loader.exec_module(study)
finally:
    if _PRIOR_PKG is None:
        sys.modules.pop("scripts.research", None)
    else:
        sys.modules["scripts.research"] = _PRIOR_PKG

BOUND = (D("99"), D("100.6"), D("1"))


def events(symbol, t):
    return ({
        "PREV7D": True, "symbol": symbol, "side": "LONG",
        "price": "100", "ts": repr(t), "event_id": f"{symbol}:{t}",
        "trade_id": f"{symbol}:trade:{t}",
    },)


def test_scenario_identity_and_parameters():
    assert study.LEAF == "p90l90_l53_distinct5m_trail020_20261027_v1"
    assert study.NAMES == (
        "LEGACY_L53_SECOND48", "FIRST_TOUCH_TRAIL020_ONLY",
        "CANDLE_TOUCH_2_TRAIL020", "CANDLE_TOUCH_3_TRAIL020",
        "CANDLE_TOUCH_4_TRAIL020", "CANDLE_TOUCH_5_TRAIL020",
    )
    assert study.TICK_SIZE["DOTUSDT"] == D("0.0001")
    assert study.TouchBook(0).label == "FIRST_TOUCH_TRAIL020_ONLY"


def test_one_contact_per_5m_even_with_real_retreat_and_recross():
    b = study.TouchBook(2)
    symbol = "DOTUSDT"
    b.handle_touch(symbol, 900, 1, D("100"), "a", events(symbol, 900), BOUND)
    b.handle_touch(symbol, 901, 2, D("100.6"), "b", (), BOUND)
    assert b.count["observed_touch"] == 1
    assert b.pos[symbol].stop == D("100.4")  # +0.6% peak minus 0.20pp
    b.handle_touch(symbol, 902, 3, D("100.5999"), "c", (), BOUND)
    b.handle_touch(symbol, 903, 4, D("100.6"), "d", (), BOUND)
    assert b.count["observed_touch"] == 1
    assert b.pos[symbol] is not None
    # New 5m bucket is not sufficient without another genuine price crossing.
    b.handle_touch(symbol, 1200, 5, D("100.6"), "e", (), BOUND)
    assert b.count["observed_touch"] == 1
    b.handle_touch(symbol, 1201, 6, D("100.5999"), "f", (), BOUND)
    b.handle_touch(symbol, 1202, 7, D("100.6"), "g", (), BOUND)
    assert b.count["observed_touch"] == 2
    assert b.count["close_RESTING_TP"] == 1
    assert b.pos[symbol] is None
    assert b.touch_events[0]["index"] == 1
    assert b.touch_events[1]["index"] == 2
    assert int(float(b.touch_events[0]["ts"]) // 300) != int(
        float(b.touch_events[1]["ts"]) // 300
    )


def test_first_contact_tightens_trailing_and_exit_before_next_candle():
    b = study.TouchBook(5)
    symbol = "DOTUSDT"
    b.handle_touch(symbol, 900, 1, D("100"), "a", events(symbol, 900), BOUND)
    b.handle_touch(symbol, 901, 2, D("100.6"), "b", (), BOUND)
    assert b.pos[symbol].stop == D("100.4")
    b.handle_touch(symbol, 902, 3, D("100.3999"), "c", (), BOUND)
    assert b.pos[symbol] is None
    assert len(b.trades) == 1
    assert b.trades[0]["exit_reason"] == "PROTECTED_STOP"
    assert b.count["observed_touch"] == 1


def test_trailing_refreshed_at_every_trade_after_first_candle():
    b = study.TouchBook(5)
    symbol = "DOTUSDT"
    b.handle_touch(symbol, 900, 1, D("100"), "a", events(symbol, 900), BOUND)
    b.handle_touch(symbol, 901, 2, D("100.6"), "b", (), BOUND)
    assert b.pos[symbol].stop == D("100.4")
    b.handle_touch(symbol, 902, 3, D("101"), "c", (), BOUND)
    assert b.pos[symbol].stop == D("100.8")
    b.handle_touch(symbol, 903, 4, D("100.9"), "d", (), BOUND)
    assert b.pos[symbol].stop == D("100.8")


def test_first_touch_no_tp_variant_never_fills_on_contact():
    b = study.TouchBook(0)
    symbol = "DOTUSDT"
    b.handle_touch(symbol, 900, 1, D("100"), "a", events(symbol, 900), BOUND)
    b.handle_touch(symbol, 901, 2, D("100.6"), "b", (), BOUND)
    assert b.pos[symbol] is not None
    assert b.pos[symbol].stop == D("100.4")
    b.handle_touch(symbol, 1201, 3, D("100.5999"), "c", (), BOUND)
    b.handle_touch(symbol, 1202, 4, D("100.6"), "d", (), BOUND)
    assert len(b.touch_events) == 2
    assert b.count.get("close_RESTING_TP", 0) == 0
    b.handle_touch(symbol, 1203, 5, D("100.39"), "e", (), BOUND)
    assert b.trades[0]["exit_reason"] == "PROTECTED_STOP"


def test_geometry_only_move_does_not_create_contact():
    state = study.TouchState(
        symbol="DOTUSDT", side="LONG", entry=D("100"),
        tick=D("0.0001"), entry_event_id="e",
        last_px=D("100.55"), last_bound=D("100.6"),
        last_bucket=900,
    )
    pos = SimpleNamespace(protected=False, stop=D("90"))
    touch, boundary = state.observe(
        1200, 1, D("100.55"), "t", 1200, D("100.5"), pos
    )
    assert touch is None
    assert boundary["boundary_only_crossed_last_price"] is True
    assert state.touches == 0


def test_short_stop_never_loosens_and_uses_0p20pp():
    pos = SimpleNamespace(
        side="SHORT", entry=D("100"), stop=D("110"),
        best_favorable=D("1"), protected=False
    )
    assert study.tighten_after_first_candle_touch(pos)
    assert pos.stop == D("99.2")
    assert pos.protected
    pos.best_favorable = D("0.9")
    assert not study.tighten_after_first_candle_touch(pos)
    assert pos.stop == D("99.2")


def test_empty_cohort_finishes_and_inconsistent_cohort_fails_closed():
    for n in (0, 2, 3, 4, 5):
        b = study.TouchBook(n)
        report = b.finish_touch()
        assert report["counts"].get("accepted", 0) == 0
        assert report["reached_at_least"] == {str(x): 0 for x in range(1, 6)}
        assert not b.trades and not b.position_outcomes
    b = study.TouchBook(2)
    b.count["accepted"] = 1
    try:
        b.finish_touch()
    except ValueError as exc:
        assert "accounting mismatch" in str(exc)
    else:
        raise AssertionError("research accounting must fail closed")

"""Unit tests: owner-approved research-only L5-3 TP price contact counting."""
import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path
from types import ModuleType, SimpleNamespace

# Some legacy test modules monkeypatch scripts.research in sys.modules at
# collection-time. Load the exact checked-out research file in an isolated
# module and restore the pre-existing test import state afterwards.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_PRIOR_PKG = sys.modules.get("scripts.research")
_FRESH_PKG = ModuleType("scripts.research")
_FRESH_PKG.__path__ = [str(_SCRIPTS / "research")]
sys.modules["scripts.research"] = _FRESH_PKG
try:
    _SPEC = importlib.util.spec_from_file_location(
        "_p90l90_l53_tp_touch_study", _SCRIPTS / "research"
        / "p90l90_l53_tp_touch1to5_v1.py"
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


def ev(symbol, t, side="LONG"):
    return ({
        "PREV7D": True, "symbol": symbol, "side": side,
        "price": "100", "ts": repr(t), "event_id": f"{symbol}:{t}",
        "trade_id": f"{symbol}:tr:{t}",
    },)


def test_current_tick_assumption_frozen_by_symbol():
    actual_ticks = study.TICK_SIZE
    expected_ticks = {
        "LTCUSDT": D("0.01"), "DOTUSDT": D("0.0001"),
        "ARBUSDT": D("0.00001"), "APTUSDT": D("0.0001"),
        "INJUSDT": D("0.0010"),
    }
    assert actual_ticks == expected_ticks
    assert len(study.NAMES) == 6
    assert study.NAMES[0] == study.BASELINE


def test_double_contact_requires_one_real_retreat_and_price_recross():
    book = study.TouchBook(2)
    sym = "DOTUSDT"
    book.handle_touch(sym, 1000, 1, D("100"), "a", ev(sym, 1000), BOUND)
    assert book.pos[sym] is not None
    book.handle_touch(sym, 1001, 2, D("100.5"), "b", (), BOUND)
    book.handle_touch(sym, 1002, 3, D("100.6"), "c", (), BOUND)
    assert len(book.touch_events) == 1
    assert book.pos[sym] is not None
    # Repeated native prints at same price are not second touches.
    book.handle_touch(sym, 1003, 4, D("100.6"), "d", (), BOUND)
    assert len(book.touch_events) == 1
    book.handle_touch(sym, 1004, 5, D("100.5999"), "e", (), BOUND)
    book.handle_touch(sym, 1005, 6, D("100.6"), "f", (), BOUND)
    assert len(book.touch_events) == 2
    assert book.pos[sym] is None
    assert book.trades[0]["exit_reason"] == "RESTING_TP"
    assert book.count.get("close_PORTFOLIO_FLATTEN", 0) == 0
    assert book.finish_touch()["reached_at_least"]["2"] == 1


def test_first_touch_can_be_preexisting_maker_order():
    book = study.TouchBook(1)
    sym = "DOTUSDT"
    book.handle_touch(sym, 1000, 1, D("100"), "a", ev(sym, 1000), BOUND)
    book.handle_touch(sym, 1001, 2, D("100.6"), "b", (), BOUND)
    assert len(book.trades) == 1
    assert book.trades[0]["exit_reason"] == "RESTING_TP"
    assert book.count["observed_touch"] == 1


def test_moving_boundary_over_stationary_price_is_not_contact():
    state = study.TouchState(
        symbol="DOTUSDT", side="LONG", entry=D("100"),
        tick=D("0.0001"), entry_event_id="e",
        last_px=D("100.55"), last_bound=D("100.6"),
        last_bucket=900,
    )
    pos = SimpleNamespace(protected=False, stop=D("90"))
    touch, change = state.observe(
        1200, 1, D("100.55"), "t1", 1200, D("100.5"), pos
    )
    assert touch is None
    assert state.touches == 0
    assert change["motion"] == "TOWARD_ENTRY"
    assert change["boundary_only_crossed_last_price"] is True
    assert change["cause"] == "OLD_EXTREME_EXPIRED_OR_ROLLING_REPLACED"


def test_genuine_away_motion_from_new_closed_candle():
    state = study.TouchState(
        symbol="DOTUSDT", side="LONG", entry=D("100"),
        tick=D("0.0001"), entry_event_id="e",
        last_px=D("100.5"), last_bound=D("100.6"),
        last_bucket=900,
    )
    pos = SimpleNamespace(protected=False, stop=D("90"))
    touch, change = state.observe(
        1200, 1, D("100.5"), "t1", 1200, D("100.8"), pos
    )
    assert touch is None
    assert change["motion"] == "AWAY_FROM_ENTRY"
    assert change["cause"] == "NEW_CLOSED_CANDLE_EXTREME"
    assert change["delta_ticks"] == "2E+3"


def test_short_mirrors_long_and_retreat_requires_movement():
    tick = D("0.01")
    assert study.crosses("SHORT", D("99.01"), D("99"), D("99"), tick)
    assert not study.crosses("SHORT", D("99"), D("99"), D("99"), tick)
    assert study.retreated("SHORT", D("99"), D("99.01"),
                           D("99"), tick)
    assert not study.retreated("SHORT", D("99"), D("99"),
                               D("99"), tick)
    assert study.before_tp("SHORT", D("99.01"), D("99"), tick)


def test_second_stop_embargo_does_not_flatten_remaining():
    book = study.TouchBook(5)
    for sym in ("DOTUSDT", "LTCUSDT", "APTUSDT"):
        book.handle_touch(sym, 1000, 1, D("100"), "a",
                          ev(sym, 1000), BOUND)
    book.handle_touch("DOTUSDT", 1001, 2, D("89"), "b", (), BOUND)
    book.handle_touch("LTCUSDT", 1002, 2, D("89"), "b", (), BOUND)
    assert book.count["pause_triggers"] == 1
    assert book.pos["APTUSDT"] is not None
    book.handle_touch("INJUSDT", 1003, 1, D("100"), "c",
                      ev("INJUSDT", 1003), BOUND)
    assert book.pos["INJUSDT"] is None
    assert book.count["blocked_global_pause"] == 1
    assert book.count.get("close_PORTFOLIO_FLATTEN", 0) == 0
    assert book.finish_touch()["counts"]["close_HARD_STOP"] == 2

def test_empty_touch_cohort_finishes_without_phantom_entries():
    # The 90d one-hour smoke begins before the first eligible Entry.
    # All contact variants must safely serialize an empty portfolio.
    for number in range(1, 6):
        book = study.TouchBook(number)
        report = book.finish_touch()
        assert report["counts"].get("accepted", 0) == 0
        assert report["reached_at_least"] == {
            str(k): 0 for k in range(1, 6)
        }
        assert book.trades == []
        assert book.position_outcomes == []
        assert all(pos is None for pos in book.pos.values())


def test_empty_touch_cohort_still_rejects_accounting_discrepancy():
    book = study.TouchBook(1)
    book.count["accepted"] = 1
    try:
        book.finish_touch()
    except ValueError as exc:
        assert "touch position/outcome accounting failed" in str(exc)
    else:
        raise AssertionError("missing entry/position must fail closed")

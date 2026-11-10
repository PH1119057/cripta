"""Pure regression gates for full 183-day 2025 P90L90 Exit unit adapter."""
from __future__ import annotations

import ast
from decimal import Decimal as D
from pathlib import Path

from scripts.research import p90l90_bull2025_exit_unit_v1 as new
from scripts.research import p90l90_exit_90d_v2 as old


def test_2025_window_and_only_frozen_cohort() -> None:
    assert (new.EVAL_END - new.EVAL_START).days == 183
    assert new.EVAL_START.isoformat() == "2025-04-01T00:00:00+00:00"
    assert new.EVAL_END.isoformat() == "2025-10-01T00:00:00+00:00"
    assert new.END_TS - new.START_TS == 183 * 86400
    assert len(new.SYMBOLS) == 5
    assert set(new.SYMBOLS) == {
        "LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"
    }
    assert new.ENTRY_ROOT.name == "entry_full_2025_v1"
    assert new.PARENT.name == "exit_unit_2025_v1"


def test_original_exit_semantics_never_copied_or_modified() -> None:
    source = Path(new.__file__).read_text()
    tree = ast.parse(source)
    compile(tree, str(new.__file__), "exec")
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "on_trade" not in names
    assert "gross_and_net" not in names
    assert "initial_position" not in names
    assert "take_profit_eligible" not in names
    assert "trade_record" not in names
    assert source.find("original.on_trade(") >= 0
    assert source.find("original.initial_position(") >= 0
    assert source.find("original.gross_and_net(") >= 0
    assert source.find("original.trade_record(") >= 0
    assert source.find("original.take_profit_eligible(") >= 0
    assert source.find("entry.load_frozen_period(") >= 0
    assert source.find("for side in SIDES:") >= 0
    assert source.find('"PREV7D"') >= 0
    assert source.find('"TRAIL035"') >= 0
    assert source.find("2026-05-18") < 0
    assert source.find("complete_90d_input") < 0
    assert source.find("NO_PAUSE/SECOND48/W7H24") >= 0


def _pos(side: str = "LONG"):
    entry = {
        "side": side, "price": "100",
        "event_id": "test-id", "trade_id": "test-trade", "ts": "1",
    }
    return old.initial_position(entry, 1, 300, "PREV7D", "TRAIL035")


def test_hardstop_10_percent_kept_exactly() -> None:
    long = _pos("LONG")
    short = _pos("SHORT")
    assert long.stop == D("90")
    assert short.stop == D("110")
    assert old.on_trade(long, D("89"), 2, 300, None, "TRAIL035")[1][0] == "HARD_STOP"
    assert old.on_trade(short, D("111"), 2, 300, None, "TRAIL035")[1][0] == "HARD_STOP"


def test_trail_035_protection_stays_original_for_both_sides() -> None:
    for side, beneficial, retrace in [
        ("LONG", D("101"), D("100.60")),
        ("SHORT", D("99"), D("99.40")),
    ]:
        pos = _pos(side)
        pos, exit_result = old.on_trade(pos, beneficial, 2, 300, None, "TRAIL035")
        assert exit_result is None
        assert pos.protected
        assert pos.stop == (D("100.65") if side == "LONG" else D("99.35"))
        _, exit_result = old.on_trade(pos, retrace, 3, 300, None, "TRAIL035")
        assert exit_result[0] == "PROTECTED_STOP"
        assert not exit_result[2]


def test_resting_tp_stays_maker_only_after_entry_tick() -> None:
    pos = _pos()
    pos.tp = D("102")
    pos.tp_live_after_seq = 1
    _, exit_result = old.on_trade(pos, D("103"), 2, 300, None, "TRAIL035")
    assert exit_result == ("RESTING_TP", D("102"), True)
    gross, entry_fee, exit_fee, net = old.gross_and_net(pos, D("102"), True)
    assert entry_fee == D("0.005500000000")
    assert exit_fee == D("0.002040000000")
    assert net == gross - entry_fee - exit_fee


def test_exit_unit_is_not_portfolio_pnl() -> None:
    assert new.SIDES == ("LONG", "SHORT")
    assert new.ROOT == new.entry.ROOT
    assert new.ORIGINAL_REL.endswith("p90l90_exit_90d_v2.py")
    assert new.QUANTUM == old.MONEY_QUANTUM

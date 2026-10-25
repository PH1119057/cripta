"""Unit chronology for P90L90 research-only five-coin Hard Stop portfolio pause."""
import importlib
import sys
from decimal import Decimal as D
from pathlib import Path


def portfolio_type():
    """Prevent legacy tests mocking scripts.research from poisoning this import."""
    names = (
        "scripts", "scripts.research",
        "scripts.research.p90l90_entry_90d_v2",
        "scripts.research.p90l90_exit_90d_v2",
        "scripts.research.p90l90_portfolio_pause_v1",
    )
    saved = {key: sys.modules.get(key) for key in names}
    root = str(Path(__file__).parents[2])
    sys.path.insert(0, root)
    try:
        for key in names:
            sys.modules.pop(key, None)
        return importlib.import_module(
            "scripts.research.p90l90_portfolio_pause_v1").Portfolio
    finally:
        sys.path.remove(root)
        for key in names:
            sys.modules.pop(key, None)
            if saved[key] is not None:
                sys.modules[key] = saved[key]



STATE = (D("80"), D("120"), D("1"))


def candidate(symbol, t, side="LONG"):
    return ({
        "symbol": symbol, "price": "100", "side": side, "ts": repr(t),
        "event_id": symbol + ":" + repr(t), "trade_id": symbol + ":trade",
        "PREV7D": True,
    },)


def test_global_hard_stop_flattens_other_coin_and_blocks_entry_until_expiry():
    book = portfolio_type()(6)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    assert book.count["global_triggers"] == 1
    assert book.count["close_HARD_STOP"] == 1
    assert book.stops[0]["target_positions"] == 1
    assert "LTCUSDT" in book.pending
    book.handle("LTCUSDT", 101.5, 2, D("99"), (), STATE)
    assert not book.pending
    assert book.trades[-1]["exit_reason"] == "PORTFOLIO_FLATTEN"
    assert book.trades[-1]["exit_price"] == "99"
    assert not book.trades[-1]["maker_exit"]
    book.handle("LTCUSDT", 102, 3, D("100"), candidate("LTCUSDT", 102), STATE)
    assert book.pos["LTCUSDT"] is None
    assert book.count["blocked_global_pause"] == 1
    book.handle("LTCUSDT", 101 + 6 * 3600, 4, D("100"),
                candidate("LTCUSDT", 101 + 6 * 3600), STATE)
    assert book.pos["LTCUSDT"] is not None
    assert book.count["accepted"] == 3
    assert book.finish()["counts"]["censored_open"] == 1


def test_no_pause_keeps_other_coin_and_has_no_global_trigger():
    book = portfolio_type()(None)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    assert book.count["close_HARD_STOP"] == 1
    assert book.count["global_triggers"] == 0
    assert book.pos["LTCUSDT"] is not None
    assert book.finish()["counts"]["censored_open"] == 1


def test_same_record_exit_prohibits_immediate_reentry():
    book = portfolio_type()(12)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("120"), candidate("APTUSDT", 101), STATE)
    assert book.count["accepted"] == 1
    assert book.count["busy_or_same_tick"] == 1
    assert book.count["close_RESTING_TP"] == 1
    assert book.finish()["counts"]["censored_open"] == 0


def test_pending_portfolio_flatten_blocks_entry_after_cooldown():
    book = portfolio_type()(6)
    book.handle("APTUSDT", 100, 1, D("100"), candidate("APTUSDT", 100), STATE)
    book.handle("LTCUSDT", 100, 1, D("100"), candidate("LTCUSDT", 100), STATE)
    book.handle("APTUSDT", 101, 2, D("89"), (), STATE)
    book.handle("DOTUSDT", 101 + 6 * 3600 + 10, 1, D("100"),
                candidate("DOTUSDT", 101 + 6 * 3600 + 10), STATE)
    assert book.pos["DOTUSDT"] is None
    assert book.count["blocked_global_pause"] == 1
    book.handle("LTCUSDT", 101 + 6 * 3600 + 20, 2, D("101"), (), STATE)
    assert book.count["close_PORTFOLIO_FLATTEN"] == 1
    book.handle("DOTUSDT", 101 + 6 * 3600 + 30, 2, D("100"),
                candidate("DOTUSDT", 101 + 6 * 3600 + 30), STATE)
    assert book.pos["DOTUSDT"] is not None
    assert book.finish()["counts"]["accepted"] == 3

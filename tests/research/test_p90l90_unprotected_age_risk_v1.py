"""Risk snapshot test: original trade Exit stays independent of future labels."""
from decimal import Decimal as D
from types import SimpleNamespace as Obj

from scripts.research import p90l90_unprotected_age_risk_v1 as risk


def setup_function():
    risk.OBS_ROWS.clear()
    risk.OBS_KEYS.clear()
    risk.ENTRY_META.clear()
    risk.ENTRY_META["LTCUSDT:1:LONG"] = {
        "depth_pct": "0", "h9_5m_low": "98",
        "h9_5m_high": "102",
    }


def fake_book(protected=False, mfe="0"):
    pos = Obj(
        entry_event_id="LTCUSDT:1:LONG", side="LONG",
        entry=D("100"), entry_ts=0.0, entry_seq=1,
        protected=protected, best_favorable=D(mfe),
    )
    return Obj(pos={"LTCUSDT": pos}, side="LONG")


def test_negative_age_has_no_future_label_or_lookahead():
    book = fake_book()
    risk.observe(book, "LTCUSDT", 3 * 3600 + 1, 2, D("99"),
                 (D("98"), D("103")))
    assert len(risk.OBS_ROWS) == 1
    row = risk.OBS_ROWS[0]
    assert row["age_gate_h"] == 3
    assert row["unprotected_negative"] is True
    assert D(row["gross_favorable_pct"]) == D("-1")
    assert "FUTURE_ONLY_label_exit_reason" not in row
    assert "eventual_original_net_usdt" not in row
    assert row["entry_time_h9"]["depth_pct"] == "0"
    assert row["current_closed5m_l53_low_raw"] == "98"
    risk.observe(book, "LTCUSDT", 3 * 3600 + 2, 3, D("98"),
                 (D("98"), D("103")))
    assert len(risk.OBS_ROWS) == 1
    risk.observe(book, "LTCUSDT", 6 * 3600 + 1, 4, D("97"),
                 (D("96"), D("101")))
    assert len(risk.OBS_ROWS) == 2
    assert risk.OBS_ROWS[-1]["age_gate_h"] == 6


def test_protected_is_not_unprotected_negative():
    book = fake_book(protected=True, mfe="0.60")
    risk.observe(book, "LTCUSDT", 3 * 3600 + 1, 2, D("100.20"),
                 (D("99"), D("102")))
    row = risk.OBS_ROWS[0]
    assert row["protected_by_original_trail"] is True
    assert row["unprotected_negative"] is False


def test_no_snapshot_after_native_position_closed():
    book = fake_book()
    book.pos["LTCUSDT"] = None
    risk.observe(book, "LTCUSDT", 3 * 3600 + 1, 2, D("90"),
                 (D("85"), D("100")))
    assert risk.OBS_ROWS == []


def test_inconsistent_mfe_and_protection_fails():
    book = fake_book(protected=True, mfe="0.20")
    try:
        risk.observe(book, "LTCUSDT", 3 * 3600 + 1, 2, D("99"),
                     (D("98"), D("103")))
    except ValueError as error:
        assert "protected" in str(error)
    else:
        raise AssertionError("inconsistent protection must fail")

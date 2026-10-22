"""Pure deterministic tests for non-overlap and nested incremental factor research."""
import importlib.util
from pathlib import Path

CODE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/trade_joint_nonoverlap_15m_r158_v1.py"
)
SPEC = importlib.util.spec_from_file_location("r158_nonoverlap", CODE)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)


def test_nonoverlap_15_minute_windows_and_split_boundaries():
    total = 30 * 1440
    rows = [
        {"minute_index": i, "price_close": 100 + i / 100000}
        for i in range(total)
    ]
    for fold, n_expected in [
        ("TRAIN20", 1919), ("VALID3", 287), ("LATE7", 671)
    ]:
        events = SUT.extract_nonoverlap(rows, fold)
        assert len(events) == n_expected
        inds = [day * 1440 for day, _, _ in events]
        assert inds[0] % 15 == 0
        assert all(0 < ret for _, _, ret in events)
        assert events[0][0] == SUT.SPLITS[fold][0]
        last_day = SUT.SPLITS[fold][1] - 1
        assert events[-1][0] == last_day
    # Price path missing inside the window invalidates that anchor only.
    rows[3]["price_close"] = None
    assert len(SUT.extract_nonoverlap(rows, "TRAIN20")) == 1918


def test_nested_group_compare_never_uses_parent_both_in_control():
    entries = []
    for day in range(2):
        for idx, magnitude in enumerate([10, 12, 14, 2, 4, 6]):
            row = {"pair": idx < 3, "parent": True}
            entries.append((day, row, magnitude if idx % 2 else -magnitude))
    daily = SUT.daily_contrast(
        entries, lambda r: r["pair"], lambda r: r["parent"] and not r["pair"]
    )
    assert len(daily) == 2
    for d in daily:
        assert d["n_test"] == d["n_parent_only"] == 3
        assert d["difference_mean_abs_bps"] == 8
        assert d["day_utc"] in ("2026-05-18", "2026-05-19")
    assert SUT.summarize(daily)["eligible_utc_days"] == 2


def test_parent_overlap_fails_closed():
    entries = [(0, {"pair": True}, 10)] * 3
    try:
        SUT.daily_contrast(entries, lambda _r: True, lambda _r: True)
    except ValueError as e:
        assert "OVERLAPPING_NESTED_ARMS" in str(e)
    else:
        raise AssertionError("Must forbid overlapped arm")


def test_exact_day_signflip_and_fdr_family():
    assert SUT.signflip_p([1, 1, 1]) == 0.25
    assert SUT.signflip_p([1, -1, 0]) == 1.0
    assert SUT.signflip_p([1, 1]) is None
    assert SUT.q_bh([0.01, 0.04, 1.0]) == [0.03, 0.06, 1.0]


def test_indicators_end_only_and_missingness():
    r = {
        "factor_2_trade_rate": 150,
        "factor_4_large_trade_share": None,
        "factor_8_max_timestamp_side_group_streak": 5,
        "factor_27_median_positive_gap_ms": 60,
        "factor_28_minute_fill_notional_p90_usdt": 100,
        "factor_5_delta_usdt": -55,
    }
    cutoff = {
        "rate": 120, "large": .3, "streak": 10, "fast": 100, "size": 120
    }
    assert SUT.condition(r, "high_rate", cutoff, {}) is True
    assert SUT.condition(r, "short_gap", cutoff, {}) is True
    assert SUT.condition(r, "delta_sell", cutoff, {}) is True
    assert SUT.condition(r, "delta_buy", cutoff, {}) is False
    assert SUT.condition(r, "high_large", cutoff, {}) is False
    assert SUT.condition(r, "high_rate", cutoff, {"rate": "DEGENERATE"}) is False


def test_train_only_parent_chooser():
    cutoff = {
        "rate": 100, "large": .2, "streak": 5, "fast": 20, "size": 50
    }
    events = [
        (i // 60, {
            "factor_2_trade_rate": 120,
            "factor_4_large_trade_share": 0.5,
            "factor_8_max_timestamp_side_group_streak": 2,
            "factor_27_median_positive_gap_ms": 10,
            "factor_28_minute_fill_notional_p90_usdt": 75,
            "factor_5_delta_usdt": -1 if i % 2 else 1,
        }, 80 if i % 2 else 20)
        for i in range(100)
    ]
    selected = SUT.choose_parent(events, "rate_sell", cutoff, {})
    assert selected["parent"] == "delta_sell"
    assert selected["train_parent_count"] == 50

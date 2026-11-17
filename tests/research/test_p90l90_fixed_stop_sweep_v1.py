"""#837: native moving TRAIL035 vs one-time fixed +0.15 protective-stop sweep."""
from __future__ import annotations

import importlib
from decimal import Decimal as D

base = importlib.import_module("scripts.research.p90l90_exit_90d_v2")
y25 = importlib.import_module("scripts.research.p90l90_fixed_stop_sweep_2025_v1")
y26 = importlib.import_module("scripts.research.p90l90_fixed_stop_sweep_2026_v1")


CASES = ("FIX_040", "FIX_050", "FIX_060", "FIX_070",
         "FIX_080", "FIX_090", "FIX_100")


def pos(side: str):
    event = {"side": side, "price": "100",
             "ts": "123", "event_id": "sample", "trade_id": "sample"}
    return base.initial_position(event, 1, 0, "PREV7D", "TRAIL035")


def moving_control(module):
    module.ACTIVE_CASE = "TRAIL035"
    try:
        for side in ("LONG", "SHORT"):
            left, right = pos(side), pos(side)
            px = D("100.8") if side == "LONG" else D("99.2")
            native = module.ORIGINAL_ON_TRADE(left, px, 2, 0, None, "TRAIL035")
            wrapped = module.fixed_on_trade(right, px, 2, 0, None, "TRAIL035")
            assert native == wrapped
            assert left.stop == right.stop
            assert left.protected and right.protected
    finally:
        module.ACTIVE_CASE = "TRAIL035"


def check_fixed(module):
    assert set(module.ACTIVATIONS) == set(CASES)
    assert module.CASES == ("TRAIL035",) + CASES
    assert base.on_trade is module.ORIGINAL_ON_TRADE
    assert base.take_profit_eligible is module.ORIGINAL_ELIGIBLE
    for case in CASES:
        threshold = module.ACTIVATIONS[case]
        assert threshold - D("0.15") in {
            D("0.25"), D("0.35"), D("0.45"), D("0.55"),
            D("0.65"), D("0.75"), D("0.85")
        }
        module.ACTIVE_CASE = case
        try:
            for side in ("LONG", "SHORT"):
                p = pos(side)
                before = threshold - D("0.01")
                early = base.threshold_price(side, p.entry, before)
                p, outcome = module.fixed_on_trade(p, early, 2, 0, None, "TRAIL035")
                assert outcome is None and not p.protected and p.stop != D("100.15")

                at = base.threshold_price(side, p.entry, threshold)
                p, outcome = module.fixed_on_trade(p, at, 3, 0, None, "TRAIL035")
                expected = base.threshold_price(side, p.entry, D("0.15"))
                assert outcome is None and p.stop == expected
                assert p.protected and p.protection_seq == 3

                # Even a 3% favorable move cannot tighten fixed protective stop.
                far = base.threshold_price(side, p.entry, D("3"))
                p, outcome = module.fixed_on_trade(p, far, 4, 0, None, "TRAIL035")
                assert outcome is None and p.stop == expected
                assert p.protection_seq == 3

                touch = base.threshold_price(side, p.entry, D("0.14"))
                updated, outcome = module.fixed_on_trade(
                    p, touch, 5, 0, None, "TRAIL035")
                assert updated is None
                assert outcome == ("PROTECTED_STOP", touch, False)
        finally:
            module.ACTIVE_CASE = "TRAIL035"


def test_fixed_stops_2026():
    moving_control(y26)
    check_fixed(y26)


def test_fixed_stops_2025():
    moving_control(y25)
    check_fixed(y25)


def test_2025_and_2026_fixed_native_parity():
    for case in CASES:
        y25.ACTIVE_CASE = case
        y26.ACTIVE_CASE = case
        try:
            a, b = pos("LONG"), pos("LONG")
            for seq, (px, bucket, tp) in enumerate((
                (D("100.7"), 0, D("103")),
                (D("101"), 300, D("103")),
                (D("101.3"), 600, D("105")),
                (D("100.1"), 600, D("104")),
            ), start=2):
                a, x = y25.fixed_on_trade(a, px, seq, bucket, tp, "TRAIL035")
                b, y = y26.fixed_on_trade(b, px, seq, bucket, tp, "TRAIL035")
                assert x == y
                assert a == b
                if a is None:
                    break
        finally:
            y25.ACTIVE_CASE = "TRAIL035"
            y26.ACTIVE_CASE = "TRAIL035"


def test_native_structural_tp_not_deleted_and_resting_priority():
    y26.ACTIVE_CASE = "FIX_040"
    try:
        p = pos("LONG")
        p, exit1 = y26.fixed_on_trade(
            p, D("100.40"), 2, 300, D("101.50"), "TRAIL035")
        assert exit1 is None
        assert p.protected and p.stop == D("100.15")
        assert p.tp == D("101.50") and p.tp_live_after_seq == 2
        p, exit2 = y26.fixed_on_trade(
            p, D("101.60"), 3, 300, D("106"), "TRAIL035")
        assert p is None
        assert exit2 == ("RESTING_TP", D("101.50"), True)
    finally:
        y26.ACTIVE_CASE = "TRAIL035"


def test_fixed_protection_survives_checkpoint_roundtrip():
    p = pos("SHORT")
    y25.ACTIVE_CASE = "FIX_060"
    try:
        p, outcome = y25.fixed_on_trade(
            p, D("99.40"), 2, 0, None, "TRAIL035")
        assert outcome is None and p.protected
        assert p.stop == D("99.85")
        encoded = y25.engine.encode({"position": p})
        decoded = y25.engine.decode(encoded)
        assert decoded["position"] == p
        assert decoded["position"].stop == D("99.85")
    finally:
        y25.ACTIVE_CASE = "TRAIL035"

"""Offline contract tests for Issue #646 structural TP-preserving time Exit."""
from __future__ import annotations

import ast
import types
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

SRC = (Path(__file__).parents[2] / "scripts" / "research"
       / "p90l90_holding_time_2026_v1.py")
TEXT = SRC.read_text(encoding="utf-8")
TREE = ast.parse(TEXT)
ORIGINAL_CALLS = []

def hits_stop(side, price, level):
    return price <= level if side == "LONG" else price >= level

def hits_tp(side, price, level):
    return price >= level if side == "LONG" else price <= level

def old_on_trade(pos, price, seq, bucket, tp, kind):
    ORIGINAL_CALLS.append((pos, price, seq, tp, kind))
    if seq > pos.entry_seq:
        if hits_stop(pos.side, price, pos.stop):
            return None, ("HARD_STOP", price, False)
        if (pos.tp is not None and seq > pos.tp_live_after_seq
                and hits_tp(pos.side, price, pos.tp)):
            return None, ("RESTING_TP", pos.tp, True)
    return pos, None

base = types.SimpleNamespace(on_trade=old_on_trade,
                             hits_stop=hits_stop, hits_tp=hits_tp)

def gross_and_net(pos, price, maker):
    return D(0), D("0.0055"), D("0.0055"), price

base.gross_and_net = gross_and_net

class FakeEntryEmbargo:
    def __init__(self, stops, pause):
        self.stops = stops
        self.pause = pause
        self.position = SimpleNamespace(
            entry_seq=100, entry_ts=1000, side="LONG",
            stop=D("-1000"), tp=None, tp_live_after_seq=-1)

    def handle(self, symbol, timestamp, seq, price, candidates, state):
        return base.on_trade(self.position, price, seq, 300, None, "TRAIL035")

embargo = types.SimpleNamespace(EntryEmbargo=FakeEntryEmbargo)
scope = {
    "D": D, "base": base, "embargo": embargo,
    "ORIGINAL_ON_TRADE": old_on_trade,
}
want = {"cap_label", "age_gate", "force_time_exit",
        "original_resting_exit_due", "TimeBook"}
extract = [node for node in TREE.body
           if isinstance(node, (ast.FunctionDef, ast.ClassDef))
           and node.name in want]
assert len(extract) == len(want)
exec(compile(ast.Module(body=extract, type_ignores=[]), str(SRC), "exec"),
     scope, scope)

class AgeGateTests(unittest.TestCase):
    def setUp(self):
        base.on_trade = old_on_trade
        ORIGINAL_CALLS.clear()
        self.pos = SimpleNamespace(entry_seq=100, entry_ts=1000)

    def test_source_syntax_and_exact_caps(self):
        self.assertIsInstance(TREE, ast.Module)
        assignments = {t.id: n.value for n in TREE.body
                       if isinstance(n, ast.Assign) for t in n.targets
                       if isinstance(t, ast.Name)}
        self.assertEqual(ast.literal_eval(assignments["CAPS_H"]),
                         (None, 3, 6, 12, 18, 24, 36, 48, 72))
        self.assertEqual(ast.literal_eval(assignments["PHASES"]), ("ALL", "NEG"))

    def test_first_eligible_tick_not_earlier(self):
        age_gate = scope["age_gate"]
        self.assertFalse(age_gate(self.pos, 101, 1000 + 3 * 3600 - .001, 3))
        self.assertTrue(age_gate(self.pos, 101, 1000 + 3 * 3600, 3))
        self.assertFalse(age_gate(self.pos, 100, 1000 + 6 * 3600, 3))
        self.assertFalse(age_gate(self.pos, 101, 1000 + 10000 * 3600, None))

    def test_conditional_gate_uses_closeable_fee_inclusive_pnl(self):
        check = scope["force_time_exit"]
        self.assertTrue(check(self.pos, 101, 1000 + 10800, D("-0.01"), 3, "NEG"))
        self.assertFalse(check(self.pos, 101, 1000 + 10800, D("0.01"), 3, "NEG"))
        self.assertTrue(check(self.pos, 101, 1000 + 10800, D("0.01"), 3, "ALL"))

    def test_age_book_preserves_base_exit_until_deadline(self):
        cls = scope["TimeBook"]
        b = cls(3, "ALL")
        ret = b.handle("LTCUSDT", 1000 + 12000, 101, D("1.2"), (), None)
        self.assertEqual(ret, (None, ("TIME_LIMIT", D("1.2"), False)))
        self.assertEqual(ORIGINAL_CALLS, [])
        self.assertIs(base.on_trade, old_on_trade)
        ret = b.handle("LTCUSDT", 1000 + 4000, 101, D("1.2"), (), None)
        self.assertEqual(ret[1], None)
        self.assertEqual(len(ORIGINAL_CALLS), 1)

    def test_no_cap_remains_original_exit(self):
        b = scope["TimeBook"](None, "ALL")
        ret = b.handle("LTCUSDT", 1000 + 200000, 101, D("1.2"), (), None)
        self.assertEqual(ret[1], None)
        self.assertEqual(len(ORIGINAL_CALLS), 1)
        self.assertIs(base.on_trade, old_on_trade)

    def test_negative_only_can_wait_past_deadline_for_negative(self):
        b = scope["TimeBook"](3, "NEG")
        result = b.handle("LTCUSDT", 1000 + 10800, 101, D("0.01"), (), None)
        self.assertIsNone(result[1])
        result = b.handle("LTCUSDT", 1000 + 10801, 102, D("-0.01"), (), None)
        self.assertEqual(result[1][0], "TIME_LIMIT")
        self.assertIs(base.on_trade, old_on_trade)

    def test_preexisting_hard_stop_precedes_time_exit_both_sides(self):
        for side, stop, px in (("LONG", "1.1", "1.0"),
                               ("SHORT", "0.9", "1.0")):
            with self.subTest(side=side):
                b = scope["TimeBook"](3, "ALL")
                b.position.side = side
                b.position.stop = D(stop)
                before = len(ORIGINAL_CALLS)
                result = b.handle("LTCUSDT", 1000 + 10800, 101,
                                  D(px), (), None)
                self.assertEqual(result[1], ("HARD_STOP", D(px), False))
                self.assertEqual(len(ORIGINAL_CALLS), before + 1)
                self.assertIs(base.on_trade, old_on_trade)

    def test_preexisting_resting_tp_precedes_time_exit_both_sides(self):
        for side, stop, tp, px in (("LONG", "-1000", "1.15", "1.2"),
                                   ("SHORT", "1000", "0.85", "0.8")):
            with self.subTest(side=side):
                b = scope["TimeBook"](3, "ALL")
                b.position.side = side
                b.position.stop = D(stop)
                b.position.tp = D(tp)
                b.position.tp_live_after_seq = 100
                result = b.handle("LTCUSDT", 1000 + 10800, 101,
                                  D(px), (), None)
                self.assertEqual(result[1], ("RESTING_TP", D(tp), True))
                self.assertIs(base.on_trade, old_on_trade)

    def test_not_yet_resting_tp_does_not_preempt_time_exit(self):
        b = scope["TimeBook"](3, "ALL")
        b.position.tp = D("1.1")
        b.position.tp_live_after_seq = 101
        result = b.handle("LTCUSDT", 1000 + 10800, 101,
                          D("1.2"), (), None)
        self.assertEqual(result[1], ("TIME_LIMIT", D("1.2"), False))
        self.assertEqual(ORIGINAL_CALLS, [])
        self.assertIs(base.on_trade, old_on_trade)

    def test_protected_stop_still_precedes_conditional_time_gate(self):
        b = scope["TimeBook"](3, "NEG")
        b.position.stop = D("1.3")
        result = b.handle("LTCUSDT", 1000 + 10800, 101,
                          D("1.2"), (), None)
        self.assertEqual(result[1], ("HARD_STOP", D("1.2"), False))
        self.assertIs(base.on_trade, old_on_trade)

    def test_original_tp_eligibility_is_never_overridden(self):
        # The historical P90L90 base.on_trade still owns RAW L5-3 TP moves.
        targets = []
        for node in ast.walk(TREE):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets.extend(node.targets if isinstance(node, ast.Assign)
                               else (node.target,))
        self.assertFalse(any(
            isinstance(t, ast.Attribute) and t.attr == "take_profit_eligible"
            for t in targets))

if __name__ == "__main__":
    unittest.main()

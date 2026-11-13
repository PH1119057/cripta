"""Offline research-only temporal and original-TP tests for #646 183d driver."""
from __future__ import annotations

import ast
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).parents[2] / "scripts/research/p90l90_holding_time_2025_v1.py"
AST = ast.parse(SRC.read_text(encoding="utf-8"))
CALLS = []

def original_on_trade(position, price, seq, bucket, tp, kind):
    CALLS.append((price, seq, tp))
    return position, None

def original_handle(book, symbol, timestamp, seq, px, qualified, state, priority):
    CALLS.append(tuple(e["depth_pct"] for e in qualified))
    return base.on_trade(book.position, px, seq, 300, None, "TRAIL035")

base = SimpleNamespace(on_trade=original_on_trade)
old = SimpleNamespace(LEVERAGE=D(5), TAKER=D("0.00055"), money=lambda v: v)
scope = {
    "base": base, "old": old, "D": D,
    "ORIGINAL_ON_TRADE": original_on_trade,
    "ORIGINAL_HANDLE": original_handle,
}
names = {"label", "gate", "should_force", "timed_handle"}
nodes = [n for n in AST.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(nodes) == len(names)
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SRC), "exec"), scope, scope)

class Time2025ContractTests(unittest.TestCase):
    def setUp(self):
        base.on_trade = original_on_trade
        CALLS.clear()
        self.pos = SimpleNamespace(entry_seq=77, entry_ts=1000., entry=D("100"),
                                   side="LONG")
        self.book = SimpleNamespace(cap_h=24, phase="NEG", side="LONG",
                                    position=self.pos, margin={"LTCUSDT": D("20")},
                                    open_fee={"LTCUSDT": D("0.055")})

    def test_horizons(self):
        d = {t.id: n.value for n in AST.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
        self.assertEqual(ast.literal_eval(d["CAPS_H"]),
                         (None, 3, 6, 12, 18, 24, 36, 48, 72))
        self.assertEqual(ast.literal_eval(d["PHASES"]), ("ALL", "NEG"))

    def test_time_cutoff_and_source_seq(self):
        gate = scope["gate"]
        self.assertFalse(gate(self.pos, 90, 78, 24))
        self.assertFalse(gate(self.pos, 1000 + 24 * 3600 - .1, 78, 24))
        self.assertFalse(gate(self.pos, 1000 + 24 * 3600, 77, 24))
        self.assertTrue(gate(self.pos, 1000 + 24 * 3600, 78, 24))
        self.assertFalse(gate(self.pos, 1000 + 200 * 3600, 78, None))

    def test_negative_mode_has_actual_taker_fee_and_entry_fee(self):
        should = scope["should_force"]
        self.assertFalse(should(self.book, "LTCUSDT", self.pos,
                                1000 + 24 * 3600, 78, D("101")))
        self.assertTrue(should(self.book, "LTCUSDT", self.pos,
                               1000 + 24 * 3600, 78, D("99")))
        self.assertFalse(should(self.book, "LTCUSDT", self.pos,
                                1000 + 23 * 3600, 78, D("99")))
        self.book.phase = "ALL"
        self.assertTrue(should(self.book, "LTCUSDT", self.pos,
                               1000 + 24 * 3600, 78, D("101")))

    def test_original_tp_and_depth_zero_remain_under_book(self):
        self.book.phase = "ALL"
        q = ({"side": "LONG", "PREV7D": True, "depth_pct": "0"},
             {"side": "LONG", "PREV7D": True, "depth_pct": "5"},
             {"side": "SHORT", "PREV7D": True, "depth_pct": "0"})
        ret = scope["timed_handle"](self.book, "LTCUSDT", 1000 + 24 * 3600,
                                    78, D("99"), q, None, 0)
        self.assertEqual(ret[1][0], "TIME_LIMIT")
        self.assertEqual(CALLS, [("0",)])
        self.assertIs(base.on_trade, original_on_trade)

    def test_no_time_limit_matches_existing_exit(self):
        self.book.cap_h = None
        q = ({"side": "LONG", "PREV7D": True, "depth_pct": "0"},)
        ret = scope["timed_handle"](self.book, "LTCUSDT", 1000 + 300 * 3600,
                                    79, D("120"), q, None, 0)
        self.assertIsNone(ret[1])
        self.assertEqual(len(CALLS), 2)
        self.assertIs(base.on_trade, original_on_trade)

    def test_structural_tp_eligibility_not_modified(self):
        for node in ast.walk(AST):
            if not isinstance(node, (ast.Assign, ast.AugAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            self.assertFalse(any(isinstance(t, ast.Attribute)
                                 and t.attr == "take_profit_eligible"
                                 for t in targets))

if __name__ == "__main__":
    unittest.main()

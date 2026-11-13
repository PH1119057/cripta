"""Read-only P90L90 3-window forensic units in a clean source-tree interpreter.

The repo-wide test collector also uses generated scripts.research stubs, hence
the isolated interpreter. No server paths, database or network are accessed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = r'''
from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal as D
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path.cwd()))
f = importlib.import_module("scripts.research.p90l90_unified_portfolio_forensic_v1")


def row(net, reason, symbol="APTUSDT", side="LONG", entry="100", exit_="200"):
    return {
        "net_usdt": net,
        "entry_fee_usdt": "0.55",
        "exit_fee_usdt": "0.59",
        "exit_reason": reason,
        "symbol": symbol,
        "side": side,
        "entry_ts": entry,
        "exit_ts": exit_,
    }


def test_pure_after_fees_and_breakeven_stop_math():
    data = [row("-101.14", "HARD_STOP"), row("4.18", "PROTECTED_STOP")]
    got = f.totals(data)
    assert got["hardstops"] == 1
    assert got["closed"] == 2
    assert got["profitable"] == 1 and got["losing"] == 1
    assert got["hardstop_rate_pct"] == "50.0000"
    assert got["hardstop_net_usdt"] == "-101.14"
    assert got["other_exits_net_usdt"] == "4.18"
    assert got["closed_net_after_fees_usdt"] == "-96.96"
    assert got["modeled_fees_usdt"] == "2.28"
    assert got["gross_before_fees_usdt"] == "-94.68"
    assert abs(D(got["arithmetic_breakeven_hardstop_rate_pct"])
               - D("3.9689")) < D("0.001")


def test_period_pins_have_distinct_v2_leaves_and_schemas():
    assert set(f.ROOTS) == {"29d", "90d", "183d"}
    assert len(set(map(str, f.ROOTS.values()))) == 3
    assert all(path.name.endswith("_20261113_v2") for path in f.ROOTS.values())
    assert all(len(sha) == 40 for sha in f.PINNED_HEADS.values())
    assert f.FOCUS == "ORIGINAL_0TO15__fixed__SECOND48"
    assert f.START_UTC["183d"] == dt.date(2025, 4, 1)


def test_hardstop_correlation_preexisting_open_positions():
    group = [
        row("-101.14", "HARD_STOP", "APTUSDT", "LONG", "100", "300"),
        row("-101.14", "HARD_STOP", "ARBUSDT", "LONG", "150", "320"),
        row("3.25", "PROTECTED_STOP", "INJUSDT", "SHORT"),
    ]
    got = f.clusters(group)
    assert len(got) == 1
    assert got[0]["stops"] == 2
    assert got[0]["all_open_before_first_stop"]
    assert got[0]["net_after_fees_usdt"] == "-202.28"
    group.append(row("-101.14", "HARD_STOP", "LTCUSDT", "LONG", "301", "400"))
    assert not f.clusters(group)[0]["all_open_before_first_stop"]


def test_time_series_end_of_day_and_denial_deltas():
    daily = [
        {"date": "2025-04-01", "accepted": 3, "stops": 0, "denials": 0,
         "closed_net_change": D("5.00"), "end_equity": D("1005")},
        {"date": "2025-04-02", "accepted": 0, "stops": 2, "denials": 7,
         "closed_net_change": D("-200"), "end_equity": D("805")},
        {"date": "2025-05-01", "accepted": 0, "stops": 1, "denials": 30,
         "closed_net_change": D("-700"), "end_equity": D("105")},
    ]
    result = f.timeline(daily)
    assert result["last_entry_utc_day"] == "2025-04-01"
    assert result["first_capital_denial_utc_day"] == "2025-04-02"
    assert result["first_marked_equity_below_200_utc_day"] == "2025-05-01"
    assert result["months"]["2025-04"]["capital_denials"] == 7
    assert result["months"]["2025-04"]["closed_net_after_fees_usdt"] == "-195.00"
    assert result["months"]["2025-05"]["end_marked_equity_usdt"] == "105.00"


for name, value in sorted(globals().copy().items()):
    if name.startswith("test_") and callable(value):
        value()
'''


def test_forensic_reader_pure_reproducibility_no_server_dependency():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-B", "-c", SCRIPT],
        cwd=root, capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

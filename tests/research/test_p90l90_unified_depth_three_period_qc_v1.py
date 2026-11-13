"""Isolated research unit suite; the repo-wide test collector stubs scripts.research."""
import subprocess
import sys
from pathlib import Path

SCRIPT = r'''"""Independent financial unit audit for unified portfolio H9 depth research #638."""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

# Source-tree research namespace is not part of the installed wheel.
sys.path.insert(0, str(Path.cwd()))
qc = importlib.import_module("scripts.research.p90l90_unified_depth_three_period_qc_v1")

def row(side="SHORT", entry="100", exit_="98", margin="200",
        maker=False, reason="HARD_STOP"):
    return {
        "side": side, "entry_price": entry, "exit_price": exit_,
        "margin_usdt": margin, "notional_usdt": str(D(margin) * 5),
        "maker_exit": maker, "exit_reason": reason,
    }


def test_fixed_margin_short_win_after_all_taker_fees():
    gross, fee_in, fee_out, net = qc.trade_calculation(row())
    assert gross == D("20.000000000000")
    assert fee_in == D("0.550000000000")
    assert fee_out == D("0.539000000000")
    assert net == D("18.911000000000")


def test_resting_maker_long_fee():
    gross, fee_in, fee_out, net = qc.trade_calculation(
        row(side="LONG", exit_="102", maker=True, reason="RESTING_TP")
    )
    assert gross == D("20.000000000000")
    assert fee_in == D("0.550000000000")
    assert fee_out == D("0.204000000000")
    assert net == D("19.246000000000")


def test_reject_wrong_side_leverage_or_maker_classification():
    with pytest.raises(ValueError, match="native trade"):
        qc.trade_calculation({**row(), "notional_usdt": "100"})
    with pytest.raises(ValueError, match="maker classification"):
        qc.trade_calculation(row(maker=True, reason="HARD_STOP"))
    with pytest.raises(ValueError, match="maker classification"):
        qc.trade_calculation(row(maker=False, reason="RESTING_TP"))
    with pytest.raises(ValueError, match="native trade"):
        qc.trade_calculation(row(side="INVALID"))


def test_unique_result_leaf_and_independent_verification_semantics():
    assert qc.STATUS == "INDEPENDENT_QC_PASS_P90L90_UNIFIED_DEPTH_THREE_PERIODS_V2"
    assert qc.run.RESEARCH.startswith("P90L90_UNIFIED_DEPTH0")
    assert qc.run.WINDOW["183d"]["days"] == 183
    assert len(qc.run.BOOK_NAMES) == 8
    assert qc.run.RESERVED_MARGIN_EPSILON == D('0.000000000001')



def test_missing_zero_denial_counter_is_valid():
    qc.verify_capital_denials({}, 0)
    qc.verify_capital_denials({"capital_blocked_candidate": 0}, 0)
    qc.verify_capital_denials({"capital_blocked_candidate": 13}, 13)


def test_missing_nonzero_or_inconsistent_denial_counter_rejected():
    with pytest.raises(ValueError, match="capital denials"):
        qc.verify_capital_denials({}, 1)
    with pytest.raises(ValueError, match="capital denials"):
        qc.verify_capital_denials({"capital_blocked_candidate": 7}, 6)
    with pytest.raises(ValueError, match="capital denials"):
        qc.verify_capital_denials({"capital_blocked_candidate": 0}, 2)


def test_reviewed_audit_has_read_only_preflight_and_pinned_finalizer():
    import inspect
    from scripts.research import p90l90_unified_depth_three_period_qc_finalize_v2 as fin
    params = inspect.signature(qc.audit).parameters
    assert "dry_run" in params
    assert "auditor_head" in params
    assert fin.PRODUCER_HEAD == "d90e0a93117c5e7edc99ef6bd6ad3b0423e6c7ea"
    assert fin.ROOT.name == "p90l90_unified_depth0_vs_0to15_capital_20261113_v2"
    assert fin.PRODUCER != fin.AUDITOR.parents[2]

for _name in sorted(name for name in globals() if name.startswith('test_')):
    globals()[_name]()
'''


def test_source_tree_research_in_clean_python_interpreter():
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=root, capture_output=True,
        text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr

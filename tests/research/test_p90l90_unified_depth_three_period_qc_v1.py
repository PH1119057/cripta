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


def test_90day_independent_qc_finalizer_is_separate_from_183day():
    from scripts.research import p90l90_unified_depth_90d_qc_finalize_v1 as fin
    from scripts.research import p90l90_unified_depth_three_period_qc_finalize_v2 as prior
    assert fin.PRODUCER_HEAD == "500e4b381642d98d117c5b57f62fd81418156cb7"
    assert fin.ROOT.parent.name == "results"
    assert "90d_warmup" in str(fin.ROOT)
    assert fin.ROOT != prior.ROOT
    assert fin.PRODUCER != prior.PRODUCER
    assert fin.AUDITOR.name == "p90l90_unified_depth_three_period_qc_v1.py"
    assert fin.GIB == 1024 ** 3



def test_qc_crash_keeps_native_result_and_exact_stderr_on_data_disk():
    import json
    import tempfile
    from unittest.mock import patch
    from subprocess import CompletedProcess

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "result.json").write_text("already completed", encoding="utf-8")
        (root / "trade_ledger.jsonl").write_text("immutable ledger", encoding="utf-8")
        failure = CompletedProcess([], 1, "partial", "Traceback: KeyError: missing_count")
        with patch.object(qc.run, "output", return_value=root):
            with patch.object(qc.run.subprocess, "run", return_value=failure) as child:
                with pytest.raises(RuntimeError, match="immutable qc_failure.json"):
                    qc.run.independent_qc("90d", root, "a" * 40)
        assert child.call_args.kwargs["cwd"] == str(qc.run.SOURCE)
        assert child.call_args.args[0][:4] == [
            sys.executable, "-B", "-m",
            "scripts.research.p90l90_unified_depth_three_period_qc_v1",
        ]
        report = json.loads((root / "qc_failure.json").read_text())
        assert report["failure_kind"] == "EXIT_CODE_1"
        assert "KeyError: missing_count" in report["stderr_or_exception_tail"]
        assert report["result_exists"] and report["ledger_exists"]
        assert not report["qc_exists"]
        assert (root / "result.json").read_text() == "already completed"
        assert (root / "trade_ledger.jsonl").read_text() == "immutable ledger"
        # Never overwrite the first failure with a misleading second reason.
        with patch.object(qc.run, "output", return_value=root):
            with patch.object(qc.run.subprocess, "run",
                              return_value=CompletedProcess([], 2, "", "another")):
                with pytest.raises(RuntimeError):
                    qc.run.independent_qc("90d", root, "a" * 40)
        assert json.loads((root / "qc_failure.json").read_text()) == report


def test_qc_child_pass_requires_persisted_and_hashed_qc_artifact():
    import json
    import tempfile
    from unittest.mock import patch
    from subprocess import CompletedProcess

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        good = {"status": qc.STATUS, "cases": 8, "period": "90d"}
        child = CompletedProcess([], 0, json.dumps(good) + "\n", "")
        with patch.object(qc.run, "output", return_value=root):
            with patch.object(qc.run.subprocess, "run", return_value=child):
                with pytest.raises(RuntimeError, match="output validation failed"):
                    qc.run.independent_qc("90d", root, "b" * 40)
        recorded = json.loads((root / "qc_failure.json").read_text())
        assert recorded["failure_kind"] in ("FileNotFoundError", "ValueError")

        # A verifiable status and independently hashed QC file permit PASS.
        (root / "qc_failure.json").unlink()
        (root / "qc.json").write_text(json.dumps(good), encoding="utf-8")
        status = {
            "stage": "COMPLETED_INDEPENDENT_QC_PASS",
            "qc_sha256": qc.run.sha(root / "qc.json"),
        }
        (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
        with patch.object(qc.run, "output", return_value=root):
            with patch.object(qc.run.subprocess, "run", return_value=child):
                assert qc.run.independent_qc("90d", root, "b" * 40) == good
        assert not (root / "qc_failure.json").exists()


def test_qc_worker_is_linux_only_and_supports_reentry_without_replay():
    import inspect
    assert callable(qc.main)
    assert "subprocess.run" in inspect.getsource(qc.run.independent_qc)
    assert "run.source_head()" in inspect.getsource(qc.main)
    assert "COMPLETE_PENDING_INDEPENDENT_QC" in inspect.getsource(qc.main)
    assert "fcntl.flock" in inspect.getsource(qc.main)

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

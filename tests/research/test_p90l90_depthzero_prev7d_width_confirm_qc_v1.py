"""Independent fee/QC contract for 2026 90d+29d replication #633."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.research import p90l90_depthzero_prev7d_width_confirm_qc_v1 as qc


def row(side="LONG", entry="100", exit_="102", maker=True, reason="RESTING_TP"):
    return {
        "entry_price": entry, "exit_price": exit_, "side": side,
        "maker_exit": maker, "exit_reason": reason,
    }


def test_maker_exit_fee_and_net_after_fees():
    gross, fee_in, fee_out, net = qc.net_components(row())
    assert gross == D("0.200000000000")
    assert fee_in == D("0.005500000000")
    assert fee_out == D("0.002040000000")
    assert net == D("0.192460000000")


def test_conservative_taker_exit_and_short_gross():
    gross, fee_in, fee_out, net = qc.net_components(
        row(side="SHORT", entry="100", exit_="98", maker=False, reason="HARD_STOP")
    )
    assert gross == D("0.200000000000")
    assert fee_in == D("0.005500000000")
    assert fee_out == D("0.005390000000")
    assert net == D("0.189110000000")


def test_maker_only_resting_tp():
    with pytest.raises(ValueError, match="maker classification"):
        qc.net_components(row(maker=True, reason="HARD_STOP"))
    with pytest.raises(ValueError, match="maker classification"):
        qc.net_components(row(maker=False, reason="RESTING_TP"))


def test_side_and_price_fail_closed():
    with pytest.raises(ValueError, match="position side"):
        qc.net_components(row(side="OTHER"))
    with pytest.raises(ValueError, match="nonpositive"):
        qc.net_components(row(entry="0"))


def test_exact_frozen_names_periods_and_full_audit_contract():
    assert len(qc.EXPECT) == 37
    assert qc.EXPECT.issuperset({
        "REFERENCE", "w080_SHORT_ONLY_SECOND48", "w090_SHORT_ONLY_SECOND48"
    })
    assert qc.TOTAL_DAYS == {"90d": 90, "29d": 29}
    assert qc.LEAF == "p90l90_depthzero_prev7d_width_confirm_20261112_v1"
    assert qc.Q == D("0.000000000001")
    assert str(qc.ROOT["90d"]).replace("\\", "/").startswith("/data/")
    assert str(qc.ROOT["29d"]).replace("\\", "/").startswith("/data/")


def test_qc_never_claims_real_fills():
    import inspect

    src = inspect.getsource(qc.audit)
    assert src.find("source_cohorts(period, report)") >= 0
    assert src.find("net_components(row)") >= 0
    assert src.find("expected_reference(period)") >= 0
    assert src.find("COMPLETED_INDEPENDENT_QC_PASS") >= 0
    assert src.find("status.json.tmp") >= 0

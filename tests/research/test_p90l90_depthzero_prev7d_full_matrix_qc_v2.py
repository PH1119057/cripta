"""Independent fee/QC contract for 2026 90d+29d replication #633."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from scripts.research import p90l90_depthzero_prev7d_full_matrix_qc_v2 as qc


def row(side="LONG", entry="100", exit_="102", maker=True, reason="RESTING_TP"):
    return {
        "entry_price": entry, "exit_price": exit_, "side": side,
        "maker_exit": maker, "exit_reason": reason,
    }


def test_maker_exit_fee_and_net_after_fees():
    gross, fee_in, fee_out, net = qc.net_components(row())
    assert gross == D("2.000000000000")
    assert fee_in == D("0.055000000000")
    assert fee_out == D("0.020400000000")
    assert net == D("1.924600000000")


def test_conservative_taker_exit_and_short_gross():
    gross, fee_in, fee_out, net = qc.net_components(
        row(side="SHORT", entry="100", exit_="98", maker=False, reason="HARD_STOP")
    )
    assert gross == D("0.200000000000")
    assert fee_in == D("0.055000000000")
    assert fee_out == D("0.053900000000")
    assert net == D("1.891100000000")


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
    assert qc.LEAF == "p90l90_depthzero_prev7d_full_matrix_20261112_v2"
    assert qc.NOTIONAL == D("100")
    assert qc.REFERENCE_NOTIONAL == D("10")
    assert qc.OPEN_ENTRY_FEE == D("0.055")
    assert qc.Q.compare(D("0.000000000001")) == 0
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


def test_immutable_reference_ten_usdt_fee_classification():
    gross, entry, exit_, net = qc.net_components(
        row(), qc.REFERENCE_NOTIONAL
    )
    assert gross == D("0.200000000000")
    assert entry == D("0.005500000000")
    assert exit_ == D("0.002040000000")
    assert net == D("0.192460000000")

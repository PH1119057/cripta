from decimal import Decimal

import pytest

from bybit_workbench.closed_trade_reporting import (
    merge_evidenced_closed_trades,
    owner_controlled_closed_cycles,
    recovered_closed_cycles,
)


def test_owner_control_trade_exact_close_is_not_strategy_r1():
    entry = [("web-test-LTCUSDT-1791480302083", "LTCUSDT", "{}", "entry-order",
              "entry-exec", "Buy", "0.1", "61.24", "0.0033682", 1791480307009)]
    exit_rows = [("LTCUSDT", "exit-order", "exit-exec", "Sell", "0.1", "60.93",
                  "0.00335115", 1791480412314, {"closedSize":"0.1"})]
    rows = owner_controlled_closed_cycles(entry, exit_rows)
    assert len(rows) == 1
    assert rows[0]["provenance"] == "OWNER_CONTROL"
    assert rows[0]["strategy_id"] is None
    assert Decimal(str(rows[0]["net_pnl"])) == Decimal("-0.03771935")
    assert owner_controlled_closed_cycles(entry, exit_rows + exit_rows) == []


def test_recovered_inj_without_strategy_attribution():
    evidence = {"source":"EXACT_BYBIT_EXECUTION_HISTORY",
                "exit_execution_ids":["inj-exit"],
                "closed_at_epoch_ms":1791463751402}
    raw = [("cap-fa7d115", "INJUSDT", "Buy", "1.3", "7.257",
            "0.0019032", "0.00518876", "-0.0819", "-0.08899196",
            evidence, None)]
    row = recovered_closed_cycles(raw)[0]
    assert row["provenance"] == "RECOVERED_UNOWNED"
    assert row["strategy_id"] is None
    assert Decimal(str(row["net_pnl"])) == Decimal("-0.08899196")


def test_same_exit_never_counts_twice():
    owned = {"symbol":"LTCUSDT","closed_at_epoch_ms":123,
             "exec_ids":["exit-exec"],"strategy_id":"r1_ltcusdt",
             "strategy_version":"1.1"}
    control = {"symbol":"LTCUSDT","closed_at_epoch_ms":123,
               "exec_ids":["exit-exec"],"strategy_id":None}
    result = merge_evidenced_closed_trades([owned],[],[control])
    assert len(result) == 1
    assert result[0]["provenance"] == "STRATEGY"


def test_unproven_recovery_does_not_enter_operator_ledger():
    assert recovered_closed_cycles([("res","INJUSDT","Buy","1","1","0","0","0","0",
          {"source":"UNKNOWN","exit_execution_ids":["x"],"closed_at_epoch_ms":1},None)]) == []


# Issue #214: these are deliberate strict XFAIL regressions, not evidence of
# report/replay parity. The upstream caller still strips account provenance,
# and the owner-visible treatment of unowned history needs an owner decision.


@pytest.mark.xfail(
    strict=True,
    reason="#214 current operator ledger globally deduplicates provider exec_id",
)
def test_r10_proven_accounts_with_same_provider_exec_id_remain_separate():
    common = {
        "symbol": "BTCUSDT",
        "closed_at_epoch_ms": 1800000000000,
        "exec_ids": ["provider-local-exec-1"],
        "provenance": "STRATEGY",
    }
    main = dict(common, identity_status="PROVEN", trading_account_id="ta-test-main")
    sub = dict(common, identity_status="PROVEN", trading_account_id="ta-test-sub")
    result = merge_evidenced_closed_trades([main, sub], [], [])
    assert len(result) == 2
    assert {row["trading_account_id"] for row in result} == {
        "ta-test-main", "ta-test-sub"
    }


@pytest.mark.xfail(
    strict=True,
    reason="#214 control close pairing currently drops account identity in rows",
)
def test_r10_control_close_must_not_pair_different_proven_accounts():
    # The extra two columns represent the SQL reader's verified
    # identity_status and trading_account_id, NOT fields inferred from
    # provider event payload or from symbol/time.
    entries = [
        (
            "control-main", "BTCUSDT", "{}", "entry-order", "entry-exec",
            "Buy", "1", "100", "0.1", 1000, "PROVEN", "ta-test-main",
        )
    ]
    exit_rows = [
        (
            "BTCUSDT", "exit-order", "exit-exec", "Sell", "1", "110",
            "0.1", 2000, {"closedSize": "1"}, "PROVEN", "ta-test-sub",
        )
    ]
    assert owner_controlled_closed_cycles(entries, exit_rows) == []

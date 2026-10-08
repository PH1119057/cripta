from decimal import Decimal

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

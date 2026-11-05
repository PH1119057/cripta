"""F01 actual recovery/ownership/Exit projection on disposable native PostgreSQL.

Signed Exchange facts are fixtures; production proof and ownership SQL are not
mocked. No real credentials, network requests, production DB or persistent writes.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.entry_cycle_recovery import RecoveryInventory
from bybit_workbench.universal_exit.loader import load_exit_binding

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operations/connectivity"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research/server/connectivity"))
import private_runtime as runtime  # noqa: E402
from test_strategy_position_binding_postgres import NOW, _payload, _seed_request  # noqa: E402

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL required")
FILL_MS = int(NOW.timestamp() * 1000)
NOW_MS = FILL_MS + 3000


class Transaction:
    """Production worker commits cannot escape this disposable transaction."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def commit(self):
        pass

    def rollback(self):
        raise AssertionError("unexpected recovery rollback")


@pytest.fixture
def pg():
    assert DSN is not None
    connection = psycopg.connect(DSN)
    assert connection.info.dbname != "cripta", "production DB forbidden"
    try:
        # Legacy table is not part of the old disposable schema bootstrap.
        connection.execute("""CREATE TABLE IF NOT EXISTS runtime.entry_geometry_bindings (
            entry_command_id text,geometry_handoff_id text,signal_id text,
            bot_instance_id text,strategy_id text,strategy_version text)""")
        connection.execute("GRANT SELECT ON runtime.entry_geometry_bindings TO cripta")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def seed(connection, prefix, *, account=None, symbol="F01USDT", partial=False, protected=True):
    ids = _seed_request(connection, prefix, reservation_state="DISPATCHED", dispatched=True,
                        symbol=symbol, account_id=account)
    account = ids["trading_account_id"]
    payload = _payload(ids)
    payload["initial_protection"] = {
        "stop_loss_enabled": True, "stop_loss_pct": "10",
        "take_profit_enabled": True, "take_profit_price": "130",
        "trigger_by": "LastPrice", "tpsl_mode": "Full",
    }
    connection.execute("UPDATE runtime.trade_commands SET state='completed',payload_json=%s "
                       "WHERE command_id=%s", (json.dumps(payload), ids["command_id"]))
    order_id, exec_id = prefix + "-order", prefix + "-exec"
    fill = {"execId": exec_id, "orderId": order_id, "orderLinkId": ids["command_id"],
            "symbol": symbol, "side": "Buy", "execType": "Trade", "execQty": "1.25",
            "execPrice": "125", "execFee": "0", "execTime": str(FILL_MS),
            "closedSize": "0", "seq": "10"}
    order = {"orderId": order_id, "orderLinkId": ids["command_id"], "symbol": symbol,
             "side": "Buy", "positionIdx": 0, "cumExecQty": "1.25", "avgPrice": "125"}
    position = {"symbol": symbol, "side": "Buy", "positionIdx": 0,
                "size": ".75" if partial else "1.25", "avgPrice": "125",
                "stopLoss": "112.5" if protected else "0", "takeProfit": "131",
                "trailingStop": "0", "seq": "20", "updatedTime": str(FILL_MS + 1000)}
    connection.execute("""INSERT INTO runtime.executions(
        exec_id,order_id,order_link_id,symbol,side,exec_qty,exec_price,exec_fee,
        exec_time_ms,received_at_epoch_ms,payload_json,trading_account_id,identity_status)
        VALUES(%s,%s,%s,%s,'Buy','1.25','125','0',%s,%s,%s,%s,'PROVEN')""",
        (exec_id, order_id, ids["command_id"], symbol, FILL_MS, NOW_MS,
         json.dumps(fill), account))
    connection.execute("""INSERT INTO runtime.hot_positions(
        symbol,position_idx,side,size,entry_price,leverage,refreshed_at_epoch_ms,
        payload_json,trading_account_id,identity_status)
        VALUES(%s,0,'Buy',%s,'125','1',%s,%s,%s,'PROVEN')""",
        (symbol, position["size"], NOW_MS, json.dumps(position), account))
    executions, orders = [fill], {order_id: order}
    if partial:
        close_order = prefix + "-close"
        executions.append({**fill, "execId": prefix + "-reduction", "orderId": close_order,
                           "orderLinkId": "exit", "side": "Sell", "execQty": ".5",
                           "closedSize": ".5", "execTime": str(FILL_MS + 500), "seq": "15"})
        orders[close_order] = {"orderId": close_order, "orderLinkId": "exit", "symbol": symbol,
                               "side": "Sell", "positionIdx": 0}
    inventory = RecoveryInventory(account, prefix + "-connector", FILL_MS, NOW_MS, NOW_MS,
                                  (position,), tuple(executions), orders)
    # Complete immutable ExitPlan fixture for the real loader after recovery.
    plan = {"strategy_id": ids["strategy_id"], "strategy_version": "1",
            "strategy_config_fingerprint": ids["strategy_fp"],
            "exit_plan_fingerprint": ids["exit_fp"], "exit_policy": {}, "protection_policy": {}}
    connection.execute("UPDATE strategy_entry.exit_plans SET plan_json=%s::jsonb "
                       "WHERE exit_plan_fingerprint=%s", (json.dumps(plan), ids["exit_fp"]))
    return ids, inventory


def configure(monkeypatch, connection, inventory):
    monkeypatch.setattr(runtime, "TRADING_ACCOUNT_ID", inventory.account)
    monkeypatch.setattr(runtime, "ACCOUNT_CONNECTOR_ID", inventory.connector)
    monkeypatch.setattr(runtime, "_f01_recovery_denials", {})
    monkeypatch.setattr(runtime, "_f01_signed_inventory", lambda *_a: inventory)
    monkeypatch.setattr(runtime, "atomic_status", lambda *_a: None)
    monkeypatch.setattr(runtime, "cancel_expired_entry_limits", lambda *_a: None)
    monkeypatch.setattr(runtime, "db", lambda *_a: Transaction(connection))

    def stop(_seconds):
        runtime.running = False

    monkeypatch.setattr(runtime, "time", SimpleNamespace(
        time=lambda: NOW_MS / 1000, monotonic=lambda: 1, sleep=stop,
    ))

    def no_network(*_a, **_kw):
        raise AssertionError("unexpected real Exchange access")

    monkeypatch.setattr(runtime, "api_get", no_network)
    monkeypatch.setattr(runtime, "api_post", no_network)


@pytest.mark.parametrize("partial", (False, True))
def test_worker_reconstructs_exact_universal_owner_and_exit_remainder(pg, monkeypatch, partial):
    prefix = "f01-" + uuid4().hex[:10]
    ids, inventory = seed(pg, prefix, partial=partial)
    configure(monkeypatch, pg, inventory)
    pg.execute("SET LOCAL ROLE cripta")
    assert pg.execute("SELECT current_user").fetchone()[0] == "cripta"
    for _restart in range(2):
        monkeypatch.setattr(runtime, "running", True)
        runtime.command_worker_loop("unused", "unused")
    row = pg.execute("""SELECT position_id,actual_qty,execution_ids,exchange_order_ids,
                               entry_command_id,state FROM runtime.position_ownership
                         WHERE entry_command_id=%s""", (ids["command_id"],)).fetchone()
    assert row is not None
    assert row[1:] == (Decimal("1.25"), [prefix + "-exec"], [prefix + "-order"],
                       ids["command_id"], "OPEN")
    with pg.cursor(row_factory=dict_row) as cursor:
        class MappingConnection:
            def execute(self, *args):
                cursor.execute(*args)
                return cursor
        position, _plan = load_exit_binding(MappingConnection(), strategy_position_id=row[0])
    assert position.actual_qty == Decimal(".75" if partial else "1.25")
    assert pg.execute("SELECT count(*) FROM runtime.position_ownership WHERE entry_command_id=%s",
                      (ids["command_id"],)).fetchone()[0] == 1
    assert pg.execute("SELECT count(*) FROM runtime.trade_commands WHERE command_type="
                      "'initial_protection' AND trading_account_id=%s",
                      (inventory.account,)).fetchone()[0] == 0


def test_sixty_actual_positions_restart_idempotently_and_never_hold_exit(pg, monkeypatch):
    batch = uuid4().hex[:8]
    for leg in range(2):
        inventories, commands = [], []
        account = None
        for index in range(30):
            prefix = f"f{leg}{index}-{batch}"
            ids, inventory = seed(pg, prefix, account=account,
                                  symbol=f"F{leg}{index}{batch.upper()}USDT",
                                  partial=index % 2 == 0)
            account = inventory.account
            inventories.append(inventory)
            commands.append(ids["command_id"])
        complete = replace(inventories[0],
            positions=tuple(p for inv in inventories for p in inv.positions),
            executions=tuple(e for inv in inventories for e in inv.executions),
            orders={k: v for inv in inventories for k, v in inv.orders.items()})
        configure(monkeypatch, pg, complete)
        pg.execute("SET LOCAL ROLE cripta")
        for _restart in range(2):
            monkeypatch.setattr(runtime, "running", True)
            runtime.command_worker_loop("unused", "unused")
        count = pg.execute("SELECT count(*) FROM runtime.position_ownership WHERE "
                           "entry_command_id=ANY(%s) AND state='OPEN'", (commands,)).fetchone()[0]
        assert count == 30
        pg.execute("RESET ROLE")
    # A queued Exit must dispatch without invoking even one history proof.
    pg.execute("UPDATE runtime.trade_commands SET state='queued' WHERE command_id=%s",
               (commands[0],))
    exit_id = "exit-" + batch
    pg.execute("INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,"
               "state,requested_at_epoch_ms,trading_account_id,identity_status) "
               "VALUES(%s,'close',%s,'{}','queued',0,%s,'PROVEN')",
               (exit_id, complete.positions[0]["symbol"], complete.account))

    def no_proof(*_a):
        raise AssertionError("queued Exit was delayed by history recovery")

    observed = []

    def dispatch(_connection, _key, _secret, row):
        observed.append(row[0])
        runtime.running = False

    monkeypatch.setattr(runtime, "_f01_current_cycle", no_proof)
    monkeypatch.setattr(runtime, "execute_command", dispatch)
    monkeypatch.setattr(runtime, "running", True)
    pg.execute("SET LOCAL ROLE cripta")
    runtime.command_worker_loop("unused", "unused")
    assert observed == [exit_id]


@pytest.mark.parametrize("bad", ("side", "qty", "reopened", "lineage"))
def test_actual_worker_and_prequeued_protection_reject_unrelated_cycle(pg, monkeypatch, bad):
    prefix = "f01n-" + uuid4().hex[:8]
    ids, inventory = seed(pg, prefix, protected=False)
    position = dict(inventory.positions[0])
    if bad == "side":
        position["side"] = "Sell"
    elif bad == "qty":
        position["size"] = "9"
    elif bad == "lineage":
        payload = _payload(ids)
        payload["initial_protection"] = {"stop_loss_pct": "10", "take_profit_price": "130"}
        payload["signal_id"] = "foreign-signal"
        pg.execute("UPDATE runtime.trade_commands SET payload_json=%s WHERE command_id=%s",
                   (json.dumps(payload), ids["command_id"]))
    else:
        entry = inventory.executions[0]
        close = {**entry, "execId": prefix + "-closed", "orderId": prefix + "-close",
                 "orderLinkId": "manual-close", "side": "Sell", "closedSize": "1.25",
                 "execTime": str(FILL_MS + 500), "seq": "11"}
        reopened = {**entry, "execId": prefix + "-reopened", "orderId": prefix + "-reopen",
                    "orderLinkId": "manual-open", "execTime": str(FILL_MS + 700), "seq": "12"}
        inventory = replace(inventory, executions=(*inventory.executions, close, reopened),
            orders={**inventory.orders,
                    close["orderId"]: {"orderId": close["orderId"], "symbol": ids["symbol"],
                                       "side": "Sell", "positionIdx": 0},
                    reopened["orderId"]: {**inventory.orders[entry["orderId"]],
                                          "orderId": reopened["orderId"],
                                          "orderLinkId": "manual-open"}})
    inventory = replace(inventory, positions=(position,))
    configure(monkeypatch, pg, inventory)
    pg.execute("SET LOCAL ROLE cripta")
    monkeypatch.setattr(runtime, "running", True)
    runtime.command_worker_loop("unused", "unused")
    assert pg.execute("SELECT count(*) FROM runtime.position_ownership WHERE entry_command_id=%s",
                      (ids["command_id"],)).fetchone()[0] == 0
    assert pg.execute("SELECT count(*) FROM runtime.trade_commands WHERE command_type="
                      "'initial_protection' AND trading_account_id=%s",
                      (inventory.account,)).fetchone()[0] == 0
    parent = pg.execute("SELECT payload_json FROM runtime.trade_commands WHERE command_id=%s",
                        (ids["command_id"],)).fetchone()[0]
    child = {"entry_command_id": ids["command_id"],
             "initial_protection": json.loads(parent)["initial_protection"]}
    child_id = prefix + "-queued"
    pg.execute("""INSERT INTO runtime.trade_commands(
        command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms,
        trading_account_id,identity_status)
        VALUES(%s,'initial_protection',%s,%s,'running',0,%s,'PROVEN')""",
        (child_id, ids["symbol"], json.dumps(child), inventory.account))
    runtime.execute_command(Transaction(pg), "unused", "unused",
                            (child_id, "initial_protection", ids["symbol"], json.dumps(child)))
    state, result = pg.execute("SELECT state,result_json FROM runtime.trade_commands "
                               "WHERE command_id=%s", (child_id,)).fetchone()
    assert state == "failed"
    assert json.loads(result)["mutation_sent"] is False
    assert pg.execute("SELECT count(*) FROM runtime.connection_events WHERE "
                      "trading_account_id=%s AND event='F01_CURRENT_CYCLE_UNPROVEN'",
                      (inventory.account,)).fetchone()[0] > 0

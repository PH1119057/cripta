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
sys.path.append(str(Path(__file__).resolve().parents[1] / "research/server/connectivity"))
from test_strategy_position_binding_postgres import NOW, _payload, _seed_request  # noqa: E402

import operations.connectivity.private_runtime as runtime  # noqa: E402

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
        # The old CI bootstrap omits the installed worker ACL. These exact
        # rights were independently read back as cripta on production; this
        # fixture only reproduces the subset needed below, in a rolled-back DB.
        connection.execute("GRANT SELECT,INSERT,UPDATE ON runtime.trade_commands,"
                           "runtime.position_ownership TO cripta")
        connection.execute("GRANT SELECT,INSERT ON runtime.executions,"
                           "runtime.connection_events TO cripta")
        connection.execute("GRANT SELECT,UPDATE ON runtime.hot_positions,"
                           "runtime.capital_reservations,runtime.exchange_position_slot_claims "
                           "TO cripta")
        connection.execute("GRANT SELECT,INSERT ON strategy_entry.execution_request_state_events "
                           "TO cripta")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def seed(connection, prefix, *, account=None, symbol="F01USDT", partial=False, protected=True,
         shared=None):
    replacements = {} if shared is None else {
        prefix + suffix: shared[field] for suffix, field in (
            ("-strategy-fp", "strategy_fp"), ("-strategy", "strategy_id"),
            ("-activation", "activation_id"), ("-entry-fp", "entry_fp"),
            ("-exit-fp", "exit_fp"), ("-binding", "binding_id"),
        )
    }

    def translate(value):
        if isinstance(value, str):
            for old, new in replacements.items():
                value = value.replace(old, new)
        return value

    class SeedConnection:
        def execute(self, statement, params=()):
            if shared is not None and any("INSERT INTO strategy_entry." + table + "(" in statement
                for table in ("strategy_cards", "strategy_activations", "entry_plans",
                              "exit_plans", "strategy_execution_bindings")):
                return connection.execute("SELECT 1")
            params = [translate(p) for p in params]
            if "INSERT INTO strategy_entry.exit_plans(" in statement:
                # Prepare the complete immutable document BEFORE its INSERT.
                # Never disable or bypass the canonical immutability trigger.
                params = list(params)
                params[4] = json.dumps({
                    "exit_plan_fingerprint": params[0], "strategy_id": params[1],
                    "strategy_version": params[2], "strategy_config_fingerprint": params[3],
                    "exit_policy": {}, "protection_policy": {},
                })
            return connection.execute(statement, params)

    ids = _seed_request(SeedConnection(), prefix, reservation_state="DISPATCHED", dispatched=True,
                        symbol=symbol, account_id=account)
    ids = {k: translate(v) for k, v in ids.items()}
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
        exec_time_ms,received_at_epoch_ms,payload_json,trading_account_id,
        account_connector_id,identity_status)
        VALUES(%s,%s,%s,%s,'Buy','1.25','125','0',%s,%s,%s,%s,%s,'PROVEN')""",
        (exec_id, order_id, ids["command_id"], symbol, FILL_MS, NOW_MS,
         json.dumps(fill), account, prefix + "-connector"))
    connection.execute("""INSERT INTO runtime.hot_positions(
        symbol,position_idx,side,size,entry_price,leverage,refreshed_at_epoch_ms,
        payload_json,trading_account_id,account_connector_id,identity_status)
        VALUES(%s,0,'Buy',%s,'125','1',%s,%s,%s,%s,'PROVEN')""",
        (symbol, position["size"], NOW_MS, json.dumps(position), account, prefix + "-connector"))
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
        shared = None
        for index in range(30):
            prefix = f"f{leg}{index}-{batch}"
            ids, inventory = seed(pg, prefix, account=account,
                                  symbol=f"F{leg}{index}{batch.upper()}USDT",
                                  partial=index % 2 == 0, shared=shared)
            account = inventory.account
            shared = ids
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


def test_startup_uncertain_protection_is_not_replayed_after_restart(pg, monkeypatch):
    prefix = "f01r-" + uuid4().hex[:8]
    ids, inventory = seed(pg, prefix, protected=False)
    configure(monkeypatch, pg, inventory)
    calls = []

    def get(endpoint, *_a):
        if endpoint == "/v5/market/instruments-info":
            rows = [{"priceFilter": {"tickSize": "0.01"}}]
        else:
            assert endpoint == "/v5/position/list"
            rows = list(inventory.positions)
        return {"retCode": 0, "result": {"list": rows}}, 0

    def uncertain(endpoint, params, *_a):
        assert endpoint == "/v5/position/trading-stop"
        assert "takeProfit" not in params, "existing TP must survive recovery"
        assert pg.execute("SELECT count(*) FROM runtime.connection_events WHERE "
                          "event='F01_PROTECTION_MUTATION_ATTEMPT' AND trading_account_id=%s",
                          (inventory.account,)).fetchone()[0] == 1
        calls.append(params)
        raise runtime.AmbiguousBybitMutation("fixture timeout after POST, no retry")

    monkeypatch.setattr(runtime, "api_get", get)
    monkeypatch.setattr(runtime, "api_post", uncertain)
    pg.execute("SET LOCAL ROLE cripta")
    with pytest.raises(runtime.AmbiguousBybitMutation):
        runtime.protect_recovered_bot_positions(Transaction(pg), "unused", "unused")
    # Same account/Entry, same unprotected readback: still no second mutation.
    assert runtime.protect_recovered_bot_positions(Transaction(pg), "unused", "unused") == 0
    assert len(calls) == 1
    assert pg.execute("SELECT count(*) FROM runtime.position_ownership WHERE entry_command_id=%s",
                      (ids["command_id"],)).fetchone()[0] == 0


def test_existing_owner_is_quarantined_on_identity_drift_then_resumes_exact_exit(pg, monkeypatch):
    prefix = "f01q-" + uuid4().hex[:8]
    ids, inventory = seed(pg, prefix, partial=True)
    configure(monkeypatch, pg, inventory)
    pg.execute("SET LOCAL ROLE cripta")
    monkeypatch.setattr(runtime, "running", True)
    runtime.command_worker_loop("unused", "unused")
    position_id = pg.execute("SELECT position_id FROM runtime.position_ownership "
                             "WHERE entry_command_id=%s", (ids["command_id"],)).fetchone()[0]
    changed = {**inventory.positions[0], "size": "9", "seq": "21"}
    monkeypatch.setattr(runtime, "_f01_signed_inventory",
                         lambda *_a: replace(inventory, positions=(changed,)))
    runtime.reconcile_position_ownership(Transaction(pg), [changed], NOW_MS,
                                         key="unused", secret="unused")
    state = pg.execute("SELECT state,close_link_status,actual_qty FROM runtime.position_ownership "
                       "WHERE position_id=%s", (position_id,)).fetchone()
    assert state == ("RECONCILIATION_REQUIRED", "F01_CURRENT_CYCLE_UNPROVEN", Decimal("1.25"))
    monkeypatch.setattr(runtime, "_f01_signed_inventory", lambda *_a: inventory)
    runtime.reconcile_position_ownership(Transaction(pg), list(inventory.positions), NOW_MS,
                                         key="unused", secret="unused")
    assert pg.execute("SELECT state,close_link_status,actual_qty FROM runtime.position_ownership "
                      "WHERE position_id=%s", (position_id,)).fetchone() == (
                          "OPEN", "OPEN", Decimal("1.25"))
    # The missing partial close was retained as an exact fact, not just a
    # derived quantity; terminal close accounting can still consume all fills.
    quantity = pg.execute(
        "SELECT exec_qty FROM runtime.executions WHERE trading_account_id=%s AND exec_id=%s",
        (inventory.account, prefix + "-reduction"),
    ).fetchone()[0]
    assert Decimal(str(quantity)) == Decimal(".5")

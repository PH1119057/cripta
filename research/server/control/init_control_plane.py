from __future__ import annotations

import json

import psycopg

BOTS = (
    ("BOT-01", "Стратегия 1"), ("BOT-02", "Стратегия 2"),
    ("BOT-03", "Стратегия 3"), ("BOT-04", "Стратегия 4"),
)
EMPTY_STATS = {"signals": 0, "orders": 0, "fills": 0, "wins": 0, "losses": 0, "net_pnl": 0, "fees": 0, "slippage_bps": None, "latency_p50_ms": None}


def main() -> None:
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    statements = (
        "CREATE SCHEMA IF NOT EXISTS control",
        """CREATE TABLE IF NOT EXISTS control.bots(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, strategy TEXT NOT NULL, mode TEXT NOT NULL,
            desired_state TEXT NOT NULL, actual_state TEXT NOT NULL, executable TEXT NOT NULL,
            mainnet_approved SMALLINT NOT NULL DEFAULT 0, symbols_json TEXT NOT NULL,
            stats_json TEXT NOT NULL, updated_at_epoch BIGINT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS control.bot_events(
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at_epoch_ms BIGINT NOT NULL,
            bot_id TEXT NOT NULL, action TEXT NOT NULL, result TEXT NOT NULL, details_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS control.execution_gates(
            mode TEXT PRIMARY KEY, enabled SMALLINT NOT NULL, reason TEXT NOT NULL, updated_at_epoch_ms BIGINT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS control.command_requests(
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            bot_id TEXT NOT NULL, mode TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL,
            state TEXT NOT NULL, created_at_epoch_ms BIGINT NOT NULL, claimed_at_epoch_ms BIGINT,
            completed_at_epoch_ms BIGINT, result_json TEXT NOT NULL, error TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS control.command_timings(
            command_id BIGINT PRIMARY KEY REFERENCES control.command_requests(id), signal_at_epoch_ns BIGINT,
            queued_at_epoch_ns BIGINT, claimed_at_epoch_ns BIGINT, send_at_epoch_ns BIGINT,
            accepted_at_epoch_ns BIGINT, filled_at_epoch_ns BIGINT)""",
    )
    for statement in statements:
        connection.execute(statement)
    for bot_id, name in BOTS:
        connection.execute("""INSERT INTO control.bots(id,name,strategy,mode,desired_state,actual_state,executable,
            mainnet_approved,symbols_json,stats_json,updated_at_epoch) VALUES(%s,%s,'не назначена','shadow','stopped',
            'not-configured','',0,'[]',%s,0) ON CONFLICT(id) DO NOTHING""", (bot_id, name, json.dumps(EMPTY_STATS)))
    for mode, enabled, reason in (
        ("shadow", 1, "локальная симуляция без отправки на Bybit"),
        ("testnet", 0, "нет отдельного Testnet credential и допуска"),
        ("mainnet", 0, "реальное исполнение заблокировано до MICRO_LIVE допуска"),
    ):
        connection.execute("""INSERT INTO control.execution_gates(mode,enabled,reason,updated_at_epoch_ms)
            VALUES(%s,%s,%s,0) ON CONFLICT(mode) DO NOTHING""", (mode, enabled, reason))
    connection.commit()
    print("control plane initialized")


if __name__ == "__main__":
    main()

from __future__ import annotations

import psycopg


def main() -> None:
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    connection.execute("CREATE SCHEMA IF NOT EXISTS monitoring")
    connection.execute("""CREATE TABLE IF NOT EXISTS monitoring.opportunities(
        signal_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
        symbol TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('long','short')),
        signal_at_epoch_ms BIGINT NOT NULL, signal_price DOUBLE PRECISION NOT NULL CHECK(signal_price>0),
        decision TEXT NOT NULL CHECK(decision IN ('entered','skipped','blocked','shadow')),
        decision_reason TEXT NOT NULL, traffic_light TEXT NOT NULL, horizon_seconds INTEGER NOT NULL,
        state TEXT NOT NULL, last_price DOUBLE PRECISION, max_favorable_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
        max_adverse_pct DOUBLE PRECISION NOT NULL DEFAULT 0, first_hits_json TEXT NOT NULL DEFAULT '{}',
        samples BIGINT NOT NULL DEFAULT 0, finalized_at_epoch_ms BIGINT, created_at_epoch_ms BIGINT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS monitoring.opportunity_events(
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, signal_id TEXT NOT NULL REFERENCES monitoring.opportunities(signal_id),
        at_epoch_ms BIGINT NOT NULL, event TEXT NOT NULL, value_pct DOUBLE PRECISION, price DOUBLE PRECISION,
        details_json TEXT NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_opportunities_state_symbol ON monitoring.opportunities(state,symbol)")
    connection.commit()
    print("opportunity monitor initialized")


if __name__ == "__main__":
    main()

from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg

SOURCE = Path("/var/lib/cripta/safety/audit.db")


def main() -> None:
    source = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    target = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    events = source.execute("SELECT at_epoch_ms,event,severity,details_json FROM system_events ORDER BY id").fetchall()
    snapshots = source.execute("""SELECT at_epoch_ms,ok,clock_offset_ms,rest_latency_ms,public_stream_age_ms,
        total_equity,available_balance,wallet_balance,account_im_rate,account_mm_rate,open_positions,
        open_orders,position_modes_json,leverages_json,error FROM exchange_snapshots ORDER BY id""").fetchall()
    with target.transaction():
        with target.cursor() as cursor:
            cursor.executemany("""INSERT INTO safety.system_events(at_epoch_ms,event,severity,details_json)
                VALUES(%s,%s,%s,%s)""", events)
            cursor.executemany("""INSERT INTO safety.exchange_snapshots(at_epoch_ms,ok,clock_offset_ms,
                rest_latency_ms,public_stream_age_ms,total_equity,available_balance,wallet_balance,
                account_im_rate,account_mm_rate,open_positions,open_orders,position_modes_json,
                leverages_json,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", snapshots)
    print(f"events={len(events)} snapshots={len(snapshots)}")
    target.close()
    source.close()


if __name__ == "__main__":
    main()

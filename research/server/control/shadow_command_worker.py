from __future__ import annotations

import json
import time

import psycopg


def main() -> None:
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    while True:
        with connection.transaction():
            command = connection.execute("""SELECT id,mode,action,payload_json FROM control.command_requests
                WHERE state='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if command:
                command_id, mode, action, payload_json = command
                claimed_ns = time.time_ns()
                connection.execute("UPDATE control.command_requests SET state='running',claimed_at_epoch_ms=%s WHERE id=%s", (claimed_ns // 1_000_000, command_id))
                connection.execute("UPDATE control.command_timings SET claimed_at_epoch_ns=%s WHERE command_id=%s", (claimed_ns, command_id))
        if not command:
            time.sleep(0.25)
            continue
        command_id, mode, action, payload_json = command
        if mode != "shadow":
            state, result, error = "rejected", {}, "execution gate denies non-shadow command"
        else:
            state, result, error = "completed", {"simulated": True, "action": action, "payload": json.loads(payload_json)}, ""
        finished_ns = time.time_ns()
        with connection.transaction():
            connection.execute("""UPDATE control.command_requests SET state=%s,completed_at_epoch_ms=%s,
                result_json=%s,error=%s WHERE id=%s""", (state, finished_ns // 1_000_000, json.dumps(result, ensure_ascii=False), error, command_id))


if __name__ == "__main__":
    main()

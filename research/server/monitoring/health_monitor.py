from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import psycopg

STATUS = Path("/var/lib/cripta/health/status.json")
PUBLIC = Path("/var/lib/cripta/connectivity/status.json")
SAFETY = Path("/var/lib/cripta/safety/latest.json")
PRIVATE = Path("/var/lib/cripta/private_runtime/status.json")
BACKUP = Path("/var/lib/cripta/backup/latest.json")
DATA = Path("/data/cripta")


def read(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def initialize(connection: psycopg.Connection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS monitoring")
    connection.execute("""CREATE TABLE IF NOT EXISTS monitoring.health_events(
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at_epoch_ms BIGINT NOT NULL,
        state TEXT NOT NULL, issues_json TEXT NOT NULL)""")
    connection.commit()


def evaluate() -> dict[str, object]:
    now = int(time.time())
    public, safety, private, backup = read(PUBLIC), read(SAFETY), read(PRIVATE), read(BACKUP)
    issues: list[dict[str, str]] = []
    if public.get("state") != "connected": issues.append({"severity": "red", "code": "public_ws", "message": "public WebSocket не подключён"})
    public_age = now - int(public.get("updated_at_epoch") or 0)
    if public_age > 10: issues.append({"severity": "red", "code": "public_stale", "message": f"public status устарел на {public_age} с"})
    safety_age = now - int(safety.get("checked_at_epoch") or 0)
    if safety.get("state") != "healthy" or safety_age > 90: issues.append({"severity": "red", "code": "exchange_truth", "message": f"exchange truth state={safety.get('state')} age={safety_age} с"})
    private_state = (private.get("private") or {}).get("state")
    trade_state = (private.get("trade") or {}).get("state")
    if private_state != "connected": issues.append({"severity": "red", "code": "private_ws", "message": f"private WS: {private_state}"})
    if trade_state != "authenticated-locked": issues.append({"severity": "yellow", "code": "trade_ws", "message": f"trade WS: {trade_state}"})
    disk = shutil.disk_usage(DATA)
    if disk.free < 8 * 1024 ** 3: issues.append({"severity": "red", "code": "disk", "message": "свободно меньше аварийного резерва 8 ГБ"})
    elif disk.free < 15 * 1024 ** 3: issues.append({"severity": "yellow", "code": "disk", "message": "свободно меньше 15 ГБ"})
    backup_stamp = str(backup.get("created_at_utc") or "")
    try:
        backup_age = now - int(time.mktime(time.strptime(backup_stamp, "%Y%m%dT%H%M%SZ")))
    except ValueError:
        backup_age = 10 ** 9
    if backup.get("state") != "verified" or backup_age > 36 * 3600: issues.append({"severity": "red", "code": "backup", "message": "нет проверенной резервной копии моложе 36 часов"})
    state = "red" if any(i["severity"] == "red" for i in issues) else "yellow" if issues else "green"
    return {"state": state, "checked_at_epoch": now, "issues": issues, "data_disk_free": disk.free}


def atomic(payload: dict[str, object]) -> None:
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATUS)


def main() -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    initialize(connection)
    previous = ""
    while True:
        payload = evaluate()
        fingerprint = json.dumps([payload["state"], payload["issues"]], ensure_ascii=False, sort_keys=True)
        if fingerprint != previous:
            connection.execute("INSERT INTO monitoring.health_events(at_epoch_ms,state,issues_json) VALUES(%s,%s,%s)",
                               (int(time.time() * 1000), payload["state"], json.dumps(payload["issues"], ensure_ascii=False)))
            connection.commit()
            previous = fingerprint
        atomic(payload)
        time.sleep(15)


if __name__ == "__main__":
    main()

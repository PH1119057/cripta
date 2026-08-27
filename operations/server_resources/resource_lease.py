from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEASE_DIR = Path("/var/lib/cripta/resource-leases")


def _systemctl_state(unit: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Аренда повышенных ресурсов на время задания")
    parser.add_argument("command", choices=("create", "inspect", "close"))
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--unit")
    parser.add_argument("--baseline-json")
    parser.add_argument("--result-marker")
    parser.add_argument("--auto-restore", action="store_true")
    args = parser.parse_args()
    path = LEASE_DIR / f"{args.lease_id}.json"

    if args.command == "create":
        if path.exists():
            raise SystemExit("аренда с таким ID уже существует")
        if not args.unit or not args.baseline_json or not args.result_marker:
            raise SystemExit("требуются --unit, --baseline-json и --result-marker")
        lease = {
            "lease_id": args.lease_id,
            "created_at": datetime.now(UTC).isoformat(),
            "unit": args.unit,
            "baseline": json.loads(args.baseline_json),
            "result_marker": args.result_marker,
            "auto_restore": args.auto_restore,
            "status": "prepared",
            "restore_status": "locked_until_api_connected",
        }
        _write(path, lease)
        print(json.dumps(lease, ensure_ascii=False, indent=2))
        return 0

    if not path.exists():
        raise SystemExit("аренда не найдена")
    lease = json.loads(path.read_text(encoding="utf-8"))
    lease["unit_state"] = _systemctl_state(str(lease["unit"]))
    lease["result_complete"] = Path(str(lease["result_marker"])).exists()
    lease["eligible_for_restore"] = (
        lease["auto_restore"]
        and lease["unit_state"] in {"inactive", "failed"}
        and lease["result_complete"]
    )
    if args.command == "close":
        if not lease["eligible_for_restore"]:
            raise SystemExit("возврат запрещён: расчёт или итог ещё не подтверждён")
        lease["status"] = "ready_to_restore"
        lease["restore_status"] = "locked_until_api_connected"
        _write(path, lease)
    print(json.dumps(lease, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

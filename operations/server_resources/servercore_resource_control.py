from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PROFILES = Path("/srv/cripta/operations/server_resources/resource_profiles.json")
DEFAULT_ENV = Path("/etc/cripta/servercore.env")
AUDIT = Path("/var/log/cripta/servercore-resource-audit.jsonl")


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _profiles(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _plan(profile_name: str, profile: dict[str, Any], server_id: str) -> dict[str, Any]:
    body = {
        "server_id": server_id,
        "profile": profile_name,
        "vcpus": int(profile["vcpus"]),
        "ram_policy": str(profile["ram_policy"]),
        "created_at": datetime.now(UTC).isoformat(),
        "steps": [
            "проверить контрольные точки активных расчётов",
            "запретить выдачу новых блоков",
            "дождаться фиксации активных небольших блоков",
            "изменить конфигурацию через Servercore/OpenStack",
            "дождаться ACTIVE и SSH",
            "проверить диски, PostgreSQL, Nginx и службы Cripta",
            "продолжить только незавершённые блоки очереди",
            "после подтверждённого завершения вернуть сохранённую исходную конфигурацию",
        ],
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    body["plan_id"] = hashlib.sha256(canonical).hexdigest()[:16]
    return body


def _audit(event: dict[str, Any]) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Безопасное управление ресурсами Servercore")
    parser.add_argument("command", choices=("readiness", "plan", "apply"))
    parser.add_argument("--profile")
    parser.add_argument("--confirm-plan-id")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args()

    env_values = _load_env(args.env_file)
    required = ("OS_AUTH_URL", "OS_APPLICATION_CREDENTIAL_ID",
                "OS_APPLICATION_CREDENTIAL_SECRET", "OS_PROJECT_ID",
                "OS_REGION_NAME", "SERVER_ID")
    missing = [key for key in required if not env_values.get(key)]
    openstack = shutil.which("openstack")
    readiness = {
        "credentials_installed": not missing,
        "missing": missing,
        "openstack_cli": openstack,
        "apply_locked": True,
    }
    if args.command == "readiness":
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0
    if not args.profile:
        raise SystemExit("требуется --profile")
    profiles = _profiles(args.profiles)
    if args.profile not in profiles or args.profile == "version":
        raise SystemExit(f"неизвестный профиль: {args.profile}")
    plan = _plan(args.profile, profiles[args.profile], env_values.get("SERVER_ID", "NOT_CONFIGURED"))
    if args.command == "plan":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    if missing or not openstack:
        raise SystemExit("APPLY ЗАБЛОКИРОВАН: API-доступ или OpenStack CLI не настроен")
    if args.confirm_plan_id != plan["plan_id"]:
        raise SystemExit("APPLY ЗАБЛОКИРОВАН: требуется точный --confirm-plan-id из свежего плана")
    # До получения реквизитов и проверки доступных flavors реальный resize намеренно закрыт.
    # Здесь будет вызов выбранного flavorRef после read-only discovery в аккаунте Servercore.
    event = {**plan, "status": "blocked_until_provider_discovery"}
    _audit(event)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

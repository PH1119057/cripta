from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "research" / "server" / "monitoring" / "health_monitor.py"


def _load_health():
    spec = importlib.util.spec_from_file_location("health_monitor_contract_test", HEALTH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_monitor_ignores_intentionally_disabled_private_trade(
    monkeypatch, tmp_path: Path
) -> None:
    health = _load_health()
    now = 1_800_000_000
    public = tmp_path / "public.json"
    safety = tmp_path / "safety.json"
    private = tmp_path / "private.json"
    backup = tmp_path / "backup.json"
    public.write_text(json.dumps({"state": "connected", "updated_at_epoch": now}), encoding="utf-8")
    safety.write_text(json.dumps({"state": "healthy", "checked_at_epoch": now}), encoding="utf-8")
    private.write_text(
        json.dumps({"private": {"state": "starting"}, "trade": {"state": "starting"}}),
        encoding="utf-8",
    )
    stamp = __import__("time").strftime("%Y%m%dT%H%M%SZ", __import__("time").gmtime(now))
    backup.write_text(json.dumps({"state": "verified", "created_at_utc": stamp}), encoding="utf-8")
    monkeypatch.setattr(health, "PUBLIC", public)
    monkeypatch.setattr(health, "SAFETY", safety)
    monkeypatch.setattr(health, "PRIVATE", private)
    monkeypatch.setattr(health, "BACKUP", backup)
    monkeypatch.setattr(health.time, "time", lambda: now)
    monkeypatch.setattr(
        health.shutil,
        "disk_usage",
        lambda _p: type("DU", (), {"free": 20 * 1024**3})(),
    )

    result = health.evaluate(expect_private_ws=False, expect_trade_ws=False)

    assert result["state"] == "green"
    assert result["issues"] == []


def test_health_monitor_still_fails_closed_when_private_is_expected(
    monkeypatch, tmp_path: Path
) -> None:
    health = _load_health()
    now = 1_800_000_000
    public = tmp_path / "public.json"
    safety = tmp_path / "safety.json"
    private = tmp_path / "private.json"
    backup = tmp_path / "backup.json"
    public.write_text(json.dumps({"state": "connected", "updated_at_epoch": now}), encoding="utf-8")
    safety.write_text(json.dumps({"state": "healthy", "checked_at_epoch": now}), encoding="utf-8")
    private.write_text(
        json.dumps({"private": {"state": "starting"}, "trade": {"state": "starting"}}),
        encoding="utf-8",
    )
    stamp = __import__("time").strftime("%Y%m%dT%H%M%SZ", __import__("time").gmtime(now))
    backup.write_text(json.dumps({"state": "verified", "created_at_utc": stamp}), encoding="utf-8")
    monkeypatch.setattr(health, "PUBLIC", public)
    monkeypatch.setattr(health, "SAFETY", safety)
    monkeypatch.setattr(health, "PRIVATE", private)
    monkeypatch.setattr(health, "BACKUP", backup)
    monkeypatch.setattr(health.time, "time", lambda: now)
    monkeypatch.setattr(
        health.shutil,
        "disk_usage",
        lambda _p: type("DU", (), {"free": 20 * 1024**3})(),
    )

    result = health.evaluate(expect_private_ws=True, expect_trade_ws=True)

    assert result["state"] == "red"
    assert {issue["code"] for issue in result["issues"]} == {"private_ws", "trade_ws"}


def test_backup_is_packaged_and_installed_from_runtime_release() -> None:
    installer = (
        ROOT / "operations" / "infrastructure" / "install_verified_release.sh"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "research" / "server" / "backup" / "cripta-backup.service"
    ).read_text(encoding="utf-8")
    assert "research/server/backup" in installer
    assert "research/server/backup/cripta-backup.service|cripta-backup.service|runtime" in installer
    assert "research/server/backup/cripta-backup.timer|cripta-backup.timer|runtime" in installer
    assert (
        "ExecStart=/usr/bin/bash "
        "/srv/cripta/runtime/current/research/server/backup/backup.sh"
    ) in service


def test_health_unit_declares_disabled_private_trade_expectations() -> None:
    unit = (
        ROOT / "research" / "server" / "monitoring" / "cripta-health-monitor.service"
    ).read_text(encoding="utf-8")
    assert "Environment=CRIPTA_EXPECT_PRIVATE_WS=0" in unit
    assert "Environment=CRIPTA_EXPECT_TRADE_WS=0" in unit


def test_system_backup_keeps_exactly_two_latest_verified_generations() -> None:
    backup_script = (
        ROOT / "research" / "server" / "backup" / "backup.sh"
    ).read_text(encoding="utf-8")
    rules = (ROOT / "docs" / "DEVELOPMENT_RELEASE_RULES_RU.md").read_text(
        encoding="utf-8"
    )
    current_map = (ROOT / "docs" / "CURRENT_PROJECT_MAP_RU.md").read_text(
        encoding="utf-8"
    )

    assert "keep exactly the two newest verified generations" in backup_script
    assert 'if (( ${#verified_generations[@]} > 2 )); then' in backup_script
    assert 'for old_stamp in "${verified_generations[@]:2}"' in backup_script
    assert "20??????T??????Z" in backup_script
    assert "20????????T??????Z" not in backup_script
    assert "SYSTEM BACKUP RETENTION = exactly 2 latest verified generations" in rules
    assert "/data/cripta/backups/system" in rules
    assert "keeps exactly the 2 latest verified generations" in current_map
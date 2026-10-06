from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from operations.monitoring import universal_entry_shadow as observer

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, 15, 30, tzinfo=UTC)


class Cursor:
    rowcount = 1


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        return Cursor()


def test_observer_runtime_error_is_upserted_as_durable_non_autoresolving_fault(
    monkeypatch,
) -> None:
    monkeypatch.setattr(observer, "LOADED_COMMIT", "a" * 40)
    connection = FakeConnection()

    first = observer._record_observer_runtime_fault(
        connection,
        ValueError("forced-test-error"),
        observed_at=NOW,
        observer_epoch_id="epoch-a",
    )
    second = observer._record_observer_runtime_fault(
        connection,
        ValueError("forced-test-error"),
        observed_at=NOW,
        observer_epoch_id="epoch-b",
    )

    assert first == second
    statement, params = connection.calls[0]
    assert "INSERT INTO runtime.lifecycle_faults" in statement
    assert "ON CONFLICT(fault_id) DO UPDATE" in statement
    assert "state='OPEN'" in statement
    assert "occurrence_count" in statement
    assert params[1] == "UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR"
    exact_ids = json.loads(str(params[3]))
    payload = json.loads(str(params[4]))
    assert exact_ids["source_commit"] == "a" * 40
    assert payload["error_type"] == "ValueError"
    assert payload["error_message"] == "forced-test-error"
    assert payload["auto_resolve"] is False
    assert payload["owner_resolution_required"] is True


def test_observer_exception_path_records_fault_before_status_retry() -> None:
    source = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    start = source.index("def _run_multi_strategy_observer()")
    end = source.index("\ndef _run_parity_main()", start)
    block = source[start:end]
    catch = block.index("except Exception as exc:")
    record = block.index("_record_observer_runtime_fault(", catch)
    persist_failure = block.index("durable fault persistence failed", record)
    retry = block.index("time.sleep(2.0)", persist_failure)
    assert catch < record < persist_failure < retry
    assert "with suppress(Exception)" not in block[catch:retry]


def test_fault_token_is_canonical_and_installer_orders_migration() -> None:
    glossary = (ROOT / "docs/CRIPTA_GLOSSARY_RU.md").read_text(encoding="utf-8")
    observation = (ROOT / "docs/OBSERVATION_ANALYTICS_RU.md").read_text(encoding="utf-8")
    migration = (
        ROOT / "operations/sql/20261006_observer_runtime_fault_v1.sql"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")

    token = "UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR"
    assert token in glossary
    assert "status.json" in observation
    assert "durable operational fault" in observation
    assert token in migration
    assert (
        installer.index("20261006_account_state_generation_v1.sql")
        < installer.index("20261006_observer_runtime_fault_v1.sql")
        < installer.index("20261003_market_observation_alert_v1.sql")
    )

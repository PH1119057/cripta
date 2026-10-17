from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "operations/monitoring/r9_migration_verifier.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

spec = importlib.util.spec_from_file_location("r9_migration_verifier", SOURCE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _BaselineConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str, params: object = None) -> _Result:
        self.queries.append(query)
        if "clock_timestamp()" in query:
            return _Result(
                [
                    (
                        datetime(2026, 10, 17, 7, 45, tzinfo=UTC),
                        123,
                        datetime(2026, 10, 17, 7, 44, tzinfo=UTC),
                        datetime(2026, 10, 17, 7, 43, tzinfo=UTC),
                        datetime(2026, 10, 17, 7, 42, tzinfo=UTC),
                    )
                ]
            )
        return _Result([])


class _MissingSchemaConnection:
    def __init__(self) -> None:
        self.read_only = False

    def execute(self, query: str, params: object = None) -> _Result:
        if query == "SET TRANSACTION READ ONLY":
            self.read_only = True
            return _Result([])
        if "SELECT to_regclass" in query:
            relation = str(params[0])
            if relation == "analytics.market_minutes":
                return _Result([(None,)])
            return _Result([(relation,)])
        raise AssertionError(f"unexpected query after schema failure: {query}")


def test_r9_verifier_is_read_only_and_has_required_gate_vocabulary() -> None:
    assert 'connection.execute("SET TRANSACTION READ ONLY")' in SOURCE
    assert ".commit(" not in SOURCE
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE", "DROP TABLE"):
        assert forbidden not in SOURCE
    for gate in (
        "SCHEMA_CONTRACT",
        "IDENTITY_REGISTRY",
        "ACCOUNT_SCOPE_PARITY",
        "MARKET_SOURCE_PARITY",
        "COMPACT_ANALYTICS_PARITY",
        "RESTART_RECOVERY",
    ):
        assert gate in SOURCE


def test_baseline_is_read_only_and_emits_restart_watermarks() -> None:
    connection = _BaselineConnection()
    result = module.baseline(connection)
    assert connection.queries[0] == "SET TRANSACTION READ ONLY"
    assert result.mayak_snapshot_id == 123
    assert result.database_time == "2026-10-17T07:45:00+00:00"
    assert result.account_generation_started_at == "2026-10-17T07:43:00+00:00"
    assert result.reconciliation_started_at == "2026-10-17T07:42:00+00:00"


def test_verify_fails_closed_before_any_parity_read_when_schema_is_old() -> None:
    connection = _MissingSchemaConnection()
    gates = module.verify(
        connection,
        trading_account_id="ta-test",
        after_time=None,
        parity_snapshots=3,
    )
    assert connection.read_only is True
    assert len(gates) == 1
    assert gates[0].name == "SCHEMA_CONTRACT"
    assert gates[0].status == "FAIL"
    assert "analytics.market_minutes" in gates[0].detail


def test_after_time_requires_timezone_and_normalizes_to_utc() -> None:
    parsed = module._parse_time("2026-10-17T12:30:00+05:00")
    assert parsed == datetime(2026, 10, 17, 7, 30, tzinfo=UTC)

    try:
        module._parse_time("2026-10-17T12:30:00")
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive restart watermark was accepted")


def test_compact_parity_query_is_versioned_and_source_scoped() -> None:
    assert "schema_version=%s" in SOURCE
    assert "market_source_id=%s" in SOURCE
    assert "source_snapshot_id" in SOURCE
    assert "mayak_snapshot_id=%s" in SOURCE
    assert "compare_compact_panel_to_contexts" in SOURCE
    assert "R8C_REQUIRED_ANALYTICAL_PATHS" in SOURCE


def test_restart_gate_requires_both_public_and_private_progress() -> None:
    assert "MAYAK_NO_POST_RESTART_SNAPSHOT" in SOURCE
    assert "COMPACT_NO_POST_RESTART_SNAPSHOT" in SOURCE
    assert "runtime.account_state_generations" in SOURCE
    assert "runtime.reconciliation_runs" in SOURCE
    assert "NO_POST_RESTART_PROVEN_ROW" in SOURCE

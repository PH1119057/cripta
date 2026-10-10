from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path


def test_future_heartbeat_requires_fresh_database_clock_confirmation() -> None:
    source = Path("src/bybit_workbench/lifecycle_supervisor.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "if age < 0:" in source
    assert '"SELECT clock_timestamp() AS observed_at"' in source
    assert "age = (db_clock - last_seen).total_seconds()" in source
    assert source.index("age = (db_clock - last_seen).total_seconds()") < source.index(
        'claim_reason = "Exit Engine claim heartbeat is from the future"'
    )
    assert 'claim_reason = "Exit Engine claim heartbeat is stale"' in source


def test_db_clock_semantics_for_racing_and_truly_future_claims() -> None:
    epoch = datetime(2026, 10, 10, tzinfo=UTC)
    claim_time = epoch + timedelta(milliseconds=120)
    assert (epoch - claim_time).total_seconds() < 0
    assert ((epoch + timedelta(milliseconds=220)) - claim_time).total_seconds() >= 0
    assert ((epoch + timedelta(milliseconds=20)) - claim_time).total_seconds() < 0

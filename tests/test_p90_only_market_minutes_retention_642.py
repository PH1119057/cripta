"""Contract tests for bounded P90-only minute-observation retirement."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE = (
    Path(__file__).resolve().parents[1]
    / "operations"
    / "monitoring"
    / "p90_only_market_minutes_retention_642.py"
)
spec = importlib.util.spec_from_file_location("minute_retention_642", MODULE)
assert spec is not None and spec.loader is not None
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


def test_exact_owner_approved_symbol_scope() -> None:
    legacy = set(retention.LEGACY_SYMBOLS)
    protected = set(retention.PROTECTED_SYMBOLS)
    assert len(legacy) == 13
    assert len(protected) == 7
    assert not (legacy & protected)
    assert protected >= {"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"}
    assert protected >= {"BTCUSDT", "ETHUSDT"}
    assert retention.EXPECTED_LEGACY_PER_SYMBOL == 65180
    assert retention.CUTOFF.isoformat() == "2026-11-12T00:00:00+00:00"


def test_bounded_sql_targets_only_market_minutes() -> None:
    sql = retention.rows_to_delete_sql()
    assert sql.count("analytics.market_minutes") == 2
    assert sql.startswith("WITH chosen AS")
    assert "external_symbol = ANY(%s::text[])" in sql
    assert "observed_minute < %s" in sql
    assert "LIMIT %s FOR UPDATE SKIP LOCKED" in sql
    assert "target.ctid=chosen.ctid" in sql
    for forbidden in ("CASCADE", "TRUNCATE", "ALTER TABLE", "DROP", "REINDEX"):
        assert forbidden not in sql
    assert retention.BATCH <= 2500
    assert retention.MAX_ROWS_PER_INVOCATION <= 25000
    assert retention.MIN_ROOT_FREE_BYTES >= 320 * 1024 * 1024


class FakeQuery:
    def __init__(self, data: list[tuple]) -> None:
        self.responses = iter(
            [
                ("cripta", "cripta", "cripta", False),
                ("r", "postgres", 0, 0, 1711808512),
                data,
            ]
        )

    def execute(self, sql: str, params: object = None) -> FakeQuery:
        return self

    def fetchone(self) -> tuple:
        return next(self.responses)

    def fetchall(self) -> list[tuple]:
        return next(self.responses)


def rows_with_all_protected() -> list[tuple]:
    return [
        (s, retention.SOURCE, 69110, retention.CUTOFF, retention.CUTOFF)
        for s in retention.PROTECTED_SYMBOLS
    ]


def test_preflight_rejects_new_legacy_data(monkeypatch) -> None:
    monkeypatch.setattr(retention, "root_free_bytes", lambda: 500_000_000)
    data = rows_with_all_protected() + [
        ("SOLUSDT", retention.SOURCE, 65180, retention.CUTOFF, retention.CUTOFF)
    ]
    import pytest

    with pytest.raises(RuntimeError, match="LEGACY_AFTER_CUTOFF"):
        retention.verify_scope(FakeQuery(data), applying=False)


def test_preflight_never_accepts_unknown_symbol(monkeypatch) -> None:
    monkeypatch.setattr(retention, "root_free_bytes", lambda: 500_000_000)
    data = rows_with_all_protected() + [
        ("UNKNOWNUSDT", retention.SOURCE, 3, retention.CUTOFF, retention.CUTOFF)
    ]
    import pytest

    with pytest.raises(RuntimeError, match="MARKET_IDENTITY_DRIFT"):
        retention.verify_scope(FakeQuery(data), applying=False)


def test_preflight_checks_exact_postgres_actor(monkeypatch) -> None:
    monkeypatch.setattr(retention, "root_free_bytes", lambda: 500_000_000)
    import pytest

    with pytest.raises(RuntimeError, match="POSTGRES_DB_ACTOR_REQUIRED"):
        retention.verify_scope(FakeQuery(rows_with_all_protected()), applying=True)

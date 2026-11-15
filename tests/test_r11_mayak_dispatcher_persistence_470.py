"""R11 #470 final code-first MAYAK persistence/Dispatcher boundary tests."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from operations.monitoring import r11_mayak_pg_observation as pg

AT = datetime(2026, 11, 15, 0, 5, 12, tzinfo=UTC)
SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)


def _snapshot() -> dict[str, Any]:
    coins = {symbol: {"quality": {}} for symbol in SEVEN}
    return {
        "observed_at": AT.isoformat(),
        "state": "прогрев",
        "confidence": 0.75,
        "engine_version": "mayak-v2-test",
        "feature_version": "feature-test",
        "architecture_version": "architecture-test",
        "config_fingerprint": "a" * 64,
        "reasons": ["warmup"],
        "price_breadth": {"up": 1, "down": 1},
        "money_breadth": {"spot_coverage": {"valid": 7, "total": 7}},
        "direction_synchronization": {"status": "WARMUP"},
        "btc": coins["BTCUSDT"],
        "eth": coins["ETHUSDT"],
        "coins": coins,
        "coin_market_contexts": {},
        "dispatcher_handoff": {
            "market_context_id": "ctx-1",
            "market_source_id": "BYBIT_PUBLIC_V5_MAINNET",
            "observed_at": AT.isoformat(),
            "market_context_schema_version": "test",
            "data_quality": "INSUFFICIENT",
            "provenance": {
                "trading_command": False,
                "r11_market_publication_id": 101,
            },
        },
        "r11_market_publication": {
            "publication_id": 101,
            "source_mode": "PG_ORIGINAL_REPLAY",
            "consumer_ready": False,
            "mayak_ws_retirement_allowed": False,
            "trading_effect": "NONE",
        },
    }


class FakeDB:
    def __init__(self, *, existing: tuple[int] | None = None) -> None:
        self.existing = existing
        self.queries: list[str] = []
        self.many: list[tuple[str, Any]] = []

    def execute(self, query: str, _params: Any = None) -> SimpleNamespace:
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        if normalized.startswith("SELECT id FROM mayak_v2.snapshots"):
            return SimpleNamespace(fetchone=lambda: self.existing)
        if normalized.startswith("SELECT state FROM mayak_v2.snapshots"):
            return SimpleNamespace(fetchone=lambda: ("calm",))
        if normalized.startswith("INSERT INTO mayak_v2.snapshots"):
            return SimpleNamespace(fetchone=lambda: (17,))
        return SimpleNamespace(fetchone=lambda: None)

    def cursor(self) -> FakeDB:
        return self

    def executemany(self, query: str, rows: Any) -> None:
        self.many.append((" ".join(query.split()), rows))


def _stub_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pg, "compact_market_minute_rows",
        lambda _snapshot_id, _snapshot: ({"x": 1},),
    )
    monkeypatch.setattr(
        pg, "compact_market_minute_insert_sql",
        lambda: "INSERT INTO analytics.market_minutes(x) VALUES(%(x)s)",
    )
    monkeypatch.setattr(
        pg, "coin_market_context_records",
        lambda _snapshot_id, _snapshot: (
            {
                "coin_context_id": "coin-1",
                "market_source_id": "source",
                "spot_instrument_id": "spot",
                "linear_instrument_id": "linear",
                "mayak_snapshot_id": 17,
                "observed_at": AT.isoformat(),
                "symbol": "APTUSDT",
                "schema_version": "s",
                "engine_version": "e",
                "feature_version": "f",
                "config_fingerprint": "a" * 64,
                "data_quality": "INSUFFICIENT",
                "payload": {},
                "provenance": {"trading_command": False},
                "content_hash": "b" * 64,
            },
        ),
    )
    monkeypatch.setattr(
        pg, "shared_market_context_record",
        lambda _snapshot_id, _snapshot: {
            "market_context_id": "global-1",
            "market_source_id": "source",
            "mayak_snapshot_id": 17,
            "observed_at": AT.isoformat(),
            "mayak_version": "e",
            "schema_version": "s",
            "config_fingerprint": "a" * 64,
            "data_quality": "INSUFFICIENT",
            "payload": {},
            "provenance": {"trading_command": False},
            "content_hash": "c" * 64,
        },
    )


def test_pg_commit_materializes_normal_mayak_inputs_not_dispatcher_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_builders(monkeypatch)
    db = FakeDB()
    snapshot_id = pg._persist_mayak_dispatcher_outputs(
        db, _snapshot(), symbols=SEVEN,
    )
    assert snapshot_id == 17
    sql = "\n".join(db.queries + [query for query, _rows in db.many])
    assert "INSERT INTO mayak_v2.snapshots" in sql
    assert "INSERT INTO analytics.market_minutes" in sql
    assert "INSERT INTO mayak_v2.coin_market_contexts" in sql
    assert "INSERT INTO mayak_v2.observation_journal" in sql
    assert "INSERT INTO mayak_v2.shared_market_contexts" in sql
    assert "INSERT INTO mayak_v2.state_events" in sql
    assert "dispatcher_v2." not in sql
    assert "mayak_v2.liquidations" not in sql


def test_existing_regular_minute_is_idempotent_and_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pg, "compact_market_minute_rows",
        lambda *_args: pytest.fail("existing minute must not be rebuilt"),
    )
    db = FakeDB(existing=(9,))
    assert pg._persist_mayak_dispatcher_outputs(
        db, _snapshot(), symbols=SEVEN,
    ) is None
    assert not db.many
    assert not any(query.startswith("INSERT INTO") for query in db.queries)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("consumer_ready", True),
        ("mayak_ws_retirement_allowed", True),
        ("trading_effect", "TRADE"),
    ],
)
def test_false_ready_or_trading_effect_never_persists(
    field: str,
    value: object,
) -> None:
    snapshot = _snapshot()
    snapshot["r11_market_publication"][field] = value
    with pytest.raises(pg.R11MayakPGSourceUnavailable, match="FALSE_READY"):
        pg._persist_mayak_dispatcher_outputs(
            FakeDB(), snapshot, symbols=SEVEN,
        )


def test_consumer_commit_and_mayak_output_share_outer_transaction() -> None:
    source = __import__("pathlib").Path(pg.__file__).read_text(encoding="utf-8")
    assert "with writer.transaction():" in source
    commit_at = source.index("committed = commit_r11_mayak_publication(")
    persist_at = source.index("_persist_mayak_dispatcher_outputs(", commit_at)
    assert commit_at < persist_at
